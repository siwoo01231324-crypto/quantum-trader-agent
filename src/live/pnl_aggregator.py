"""Realized PnL aggregator fed by broker fill events (#194).

Backs `DashboardState.pnl_realtime / pnl_daily / pnl_monthly` and per-card
`pnl_today` so the dashboard reflects live trading without ad-hoc wiring.

Realized PnL only:
  buy:  cost_basis updated (qty-weighted average), realized = -fee
  sell: realized = (price - avg_cost) * qty - fee, holdings -= qty

KST 09:00 business-date convention (KRX trading day):
  - A fill timestamped before 09:00 KST belongs to the previous business day.
  - daily / monthly only accumulate fills whose business date == today's BD.
  - Reading `daily` / `monthly` after the BD has rolled returns 0 (auto-reset).

The aggregator parses `strategy_id` from the `{strategy}:{symbol}:{ts}:{idx}`
prefix in `client_order_id` (same fallback used by StrategyPositionStore in
#192). When PaperBroker eventually injects strategy_id into `order_filled`
payloads directly, the explicit field takes precedence.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from src.live.wal import replay

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")


class PnLAggregator:
    def __init__(self, *, kst_now: Callable[[], datetime] | None = None) -> None:
        self._cum_realized: float = 0.0
        self._daily: float = 0.0
        self._monthly: float = 0.0
        self._by_strategy: dict[str, float] = {}
        self._daily_by_strategy: dict[str, float] = {}
        # (strategy_id, symbol) → (qty held, avg cost)
        self._cost_basis: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
        # Track the BD/month last seen so we can auto-reset on rollover.
        self._cached_business_date: date | None = None
        self._cached_business_month: tuple[int, int] | None = None
        self._kst_now = kst_now or (lambda: datetime.now(KST))

    # -- public API --------------------------------------------------------

    def record_fill(
        self,
        *,
        strategy_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal = Decimal("0"),
        ts: datetime | None = None,
    ) -> None:
        realized = self._apply_to_cost_basis(strategy_id, symbol, side, qty, price, fee)

        # Cumulative buckets (always accumulate)
        self._cum_realized += realized
        self._by_strategy[strategy_id] = self._by_strategy.get(strategy_id, 0.0) + realized

        # Daily / monthly only when fill belongs to current BD/month.
        # Resolve "current" with a fresh check so we don't credit yesterday's
        # fills into today's bucket after midnight.
        self._refresh_business_window()
        fill_kst = self._to_kst(ts) if ts is not None else self._kst_now()
        fill_bd = self._business_date(fill_kst)
        if fill_bd == self._cached_business_date:
            self._daily += realized
            self._daily_by_strategy[strategy_id] = (
                self._daily_by_strategy.get(strategy_id, 0.0) + realized
            )
        if (fill_bd.year, fill_bd.month) == self._cached_business_month:
            self._monthly += realized

    def ingest_fill_event(self, event_type: str, payload: dict) -> None:
        if event_type != "order_filled":
            return
        symbol = payload.get("symbol")
        side = payload.get("side")
        raw_qty = payload.get("fill_qty") or payload.get("qty")
        raw_price = payload.get("fill_price") or payload.get("price")
        raw_fee = payload.get("fees") or payload.get("fee") or "0"
        if not (symbol and side and raw_qty is not None and raw_price is not None):
            return
        try:
            qty = Decimal(str(raw_qty))
            price = Decimal(str(raw_price))
            fee = Decimal(str(raw_fee))
        except Exception as err:
            logger.warning("ingest_fill_event: bad numeric in payload: %s", err)
            return
        strategy_id = payload.get("strategy_id") or self._resolve_strategy(
            payload.get("client_order_id", "")
        )
        if not strategy_id:
            logger.warning(
                "ingest_fill_event: cannot resolve strategy_id (coid=%r)",
                payload.get("client_order_id"),
            )
            return
        ts_str = payload.get("ts") or payload.get("fill_ts")
        ts = self._parse_iso(ts_str)
        self.record_fill(
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            fee=fee,
            ts=ts,
        )

    def replay_from_wal(self, wal_path: Path | str) -> None:
        events, _ = replay(wal_path)
        for event in events:
            payload = dict(event.payload or {})
            payload.setdefault("ts", event.ts)
            self.ingest_fill_event(event.event_type, payload)

    @property
    def realtime(self) -> float:
        return self._cum_realized

    @property
    def daily(self) -> float:
        self._refresh_business_window()
        return self._daily

    @property
    def monthly(self) -> float:
        self._refresh_business_window()
        return self._monthly

    @property
    def by_strategy(self) -> dict[str, float]:
        return dict(self._by_strategy)

    def daily_for(self, strategy_id: str) -> float:
        self._refresh_business_window()
        return self._daily_by_strategy.get(strategy_id, 0.0)

    # -- internals ---------------------------------------------------------

    def _apply_to_cost_basis(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        fee: Decimal,
    ) -> float:
        key = (strategy_id, symbol)
        held, avg = self._cost_basis.get(key, (Decimal("0"), Decimal("0")))
        if side.lower() == "buy":
            if held < 0:
                # Covering a SHORT (#238): realize (entry - cover) * qty on the
                # portion that closes the short; any excess opens a long.
                cover_qty = min(qty, -held)
                realized = float((avg - price) * cover_qty - fee)
                held = held + qty
                if held > 0:
                    # Flipped net long — the residual long's basis is `price`.
                    avg = price
            else:
                new_qty = held + qty
                if new_qty > 0:
                    avg = (held * avg + qty * price) / new_qty
                held = new_qty
                realized = -float(fee)
        else:  # sell
            if held <= 0:
                # Opening / adding to a SHORT (#238). Mirror the buy averaging
                # on the negative side so LivePositionRiskManager has an entry
                # price to gate against (root incident: naked short, no stop).
                new_qty = held - qty
                # Guard the divide the same way the long-add path guards
                # `if new_qty > 0` (#238 review MEDIUM): a broker zero-qty
                # correction / liquidation-ack sell with held==0 would make
                # -new_qty==0 → ZeroDivisionError, silently killing the
                # aggregator (→ all P&L / risk-gating halts). qty==0 is then a
                # -fee no-op; the short entry avg is left untouched.
                if -new_qty > 0:
                    avg = ((-held) * avg + qty * price) / (-new_qty)
                held = new_qty
                realized = -float(fee)
            else:
                # Selling a LONG. For qty <= held this is byte-identical to
                # the legacy path. For an oversized sell (qty > held) the long
                # portion realizes (price-avg)*held and the excess opens a
                # SHORT at `price` — symmetric to the buy-side cover→flip
                # above. (#238 review: the old code used the full qty here,
                # over-realizing P&L and leaving the new short on the stale
                # long avg.)
                close_qty = min(qty, held)
                realized = float((price - avg) * close_qty - fee)
                held = held - qty
                if held < 0:
                    avg = price
        self._cost_basis[key] = (held, avg)
        return realized

    def _refresh_business_window(self) -> None:
        bd = self._business_date(self._kst_now())
        ym = (bd.year, bd.month)
        if self._cached_business_date is None:
            self._cached_business_date = bd
            self._cached_business_month = ym
            return
        if bd != self._cached_business_date:
            self._daily = 0.0
            self._daily_by_strategy.clear()
            self._cached_business_date = bd
        if ym != self._cached_business_month:
            self._monthly = 0.0
            self._cached_business_month = ym

    @staticmethod
    def _business_date(kst_dt: datetime) -> date:
        if kst_dt.time() < dtime(hour=9):
            return (kst_dt - timedelta(days=1)).date()
        return kst_dt.date()

    @staticmethod
    def _to_kst(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=KST)
        return dt.astimezone(KST)

    @staticmethod
    def _parse_iso(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    @staticmethod
    def _resolve_strategy(client_order_id: str) -> str | None:
        if not client_order_id:
            return None
        head, sep, _ = client_order_id.partition(":")
        return head if sep else None
