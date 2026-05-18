"""Cross-run round-trip trade-history reconstruction (read-only WAL replay).

Pure & deterministic. Replays `order_filled` WAL events and pairs fills
per (strategy_id, symbol) into round-trip trades using the SAME long/short
+ flip accounting as `pnl_aggregator._apply_to_cost_basis`:

  - an entry fill opens / increases the position (qty-weighted avg entry)
  - an opposite-side fill reduces / closes the position; realized P&L is
    booked only on the closing portion (currency = the venue's, NEVER
    cross-converted)
  - an oversized opposite fill *flips*: it closes the existing side then
    opens a fresh position on the other side at the fill price
  - any unclosed remainder at end-of-replay → one `status="open"` trade
    (entry only; no exit_ts / exit_price / realized_pnl / holding_seconds)

strategy_id resolution mirrors `StrategyPositionStore._resolve_strategy` /
`PnLAggregator._resolve_strategy`: explicit payload ``strategy_id`` wins,
else the ``{strategy}:{symbol}:{ts}:{idx}`` prefix is parsed from
``client_order_id``. A fill that resolves to neither is dropped (logged)
— it cannot be attributed to a strategy.

The only I/O is reading the supplied WAL paths via the existing
`src.live.wal.replay`. No new dependencies; no engine touched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.live.pnl_aggregator import classify_venue
from src.live.wal import replay

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trade:
    """One reconstructed round-trip (or still-open) position.

    For ``status == "open"``: ``exit_ts`` / ``exit_price`` /
    ``realized_pnl`` / ``holding_seconds`` are all ``None``.

    ``realized_pnl`` currency is the venue's own (USDT for binance, KRW for
    kis) — deliberately NOT cross-converted.
    """

    strategy_id: str
    symbol: str
    venue: str
    side: str  # "long" | "short"
    qty: float
    entry_ts: str
    entry_price: float
    exit_ts: str | None
    exit_price: float | None
    realized_pnl: float | None
    holding_seconds: float | None
    status: str  # "closed" | "open"


def discover_wal_files(log_dir: Path | str) -> list[Path]:
    """Glob every run's ``*/wal.jsonl`` under *log_dir*, sorted deterministically.

    A missing directory yields an empty list (boot before any run wrote a
    WAL is normal). Ordering is a plain path sort so concatenating the
    replayed events across runs is reproducible.
    """
    root = Path(log_dir)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/wal.jsonl"))


def _resolve_strategy(payload: dict) -> str | None:
    """Same fallback as StrategyPositionStore / PnLAggregator.

    Explicit ``strategy_id`` wins; else parse the ``{strategy}:...`` prefix
    of ``client_order_id`` (requires a ``:`` separator).
    """
    explicit = payload.get("strategy_id")
    if explicit:
        return explicit
    coid = payload.get("client_order_id", "")
    if not coid:
        return None
    head, sep, _ = coid.partition(":")
    return head if sep else None


@dataclass
class _Leg:
    """Mutable per-(strategy, symbol) open leg accumulator.

    ``qty`` is signed: > 0 long, < 0 short, 0 flat. ``avg`` is the
    qty-weighted entry price of the currently-open side. ``entry_ts`` is the
    timestamp of the fill that opened the *current* side.
    """

    qty: Decimal
    avg: Decimal
    entry_ts: str


def _emit_closed(
    leg: _Leg,
    *,
    strategy_id: str,
    symbol: str,
    venue: str,
    close_qty: Decimal,
    exit_price: Decimal,
    exit_ts: str,
    fee: Decimal,
) -> Trade:
    is_long = leg.qty > 0
    side = "long" if is_long else "short"
    if is_long:
        pnl = (exit_price - leg.avg) * close_qty - fee
    else:
        pnl = (leg.avg - exit_price) * close_qty - fee
    return Trade(
        strategy_id=strategy_id,
        symbol=symbol,
        venue=venue,
        side=side,
        qty=float(close_qty),
        entry_ts=leg.entry_ts,
        entry_price=float(leg.avg),
        exit_ts=exit_ts,
        exit_price=float(exit_price),
        realized_pnl=float(pnl),
        holding_seconds=_holding_seconds(leg.entry_ts, exit_ts),
        status="closed",
    )


def _holding_seconds(entry_ts: str, exit_ts: str) -> float | None:
    try:
        a = datetime.fromisoformat(entry_ts)
        b = datetime.fromisoformat(exit_ts)
        return (b - a).total_seconds()
    except (ValueError, TypeError):
        # TypeError: tz-aware vs naive subtraction. WAL ts are consistently
        # UTC-offset ISO8601 so this is defensive on a read-only path.
        return None


def reconstruct_trades(wal_paths: list[Path] | list[str]) -> list[Trade]:
    """Replay every WAL path and reconstruct round-trip trades.

    Paths are replayed in the given order (caller supplies time order;
    `discover_wal_files` gives a deterministic sort). Within a path,
    `src.live.wal.replay` preserves file order. Fills are then applied
    per (strategy_id, symbol) with long/short + flip accounting identical
    to `pnl_aggregator._apply_to_cost_basis`.

    Returns trades ordered by ``entry_ts`` then ``symbol`` (deterministic).
    """
    legs: dict[tuple[str, str], _Leg] = {}
    trades: list[Trade] = []

    for path in wal_paths:
        events, _corruptions = replay(path)
        for event in events:
            if event.event_type != "order_filled":
                continue
            payload = dict(event.payload or {})
            symbol = payload.get("symbol")
            side = payload.get("side")
            raw_qty = payload.get("fill_qty") or payload.get("qty")
            raw_price = payload.get("fill_price") or payload.get("price")
            if not (symbol and side and raw_qty is not None and raw_price is not None):
                continue
            strategy_id = _resolve_strategy(payload)
            if not strategy_id:
                logger.warning(
                    "reconstruct_trades: cannot resolve strategy_id (coid=%r)",
                    payload.get("client_order_id"),
                )
                continue
            try:
                qty = Decimal(str(raw_qty))
                price = Decimal(str(raw_price))
                fee = Decimal(str(payload.get("fees") or payload.get("fee") or "0"))
            except Exception as err:  # noqa: BLE001 — defensive, never crash
                logger.warning("reconstruct_trades: bad numeric in payload: %s", err)
                continue
            ts = payload.get("ts") or payload.get("fill_ts") or event.ts
            venue = classify_venue(symbol)
            key = (strategy_id, symbol)
            is_buy = str(side).lower() == "buy"

            leg = legs.get(key)
            held = leg.qty if leg is not None else Decimal("0")

            if is_buy:
                if held < 0:
                    # Covering / flipping a SHORT (mirror of pnl_aggregator).
                    cover_qty = min(qty, -held)
                    if cover_qty > 0:
                        trades.append(_emit_closed(
                            leg, strategy_id=strategy_id, symbol=symbol,
                            venue=venue, close_qty=cover_qty,
                            exit_price=price, exit_ts=ts, fee=fee,
                        ))
                    new_held = held + qty
                    if new_held > 0:
                        # Flipped net long — residual basis = fill price.
                        legs[key] = _Leg(qty=new_held, avg=price, entry_ts=ts)
                    elif new_held == 0:
                        legs.pop(key, None)
                    else:
                        # Still short, smaller — keep original short avg/entry.
                        legs[key] = _Leg(qty=new_held, avg=leg.avg, entry_ts=leg.entry_ts)
                else:
                    # Opening / adding to a LONG (qty-weighted avg).
                    new_qty = held + qty
                    if leg is None or held == 0:
                        legs[key] = _Leg(qty=new_qty, avg=price, entry_ts=ts)
                    else:
                        avg = (held * leg.avg + qty * price) / new_qty if new_qty > 0 else leg.avg
                        legs[key] = _Leg(qty=new_qty, avg=avg, entry_ts=leg.entry_ts)
            else:  # sell
                if held <= 0:
                    # Opening / adding to a SHORT (mirror of pnl_aggregator;
                    # 0/0 guard so a zero-qty correction can't blow up).
                    new_qty = held - qty
                    if leg is None or held == 0:
                        if qty > 0:
                            legs[key] = _Leg(qty=new_qty, avg=price, entry_ts=ts)
                        # qty == 0 with no position → no-op (matches aggregator)
                    else:
                        if -new_qty > 0:
                            avg = ((-held) * leg.avg + qty * price) / (-new_qty)
                        else:
                            avg = leg.avg
                        legs[key] = _Leg(qty=new_qty, avg=avg, entry_ts=leg.entry_ts)
                else:
                    # Selling / flipping a LONG.
                    close_qty = min(qty, held)
                    if close_qty > 0:
                        trades.append(_emit_closed(
                            leg, strategy_id=strategy_id, symbol=symbol,
                            venue=venue, close_qty=close_qty,
                            exit_price=price, exit_ts=ts, fee=fee,
                        ))
                    new_held = held - qty
                    if new_held < 0:
                        # Flipped net short — residual basis = fill price.
                        legs[key] = _Leg(qty=new_held, avg=price, entry_ts=ts)
                    elif new_held == 0:
                        legs.pop(key, None)
                    else:
                        # Still long, smaller — keep original long avg/entry.
                        legs[key] = _Leg(qty=new_held, avg=leg.avg, entry_ts=leg.entry_ts)

    # Unclosed remainders → one open trade each.
    for (strategy_id, symbol), leg in legs.items():
        if leg.qty == 0:
            continue
        trades.append(Trade(
            strategy_id=strategy_id,
            symbol=symbol,
            venue=classify_venue(symbol),
            side="long" if leg.qty > 0 else "short",
            qty=float(abs(leg.qty)),
            entry_ts=leg.entry_ts,
            entry_price=float(leg.avg),
            exit_ts=None,
            exit_price=None,
            realized_pnl=None,
            holding_seconds=None,
            status="open",
        ))

    trades.sort(key=lambda t: (t.entry_ts, t.symbol))
    return trades
