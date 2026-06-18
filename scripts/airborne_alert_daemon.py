"""Live USDT-perp Airborne v1.1 alert daemon.

Streams Binance USDM Futures 1h/5m klines for the top-N USDT-perp universe,
evaluates Airborne BB-reversal v1.1 long+short signals on each confirmed 1h
bar, and pushes Telegram alerts via :func:`observability.alerts.notify`.

The markPrice@arr@1s stream is also subscribed but currently consumed silently
(MVP: kline-only signal evaluation). Phase 2 will add 5m trailing-stop
warnings keyed off mark_price ticks.

Strategy family note: the entire Airborne BB-reversal family (v1, v1.1, v2,
v3) is ``status: rejected`` in 5y multi-regime backtest (PF<1). These alerts
are a **visual guide reproduction** of the external-lecture indicator — do
not depend on them for auto-trading. See
``docs/specs/strategies/airborne-family-overview.md``.

Usage:
    python scripts/airborne_alert_daemon.py --top-n 50
    python scripts/airborne_alert_daemon.py --top-n 5 --dry-run
    python scripts/airborne_alert_daemon.py --testnet --top-n 3 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _autoload_dotenv() -> None:
    """Walk up from cwd / repo root looking for a .env file (mirrors live_run.py)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (Path.cwd(), _ROOT, _ROOT.parent):
        env = candidate / ".env"
        if env.exists():
            load_dotenv(env)
            return


_autoload_dotenv()

import signals  # noqa: E402
from brokers.binance.market_ws import (  # noqa: E402
    BinanceMarketDataStream,
    KlineEvent,
    MarkPriceEvent,
    REST_BASE_LIVE,
    REST_BASE_TESTNET,
    WS_BASE_LIVE,
    WS_BASE_TESTNET,
    bootstrap_history,
)
from observability.alerts import notify  # noqa: E402
from signals.airborne_bb_reversal import (  # noqa: E402
    AirborneSetup,
    evaluate_long_fire_v11,
    evaluate_short_fire_v11,
)
from universe.binance_futures_snapshot import fetch_futures_24h_snapshot  # noqa: E402
from universe.binance_top import top_n_by_volume  # noqa: E402

log = logging.getLogger("airborne_alert_daemon")

# 데몬 버전 — 게이트/표시 시각 의미가 바뀔 때 patch 증가 + patch-notes 추가.
# v0.6.56 (2026-06-11): buy-time(+1) 시프트 되돌림 → 도착시각(=알림시각) 기준
# 게이트·표시 통일 (트레이더 봉루프 decouple 게이트와 일치).
# v0.6.81 (2026-06-17): 알림에 진입필터(변동성/모멘텀) 차단 사유 표시 + 숏게이트를
# production.yaml(오후 역알파 제외)과 동기화 + 숏 미거래 시각 표기.
# v0.6.85 (2026-06-19): 1h fetch limit 100→250 — BTC EMA200 trend filter 가 200봉
# 필요했는데 100만 받아 "BTC 하락추세 LONG 차단" 알림이 절대 안 뜨던 버그 수정.
DAEMON_VERSION = "v0.6.85"

# 2026-06-07 — Bitget venue 지원 (#airborne-bitget-venue). 실거래 트레이더가
# Bitget top-100 을 거래하는데 알림 데몬은 Binance fapi top-100 을 봐서 알림과
# 실거래의 유니버스/가격이 어긋나던 문제. --venue bitget 이면 Bitget top-100 +
# Bitget REST candles 로 fire 를 평가. --venue binance (기본값) 은 기존 경로
# byte-identical 보존 — 데이터 소스만 swap, fire 평가/게이트/notice 는 동일.
VENUE_BINANCE = "binance"
VENUE_BITGET = "bitget"

BB_WINDOW = 20
BB_STD = 2.0
MAX_LOOKBACK = 50
MIN_HISTORY = BB_WINDOW + 2
DEFAULT_TOP_N = 100  # 2026-05-22: 50 → 100. SKYAI 처럼 거래량 변동 큰 종목이
                     # top-50 in/out 을 반복하며 빠진 동안 시그널이 통째로
                     # 누락되던 사고 (#airborne-watchlist) 완화.
COOLDOWN_HOURS = 4  # suppress repeat (symbol, side) fires within this window
BAR_MS_1H = 3_600_000

# 2026-06-04 — 텔레그램 알림 본문에 "어떤 production 전략이 이 fire 로 진입할지"
# 안내 추가. production.yaml 의 두 airborne 전략과 정확히 일치하는 게이트.
#
# 2026-06-05 — strategy 모듈에서 직접 import. truth source 통일. 이전엔 daemon
# 이 hardcoded set 이라 strategy 가 바뀌면 텔레그램 안내가 거짓. 사용자 지적:
# "원래는 텔레그램에서 kst hours 거래 예정 알림 와도 실제론 필터링돼서 안 살 수도
# 있잖아". 다음에 또 바뀌어도 daemon 코드 안 만져도 자동 동기.
# 2026-06-06 — v3 {1,2,3,6,7,8,23} 로 갱신. _KST_TOP_HOURS_V3 import.
try:
    from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
        _KST_TOP_HOURS_V3 as _KST_HOURS_KSTHOURS,
    )
except ImportError:
    # daemon-only 환경 (전략 코드 미배포) 안전 fallback. v3 set.
    _KST_HOURS_KSTHOURS: frozenset[int] = frozenset({1, 2, 3, 6, 7, 8, 23})

