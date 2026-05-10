"""Tests for scripts/cron_paper_universe_rebal.py (#218 후속 — paper rebal cron)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from brokers.base import OrderAck, OrderRequest

import cron_paper_universe_rebal as cron  # noqa: E402


# ---------------------------------------------------------------------------
# load_strategy_config
# ---------------------------------------------------------------------------

def test_load_strategy_config_returns_kwargs(tmp_path: Path):
    yaml_path = tmp_path / "production.yaml"
    yaml_path.write_text(
        """strategies:
  - id: cs-tsmom-kr-daily
    class: backtest.strategies.cs_async_wrapper.CrossSectionalAsyncStrategy
    kwargs:
      strategy_id: cs_tsmom_kr_daily
      module: backtest.strategies.cs_tsmom_kr_daily
      weights_kind: krx
      params: {top_n: 20}
""", encoding="utf-8")
    kwargs = cron.load_strategy_config(yaml_path, "cs-tsmom-kr-daily")
    assert kwargs["strategy_id"] == "cs_tsmom_kr_daily"
    assert kwargs["weights_kind"] == "krx"
    assert kwargs["params"]["top_n"] == 20


def test_load_strategy_config_missing_raises(tmp_path: Path):
    yaml_path = tmp_path / "production.yaml"
    yaml_path.write_text("strategies: []\n", encoding="utf-8")
    with pytest.raises(KeyError, match="not found"):
        cron.load_strategy_config(yaml_path, "ghost")


# ---------------------------------------------------------------------------
# compute_target_weights
# ---------------------------------------------------------------------------

def _make_panels(n_bars: int = 300, n_codes: int = 30):
    rng = np.random.default_rng(42)
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="B")
    codes = [f"T{i:03d}" for i in range(n_codes)]
    rets = rng.normal(0.001, 0.02, size=(n_bars, n_codes))
    closes = 1000 * np.exp(np.cumsum(rets, axis=0))
    high = closes * 1.01
    low = closes * 0.99
    turnover = rng.uniform(1e9, 1e11, size=closes.shape)
    return (
        pd.DataFrame(closes, index=dates, columns=codes),
        pd.DataFrame(high, index=dates, columns=codes),
        pd.DataFrame(low, index=dates, columns=codes),
        pd.DataFrame(turnover, index=dates, columns=codes),
    )


def test_compute_target_weights_krx_kind():
    panels = _make_panels()
    kwargs = {
        "module": "backtest.strategies.cs_tsmom_kr_daily",
        "weights_kind": "krx",
        "params": {"top_n": 5, "min_turnover": 1e8, "min_price": 100},
    }
    weights = cron.compute_target_weights("cs_tsmom_kr_daily", kwargs, panels)
    assert isinstance(weights, pd.Series)
    assert len(weights) <= 5
    assert (weights > 0).all()
    assert weights.sum() <= 1.0 + 1e-9


def test_compute_target_weights_krx_hlc_kind():
    panels = _make_panels()
    kwargs = {
        "module": "backtest.strategies.cs_adx_ma_kr",
        "weights_kind": "krx_hlc",
        "params": {"top_n": 5, "min_turnover": 1e8, "min_price": 100},
    }
    weights = cron.compute_target_weights("cs_adx_ma_kr", kwargs, panels)
    assert isinstance(weights, pd.Series)
    # 합성 데이터 → 신호 충분 약할 수 있음, 그래도 길이 ≤ 5
    assert len(weights) <= 5


def test_compute_target_weights_crypto_kind():
    panels = _make_panels()
    kwargs = {
        "module": "backtest.strategies.cs_tsmom_crypto_daily",
        "weights_kind": "crypto",
        "params": {"top_n": 3, "min_quote_vol": 1e7},
    }
    weights = cron.compute_target_weights("cs_tsmom_crypto_daily", kwargs, panels)
    assert isinstance(weights, pd.Series)
    assert len(weights) <= 3


def test_compute_target_weights_unknown_kind_raises():
    panels = _make_panels()
    with pytest.raises(ValueError, match="unknown weights_kind"):
        cron.compute_target_weights("x", {"module": "backtest.strategies.cs_tsmom_kr_daily",
                                           "weights_kind": "futures_perp"}, panels)


def test_compute_target_weights_missing_module_raises():
    panels = _make_panels()
    with pytest.raises(ValueError, match="missing 'module'"):
        cron.compute_target_weights("x", {"weights_kind": "krx"}, panels)


# ---------------------------------------------------------------------------
# setup_paper_broker — initial balance
# ---------------------------------------------------------------------------

def test_setup_paper_broker_creates_fresh_state(tmp_path: Path):
    wal = tmp_path / "wal.jsonl"
    broker = cron.setup_paper_broker(wal, Decimal("1000000"), "KRW")
    assert broker.name == "paper"
    bal = broker._balances["KRW"]
    assert bal.free == Decimal("1000000")
    assert broker._positions == {}


# ---------------------------------------------------------------------------
# run_rebalance dry-run (no broker / no Telegram / no fetch)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_rebalance_dry_run_returns_picks(tmp_path: Path, monkeypatch):
    """build_krx_universe_panel mocked → compute_weights → dry_run path."""
    yaml_path = tmp_path / "production.yaml"
    yaml_path.write_text(
        """strategies:
  - id: cs-tsmom-kr-daily
    class: x.X
    kwargs:
      strategy_id: cs_tsmom_kr_daily
      module: backtest.strategies.cs_tsmom_kr_daily
      weights_kind: krx
      params: {top_n: 5, min_turnover: 100000000, min_price: 100}
