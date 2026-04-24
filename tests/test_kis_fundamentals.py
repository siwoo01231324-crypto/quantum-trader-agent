"""Tests for KIS fundamentals client + fundamentals_store (issue #74).

Refactored 2026-04-24 after live paper-account verification revealed:
  - TR-ID FHKST66430100 was mis-labelled (balance-sheet, not financial-ratio)
  - FID_DIV_CLS_CODE is a REQUIRED parameter
  - output is a LIST of quarterly records
  - PER/PBR come from inquire-price (FHKST01010100), NOT financial-ratio
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

RATIO_FIXTURE = ROOT / "tests" / "fixtures" / "kis" / "financial_ratio_sample.json"
INQUIRE_FIXTURE = ROOT / "tests" / "fixtures" / "kis" / "inquire_price_sample.json"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixture provenance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_path", [RATIO_FIXTURE, INQUIRE_FIXTURE])
def test_fixture_has_meta_provenance(fixture_path: Path):
    """Both fixtures must carry _meta with source URL, captured ISO date, tr_id."""
    data = _load(fixture_path)
    meta = data.get("_meta", {})
    assert meta.get("source"), f"{fixture_path.name}: _meta.source required"
    captured = meta.get("captured", "")
    assert captured, f"{fixture_path.name}: _meta.captured required"
    datetime.strptime(captured[:10], "%Y-%m-%d")
    assert meta.get("tr_id"), f"{fixture_path.name}: _meta.tr_id required"
    assert meta["tr_id"].startswith("FHKST"), "KIS TR-IDs start with FHKST"


def test_ratio_fixture_shape():
    data = _load(RATIO_FIXTURE)
    out = data["response"]["output"]
    assert isinstance(out, list), "financial-ratio output must be a list"
    assert out, "fixture must contain at least one quarter"
    row0 = out[0]
    for required_key in ("stac_yymm", "eps", "bps", "roe_val"):
        assert required_key in row0, f"fixture row missing {required_key}"


def test_inquire_fixture_shape():
    data = _load(INQUIRE_FIXTURE)
    out = data["response"]["output"]
    assert isinstance(out, dict), "inquire-price output must be a dict"
    for required_key in ("per", "pbr", "eps", "bps"):
        assert required_key in out, f"inquire fixture missing {required_key}"


# ---------------------------------------------------------------------------
# fetch_financial_ratio_series (mocked)
# ---------------------------------------------------------------------------

def test_fetch_financial_ratio_series_mocked():
    """Mock KIS REST call; verify list of FinancialRatio returned."""
    from unittest.mock import MagicMock, patch

    from src.brokers.kis.fundamentals_client import fetch_financial_ratio_series
    from src.brokers.kis.schemas import FinancialRatio

    fixture = _load(RATIO_FIXTURE)
    mock_response = fixture["response"]

    mock_auth = MagicMock()
    mock_auth.get_token.return_value = "test_token"

    with patch("src.brokers.kis.fundamentals_client.KISClient") as MockClient:
        mock_client = MagicMock()
        mock_client._get.return_value = mock_response
        MockClient.return_value = mock_client

        results = fetch_financial_ratio_series(
            "005930",
            auth=mock_auth,
            app_key="k", app_secret="s",
            cano="12345678", acnt_prdt_cd="01",
            paper=True,
        )

    # confirm params sent included FID_DIV_CLS_CODE (critical bug-fix regression guard)
    call_args = mock_client._get.call_args
    assert call_args is not None
    params = call_args[0][2] if len(call_args[0]) >= 3 else call_args.kwargs.get("params") or call_args[0][-1]
    assert params.get("FID_DIV_CLS_CODE") == "1", "FID_DIV_CLS_CODE required by KIS"

    assert isinstance(results, list)
    assert len(results) == len(mock_response["output"])
    for r in results:
        assert isinstance(r, FinancialRatio)
        assert r.symbol == "005930"
        assert r.fiscal_date is not None
        assert r.eps is not None


def test_fetch_market_multiples_mocked():
    from unittest.mock import MagicMock, patch

    from src.brokers.kis.fundamentals_client import fetch_market_multiples
    from src.brokers.kis.schemas import MarketMultiples

    fixture = _load(INQUIRE_FIXTURE)
    mock_response = fixture["response"]

    mock_auth = MagicMock()
    mock_auth.get_token.return_value = "test_token"

    with patch("src.brokers.kis.fundamentals_client.KISClient") as MockClient:
        mock_client = MagicMock()
        mock_client._get.return_value = mock_response
        MockClient.return_value = mock_client

        result = fetch_market_multiples(
            "005930",
            auth=mock_auth,
            app_key="k", app_secret="s",
            cano="12345678", acnt_prdt_cd="01",
            paper=True,
        )

    assert isinstance(result, MarketMultiples)
    assert result.symbol == "005930"
    assert result.per is not None
    assert result.pbr is not None


# ---------------------------------------------------------------------------
# to_fundamentals_frame — FinancialRatio path
# ---------------------------------------------------------------------------

def test_to_fundamentals_frame_financial_ratio_schema_conform():
    """Produced DataFrame must match FUNDAMENTALS_PIT_SCHEMA columns + units."""
    from data_lake.fundamentals_store import to_fundamentals_frame
    from data_lake.schema import FUNDAMENTALS_PIT_SCHEMA
    from src.brokers.kis.schemas import FinancialRatio

    r = FinancialRatio(
        symbol="005930",
        fiscal_date="202512",
        eps=Decimal("6564"), bps=Decimal("63997"), sps=Decimal("49471"),
        roe_val=Decimal("10.85"), grs=Decimal("10.88"),
        bsop_prfi_inrt=Decimal("33.23"), ntin_inrt=Decimal("31.22"),
        lblt_rate=Decimal("29.94"), rsrv_rate=Decimal("45296.17"),
    )
    df = to_fundamentals_frame(r)

    assert isinstance(df, pd.DataFrame)
    assert set(df.columns) == set(FUNDAMENTALS_PIT_SCHEMA.keys())
    # every metric mapped
    metrics = set(df["metric"].astype(str))
    assert {"eps", "bps", "sps", "roe", "revenue_growth",
            "operating_profit_margin", "net_income_margin",
            "debt_ratio", "retained_earnings_rate"} <= metrics
    # unit normalization
    krw_metrics = set(df[df["unit"] == "krw"]["metric"].astype(str))
    assert krw_metrics == {"eps", "bps", "sps"}
    pct_metrics = set(df[df["unit"] == "pct"]["metric"].astype(str))
    assert {"roe", "revenue_growth", "debt_ratio"} <= pct_metrics
    # source
    assert set(df["source"].astype(str)) == {"kis_fin_ratio_v1"}


def test_to_fundamentals_frame_market_multiples_schema_conform():
    from data_lake.fundamentals_store import to_fundamentals_frame
    from data_lake.schema import FUNDAMENTALS_PIT_SCHEMA
    from src.brokers.kis.schemas import MarketMultiples

    mm = MarketMultiples(symbol="005930",
                         per=Decimal("33.44"), pbr=Decimal("3.43"),
                         eps=Decimal("6564"), bps=Decimal("63997"))
    df = to_fundamentals_frame(mm)
    assert set(df.columns) == set(FUNDAMENTALS_PIT_SCHEMA.keys())
    assert set(df["metric"].astype(str)) == {"per", "pbr", "eps", "bps"}
    assert set(df[df["metric"] == "per"]["unit"].astype(str)) == {"ratio"}
    assert set(df[df["metric"] == "eps"]["unit"].astype(str)) == {"krw"}
    assert set(df["source"].astype(str)) == {"kis_market_mult_v1"}
    assert set(df["fiscal_period"].astype(str)) == {"pit"}


def test_to_fundamentals_frame_combined_ratios_and_multiples():
    from data_lake.fundamentals_store import to_fundamentals_frame
    from src.brokers.kis.schemas import FinancialRatio, MarketMultiples

    records = [
        FinancialRatio(symbol="005930", fiscal_date="202512",
                       eps=Decimal("6564"), bps=Decimal("63997"),
                       roe_val=Decimal("10.85")),
        MarketMultiples(symbol="005930", per=Decimal("33.44"), pbr=Decimal("3.43")),
    ]
    df = to_fundamentals_frame(records)
    assert set(df["source"].astype(str)) == {"kis_fin_ratio_v1", "kis_market_mult_v1"}


def test_to_fundamentals_frame_empty_on_no_fiscal_date():
    from data_lake.fundamentals_store import to_fundamentals_frame
    from src.brokers.kis.schemas import FinancialRatio

    r = FinancialRatio(symbol="005930", fiscal_date=None, eps=None)
    df = to_fundamentals_frame(r)
    assert df.empty
    assert set(df.columns) == {
        "symbol", "announce_date", "period_end", "fiscal_period",
        "metric", "value", "unit", "source", "ingested_at",
    }


def test_to_fundamentals_frame_ingested_at_is_utc_tz_aware():
    from data_lake.fundamentals_store import to_fundamentals_frame
    from src.brokers.kis.schemas import FinancialRatio

    r = FinancialRatio(symbol="005930", fiscal_date="202512", eps=Decimal("6564"))
    df = to_fundamentals_frame(r)
    ts = df["ingested_at"].iloc[0]
    assert ts.tzinfo is not None
    assert ts.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Live integration — skipped unless HANTOO_* env is set
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_live_financial_ratio_and_multiples():
    """End-to-end live test against KIS paper account. Requires HANTOO_* env."""
    import os

    app_key = os.environ.get("HANTOO_FAKE_API_KEY", "")
    app_secret = os.environ.get("HANTOO_FAKE_SECRET_API_KEY", "")
    credit = os.environ.get("HANTOO_CREDIT_NUMBER", "")
    if not (app_key and app_secret and "-" in credit):
        pytest.skip("HANTOO_FAKE_* env not set")

    from src.brokers.kis.auth import KISAuth
    from src.brokers.kis.fundamentals_client import (
        fetch_financial_ratio_series, fetch_market_multiples,
    )
    from src.brokers.kis.schemas import FinancialRatio, MarketMultiples

    cano, acnt = credit.split("-", 1)
    auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)

    series = fetch_financial_ratio_series(
        "005930", auth=auth,
        app_key=app_key, app_secret=app_secret,
        cano=cano, acnt_prdt_cd=acnt, paper=True,
    )
    assert series and isinstance(series[0], FinancialRatio)
    assert series[0].eps is not None or series[0].bps is not None

    mm = fetch_market_multiples(
        "005930", auth=auth,
        app_key=app_key, app_secret=app_secret,
        cano=cano, acnt_prdt_cd=acnt, paper=True,
    )
    assert isinstance(mm, MarketMultiples)
    assert mm.per is not None and mm.pbr is not None
