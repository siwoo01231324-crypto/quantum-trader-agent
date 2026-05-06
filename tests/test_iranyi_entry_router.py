"""Tests for src/backtest/iranyi/entry_router.py (issue #185 W1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.iranyi.entry_router import (
    VARIANT_REGISTRY,
    route,
    variant_registry_sha256,
)

_EXPECTED_SHA256 = "8405cf460d0adf1ff4199eed84f679e59a3773849322d4221247dad51012bd8a"

_EXPECTED_TF = {
    "D0": "4h",
    "D1": "4h",
    "D2": "4h",
    "D3": "4h",
    "D4": "4h",
    "D5": "4h",
    "D6": "5m",
    "D7": "5m",
    "D8": "5m",
    "D9": "5m",
}


def _synthetic_history(n: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series(100.0 + rng.normal(scale=0.5, size=n).cumsum(), index=idx)
    volume = pd.Series(rng.uniform(1.0, 5.0, size=n), index=idx)
    high = close + rng.uniform(0.1, 0.5, size=n)
    low = close - rng.uniform(0.1, 0.5, size=n)
    return pd.DataFrame(
        {"close": close, "open": close.shift(1).fillna(close), "high": high, "low": low, "volume": volume},
        index=idx,
    )


class TestVariantRegistryFrozen:
    def test_sha256_matches_known_value(self) -> None:
        """VARIANT_REGISTRY canonical JSON sha256 must be the frozen value."""
        assert variant_registry_sha256() == _EXPECTED_SHA256, (
            f"VARIANT_REGISTRY has been modified! Got {variant_registry_sha256()}, "
            f"expected {_EXPECTED_SHA256}. A registry change requires a new issue."
        )

    def test_all_ten_variants_registered(self) -> None:
        for vid in [f"D{i}" for i in range(10)]:
            assert vid in VARIANT_REGISTRY, f"{vid} missing from VARIANT_REGISTRY"

    def test_no_extra_variants(self) -> None:
        assert set(VARIANT_REGISTRY.keys()) == {f"D{i}" for i in range(10)}

    def test_tf_fields_correct(self) -> None:
        for vid, expected_tf in _EXPECTED_TF.items():
            assert VARIANT_REGISTRY[vid]["tf"] == expected_tf, (
                f"{vid} tf mismatch: got {VARIANT_REGISTRY[vid]['tf']}, expected {expected_tf}"
            )

    def test_each_variant_has_rules_list(self) -> None:
        for vid, spec in VARIANT_REGISTRY.items():
            assert "rules" in spec, f"{vid} missing 'rules'"
            assert isinstance(spec["rules"], list), f"{vid} rules must be list"
            assert len(spec["rules"]) > 0, f"{vid} rules must be non-empty"

    def test_5m_variants_have_take_pct(self) -> None:
        for vid in ["D6", "D7", "D8", "D9"]:
            assert "take_pct" in VARIANT_REGISTRY[vid], f"{vid} missing take_pct"
            assert VARIANT_REGISTRY[vid]["take_pct"] == 0.05


class TestRouteFunction:
    def test_route_returns_dict_for_known_variant(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        result = route("D0", bar, history, {})
        assert isinstance(result, dict)

    def test_route_returns_none_for_unknown_variant(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": 100.0}
        result = route("X99", bar, history, {})
        assert result is None

    def test_route_result_has_required_keys(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        result = route("D0", bar, history, {})
        assert result is not None
        assert "variant_id" in result
        assert "signal" in result
        assert "tf" in result
        assert "rules_passed" in result

    def test_route_variant_id_matches(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        for vid in [f"D{i}" for i in range(10)]:
            result = route(vid, bar, history, {})
            assert result is not None
            assert result["variant_id"] == vid

    def test_route_tf_matches_registry(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        for vid, expected_tf in _EXPECTED_TF.items():
            result = route(vid, bar, history, {})
            assert result is not None
            assert result["tf"] == expected_tf

    def test_route_signal_is_long_or_none(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        for vid in [f"D{i}" for i in range(10)]:
            result = route(vid, bar, history, {})
            assert result is not None
            assert result["signal"] in ("long", None)

    def test_route_rules_passed_is_bool(self) -> None:
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        for vid in [f"D{i}" for i in range(10)]:
            result = route(vid, bar, history, {})
            assert result is not None
            assert isinstance(result["rules_passed"], bool)

    def test_route_all_variants_no_exception(self) -> None:
        """All D0~D9 variants must run without raising exceptions."""
        history = _synthetic_history(30)
        bar = {"close": float(history["close"].iloc[-1])}
        context = {
            "regime": "bull",
            "metalabeler_win_prob": 0.7,
            "ubai_rs_quartile": 0,
        }
        for vid in [f"D{i}" for i in range(10)]:
            result = route(vid, bar, history, context)
            assert result is not None
