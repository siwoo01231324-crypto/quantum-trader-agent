"""Cross-run trade-history reconstruction (round-trip replay).

`src.live.trade_history` replays `order_filled` WAL events and pairs fills
per (strategy_id, symbol) into round-trip trades using the SAME long/short
+ flip accounting as `pnl_aggregator._apply_to_cost_basis`:

  - entry opens / increases the position (qty-weighted avg entry price)
  - an opposite-side fill reduces / closes the position
  - a flip closes the existing side then opens the opposite side
  - unclosed remainder → one status="open" trade (entry only, no exit/pnl)

strategy_id resolution mirrors StrategyPositionStore / PnLAggregator:
payload `strategy_id` else the `{strategy}:{symbol}:{ts}:{idx}` prefix
parsed from `client_order_id`.

Pure & deterministic — the only I/O is reading the supplied WAL paths via
the existing `src.live.wal.replay`. Trades are ordered by entry_ts then
symbol.
"""
from __future__ import annotations

from pathlib import Path

from src.live.trade_history import (
    discover_wal_files,
    reconstruct_trades,
)
from src.live.types import WALEvent
from src.live.wal import WAL


def _fill(
    *,
    coid: str,
    symbol: str,
    side: str,
    qty: str,
    price: str,
    ts: str,
    fees: str = "0",
    strategy_id: str | None = None,
) -> WALEvent:
    payload = {
        "client_order_id": coid,
        "symbol": symbol,
        "side": side,
        "fill_qty": qty,
        "fill_price": price,
        "fees": fees,
    }
    if strategy_id is not None:
        payload["strategy_id"] = strategy_id
    return WALEvent(ts=ts, event_type="order_filled", payload=payload)


def _write_wal(path: Path, events: list[WALEvent]) -> Path:
    wal = WAL(path)
    for ev in events:
        wal.write(ev)
    # WAL creates the file lazily on first write; an empty WAL still needs
    # the file present for discover_wal_files to glob it (matches a real
    # run that wrote a startup heartbeat then no fills).
    if not events:
        path.touch()
    return path


# ---------------------------------------------------------------------------
# discover_wal_files
# ---------------------------------------------------------------------------

def test_discover_wal_files_globs_each_run_sorted(tmp_path: Path):
    (tmp_path / "run-b").mkdir()
    (tmp_path / "run-a").mkdir()
    (tmp_path / "run-c").mkdir()
    pb = _write_wal(tmp_path / "run-b" / "wal.jsonl", [])
    pa = _write_wal(tmp_path / "run-a" / "wal.jsonl", [])
    pc = _write_wal(tmp_path / "run-c" / "wal.jsonl", [])
    found = discover_wal_files(tmp_path)
    assert found == sorted([pa, pb, pc])  # deterministic order


def test_discover_wal_files_empty_dir_returns_empty(tmp_path: Path):
    assert discover_wal_files(tmp_path) == []


def test_discover_wal_files_missing_dir_returns_empty(tmp_path: Path):
    assert discover_wal_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# reconstruct_trades — basic round trips
# ---------------------------------------------------------------------------

def test_simple_long_round_trip(tmp_path: Path):
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="alpha:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="alpha:BTCUSDT:2:1", symbol="BTCUSDT", side="sell",
              qty="1", price="110", ts="2026-05-06T02:00:00+00:00",
              fees="1"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    t = trades[0]
    assert t.strategy_id == "alpha"
    assert t.symbol == "BTCUSDT"
    assert t.venue == "binance"
    assert t.side == "long"
    assert t.qty == 1.0
    assert t.entry_ts == "2026-05-06T01:00:00+00:00"
    assert t.entry_price == 100.0
    assert t.exit_ts == "2026-05-06T02:00:00+00:00"
    assert t.exit_price == 110.0
    # realized = (110-100)*1 - 1 fee = 9 (currency = venue's, no convert)
    assert t.realized_pnl == 9.0
    assert t.holding_seconds == 3600.0
    assert t.status == "closed"


