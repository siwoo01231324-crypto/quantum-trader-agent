"""Universe-scan basket dispatcher (#218 follow-up — 2026-05-21 fix).

`cs_async_wrapper.CrossSectionalAsyncStrategy` 는 매주 리밸 시점에
``latest_weights: pd.Series[symbol -> weight]`` 를 갱신한다. 단 그 wrapper 의
``on_bar`` 는 ``Signal(action="buy", size=exposure, reason="rebal:N_picks")``
만 emit 하고 symbol 은 가짜 basket id ("CRYPTO_TOP30_BASKET").

``AsyncStrategyOrchestrator.run_bar`` 가 그 Signal 받으면 size_to_qty 가
basket 가격을 못 찾아 ``None`` → OrderIntent 발행 자체가 silent drop. 즉
universe-scan 전략은 **별도 polling hook** 으로 ``latest_weights`` 를 직접
읽어 ``portfolio.cs_rebalance_dispatch.dispatch_rebalance()`` 로 종목별
실거래 발주를 해줘야 한다.

본 모듈은 그 hook 을 제공한다:

  await dispatch_universe_baskets(
      orchestrator=orch,
      snapshot=snapshot,
      broker=router,
      position_store=store,
      ohlcv_history=market_snapshot.get("ohlcv_history"),
      wal=wal,
  )

호출은 ``scripts/live_run.py`` 의 consumer 가 ``orchestrator.run_bar`` 직후에
1회 한다. 같은 weights 의 중복 발주는 in-memory 캐시 (``_last_dispatched``)
로 차단 — weights signature 가 바뀐 strategy 만 처리.

env gate: ``CS_BASKET_DISPATCH=1`` 일 때만 live_run 이 wire 한다. 기본 OFF
라 EXE 회귀 무영향.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd

from portfolio.cs_rebalance_dispatch import (
    RebalanceReport,
    dispatch_rebalance,
)
from portfolio.weights_to_orders import (
    BINANCE_DEFAULT_LOT,
    KRX_LOT,
    LotSpec,
)

logger = logging.getLogger(__name__)


def _resolve_lot_spec(strategy_id: str, symbols: list[str]) -> LotSpec:
    """전략·심볼 기반 lot_spec 결정.

    cs_tsmom_kr_daily / cs_rsi_div_kr / cs_adx_ma_kr / cs_bb_macd_kr → KRX_LOT.
    cs_tsmom_crypto_daily / cs_rsi_div_crypto / cs_macd_vol_crypto → Binance.
    """
    if any(s.endswith("USDT") for s in symbols):
        return BINANCE_DEFAULT_LOT
    return KRX_LOT


def _snapshot_field(snapshot: Any, key: str, default: float = 0.0) -> float:
    """Read a numeric field from snapshot regardless of shape (#324).

    Live runtime: ``SnapshotBuilder.build_snapshot()`` returns a **dict**.
    Legacy tests: ``SimpleNamespace`` 객체.  이전 코드는 dict 에 ``getattr``
    을 호출해 항상 default 를 받았고, 그 결과 ``capital=0`` → 매 tick
    ``reason=zero_equity`` silent skip → cs-tsmom-crypto-daily 발주가 한 건도
    안 나가던 사고가 발생했다. 두 shape 모두 받게 한다.
    """
    if isinstance(snapshot, dict):
        val = snapshot.get(key, default)
    else:
        val = getattr(snapshot, key, default)
    return float(val or 0.0)


def _equity_for_strategy(snapshot: Any, strategy_id: str,
                          symbols: list[str]) -> float:
    """전략 venue 에 맞는 equity 추출.

    Binance basket (symbols 가 *USDT) → equity_usdt.
    KRX basket → equity_krw.
    Snapshot 없거나 필드 부재 시 0 → caller 가 skip.
    """
    if any(s.endswith("USDT") for s in symbols):
        return _snapshot_field(snapshot, "equity_usdt", 0.0)
    return _snapshot_field(snapshot, "equity_krw", 0.0)


def _prices_from_ohlcv(symbols: list[str],
                        ohlcv_history: Mapping[str, pd.DataFrame] | None) -> pd.Series:
    """ohlcv_history dict 에서 종목별 마지막 close 추출.

    누락 종목은 0 (weights_to_orders 가 0 가격이면 그 종목 청산만 시도).
    """
    if not ohlcv_history:
        return pd.Series(dtype=float)
    out: dict[str, float] = {}
    for sym in symbols:
        df = ohlcv_history.get(sym)
        if df is None or len(df) == 0:
            continue
        try:
            last = df["close"].iloc[-1]
            if pd.notna(last) and float(last) > 0:
                out[sym] = float(last)
        except (KeyError, IndexError, ValueError):
            continue
    return pd.Series(out, dtype=float)


def _weights_signature(weights: pd.Series) -> tuple:
    """weights diff 감지용 hashable signature.

    같은 (symbol, rounded_weight) 튜플 정렬 시 동일 → 중복 dispatch 차단.
    rounding 1e-6 까지 — basis-point 변화는 noise 로 무시.
    """
    return tuple(sorted(
        (str(sym), round(float(w), 6))
        for sym, w in weights.items() if pd.notna(w) and float(w) > 0
    ))


@dataclass
class BasketDispatcher:
    """중복 dispatch 차단용 stateful wrapper.

    ``orchestrator.strategies`` 를 iterate 해서 ``latest_weights`` 를 노출하는
    전략만 후킹. signature 가 바뀐 전략만 ``dispatch_rebalance`` 호출.
    """
    last_dispatched: dict[str, tuple] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.last_dispatched is None:
            self.last_dispatched = {}

    async def dispatch(
        self,
        *,
        orchestrator: Any,
        snapshot: Any,
        broker: Any,
        position_store: Any | None = None,
        ohlcv_history: Mapping[str, pd.DataFrame] | None = None,
        wal: Any | None = None,
    ) -> list[RebalanceReport]:
        reports: list[RebalanceReport] = []
        strategies = getattr(orchestrator, "strategies", None) or {}
        for sid, strat in strategies.items():
            weights = getattr(strat, "latest_weights", None)
            if weights is None or not hasattr(weights, "empty") or weights.empty:
                continue
            sig = _weights_signature(weights)
            if not sig:
                continue
            if self.last_dispatched.get(sid) == sig:
                continue  # 같은 weights — 이미 dispatch 됨

            symbols = list(weights.index)
            lot_spec = _resolve_lot_spec(sid, symbols)
            capital = _equity_for_strategy(snapshot, sid, symbols)
            if capital <= 0:
                logger.info(
                    "cs_basket_dispatcher.skip strategy_id=%s reason=zero_equity",
                    sid,
                )
                continue
            prices = _prices_from_ohlcv(symbols, ohlcv_history)
            if prices.empty:
                logger.info(
                    "cs_basket_dispatcher.skip strategy_id=%s reason=no_prices",
                    sid,
                )
                continue

            current: dict[str, float] = {}
            if position_store is not None and hasattr(position_store, "get_positions"):
                try:
                    for sym, qty in (position_store.get_positions(sid) or []):
                        current[str(sym)] = float(qty)
                except Exception as err:  # noqa: BLE001
                    logger.warning(
                        "cs_basket_dispatcher.position_store_failed sid=%s err=%s",
                        sid, err,
                    )

            try:
                report = await dispatch_rebalance(
                    strategy_id=sid,
                    target_weights=weights,
                    current_positions=current,
                    prices=prices,
                    total_capital=capital,
                    broker=broker,
                    lot_spec=lot_spec,
                    rebal_reason=f"cs_basket_dispatch:{sid}",
                )
            except Exception as err:  # noqa: BLE001 — never abort live loop
                logger.error(
                    "cs_basket_dispatcher.dispatch_failed sid=%s err=%s",
                    sid, err, exc_info=True,
                )
                continue

            self.last_dispatched[sid] = sig
            reports.append(report)

            if wal is not None:
                try:
                    from execution.wal import WALEvent  # noqa: PLC0415
                    wal.write(WALEvent(
                        ts=datetime.now(timezone.utc).isoformat(),
                        event_type="basket_rebalanced",
                        payload={
                            "strategy_id": sid,
                            "n_picks": int(sum(1 for w in weights if w > 0)),
                            "n_submitted": report.summary.get("n_submitted", 0),
                            "n_rejected": report.summary.get("n_rejected", 0),
                            "n_skipped": report.summary.get("n_skipped_exception", 0),
                            "capital": capital,
                            "lot_spec": lot_spec.__class__.__name__,
                        },
                    ))
                except Exception as wal_err:  # noqa: BLE001
                    logger.warning(
                        "cs_basket_dispatcher.wal_write_failed sid=%s err=%s",
                        sid, wal_err,
                    )

        return reports
