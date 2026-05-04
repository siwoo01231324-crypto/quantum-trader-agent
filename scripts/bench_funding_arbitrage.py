#!/usr/bin/env python3
"""Bench funding arbitrage variants F0-F5 (issue #174).

Skeleton: variant matrix freeze + SHA256 witness. Actual backtest execution
requires 5-year funding data for all 3 exchanges to be fetched first.

Usage:
    python scripts/bench_funding_arbitrage.py \
        --lake-dir lake/ \
        --symbol BTCUSDT \
        --start 2020-09-01 --end 2025-12-31 \
        --output bench_output_funding_arb.json

Variant matrix F0-F5:
    F0: Binance-only, 8h rebalance (S4 baseline, Sharpe 0.961, mhr 0.29)
    F1: Binance-only, 1h rebalance
    F2: Binance-OKX spread arb
    F3: 3-exchange (Binance + OKX + Bybit) spread
    F4: Binance-OKX spread + 1h rebalance
    F5: Ensemble(F0, F2, F4) weighted

SHA256 of this registry is embedded in every output JSON as `registry_sha256`
to support pre-registration integrity verification (#99 DSR/PBO protocol).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

import pandas as pd

# Ensure project root on sys.path when run as script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.swing.multi_exchange_carry import VARIANT_REGISTRY

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Variant parameter matrix (frozen before any backtest run)
# ---------------------------------------------------------------------------

PARAM_MATRIX: dict[str, dict] = {
    "F0": {"threshold_neg": -0.005e-2},
    "F1": {"threshold_neg": -0.005e-2},
    "F2": {"spread_threshold": 0.001e-2},
    "F3": {"spread_threshold": 0.001e-2},
    "F4": {"spread_threshold": 0.001e-2},
    "F5": {"w0": 0.4, "w2": 0.4, "w4": 0.2, "threshold_neg": -0.005e-2, "spread_threshold": 0.001e-2},
}


def _registry_sha256() -> str:
    """Compute SHA256 of the serialized PARAM_MATRIX for pre-registration witness."""
    payload = json.dumps(PARAM_MATRIX, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _load_funding(lake_dir: Path, exchange: str, symbol: str) -> pd.DataFrame | None:
    """Load funding rate parquet for exchange/symbol. Returns None if not found."""
    path = lake_dir / "funding_rate" / f"exchange={exchange}" / f"symbol={symbol}" / "part-0.parquet"
    if not path.exists():
        log.warning("Funding data not found: %s", path)
        return None
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").sort_index()


def _merge_ohlcv_and_funding(
    ohlcv_path: Path,
    funding_frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Merge OHLCV bars with funding rates from all exchanges.

    Funding is resampled/ffilled onto the OHLCV bar index.
    Returns DataFrame with _funding_rate_{exchange} columns added.
    """
    ohlcv = pd.read_parquet(ohlcv_path)
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)
    ohlcv = ohlcv.sort_index()

    for exchange, fdf in funding_frames.items():
        col = f"_funding_rate_{exchange}"
        ohlcv[col] = fdf["funding_rate"].reindex(ohlcv.index, method="ffill")

    return ohlcv


def _run_variant(
    variant_id: str,
    params: dict,
    df: pd.DataFrame,
) -> dict:
    """Run a single variant strategy and return metrics dict (skeleton)."""
    strategy_fn = VARIANT_REGISTRY[variant_id]
    signal = strategy_fn(df, **params)

    unavailable = signal.name.endswith("_signal_unavailable") if hasattr(signal, "name") else False

    return {
        "variant_id": variant_id,
        "params": params,
        "n_bars": len(df),
        "n_signals": int((signal != 0).sum()),
        "data_available": not unavailable,
        # Actual backtest metrics (Sharpe, MDD, mhr) require OHLCV + full execution.
        # Placeholder until data is fetched:
        "sharpe": None,
        "mdd": None,
        "monthly_hit_rate": None,
        "note": "SKELETON — run after 5y funding data fetch",
    }


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Bench funding arbitrage variants F0-F5.")
    parser.add_argument("--lake-dir", default="lake/", help="Data lake root directory")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol (e.g. BTCUSDT)")
    parser.add_argument("--start", default="2020-09-01", help="Start date ISO")
    parser.add_argument("--end", default="2025-12-31", help="End date ISO")
    parser.add_argument("--output", default="bench_output_funding_arb.json", help="Output JSON path")
    args = parser.parse_args(argv)

    lake_dir = Path(args.lake_dir)
    registry_hash = _registry_sha256()
    print(f"Registry SHA256: {registry_hash}")
    print(f"Variants: {list(PARAM_MATRIX.keys())}")

    # Load funding data (may be absent if fetch not yet run)
    # OKX uses a different symbol format (BTC-USDT-SWAP) while bench --symbol is BTCUSDT
    _OKX_SYMBOL_MAP = {"BTCUSDT": "BTC-USDT-SWAP"}
    exchanges = ["binance", "okx", "bybit"]
    funding_frames: dict[str, pd.DataFrame] = {}
    for ex in exchanges:
        sym = _OKX_SYMBOL_MAP.get(args.symbol, args.symbol) if ex == "okx" else args.symbol
        fdf = _load_funding(lake_dir, ex, sym)
        if fdf is not None:
            # Deduplicate index (can occur if multiple fetches overlap)
            fdf = fdf[~fdf.index.duplicated(keep="last")]
            funding_frames[ex] = fdf
            print(f"  Loaded {ex} funding: {len(fdf)} rows")
        else:
            print(f"  Missing {ex} funding data (run fetch_funding_rates.py --exchange {ex} first)")

    if not funding_frames:
        print("No funding data available. Outputting skeleton result only.")
        results = [
            {
                "variant_id": vid,
                "params": params,
                "n_bars": 0,
                "n_signals": 0,
                "data_available": False,
                "sharpe": None,
                "mdd": None,
                "monthly_hit_rate": None,
                "note": "SKELETON — no funding data fetched yet",
            }
            for vid, params in PARAM_MATRIX.items()
        ]
    else:
        # Build a minimal DataFrame from funding data for signal generation
        # Use Binance index as master (longest history); reindex others onto it with ffill
        master_key = "binance" if "binance" in funding_frames else list(funding_frames.keys())[0]
        idx = funding_frames[master_key].index
        df = pd.DataFrame(index=idx)
        for ex, fdf in funding_frames.items():
            col = f"_funding_rate_{ex}"
            df[col] = fdf["funding_rate"].reindex(idx, method="ffill")
        # Backfill Binance as _funding_rate for F0/F1 fallback
        if "binance" in funding_frames:
            df["_funding_rate"] = df["_funding_rate_binance"]

        results = [
            _run_variant(vid, params, df)
            for vid, params in PARAM_MATRIX.items()
        ]

    output = {
        "registry_sha256": registry_hash,
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "param_matrix": PARAM_MATRIX,
        "results": results,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Output written to: {out_path}")


if __name__ == "__main__":
    main()
