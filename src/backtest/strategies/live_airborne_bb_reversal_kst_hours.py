"""Live-scanner: Pine v1.2 airborne BB-reversal (bidir) + KST {1,2,3,5,6,7,8,23}시 게이트 (v3+5시).

[[live-airborne-bb-reversal-kst-morning]] (rejected, PF 0.906) 의 후속.
*시각 단일 블록* (06-12) 이 over-fit 임이 5y 데이터로 증명된 후, v2 는
5y 분석 + 30d sim_cache 데이터 기반으로 {7,8,16,20,22} 로 설정됐으나
13일 1분봉 실측 분석에서 v3 로 재설계.

## v3 게이트 선정 근거 (2026-06-06)

`logs/airborne_fires/sim_cache_1m.jsonl` 의 13일 1분봉 실측 hour-of-day 분석:

새벽~아침 {1,2,3,6,7,8,23} 이 순손익/PF 최상위 (net +68%, PF 2.39).
옛 v2 의 16시 (PF 0.15) · 22시 (PF 0.61) 가 손실 누적 확인.

⚠️ CAVEAT: 13일 in-sample 선정 — 5y bench 미검증이며 5y hourly 분석은 다른
시각 ({8,11,16,22}) 을 선호. hour-of-day 알파가 윈도우마다 불안정 →
과적합 위험. 운영자 직접 판단으로 적용, 5y walk-forward 검증 전까지 모니터링 필요.

## 데몬과 분리

`scripts/airborne_alert_daemon.py` 의 Telegram FIRE 알림은 24h 그대로 발화.
본 전략은 같은 signal 모듈을 orchestrator 안에서 직접 호출하므로 daemon
코드/설정 일체 무수정.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
)

# v3 (2026-06-06): 13일 1분봉 실측(logs/airborne_fires/sim_cache_1m.jsonl)
# hour-of-day 분석에서 새벽~아침 {1,2,3,6,7,8,23} 이 순손익/PF 최상위
# (net +68%, PF 2.39), 옛 v2 의 16시(PF 0.15)·22시(PF 0.61)가 손실 누적.
# 23시 추가(2026-06-06): 23시 숏 PF 2.09(+7.5%), 롱은 손실(PF 0.69)이나 BTC trend filter 가 약세장 23시 롱을 차단해 숏만 잔존 → 운영자 추가. 동일 in-sample caveat 적용.
# 5시 추가(2026-06-18): 라이브 에어본 포착분(sim_cache_2pct, 6/04~) 시각별 PF 에서
# 5시 롱 PF 2.22(n=63, win 66.7%, +20.3) — 현재 게이트 평균(PF 1.75) 상회. 5시 숏도
# PF 1.23(+3.0) 양수 → 양방향 공유 게이트에 추가. ⚠️ 인접 4시는 PF 0.13(최악)이라
# 제외 유지. 5y hourly 로는 5시 롱 PF 0.99(≈본전)라 최근-윈도우 알파 — 모니터링.
# ⚠️ CAVEAT: 13일 in-sample 선정 — 5y bench 미검증이며 5y hourly 분석은
# 다른 시각({8,11,16,22})을 선호. hour-of-day 알파가 윈도우마다 불안정 →
# 과적합 위험. 운영자 직접 판단으로 적용, 5y walk-forward 검증 전까지 모니터링 필요.
_KST_TOP_HOURS_V3: frozenset[int] = frozenset({1, 2, 3, 5, 6, 7, 8, 23})

# BTC trend filter (2026-06-05) — airborne 이 시장 전체 하락추세에서 LONG 잡는
# 사고 차단. 6/04 incident: bb-reversal 보유 14 LONG 종목이 새벽~오전에 전량
# -3% SL 동시 청산. 동일 stop_loss_pct + LONG 편향에서 시장 동조 손실. journal
# 분석의 "portfolio-level stop 또는 correlation-aware position sizing" 권고
# 반영 — 더 단순한 접근: BTC 하락추세 시 LONG entry 자체 차단.
_BTC_SYMBOL: str = "BTCUSDT"
_BTC_EMA_PERIOD_HOURS: int = 200      # 약 8일
_BTC_DOWNTREND_PCT: float = -0.02    # AND 조건: 직전 24h BTC < -2% (급락) 일 때만


def _btc_is_downtrend(
    btc_hist: pd.DataFrame,
    *,
    ema_period: int = _BTC_EMA_PERIOD_HOURS,
    drawdown_threshold: float = _BTC_DOWNTREND_PCT,
) -> tuple[bool, str]:
    """BTC 하락추세 — EMA200 하회 **AND** 24h 급락(<-2%) 둘 다일 때만 (2026-06-19 강화).

    옛 로직은 두 조건 OR(EMA200 하회 *또는* 24h<-1%) 였으나, EMA200 은 1h 기준
    ~8일 지연이라 EMA200 근처 횡보·회복장에서 *멀쩡한 롱을 떼로 차단*했다 (최근 7일
    실측: 차단된 롱 승률 80%·net +48% — 이기는 롱을 죽임). 6/04 같은 *급락 사고*만
    막도록 **AND/-2%** 로 좁힘: EMA200 아래(추세 약세) 이면서 동시에 24h −2% 급락
    중일 때만 LONG 차단. 단일 조건(횡보 하회 or 단발 딥)으로는 차단 안 함.

    ⚠️ 5y 보호력은 약해짐(OR 가 5y 보호 최강) — 레짐 의존적 trade-off. 약세장 전환
    시 재강화 판단은 일일 필터 감사(docs/routines/cs-tsmom-daily-report.md) 로.

    데이터 부족(EMA200 200봉 or 24h 25봉 미달) 시 False (graceful — long block 안 함).

    Returns:
      (is_downtrend, reason)
    """
    if btc_hist is None or len(btc_hist) < ema_period:
        return False, "insufficient_btc_history"
    close = btc_hist["close"]
    if len(close) < 25:
        return False, "insufficient_24h_history"
    last_close = float(close.iloc[-1])
    ema = close.ewm(span=ema_period, adjust=False).mean()
    below_ema = last_close < float(ema.iloc[-1])
    prev_24h = float(close.iloc[-25])
    ret_24h = (last_close - prev_24h) / prev_24h
    if below_ema and ret_24h < drawdown_threshold:
        return True, (
            f"btc_downtrend (below_ema200 & 24h={ret_24h*100:.2f}% < "
            f"{drawdown_threshold*100:.1f}%)"
        )
    return False, "btc_uptrend_or_neutral"


class LiveAirborneBbReversalKstHours(LiveAirborneBbReversalKstMorning):
    """v1.2 bidir airborne + KST hour gate + BTC trend filter (2026-06-05).

    Parent 와 동일한 시그널·청산·warmup. 두 가지 차이:
      1. KST entry hours = {1,2,3,5,6,7,8,23} (v3+5시) — 새벽~아침+23시 시각.
         ⚠️ 13일 in-sample 선정, 5y 미검증, 과적합 위험.
      2. BTC trend filter — BTC 가 하락추세이면 LONG entry 자체 차단 (short 은
         그대로). 시장 동조 손실 (6/04 incident) 차단.

    BTC trend filter 는 default 활성 (instance kwarg ``btc_trend_filter_enabled``
    로 끄기 가능 — 옛 동작 byte-identical 회귀 가드용).
    """

    # ClassVar 명시 — completeness check (static AST scan) 가 inheritance 추적
    # 안 하므로 stop/TP 도 명시. 값은 부모와 동일 (instance ctor 가 override 가능).
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    kst_entry_hours: ClassVar[frozenset[int]] = _KST_TOP_HOURS_V3

    # 새 instance attr — BTC trend filter 토글. default True (활성).
    btc_trend_filter_enabled: bool = True

    def __init__(self, *args, btc_trend_filter_enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.btc_trend_filter_enabled = bool(btc_trend_filter_enabled)
        # 봉마감 게이트 dedup — symbol → 마지막 진입한 마감봉 ts. 같은 마감봉엔
        # 한 번만 진입(데몬은 봉당 1회 알림 → 트레이더도 봉당 1회 진입, TP/SL
        # 청산 후 같은 봉 재진입 폭주 방지). live 에서만 채워짐.
        self._fired_bar_ts: dict[str, object] = {}

    # Dynamic Universe Architecture (2026-05-28):
    # - Phase 1: interval = "1h" (이전 1d → 사실상 무용지물이던 문제 해결)
    # - Phase 2: universe = daemon top-100 dynamic (24h 거래량 기반).
    #   binance_top_dynamic.get_top_n_symbols(100) — 5분 캐시 + fetch 실패 시
    #   정적 BINANCE_USDT_TOP30 fallback (graceful, 매매 안 멈춤).
    @classmethod
    def get_interval(cls) -> str:
        return "1h"

    # 유니버스 크기 — env ``AIRBORNE_UNIVERSE_TOP_N`` (기본 200).
    # 2026-06-22 — 100 → 200 확장. top-100 이 알림 온 TP 승자를 ``not_in_universe``
    # 로 거부하던 문제(4주 차단 바스켓 net +19.1% / PF 1.56, n=65 — 거른 게 순이득
    # 신호였음). 알림 데몬도 같은 env 를 읽어 유니버스 정합. 롤백 = env=100.
    @staticmethod
    def _universe_top_n() -> int:
        import os
        try:
            return max(1, int(os.environ.get("AIRBORNE_UNIVERSE_TOP_N", "200") or 200))
        except ValueError:
            return 200

    @classmethod
    def get_universe(cls) -> list[str]:
        """24h 거래량 top-N USDT-perp (env AIRBORNE_UNIVERSE_TOP_N, 기본 200) —
        venue 자동 라우팅.

        2026-06-05 — Binance / Bitget 동시 운영. env ``QTA_BROKER_VENUE`` 가
        ``bitget`` 이면 Bitget 거래량 기준 (Bitget 미상장 종목 사전 제외 →
        ``status=400`` 폭주 + API rate-limit 낭비 차단). 그 외 (기본/binance)
        는 기존 Binance 동작.
        2026-06-22 — top-N 을 env 로 확장(기본 100→200). 상세는 위 주석.
        """
        import os
        n = cls._universe_top_n()
        venue = os.environ.get("QTA_BROKER_VENUE", "").strip().lower()
        if venue == "bitget":
            from src.portfolio.bitget_top_dynamic import get_top_n_symbols
            return get_top_n_symbols(n)
        from src.portfolio.binance_top_dynamic import get_top_n_symbols
        return get_top_n_symbols(n)

    def _bar_interval_sec(self) -> int:
        iv = str(self.get_interval())
        try:
            if iv.endswith("h"):
                return int(iv[:-1]) * 3600
            if iv.endswith("m"):
                return int(iv[:-1]) * 60
            if iv.endswith("d"):
                return int(iv[:-1]) * 86400
        except ValueError:
            pass
        return 3600

    def _bar_close_gate(self, ctx):
        """live 에서 형성 중인 마지막 봉을 마감봉으로 치환 (데몬과 정렬).

        데몬(텔레그램 알림)은 마감된 1h봉에서만 발화하는데, base 전략은
        history 의 iloc[-1](live 에선 진행 중 봉)을 평가해 봉 한가운데서도
        발화 → 알림에 없는 종목 매수 (2026-06-08 PIPPINUSDT 사고). live
        (ctx['live_run']=True) 이고 마지막 봉이 아직 형성 중이면 그 봉을 떼고
        마감봉 기준으로 평가하도록 ctx 를 치환한다.

        backtest(bench_live_scanner)는 on_bar 를 직접 호출하며 ctx 에
        ``live_run`` 이 없어 그대로 통과 → 기존 동작 byte-identical.

        반환: (평가용 ctx, 마감봉 ts). 마감봉 부족 시 (None, None).
        """
        if not (isinstance(ctx, dict) and ctx.get("live_run")):
            return ctx, None
        snap = ctx.get("market_snapshot")
        if not isinstance(snap, dict):
            return ctx, None
        hist = snap.get("history")
        if hist is None or len(hist) < 2:
            return ctx, None
        try:
            now = pd.Timestamp(ctx.get("ts"))
            last_open = pd.Timestamp(hist.index[-1])
        except (ValueError, TypeError):
            return ctx, None
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
        if last_open.tzinfo is None:
            last_open = last_open.tz_localize("UTC")
        interval = pd.Timedelta(seconds=self._bar_interval_sec())
        if now >= last_open + interval:
            # 마지막 봉이 이미 마감 (종료시각 지남) → 그대로 평가.
            return ctx, last_open
        # 형성 중 → 마지막(미완성) 봉 제거, 마감봉으로 평가.
        trimmed = hist.iloc[:-1]
        if len(trimmed) < 2:
            return None, None
        new_snap = dict(snap)
        new_snap["history"] = trimmed
        try:
            new_snap["price"] = float(trimmed["close"].iloc[-1])
        except (ValueError, TypeError, KeyError):
            pass
        new_ctx = dict(ctx)
        new_ctx["market_snapshot"] = new_snap
        return new_ctx, pd.Timestamp(trimmed.index[-1])

    # ── 재진입 차단 dedup 영속 (live only) ──────────────────────────────────────

    def _dedup_path(self):
        from pathlib import Path
        import os
        base = os.environ.get("AIRBORNE_REENTRY_STATE_DIR", "logs/airborne_reentry")
        return Path(base) / f"{type(self).__name__}.json"

    def _ensure_dedup_loaded(self) -> None:
        if getattr(self, "_dedup_loaded", False):
            return
        self._dedup_loaded = True
        try:
            import json
            path = self._dedup_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        self._fired_bar_ts.setdefault(str(k), str(v))
        except Exception:  # noqa: BLE001 — 영속 실패가 매매를 막으면 안 됨
            pass

    def _persist_dedup(self) -> None:
        try:
            import json
            path = self._dedup_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({k: str(v) for k, v in self._fired_bar_ts.items()}),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    # ── 데몬-발화 게이트 (live only) ─────────────────────────────────────────────

    def _get_fire_store(self):
        cached = getattr(self, "_fire_store_cache", "UNSET")
        if cached != "UNSET":
            return cached
        store = None
        try:
            import os
            from src.dashboard.airborne_fire_store import AirborneFireStore
            path = os.environ.get(
                "AIRBORNE_FIRE_STORE_PATH", "logs/airborne_fires/history.jsonl",
            )
            store = AirborneFireStore(path)
        except Exception:  # noqa: BLE001
            store = None
        self._fire_store_cache = store
        return store

    def _daemon_fired(self, symbol: str, action: str, closed_ts) -> bool:
        """데몬(텔레그램 알림 소스)이 *이 종목·이 봉·이 방향* 으로 발화했나.

        데몬 fire ``ts`` = 봉 *마감* 시각, 트레이더 ``closed_ts`` = 봉 *시작* →
        ``floor(fire_ts, 1h) == closed_ts + 1봉`` 이면 같은 봉. fire 소스
        미가용(파일 부재)이면 fail-open(True) — 알림 인프라가 죽었다고 트레이딩을
        멈추지 않음(기존 동작 fallback).
        """
        store = self._get_fire_store()
        if store is None or not store.path.exists():
            return True  # fire 소스 미가용 → 게이트 무력화 (fail-open)
        side = "long" if action == "buy" else "short"
        try:
            from datetime import timezone as _tz
            bar_open = pd.Timestamp(closed_ts)
            if bar_open.tzinfo is None:
                bar_open = bar_open.tz_localize("UTC")
            interval = pd.Timedelta(seconds=self._bar_interval_sec())
            bar_close = (bar_open + interval).floor("1h")
            since = (bar_open - interval).to_pydatetime()
            if since.tzinfo is None:
                since = since.replace(tzinfo=_tz.utc)
            fires = store.load_since(since)
        except Exception:  # noqa: BLE001 — 게이트 에러로 거래 막지 않음
            return True
        for f in fires:
            if str(f.get("symbol", "")) != symbol:
                continue
            if str(f.get("side", "")).lower() != side:
                continue
            try:
                f_ts = pd.Timestamp(f.get("ts"))
                if f_ts.tzinfo is None:
                    f_ts = f_ts.tz_localize("UTC")
                if f_ts.floor("1h") == bar_close:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    def _apply_daemon_gate_and_dedup(self, ctx, sig, closed_ts):
        """진입(buy/sell)에 데몬-발화 게이트 + 같은봉 dedup(영속) 적용.

        두 airborne 전략(KstHours·ShortWhitelist)이 공유. closed_ts None
        (backtest)이거나 진입 아니면 sig 그대로. 게이트 차단 시 hold 반환
        (dedup 미기록 → store 갱신되면 다음 평가에 재시도). 같은 봉 재진입은
        디스크 영속 dedup 으로 차단 (재시작해도 유지 → 재시작 재매수 방지).
        """
        if closed_ts is None or getattr(sig, "action", None) not in ("buy", "sell"):
            return sig
        snap = ctx.get("market_snapshot") if isinstance(ctx, dict) else None
        symbol = snap.get("symbol") if isinstance(snap, dict) else None
        if symbol is None:
            return sig
        from backtest.protocol import Signal as _Sig
        if not self._daemon_fired(symbol, sig.action, closed_ts):
            return _Sig(action="hold", size=0.0,
                        reason=f"no_daemon_fire:{symbol}@{closed_ts}")
        self._ensure_dedup_loaded()
        if self._fired_bar_ts.get(symbol) == str(closed_ts):
            return _Sig(action="hold", size=0.0,
                        reason=f"already_entered_bar:{closed_ts}")
        self._fired_bar_ts[symbol] = str(closed_ts)
        self._persist_dedup()
        return sig

    # ── consume 모드 — 데몬 발화를 그대로 따라 진입 (자체평가 대신) ──────────────

    def _daemon_fire_side(self, symbol: str, closed_ts) -> str | None:
        """데몬이 이 종목·이 봉에 발화한 side ("long"/"short") 또는 None.

        매칭은 _daemon_fired 와 동일 (floor(fire ts,1h)==closed_ts+1봉). store
        미가용이면 None (consume 은 fail-closed — 발화 데이터 없으면 진입 안 함).
        """
        store = self._get_fire_store()
        if store is None or not store.path.exists():
            return None
        try:
            from datetime import timezone as _tz
            bar_open = pd.Timestamp(closed_ts)
            if bar_open.tzinfo is None:
                bar_open = bar_open.tz_localize("UTC")
            interval = pd.Timedelta(seconds=self._bar_interval_sec())
            bar_close = (bar_open + interval).floor("1h")
            since = (bar_open - interval).to_pydatetime()
            if since.tzinfo is None:
                since = since.replace(tzinfo=_tz.utc)
            fires = store.load_since(since)
        except Exception:  # noqa: BLE001
            return None
        for f in fires:
            if str(f.get("symbol", "")) != symbol:
                continue
            try:
                f_ts = pd.Timestamp(f.get("ts"))
                if f_ts.tzinfo is None:
                    f_ts = f_ts.tz_localize("UTC")
                if f_ts.floor("1h") == bar_close:
                    s = str(f.get("side", "")).lower()
                    if s in ("long", "short"):
                        return s
            except (ValueError, TypeError):
                continue
        return None

    @staticmethod
    def _consume_enabled() -> bool:
        import os
        return os.environ.get("AIRBORNE_CONSUME_DAEMON_FIRES", "0") == "1"

    @staticmethod
    def _fire_consumer_active() -> bool:
        """봉루프 decouple 한 AirborneFireConsumer 가 활성인지 (2026-06-11).

        활성 시 on_bar consume 분기는 hold 반환 — 진입은 fire consumer 가
        history.jsonl 발화를 직접 구동해 담당한다 (이중경로 차단). dedup 은
        양쪽이 공유하므로 안전망은 이중이나, 명시적으로 봉루프 consume 을 끈다.
        상세: docs/specs/airborne-fire-driven-consume.md.
        """
        import os
        return os.environ.get("AIRBORNE_FIRE_CONSUMER", "0") == "1"

    def _consume_daemon_fire_on_bar(self, ctx, closed_ts, history, symbol, allowed_sides):
        """consume — 데몬 발화를 그대로 진입. KST 게이트 + side 필터 + 같은봉 dedup.

        자체 airborne 평가를 *완전히 대체* → 거래 = 데몬 알림 100% (입력 데이터
        차이로 인한 종목 불일치 제거). allowed_sides: kst-hours={long,short},
        short-whitelist={short}.
        """
        # 봉루프 decouple consumer 활성 시 on_bar consume 은 hold — 진입은
        # AirborneFireConsumer 가 발화를 직접 구동해 담당 (이중경로 차단).
        if self._fire_consumer_active():
            return Signal(action="hold", size=0.0,
                          reason="fire_consumer_active:onbar_consume_disabled")
        from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
            _bar_hour_kst,
        )
        # KST 시간 게이트 (자체평가 path 와 동일)
        hour = _bar_hour_kst(history)
        if hour is not None and hour not in self.kst_entry_hours:
            return Signal(action="hold", size=0.0,
                          reason=f"time_filter:kst_hour={hour}")
        side = self._daemon_fire_side(symbol, closed_ts)
        if side is None or side not in allowed_sides:
            return Signal(action="hold", size=0.0,
                          reason=f"no_daemon_fire:{symbol}@{closed_ts}")
        action = "buy" if side == "long" else "sell"
        # 같은봉 dedup (영속 — 재시작/봉당 1회)
        self._ensure_dedup_loaded()
        if self._fired_bar_ts.get(symbol) == str(closed_ts):
            return Signal(action="hold", size=0.0,
                          reason=f"already_entered_bar:{closed_ts}")
        self._fired_bar_ts[symbol] = str(closed_ts)
        self._persist_dedup()
        return Signal(action=action, size=self.default_size,
                      reason=f"consume_daemon_fire:{side}@{closed_ts}")

    async def on_bar(self, ctx):
        """parent 의 시그널 평가 → buy intent 면 BTC trend filter 적용.

        BTC 가 하락추세 (200 EMA 아래 OR 24h drawdown < -1%) 면 long entry
        자체 차단. short entry 는 그대로 통과 (시장 하락에 short 는 정상 진입).

        BTC ohlcv 는 orchestrator 가 per_symbol_snap["universe_ohlcv"] 로 박아줌
        (2026-06-05 orchestrator 변경). 그 key 없으면 (legacy 환경 / backtest
        구버전) BTC trend check 생략 → 기존 동작 byte-identical.
        """
        # 2026-06-08 봉마감 게이트 (live) — 미완성 봉 진입 차단, 마감봉만 평가.
        gated, closed_ts = self._bar_close_gate(ctx)
        if gated is None:
            return Signal(action="hold", size=0.0, reason="await_bar_close")
        ctx = gated
        # consume 모드 — 자체평가 대신 데몬 발화를 그대로 따라 진입 (거래=알림 100%).
        if self._consume_enabled() and closed_ts is not None:
            _snap = ctx.get("market_snapshot") if isinstance(ctx, dict) else None
            _hist = _snap.get("history") if isinstance(_snap, dict) else None
            _sym = _snap.get("symbol") if isinstance(_snap, dict) else None
            if _hist is None or _sym is None:
                return Signal(action="hold", size=0.0, reason="consume_no_data")
            sig = self._consume_daemon_fire_on_bar(
                ctx, closed_ts, _hist, _sym, {"long", "short"},
            )
            if (self.btc_trend_filter_enabled
                    and getattr(sig, "action", None) == "buy"
                    and isinstance(_snap, dict)):
                universe = _snap.get("universe_ohlcv")
                if isinstance(universe, dict):
                    btc_hist = universe.get(_BTC_SYMBOL)
                    if btc_hist is not None and len(btc_hist) > 0:
                        is_down, reason = _btc_is_downtrend(btc_hist)
                        if is_down:
                            return Signal(
                                action="hold", size=0.0,
                                reason=f"btc_trend_filter_long_blocked:{reason}",
                            )
            return sig
        sig = await super().on_bar(ctx)
        # ── BTC trend filter — long entry 만 적용 (short/hold 통과) ──
        if (
            self.btc_trend_filter_enabled
            and sig is not None
            and getattr(sig, "action", None) == "buy"
        ):
            snap = ctx.get("market_snapshot") if isinstance(ctx, dict) else None
            universe = snap.get("universe_ohlcv") if isinstance(snap, dict) else None
            if isinstance(universe, dict):
                btc_hist = universe.get(_BTC_SYMBOL)
                if btc_hist is not None and len(btc_hist) > 0:
                    is_down, reason = _btc_is_downtrend(btc_hist)
                    if is_down:
                        # downtrend → long entry 차단.
                        existing_reason = getattr(sig, "reason", "") or ""
                        sig = Signal(
                            action="hold", size=0.0,
                            reason=f"btc_trend_filter_long_blocked:{reason} ({existing_reason})",
                        )
        # ── 데몬-발화 게이트 + 같은봉 dedup (live, 공유 헬퍼) ──
        # 데몬 발화한 종목·봉만 진입(알림없는 매수 차단) + 같은봉 1회(영속 → 재시작
        # 재매수 방지). short-whitelist 도 동일 헬퍼 사용.
        return self._apply_daemon_gate_and_dedup(ctx, sig, closed_ts)