# 2026-06-17 — production.yaml short-whitelist kst_entry_hours 와 동기화. 24h(stale)
# → 오후 역알파 제외(13·14·16·17) + short_block(7) 반영한 실제 게이트. 알림이
# 실제 진입과 일치하도록. production.yaml 변경 시 같이 갱신 (drift 주의).
_KST_HOURS_SHORT_WL: frozenset[int] = frozenset(
    {0, 1, 2, 3, 5, 6, 8, 9, 10, 11, 12, 15, 18, 19, 20, 21, 22, 23}
)
# 진입 필터 임계 (트레이더 consumer 와 동일 — 알림이 실제 진입과 일치하도록).
# env 로 트레이더와 함께 튜닝. 0 이면 해당 필터 비활성(표시 안 함).
_MAX_VOL_PCT: float = float(os.environ.get("AIRBORNE_MAX_VOL_PCT", "5") or 5)
_SHORT_PUMP_PCT: float = float(os.environ.get("AIRBORNE_SHORT_PUMP_SKIP_PCT", "20") or 20)
_LONG_CRASH_PCT: float = float(os.environ.get("AIRBORNE_LONG_CRASH_SKIP_PCT", "10") or 10)
# whitelist 활성 종목 lazy cache — daemon 수명 동안 1회 로드. weekly refresh
# 시 daemon 재시작 필요.
_WHITELIST_ACTIVE_CACHE: frozenset[str] | None = None


def _load_whitelist_active() -> frozenset[str]:
    global _WHITELIST_ACTIVE_CACHE
    if _WHITELIST_ACTIVE_CACHE is not None:
        return _WHITELIST_ACTIVE_CACHE
    try:
        from live.airborne_short_whitelist.whitelist_loader import (
            active_symbols,
            load_whitelist,
        )
        path = _ROOT / "config" / "airborne_short_whitelist.yaml"
        cfg = load_whitelist(path)
        _WHITELIST_ACTIVE_CACHE = active_symbols(cfg)
        log.info("whitelist loaded: %d active symbols", len(_WHITELIST_ACTIVE_CACHE))
    except Exception as exc:  # noqa: BLE001
        log.warning("whitelist load failed (%s) — short-whitelist 안내 disabled", exc)
        _WHITELIST_ACTIVE_CACHE = frozenset()
    return _WHITELIST_ACTIVE_CACHE


def _norm_symbol(sym: str) -> str:
    """Binance 멀티플라이어 프리픽스를 제거해 Bitget/whitelist 단위로 정규화.

    airborne fire 는 Binance 표기('1000SHIBUSDT')로 들어오는데 whitelist 는
    Bitget 단위('SHIBUSDT')라서 직접 비교 시 영원히 불일치 → "화이트리스트 외
    종목" 오알림 (2026-06-07 #380 SHIB 사례). 양쪽을 정규화해 비교한다.

    선두 '1000' 한 번만 제거 — '1000SHIBUSDT'→'SHIBUSDT'. whitelist 가
    '1000LUNCUSDT' 처럼 프리픽스를 유지하는 경우도 양쪽 동일 정규화로 매칭됨.
    """
    s = sym.upper()
    if s.startswith("1000") and len(s) > 4:
        s = s[4:]
    return s


def _in_trading_universe(symbol: str) -> bool:
    """fire 심볼이 short-whitelist 전략의 매매 대상(거래량 top-100)인지.

    #380 — 전략이 고정 whitelist 대신 거래량 top-100 동적 universe 로 전환됨.
    따라서 알림도 top-100 기준으로 "진입 예정" 판정 ('1000' 멀티플라이어 정규화
    후 비교). 조회 실패 시 보수적으로 True (오알림보다 누락-경고 회피)."""
    try:
        from portfolio.bitget_top_dynamic import get_top_n_symbols
        universe = get_top_n_symbols(100)
    except Exception:  # noqa: BLE001
        return True
    target = _norm_symbol(symbol)
    return any(_norm_symbol(s) == target for s in universe)


def _kst_hour_from_open_time(open_time_ms: int) -> int:
    """1h 봉의 open time (ms) → KST hour. strategy 의 게이트와 일관성."""
    ts = pd.Timestamp(open_time_ms, unit="ms", tz="UTC").tz_convert("Asia/Seoul")
    return int(ts.hour)


def _fire_arrival_kst_hour(ev_open_time_ms: int) -> int:
    """fire 의 *도착시각*(= 봉 마감 = 알림 시각) KST hour.

    2026-06-11 — 봉루프 decouple 한 트레이더의 신규 게이트(도착시각 기반)와
    일치. 트레이더는 ``floor(fire_ts,1h).KST.hour`` 를 게이트 집합에 대조하는데
    (docs/specs/airborne-fire-driven-consume.md), 데몬 데이터 소스의
    ``ev.open_time`` 은 fire 봉을 *마감* 시각으로 라벨하므로 그 KST hour 가 곧
    도착시각이다. **발화가 7시에 오면 7 ∈ {1,2,3,6,7,8,23} → 매수, 매수가 정확히
    게이트 시각에 일어난다.**

    v0.6.51 의 buy-time(+1) 시프트 및 "봉 시작시각 보정(-1h)" 을 모두 되돌려
    혼선을 제거 — 게이트 판정·표시 모두 도착시각(알림시각) 하나로 통일.
    """
    return _kst_hour_from_open_time(ev_open_time_ms)


def _kst_hours_label(hours: frozenset[int]) -> str:
    """{7,8,16,20,22} → '7/8/16/20/22' — 안내 문자열 자동 생성."""
    return "/".join(str(h) for h in sorted(hours))


# BTC trend filter status — daemon 내부 캐시. live state 가 들어가면 알림에
# "BTC 하락추세라 LONG 차단" 표시. None = 데이터 미확보 (안전 fallback,
# block 표시 안 함).
_BTC_DOWNTREND_STATE: bool | None = None
_BTC_DOWNTREND_REASON: str = ""


def _update_btc_trend_state(btc_hist) -> None:
    """daemon main loop 가 매 1h 봉 마감 시 호출. _btc_is_downtrend 그대로 활용
    (strategy 와 정확히 같은 로직)."""
    global _BTC_DOWNTREND_STATE, _BTC_DOWNTREND_REASON
    try:
        from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
            _btc_is_downtrend,
        )
        is_down, reason = _btc_is_downtrend(btc_hist)
        _BTC_DOWNTREND_STATE = is_down
        _BTC_DOWNTREND_REASON = reason
    except Exception as exc:  # noqa: BLE001
        log.warning("btc trend state update failed: %s", exc)
        _BTC_DOWNTREND_STATE = None


