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
        balance_provider: Any | None = None,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._kis_client = kis_client
        self._config = config or SnapshotBuilderConfig()
        # #238 Item 9 — real venue balances → snapshot equity. Without this
        # the orchestrator's #238-Item-8 fraction→qty conversion sees
        # equity_usdt=0 and safely DROPS every Binance order (inert). Inject
        # the existing AccountInfoProvider (internally 15s-cached, so a
        # per-tick fetch() is cheap). provider.fetch() →
        # {"binance": {"ok", "available_usdt"}, "kis": {"ok", "cash_balance"}}.
        # None (default) → config placeholder only → byte-identical to pre-#238.
        self._balance_provider = balance_provider
        # #238 follow-up — venue-inert visibility. Mirrors the venue keys
        # emitted by AccountInfoProvider.fetch() ("binance", "kis"). Each
        # value: {"ok": bool, "reason": str, "equity": float}. The dashboard
        # reads this so a silently-dropped venue (equity unavailable → every
        # order dropped by the Item-8 conversion) is no longer invisible.
        # Empty when balance_provider is None (default) → byte-identical.
        self.last_equity_status: dict[str, dict[str, Any]] = {}
        # Last *ok* flag reported per venue, so the WARNING only fires on a
        # state-change (mirrors the project's throttle ethos — no per-tick
        # log flood). None = never reported yet.
        self._last_equity_ok: dict[str, bool] = {}
        # Last-known-GOOD real equity per venue (#238 follow-up root cause).
        # The live KIS daemon hammers KIS REST (warmup + feed) which
        # transiently rate-limits (EGW00201) a freshly-constructed
        # AccountInfoProvider's balance call. Standalone (no contention) the
        # same fetch succeeds. WITHOUT this, a single transient ok:False
        # regressed equity_krw back to the tiny placeholder → the Item-8
        # conversion dropped EVERY KIS order → operator saw "0 trades".
        # Once a venue has reported real (>0) equity we keep overlaying that
        # value across later transient failures (the venue stays trading on
        # last-known-good) while still surfacing the degraded status. We
        # never FABRICATE equity: a venue that was never observed stays on
        # the placeholder so the conversion safely drops (no naked sizing).
        self._last_good_equity: dict[str, float] = {}
        self._buffers: dict[str, pd.DataFrame] = {}
        # #231 S2 — cs_async_wrapper 등 universe-scan strategies 가 dispatch
        # 되도록 매 N초마다 broker.fetch_universe_snapshot 호출 + 결과를
        # ohlcv_history dict 에 merge. provider 미주입 시 zero-impact
        # (graceful hold path 유지 — production.yaml 주석에 명시).
        # provider signature: () -> dict[symbol_code, pd.DataFrame(OHLCV)]
        self._universe_quote_provider = universe_quote_provider
        self._universe_ttl_sec = float(universe_ttl_sec)
        self._universe_cache: dict[str, pd.DataFrame] = {}
        # `-inf` sentinel — `monotonic()` returns process/system uptime which can
        # be small on fresh CI runners; using `0.0` made the "is stale?" check
        # (`now - ts < ttl`) return True on first call, skipping the provider
        # entirely (#236 follow-up: regression introduced in #231 S2).
        self._universe_cache_ts: float = float("-inf")

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
        self._inject_real_equity(snapshot)
        return snapshot

    def refresh_balance(self) -> None:
        """Refresh the balance provider's cache OFF the event-loop thread.

        #3 (prior-review MEDIUM): `build_snapshot` runs synchronously in the
        live consumer coroutine on the event-loop thread. The loop calls this
        via ``await asyncio.to_thread(snapshot_builder.refresh_balance)``
        once per tick BEFORE the sync `build_snapshot`, so the only thing
        that ever runs the (15s-cached) KIS+Binance REST is this worker
        thread — never the event loop. `_inject_real_equity` then does a
        pure non-blocking cached read.

        No provider, or a provider without ``fetch()``, is a harmless no-op
        (byte-identical to pre-#3 for the default path). Errors are swallowed
        + logged here (the loop already guards, and `_inject_real_equity`
        independently surfaces a degraded venue from the stale cache).
        """
        provider = self._balance_provider
        if provider is None:
            return
        fetch = getattr(provider, "fetch", None)
        if not callable(fetch):
            return
        try:
            fetch()
        except Exception as err:  # noqa: BLE001 — never kill the tick loop
            logger.warning("snapshot.balance_refresh_error: %s", err)

    def _read_balance(self) -> dict[str, Any] | None:
        """Non-blocking read of the latest balances for `_inject_real_equity`.

        Prefers the provider's ``peek()`` (pure cached read, no REST — the
        #3 off-loop seam: the loop pre-warms via `refresh_balance` so peek()
        is a hit). Falls back to ``fetch()`` only for providers/test fakes
        that do not implement peek() — preserving the pre-#3 contract for
        those (their fetch() is cheap / internally cached).
        """
        provider = self._balance_provider
        peek = getattr(provider, "peek", None)
        if callable(peek):
            return peek() or {}
        return provider.fetch() or {}

    def _inject_real_equity(self, snapshot: dict[str, Any]) -> None:
        """Overlay real venue balances onto the snapshot (#238 Item 9).

        Best-effort + last-known-good: once a venue has reported real (>0)
        equity we keep overlaying that value across later TRANSIENT provider
        failures so the live daemon does not silently stop trading on a
        single rate-limited balance call. A venue that was NEVER observed
        leaves the config placeholder (equity_usdt unset → Item-8 conversion
        safely drops — no fabricated/naked sizing). MUST NOT raise into the
        per-tick hot loop.

        #3: the balance is read NON-BLOCKING here (peek of the provider's
        cache that the loop pre-warmed off-loop via `refresh_balance`); the
        event-loop thread never runs the cache-miss REST.
        """
        if self._balance_provider is None:
            return
        try:
            bal = self._read_balance() or {}
        except Exception as err:  # noqa: BLE001 — never kill the tick loop
            logger.warning("snapshot.balance_provider_error: %s", err)
            # The provider blew up → degraded this tick. Re-overlay any
            # last-known-good equity so a transient failure does not stop
            # trading; surface the degraded status regardless.
            reason = f"balance_provider_error: {type(err).__name__}: {err}"
            self._apply_venue(snapshot, "binance", "equity_usdt",
                              ok=False, reason=reason, fresh_equity=None)
            self._apply_venue(snapshot, "kis", "equity_krw",
                              ok=False, reason=reason, fresh_equity=None)
            return

        binance = bal.get("binance") or {}
        usdt = binance.get("available_usdt")
        bn_ok = bool(binance.get("ok") and usdt is not None and float(usdt) > 0)
        self._apply_venue(
            snapshot, "binance", "equity_usdt",
            ok=bn_ok,
            reason="" if bn_ok else self._inert_reason(binance, usdt),
            fresh_equity=float(usdt) if bn_ok else None,
        )

        kis = bal.get("kis") or {}
        cash = kis.get("cash_balance")
        kis_ok = bool(kis.get("ok") and cash is not None and float(cash) > 0)
        self._apply_venue(
            snapshot, "kis", "equity_krw",
            ok=kis_ok,
            reason="" if kis_ok else self._inert_reason(kis, cash),
            fresh_equity=float(cash) if kis_ok else None,
        )

    def _apply_venue(
        self, snapshot: dict[str, Any], venue: str, equity_key: str,
        *, ok: bool, reason: str, fresh_equity: float | None,
    ) -> None:
        """Overlay one venue's equity + record status (last-known-good aware).

        - fresh real equity (>0) → overlay it, remember it as known-good.
        - degraded (ok:False) but a prior known-good exists → re-overlay the
          last-known-good value (the venue keeps trading through a transient
          provider failure) while reporting the degraded status.
        - degraded and NEVER observed → leave the placeholder untouched so
          the Item-8 conversion safely drops (no fabricated sizing).
        """
        if ok and fresh_equity is not None:
            snapshot[equity_key] = fresh_equity
            self._last_good_equity[venue] = fresh_equity
            self._record_equity_status(venue, ok=True, reason="",
                                       equity=fresh_equity)
            return
        last_good = self._last_good_equity.get(venue)
        if last_good is not None:
            # Transient failure — hold last-known-good so trading continues.
            snapshot[equity_key] = last_good
            self._record_equity_status(
                venue, ok=False,
                reason=f"{reason} (last-known-good equity={last_good} 유지)",
                equity=last_good,
            )
            return
        # Never observed real equity → leave placeholder (safe drop).
        self._record_equity_status(venue, ok=False, reason=reason, equity=0.0)

    @staticmethod
    def _inert_reason(venue_block: dict[str, Any], balance: Any) -> str:
        """Human-readable reason a venue is inert (no real equity)."""
        if not venue_block.get("ok"):
            return str(venue_block.get("error") or "venue ok:False")
        if balance is None:
            return "balance missing"
        return "cash_balance<=0"

    def _record_equity_status(
        self, venue: str, *, ok: bool, reason: str, equity: float,
    ) -> None:
        """Stash structured status + emit a throttled WARNING on state-change.

        The WARNING fires only when *ok* transitions for this venue (or on
        the first observation while inert), never per-tick — mirroring the
        project's throttle ethos so the log does not flood at tick frequency.
        """
        self.last_equity_status[venue] = {
            "ok": ok, "reason": reason, "equity": equity,
        }
        prev = self._last_equity_ok.get(venue)
        if prev != ok:
            self._last_equity_ok[venue] = ok
            if not ok:
                logger.warning(
                    "snapshot.venue_equity INERT venue=%s reason=%s "
                    "— 해당 venue 주문 전량 보류 (real equity 미확보)",
                    venue, reason,
                )
            else:
                logger.info(
                    "snapshot.venue_equity RECOVERED venue=%s equity=%s",
                    venue, equity,
                )

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
