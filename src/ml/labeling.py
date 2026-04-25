"""Triple-barrier labeling — López de Prado AFML Ch.3."""
from __future__ import annotations

import pandas as pd
import numpy as np


def triple_barrier_label(
    prices: pd.Series,
    events: pd.DataFrame,
    tp: "pd.Series | float",
    sl: "pd.Series | float",
    costs_bps: float = 0.0,
) -> pd.DataFrame:
    """Apply triple-barrier labeling to a set of entry events.

    Parameters
    ----------
    prices:
        Close price series indexed by datetime.
    events:
        DataFrame indexed by entry timestamp with columns:
          - side: int {+1, -1}
          - t1: datetime (vertical barrier / max holding time)
    tp:
        Take-profit barrier width. If Series, index must align with events index.
        Expressed as a fraction of entry price (e.g. 0.02 = 2%).
    sl:
        Stop-loss barrier width (positive value, applied symmetrically on loss side).
    costs_bps:
        Round-trip transaction cost in basis points subtracted from net return
        before labeling.

    Returns
    -------
    DataFrame with columns:
      - label: int {0, 1}  — 1 if cost-adjusted return > 0 AND exit via tp or t1
      - ret: float         — cost-adjusted net return
      - barrier: str       — 'tp', 'sl', or 't1'
      - t_touch: datetime  — first barrier touch timestamp (strictly > entry_ts)
    """
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("prices must have a DatetimeIndex")

    # Validate coverage
    min_entry = events.index.min()
    max_t1 = events["t1"].max()
    if prices.index.min() > min_entry or prices.index.max() < max_t1:
        raise ValueError(
            "prices does not cover the full event range "
            f"[{min_entry}, {max_t1}]"
        )

    costs_frac = costs_bps / 1e4

    # Normalise barriers to Series
    if isinstance(tp, (int, float)):
        tp_s = pd.Series(float(tp), index=events.index)
    else:
        tp_s = tp.reindex(events.index)

    if isinstance(sl, (int, float)):
        sl_s = pd.Series(float(sl), index=events.index)
    else:
        sl_s = sl.reindex(events.index)

    records: list[dict] = []

    for entry_ts, row in events.iterrows():
        side: int = int(row["side"])
        t1: pd.Timestamp = row["t1"]
        tp_val: float = float(tp_s.loc[entry_ts])
        sl_val: float = float(sl_s.loc[entry_ts])

        entry_price = prices.loc[entry_ts]

        # Price path strictly after entry (lookahead guard)
        path = prices.loc[entry_ts:t1]
        path = path.iloc[1:]  # drop the bar at entry_ts itself

        if path.empty:
            # No price data after entry up to t1 → t1 barrier, zero ret
            records.append(
                {
                    "label": 0,
                    "ret": -costs_frac,
                    "barrier": "t1",
                    "t_touch": t1,
                }
            )
            continue

        # Compute returns relative to entry price (in direction of trade)
        ret_series = side * (path - entry_price) / entry_price

        # Barrier levels
        tp_hit = ret_series[ret_series >= tp_val]
        sl_hit = ret_series[ret_series <= -sl_val]

        first_tp = tp_hit.index[0] if not tp_hit.empty else pd.NaT
        first_sl = sl_hit.index[0] if not sl_hit.empty else pd.NaT

        # Determine which barrier fires first
        candidates: list[tuple[pd.Timestamp, str]] = []
        if first_tp is not pd.NaT:
            candidates.append((first_tp, "tp"))
        if first_sl is not pd.NaT:
            candidates.append((first_sl, "sl"))
        candidates.append((t1, "t1"))

        t_touch, barrier = min(candidates, key=lambda x: x[0])

        net_ret = float(ret_series.loc[t_touch] if t_touch != t1 else ret_series.iloc[-1])
        net_ret -= costs_frac

        label = 1 if (net_ret > 0 and barrier in ("tp", "t1")) else 0

        records.append(
            {
                "label": label,
                "ret": net_ret,
                "barrier": barrier,
                "t_touch": t_touch,
            }
        )

    result = pd.DataFrame(records, index=events.index)
    result["label"] = result["label"].astype(int)
    return result