def _token_filter_metrics(history) -> "tuple[float | None, float | None]":
    """history(1h df) → (평균 1h 변동폭%, 직전24h 변화%). 트레이더 consumer 와 동일
    계산 — 알림이 실제 진입필터와 일치. 데이터 부족/오류 시 (None, None)."""
    try:
        if history is None or len(history) < 25:
            return None, None
        c = history["close"]
        last = float(c.iloc[-1]); prev = float(c.iloc[-25])
        chg = (last - prev) / prev * 100 if prev > 0 else None
        rng = (history["high"] - history["low"]) / history["close"] * 100
        rng = rng[history["close"] > 0]
        vol = float(rng.mean()) if len(rng) else None
        return vol, chg
    except Exception:  # noqa: BLE001
        return None, None


def _format_strategy_notice(*, side: str, kst_hour: int, symbol: str, history=None) -> str:
    """이 fire 로 어떤 production 전략이 진입할지 + 막히면 어떤 필터 때문인지 안내.

    체크 순서는 트레이더 consumer 와 동일: 시간게이트 → 변동성 → 모멘텀 → BTC추세.
    ``kst_hour`` = fire 도착시각(= 봉마감 = 알림시각) KST. 변동성/모멘텀은 history
    로 계산(consumer 와 동일 임계). history 미공급 시 그 필터는 표시 생략.
    """
    in_kst4 = kst_hour in _KST_HOURS_KSTHOURS
    in_kst19 = kst_hour in _KST_HOURS_SHORT_WL
    in_wl = _in_trading_universe(symbol)
    is_short = side.lower() == "short"
    hours_label = _kst_hours_label(_KST_HOURS_KSTHOURS)
    # 숏 미거래 시각 (전체 - 숏게이트) — 사용자 요청: 숏 진입/미진입 시각 표시.
    wl_off_label = _kst_hours_label(frozenset(range(24)) - _KST_HOURS_SHORT_WL)
    vol, chg = _token_filter_metrics(history)

    def _filter_block() -> "str | None":
        """공통 진입필터(변동성/모멘텀) 차단 사유 — consumer 와 동일."""
        if _MAX_VOL_PCT > 0 and vol is not None and vol > _MAX_VOL_PCT:
            return f"❌ 고변동 {vol:.1f}%/h (>{_MAX_VOL_PCT:.0f} 필터)"
        if chg is not None:
            if is_short and _SHORT_PUMP_PCT > 0 and chg > _SHORT_PUMP_PCT:
                return f"❌ 펌핑 +{chg:.0f}% (>+{_SHORT_PUMP_PCT:.0f} 숏필터)"
            if (not is_short) and _LONG_CRASH_PCT > 0 and chg < -_LONG_CRASH_PCT:
                return f"❌ 폭락 {chg:.0f}% (<-{_LONG_CRASH_PCT:.0f} 롱필터)"
        return None
    filt = _filter_block()

    # kst-hours: bidir + KST gate + 변동성/모멘텀 + BTC trend(long)
    if not in_kst4:
        kst_line = f"❌ KST {kst_hour}시 — 게이트 외 (매매 {hours_label}시만)"
    elif filt is not None:
        kst_line = filt
    elif side.lower() == "long" and _BTC_DOWNTREND_STATE is True:
        kst_line = f"❌ BTC 하락추세 LONG 차단 ({_BTC_DOWNTREND_REASON or 'downtrend'})"
    else:
        kst_line = "✅ 진입 예정"

    # short-whitelist: SHORT only + top-100 + 숏게이트 + 변동성/모멘텀
    if side.lower() == "long":
        wl_line = "❌ LONG 미지원 (숏 전용 전략)"
    elif not in_wl:
        wl_line = "❌ TOP100 외 종목"
    elif not in_kst19:
        wl_line = f"❌ KST {kst_hour}시 — 숏 미거래 시각 (미거래 {wl_off_label}시)"
    elif filt is not None:
        wl_line = filt
    else:
        wl_line = "✅ 진입 예정"

    return (
        "🤖 봇 진입 가능성:\n"
        f"   • kst-hours (양방향): {kst_line}\n"
        f"   • short-whitelist (숏 전용): {wl_line}"
    )


@dataclass
class SymbolState:
    history_1h: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"]
    ))
    history_5m: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"]
    ))
    last_fire_open_time: dict[str, int] = field(default_factory=dict)


def _append_bar(df: pd.DataFrame, ev: KlineEvent, *, max_bars: int) -> pd.DataFrame:
    ts = pd.Timestamp(ev.open_time, unit="ms", tz="UTC")
    new_row = pd.DataFrame(
        {"open": [ev.open], "high": [ev.high], "low": [ev.low],
         "close": [ev.close], "volume": [ev.volume]},
        index=[ts],
    )
    if ts in df.index:
        df.loc[ts] = new_row.iloc[0]
        return df
    df = new_row if df.empty else pd.concat([df, new_row])
    if len(df) > max_bars:
        df = df.iloc[-max_bars:]
    return df


def _five_min_trend_preview(history_5m: pd.DataFrame, lookback: int = 3) -> str:
    if len(history_5m) < lookback + 1:
        return "n/a"
    diffs = history_5m["close"].iloc[-lookback:].diff().dropna()
    if (diffs > 0).all():
        return "ascending"
    if (diffs < 0).all():
        return "descending"
    return "mixed"


