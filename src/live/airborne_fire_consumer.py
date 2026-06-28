"""에어본 발화 직접구동 consumer — 봉루프 decouple (2026-06-11).

배경 (사고 — "7시 롱 미매수"):
  consume 모드는 ``logs/airborne_fires/history.jsonl`` 발화를 직접 읽지만,
  *언제 평가하나* 가 트레이더의 OHLCV 봉루프(``orchestrator.run_bar`` →
  live-scanner per-symbol on_bar)에 종속됐다. ``run_bar`` 은 ``_universe_ohlcv``
  (트레이더 스냅샷)에 있는 종목만 평가하므로, universe refresh 랙으로 발화 종목이
  그 봉 스냅샷에 없으면 영영 미평가 → 발화는 history.jsonl 에 있는데 진입 안 됨
  (2026-06-11 22:00 UTC 12 발화 전부 미진입).

설계:
  발화가 곧 진입 트리거. ``AirborneFireConsumer`` 가 백그라운드 task 로 fire
  store 를 직접 polling → 게이트(도착시각 KST hour / side / universe / freshness
  / BTC trend) 통과분을 ``orchestrator.dispatch_fire_entry`` 로 직접 진입. 봉
  스냅샷 랙과 무관.

dedup:
  기존 on_bar consume 과 *동일한* ``logs/airborne_reentry/{ClassName}.json``
  영속 dedup 을 공유한다 (전략 인스턴스의 ``_ensure_dedup_loaded`` /
  ``_fired_bar_ts`` / ``_persist_dedup``). 키 값 = ``str(bar_open)`` (bar_open =
  floor(fire_ts,1h) − 1h) 으로 on_bar consume 의 ``closed_ts`` 키와 정확히
  일치 → 두 경로가 동시 가동돼도 중복진입 0. 추가로 orchestrator 의
  ``_live_entered`` 가 (sid, symbol) 당 1포지션 보장.

상세: docs/specs/airborne-fire-driven-consume.md (authoritative).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import pandas as pd

logger = logging.getLogger(__name__)

_KST = "Asia/Seoul"
# astimezone() 용 tzinfo 객체 (위 _KST 문자열은 pandas tz_convert 전용).
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

_KST_TZ = _ZoneInfo("Asia/Seoul")
_BTC_SYMBOL = "BTCUSDT"


@dataclass
class AirborneStrategySpec:
    """발화-구동 진입 대상 전략 1개의 게이트 명세.

    loop.py 가 등록된 airborne live-scanner 전략을 introspect 해 구성한다.
    """

    id: str
    # 진입 허용 KST 도착시각 (= 봉 마감 = 알림 시각). floor(fire_ts,1h).KST.hour.
    kst_entry_hours: frozenset[int]
    # 진입 허용 side — {"long","short"} (bidir) 또는 {"short"} (short-whitelist).
    allowed_sides: frozenset[str]
    # 진입 허용 universe (종목 set). None 이면 무제한 (universe 필터 생략).
    universe: frozenset[str] | None
    # BTC 하락추세 시 long 진입 차단 여부 (short 은 무관).
    btc_filter: bool
    # dedup 공유용 전략 인스턴스 — _ensure_dedup_loaded / _fired_bar_ts /
    # _persist_dedup 를 직접 호출해 on_bar consume 과 dedup 정합.
    instance: object = field(default=None, repr=False)


class AirborneFireConsumer:
    """fire store → 게이트 → ``orchestrator.dispatch_fire_entry`` 백그라운드 task.

    PositionReconciler 와 동일하게 ``run_loop(stop_event)`` 으로 가동되며, 절대
    raise 하지 않는다 (한 발화/한 sweep 의 예외가 trading 을 멈추지 못함).
    """

    def __init__(
        self,
        *,
        fire_store,
        orchestrator,
        strategy_specs: list[AirborneStrategySpec],
        route_intents: Callable[[list], Awaitable[None]] | Callable[[list], None],
        equity_provider: Callable[[], float],
        btc_ohlcv_provider: Callable[[], "pd.DataFrame | None"] | None = None,
        notify: Callable[[str], None] | None = None,
        freshness_sec: float = 600.0,
        long_freshness_sec: float = 90.0,
        short_block_hours: "frozenset[int] | None" = None,
        interval_sec: float = 15.0,
        pace_sec: float = 0.15,
        klines_fetcher: "Callable[[str], Awaitable] | None" = None,
        daily_pnl_provider: "Callable[[], float] | None" = None,
    ) -> None:
        self._store = fire_store
        self._orch = orchestrator
        self._specs = list(strategy_specs)
        self._route_intents = route_intents
        self._equity_provider = equity_provider
        self._btc_ohlcv_provider = btc_ohlcv_provider
        # ground-truth 진입/미진입 텔레그램 알림 (2026-06-20) — 실제로 매수한
        # 트레이더(consumer)가 직접 "✅ 실진입 N건 / ❌ 미진입 N건(사유)" 을 통지.
        # 알림 데몬(airborne_alert_daemon)의 "진입 예정" 예측과 별개로 *실거래와
        # 100% 일치* 하는 단일 진실. None 이면 비활성(테스트 기본). sync callable(text)
        # — sweep 안에서 asyncio.to_thread 로 호출(blocking 회피).
        self._notify = notify
        # fire 단위 dedup — 같은 (symbol, side, bar_open) 발화를 매 sweep (15s)
        # 재평가해도 알림은 1회만. freshness 밖이면 다시 안 잡혀 무한 X.
        self._skip_notified: set[tuple[str, str, str]] = set()
        self._entry_notified: set[tuple[str, str, str]] = set()
        self._freshness_sec = float(freshness_sec)
        # 롱 전용 짧은 freshness (2026-06-14) — BTC 추세필터가 롱을 막으면 fire 가
        # store 에 남아 매 sweep 재평가되다, BTC 추세가 풀리는 순간 *묵은* fire_close
        # 가격으로 8분 뒤 진입(stale → price-past-mark NAKED, v0.6.65). 롱은 봉마감
        # 직후(≤~1.5분)에만 진입 — 그 안에 BTC 통과 못 하면 abandon(늦은 stale 진입
        # 차단). BTC 상승추세 롱은 첫 sweep(~45s)에 즉시 진입 → "정각 빠른 매수".
        # 숏은 기존 freshness 유지(재시작 backlog 보호). env AIRBORNE_LONG_FRESHNESS_SEC.
        self._long_freshness_sec = float(long_freshness_sec)
        # 숏 차단 시간대 (2026-06-15) — 해당 KST 시각의 SHORT 발화는 진입 안 함.
        # 기본 {7}: KST 07시 = 유럽장 시작 거래량 급증. 이 시각엔 평소 숏 신호가
        # 적은데(대개 롱), 숏이 다발로 뜨면 = 많은 종목이 BB 상단 동시 터치 =
        # 본격 상승추세 신호 → 숏 진입 시 줄줄이 깔림(2026-06-15 07시 실거래
        # -21.96 USDT, 23건 중 21패). LONG 은 영향 없음(이 가드는 short 만).
        # env AIRBORNE_SHORT_BLOCK_HOURS (csv). None → {7}.
        self._short_block_hours = (
            frozenset({7}) if short_block_hours is None else frozenset(short_block_hours)
        )
        self._interval_sec = float(interval_sec)
        # ③ 주문 페이싱 — 동시발화(03·23시 25개+)를 한꺼번에 쏘면 거래소가 [429]
        # Too Many Requests / [40092] service unavailable 로 튕긴다(2026-06-12 audit).
        # 발주 사이에 짧은 간격을 둬 rate-limit 회피(발주는 어차피 순차 await 이나
        # 무딜레이라 초당 폭주). 0 이면 비활성(기존 동작).
        self._pace_sec = float(pace_sec)
        # 모멘텀 진입 필터 (2026-06-17) — 이미 크게 움직인 토큰 진입 차단.
        # 숏: 직전24h +X%↑ 펌핑이면 스퀴즈·슬립 위험 → skip. 롱: -Y%↓ 폭락이면
        # 떨어지는칼 → skip. 백테스트(6/01+, sim): 숏 skip>+20% PF 1.69→1.89·
        # 승률 48→51%, 롱 skip<-10% PF 1.17→1.31·41→44% (positive-sum, 손실꼬리만
        # 제거). klines_fetcher 주입 시에만 활성(테스트는 미주입=OFF). env 로 임계 튜닝
        # (0 이면 해당 방향 비활성). 토큰 OHLCV 미가용 시 fail-open(허용).
        import os as _os
        self._klines_fetcher = klines_fetcher
        # 2026-06-22: 펌핑 숏차단 임계 +20 → +30. +20 룰이 펌핑 후 BB되돌림 숏
        # (이 전략의 핵심)을 죽였음 — 4주 차단 바스켓 net +7.9% / PF 1.85, n=29
        # (거른 게 순이득 신호). +30 으로 완화. 롤백 = env=20.
        self._short_pump_skip = float(
            _os.environ.get("AIRBORNE_SHORT_PUMP_SKIP_PCT", "30") or 30
        )
        self._long_crash_skip = float(
            _os.environ.get("AIRBORNE_LONG_CRASH_SKIP_PCT", "10") or 10
        )
        # 변동성 필터 (2026-06-17) — 코인 최근 평균 1h 변동폭% > 임계면 양방향 진입
        # skip. SKYAI/SIREN류 초고변동 코인은 -1% SL 이 무의미(노이즈로 뚫고 stop
        # 슬립 -19%). 실거래 검증: >5%/h 코인 PF 0.16/net -278(슬립). 0=비활성.
        self._max_vol_pct = float(_os.environ.get("AIRBORNE_MAX_VOL_PCT", "5") or 5)
        # ── 진입 콘텐츠 필터 6종 개별 ENV 토글 (2026-06-25) ──────────────────────
        # 6 필터 = 타임게이트·btc하락추세·숏차단시각·고변동·펌핑·폭락. 각각
        # AIRBORNE_FILTER_<NAME>=1/0 으로 켜고 끈다. 미설정이면 매크로 기본값:
        #   AIRBORNE_NO_ENTRY_FILTERS=1 → 6개 전부 기본 OFF (raw 무필터 baseline)
        #   AIRBORNE_TIME_GATE_ONLY=1   → 타임게이트만 기본 ON, 콘텐츠 5종 OFF
        #   둘 다 미설정                → 6개 전부 기본 ON (현행 production)
        # 개별 토글이 명시되면 매크로 기본값을 덮어쓴다 → "타임게이트만 + btc만 추가"
        # 같은 임의 조합 가능. 둘 다 set 이면 NO_ENTRY_FILTERS(전부 OFF)가 우선.
        # freshness(stale)·universe·capital·dedup 은 데이터/안전 가드라 항상 유지.
        # 근거(2026-06-22 4소스 대조): 롱 필터 차단 바스켓이 죄다 sim net+
        # (=좋은 신호 더 많이 거름, anti-select) → 필터별 on/off 실거래 검증용.
        def _flag(name: str, default: bool) -> bool:
            v = _os.environ.get(name)
            if v is None or not v.strip():
                return default
            return v.strip().lower() in ("1", "true", "yes", "on")

        _no_entry = _flag("AIRBORNE_NO_ENTRY_FILTERS", False)
        _time_only = _flag("AIRBORNE_TIME_GATE_ONLY", False)
        if _no_entry:
            d_time = d_btc = d_sblock = d_vol = d_pump = d_crash = False
        elif _time_only:
            d_time = True
            d_btc = d_sblock = d_vol = d_pump = d_crash = False
        else:
            d_time = d_btc = d_sblock = d_vol = d_pump = d_crash = True
        self._f_time_gate = _flag("AIRBORNE_FILTER_TIME_GATE", d_time)
        self._f_btc_downtrend = _flag("AIRBORNE_FILTER_BTC_DOWNTREND", d_btc)
        self._f_short_block = _flag("AIRBORNE_FILTER_SHORT_BLOCK_HOURS", d_sblock)
        self._f_high_vol = _flag("AIRBORNE_FILTER_HIGH_VOL", d_vol)
        self._f_short_pump = _flag("AIRBORNE_FILTER_SHORT_PUMP", d_pump)
        self._f_long_crash = _flag("AIRBORNE_FILTER_LONG_CRASH", d_crash)
        logger.warning(
            "AirborneFireConsumer entry filters — time_gate=%s btc_downtrend=%s "
            "short_block=%s high_vol=%s short_pump=%s long_crash=%s "
            "(freshness/universe/capital 은 항상 유지)",
            self._f_time_gate, self._f_btc_downtrend, self._f_short_block,
            self._f_high_vol, self._f_short_pump, self._f_long_crash,
        )
        # ── 당일 손익 기반 전체 진입 정지 게이트 3종 (2026-06-27) ─────────────────
        # 오전에 번 걸 오후·밤에 토해내는 패턴(24일~ 반복) 방어. 전부 % of equity
        # 기준, KST 자정 리셋, *신규진입만* 차단(미청산 포지션은 TP/SL 그대로 유지),
        # 다음날 자동 재개(별도 unlock 불필요). daily_pnl_provider 는 PnLAggregator.
        # daily (KST business-date 리셋) 주입. provider 없거나 equity≤0 이면 fail-open.
        #   AIRBORNE_DAILY_GUARDS=1  → 3종 전부 기본 ON (개별 토글로 덮어쓰기 가능)
        #   개별: AIRBORNE_DAILY_PROFIT_LOCK / _GIVEBACK_LOCK / _LOSS_LOCK = 1/0
        #   임계: _PROFIT_TARGET_PCT(3.5) / _GIVEBACK_PCT(40) / _GIVEBACK_ARM_PCT(3.0)
        #   (arm 1.0 은 2026-06-28 +1.2% 노이즈에 종일 정지 사고 → 3.0 으로 상향.
        #    arm 은 target(3.5)보다 낮아야 give-back 이 의미 — target 도달은 profit_lock)
        #         / _LOSS_LIMIT_PCT(3.0)
        # caveat: native TP/SL·수동청산(숫자 coid)은 strategy_id 귀속 실패로
        # aggregator daily 에서 누락될 수 있음 → 게이트가 약간 늦게 걸릴 수 있다.
        # 매크로 AIRBORNE_DAILY_GUARDS=1 → 이익목표+고점반납 2종만 ON.
        # 손실한도는 매크로 제외(2026-06-27 사용자 결정) — 명시 opt-in 만
        # (AIRBORNE_DAILY_LOSS_LOCK=1). 코드는 27일류(초반부터 흘러내림) 대비 보존.
        _all_guards = _flag("AIRBORNE_DAILY_GUARDS", False)
        self._f_profit_lock = _flag("AIRBORNE_DAILY_PROFIT_LOCK", _all_guards)
        self._f_giveback_lock = _flag("AIRBORNE_DAILY_GIVEBACK_LOCK", _all_guards)
        self._f_daily_loss_lock = _flag("AIRBORNE_DAILY_LOSS_LOCK", False)

        def _envf(name: str, default: float) -> float:
            try:
                return float(_os.environ.get(name, "") or default)
            except (TypeError, ValueError):
                return default

        self._daily_profit_target_pct = _envf("AIRBORNE_DAILY_PROFIT_TARGET_PCT", 3.5)
        self._giveback_pct = _envf("AIRBORNE_DAILY_GIVEBACK_PCT", 40.0)
        self._giveback_arm_pct = _envf("AIRBORNE_DAILY_GIVEBACK_ARM_PCT", 3.0)
        self._daily_loss_limit_pct = _envf("AIRBORNE_DAILY_LOSS_LIMIT_PCT", 3.0)
        self._daily_pnl_provider = daily_pnl_provider
        # intraday peak 추적 (KST date 로 키 — 날 바뀌면 리셋). give-back 락 전용.
        self._intraday_peak_date: "date | None" = None
        self._intraday_peak_pnl: float = 0.0
        # 정지 텔레그램 1회/일 통지 dedup.
        self._halt_notified_date: "date | None" = None
        if self._f_profit_lock or self._f_giveback_lock or self._f_daily_loss_lock:
            logger.warning(
                "AirborneFireConsumer DAILY GUARDS — profit_lock=%s(%.1f%%) "
                "giveback_lock=%s(%.0f%% peak≥%.1f%%) loss_lock=%s(-%.1f%%) "
                "[%% of equity, KST 자정 리셋, 신규진입만 차단]",
                self._f_profit_lock, self._daily_profit_target_pct,
                self._f_giveback_lock, self._giveback_pct, self._giveback_arm_pct,
                self._f_daily_loss_lock, self._daily_loss_limit_pct,
            )
        # symbol → (stamp, df|None). 1h 캔들 5분 캐시 — 24h 변화·평균변동폭 공용.
        self._klines_cache: dict[str, tuple] = {}
        # cross-airborne 봉 dedup (2026-06-23): symbol → 마지막 진입 bar_open_key.
        # 전 airborne 전략 공유 → 한 종목-봉 fire 는 통틀어 1회만 진입(순차 재진입
        # 차단). 종목당 1개라 메모리 바운드(per-spec _fired_bar_ts 미러).
        self._entered_bar: dict[str, str] = {}

    # ── BTC trend filter (long 차단) ──────────────────────────────────────────

    def _btc_downtrend(self) -> bool:
        """BTC 하락추세 여부 — strategy 와 동일 로직(_btc_is_downtrend) 재사용.

        provider 미연결 / 데이터 부족 / 예외 시 False (graceful — long block 안 함,
        기존 on_bar consume 의 BTC filter fallback 과 동일 시맨틱).
        """
        if self._btc_ohlcv_provider is None:
            return False
        try:
            btc_hist = self._btc_ohlcv_provider()
        except Exception as err:  # noqa: BLE001 — 게이트 에러로 거래 막지 않음
            logger.warning("airborne_fire_consumer btc provider failed: %s", err)
            return False
        if btc_hist is None or len(btc_hist) == 0:
            return False
        try:
            from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
                _btc_is_downtrend,
            )
            is_down, _reason = _btc_is_downtrend(btc_hist)
            return bool(is_down)
        except Exception as err:  # noqa: BLE001
            logger.warning("airborne_fire_consumer btc trend calc failed: %s", err)
            return False

    # ── dedup 공유 (on_bar consume 과 동일 키) ────────────────────────────────

    @staticmethod
    def _bar_open_key(fire_ts: pd.Timestamp) -> str:
        """fire_ts → dedup 키 = str(bar_open).

        bar_open = floor(fire_ts,1h) − 1h. on_bar consume 의 ``closed_ts`` (봉
        *시작* 시각) 과 동일 → 두 경로가 dedup 을 공유한다.
        """
        bar_close = fire_ts.floor("1h")
        bar_open = bar_close - pd.Timedelta(hours=1)
        return str(bar_open)

    def _dedup_already(self, spec: AirborneStrategySpec, symbol: str, bar_open_key: str) -> bool:
        inst = spec.instance
        if inst is None:
            return False
        try:
            inst._ensure_dedup_loaded()
            return inst._fired_bar_ts.get(symbol) == bar_open_key
        except Exception as err:  # noqa: BLE001
            logger.warning("airborne_fire_consumer dedup read failed: %s", err)
            return False

    def _dedup_mark(self, spec: AirborneStrategySpec, symbol: str, bar_open_key: str) -> None:
        inst = spec.instance
        if inst is None:
            return
        try:
            inst._ensure_dedup_loaded()
            inst._fired_bar_ts[symbol] = bar_open_key
            inst._persist_dedup()
        except Exception as err:  # noqa: BLE001
            logger.warning("airborne_fire_consumer dedup mark failed: %s", err)

    # ── ground-truth 알림 버퍼 (진입/미진입) ──────────────────────────────────

    def _record_entry(
        self, symbol: str, side: str, hour: int, bar_open_key: str
    ) -> None:
        """실제 진입한 발화를 이번 sweep 버퍼에 기록 (_notify 미연결이면 no-op)."""
        if self._notify is None:
            return
        self._entry_buf.append({
            "symbol": symbol, "side": side, "hour": hour,
            "key": (symbol, side, bar_open_key),
        })

    def _record_skip(
        self, symbol: str, side: str, hour: int, bar_open_key: str, reason: str
    ) -> None:
        """게이트로 진입 안 한 발화 + 사유를 이번 sweep 버퍼에 기록.

        ``reason`` 은 텔레그램에 그대로 표기되는 한글 라벨(예: '폭락-23%',
        '고변동9%', '시간게이트', '유니버스밖', 'BTC하락추세', '자본/사이징').
        stale(freshness 초과) / dedup_already / side 불일치는 노이즈라 기록 안 함.
        """
        if self._notify is None:
            return
        self._skip_buf.append({
            "symbol": symbol, "side": side, "hour": hour,
            "key": (symbol, side, bar_open_key), "reason": reason,
        })

    # ── 당일 손익 정지 게이트 ──────────────────────────────────────────────────

    def _evaluate_daily_halt(self, now: datetime) -> "str | None":
        """당일 손익 기반 전체 진입 정지 평가 (KST 자정 리셋).

        반환: None=거래 허용, str=정지 사유(텔레그램/로그 라벨).
        3종(손실한도→이익목표→고점반납) 순서로 평가. 전부 % of equity 기준.
        매 sweep 호출되어 intraday peak 를 갱신하므로 give-back 락이 작동한다.
        provider 미주입 또는 equity≤0(자본 미확보) 이면 fail-open(거래 허용).
        """
        if not (self._f_profit_lock or self._f_giveback_lock
                or self._f_daily_loss_lock):
            return None
        if self._daily_pnl_provider is None:
            return None
        try:
            daily = float(self._daily_pnl_provider())
            equity = float(self._equity_provider() or 0.0)
        except Exception:  # noqa: BLE001 — 게이트 계산 실패는 fail-open
            return None
        if equity <= 0:
            return None

        # KST date rollover → intraday peak 리셋(새 날 시작값 = 현재 daily).
        kst_date = now.astimezone(_KST_TZ).date()
        if self._intraday_peak_date != kst_date:
            self._intraday_peak_date = kst_date
            self._intraday_peak_pnl = daily
        if daily > self._intraday_peak_pnl:
            self._intraday_peak_pnl = daily

        pnl_pct = daily / equity * 100.0
        peak_pct = self._intraday_peak_pnl / equity * 100.0

        # 1. 당일 손실 한도 — 흘러내리는 날(예: 27일) 방어.
        if self._f_daily_loss_lock and self._daily_loss_limit_pct > 0:
            if pnl_pct <= -self._daily_loss_limit_pct:
                return (f"당일손실한도 {pnl_pct:+.1f}%≤-{self._daily_loss_limit_pct:.1f}% "
                        f"(equity={equity:.0f})")
        # 2. 당일 이익 목표 — 오른 날 익절 잠금.
        if self._f_profit_lock and self._daily_profit_target_pct > 0:
            if pnl_pct >= self._daily_profit_target_pct:
                return (f"당일이익목표 {pnl_pct:+.1f}%≥{self._daily_profit_target_pct:.1f}% "
                        f"(equity={equity:.0f})")
        # 3. 고점 반납 락 — 고점이 arm 임계 도달 후, 고점이익의 X% 반납 시 정지.
        if (self._f_giveback_lock and self._giveback_pct > 0
                and peak_pct >= self._giveback_arm_pct
                and self._intraday_peak_pnl > 0):
            trigger = self._intraday_peak_pnl * (1.0 - self._giveback_pct / 100.0)
            if daily <= trigger:
                return (f"당일고점반납 peak={peak_pct:+.1f}%→now={pnl_pct:+.1f}% "
                        f"({self._giveback_pct:.0f}% 반납)")
        return None

    async def _maybe_notify_halt(self, now: datetime, reason: str) -> None:
        """정지 진입 시 텔레그램 1회/KST일 통지(로그는 항상). 절대 raise 안 함."""
        kst_date = now.astimezone(_KST_TZ).date()
        if self._halt_notified_date == kst_date:
            return
        self._halt_notified_date = kst_date
        logger.warning(
            "airborne fire consumer: DAILY HALT — %s. 당일 신규 진입 전면 중단 "
            "(미청산 포지션 TP/SL 유지, 내일 자동 재개).", reason,
        )
        if self._notify is None:
            return
        try:
            await asyncio.to_thread(
                self._notify,
                f"🛑 당일 거래 정지 — {reason}\n"
                f"신규 진입 중단. 미청산 포지션은 TP/SL 그대로. 내일 자동 재개.",
            )
        except Exception as err:  # noqa: BLE001
            logger.warning("airborne_fire_consumer halt notify failed: %s", err)

    # ── sweep ────────────────────────────────────────────────────────────────

    async def sweep_once(self) -> int:
        """now−freshness 이후 발화를 1회 sweep — 진입한 발화 수 반환.

        각 발화는 try/except 로 감싸 한 발화의 예외가 sweep 전체를 죽이지
        않는다.
        """
        now = datetime.now(timezone.utc)
        # 당일 손익 정지 게이트 — 트립 시 이번 sweep 진입 전면 skip(전략 무관).
        # peak 추적 때문에 매 sweep 평가해야 한다(여기서 한 번만 호출).
        halt_reason = self._evaluate_daily_halt(now)
        if halt_reason is not None:
            await self._maybe_notify_halt(now, halt_reason)
            return 0
        since = now - timedelta(seconds=self._freshness_sec)
        try:
            fires = self._store.load_since(since)
        except Exception as err:  # noqa: BLE001
            logger.warning("airborne_fire_consumer load_since failed: %s", err)
            return 0
        # load_since 가 ts 오름차순 정렬 보장하지만 방어적으로 재정렬.
        fires = sorted(fires, key=lambda r: str(r.get("ts", "")))
        entered = 0
        # BTC 하락추세는 sweep 당 1회만 계산해 캐시 (long 발화마다 200h EMA
        # 재계산 회피). long 발화가 하나도 없으면 아예 안 본다 (lazy).
        self._btc_down_cache: bool | None = None
        # 이번 sweep 의 진입/미진입 이벤트 버퍼 (ground-truth 알림용).
        self._entry_buf: list[dict] = []
        self._skip_buf: list[dict] = []
        for f in fires:
            try:
                if await self._consume_one(f, now):
                    entered += 1
            except Exception as err:  # noqa: BLE001 — 한 발화 실패가 sweep 죽이면 안 됨
                logger.warning(
                    "airborne_fire_consumer fire failed sym=%s err=%s",
                    f.get("symbol"), err,
                )
        # 진입/미진입 ground-truth 알림 (절대 raise 안 함).
        try:
            await self._notify_events()
        except Exception as err:  # noqa: BLE001
            logger.warning("airborne_fire_consumer event-notify failed: %s", err)
        if entered:
            logger.info(
                "airborne fire consumer: %d entries (scanned %d fires since %s)",
                entered, len(fires), since.isoformat(),
            )
        return entered

    def _btc_down_cached(self) -> bool:
        """sweep 당 1회만 BTC 하락추세 계산 — 캐시. sweep_once 가 매 sweep
        시작 시 ``_btc_down_cache=None`` 으로 리셋한다."""
        if getattr(self, "_btc_down_cache", None) is None:
            self._btc_down_cache = self._btc_downtrend()
        return bool(self._btc_down_cache)

    async def _get_klines(self, symbol: str):
        """klines_fetcher 로 1h 캔들 fetch, 5분 캐시 (df). 24h 변화·변동폭 공용.
        실패/미상장 시 None (fail-open). None 도 캐시해 5분 내 재fetch 폭주 차단."""
        from datetime import datetime, timezone
        nowt = datetime.now(timezone.utc)
        c = self._klines_cache.get(symbol)
        if c is not None and (nowt - c[0]).total_seconds() < 300:
            return c[1]
        df = None
        try:
            df = await self._klines_fetcher(symbol)
            if df is None or len(df) < 25:
                df = None
        except Exception as err:  # noqa: BLE001 — 게이트 에러로 거래 막지 않음
            logger.warning("airborne klines fetch failed sym=%s: %s", symbol, err)
            df = None
        self._klines_cache[symbol] = (nowt, df)
        return df

    async def _token_24h_change(self, symbol: str) -> "float | None":
        """직전 24h % 변화 (모멘텀 필터용). 미가용 시 None (fail-open)."""
        df = await self._get_klines(symbol)
        if df is None:
            return None
        cl = df["close"]; last = float(cl.iloc[-1]); prev = float(cl.iloc[-25])
        return (last - prev) / prev * 100 if prev > 0 else None

    async def _token_avg_1h_range(self, symbol: str) -> "float | None":
        """최근 1h 평균 변동폭%((high-low)/close). 변동성 필터용. 미가용 시 None."""
        df = await self._get_klines(symbol)
        if df is None:
            return None
        try:
            rng = (df["high"] - df["low"]) / df["close"] * 100
            rng = rng[df["close"] > 0]
            return float(rng.mean()) if len(rng) else None
        except Exception:  # noqa: BLE001
            return None

    async def _consume_one(self, fire: dict, now: datetime) -> bool:
        symbol = str(fire.get("symbol", ""))
        side = str(fire.get("side", "")).lower()
        if not symbol or side not in ("long", "short"):
            return False
        try:
            fire_close = float(fire.get("fire_close", 0) or 0)
        except (TypeError, ValueError):
            return False
        if not (fire_close > 0):
            return False

        # fire_ts 파싱 (UTC).
        try:
            fire_ts = pd.Timestamp(str(fire.get("ts", "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        if fire_ts.tzinfo is None:
            fire_ts = fire_ts.tz_localize("UTC")

        # freshness — now−fire_ts ≤ freshness_sec (재시작 backlog 재매수 차단).
        # 롱은 짧은 cap: 봉마감 직후에만 진입, BTC 추세필터로 지연돼 묵은 가격에
        # 늦게 들어가는 것(8분 stale → NAKED) 차단. 숏은 기존 freshness 유지.
        age_sec = (now - fire_ts.to_pydatetime()).total_seconds()
        eff_freshness = (
            self._long_freshness_sec if side == "long" else self._freshness_sec
        )
        if age_sec > eff_freshness:
            logger.info(
                "airborne fire skip sym=%s side=%s reason=stale age=%.0fs cap=%.0fs",
                symbol, side, age_sec, eff_freshness,
            )
            return False

        # 도착시각 게이트 — floor(fire_ts,1h).KST.hour.
        bar_close = fire_ts.floor("1h")
        hour_kst = int(bar_close.tz_convert(_KST).hour)
        bar_open_key = self._bar_open_key(fire_ts)

        # 숏 차단 시간대 (2026-06-15) — KST 07시 등에 숏 다발 = 상승추세 신호로
        # 보고 SHORT 진입 안 함. LONG 은 통과. short-whitelist·kst-hours 양쪽
        # 07 숏을 단일 지점에서 차단(07 롱은 kst-hours 게이트로 그대로 진입).
        if (side == "short" and self._f_short_block
                and hour_kst in self._short_block_hours):
            self._record_skip(symbol, side, hour_kst, bar_open_key, "숏차단시각")
            return False

        # ── 모멘텀 진입 필터 (2026-06-17, symbol+side 레벨) ──────────────────
        # 숏: 직전24h +X%↑ 펌핑 → 스퀴즈/슬립 위험으로 skip. 롱: -Y%↓ 폭락 →
        # 떨어지는칼 skip. fetcher 미주입(테스트) 또는 임계 0 이면 비활성. 토큰
        # OHLCV 미가용(Bitget-only 등) 시 None → fail-open(허용).
        if self._klines_fetcher is not None:
            # 변동성 필터 (양방향) — 코인 최근 평균 1h 변동폭% > 임계면 skip.
            # 초고변동 코인(SKYAI/SIREN)은 -1% SL 이 무의미해 stop 슬립 -19%.
            if self._f_high_vol and self._max_vol_pct > 0:
                vol = await self._token_avg_1h_range(symbol)
                if vol is not None and vol > self._max_vol_pct:
                    logger.info(
                        "airborne fire skip sym=%s side=%s reason=high_volatility "
                        "avg1h=%.1f%% (>%.1f)", symbol, side, vol, self._max_vol_pct,
                    )
                    self._record_skip(
                        symbol, side, hour_kst, bar_open_key, f"고변동{vol:.0f}%"
                    )
                    return False
            if side == "short" and self._f_short_pump and self._short_pump_skip > 0:
                chg = await self._token_24h_change(symbol)
                if chg is not None and chg > self._short_pump_skip:
                    logger.info(
                        "airborne fire skip sym=%s side=short reason=momentum_pump "
                        "24h=%+.1f%% (>+%.1f)", symbol, chg, self._short_pump_skip,
                    )
                    self._record_skip(
                        symbol, side, hour_kst, bar_open_key, f"펌핑+{chg:.0f}%"
                    )
                    return False
            elif side == "long" and self._f_long_crash and self._long_crash_skip > 0:
                chg = await self._token_24h_change(symbol)
                if chg is not None and chg < -self._long_crash_skip:
                    logger.info(
                        "airborne fire skip sym=%s side=long reason=momentum_crash "
                        "24h=%+.1f%% (<-%.1f)", symbol, chg, self._long_crash_skip,
                    )
                    self._record_skip(
                        symbol, side, hour_kst, bar_open_key, f"폭락{chg:.0f}%"
                    )
                    return False

        entered_any = False
        # 미진입 시 binding 사유 추적 (ground-truth 알림용). side 매칭 spec 중 hour
        # 게이트를 하나도 못 통과하면 "시간게이트", universe/btc/capital 로 막혔으면
        # 그 사유를 표기. side 불일치/dedup_already 만으로 막힌 건 사유 없음(노이즈).
        any_passed_hour = False
        hour_blocked = False
        spec_block_reason: str | None = None
        for spec in self._specs:
            if side not in spec.allowed_sides:
                continue
            if self._f_time_gate and hour_kst not in spec.kst_entry_hours:
                hour_blocked = True
                continue
            any_passed_hour = True
            if spec.universe is not None and symbol not in spec.universe:
                logger.info(
                    "airborne fire skip sid=%s sym=%s side=%s reason=not_in_universe",
                    spec.id, symbol, side,
                )
                spec_block_reason = spec_block_reason or "유니버스밖"
                continue
            if (side == "long" and spec.btc_filter and self._f_btc_downtrend
                    and self._btc_down_cached()):
                logger.info(
                    "airborne fire skip sid=%s sym=%s side=long reason=btc_downtrend",
                    spec.id, symbol,
                )
                spec_block_reason = spec_block_reason or "BTC하락추세"
                continue
            # cross-airborne 봉 dedup (2026-06-23) — 한 종목-봉 fire 는 airborne 전체
            # 통틀어 1회만 진입. A 가 진입 후 *청산해도* B 가 같은 fire 재진입 못 함
            # (DEXE 10:00봉: bb-reversal 진입→10:05 청산 → short-whitelist 10:05:48
            # 같은 fire 재숏 사고). per-spec _dedup_already 는 전략별 dedup 파일이라
            # cross-strategy 를 못 봄 → 공유 dict ``_entered_bar`` 로 차단. #471 의
            # _live_entered "동시보유" 차단을 봉 단위로 보완(순차 재진입까지 커버).
            if self._entered_bar.get(symbol) == bar_open_key:
                continue
            if self._dedup_already(spec, symbol, bar_open_key):
                continue
            intent = self._orch.dispatch_fire_entry(
                spec.id, symbol, side,
                price=fire_close, ts=fire_ts.isoformat(),
                equity_usdt=float(self._equity_provider()),
            )
            if intent is None:
                logger.info(
                    "airborne fire skip sid=%s sym=%s side=%s reason=dispatch_none "
                    "(sizing/capital/이미진입)",
                    spec.id, symbol, side,
                )
                spec_block_reason = spec_block_reason or "자본/사이징"
                continue
            # 발주 (run_bar OrderIntent 와 동일 라우팅) → dedup 마크. 발주를
            # await 한 뒤에 dedup 을 찍어 미발주분 재시도 가능 (orchestrator 의
            # _live_entered 는 dispatch_fire_entry 가 이미 잡음 — 중복 진입 방지).
            await self._route([intent])
            self._dedup_mark(spec, symbol, bar_open_key)
            self._entered_bar[symbol] = bar_open_key  # cross-airborne 봉 dedup 마크
            logger.info(
                "airborne fire entry sid=%s sym=%s side=%s price=%s kst=%d",
                spec.id, symbol, side, fire_close, hour_kst,
            )
            entered_any = True
            self._record_entry(symbol, side, hour_kst, bar_open_key)
            # ③ 페이싱 — 발주 사이 간격(rate-limit 회피). 0 이면 skip.
            if self._pace_sec > 0:
                await asyncio.sleep(self._pace_sec)

        # 미진입 ground-truth 기록 — 진입한 발화는 entry 로만 잡고 skip 안 함.
        # 사유 우선순위: spec 사유(universe/btc/capital) > 시간게이트. side 불일치/
        # dedup_already 만으로 막힌 건 사유 없음 → 알림 제외(노이즈).
        if not entered_any:
            if spec_block_reason is not None:
                self._record_skip(
                    symbol, side, hour_kst, bar_open_key, spec_block_reason
                )
            elif hour_blocked and not any_passed_hour:
                self._record_skip(
                    symbol, side, hour_kst, bar_open_key, "시간게이트"
                )
        return entered_any

    async def _notify_events(self) -> None:
        """이번 sweep 의 실진입/미진입을 ground-truth 텔레그램으로 통지.

        - 실진입: ``✅ 실진입 N건: SYM(롱/숏), ...`` (실제 발주된 발화만)
        - 미진입: ``❌ 미진입 N건: SYM(숏) 사유, ...`` (게이트로 막힌 발화 + 사유,
          한 메시지 inline 요약)

        실거래(consumer)가 직접 통지하므로 알림 데몬의 "진입 예정" 예측과 달리
        실제 매수와 100% 일치한다. 같은 발화 재알림 방지를 위해 통지 key 를
        ``_entry_notified`` / ``_skip_notified`` 에 마크. ``_notify`` 미연결이면
        no-op. sync notify 는 to_thread 로 호출(blocking 회피). 절대 raise 안 함.
        """
        if self._notify is None:
            return

        async def _send(text: str) -> None:
            try:
                await asyncio.to_thread(self._notify, text)
            except Exception as err:  # noqa: BLE001 — 알림 실패가 거래 막지 않음
                logger.warning("airborne event notify failed: %s", err)

        # ── 실진입 ──────────────────────────────────────────────────────────
        ent_tokens: list[str] = []
        for r in getattr(self, "_entry_buf", []):
            if r["key"] in self._entry_notified:
                continue
            self._entry_notified.add(r["key"])
            side_ko = "숏" if r["side"] == "short" else "롱"
            ent_tokens.append(f"{r['symbol']}({side_ko})")
        if ent_tokens:
            head = ", ".join(ent_tokens[:20])
            more = f" 외 {len(ent_tokens) - 20}건" if len(ent_tokens) > 20 else ""
            await _send(f"✅ 실진입 {len(ent_tokens)}건: {head}{more}")

        # ── 미진입 (사유 inline) ────────────────────────────────────────────
        skip_tokens: list[str] = []
        for r in getattr(self, "_skip_buf", []):
            if r["key"] in self._skip_notified:
                continue
            self._skip_notified.add(r["key"])
            side_ko = "숏" if r["side"] == "short" else "롱"
            skip_tokens.append(f"{r['symbol']}({side_ko}) {r['reason']}")
        if skip_tokens:
            head = ", ".join(skip_tokens[:15])
            more = f" 외 {len(skip_tokens) - 15}건" if len(skip_tokens) > 15 else ""
            await _send(f"❌ 미진입 {len(skip_tokens)}건: {head}{more}")

        # dedup set 무한증식 방지 — freshness 밖 발화는 재로드 안 되므로 대량
        # 누적 시 통째 비워도 재알림 위험 낮음.
        if len(self._skip_notified) > 5000:
            self._skip_notified.clear()
        if len(self._entry_notified) > 5000:
            self._entry_notified.clear()

    async def _route(self, intents: list) -> None:
        """route_intents 호출 — sync/async 양쪽 지원 (테스트는 sync spy).

        route_intents 가 coroutine 을 반환하면 await, 아니면 즉시 반환.
        """
        result = self._route_intents(intents)
        if asyncio.iscoroutine(result):
            await result

    # ── run loop ───────────────────────────────────────────────────────────────

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        """interval_sec 마다 sweep_once — stop_event 까지. 절대 raise 안 함.

        PositionReconciler.run_loop 구조를 mirror.
        """
        logger.info(
            "airborne fire-driven consumer started (decoupled from bar loop) "
            "interval=%.1fs freshness=%.0fs specs=%d",
            self._interval_sec, self._freshness_sec, len(self._specs),
        )
        while not stop_event.is_set():
            try:
                await self.sweep_once()
            except Exception as err:  # noqa: BLE001 — loop 절대 안 죽음
                logger.warning("airborne_fire_consumer sweep failed: %s", err)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_sec)
            except asyncio.TimeoutError:
                pass  # next cycle
        logger.info("airborne fire-driven consumer stopped")
