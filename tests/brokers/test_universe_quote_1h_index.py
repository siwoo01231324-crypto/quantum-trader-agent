"""Regression — `_klines_to_dataframe.index.normalize()` collapsed 1h bars.

PR #336 (Dynamic Universe Phase 1) added per-strategy interval routing
(``get_interval()`` → ``"1h"`` for ``live-airborne-bb-reversal-kst-hours``).
But ``_klines_to_dataframe`` hard-coded ``pd.to_datetime(...).dt.normalize()``
on the index — daily-bar convention. When ``fetch_universe_klines(symbols,
interval="1h")`` ran, every hourly row got rewritten to that day's 00:00
UTC, so all 24 hourly bars per day collapsed onto a single index value.
``df["close"].iloc[-1]`` worked (still returned the last bar's close), but
any history-based indicator (BB, RSI, MA) that read more than the very
last row sees a stairstep of duplicate timestamps — strategies silently
returned hold.

2026-05-29 incident: ``python scripts/live_run.py`` ran for ~16 hours after
the PR #338 NameError fix, `qta-airborne-daemon` emitted dozens of valid
fires (KST 11/16 buckets in the {8,11,16,22} gate), yet WAL produced zero
order events.

These tests pin two things:
1. For "1h" / "15m" / non-1d intervals, distinct timestamps are PRESERVED —
   24 unique index values for 24 hourly bars on the same date.
2. For "1d" the old normalize behavior is BYTE-IDENTICAL (cs-tsmom 5y
   bench result must not shift).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.brokers.binance.universe_quote import _klines_to_dataframe


def _synthetic_klines(open_ms_iter):
    """Mimic Binance /klines payload — 12-tuple per row."""
    rows = []
    for ms in open_ms_iter:
        rows.append([
            ms,           # open_time
            "100.0",      # open
            "101.0",      # high
            "99.0",       # low
            "100.5",      # close
            "10.0",       # volume
            ms + 1000,    # close_time
            "1005.0",     # quote_volume
            42,           # trades
            "5.0",        # tb_base
            "503.0",      # tb_quote
            "0",          # _
        ])
    return rows


def test_1h_keeps_unique_index_per_bar():
    """24 hourly bars on the same date must produce 24 distinct index values."""
    # 2026-05-30T00:00:00 UTC = ms epoch
    base = pd.Timestamp("2026-05-30T00:00:00Z").value // 10**6
    rows = _synthetic_klines(base + h * 3_600_000 for h in range(24))
    df = _klines_to_dataframe(rows, interval="1h")
    assert len(df) == 24
    # All distinct — the regression was every row collapsing to one date.
    assert df.index.nunique() == 24, (
        f"1h bars must keep their hour granularity, got "
        f"{df.index.nunique()} distinct index values for 24 hourly rows. "
        f"Was `.dt.normalize()` re-introduced? That collapses 1h → daily and "
        f"made airborne strategy hold for 16h on 2026-05-29 (PR #336 회귀)."
    )


def test_15m_keeps_unique_index_per_bar():
    """96 fifteen-minute bars across a day stay distinct."""
    base = pd.Timestamp("2026-05-30T00:00:00Z").value // 10**6
    rows = _synthetic_klines(base + i * 15 * 60_000 for i in range(96))
    df = _klines_to_dataframe(rows, interval="15m")
    assert len(df) == 96
    assert df.index.nunique() == 96


def test_1d_preserves_normalize_byte_identical():
    """1d (default) keeps the legacy normalize() behavior — cs-tsmom 5y bench
    result must not shift under this fix.
    """
    # Three 1d rows at midnight UTC; normalize() is a no-op but exercise the path.
    base = pd.Timestamp("2026-05-28T00:00:00Z").value // 10**6
    rows = _synthetic_klines(base + d * 86_400_000 for d in range(3))
    df_explicit = _klines_to_dataframe(rows, interval="1d")
    df_default = _klines_to_dataframe(rows)  # default interval="1d"
    pd.testing.assert_frame_equal(df_explicit, df_default)
    # All three index values must be midnight UTC (already normalized).
    for ts in df_explicit.index:
        assert ts.hour == 0 and ts.minute == 0 and ts.second == 0


def test_1d_normalizes_intra_day_ms_offset():
    """1d path tolerates intra-day open_time (Binance has been known to return
    non-midnight open_time for some symbols) — normalize() floors to midnight.
    Behavior preserved from before the fix.
    """
    # 2026-05-28T05:00:00 UTC — non-midnight
    odd_base = pd.Timestamp("2026-05-28T05:00:00Z").value // 10**6
    rows = _synthetic_klines([odd_base])
    df = _klines_to_dataframe(rows, interval="1d")
    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2026-05-28T00:00:00")