def build_alert_payload(
    *, symbol: str, side: str, ev: KlineEvent, setup: AirborneSetup,
    trigger: float, history_5m: pd.DataFrame,
) -> dict[str, str]:
    return {
        "symbol": symbol,
        "timeframe": "1h",
        "side": side,
        "fire_close": f"{ev.close:.6g}",
        "trigger": f"{trigger:.6g}",
        "base": f"{setup.base:.6g}",
        "extreme": f"{setup.extreme:.6g}",
        "5m_preview": _five_min_trend_preview(history_5m),
        "note": "v1.1 reproduction — family rejected; visual guide only",
    }


def _cooldown_ok(state: SymbolState, side: str, ev_open_time: int) -> bool:
    last = state.last_fire_open_time.get(side, 0)
    return ev_open_time - last >= COOLDOWN_HOURS * BAR_MS_1H


def dispatch_fire(
    *, symbol: str, side: str, state: SymbolState, ev: KlineEvent,
    setup: AirborneSetup, trigger: float, dry_run: bool,
    notify_fn=notify,
) -> bool:
    """Apply cooldown, build payload, and emit alert. Returns True if dispatched.

    Pure dispatcher (testable) — takes the notify callable as a kwarg so tests
    can inject a spy.
    """
    if not _cooldown_ok(state, side, ev.open_time):
        log.debug("%s %s fire suppressed by cooldown", symbol, side)
        return False
    state.last_fire_open_time[side] = ev.open_time

    payload = build_alert_payload(
        symbol=symbol, side=side, ev=ev, setup=setup, trigger=trigger,
        history_5m=state.history_5m,
    )
    # 2026-06-04 — 알림 가독성 개선: 이모지 방향 + 한글 본문 + 봇 진입 가능성 안내.
    # LONG = BB 하단 돌파 후 반등 → extreme=최저점 / SHORT = BB 상단 돌파 후 후퇴 → extreme=최고점.
    if side.lower() == "long":
        title = f"🟢⬆️ 롱 진입 신호 — {symbol} (1시간봉)"
        extreme_label = "최저점"
    else:
        title = f"🔴⬇️ 숏 진입 신호 — {symbol} (1시간봉)"
        extreme_label = "최고점"
    kst_hour = _fire_arrival_kst_hour(ev.open_time)
    notice = _format_strategy_notice(
        side=side, kst_hour=kst_hour, symbol=symbol, history=state.history_1h
    )
    body = (
        "✨ 40% 되돌림 발화 (Airborne v1.1)\n"
        f"   현재가: {ev.close:.6g}\n"
        f"   진입가(40% 되돌림): {trigger:.6g}\n"
        f"   돌파 시작가: {setup.base:.6g}  /  {extreme_label}: {setup.extreme:.6g}\n"
        "\n"
        f"{notice}"
    )
    if dry_run:
        print(f"[DRY] {title}\n  {body}\n  {payload}", flush=True)
    else:
        notify_fn("info", title, body, payload)
    log.info("FIRE %s %s @ close=%.6g trigger=%.6g", symbol, side, ev.close, trigger)
    return True


def evaluate_and_dispatch(
    *, symbol: str, state: SymbolState, ev: KlineEvent, dry_run: bool,
    notify_fn=notify,
) -> tuple[bool, bool]:
    """Run v1.1 long+short evaluators and dispatch fires. Returns (long_fired, short_fired)."""
    df = state.history_1h
    if len(df) < MIN_HISTORY:
        log.debug("%s warmup (%d/%d)", symbol, len(df), MIN_HISTORY)
        return False, False
    bb = signals.compute("bollinger", close=df["close"], window=BB_WINDOW, n_std=BB_STD)
    bb_lower = bb["lower"]
    bb_upper = bb["upper"]

    long_fires, long_setup, long_trig = evaluate_long_fire_v11(
        history=df, bb_lower=bb_lower, max_lookback=MAX_LOOKBACK,
    )
    short_fires, short_setup, short_trig = evaluate_short_fire_v11(
        history=df, bb_upper=bb_upper, max_lookback=MAX_LOOKBACK,
    )

    long_dispatched = short_dispatched = False
    if long_fires and long_setup is not None:
        long_dispatched = dispatch_fire(
            symbol=symbol, side="long", state=state, ev=ev,
            setup=long_setup, trigger=long_trig,
            dry_run=dry_run, notify_fn=notify_fn,
        )
    if short_fires and short_setup is not None:
        short_dispatched = dispatch_fire(
            symbol=symbol, side="short", state=state, ev=ev,
            setup=short_setup, trigger=short_trig,
            dry_run=dry_run, notify_fn=notify_fn,
        )
    return long_dispatched, short_dispatched


DEFAULT_UNIVERSE_REFRESH_HOURS = 6.0


