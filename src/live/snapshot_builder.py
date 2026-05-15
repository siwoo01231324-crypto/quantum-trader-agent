"""Multi-timeframe market snapshot builder for the live loop (#177).

Strategies bundled in `configs/orchestrator/production.yaml` consume different
shapes of `market_snapshot`:

  - MomoBtcV2 (sync, via _StrategyAdapter): bar + history + context["factors"]
  - MomoVolFiltered (4h):  snap["ohlcv_history"][SYMBOL]
  - MeanrevPairs (1h):     snap["ohlcv_history"][SYMBOL]
  - BreakoutDonchian (EOD): snap["ohlcv_history"]  (per-symbol mapping)
  - MomoKisV1 (15m):       snap["history"] + ctx["factors"]["rsi"]

The previous `_tick_to_market_snapshot` helper only emitted
`{ts, symbol, price, equity_krw}`, so 4 of 5 strategies always returned
"insufficient history" → no signals.

`SnapshotBuilder.warmup()` boots a per-symbol 1m OHLCV buffer using KIS REST
backfill (`fetch_intraday_ohlcv_raw`). `build_snapshot(tick)` then derives the
fields above each tick. The 1m series is also exposed at higher timeframes via
pandas resample.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.live.types import Tick
from universe.krx_calendar import KST

logger = logging.getLogger(__name__)

_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
_KRX_SYMBOL_LEN = 6
_DEFAULT_BUFFER_LIMIT = 1500


def is_krx_symbol(symbol: str) -> bool:
    """Return True iff *symbol* matches the KRX 6-digit code shape (e.g. ``005930``)."""
    return len(symbol) == _KRX_SYMBOL_LEN and symbol.isdigit()


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=_OHLCV_COLUMNS).astype(float)


@dataclass
class SnapshotBuilderConfig:
    warmup_bars: int = 1000
    buffer_limit: int = _DEFAULT_BUFFER_LIMIT
    equity_krw: float = 100000.0


class SnapshotBuilder:
    """Rolling per-symbol 1m OHLCV buffer + multi-timeframe snapshot factory.

    Parameters
    ----------
    symbols: list of subscription symbols.
    kis_client: any object exposing the surface consumed by
        `src.brokers.kis.price_client.fetch_intraday_ohlcv_raw`. May be None,
        in which case warmup quietly leaves the KRX buffers empty (Phase 1
        Binance-only mode or unit tests).
    config: optional `SnapshotBuilderConfig` for warmup depth and equity hint.
    """

    def __init__(
        self,
        symbols: list[str],
        kis_client: Any | None = None,
        *,
        config: SnapshotBuilderConfig | None = None,
        universe_quote_provider: Any | None = None,
        universe_ttl_sec: float = 300.0,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._kis_client = kis_client
        self._config = config or SnapshotBuilderConfig()
        self._buffers: dict[str, pd.DataFrame] = {}
        # #231 S2 — cs_async_wrapper 등 universe-scan strategies 가 dispatch
        # 되도록 매 N초마다 broker.fetch_universe_snapshot 호출 + 결과를
        # ohlcv_history dict 에 merge. provider 미주입 시 zero-impact
        # (graceful hold path 유지 — production.yaml 주석에 명시).
        # provider signature: () -> dict[symbol_code, pd.DataFrame(OHLCV)]
        self._universe_quote_provider = universe_quote_provider
        self._universe_ttl_sec = float(universe_ttl_sec)
        self._universe_cache: dict[str, pd.DataFrame] = {}
        self._universe_cache_ts: float = 0.0

    # ── Public surface ───────────────────────────────────────────────────

    async def warmup(self) -> None:
        """Bootstrap per-symbol 1m buffers via KIS REST backfill (KRX symbols only).

        Non-KRX symbols receive an empty buffer; their strategies stay in
        warmup hold until live ticks accumulate.
        """
        for symbol in self._symbols:
            if not is_krx_symbol(symbol):
                self._buffers[symbol] = _empty_ohlcv()
                continue
            if self._kis_client is None:
                logger.warning(
                    "SnapshotBuilder.warmup: no KIS client; %s buffer empty",
                    symbol,
                )
                self._buffers[symbol] = _empty_ohlcv()
                continue
            try:
                df = await asyncio.to_thread(self._fetch_warmup_df, symbol)
            except Exception as exc:
                logger.warning(
                    "SnapshotBuilder.warmup_failed symbol=%s error=%s",
                    symbol, exc,
                )
                df = _empty_ohlcv()
            self._buffers[symbol] = df.iloc[-self._config.warmup_bars:]
            logger.info(
                "SnapshotBuilder.warmup_loaded symbol=%s bars=%d",
                symbol, len(self._buffers[symbol]),
            )

    def append_tick(self, tick: Tick) -> None:
        """Append a synthesized 1m bar (close-only) for *tick* to its buffer.

        Tick values from polling-style feeds carry close + cumulative volume;
        we set ``open=high=low=close=tick.price`` for the inserted row. Higher
        moments (ATR, MACD on highs/lows) therefore see zero intra-bar
        volatility — strategies that depend on those degrade to "hold" rather
        than firing on noise. WS realtime feed (future issue) reconstructs
        full OHLC.
        """
        ts = self._normalize_ts(tick)
        price = float(tick.price)
        volume = float(tick.qty)
        df = self._buffers.setdefault(tick.symbol, _empty_ohlcv())
        if ts in df.index:
            return
        new_row = pd.DataFrame(
            [[price, price, price, price, volume]],
            columns=_OHLCV_COLUMNS,
            index=[ts],
        )
        if df.empty:
            merged = new_row
        else:
            merged = pd.concat([df, new_row])
        self._buffers[tick.symbol] = merged.iloc[-self._config.buffer_limit:]

    def _refresh_universe_cache_if_stale(self) -> None:
        """Pull universe OHLCV via provider when TTL elapsed (#231 S2).

        Sync call (provider must be sync; for async brokers, caller wraps with
        a thread-bridge). Failures are swallowed + logged — cs_async_wrapper
        falls back to graceful hold if no data merged.
        """
        if self._universe_quote_provider is None:
            return
        import time
        now = time.monotonic()
        if now - self._universe_cache_ts < self._universe_ttl_sec:
            return
        try:
            universe_data = self._universe_quote_provider()
            if isinstance(universe_data, dict):
                self._universe_cache = universe_data
                self._universe_cache_ts = now
                logger.info(
                    "SnapshotBuilder.universe_refreshed symbols=%d",
                    len(universe_data),
                )
        except Exception as exc:
            logger.warning(
                "SnapshotBuilder.universe_quote_failed error=%s — graceful hold path",
                exc,
            )

    def build_snapshot(self, tick: Tick) -> dict[str, Any]:
        """Construct the `market_snapshot` dict consumed by orchestrator strategies."""
        self.append_tick(tick)
        symbol = tick.symbol
        history_1m = self._buffers.get(symbol, _empty_ohlcv())
        # #231 S2 — universe-scan strategies (cs_*) 를 위해 universe OHLCV 를
        # 별도 provider 로 fetch + cache. live buffers (3종 tick) 와 universe
        # cache (350종 일봉) 를 merge — 같은 symbol 충돌 시 live buffer 우선.
        self._refresh_universe_cache_if_stale()
        ohlcv_history = {sym: buf for sym, buf in self._universe_cache.items()}
        ohlcv_history.update(self._buffers)

        factors = self._compute_factors(history_1m)

        snapshot: dict[str, Any] = {
            "ts": tick.ts,
            "symbol": symbol,
            "price": float(tick.price),
            "equity_krw": self._config.equity_krw,
            "history": history_1m,
            "ohlcv_history": ohlcv_history,
            "factors": factors,
        }
        return snapshot

    @property
    def buffers(self) -> dict[str, pd.DataFrame]:
        """Read-only view for tests."""
        return self._buffers

    # ── Internals ────────────────────────────────────────────────────────

    def _fetch_warmup_df(self, symbol: str) -> pd.DataFrame:
        from src.brokers.kis.price_client import fetch_intraday_ohlcv_raw
        today = datetime.now(KST).strftime("%Y%m%d")
        bars = fetch_intraday_ohlcv_raw(
            self._kis_client, symbol, today, interval="1",
        )
        rows = []
        idx = []
        for bar in bars:
            ts = pd.Timestamp(
                datetime.strptime(f"{bar.date}{bar.time}", "%Y%m%d%H%M%S")
                .replace(tzinfo=KST)
                .astimezone(timezone.utc)
            )
            rows.append([float(bar.open), float(bar.high), float(bar.low),
                         float(bar.close), float(bar.volume)])
            idx.append(ts)
        if not rows:
            return _empty_ohlcv()
        return pd.DataFrame(rows, columns=_OHLCV_COLUMNS, index=pd.DatetimeIndex(idx, tz="UTC"))

    @staticmethod
    def _normalize_ts(tick: Tick) -> pd.Timestamp:
        raw = tick.server_ts or tick.ts
        ts = pd.Timestamp(raw)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        # Floor to 1-minute boundary so duplicate-bar detection is exact.
        return ts.floor("1min")

    @staticmethod
    def _compute_factors(history_1m: pd.DataFrame) -> dict[str, pd.Series]:
        """Precompute factors strategies declare via `required_factors`.

        We currently materialise RSI(14) so MomoKisV1 / MomoBtcV2 can fire;
        other factors are computed on-demand inside the strategies via
        `signals.compute(...)`.
        """
        if history_1m.empty:
            return {"rsi": pd.Series(dtype=float)}
        try:
            from signals.rsi import compute_rsi
            rsi = compute_rsi(history_1m["close"], period=14)
            return {"rsi": rsi}
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("SnapshotBuilder._compute_factors_failed error=%s", exc)
            return {"rsi": pd.Series(dtype=float)}
