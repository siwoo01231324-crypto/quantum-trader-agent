"""Entry router for Iranyi D0~D9 variant matrix (issue #185).

VARIANT_REGISTRY is frozen at sha256 8405cf460d0adf1ff4199eed84f679e59a3773849322d4221247dad51012bd8a.
Any post-hoc change requires a new issue.
"""
from __future__ import annotations

import hashlib
import json
import logging
import warnings
from typing import Any

import pandas as pd

# W1 features (always available)
from src.features.ma_alignment import ma_aligned_pre_cross
from src.features.forward_ma_projection import ma_projection_meeting_point
from src.features.ma_magnet import return_to_ma_signal
from src.features.price_ma_zscore import price_ma_zscore
from src.features.vwma import vwma_cross
from src.features.ma_projection import ema_slope
from src.features.time_of_day import time_gate
from src.features.multi_tf import multi_tf_alignment

# W2 features — conditionally imported; stub warnings emitted if absent
try:
    from src.features.volume_burst import volume_zscore as _volume_zscore

    _HAS_VOLUME_BURST = True
except ImportError:
    warnings.warn(
        "src.features.volume_burst not found (W2 pending) — volume_burst rule stubbed to False",
        stacklevel=1,
    )
    _HAS_VOLUME_BURST = False

try:
    from src.features.turning_point import is_turning_point as _is_turning_point

    _HAS_TURNING_POINT = True
except ImportError:
    warnings.warn(
        "src.features.turning_point not found (W2 pending) — turning_point rule stubbed to False",
        stacklevel=1,
    )
    _HAS_TURNING_POINT = False

try:
    from src.features.vpvr_poc import volume_profile_support_zones as _vpvr_poc

    _HAS_VPVR_POC = True
except ImportError:
    warnings.warn(
        "src.features.vpvr_poc not found (W2 pending) — vpvr_poc rule stubbed to False",
        stacklevel=1,
    )
    _HAS_VPVR_POC = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FROZEN variant registry — do NOT edit post-merge (sha256 guarded in tests)
# ---------------------------------------------------------------------------
VARIANT_REGISTRY: dict[str, dict[str, Any]] = {
    "D0": {"tf": "4h", "rules": ["vwma_cross", "ema_slope_gt_0", "atr_stop_2x_atr14", "take_7pct"]},
    "D1": {
        "tf": "4h",
        "rules": [
            "vwma_cross",
            "ema_slope_gt_0",
            "regime_r4_bull",
            "donchian_20",
            "time_gate",
            "atr_stop_2x_atr14",
            "take_7pct",
        ],
    },
    "D2": {"tf": "4h", "rules": ["D1", "ma_alignment_50_100", "forward_ma_projection"]},
    "D3": {"tf": "4h", "rules": ["D1", "price_ma_zscore", "ma200_magnet"]},
    "D4": {"tf": "4h", "rules": ["D1", "vpvr_poc_support", "volume_burst"]},
    "D5": {"tf": "4h", "rules": ["D2_rules", "D3_rules", "D4_rules"]},
    "D6": {
        "tf": "5m",
        "rules": ["D1_rules", "multi_tf_gate_1h", "multi_tf_gate_1d"],
        "take_pct": 0.05,
    },
    "D7": {
        "tf": "5m",
        "rules": ["D6_rules", "ubai_relative_strength_top_quartile"],
        "take_pct": 0.05,
        "universe": "top10_alt",
    },
    "D8": {
        "tf": "5m",
        "rules": ["D7_rules", "turning_point_only"],
        "take_pct": 0.05,
        "universe": "top10_alt",
    },
    "D9": {
        "tf": "5m",
        "rules": ["D8_rules", "metalabeler_winprob_ge_0_6"],
        "take_pct": 0.05,
        "universe": "top10_alt",
    },
}

_EXPECTED_SHA256 = "8405cf460d0adf1ff4199eed84f679e59a3773849322d4221247dad51012bd8a"


