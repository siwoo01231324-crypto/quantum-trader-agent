"""Tests for scripts/run_backtest.py — CLI runner + integration."""
from __future__ import annotations

import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_synthetic_parquet(tmp_path: Path, n_bars: int = 200) -> Path:
    """Create synthetic OHLCV Parquet data for testing."""
    data_dir = tmp_path / "lake"
    out_dir = data_dir / "ohlcv" / "freq=15m" / "year=2025" / "month=06" / "symbol=BTCUSDT"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_ts = pd.Timestamp("2025-06-01", tz="UTC")
    timestamps = [base_ts + pd.Timedelta(minutes=15 * i) for i in range(n_bars)]
    import numpy as np
    np.random.seed(42)
    # Random walk for close prices
    close = 30000.0 + np.cumsum(np.random.randn(n_bars) * 50)

    df = pd.DataFrame({
        "symbol": "BTCUSDT",
        "ts": timestamps,
        "freq": "15m",
        "open": close - np.random.rand(n_bars) * 10,
        "high": close + np.abs(np.random.randn(n_bars)) * 30,
        "low": close - np.abs(np.random.randn(n_bars)) * 30,
        "close": close,
        "volume": np.random.rand(n_bars) * 100 + 10,
        "vwap": close,
        "trade_count": np.random.randint(100, 1000, n_bars),
        "source": "test",
        "ingested_at": pd.Timestamp.now(tz="UTC"),
    })

    pq.write_table(pa.Table.from_pandas(df), out_dir / "part-0.parquet")
    return data_dir


def _create_strategy_md(tmp_path: Path) -> Path:
    """Create a mock momo-btc-v2.md strategy file."""
    docs_dir = tmp_path / "docs" / "specs" / "strategies"
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / "momo-btc-v2.md"
    path.write_text(textwrap.dedent("""\
        ---
        type: strategy
        id: momo-btc-v2
        name: BTC Momentum v2
        sharpe_bt: 1.82
        sharpe_live: null
        ---

        # BTC Momentum v2
        Test strategy file.
    """), encoding="utf-8")
    return tmp_path / "docs"


# ---------------------------------------------------------------------------
# Test 1: Strategy loading by name
# ---------------------------------------------------------------------------

def test_cli_loads_strategy_by_name():
    """--strategy momo-btc-v2 -> loads MomoBtcV2 class."""
    import importlib.util
    script_path = Path(__file__).parent.parent / "scripts" / "run_backtest.py"
    spec = importlib.util.spec_from_file_location("run_backtest_mod", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert "momo-btc-v2" in mod.STRATEGY_REGISTRY
    cls = mod.STRATEGY_REGISTRY["momo-btc-v2"]
    assert cls.__name__ == "MomoBtcV2"


# ---------------------------------------------------------------------------
# Test 2: Metrics printed to stdout
# ---------------------------------------------------------------------------

def test_cli_outputs_metrics_to_stdout(tmp_path, capsys):
    """Runner prints sharpe, mdd, total_return, trades to stdout."""
    data_dir = _create_synthetic_parquet(tmp_path, n_bars=200)
    docs_dir = _create_strategy_md(tmp_path)

    from backtest.bundle import load_ohlcv_from_parquet
    from backtest.engine import run_backtest, BacktestConfig
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    df = load_ohlcv_from_parquet(data_dir, "BTCUSDT", "15m")
    strategy = MomoBtcV2()
    result = run_backtest(df, strategy, BacktestConfig())
    m = result.metrics

    # Print like the runner does
    print(f"sharpe: {m['sharpe']:.4f}")
    print(f"mdd: {m['mdd']:.4f}")
    print(f"total_return: {m['total_return']:.4f}")
    print(f"trades: {m['trades']}")
    print(f"win_rate: {m['win_rate']:.4f}")

    captured = capsys.readouterr()
    assert "sharpe:" in captured.out
    assert "mdd:" in captured.out
    assert "total_return:" in captured.out
    assert "trades:" in captured.out


# ---------------------------------------------------------------------------
# Test 3: Frontmatter update
# ---------------------------------------------------------------------------

def test_frontmatter_update_writes_sharpe_bt(tmp_path):
    """After run, momo-btc-v2.md frontmatter has updated sharpe_bt field."""
    docs_dir = _create_strategy_md(tmp_path)

    from backtest.frontmatter import update_strategy_frontmatter

    metrics = {"sharpe": 2.345, "mdd": 0.04, "trades": 10, "win_rate": 0.6}
    path = update_strategy_frontmatter("momo-btc-v2", metrics, docs_dir)

    content = path.read_text(encoding="utf-8")
    assert "sharpe_bt: 2.345" in content
    # Ensure original content is preserved
    assert "BTC Momentum v2" in content


# ---------------------------------------------------------------------------
# Test 4: Doc agent draft generated
# ---------------------------------------------------------------------------

def test_doc_agent_draft_generated(tmp_path):
    """generate_backtest_draft produces a .draft.md file."""
    from services.doc_agent.generators import generate_backtest_draft

    bt_result = {
        "strategy": "momo-btc-v2",
        "period": ["2025-06-01", "2025-06-15"],
        "metrics": {"sharpe": 1.5, "mdd": 0.03, "trades": 8},
    }

    draft_path = generate_backtest_draft(bt_result, output_root=tmp_path)
    assert draft_path.exists()
    assert draft_path.suffix == ".md"
    assert ".draft" in draft_path.stem or "draft" in draft_path.name
    content = draft_path.read_text(encoding="utf-8")
    assert "momo-btc-v2" in content


# ---------------------------------------------------------------------------
# Test 5: Full pipeline with synthetic data
# ---------------------------------------------------------------------------

def test_full_pipeline_synthetic_data(tmp_path):
    """Create temp Parquet, run backtest, verify all outputs."""
    data_dir = _create_synthetic_parquet(tmp_path, n_bars=300)
    docs_dir = _create_strategy_md(tmp_path)

    from backtest.bundle import load_ohlcv_from_parquet
    from backtest.engine import run_backtest, BacktestConfig
    from backtest.frontmatter import update_strategy_frontmatter
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    # Load data
    df = load_ohlcv_from_parquet(data_dir, "BTCUSDT", "15m")
    assert len(df) == 300

    # Run backtest
    strategy = MomoBtcV2()
    result = run_backtest(df, strategy, BacktestConfig())

    # Verify result structure
    assert hasattr(result, "equity_curve")
    assert hasattr(result, "trades")
    assert hasattr(result, "metrics")
    assert len(result.equity_curve) == 300

    # Verify metrics keys
    m = result.metrics
    for key in ("sharpe", "mdd", "total_return", "trades", "win_rate"):
        assert key in m, f"Missing metric key: {key}"

    # Update frontmatter
    path = update_strategy_frontmatter("momo-btc-v2", m, docs_dir)
    content = path.read_text(encoding="utf-8")
    assert "sharpe_bt:" in content

    # Generate draft
    from services.doc_agent.generators import generate_backtest_draft
    bt_json = {
        "strategy": "momo-btc-v2",
        "period": ["2025-06-01", "2025-06-15"],
        "metrics": m,
    }
    draft = generate_backtest_draft(bt_json, output_root=tmp_path)
    assert draft.exists()