def test_partial_fills_then_close(tmp_path: Path):
    """Two partial entry fills, one closing fill."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="alpha:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="alpha:BTCUSDT:2:1", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:30:00+00:00"),
        _fill(coid="alpha:BTCUSDT:3:2", symbol="BTCUSDT", side="sell",
              qty="2", price="120", ts="2026-05-06T03:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    t = trades[0]
    assert t.qty == 2.0
    assert t.entry_price == 100.0
    assert t.exit_price == 120.0
    assert t.realized_pnl == 40.0  # (120-100)*2
    assert t.status == "closed"


def test_multiple_entries_averaged_then_one_exit(tmp_path: Path):
    """Entries at different prices → qty-weighted avg entry."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="alpha:ETHUSDT:1:0", symbol="ETHUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="alpha:ETHUSDT:2:1", symbol="ETHUSDT", side="buy",
              qty="3", price="300", ts="2026-05-06T01:30:00+00:00"),
        _fill(coid="alpha:ETHUSDT:3:2", symbol="ETHUSDT", side="sell",
              qty="4", price="400", ts="2026-05-06T02:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    t = trades[0]
    # avg entry = (1*100 + 3*300) / 4 = 250
    assert t.entry_price == 250.0
    assert t.qty == 4.0
    assert t.realized_pnl == 600.0  # (400-250)*4
    assert t.side == "long"


def test_short_round_trip(tmp_path: Path):
    """Open short (sell), cover (buy) at a lower price → profit."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="momo:BTCUSDT:1:0", symbol="BTCUSDT", side="sell",
              qty="1", price="60000", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="momo:BTCUSDT:2:1", symbol="BTCUSDT", side="buy",
              qty="1", price="57000", ts="2026-05-06T02:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "short"
    assert t.entry_price == 60000.0
    assert t.exit_price == 57000.0
    assert t.realized_pnl == 3000.0  # (entry - cover) * qty
    assert t.status == "closed"


# ---------------------------------------------------------------------------
# flips
# ---------------------------------------------------------------------------

def test_long_to_short_flip(tmp_path: Path):
    """Long 1 @ 100, sell 3 @ 110 → close long (+10), open short 2 @ 110."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="momo:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="momo:BTCUSDT:2:1", symbol="BTCUSDT", side="sell",
              qty="3", price="110", ts="2026-05-06T02:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    # One closed long + one still-open short remainder
    assert len(trades) == 2
    closed, opened = trades
    assert closed.side == "long"
    assert closed.qty == 1.0
    assert closed.realized_pnl == 10.0
    assert closed.status == "closed"
    assert opened.side == "short"
    assert opened.qty == 2.0
    assert opened.entry_price == 110.0
    assert opened.status == "open"
    assert opened.exit_ts is None
    assert opened.realized_pnl is None


def test_short_to_long_flip(tmp_path: Path):
    """Short 1 @ 100, buy 3 @ 90 → cover short (+10), open long 2 @ 90."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="momo:BTCUSDT:1:0", symbol="BTCUSDT", side="sell",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="momo:BTCUSDT:2:1", symbol="BTCUSDT", side="buy",
              qty="3", price="90", ts="2026-05-06T02:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 2
    closed, opened = trades
    assert closed.side == "short"
    assert closed.qty == 1.0
    assert closed.realized_pnl == 10.0  # (100-90)*1
    assert closed.status == "closed"
    assert opened.side == "long"
    assert opened.qty == 2.0
    assert opened.entry_price == 90.0
    assert opened.status == "open"


# ---------------------------------------------------------------------------
# open / edge cases
# ---------------------------------------------------------------------------

def test_still_open_position_emits_open_trade(tmp_path: Path):
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="alpha:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="2", price="100", ts="2026-05-06T01:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    t = trades[0]
    assert t.status == "open"
    assert t.side == "long"
    assert t.qty == 2.0
    assert t.entry_price == 100.0
    assert t.exit_ts is None
    assert t.exit_price is None
    assert t.realized_pnl is None
    assert t.holding_seconds is None


def test_empty_wal_returns_no_trades(tmp_path: Path):
    p = _write_wal(tmp_path / "wal.jsonl", [])
    assert reconstruct_trades([p]) == []


def test_no_wal_paths_returns_no_trades():
    assert reconstruct_trades([]) == []


def test_missing_strategy_id_falls_back_to_coid_prefix(tmp_path: Path):
    """Payload has no strategy_id → parse `{strategy}:...` from coid."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="gamma:000660:1:0", symbol="000660", side="buy",
              qty="10", price="100000", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="gamma:000660:2:1", symbol="000660", side="sell",
              qty="10", price="101000", ts="2026-05-06T02:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    t = trades[0]
    assert t.strategy_id == "gamma"
    assert t.venue == "kis"
    assert t.realized_pnl == 10000.0  # (101000-100000)*10 KRW, no convert


def test_explicit_strategy_id_takes_precedence(tmp_path: Path):
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="WRONG:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00",
              strategy_id="right"),
        _fill(coid="WRONG:BTCUSDT:2:1", symbol="BTCUSDT", side="sell",
              qty="1", price="100", ts="2026-05-06T02:00:00+00:00",
              strategy_id="right"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 1
    assert trades[0].strategy_id == "right"


def test_unresolvable_strategy_id_is_skipped(tmp_path: Path):
    """No strategy_id and no `:` in coid → fill cannot be attributed."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="nocolon", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
    ])
    assert reconstruct_trades([p]) == []


def test_multiple_wal_files_concatenated_in_time_order(tmp_path: Path):
    """Entry in run-1's WAL, exit in run-2's WAL → one closed trade."""
    (tmp_path / "run-1").mkdir()
    (tmp_path / "run-2").mkdir()
    p1 = _write_wal(tmp_path / "run-1" / "wal.jsonl", [
        _fill(coid="alpha:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
    ])
    p2 = _write_wal(tmp_path / "run-2" / "wal.jsonl", [
        _fill(coid="alpha:BTCUSDT:2:1", symbol="BTCUSDT", side="sell",
              qty="1", price="125", ts="2026-05-07T01:00:00+00:00"),
    ])
    trades = reconstruct_trades([p1, p2])
    assert len(trades) == 1
    t = trades[0]
    assert t.status == "closed"
    assert t.realized_pnl == 25.0
    assert t.entry_ts == "2026-05-06T01:00:00+00:00"
    assert t.exit_ts == "2026-05-07T01:00:00+00:00"
    assert t.holding_seconds == 86400.0


def test_deterministic_ordering_by_entry_ts_then_symbol(tmp_path: Path):
    """Trades ordered by entry_ts, ties broken by symbol."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        # Same entry_ts, different symbols → symbol tiebreak (ETH before SOL)
        _fill(coid="a:SOLUSDT:1:0", symbol="SOLUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T05:00:00+00:00"),
        _fill(coid="a:ETHUSDT:1:1", symbol="ETHUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T05:00:00+00:00"),
        # Earlier entry_ts → must sort first overall
        _fill(coid="a:BTCUSDT:1:2", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert [t.symbol for t in trades] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_two_symbols_same_strategy_isolated(tmp_path: Path):
    """Fills on different symbols don't net against each other."""
    p = _write_wal(tmp_path / "wal.jsonl", [
        _fill(coid="a:BTCUSDT:1:0", symbol="BTCUSDT", side="buy",
              qty="1", price="100", ts="2026-05-06T01:00:00+00:00"),
        _fill(coid="a:ETHUSDT:2:1", symbol="ETHUSDT", side="buy",
              qty="1", price="200", ts="2026-05-06T01:30:00+00:00"),
        _fill(coid="a:BTCUSDT:3:2", symbol="BTCUSDT", side="sell",
              qty="1", price="110", ts="2026-05-06T02:00:00+00:00"),
    ])
    trades = reconstruct_trades([p])
    assert len(trades) == 2
    btc = next(t for t in trades if t.symbol == "BTCUSDT")
    eth = next(t for t in trades if t.symbol == "ETHUSDT")
    assert btc.status == "closed"
    assert btc.realized_pnl == 10.0
    assert eth.status == "open"
