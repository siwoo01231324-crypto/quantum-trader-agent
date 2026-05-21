"""Airborne BB-reversal signal — pure functions for breakout / extreme / trigger.

Reverse-engineered from the external-lecture indicator "에어본(체험판)" (Pine
``PUB;0b920144158f4848ba5d506932a636d7``, v5). Methodology and validation:
``docs/background/38-airborne-indicator-reverse-engineering.md``. Used by
``backtest.strategies.live_airborne_bb_reversal``.

State machine:

    none ─→ long_setup  ─→ fire long  (on confirmed close >= trigger)
    none ─→ short_setup ─→ fire short (on confirmed close <= trigger)

Trigger formula (after a breakout):

    swing   = |extreme - base|
    trigger = extreme ± RETRACE_RATIO * swing    (- for short, + for long)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

RETRACE_RATIO = 0.4
Side = Literal["long", "short"]


@dataclass(frozen=True)
class AirborneSetup:
    """Snapshot of an active long-setup ready to be evaluated against the current bar.

    All fields refer to historical aggregates (base is the breakout bar's close,
    extreme is the running min/max since the breakout). The CURRENT bar is NOT
    included — callers fold it in themselves to evaluate firing.
    """

    breakout_index: int
    base: float
    extreme: float

    def trigger(self, side: Side, current_extreme: float | None = None) -> float:
        """Return the trigger price using ``current_extreme`` if provided.

        Pass the current bar's low (for long) or high (for short) as
        ``current_extreme`` to fold the live bar into extreme tracking. If
        omitted, the dataclass's frozen ``extreme`` is used (post-breakout
        through the most recent CONFIRMED bar).
        """
        ext = self.extreme if current_extreme is None else (
            min(self.extreme, current_extreme) if side == "long"
            else max(self.extreme, current_extreme)
        )
        if side == "long":
            return ext + RETRACE_RATIO * (self.base - ext)
        return ext - RETRACE_RATIO * (ext - self.base)


def find_active_long_setup(
    *,
    low: pd.Series,
    close: pd.Series,
    bb_lower: pd.Series,
    max_lookback: int,
) -> AirborneSetup | None:
    """Find the most recent unterminated long setup, scanning backwards.

    A long breakout bar at index ``i`` satisfies:
        ``low[i] <= bb_lower[i]``  AND  ``low[i-1] > bb_lower[i-1]``

    The setup terminates on the first confirmed bar ``j`` (``i < j <= N-2``)
    where ``close[j] >= trigger(extreme_through_j, base)``. The CURRENT bar
    (``i == N-1``) is excluded from termination — callers evaluate firing
    against it explicitly.

    Returns ``None`` if no active setup found within ``max_lookback`` bars,
    or if NaN BB values prevent evaluation.
    """
    n = len(close)
    if n < 3:
        return None

    # Scan backwards from second-to-last bar (current bar is N-1, can't be a
    # breakout because we need at least one subsequent bar to evaluate firing).
    start = n - 2
    end = max(n - max_lookback - 1, 0)  # exclusive lower bound
    for i in range(start, end, -1):
        if i - 1 < 0:
            continue
        bl_i = bb_lower.iloc[i]
        bl_i1 = bb_lower.iloc[i - 1]
        if pd.isna(bl_i) or pd.isna(bl_i1):
            continue
        l_i = float(low.iloc[i])
        l_i1 = float(low.iloc[i - 1])
        if not (l_i <= float(bl_i) and l_i1 > float(bl_i1)):
            continue

        base = float(close.iloc[i])
        extreme = l_i
        terminated = False
        for j in range(i + 1, n - 1):  # exclude current bar
            extreme = min(extreme, float(low.iloc[j]))
            trig_j = extreme + RETRACE_RATIO * (base - extreme)
            if float(close.iloc[j]) >= trig_j:
                terminated = True
                break

        if terminated:
            # Most recent breakout already fired/terminated. We don't reactivate
            # older breakouts (state machine is "none" after fire).
            return None

        return AirborneSetup(breakout_index=i, base=base, extreme=extreme)

    return None


def evaluate_long_fire(
    *,
    history: pd.DataFrame,
    bb_lower: pd.Series,
    max_lookback: int,
) -> tuple[bool, AirborneSetup | None, float]:
    """Evaluate whether the current bar's confirmed close fires a long signal.

    Returns ``(fires, setup, trigger_at_current)``:
        - ``fires``: True if there is an active setup AND
                     ``close[-1] >= trigger`` (using current low folded in).
        - ``setup``: The active setup (or None).
        - ``trigger_at_current``: The trigger price after folding the current
                                  bar's low into extreme tracking. NaN if no
                                  setup.
    """
    setup = find_active_long_setup(
        low=history["low"],
        close=history["close"],
        bb_lower=bb_lower,
        max_lookback=max_lookback,
    )
    if setup is None:
        return False, None, float("nan")

    current_low = float(history["low"].iloc[-1])
    trigger = setup.trigger("long", current_extreme=current_low)
    current_close = float(history["close"].iloc[-1])
    fires = current_close >= trigger
    return fires, setup, trigger


# =============================================================================
# v1.1 — close-based breakout + margin + body gates (long + short)
# =============================================================================
# Mirrors the Pine v1.1 source preserved at
# ``docs/specs/strategies/live-airborne-bb-reversal.pine`` (TV slot
# ``USER;d9f4857aaf05421ab3817870c8e99934`` "Airborne BB Reversal (RE v1) 1").
# Used by ``scripts/airborne_alert_daemon.py`` for live USDT-perp alerts and
# (after refactor) by ``backtest.strategies.live_airborne_bb_reversal_v11``.

DEFAULT_MIN_CLOSE_MARGIN_V11 = 0.001  # 0.1% — close vs BB threshold
DEFAULT_MIN_BODY_PCT_V11 = 0.005       # 0.5% — breakout bar body filter


def _body_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    return (close - open_).abs() / open_.where(open_ != 0, 1.0)


def _validate_v11_params(min_close_margin: float, min_body_pct: float) -> None:
    if min_close_margin < 0:
        raise ValueError(f"min_close_margin >= 0 required, got {min_close_margin}")
    if min_body_pct < 0:
        raise ValueError(f"min_body_pct >= 0 required, got {min_body_pct}")


def find_active_long_setup_v11(
    *,
    history: pd.DataFrame,
    bb_lower: pd.Series,
    max_lookback: int,
    min_close_margin: float = DEFAULT_MIN_CLOSE_MARGIN_V11,
    min_body_pct: float = DEFAULT_MIN_BODY_PCT_V11,
) -> AirborneSetup | None:
    """Find the most recent unterminated v1.1 long setup.

    A long breakout at index ``i`` requires (close-based, not high/low):
        close[i]   <  bb_lower[i]   * (1 - min_close_margin)
        close[i-1] >= bb_lower[i-1] * (1 - min_close_margin)
        |close[i] - open[i]| / open[i] >= min_body_pct

    Termination + return rules match ``find_active_long_setup``: scan backwards
    from N-2; setup terminates if any confirmed bar ``j`` (i < j <= N-2) has
    ``close[j] >= trigger(extreme_through_j, base)``; current bar (N-1) is
    excluded — caller evaluates firing against it via ``evaluate_long_fire_v11``.
    """
    _validate_v11_params(min_close_margin, min_body_pct)
    n = len(history)
    if n < 3:
        return None
    close = history["close"]
    open_ = history["open"]
    low = history["low"]
    lower_thr = bb_lower * (1 - min_close_margin)
    body_pct = _body_pct(open_, close)

    start = n - 2
    end = max(n - max_lookback - 1, 0)
    for i in range(start, end, -1):
        if i - 1 < 0:
            continue
        if pd.isna(lower_thr.iloc[i]) or pd.isna(lower_thr.iloc[i - 1]):
            continue
        if not (
            float(close.iloc[i]) < float(lower_thr.iloc[i])
            and float(close.iloc[i - 1]) >= float(lower_thr.iloc[i - 1])
            and float(body_pct.iloc[i]) >= min_body_pct
        ):
            continue

        base = float(close.iloc[i])
        extreme = float(low.iloc[i])
        terminated = False
        for j in range(i + 1, n - 1):
            extreme = min(extreme, float(low.iloc[j]))
            trig_j = extreme + RETRACE_RATIO * (base - extreme)
            if float(close.iloc[j]) >= trig_j:
                terminated = True
                break
        if terminated:
            return None
        return AirborneSetup(breakout_index=i, base=base, extreme=extreme)

    return None


def find_active_short_setup_v11(
    *,
    history: pd.DataFrame,
    bb_upper: pd.Series,
    max_lookback: int,
    min_close_margin: float = DEFAULT_MIN_CLOSE_MARGIN_V11,
    min_body_pct: float = DEFAULT_MIN_BODY_PCT_V11,
) -> AirborneSetup | None:
    """Find the most recent unterminated v1.1 short setup (mirror of long).

    A short breakout at index ``i`` requires:
        close[i]   >  bb_upper[i]   * (1 + min_close_margin)
        close[i-1] <= bb_upper[i-1] * (1 + min_close_margin)
        |close[i] - open[i]| / open[i] >= min_body_pct

    Extreme is tracked via running max(high). Setup terminates if any confirmed
    bar ``j`` has ``close[j] <= trigger`` (trigger = extreme - 0.4 * (extreme - base)).
    """
    _validate_v11_params(min_close_margin, min_body_pct)
    n = len(history)
    if n < 3:
        return None
    close = history["close"]
    open_ = history["open"]
    high = history["high"]
    upper_thr = bb_upper * (1 + min_close_margin)
    body_pct = _body_pct(open_, close)

    start = n - 2
    end = max(n - max_lookback - 1, 0)
    for i in range(start, end, -1):
        if i - 1 < 0:
            continue
        if pd.isna(upper_thr.iloc[i]) or pd.isna(upper_thr.iloc[i - 1]):
            continue
        if not (
            float(close.iloc[i]) > float(upper_thr.iloc[i])
            and float(close.iloc[i - 1]) <= float(upper_thr.iloc[i - 1])
            and float(body_pct.iloc[i]) >= min_body_pct
        ):
            continue

        base = float(close.iloc[i])
        extreme = float(high.iloc[i])
        terminated = False
        for j in range(i + 1, n - 1):
            extreme = max(extreme, float(high.iloc[j]))
            trig_j = extreme - RETRACE_RATIO * (extreme - base)
            if float(close.iloc[j]) <= trig_j:
                terminated = True
                break
        if terminated:
            return None
        return AirborneSetup(breakout_index=i, base=base, extreme=extreme)

    return None


def evaluate_long_fire_v11(
    *,
    history: pd.DataFrame,
    bb_lower: pd.Series,
    max_lookback: int,
    min_close_margin: float = DEFAULT_MIN_CLOSE_MARGIN_V11,
    min_body_pct: float = DEFAULT_MIN_BODY_PCT_V11,
) -> tuple[bool, AirborneSetup | None, float]:
    """Evaluate whether the current bar's confirmed close fires a v1.1 long signal.

    Returns ``(fires, setup, trigger_at_current)``. Trigger folds the current
    bar's low into the running extreme (same semantics as v1).
    """
    setup = find_active_long_setup_v11(
        history=history,
        bb_lower=bb_lower,
        max_lookback=max_lookback,
        min_close_margin=min_close_margin,
        min_body_pct=min_body_pct,
    )
    if setup is None:
        return False, None, float("nan")
    current_low = float(history["low"].iloc[-1])
    trigger = setup.trigger("long", current_extreme=current_low)
    current_close = float(history["close"].iloc[-1])
    fires = current_close >= trigger
    return fires, setup, trigger


def evaluate_short_fire_v11(
    *,
    history: pd.DataFrame,
    bb_upper: pd.Series,
    max_lookback: int,
    min_close_margin: float = DEFAULT_MIN_CLOSE_MARGIN_V11,
    min_body_pct: float = DEFAULT_MIN_BODY_PCT_V11,
) -> tuple[bool, AirborneSetup | None, float]:
    """Evaluate whether the current bar's confirmed close fires a v1.1 short signal.

    Mirror of ``evaluate_long_fire_v11``: folds current bar's high into extreme,
    fires when ``close <= trigger``.
    """
    setup = find_active_short_setup_v11(
        history=history,
        bb_upper=bb_upper,
        max_lookback=max_lookback,
        min_close_margin=min_close_margin,
        min_body_pct=min_body_pct,
    )
    if setup is None:
        return False, None, float("nan")
    current_high = float(history["high"].iloc[-1])
    trigger = setup.trigger("short", current_extreme=current_high)
    current_close = float(history["close"].iloc[-1])
    fires = current_close <= trigger
    return fires, setup, trigger


__all__ = [
    "RETRACE_RATIO",
    "DEFAULT_MIN_CLOSE_MARGIN_V11",
    "DEFAULT_MIN_BODY_PCT_V11",
    "AirborneSetup",
    "find_active_long_setup",
    "evaluate_long_fire",
    "find_active_long_setup_v11",
    "find_active_short_setup_v11",
    "evaluate_long_fire_v11",
    "evaluate_short_fire_v11",
]
