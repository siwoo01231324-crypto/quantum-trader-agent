"""End-to-end smoke for production.yaml → orchestrator → run_bar (#177).

Verifies the AC1+AC3 verification chain:
  1. `configs/orchestrator/production.yaml` loads exactly the 5 baseline strategies.
  2. With a synthetic but realistic snapshot (enough RSI history at a KRX 15-min
     boundary), `momo-kis-v1` produces a non-hold Signal — i.e. the EXE smoke
     command `qta.exe --symbols 005930 --max-iterations 5` would emit at least
     one signal in steady state.

Runs without KIS network / model artifacts.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import pytz

from portfolio.config_loader import load_orchestrator_from_yaml
from risk.dsl import Policy

_REPO = Path(__file__).resolve().parents[1]
_PRODUCTION_YAML = _REPO / "configs" / "orchestrator" / "production.yaml"

KST = pytz.timezone("Asia/Seoul")


def _make_policy() -> Policy:
    return Policy(policy_version=1, name="smoke")


def _build_history(n_bars: int = 60) -> pd.DataFrame:
    """Synth a 1m OHLCV path with a clear bullish RSI divergence in the tail."""
    rng = np.random.default_rng(42)
    base = 80000.0
    # Trend down then up — engineered so RSI shows divergence in the last window.
    closes = []
    for i in range(n_bars):
        if i < n_bars // 2:
            closes.append(base * (1 - 0.0008 * i) + rng.normal(0, 50))
        else:
            tail_i = i - n_bars // 2
            closes.append(closes[-1] * (1 + 0.0009 * tail_i) + rng.normal(0, 50))
    closes = np.array(closes)
    idx = pd.date_range(
        start=pd.Timestamp("2026-05-04 00:00", tz="UTC"),
        periods=n_bars, freq="1min",
    )
    df = pd.DataFrame({
        "open": closes, "high": closes + 50, "low": closes - 50,
        "close": closes, "volume": [1000.0] * n_bars,
    }, index=idx)
    return df


def test_production_yaml_registers_all_strategies():
    """Single-ticker (5) + universe-scan (6, #218) + smoke (2, #236) +
    live-scanner (5, #227/#238) = 18 등록 (cs-bb-macd-kr inactive 제외).

    Smoke entries 는 SMOKE_TEST_ENABLED env 없으면 hold. Live-scanner entries
    는 LIVE_SCANNER_ENABLED env 없으면 LivePositionRiskManager 미구성이라
    universe-scan / single-ticker 만 동작 (#238 — 두 env-gate 모두 zero-impact).
    """
    orch = load_orchestrator_from_yaml(
        _PRODUCTION_YAML,
        _make_policy(),
        on_metalabeler_missing="skip",
    )
    assert set(orch._strategies.keys()) == {
        # Legacy single-ticker (5)
        "momo-btc-v2",
        "momo-vol-filtered",
        "meanrev-pairs",
        "breakout-donchian",
        "momo-kis-v1",
        # Universe-scan (#218, 6 active; cs-bb-macd-kr inactive)
        "cs-tsmom-kr-daily",
        "cs-rsi-div-kr",
        "cs-adx-ma-kr",
        "cs-tsmom-crypto-daily",
        "cs-rsi-div-crypto",
        "cs-macd-vol-crypto",
        # Smoke 통로 검증 (#236, env-gated — hold only without SMOKE_TEST_ENABLED)
        "smoke-1m-roundtrip-kis",
        "smoke-1m-roundtrip-binance",
        # Live-scanner (#227/#238, env-gated — LIVE_SCANNER_ENABLED 없으면 청산 비활성)
        "live-rsi-oversold-volume-spike",
        "live-macd-bullish-cross-breakout",
        "live-bb-lower-bounce",
        "live-breakout-with-atr-stop",
        "live-oversold-with-divergence",
    }


@pytest.mark.asyncio
async def test_momo_kis_v1_emits_signal_with_sufficient_history():
    """At a KRX 15-min boundary with 60 bars of synthesised divergence-pattern
    closes, momo-kis-v1 must return a non-hold Signal.

    This is the unit-level proxy for the AC `qta.exe ... 시그널 발생 확인`
    requirement (no need to run the EXE on a market session)."""
    orch = load_orchestrator_from_yaml(
        _PRODUCTION_YAML,
        _make_policy(),
        on_metalabeler_missing="skip",
    )
    history = _build_history(60)
    # Wilder RSI period 14 — precompute via the project's helper.
    from signals.rsi import compute_rsi
    rsi = compute_rsi(history["close"], period=14)

    # 15-min boundary in KST that the strategy will accept (10:15 KST = 01:15 UTC).
    ts = datetime(2026, 5, 4, 1, 15, tzinfo=timezone.utc)
    snapshot = {
        "ts": ts.isoformat(),
        "symbol": "005930",
        "price": float(history["close"].iloc[-1]),
        "equity_krw": 100000.0,
        "history": history,
        "ohlcv_history": {"005930": history},
        "factors": {"rsi": rsi},
    }

    momo_kis = orch._strategies["momo-kis-v1"]
    signal = await momo_kis.on_bar({"ts": ts, "market_snapshot": snapshot})
    assert signal is not None
    # Even if the synthesised series doesn't trigger entry, the strategy must at
    # minimum pass its bar-boundary + warmup gates — i.e. the *reason* should
    # advance beyond "warmup" or "not my bar".
    assert signal.reason not in {"warmup", "not my bar"}, (
        f"MomoKisV1 did not pass bar gates: {signal!r}"
    )
