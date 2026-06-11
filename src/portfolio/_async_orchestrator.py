"""Async multi-strategy orchestrator — issue #78.

Not callable from LLM tool surface (CLAUDE.md invariant #6).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

from risk import (
    Policy,
    Snapshot,
    Order,
    Action,
    evaluate,
    PortfolioRiskReport,
)
from src.brokers.base import AsyncBrokerAdapter
from src.live.types import EVENT_STRATEGY_EVALUATED, EVENT_STRATEGY_TOGGLED, WALEvent
from .orchestrator import _SyncStrategyOrchestrator
from .order_intent import OrderIntent
from .sizing import resolve_size, size_to_qty

logger = logging.getLogger(__name__)


class AsyncStrategyOrchestrator:
    """Async multi-strategy tick driver with quarantine, risk gating, and bar/wall-clock refresh.

    Composes _SyncStrategyOrchestrator for sync portfolio-risk operations.
    Sync API delegates lock-free; async API uses asyncio.Lock for write protection.
    """

    def __init__(
        self,
        policy: Policy,
        *,
        refresh_every_n_bars: int | None = None,
        min_reliability: float = 0.0,
        broker: AsyncBrokerAdapter | None = None,
        wal_observer: Callable[[WALEvent], None] | None = None,
        min_order_interval_sec: float = 0.0,
    ) -> None:
        self._sync = _SyncStrategyOrchestrator(policy)
        self._policy = policy
        self._broker: AsyncBrokerAdapter | None = broker
        self._strategies: dict[str, object] = {}
        self._recent_returns: dict[str, pd.Series] = {}
        self._quarantined: set[str] = set()
        self._disabled: set[str] = set()
        self._fail_count: dict[str, int] = {}
        # #238 — live-scanner 포지션 중복 진입 차단. ATR breakout 등 조건이
        # 지속되는 동안 매 tick(초당 수 건) 재매수하던 폭주 버그 fix. (sid,
        # symbol) 진입 시 기록, LivePositionRiskManager 가 청산하면
        # release_live_position() 으로 해제 → 재진입 허용.
        self._live_entered: set[tuple[str, str]] = set()
        # 2026-05-21 — stop/TP 청산 직후 cooldown 차단. release_live_position()
        # 이 호출되면 strategy 의 `cooldown_after_stop_sec` 만큼 monotonic 타임
        # 스탬프를 기록 → 그 안에 들어오는 BUY 신호는 통과 안 시킴. 본 dict 이
        # 없으면 ATR breakout 자리에서 stop → 1초 만에 재진입 → 또 stop … 패턴
        # 으로 18초간 30회 churn 하며 $20+ 손실 발생 사례 있음 (2026-05-21
        # cand-c-breakout NEARUSDT 16:57:49~16:58:07). 키 = (sid, symbol),
        # value = monotonic 만료 시각. 만료된 entry 는 dispatch 에서 자연
        # cleanup. cooldown=0 인 strategy 는 dict 에 아예 안 들어감 = 기존
        # 동작 100% 보존.
        self._stop_cooldown_until: dict[tuple[str, str], float] = {}
        # #238 — orchestrator-level duplicate-order backstop (Item 3). Item 1
        # throttles momo at the strategy layer; this catches ANY non-live-
        # scanner strategy flooding identical (sid, symbol, side) intents per
        # WS tick. DEFAULT 0.0 = DISABLED → every existing test / backtest /
        # universe-scan rebalance stays bit-identical; live config opts in.
        # Live-scanner is excluded (keeps its own _live_entered lifecycle).
        self._min_order_interval_sec = min_order_interval_sec
        self._last_order_ts: dict[tuple[str, str, str], float] = {}
        self._report_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._bar_count = 0
        self._refresh_every_n_bars = refresh_every_n_bars
        self._wal_observer = wal_observer
        # 2026-05-21 — live-scanner BUY 통과 시 호출 콜백. ATR 기반 동적 stop
        # 등 strategy 가 Signal 에 실어보낸 per-entry stop/TP/trailing pct 를
        # LivePositionRiskManager.register_entry_override 로 전달한다. live_run
        # 에서 risk_mgr.register_entry_override 메서드로 와이어. None 이면
        # 콜백 안 함 (정적 policy 만 사용) = 기존 동작.
        self._on_entry: Callable[..., None] | None = None

    # ---- sync delegation API -----------------------------------------------

    def register_strategy(self, strategy_id: str, strategy: object) -> None:
        self._strategies[strategy_id] = strategy
        self._fail_count.setdefault(strategy_id, 0)

    def register_strategy_returns(self, strategy_id: str, series: pd.Series) -> None:
        self._sync.register_strategy_returns(strategy_id, series)
        self._recent_returns[strategy_id] = series

    def release_live_position(self, strategy_id: str, symbol: str) -> None:
        """#238 — LivePositionRiskManager 가 stop/TP 청산 시 호출. (sid, symbol)
        진입 기록을 해제해 다음 조건 충족 시 재진입 허용. 미호출 시 해당
        strategy-symbol 은 프로세스 수명 동안 1회만 진입 (안전 측 fail-safe).

        2026-05-21: strategy 가 `cooldown_after_stop_sec > 0` 을 선언했다면
        그만큼 monotonic 시각을 기록 → dispatch 에서 cooldown 안의 BUY 신호
        차단. cooldown=0 (default) 이면 dict 변경 0 → 기존 동작 보존."""
        key = (strategy_id, symbol)
        self._live_entered.discard(key)
        strat = self._strategies.get(strategy_id)
        cooldown_sec = float(getattr(strat, "cooldown_after_stop_sec", 0.0) or 0.0)
        if cooldown_sec > 0.0:
            self._stop_cooldown_until[key] = time.monotonic() + cooldown_sec

    def sync_live_entered(
        self, strategy_id: str, symbol: str, qty: float,
    ) -> None:
        """PositionReconciler 의 broker↔store auto-fix 와 `_live_entered` 정합.

        2026-05-22 버그: ``restore_live_entered`` 가 부팅 시 store 의 phantom
        포지션을 `_live_entered` 에 등록한 뒤, ``PositionReconciler`` 가 broker
        ground-truth 와 비교해 store qty 를 0 으로 ``force_sync_position`` 해도
        `_live_entered` set 은 그대로 남았다. 결과: store flat 인데 dispatch 가
        그 (sid, symbol) 을 "live_position_open" 으로 영구 진입 차단 → 재진입
        불가. reconciler 가 청소한 4종목이 11시간 매수 0 의 원인.

        본 메서드를 reconciler 의 auto-fix 콜백으로 연결해 set 을 store 와
        정합한다. qty==0 → discard (재진입 허용), qty!=0 → add (보유 표기).
        cooldown(`_stop_cooldown_until`) 은 건드리지 않는다 — reconcile sync 는
        stop 청산이 아니라 단순 상태 정합이므로 cooldown 을 걸 이유가 없다.
        """
        key = (strategy_id, symbol)
        if qty == 0:
            self._live_entered.discard(key)
        else:
            self._live_entered.add(key)

    def restore_live_entered(
        self, positions: dict[str, list[tuple[str, float]]],
    ) -> None:
        """Startup-time restore of _live_entered from existing positions
        (typically StrategyPositionStore.all_positions()).

        BUG fixed: ``_live_entered`` 가 in-memory set 만이라 qta.exe / live_run.py
        재시작 시 비어있음 → 부팅 후 첫 tick 에 이미 보유 중인 (sid, symbol) 도
        '신규 진입' 으로 판정 → buy 발사 → broker 마진 누적 + 중복 매수.
        매번 재시작 = 같은 종목 추가 매수 폭주.

        본 메서드를 startup 시 호출하면 store/replay 로 복원된 logical position
        을 _live_entered 로 옮겨놓아 부팅 후 첫 tick 에 보유 중 종목은 진입 차단.
        is_live_scanner 가 True 인 strategy 만 추가 (단일종목 momo 등은 자체
        lifecycle 사용, 본 set 미사용).
        """
        added = 0
        for sid, sym_qty in positions.items():
            strat = self._strategies.get(sid)
            if not getattr(strat, "is_live_scanner", False):
                continue
            for symbol, qty in sym_qty:
                if qty != 0:
                    self._live_entered.add((sid, symbol))
                    added += 1
        return added

    def refresh_portfolio_risk(self, ts=None) -> Optional[PortfolioRiskReport]:
        return self._sync.refresh_portfolio_risk(ts)

    def strategy_reliability_score(self, strategy_id: str) -> float:
        return self._sync.strategy_reliability_score(strategy_id)

    @property
    def quarantined_strategies(self) -> frozenset[str]:
        return frozenset(self._quarantined)

    @property
    def strategies(self) -> dict[str, object]:
        """Read-only snapshot of registered strategies (#227 S3).

        Used by external wiring (e.g. live_run.py) to discover registered
        strategies and read their class attributes — typically the
        ``LiveScannerMixin`` exit thresholds.
        """
        return dict(self._strategies)

    @property
    def current_report(self) -> Optional[PortfolioRiskReport]:
        return self._sync.current_report

    # ---- enable / disable (#180) -------------------------------------------

    @property
    def disabled_strategies(self) -> frozenset[str]:
        return frozenset(self._disabled)

    def is_enabled(self, strategy_id: str) -> bool:
        return strategy_id not in self._disabled

    def enable_strategy(self, strategy_id: str) -> None:
        """Re-enable a previously disabled strategy.

        No-op if already enabled (audit event NOT emitted on no-op).
        """
        if strategy_id not in self._strategies:
            raise ValueError(f"strategy {strategy_id!r} not registered")
        if strategy_id not in self._disabled:
            return
        self._disabled.discard(strategy_id)
        self._emit_strategy_toggled(strategy_id, enabled=True)

    def disable_strategy(
        self,
        strategy_id: str,
        *,
        positions: list[tuple[str, float]] | None = None,
    ) -> list[OrderIntent]:
        """Disable a strategy: block new signals + emit WAL audit + return liquidation intents.

        Args:
            strategy_id: registered strategy id.
            positions: list of (symbol, qty) currently held by this strategy.
                Each non-zero qty produces a market-sell OrderIntent for the caller
                to submit (D1: 즉시 청산 — issue #180 user decision 2026-05-05).

        Returns:
            list[OrderIntent]: liquidation intents (side='sell') for each non-zero
            position. Empty if positions is None or all qtys are zero.

        Raises:
            ValueError: if strategy_id is not registered.

        Idempotent: disabling an already-disabled strategy still returns liquidation
        intents (caller may pass updated positions) but does NOT re-emit the WAL audit.
        """
        if strategy_id not in self._strategies:
            raise ValueError(f"strategy {strategy_id!r} not registered")

        was_enabled = strategy_id not in self._disabled
        self._disabled.add(strategy_id)
        if was_enabled:
            self._emit_strategy_toggled(strategy_id, enabled=False)

        intents: list[OrderIntent] = []
        if positions:
            for symbol, qty in positions:
                if qty <= 0:
                    continue
                intents.append(OrderIntent(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    reason="strategy_disabled_liquidation",
                ))
        return intents

    def _emit_strategy_toggled(self, strategy_id: str, *, enabled: bool) -> None:
        if self._wal_observer is None:
            return
        ev = WALEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            event_type=EVENT_STRATEGY_TOGGLED,
            payload={
                "strategy_id": strategy_id,
                "enabled": enabled,
                "actor": "user",
            },
        )
        try:
            self._wal_observer(ev)
        except Exception as err:
            logger.warning(
                "portfolio.orchestrator.wal_observer_error event=strategy_toggled "
                "strategy_id=%s enabled=%s error=%s",
                strategy_id, enabled, err,
            )

    def _emit_strategy_evaluated(
        self,
        strategy_id: str,
        *,
        symbol: str,
        decision: str,
        reason: str,
        ts: object,
    ) -> None:
        """Emit `strategy_evaluated` WAL event (#231 S5).

        Called once per (strategy, symbol) pair in run_bar dispatch — gives
        runtime visibility into on_bar invocation regardless of buy/sell/hold
        outcome. Used by AC0_strategy_dispatch + AC5 (24h dispatch ≥ 1000).
        Decision values: "buy" | "sell" | "hold" | "exception".
        """
        if self._wal_observer is None:
            return
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        ev = WALEvent(
            ts=ts_str,
            event_type=EVENT_STRATEGY_EVALUATED,
            payload={
                "strategy_id": strategy_id,
                "symbol": symbol,
                "decision": decision,
                "reason": reason,
            },
        )
        try:
            self._wal_observer(ev)
        except Exception as err:
            logger.warning(
                "portfolio.orchestrator.wal_observer_error event=strategy_evaluated "
                "strategy_id=%s symbol=%s decision=%s error=%s",
                strategy_id, symbol, decision, err,
            )

    # ---- async API ---------------------------------------------------------

    async def run_bar(
        self,
        ts,
        market_snapshot: dict,
        *,
        strategies: list[str] | None = None,
    ) -> list[OrderIntent]:
        targets = [
            sid for sid in self._strategies
            if sid not in self._quarantined
            and sid not in self._disabled
            and (strategies is None or sid in strategies)
        ]

        # Surface `factors` at ctx top-level (#177) so AsyncStrategies that read
        # `ctx["factors"][...]` (e.g. MomoKisV1 → ctx["factors"]["rsi"]) see
        # the precomputed series populated by SnapshotBuilder. Falls back to
        # empty dict for callers that don't supply factors.
        _snap_dict = market_snapshot if isinstance(market_snapshot, dict) else {}
        _factors = _snap_dict.get("factors", {})
        # Live-scanner per-symbol dispatch inputs (#227 S1). Both keys optional —
        # absence keeps every strategy on the legacy single-dispatch path.
        _universe_ohlcv = _snap_dict.get("ohlcv_history")
        _universe_factors = _snap_dict.get("universe_factors", {}) or {}
        _equity_krw = _snap_dict.get("equity_krw", 0.0)
        # #238 — venue-correct available equity for fraction→qty conversion.
        # Binance positions are USDT; the snapshot historically carried only
        # `equity_krw` (a KRW/placeholder value). A Binance `*USDT` symbol must
        # size against USDT equity, never KRW. Absent key → 0.0 → conversion
        # drops the order (safe: no order beats a wrong-currency order).
        _equity_usdt = _snap_dict.get("equity_usdt", 0.0)

        tasks = []
        sids = []
        # Per-task symbol override — set for live-scanner per-symbol dispatch,
        # None for legacy strategies (cs_*, momo_*, single-ticker).
        task_symbols: list[str | None] = []

        def _spawn(strategy, ctx):
            if inspect.iscoroutinefunction(strategy.on_bar):
                return asyncio.create_task(strategy.on_bar(ctx))
            return asyncio.create_task(asyncio.to_thread(strategy.on_bar, ctx))

        for sid in targets:
            strategy = self._strategies[sid]
            if (
                getattr(strategy, "is_live_scanner", False)
                and isinstance(_universe_ohlcv, dict)
                and _universe_ohlcv
            ):
                # #227 S1 — iterate the universe and create one task per symbol.
                # Each task receives a single-symbol market_snapshot + the
                # per-symbol factors slice (or empty dict if none registered).
                #
                # 2026-05-28 Dynamic Universe Phase 3 — per-strategy filtering.
                # 전략이 ``get_universe()`` 를 선언했으면 그 set 안의 symbol 만
                # dispatch. 미선언 (legacy) 면 전체 universe — byte-identical.
                # cs-tsmom 같은 universe-scan 도 default get_universe = TOP30 이라
                # 받는 set 이 같아 회귀 X.
                get_u_cm = getattr(type(strategy), "get_universe", None)
                allowed: set[str] | None = None
                if callable(get_u_cm):
                    try:
                        allowed = set(get_u_cm())
                    except Exception:
                        allowed = None
                for symbol, hist in _universe_ohlcv.items():
                    if hist is None or len(hist) == 0:
                        continue
                    if allowed is not None and symbol not in allowed:
                        continue
                    last_close = float(hist["close"].iloc[-1])
                    per_symbol_snap = {
                        "symbol": symbol,
                        "history": hist,
                        "price": last_close,
                        "equity_krw": _equity_krw,
                        # 2026-06-05: cross-symbol info — strategy 가 BTC trend
                        # filter 같은 universe-wide 가드를 적용할 수 있도록
                        # 전체 universe ohlcv 도 함께 노출. live-airborne 의
                        # btc_trend_filter (airborne 이 하락추세에서 LONG 잡는
                        # 사고 차단) 가 첫 소비자. 다른 strategy 는 본 key 를
                        # ignore — backward-compatible.
                        "universe_ohlcv": _universe_ohlcv,
                    }
                    per_symbol_factors = _universe_factors.get(symbol, {}) or {}
                    ctx = {
                        "ts": ts,
                        "market_snapshot": per_symbol_snap,
                        "factors": per_symbol_factors,
                        # 2026-06-08 — live 디스패치 표식. airborne 봉마감 게이트가
                        # 이 플래그로 live(미완성봉 치환) vs backtest(bench 는 직접
                        # on_bar 호출, 본 플래그 없음 → 무변경)를 구분한다.
                        "live_run": True,
                    }
                    tasks.append(_spawn(strategy, ctx))
                    sids.append(sid)
                    task_symbols.append(symbol)
            else:
                ctx = {"ts": ts, "market_snapshot": market_snapshot, "factors": _factors}
                tasks.append(_spawn(strategy, ctx))
                sids.append(sid)
                task_symbols.append(None)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        order_intents: list[OrderIntent] = []
        report_snapshot = self._sync.current_report
        # Dedup quarantine accounting to once per (sid, run_bar call) — a
        # live-scanner that throws across N symbols in one tick must not
        # increment the failure counter N times (#227 S1).
        counted_failed: set[str] = set()

        for sid, sym_override, result in zip(sids, task_symbols, results):
            # Resolve symbol once — used by S5 WAL event AND order routing.
            order_symbol = sym_override or _snap_dict.get("symbol", "UNKNOWN")

            if isinstance(result, BaseException):
                if sid not in counted_failed:
                    counted_failed.add(sid)
                    self._fail_count[sid] = self._fail_count.get(sid, 0) + 1
                    count = self._fail_count[sid]
                    logger.warning(
                        "portfolio.orchestrator.strategy_exception strategy_id=%s exception=%s",
                        sid,
                        result,
                    )
                    if count >= 3:
                        self._quarantined.add(sid)
                        logger.warning(
                            "portfolio.orchestrator.quarantine strategy_id=%s fail_count=%d",
                            sid,
                            count,
                        )
                self._emit_strategy_evaluated(
                    sid, symbol=order_symbol, decision="exception",
                    reason=type(result).__name__, ts=ts,
                )
                continue

            signal = result
            if signal is None:
                self._emit_strategy_evaluated(
                    sid, symbol=order_symbol, decision="hold",
                    reason="no_signal", ts=ts,
                )
                continue

            # Reset only when no sibling task for this sid threw in the same
            # tick — preserves the spirit of "consecutive bad ticks" semantics
            # for live-scanner strategies.
            if sid not in counted_failed:
                self._fail_count[sid] = 0

            if signal.action == "hold":
                self._emit_strategy_evaluated(
                    sid, symbol=order_symbol, decision="hold",
                    reason="action_hold", ts=ts,
                )
                continue

            # #238 — live-scanner 포지션 중복 진입 차단. 조건(ATR breakout 등)이
            # 지속되면 매 tick 진입 신호가 나오는데, 이미 (sid, symbol) 포지션을
            # 보유 중이면 추가 진입하지 않는다 (1 position per strategy-symbol).
            # 청산은 LivePositionRiskManager 가 stop/TP 로 수행 →
            # release_live_position() 호출 시 재진입 가능.
            #
            # #380 — buy 뿐 아니라 sell(숏 진입)도 동일 적용. 이전엔 buy 만
            # 차단해 SHORT-only/bidir live-scanner 의 숏 진입이 무방비로 매 게이트
            # 마다 stack 됐다 (2026-06-07 SHIB 4중진입 사고). live-scanner 의
            # buy=롱진입 / sell=숏진입 둘 다 entry 이며 청산은 risk manager 담당.
            is_live = getattr(self._strategies.get(sid), "is_live_scanner", False)
            if is_live and signal.action in ("buy", "sell"):
                key = (sid, order_symbol)
                # 2026-05-21 — stop 직후 cooldown 차단. release_live_position()
                # 에서 기록한 만료 시각이 지났는지 확인. 만료된 entry 는 여기서
                # 정리 (lazy cleanup) → dict 무한 성장 방지.
                cooldown_until = self._stop_cooldown_until.get(key, 0.0)
                if cooldown_until > 0.0:
                    if time.monotonic() < cooldown_until:
                        self._emit_strategy_evaluated(
                            sid, symbol=order_symbol, decision="hold",
                            reason="stop_cooldown_active", ts=ts,
                        )
                        continue
                    # cooldown 만료 → cleanup
                    self._stop_cooldown_until.pop(key, None)
                # #380 — max_concurrent_positions 캡 (전 전략 공통 옵션).
                # 신규 종목 진입 직전, 해당 strategy 의 현재 보유 포지션 수가
                # 캡 이상이면 진입 hold. top-100 universe 에서 동시에 수십 종목이
                # 발화해도 총 노출을 N 종목으로 제한한다. 미설정(None) 이면 무제한
                # (legacy 동작 보존). 이미 보유 중인 종목 재진입은 카운트에 무관
                # (아래 live_entered dedup 이 별도 처리).
                if key not in self._live_entered:
                    cap = getattr(
                        self._strategies.get(sid), "max_concurrent_positions", None
                    )
                    if cap is not None:
                        open_count = sum(
                            1 for (s, _sym) in self._live_entered if s == sid
                        )
                        if open_count >= int(cap):
                            self._emit_strategy_evaluated(
                                sid, symbol=order_symbol, decision="hold",
                                reason=f"max_concurrent_reached:{open_count}>={int(cap)}",
                                ts=ts,
                            )
                            continue
                if key in self._live_entered:
                    self._emit_strategy_evaluated(
                        sid, symbol=order_symbol, decision="hold",
                        reason="live_position_open", ts=ts,
                    )
                    continue
                self._live_entered.add(key)
                # 2026-05-21 — Signal 에 동적 stop/TP/trailing pct override 가
                # 들어있으면 risk manager 의 per-(sid, sym) dynamic policy 로
                # 등록. 콜백 미연결 또는 override 셋이 모두 None 이면 no-op
                # (정적 policy fallback). 단일 register 호출에 모두 모아 전달.
                if self._on_entry is not None and (
                    getattr(signal, "stop_loss_pct_override", None) is not None
                    or getattr(signal, "take_profit_pct_override", None) is not None
                    or getattr(signal, "trailing_stop_pct_override", None) is not None
                ):
                    try:
                        self._on_entry(
                            sid, order_symbol,
                            stop_loss_pct=getattr(signal, "stop_loss_pct_override", None),
                            take_profit_pct=getattr(signal, "take_profit_pct_override", None),
                            trailing_stop_pct=getattr(signal, "trailing_stop_pct_override", None),
                        )
                    except Exception as err:  # noqa: BLE001 — defensive
                        logger.warning(
                            "_on_entry callback failed sid=%s sym=%s err=%s",
                            sid, order_symbol, err,
                        )

            # #238 Item 3 — orchestrator-level duplicate-order backstop for
            # non-live-scanner strategies. While a strategy's condition
            # persists it re-emits an identical (sid, symbol, side) every WS
            # tick; suppress repeats within the wall-clock window so we stop
            # re-submitting the same order to the broker. A *different* action
            # (reversal/exit) uses a different key and is never throttled.
            # DEFAULT 0.0 → block skipped entirely (bit-identical).
            if self._min_order_interval_sec > 0.0 and not is_live:
                dup_key = (sid, order_symbol, signal.action)
                now = time.monotonic()
                last = self._last_order_ts.get(dup_key, 0.0)
                if last > 0.0 and (now - last) < self._min_order_interval_sec:
                    self._emit_strategy_evaluated(
                        sid, symbol=order_symbol, decision="hold",
                        reason="duplicate_order_throttled", ts=ts,
                    )
                    continue
                self._last_order_ts[dup_key] = now

            # buy/sell — emit before order routing so the event captures
            # strategy intent regardless of downstream risk-gate decision.
            self._emit_strategy_evaluated(
                sid, symbol=order_symbol, decision=signal.action,
                reason=getattr(signal, "reason", None) or "entry", ts=ts,
            )

            recent = self._recent_returns.get(sid)
            fraction = resolve_size(signal, recent)
            order_price = (
                _snap_dict.get("price", 0.0)
                if sym_override is None
                else float(_universe_ohlcv[sym_override]["close"].iloc[-1])
            )
            # #238 — `resolve_size` returns a *fraction of available equity*.
            # The orchestrator previously used that fraction DIRECTLY as the
            # coin qty (size=0.05 → 0.05 coins; momo full size=1.0 → 1.0 BTC
            # ≈ $80k → the -2019 Margin-insufficient flood). Convert it to a
            # real coin/share qty against the venue-correct equity, then apply
            # exchange filters (step ROUND_DOWN, min-notional, zero-qty drop).
            # Venue rule: KRX 6-digit → KRW equity; Binance `*USDT` → USDT
            # equity. A dropped (None) conversion emits NO OrderIntent — a
            # guaranteed-rejected order is exactly the bug class #238 fixed.
            if order_symbol.endswith("USDT") and len(order_symbol) > len("USDT"):
                venue_equity = _equity_usdt
            else:
                venue_equity = _equity_krw
            qty = size_to_qty(
                fraction,
                equity=venue_equity,
                price=order_price,
                symbol=order_symbol,
            )
            if qty is None:
                logger.info(
                    "portfolio.orchestrator.size_drop strategy_id=%s symbol=%s "
                    "fraction=%s equity=%s price=%s",
                    sid, order_symbol, fraction, venue_equity, order_price,
                )
                continue
            order = Order(
                symbol=order_symbol,
                side=signal.action,
                qty=qty,
                price=order_price,
            )
            snap = Snapshot(
                intent=order,
                equity_krw=_equity_krw,
                portfolio_risk=report_snapshot,
            )
            decision = evaluate(self._policy, snap)

            if decision.action == Action.ALLOW:
                # 2026-05-22 post-only Maker 진입 (post-only-maker-entry.draft.md).
                # BUY 진입에 한해 strategy 의 ``entry_order_type`` 속성을 읽어
                # OrderIntent 에 stamp. SELL(청산)은 항상 "market" — 확실한
                # 체결이 수수료 절감보다 우선. ref_price 는 위에서 per-symbol
                # 로 계산한 ``order_price`` — 멀티심볼 배치에서도 심볼별로
                # 정확하므로 executor 가 market_state.tick.last (단일 심볼만
                # 정확) 대신 이 값을 limit 가격 기준가로 쓴다 (gap A).
                strat = self._strategies.get(sid)
                order_intents.append(self._build_entry_intent(
                    strategy_id=sid,
                    strategy=strat,
                    symbol=order.symbol,
                    action=signal.action,
                    qty=qty,
                    price=order_price,
                    reason=signal.reason,
                ))
            else:
                logger.info(
                    "risk.breach rule_id=%s",
                    decision.rule_id,
                )

        self._bar_count += 1
        if (
            self._refresh_every_n_bars is not None
            and self._bar_count % self._refresh_every_n_bars == 0
        ):
            await self.refresh_portfolio_risk_async(ts)

        return order_intents

    # ---- shared entry tail (run_bar + dispatch_fire_entry) ------------------

    def _build_entry_intent(
        self,
        *,
        strategy_id: str,
        strategy: object,
        symbol: str,
        action: str,
        qty: float,
        price: float,
        reason: str,
    ) -> OrderIntent:
        """진입 OrderIntent 빌드 — entry_order_type stamp + preset TP/SL meta
        + reduce_only 판정. ``run_bar`` 의 ALLOW 분기와 ``dispatch_fire_entry``
        가 공유하는 꼬리 로직. 추출 전 ``run_bar`` 인라인 코드와 byte-identical
        (회귀 박제: tests/integration/test_airborne_path_smoke.py).

        2026-05-22 post-only Maker 진입 — BUY 진입에 한해 strategy 의
        ``entry_order_type`` 속성을 읽어 stamp. SELL(청산/숏진입)은 항상 market.

        2026-06-08 — 진입 주문에 거래소 네이티브 TP/SL 가격 첨부. 청산
        (reduce_only)이 아닌 진입에만, 전략의 stop_loss_pct/take_profit_pct
        (가격 pct)로 trigger 가격 계산해 meta 에 stamp. 숏(sell) 진입: SL=가격↑,
        TP=가격↓ / 롱(buy): 반대.
        """
        entry_order_type = "market"
        if action == "buy":
            declared = getattr(strategy, "entry_order_type", "market")
            if declared in ("market", "post_only"):
                entry_order_type = declared
        # #238 Item 7 — long-only 전략의 SELL 은 항상 청산이라 reduceOnly stamp
        # (보유 0 에서 sell 이 naked short 되는 사고 차단). shorts_allowed=True
        # 를 선언한 bidir 전략 (airborne v1.2 등) 은 SELL 이 short 진입일 수
        # 있으므로 reduceOnly 해제. long-only 전략 (default) 은 byte-identical.
        _ro = (
            action == "sell"
            and not getattr(strategy, "shorts_allowed", False)
        )
        _preset_meta = None
        if not _ro and price and price > 0:
            _slp = getattr(strategy, "stop_loss_pct", None)
            _tpp = getattr(strategy, "take_profit_pct", None)
            if _slp and _tpp:
                if action == "sell":   # short entry
                    _sl = price * (1 + float(_slp))
                    _tp = price * (1 - float(_tpp))
                else:                  # long entry
                    _sl = price * (1 - float(_slp))
                    _tp = price * (1 + float(_tpp))
                _preset_meta = {
                    "preset_tp_price": _tp, "preset_sl_price": _sl,
                }
        return OrderIntent(
            strategy_id=strategy_id,
            symbol=symbol,
            side=action,
            qty=qty,
            reason=reason,
            meta=_preset_meta,
            reduce_only=_ro,
            entry_order_type=entry_order_type,
            ref_price=(price if entry_order_type == "post_only" else None),
        )

    # ---- fire-driven entry (봉루프 decouple — airborne consume) -------------

    def dispatch_fire_entry(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        *,
        price: float,
        ts,
        equity_usdt: float,
    ) -> OrderIntent | None:
        """단일 발화(fire) 직접 진입 — ``run_bar`` 봉루프와 무관.

        airborne consume 의 ``history.jsonl`` 발화를 트레이더 OHLCV 봉루프와
        decouple 해 직접 구동한다 (봉 스냅샷 랙이 발화를 떨어뜨리지 못함, 2026-
        06-11 "7시 롱 미매수" 사고 fix — docs/specs/airborne-fire-driven-
        consume.md). ``run_bar`` 의 진입 로직(_live_entered dedup → stop
        cooldown → max_concurrent cap → _on_entry → sizing → policy → preset
        meta → OrderIntent)을 그대로 재사용하되 Signal 자체평가 대신 발화의
        side/price 를 사용한다.

        Args:
            strategy_id: 등록된 전략 id.
            symbol: 발화 종목 (예: "SOLUSDT").
            side: "long" | "short" — 'buy'/'sell' 로 매핑.
            price: 진입 기준가 (발화 close).
            ts: 발화 시각 (reason/WAL 표기용).
            equity_usdt: USDT venue 가용 자본 (사이징 기준).

        Returns:
            진입 OrderIntent 또는 진입 불가 시 None (dedup/cooldown/cap/sizing/
            risk gate 차단). None 이면 호출자는 발주하지 않고 dedup 도 안 찍는다.
        """
        # 1) 등록/quarantine/disabled 게이트.
        strat = self._strategies.get(strategy_id)
        if strat is None:
            return None
        if strategy_id in self._quarantined or strategy_id in self._disabled:
            return None

        action = "buy" if side == "long" else "sell"
        key = (strategy_id, symbol)

        # 2) live_entered dedup — (sid, symbol) 당 1 포지션.
        if key in self._live_entered:
            self._emit_strategy_evaluated(
                strategy_id, symbol=symbol, decision="hold",
                reason="live_position_open", ts=ts,
            )
            return None

        # 3) stop cooldown — 청산 직후 재진입 차단 (run_bar 와 동일).
        cooldown_until = self._stop_cooldown_until.get(key, 0.0)
        if cooldown_until > 0.0:
            if time.monotonic() < cooldown_until:
                self._emit_strategy_evaluated(
                    strategy_id, symbol=symbol, decision="hold",
                    reason="stop_cooldown_active", ts=ts,
                )
                return None
            self._stop_cooldown_until.pop(key, None)

        # 4) max_concurrent_positions 캡 (run_bar 와 동일).
        cap = getattr(strat, "max_concurrent_positions", None)
        if cap is not None:
            open_count = sum(
                1 for (s, _sym) in self._live_entered if s == strategy_id
            )
            if open_count >= int(cap):
                self._emit_strategy_evaluated(
                    strategy_id, symbol=symbol, decision="hold",
                    reason=f"max_concurrent_reached:{open_count}>={int(cap)}",
                    ts=ts,
                )
                return None

        # 5) 진입 기록 + _on_entry 동적 stop/TP 콜백. run_bar 은 Signal override
        # 를 쓰지만 fire 진입은 Signal 이 없으므로 전략 클래스 속성의 stop_loss_pct
        # /take_profit_pct/trailing_stop_pct 를 dynamic policy 로 등록한다 (정적
        # policy fallback 과 동일 값이지만 risk mgr 의 per-(sid,sym) 등록을 보장).
        self._live_entered.add(key)
        if self._on_entry is not None:
            _slp = getattr(strat, "stop_loss_pct", None)
            _tpp = getattr(strat, "take_profit_pct", None)
            _trl = getattr(strat, "trailing_stop_pct", None)
            if _slp is not None or _tpp is not None or _trl is not None:
                try:
                    self._on_entry(
                        strategy_id, symbol,
                        stop_loss_pct=_slp,
                        take_profit_pct=_tpp,
                        trailing_stop_pct=_trl,
                    )
                except Exception as err:  # noqa: BLE001 — defensive
                    logger.warning(
                        "_on_entry callback failed (fire) sid=%s sym=%s err=%s",
                        strategy_id, symbol, err,
                    )

        # 6) sizing — Signal-like 로 fraction 산출 → qty 변환.
        from backtest.protocol import Signal as _Signal
        signal = _Signal(
            action=action,
            size=getattr(strat, "default_size", 0.05),
            reason=f"fire_consume:{side}@{ts}",
        )
        self._emit_strategy_evaluated(
            strategy_id, symbol=symbol, decision=action,
            reason=signal.reason, ts=ts,
        )
        fraction = resolve_size(signal, self._recent_returns.get(strategy_id))
        qty = size_to_qty(
            fraction, equity=equity_usdt, price=price, symbol=symbol,
        )
        if qty is None:
            logger.info(
                "portfolio.orchestrator.fire_size_drop strategy_id=%s symbol=%s "
                "fraction=%s equity=%s price=%s",
                strategy_id, symbol, fraction, equity_usdt, price,
            )
            # 사이징 드롭 시 진입 기록 해제 — 미발주이므로 다음 발화에 재시도 허용.
            self._live_entered.discard(key)
            return None

        # 7) policy evaluate (run_bar 와 동일 Order/Snapshot 빌드).
        order = Order(symbol=symbol, side=action, qty=qty, price=price)
        snap = Snapshot(
            intent=order,
            equity_krw=equity_usdt,
            portfolio_risk=self._sync.current_report,
        )
        decision = evaluate(self._policy, snap)
        if decision.action != Action.ALLOW:
            logger.info("risk.breach (fire) rule_id=%s", decision.rule_id)
            self._live_entered.discard(key)
            return None

        # 8) preset TP/SL meta + OrderIntent (run_bar 와 공유 헬퍼).
        return self._build_entry_intent(
            strategy_id=strategy_id,
            strategy=strat,
            symbol=symbol,
            action=action,
            qty=qty,
            price=price,
            reason=signal.reason,
        )

    async def refresh_portfolio_risk_async(self, ts=None) -> Optional[PortfolioRiskReport]:
        t0 = time.monotonic()
        async with self._report_lock:
            report = self._sync.refresh_portfolio_risk(ts)
        age_sec = time.monotonic() - t0
        n = len(self._strategies)
        logger.info(
            "portfolio.orchestrator.risk_refresh age_sec=%.1f n_strategies=%d",
            age_sec,
            n,
        )
        return report

    async def start_risk_refresh_loop(
        self,
        interval_sec: float = 300.0,
        jitter_frac: float = 0.1,
    ) -> None:
        async def _loop() -> None:
            while True:
                jitter = interval_sec * jitter_frac * (2 * random.random() - 1)
                await asyncio.sleep(max(0.0, interval_sec + jitter))
                await self.refresh_portfolio_risk_async()

        self._refresh_task = asyncio.create_task(_loop())

    async def stop_risk_refresh_loop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
