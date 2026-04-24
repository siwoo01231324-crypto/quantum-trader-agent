from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Union

import pandas as pd

from src.brokers.kis.schemas import FinancialRatio, MarketMultiples

_SOURCE_FIN_RATIO = "kis_fin_ratio_v1"
_SOURCE_MARKET_MULT = "kis_market_mult_v1"

# Per-attr normalisation: (metric_name_in_schema, unit)
# Verified against live KIS FHKST66430300 response 2026-04-24.
_FIN_RATIO_METRIC_UNIT: dict[str, tuple[str, str]] = {
    "eps":            ("eps",                    "krw"),
    "bps":            ("bps",                    "krw"),
    "sps":            ("sps",                    "krw"),
    "roe_val":        ("roe",                    "pct"),
    "grs":            ("revenue_growth",         "pct"),
    "bsop_prfi_inrt": ("operating_profit_margin", "pct"),
    "ntin_inrt":      ("net_income_margin",      "pct"),
    "lblt_rate":      ("debt_ratio",             "pct"),
    "rsrv_rate":      ("retained_earnings_rate", "pct"),
}

_MARKET_MULT_METRIC_UNIT: dict[str, tuple[str, str]] = {
    "per": ("per", "ratio"),
    "pbr": ("pbr", "ratio"),
    "eps": ("eps", "krw"),
    "bps": ("bps", "krw"),
}


def _fiscal_date_to_ts(fiscal_date: str) -> pd.Timestamp:
    """Convert "YYYYMM" to the last calendar day of that month (Asia/Seoul)."""
    year = int(fiscal_date[:4])
    month = int(fiscal_date[4:6])
    ts = pd.Timestamp(year=year, month=month, day=1, tz="Asia/Seoul") + pd.offsets.MonthEnd(0)
    return ts


def to_fundamentals_frame(
    raw: Union[FinancialRatio, list[FinancialRatio], MarketMultiples, list[MarketMultiples]],
    *,
    now_utc: datetime | None = None,
) -> pd.DataFrame:
    """Convert one or more FinancialRatio / MarketMultiples records to PIT frame.

    Columns: symbol, announce_date, period_end, fiscal_period, metric,
             value, unit, source, ingested_at.

    FinancialRatio records (quarterly, period-end):
      - eps/bps/sps → krw, roe_val/grs/margins/lblt_rate/rsrv_rate → pct
      - source = "kis_fin_ratio_v1"
      - fiscal_period = "YYYYMM", period_end = last-day-of-month (Asia/Seoul)
      - announce_date proxied to period_end (best available without DART filing date)

    MarketMultiples records (point-in-time):
      - per/pbr → ratio, eps/bps → krw
      - source = "kis_market_mult_v1"
      - fiscal_period = "pit", announce_date = ingested_at (point-in-time)
      - period_end = ingested_at truncated to date

    ingested_at: UTC now() unless override passed (for deterministic tests).
    """
    if isinstance(raw, (FinancialRatio, MarketMultiples)):
        records = [raw]
    else:
        records = list(raw)

    ingested_at = now_utc or datetime.now(timezone.utc)
    rows: list[dict] = []

    for r in records:
        symbol = r.symbol or ""
        if isinstance(r, FinancialRatio):
            if not r.fiscal_date:
                continue
            period_end = _fiscal_date_to_ts(r.fiscal_date)
            announce_date = period_end
            fiscal_period = r.fiscal_date
            source = _SOURCE_FIN_RATIO
            metric_map = _FIN_RATIO_METRIC_UNIT
        elif isinstance(r, MarketMultiples):
            # point-in-time multiples — use ingested_at as the PIT stamp
            pit_ts = pd.Timestamp(ingested_at).tz_convert("UTC")
            period_end = pit_ts
            announce_date = pit_ts
            fiscal_period = "pit"
            source = _SOURCE_MARKET_MULT
            metric_map = _MARKET_MULT_METRIC_UNIT
        else:
            continue

        for attr, (metric_name, unit) in metric_map.items():
            val: Decimal | None = getattr(r, attr, None)
            if val is None:
                continue
            rows.append({
                "symbol": symbol,
                "announce_date": announce_date,
                "period_end": period_end,
                "fiscal_period": fiscal_period,
                "metric": metric_name,
                "value": float(val),
                "unit": unit,
                "source": source,
                "ingested_at": ingested_at,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "symbol", "announce_date", "period_end", "fiscal_period",
            "metric", "value", "unit", "source", "ingested_at",
        ])

    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("category")
    df["fiscal_period"] = df["fiscal_period"].astype("category")
    df["metric"] = df["metric"].astype("category")
    df["unit"] = df["unit"].astype("category")
    df["source"] = df["source"].astype("category")
    return df