def compute_universe_diff(
    prev: list[str], curr: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(added, removed, unchanged)`` between two universe lists.

    Pure function. ``added`` and ``unchanged`` follow ``curr`` ordering;
    ``removed`` follows ``prev`` ordering.
    """
    prev_set = set(prev)
    curr_set = set(curr)
    added = [s for s in curr if s not in prev_set]
    removed = [s for s in prev if s not in curr_set]
    unchanged = [s for s in curr if s in prev_set]
    return added, removed, unchanged


# ── Venue 추상화 (Binance / Bitget) ─────────────────────────────────────────
#
# 데몬 내부는 항상 ``{symbol: {interval: DataFrame}}`` (Binance bootstrap_history
# 포맷) 을 기대한다. Bitget bootstrap_history 는 ``{symbol: [KlineEvent]}`` 라
# 모양이 달라서 아래 어댑터가 동일 포맷으로 변환한다. 이렇게 하면 downstream
# (_bootstrap_into_states / polling fetch / evaluate) 코드는 venue 무관.


def _bitget_bars_to_history(bars: list) -> "pd.DataFrame":
    """Bitget ``[KlineEvent]`` → 데몬 history DataFrame.

    Bitget KlineEvent: open_time(ms) / Decimal OHLCV / closed. 데몬은 UTC
    DatetimeIndex + float ``[open,high,low,close,volume]`` 컬럼을 기대한다
    (Binance ``_klines_to_dataframe`` 와 동일 모양). Bitget candles REST 는
    최신→과거 순서로 올 수 있어 open_time 기준 오름차순 정렬한다.
    """
    cols = ["open", "high", "low", "close", "volume"]
    if not bars:
        return pd.DataFrame(columns=cols)
    rows = sorted(bars, key=lambda b: b.open_time)
    idx = pd.to_datetime([b.open_time for b in rows], unit="ms", utc=True)
    return pd.DataFrame(
        {
            "open": [float(b.open) for b in rows],
            "high": [float(b.high) for b in rows],
            "low": [float(b.low) for b in rows],
            "close": [float(b.close) for b in rows],
            "volume": [float(b.volume) for b in rows],
        },
        index=idx,
    )


async def _compute_universe(
    *, venue: str, top_n: int, rest_base_url: str,
) -> list[str]:
    """venue 별 top-N 유니버스 계산.

    bitget: ``get_top_n_symbols(top_n)`` (sync, 5분 캐시 + fallback).
    binance: 기존 ``fetch_futures_24h_snapshot`` + ``top_n_by_volume``.
    """
    if venue == VENUE_BITGET:
        from portfolio.bitget_top_dynamic import get_top_n_symbols
        # sync 함수 — 짧은 httpx.Client 호출이라 이벤트루프 블로킹 무시 가능.
        return get_top_n_symbols(top_n)
    snap = await fetch_futures_24h_snapshot(base_url=rest_base_url)
    return top_n_by_volume(snap, n=top_n)


async def _bootstrap_history_venue(
    *, venue: str, symbols: list[str], rest_base_url: str,
) -> dict[str, dict[str, "pd.DataFrame"]]:
    """venue 별 1h+5m history fetch → ``{symbol: {interval: DataFrame}}``.

    binance: 기존 ``bootstrap_history`` 그대로 (동일 포맷 반환).
    bitget: ``bitget bootstrap_history`` (interval 당 1회) 를 interval 별로
    호출해 ``[KlineEvent]`` 를 받고 어댑터로 DataFrame 변환.
    """
    if venue != VENUE_BITGET:
        return await bootstrap_history(
            symbols=symbols, intervals=("1h", "5m"),
            # 1h=250: BTC trend filter 의 _btc_is_downtrend 가 EMA200 계산에 200봉
            # 필요. 100 이면 항상 "insufficient_btc_history" → 알림에 BTC 하락추세
            # LONG 차단이 절대 안 뜸 (2026-06-19 fix). 시그널은 100 으로 충분하나
            # 추가 history 는 무해(BB/ATR 은 trailing window).
            limit_per_interval={"1h": 250, "5m": 50},
            base_url=rest_base_url,
        )
    from brokers.bitget.market_ws import bootstrap_history as bitget_bootstrap
    out: dict[str, dict[str, pd.DataFrame]] = {s: {} for s in symbols}
    # 1h=250: BTC EMA200 trend filter 표시용 (위 binance 주석 참조).
    for iv, limit in (("1h", 250), ("5m", 50)):
        per_sym = await bitget_bootstrap(
            symbols=symbols, interval=iv, limit=limit, paper=True,
        )
        for s in symbols:
            out[s][iv] = _bitget_bars_to_history(per_sym.get(s, []))
    return out


async def _bootstrap_into_states(
    symbols: list[str],
    states: dict[str, SymbolState],
    *,
    rest_base_url: str,
    venue: str = VENUE_BINANCE,
) -> None:
    """REST-bootstrap ``symbols`` history into ``states`` (in-place).

    Each new symbol gets a fresh :class:`SymbolState` with 1h+5m history
    seeded. Existing entries in ``states`` are not touched — caller is
    expected to have already removed stale entries.
    """
    if not symbols:
        return
    # 2026-05-22: batch bootstrap 이 심볼 1개라도 실패하면 (예: always-include
    # 에 Binance Futures 에 없는 EURUSDT 가 들어가 400 Bad Request) 예외가
    # 전파돼 데몬 전체가 crash → unless-stopped 재시작 → 무한 crash loop.
    # batch 실패 시 심볼별 개별 재시도로 강등 — 잘못된 심볼 1개가 나머지
    # 99개 + 데몬 전체를 죽이지 못하게 한다.
    try:
        boot = await _bootstrap_history_venue(
            venue=venue, symbols=symbols, rest_base_url=rest_base_url,
        )
    except Exception as err:  # noqa: BLE001 — degrade to per-symbol
        log.warning(
            "batch bootstrap failed (%s) — per-symbol 재시도", err,
        )
        boot = {}
        for s in symbols:
            try:
                one = await _bootstrap_history_venue(
                    venue=venue, symbols=[s], rest_base_url=rest_base_url,
                )
                boot.update(one)
            except Exception as e2:  # noqa: BLE001
                log.warning("bootstrap skip %s — %s", s, e2)
    for s in symbols:
        st = SymbolState()
        st.history_1h = boot.get(s, {}).get("1h", st.history_1h)
        st.history_5m = boot.get(s, {}).get("5m", st.history_5m)
        # boot 에 없는 심볼 (fetch 실패) = 빈 history → evaluate 가 warmup
        # 으로 자연 skip. 데몬은 정상 가동.
        states[s] = st


async def _consume_stream(
    stream: BinanceMarketDataStream,
    states: dict[str, SymbolState],
    dry_run: bool,
) -> None:
    """Drain ``stream`` until exhausted or cancelled. Dispatches alerts on
    each confirmed 1h close for symbols currently in ``states``.

    MarkPrice events are consumed silently (MVP). Events for symbols that
    have been removed from the universe mid-cycle are dropped without
    error (lookup miss in ``states``).
    """
    async for ev in stream.stream():
        if isinstance(ev, MarkPriceEvent):
            continue
        sym = ev.symbol
        state = states.get(sym)
        if state is None:
            continue
        if ev.interval == "5m":
            if ev.is_closed:
                state.history_5m = _append_bar(state.history_5m, ev, max_bars=100)
            continue
        if ev.interval == "1h":
            if not ev.is_closed:
                continue
            state.history_1h = _append_bar(state.history_1h, ev, max_bars=250)  # EMA200 trend filter 여유
            # 2026-06-05 — BTC trend filter 알림 동기. BTCUSDT 1h 마감 시
            # state 갱신 → 다음 fire 알림이 정확한 BTC trend 상태 반영.
            if sym == "BTCUSDT":
                _update_btc_trend_state(state.history_1h)
            evaluate_and_dispatch(symbol=sym, state=state, ev=ev, dry_run=dry_run)


async def _run_ws_loop(
    *,
    top_n: int = DEFAULT_TOP_N,
    dry_run: bool = False,
    ws_base_url: str = WS_BASE_LIVE,
    rest_base_url: str = REST_BASE_LIVE,
    universe_refresh_hours: float = DEFAULT_UNIVERSE_REFRESH_HOURS,
    always_include: list[str] | None = None,
) -> None:
    """WebSocket-based mode (legacy) — needs an unblocked region (VPN/cloud).

    Binance mainnet WS (``fstream.binance.com``) pushes 0 messages to Korean
    IPs (region-block) — handshake succeeds but no data frames arrive. For
    Korean-IP-safe operation use ``--mode polling`` (REST has no region block).

    If ``universe_refresh_hours > 0`` the universe is re-computed on that
    cadence and the WS stream is rebuilt to reflect added / removed
    symbols. Removed-symbol state is dropped; added-symbol history is
    REST-bootstrapped before subscription. Cooldown state for unchanged
    symbols is preserved across cycles.

    Passing ``universe_refresh_hours <= 0`` disables periodic refresh
    (legacy behaviour: universe locked at startup, stream runs forever).
    """
    states: dict[str, SymbolState] = {}
    prev_universe: list[str] = []
    refresh_secs: float | None = (
        universe_refresh_hours * 3600 if universe_refresh_hours > 0 else None
    )

    pinned = [s.strip().upper() for s in (always_include or []) if s.strip()]

    while True:
        log.info("fetching 24h snapshot from %s ...", rest_base_url)
        snap = await fetch_futures_24h_snapshot(base_url=rest_base_url)
        universe = top_n_by_volume(snap, n=top_n)
        if not universe:
            log.error("empty universe — retrying in 60s")
            await asyncio.sleep(60)
            continue
        # 거래량 순위 무관 강제 포함 — SKYAI 처럼 top-N in/out 을 반복하며
        # 빠진 동안 시그널이 누락되던 종목 (2026-05-22 #airborne-watchlist).
        for sym in pinned:
            if sym not in universe:
                universe.append(sym)
                log.info("pinned symbol force-added to universe: %s", sym)

        added, removed, unchanged = compute_universe_diff(prev_universe, universe)
        if prev_universe:
            log.info(
                "universe refresh — added=%s removed=%s unchanged=%d",
                added, removed, len(unchanged),
            )
        else:
            log.info(
                "initial universe (top-%d USDT-perp, %d symbols): %s",
                top_n, len(universe), universe,
            )

        for sym in removed:
            states.pop(sym, None)
        await _bootstrap_into_states(added, states, rest_base_url=rest_base_url)
        prev_universe = universe
        log.info("states current: %d symbols seeded", len(states))

        stream = BinanceMarketDataStream(
            symbols=universe, intervals=("1h", "5m"),
            base_url=ws_base_url,
            include_mark_price_arr=True,
        )

        if refresh_secs is None:
            log.info(
                "opening WS (%d streams) — universe refresh disabled",
                stream.stream_count,
            )
            await _consume_stream(stream, states, dry_run)
            return  # stream exhausted (only happens on hard error)

        log.info(
            "opening WS (%d streams) — universe refresh in %.1fh",
            stream.stream_count, universe_refresh_hours,
        )
        consume_task = asyncio.create_task(
            _consume_stream(stream, states, dry_run),
            name="airborne-stream-consumer",
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(consume_task), timeout=refresh_secs,
            )
        except asyncio.TimeoutError:
            log.info(
                "universe refresh cycle triggered (%.1fh elapsed)",
                universe_refresh_hours,
            )
        finally:
            await stream.close()
            consume_task.cancel()
            try:
                await consume_task
            except (asyncio.CancelledError, Exception):
                pass


def _next_polling_wakeup(now_dt: datetime, buffer_sec: int = 30) -> datetime:
    """Return the next 1h boundary +``buffer_sec`` (UTC) strictly after ``now_dt``.

    e.g. buffer_sec=30: now=05:00:25 → 05:00:30; now=05:00:35 → 06:00:30. The
    offset lets Binance finalize the just-closed 1h bar before we REST-fetch it.

    2026-06-17 — 진입 지연 단축. 버퍼 30s 는 봉마감 후 발화를 +30s 지연시켜(이후
    fetch ~15s 까지 더해 median 발화 :45) consume-mode 트레이더가 정각 대비 1분+
    늦게 진입, sim TP 가 실거래 SL 로 뒤집히던 주원인. Binance 1h klines 는 마감
    직후 확정되므로 버퍼를 줄여도 안전. 호출부가 env ``AIRBORNE_POLL_BUFFER_SEC``
    (기본 10s) 로 주입. 기본 인자 30 은 기존 단위테스트 byte-identical 보존용.
    Pure function — extracted for deterministic unit testing.
    """
    candidate = now_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
        seconds=int(buffer_sec)
    )
    if candidate <= now_dt:
        candidate += timedelta(hours=1)
    return candidate


async def _run_polling_loop(
    *,
    top_n: int = DEFAULT_TOP_N,
    dry_run: bool = False,
    rest_base_url: str = REST_BASE_LIVE,
    universe_refresh_hours: float = DEFAULT_UNIVERSE_REFRESH_HOURS,
    always_include: list[str] | None = None,
    venue: str = VENUE_BINANCE,
) -> None:
    """REST polling mode — Korean-IP-safe (no WebSocket dependence).

    Binance mainnet WS (``fstream.binance.com``) pushes 0 messages to Korean
    IPs (region-block) — handshake succeeds but no data frames arrive. The
    public REST API (``fapi.binance.com/fapi/v1/klines``) has no such block.
    The Airborne signal is computed identically on identical OHLCV input
    regardless of source, so REST polling delivers the same fires at the same
    prices as a non-blocked WS feed — only the data path differs.

    Wakes at each 1h boundary +30s (UTC), REST-fetches kline history for
    every universe symbol, detects newly-confirmed 1h bars by comparing the
    latest open_time against state, and dispatches alerts. Universe is
    re-computed every ``universe_refresh_hours`` (default 6h). The pinned
    ``always_include`` symbols are force-kept exactly as in the WS loop.
    """
    pinned = [s.strip().upper() for s in (always_include or []) if s.strip()]
    states: dict[str, SymbolState] = {}
    prev_universe: list[str] = []
    last_universe_refresh: float = 0.0
    refresh_secs: float | None = (
        universe_refresh_hours * 3600 if universe_refresh_hours > 0 else None
    )

    while True:
        # ── Universe refresh (first cycle + every N hours) ─────────────
        now_loop = asyncio.get_event_loop().time()
        need_refresh = (
            not prev_universe
            or (refresh_secs is not None
                and now_loop - last_universe_refresh >= refresh_secs)
        )
        if need_refresh:
            log.info("fetching universe (venue=%s) ...", venue)
            universe = await _compute_universe(
                venue=venue, top_n=top_n, rest_base_url=rest_base_url,
            )
            if not universe:
                log.error("empty universe — retrying in 60s")
                await asyncio.sleep(60)
                continue
            # 거래량 순위 무관 강제 포함 — WS loop 와 동일 정책. pinned 종목이
            # 해당 venue 에 없으면 (예: Bitget 에 없는 심볼) bootstrap 단계에서
            # 빈 history → graceful skip (warmup 으로 자연 누락).
            for sym in pinned:
                if sym not in universe:
                    universe.append(sym)
                    log.info("pinned symbol force-added to universe: %s", sym)
            added, removed, unchanged = compute_universe_diff(
                prev_universe, universe
            )
            if prev_universe:
                log.info(
                    "universe refresh — added=%s removed=%s unchanged=%d",
                    added, removed, len(unchanged),
                )
            else:
                log.info(
                    "initial universe (top-%d USDT-perp, %d symbols): %s",
                    top_n, len(universe), universe,
                )
            for sym in removed:
                states.pop(sym, None)
            await _bootstrap_into_states(
                added, states, rest_base_url=rest_base_url, venue=venue,
            )
            prev_universe = universe
            last_universe_refresh = now_loop
            log.info("states current: %d symbols seeded", len(states))

        # ── Sleep until next 1h boundary + buffer (UTC) ────────────────
        # 버퍼 = env AIRBORNE_POLL_BUFFER_SEC (기본 10s, 기존 30s 에서 단축 →
        # 발화·진입 지연 ~20s 절감). Binance 1h klines 는 마감 직후 확정.
        now_dt = datetime.now(timezone.utc)
        _poll_buffer = int(float(os.environ.get("AIRBORNE_POLL_BUFFER_SEC", "10") or 10))
        next_wakeup = _next_polling_wakeup(now_dt, buffer_sec=_poll_buffer)
        wait_secs = (next_wakeup - now_dt).total_seconds()
        log.info(
            "polling: next cycle at %s UTC (%.0fs sleep)",
            next_wakeup.strftime("%H:%M:%S"), wait_secs,
        )
        await asyncio.sleep(wait_secs)

        # ── REST poll all symbols (1h limit=100, 5m limit=50) ──────────
        log.info("polling cycle start — %d symbols", len(prev_universe))
        try:
            poll = await _bootstrap_history_venue(
                venue=venue, symbols=prev_universe, rest_base_url=rest_base_url,
            )
        except Exception as exc:  # noqa: BLE001 — retry next cycle
            log.error("polling fetch failed: %s — retrying next cycle", exc)
            continue

        # ── Detect new 1h bar per symbol → evaluate_and_dispatch ───────
        new_bar_count = 0
        for sym in prev_universe:
            state = states.get(sym)
            if state is None:
                continue
            new_1h = poll.get(sym, {}).get("1h")
            new_5m = poll.get(sym, {}).get("5m")
            if new_1h is None or new_1h.empty:
                continue

            new_last_ts = new_1h.index[-1]
            had_prev = not state.history_1h.empty
            prev_last_ts = state.history_1h.index[-1] if had_prev else None

            # state 갱신 (history 통째 교체, cooldown 은 SymbolState 에 보존)
            state.history_1h = new_1h
            if new_5m is not None and not new_5m.empty:
                state.history_5m = new_5m
            # 2026-06-05 — BTC trend filter 알림 동기 (polling 모드).
            if sym == "BTCUSDT":
                _update_btc_trend_state(state.history_1h)

            if had_prev and new_last_ts <= prev_last_ts:
                continue  # 아직 새 봉 없음

            new_bar_count += 1
            row = new_1h.iloc[-1]
            open_ms = int(new_last_ts.timestamp() * 1000)
            ev = KlineEvent(
                symbol=sym, interval="1h",
                open_time=open_ms,
                close_time=open_ms + 3_599_999,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                is_closed=True,
            )
            evaluate_and_dispatch(symbol=sym, state=state, ev=ev, dry_run=dry_run)

        log.info("polling cycle complete — %d new 1h bars evaluated", new_bar_count)


async def run_daemon(
    *,
    top_n: int = DEFAULT_TOP_N,
    dry_run: bool = False,
    ws_base_url: str = WS_BASE_LIVE,
    rest_base_url: str = REST_BASE_LIVE,
    universe_refresh_hours: float = DEFAULT_UNIVERSE_REFRESH_HOURS,
    always_include: list[str] | None = None,
    mode: str = "polling",
    venue: str = VENUE_BINANCE,
) -> None:
    """Top-level entry — dispatches to polling or WS loop based on ``mode``.

    ``mode='polling'`` (default): REST polling at each 1h boundary +30s —
        Korean-IP-safe (WS region block doesn't affect REST). Signal cadence
        matches the 1h bar grain exactly.
    ``mode='ws'``: legacy WebSocket combined stream — needs an unblocked
        region (cloud / VPN). Higher data density (markPrice, 5m kline).

    ``venue='binance'`` (default): Binance USDM Futures top-N + Binance REST.
    ``venue='bitget'``: Bitget USDT-FUTURES top-N + Bitget REST candles —
        실거래 트레이더(broker=bitget-demo)와 동일 유니버스/가격. polling 모드만
        지원 (Bitget WS 미배선) — ``--venue bitget --mode ws`` 는 명확히 거부.
    """
    if venue not in (VENUE_BINANCE, VENUE_BITGET):
        raise ValueError(
            f"unknown venue: {venue!r} (expected 'binance' or 'bitget')"
        )
    if venue == VENUE_BITGET and mode == "ws":
        raise ValueError(
            "venue=bitget 는 mode=polling 만 지원합니다 "
            "(Bitget WS 경로 미배선) — '--venue bitget --mode polling' 사용."
        )
    log.info("daemon mode: %s venue: %s", mode, venue)
    if mode == "polling":
        await _run_polling_loop(
            top_n=top_n,
            dry_run=dry_run,
            rest_base_url=rest_base_url,
            universe_refresh_hours=universe_refresh_hours,
            always_include=always_include,
            venue=venue,
        )
    elif mode == "ws":
        await _run_ws_loop(
            top_n=top_n,
            dry_run=dry_run,
            ws_base_url=ws_base_url,
            rest_base_url=rest_base_url,
            universe_refresh_hours=universe_refresh_hours,
            always_include=always_include,
        )
    else:
        raise ValueError(f"unknown mode: {mode!r} (expected 'polling' or 'ws')")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Airborne v1.1 USDT-perp alert daemon (Binance Futures, Telegram)",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                        help=f"Universe size (default {DEFAULT_TOP_N})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts to stdout instead of Telegram")
    parser.add_argument("--testnet", action="store_true",
                        help="Use Binance testnet REST + WS endpoints")
    parser.add_argument(
        "--universe-refresh-hours", type=float,
        default=DEFAULT_UNIVERSE_REFRESH_HOURS,
        help=(
            f"Re-compute top-N universe on this cadence (default "
            f"{DEFAULT_UNIVERSE_REFRESH_HOURS}h). Pass 0 to disable "
            "(universe locks at startup, legacy behaviour)."
        ),
    )
    parser.add_argument(
        "--always-include", default="",
        help=(
            "거래량 순위 무관 항상 universe 에 포함할 심볼 (쉼표 구분, 예: "
            "SKYAIUSDT,TRXUSDT). 환경변수 AIRBORNE_ALWAYS_INCLUDE 로도 지정 "
            "가능 (CLI 우선). top-N in/out 으로 시그널 누락되는 관심 종목용."
        ),
    )
    parser.add_argument(
        "--mode", choices=["polling", "ws"], default=None,
        help=(
            "Data source mode. 'polling' (default): REST polling at each 1h "
            "boundary +30s — Korean-IP-safe, no WS dependence. 'ws': legacy "
            "WebSocket combined stream — needs VPN/cloud outside Korea "
            "(Binance mainnet WS push is region-blocked for Korean IPs). "
            "환경변수 AIRBORNE_MODE 로도 지정 가능 (CLI 우선)."
        ),
    )
    parser.add_argument(
        "--venue", choices=[VENUE_BINANCE, VENUE_BITGET], default=None,
        help=(
            "Data source venue. 'binance' (default): Binance USDM Futures "
            "top-N + Binance REST. 'bitget': Bitget USDT-FUTURES top-N + "
            "Bitget REST candles — 실거래 트레이더(broker=bitget-demo)와 동일 "
            "유니버스/가격으로 알림. bitget 은 polling 모드만 지원. "
            "환경변수 AIRBORNE_VENUE 로도 지정 가능 (CLI 우선)."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    # 우선순위: CLI --mode > 환경변수 AIRBORNE_MODE > 기본값 polling.
    mode = args.mode or os.environ.get("AIRBORNE_MODE") or "polling"
    if mode not in ("polling", "ws"):
        mode = "polling"

    # 우선순위: CLI --venue > 환경변수 AIRBORNE_VENUE > 기본값 binance.
    venue = args.venue or os.environ.get("AIRBORNE_VENUE") or VENUE_BINANCE
    if venue not in (VENUE_BINANCE, VENUE_BITGET):
        venue = VENUE_BINANCE

    always_include_raw = args.always_include or os.environ.get(
        "AIRBORNE_ALWAYS_INCLUDE", ""
    )
    always_include = [
        s.strip().upper() for s in always_include_raw.split(",") if s.strip()
    ]

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    log.info("airborne alert daemon %s", DAEMON_VERSION)

    ws_base = WS_BASE_TESTNET if args.testnet else WS_BASE_LIVE
    rest_base = REST_BASE_TESTNET if args.testnet else REST_BASE_LIVE

    try:
        asyncio.run(run_daemon(
            top_n=args.top_n,
            dry_run=args.dry_run,
            ws_base_url=ws_base,
            rest_base_url=rest_base,
            universe_refresh_hours=args.universe_refresh_hours,
            always_include=always_include,
            mode=mode,
            venue=venue,
        ))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
