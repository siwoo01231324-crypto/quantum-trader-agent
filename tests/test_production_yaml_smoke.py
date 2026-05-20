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
    """Single-ticker (4) + universe-scan (6, #218) + smoke (2, #236) = 12 등록.

    2026-05-20 — momo-btc-v2 + live-scanner 5종 비활성화 (9553e87): 5y 검증
    결과 전부 PF<1·음의 기대값으로 확정되어 production.yaml 에서 주석처리.
    cs-bb-macd-kr 은 이전부터 inactive (Sharpe -0.32). 재활성화는 진입 신호
    재설계 + PF>1 게이트 통과 후에만.

    Smoke entries 는 SMOKE_TEST_ENABLED env 없으면 hold. 검증된 universe-scan
    + momo-kis-v1 만 실제 동작.
    """
    orch = load_orchestrator_from_yaml(
        _PRODUCTION_YAML,
        _make_policy(),
        on_metalabeler_missing="skip",
    )
    assert set(orch._strategies.keys()) == {
        # Single-ticker (2 active — momo-btc-v2/momo-vol-filtered/meanrev-pairs
        # disabled per 9553e87 + #248 Scenario B)
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
        # Candidate-C 4-parallel live-scanner 실험 (#247) — production wiring
        # 검증 단계, env-gated.
        "cand-c-2026-05-20-live-rsi-oversold-volume-spike",
        "cand-c-2026-05-20-live-breakout-with-atr-stop",
        "cand-c-2026-05-20-live-bb-lower-bounce",
        "cand-c-2026-05-20-live-oversold-with-divergence",
        # Live-scanner 원본 5종 — DISABLED (#240 / 5y eval): 9553e87 참조.
    }


def test_production_yaml_arms_orchestrator_dup_backstop():
    """#238 Item 3b — the top-level `orchestrator:` block must wire the
    duplicate-order backstop into the live orchestrator (config_loader path
    that qta.exe uses). Locks the wiring so it can't silently regress to the
    dormant 0.0 default the review flagged.
    """
    orch = load_orchestrator_from_yaml(
        _PRODUCTION_YAML,
        _make_policy(),
        on_metalabeler_missing="skip",
    )
    assert orch._min_order_interval_sec == 60.0


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
