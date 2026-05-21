"""Unit tests for v1.1 close-based long+short helpers in signals.airborne_bb_reversal.

Tests are hermetic — they construct OHLC + bb_lower / bb_upper Series directly
so behavior is verified against the documented Pine v1.1 source rather than
against a particular Bollinger implementation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals.airborne_bb_reversal import (
    AirborneSetup,
    DEFAULT_MIN_BODY_PCT_V11,
    DEFAULT_MIN_CLOSE_MARGIN_V11,
    RETRACE_RATIO,
    evaluate_long_fire_v11,
    evaluate_short_fire_v11,
    find_active_long_setup_v11,
    find_active_short_setup_v11,
)


def _frame(opens, highs, lows, closes) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 1_000.0)},
        index=idx,
    )


def _bb_series(value: float, n: int) -> pd.Series:
    return pd.Series([value] * n, index=pd.date_range("2026-01-01", periods=n, freq="1h"))


# =============================================================================
# Long side
# =============================================================================

def test_long_fire_basic():
    """Clean breakout at bar 2, retrace, then current bar close >= trigger → FIRE.

    bb_lower=99, default margin 0.001 → lower_thr=98.901
        bar 0,1: close=100 (above thr)
        bar 2  : close=98 (breaks below thr, body 2% ≥ 0.5%) → base=98, ext=95
        bar 3  : low=90, close=92 → ext=90, trig=90+0.4*(98-90)=93.2 → 92<93.2 (no term)
        bar 4  : low=88, close=95 → folded ext=88, trig=88+0.4*(98-88)=92.0 → 95>=92 FIRE
    """
    opens  = [100, 100, 100, 92, 91]
    highs  = [100.5, 100.5, 100.5, 92.5, 95.5]
    lows   = [99.5, 99.5, 95, 90, 88]
    closes = [100, 100, 98, 92, 95]
    history = _frame(opens, highs, lows, closes)
    bb_lower = _bb_series(99.0, len(closes))

    fires, setup, trig = evaluate_long_fire_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert fires is True
    assert setup is not None
    assert setup.breakout_index == 2
    assert setup.base == pytest.approx(98.0)
    assert setup.extreme == pytest.approx(90.0)  # ext through bar j (excl. current)
    assert trig == pytest.approx(92.0)


def test_long_pending_close_below_trigger():
    """Same shape as basic, but current close stays below trigger → no fire."""
    opens  = [100, 100, 100, 92, 91]
    highs  = [100.5, 100.5, 100.5, 92.5, 91.5]
    lows   = [99.5, 99.5, 95, 90, 88]
    closes = [100, 100, 98, 92, 91]  # 91 < trigger 92.0
    history = _frame(opens, highs, lows, closes)
    bb_lower = _bb_series(99.0, len(closes))

    fires, setup, trig = evaluate_long_fire_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert fires is False
    assert setup is not None
    assert trig == pytest.approx(92.0)


def test_long_no_breakout_returns_none():
    """close never below threshold → no setup → no fire."""
    closes = [100.0] * 5
    history = _frame(closes, [c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
    bb_lower = _bb_series(99.0, 5)

    fires, setup, trig = evaluate_long_fire_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert fires is False
    assert setup is None
    assert pd.isna(trig)


def test_long_body_filter_rejects_small_body():
    """Breakout close < threshold + prev close >= threshold, but body < 0.5% → reject."""
    # Body on breakout bar = |close-open|/open = |98.0-98.4|/98.4 ≈ 0.4% < 0.5%
    opens  = [100, 100, 98.4, 92, 91]
    highs  = [100.5, 100.5, 98.5, 92.5, 95.5]
    lows   = [99.5, 99.5, 95, 90, 88]
    closes = [100, 100, 98.0, 92, 95]
    history = _frame(opens, highs, lows, closes)
    bb_lower = _bb_series(99.0, len(closes))

    setup = find_active_long_setup_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert setup is None


def test_long_margin_filter_rejects_micro_breakout():
    """close just barely below bb_lower but not below the margin-adjusted threshold."""
    # margin=0.01 → lower_thr = 99 * 0.99 = 98.01. close=98.5 > 98.01 → no breakout.
    opens  = [100, 100, 99.5, 92, 91]
    highs  = [100.5, 100.5, 99.5, 92.5, 95.5]
    lows   = [99.5, 99.5, 95, 90, 88]
    closes = [100, 100, 98.5, 92, 95]
    history = _frame(opens, highs, lows, closes)
    bb_lower = _bb_series(99.0, len(closes))

    setup = find_active_long_setup_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
        min_close_margin=0.01,
    )
    assert setup is None


def test_long_setup_terminated_returns_none():
    """Breakout at bar 2 terminates at bar 3 (close>=trigger before current bar)."""
    # bar 2 breakout: base=98, ext=95. bar 3: low=90 → ext=90, trig=93.2.
    # If close[3]=94 >= 93.2 → TERMINATED.
    opens  = [100, 100, 100, 92, 95]
    highs  = [100.5, 100.5, 100.5, 94.5, 96]
    lows   = [99.5, 99.5, 95, 90, 93]
    closes = [100, 100, 98, 94, 95]  # bar 3 close 94 >= 93.2 → fires/terminates
    history = _frame(opens, highs, lows, closes)
    bb_lower = _bb_series(99.0, len(closes))

    setup = find_active_long_setup_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert setup is None


def test_long_warmup_short_history_returns_none():
    history = _frame([100, 100], [100.5, 100.5], [99.5, 99.5], [100, 99])
    bb_lower = _bb_series(99.0, 2)

    setup = find_active_long_setup_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert setup is None


def test_long_nan_bb_lower_skipped():
    """NaN bb_lower values must be skipped, not crash."""
    opens  = [100, 100, 100, 92, 91]
    highs  = [100.5, 100.5, 100.5, 92.5, 95.5]
    lows   = [99.5, 99.5, 95, 90, 88]
    closes = [100, 100, 98, 92, 95]
    history = _frame(opens, highs, lows, closes)
    bb_lower = pd.Series(
        [np.nan, np.nan, 99.0, 99.0, 99.0],
        index=history.index,
    )

    # bar 2 needs lower_thr[1] (NaN) → skip. No fallback breakout candidate.
    fires, setup, trig = evaluate_long_fire_v11(
        history=history, bb_lower=bb_lower, max_lookback=10,
    )
    assert fires is False
    assert setup is None


# =============================================================================
# Short side (mirror)
# =============================================================================

def test_short_fire_basic():
    """Mirror of long_fire_basic.

    bb_upper=101, default margin 0.001 → upper_thr=101.101
        bar 0,1: close=100 (below thr)
        bar 2  : close=103 (breaks above thr, body 3% ≥ 0.5%) → base=103, ext=105
        bar 3  : high=110, close=108 → ext=110, trig=110-0.4*(110-103)=110-2.8=107.2
                                       → 108 > 107.2 → no termination
        bar 4  : high=110, close=104 → folded ext=110, trig=107.2 → 104<=107.2 FIRE
    """
    opens  = [100, 100, 100, 108, 109]
    highs  = [100.5, 100.5, 105, 110, 110]
    lows   = [99.5, 99.5, 99.5, 107, 103.5]
    closes = [100, 100, 103, 108, 104]
    history = _frame(opens, highs, lows, closes)
    bb_upper = _bb_series(101.0, len(closes))

    fires, setup, trig = evaluate_short_fire_v11(
        history=history, bb_upper=bb_upper, max_lookback=10,
    )
    assert fires is True
    assert setup is not None
    assert setup.breakout_index == 2
    assert setup.base == pytest.approx(103.0)
    assert setup.extreme == pytest.approx(110.0)
    assert trig == pytest.approx(107.2)


def test_short_pending_close_above_trigger():
    """Breakout exists, but current close stays above trigger → pending."""
    opens  = [100, 100, 100, 108, 108]
    highs  = [100.5, 100.5, 105, 110, 110]
    lows   = [99.5, 99.5, 99.5, 107, 107.5]
    closes = [100, 100, 103, 108, 108]  # 108 > 107.2 → no fire
    history = _frame(opens, highs, lows, closes)
    bb_upper = _bb_series(101.0, len(closes))

    fires, setup, trig = evaluate_short_fire_v11(
        history=history, bb_upper=bb_upper, max_lookback=10,
    )
    assert fires is False
    assert setup is not None
    assert trig == pytest.approx(107.2)


def test_short_no_breakout_returns_none():
    closes = [100.0] * 5
    history = _frame(closes, [c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
    bb_upper = _bb_series(101.0, 5)

    fires, setup, trig = evaluate_short_fire_v11(
        history=history, bb_upper=bb_upper, max_lookback=10,
    )
    assert fires is False
    assert setup is None
    assert pd.isna(trig)


def test_short_body_filter_rejects_small_body():
    """Body too small on breakout bar."""
    # body[2] = |103-102.7|/102.7 ≈ 0.29% < 0.5%
    opens  = [100, 100, 102.7, 108, 109]
    highs  = [100.5, 100.5, 105, 110, 110]
    lows   = [99.5, 99.5, 102, 107, 103.5]
    closes = [100, 100, 103, 108, 104]
    history = _frame(opens, highs, lows, closes)
    bb_upper = _bb_series(101.0, len(closes))

    setup = find_active_short_setup_v11(
        history=history, bb_upper=bb_upper, max_lookback=10,
    )
    assert setup is None


def test_short_setup_terminated_returns_none():
    """Breakout at bar 2, terminates at bar 3 (close <= trigger)."""
    # bar 2: base=103, ext=105. bar 3: high=110 → ext=110, trig=110-0.4*(110-103)=107.2
    # close[3]=107 <= 107.2 → terminated
    opens  = [100, 100, 100, 108, 107.5]
    highs  = [100.5, 100.5, 105, 110, 108]
    lows   = [99.5, 99.5, 99.5, 106.5, 106]
    closes = [100, 100, 103, 107, 107.5]
    history = _frame(opens, highs, lows, closes)
    bb_upper = _bb_series(101.0, len(closes))

    setup = find_active_short_setup_v11(
        history=history, bb_upper=bb_upper, max_lookback=10,
    )
    assert setup is None


# =============================================================================
# Param validation + edge cases
# =============================================================================

def test_invalid_min_close_margin_raises():
    history = _frame([100] * 5, [100] * 5, [99] * 5, [100] * 5)
    bb = _bb_series(99.0, 5)
    with pytest.raises(ValueError, match="min_close_margin"):
        find_active_long_setup_v11(
            history=history, bb_lower=bb, max_lookback=10, min_close_margin=-0.001,
        )


def test_invalid_min_body_pct_raises():
    history = _frame([100] * 5, [100] * 5, [99] * 5, [100] * 5)
    bb = _bb_series(99.0, 5)
    with pytest.raises(ValueError, match="min_body_pct"):
        find_active_short_setup_v11(
            history=history, bb_upper=bb, max_lookback=10, min_body_pct=-0.001,
        )


def test_defaults_match_pine_source():
    """Pine v1.1 source pins min_close_margin=0.001 and min_body_pct=0.005."""
    assert DEFAULT_MIN_CLOSE_MARGIN_V11 == 0.001
    assert DEFAULT_MIN_BODY_PCT_V11 == 0.005
    assert RETRACE_RATIO == 0.4