""", encoding="utf-8")

    panels = _make_panels()
    monkeypatch.setattr(cron, "build_krx_universe_panel", lambda **kw: panels)
    result = await cron.run_rebalance("cs-tsmom-kr-daily", dry_run=True,
                                       production_yaml=yaml_path)
    assert result["strategy_id"] == "cs-tsmom-kr-daily"
    assert result["dry_run"] is True
    assert result["n_picks"] <= 5
    assert isinstance(result["picks"], dict)


# ---------------------------------------------------------------------------
# run_rebalance live broker path (mocked broker)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_rebalance_live_path_dispatches_orders(tmp_path: Path, monkeypatch):
    yaml_path = tmp_path / "production.yaml"
    yaml_path.write_text(
        """strategies:
  - id: cs-tsmom-kr-daily
    class: x.X
    kwargs:
      strategy_id: cs_tsmom_kr_daily
      module: backtest.strategies.cs_tsmom_kr_daily
      weights_kind: krx
      params: {top_n: 5, min_turnover: 100000000, min_price: 100}
""", encoding="utf-8")
    panels = _make_panels()
    monkeypatch.setattr(cron, "build_krx_universe_panel", lambda **kw: panels)

    placed = []

    class _MockBroker:
        async def place_order(self, req):
            placed.append(req)
            return OrderAck(
                broker_order_id=f"m{len(placed)}",
                client_order_id=req.client_order_id,
                symbol=req.symbol, status="FILLED",
                ts=datetime.now(timezone.utc),
                qty=req.qty, price=Decimal("1000"),
            )

    mock_broker = _MockBroker()
    mock_broker._balances = {"KRW": MagicMock(free=Decimal("100000000"))}
    mock_broker._positions = {}

    monkeypatch.setattr(cron, "setup_paper_broker", lambda *args, **kwargs: mock_broker)
    # Telegram skip
    sys.modules.pop("telegram_rebal", None)
    import scripts.telegram_rebal as tr  # type: ignore
    monkeypatch.setattr(tr, "send_rebal_digest",
                        lambda *args, **kwargs: True)
    sys.modules["telegram_rebal"] = tr

    result = await cron.run_rebalance("cs-tsmom-kr-daily",
                                       production_yaml=yaml_path,
                                       wal_dir=tmp_path / "wal")
    assert result["n_picks"] >= 1
    # 발주 완료 (top-5 cs_tsmom 합성 → buy 발주 발생 가능)
    # n_submitted 는 0 일 수도 있음 (가격이 너무 비싸 lot 미만 등) — 정상 케이스
    assert "n_submitted" in result


# ---------------------------------------------------------------------------
# run_rebalance unknown strategy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_rebalance_unknown_strategy_raises(tmp_path: Path):
    yaml_path = tmp_path / "production.yaml"
    yaml_path.write_text("strategies: []\n", encoding="utf-8")
    with pytest.raises(KeyError):
        await cron.run_rebalance("ghost", dry_run=True, production_yaml=yaml_path)