def variant_registry_sha256() -> str:
    """Return SHA-256 hex digest of the canonical JSON of VARIANT_REGISTRY."""
    canonical = json.dumps(VARIANT_REGISTRY, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Internal rule evaluators
# ---------------------------------------------------------------------------

def _eval_vwma_cross(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    volume = history["volume"]
    sig = vwma_cross(close, volume, window=min(100, len(close)))
    return sig.iloc[-1] == "golden"


def _eval_ema_slope_gt_0(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    s = ema_slope(close, span=min(100, len(close) // 2 or 1), slope_window=min(5, len(close)))
    val = s.iloc[-1]
    return bool(not pd.isna(val) and val > 0)


def _eval_time_gate(bar: dict, history: pd.DataFrame) -> bool:
    idx = history.index
    tg = time_gate(idx)
    return bool(tg.iloc[-1])


def _eval_ma_alignment(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    return bool(
        ma_aligned_pre_cross(close, period_short=50, period_long=100, lookback=10).iloc[-1]
    )


def _eval_forward_ma_projection(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    volume = history.get("volume", pd.Series(1.0, index=close.index))
    from src.features.vwma import vwma as _vwma

    vw = _vwma(close, volume, window=min(100, len(close)))
    ma = close.rolling(window=min(50, len(close))).mean()
    out = ma_projection_meeting_point(vw, ma, horizon=min(20, len(close)))
    btm = out["bars_to_meet"].iloc[-1]
    return bool(not pd.isna(btm) and btm < 20 and btm > 0)


def _eval_price_ma_zscore(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    ma = close.rolling(window=min(50, len(close))).mean()
    z = price_ma_zscore(close, ma, lookback=min(100, len(close)))
    val = z.iloc[-1]
    return bool(not pd.isna(val) and val > -0.5)


def _eval_ma200_magnet(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    ma200 = close.rolling(window=min(200, len(close))).mean()
    sig = return_to_ma_signal(close, ma200, z_threshold=-1.5)
    return bool(sig.iloc[-1])


def _eval_volume_burst(bar: dict, history: pd.DataFrame) -> bool:
    if not _HAS_VOLUME_BURST:
        return False
    volume = history["volume"]
    z = _volume_zscore(volume, lookback=min(20, len(volume)))
    val = z.iloc[-1]
    return bool(not pd.isna(val) and val > 1.5)


def _eval_vpvr_poc_support(bar: dict, history: pd.DataFrame) -> bool:
    if not _HAS_VPVR_POC:
        return False
    close = history["close"]
    price = bar.get("close", close.iloc[-1])
    poc_price, _ = _vpvr_poc(history, window=min(200, len(history)), n_bins=24)
    if poc_price is None or pd.isna(poc_price):
        return False
    return bool(price <= poc_price * 1.02)


def _eval_turning_point(bar: dict, history: pd.DataFrame) -> bool:
    if not _HAS_TURNING_POINT:
        return False
    close = history["close"]
    sig = _is_turning_point(close, lookback=5)
    return bool(sig.iloc[-1])


def _eval_multi_tf_alignment(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    volume = history.get("volume", pd.Series(1.0, index=close.index))
    out = multi_tf_alignment(close, volume, higher_tf="1h", vwma_window=min(20, len(close)))
    return bool(out.iloc[-1])


def _eval_regime_r4_bull(bar: dict, context: dict) -> bool:
    regime = context.get("regime")
    if regime is None:
        return True
    return regime in ("bull", "r4_bull", 1)


def _eval_donchian_20(bar: dict, history: pd.DataFrame) -> bool:
    close = history["close"]
    high = history.get("high", close)
    window = min(20, len(high))
    donchian_high = high.rolling(window).max().iloc[-1]
    cur_close = close.iloc[-1]
    return bool(not pd.isna(donchian_high) and cur_close >= donchian_high)


def _eval_metalabeler(bar: dict, context: dict) -> bool:
    win_prob = context.get("metalabeler_win_prob")
    if win_prob is None:
        return False
    return float(win_prob) >= 0.6


def _eval_ubai_rs(bar: dict, context: dict) -> bool:
    rs_quartile = context.get("ubai_rs_quartile")
    if rs_quartile is None:
        return False
    return int(rs_quartile) <= 1


# ---------------------------------------------------------------------------
# Rule dispatch
# ---------------------------------------------------------------------------

def _check_rule(rule: str, bar: dict, history: pd.DataFrame, context: dict) -> bool:
    """Evaluate a single rule string; returns True if rule passes."""
    if rule.startswith("atr_stop_") or rule.startswith("take_"):
        # Stop/take rules are exit-side parameters parsed by the bench harness,
        # not entry filters — always pass at entry-router gating.
        return True
    if rule == "vwma_cross":
        return _eval_vwma_cross(bar, history)
    if rule == "ema_slope_gt_0":
        return _eval_ema_slope_gt_0(bar, history)
    if rule == "time_gate":
        return _eval_time_gate(bar, history)
    if rule == "ma_alignment_50_100":
        return _eval_ma_alignment(bar, history)
    if rule == "forward_ma_projection":
        return _eval_forward_ma_projection(bar, history)
    if rule == "price_ma_zscore":
        return _eval_price_ma_zscore(bar, history)
    if rule == "ma200_magnet":
        return _eval_ma200_magnet(bar, history)
    if rule == "volume_burst":
        return _eval_volume_burst(bar, history)
    if rule == "vpvr_poc_support":
        return _eval_vpvr_poc_support(bar, history)
    if rule == "turning_point_only":
        return _eval_turning_point(bar, history)
    if rule in ("multi_tf_gate_1h", "multi_tf_gate_1d"):
        return _eval_multi_tf_alignment(bar, history)
    if rule == "regime_r4_bull":
        return _eval_regime_r4_bull(bar, context)
    if rule == "donchian_20":
        return _eval_donchian_20(bar, history)
    if rule == "metalabeler_winprob_ge_0_6":
        return _eval_metalabeler(bar, context)
    if rule == "ubai_relative_strength_top_quartile":
        return _eval_ubai_rs(bar, context)
    # Compound rule references (D1_rules, D2_rules, etc.)
    if rule.endswith("_rules"):
        ref_variant = rule.split("_")[0]
        if ref_variant in VARIANT_REGISTRY:
            return _all_rules(VARIANT_REGISTRY[ref_variant]["rules"], bar, history, context)
    # Variant shorthand (e.g. "D1" meaning "all rules of D1")
    if rule in VARIANT_REGISTRY:
        return _all_rules(VARIANT_REGISTRY[rule]["rules"], bar, history, context)
    logger.warning("Unknown rule %r — treating as False", rule)
    return False


def _all_rules(rules: list[str], bar: dict, history: pd.DataFrame, context: dict) -> bool:
    return all(_check_rule(r, bar, history, context) for r in rules)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route(
    variant_id: str,
    bar: dict,
    history: pd.DataFrame,
    context: dict,
) -> dict | None:
    """Evaluate all rules for ``variant_id`` and return an entry signal dict.

    Parameters
    ----------
    variant_id:
        One of D0~D9.
    bar:
        Current bar as dict with at least ``{"close": float}``.
    history:
        DataFrame of recent OHLCV bars (columns: close, open, high, low, volume).
    context:
        Ambient context dict (regime, metalabeler scores, UBAI RS, etc.).

    Returns
    -------
    dict with keys ``variant_id``, ``signal`` (``"long"`` or ``None``),
    ``tf``, ``rules_passed`` — or ``None`` if variant_id is unknown.
    """
    if variant_id not in VARIANT_REGISTRY:
        logger.error("Unknown variant_id %r", variant_id)
        return None

    spec = VARIANT_REGISTRY[variant_id]
    rules = spec["rules"]
    passed = _all_rules(rules, bar, history, context)

    return {
        "variant_id": variant_id,
        "signal": "long" if passed else None,
        "tf": spec["tf"],
        "rules_passed": passed,
    }
