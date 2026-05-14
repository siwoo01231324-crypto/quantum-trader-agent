"""Generic 5y backtest harness for cs_* universe-scan strategies (#218 follow-up).

Generalizes scripts/bench_cs_tsmom_kr.py + bench_cs_tsmom_crypto.py so the
5 strategies that haven't yet been bench'd (cs_rsi_div_kr / cs_bb_macd_kr /
cs_adx_ma_kr / cs_rsi_div_crypto / cs_macd_vol_crypto) all run through one
script.

Usage::

    # KRX 5y bench, default top_n=20, weekly rebal
    python scripts/bench_cs_universe.py --strategy cs_rsi_div_kr
    python scripts/bench_cs_universe.py --strategy cs_bb_macd_kr
    python scripts/bench_cs_universe.py --strategy cs_adx_ma_kr

    # Binance 5y bench
    python scripts/bench_cs_universe.py --strategy cs_rsi_div_crypto
    python scripts/bench_cs_universe.py --strategy cs_macd_vol_crypto

    # All 5 in one go
    python scripts/bench_cs_universe.py --all

The universe + fetch path delegates to the existing dedicated benches'
helpers (build_universe / fetch_universe / build_panels) so caching paths
remain compatible (data/cache/krx_daily/ and data/cache/binance_daily/).
Each strategy contributes only its own ``compute_weights`` call.

Output: per-strategy JSON metrics under
``docs/work/active/swing-strategy-portfolio/cs_<id>_output.json`` and a markdown
summary under the same directory.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / "docs" / "work" / "active" / "swing-strategy-portfolio"


# --- Strategy registry ------------------------------------------------------

@dataclass
class StrategySpec:
    strategy_id: str
    module: str             # python module to import compute_weights from
    universe: str           # "krx" or "binance"
    needs_high_low: bool    # cs_adx_ma_kr 만 True (high/low/close panel)
    quote_volume_kw: str    # "turnover" (KRX) or "quote_volume" (Binance)
    extra_kwargs: dict      # 기본값 외 추가 파라미터


REGISTRY: dict[str, StrategySpec] = {
    "cs_tsmom_kr_daily": StrategySpec(
        strategy_id="cs_tsmom_kr_daily",
        module="backtest.strategies.cs_tsmom_kr_daily",
        universe="krx",
        needs_high_low=False,
        quote_volume_kw="turnover",
        extra_kwargs={"top_n": 20, "rebal_freq": 5, "long_lb": 252, "skip_lb": 21,
                      "min_turnover": 1e9, "min_price": 1000.0},
    ),
    "cs_rsi_div_kr": StrategySpec(
        strategy_id="cs_rsi_div_kr",
        module="backtest.strategies.cs_rsi_div_kr",
        universe="krx",
        needs_high_low=False,
        quote_volume_kw="turnover",
        extra_kwargs={"top_n": 20, "rebal_freq": 5, "rsi_period": 14, "lookback": 20,
                      "min_turnover": 1e9, "min_price": 1000.0},
    ),
    "cs_bb_macd_kr": StrategySpec(
        strategy_id="cs_bb_macd_kr",
        module="backtest.strategies.cs_bb_macd_kr",
        universe="krx",
        needs_high_low=False,
        quote_volume_kw="turnover",
        extra_kwargs={"top_n": 20, "rebal_freq": 5, "bb_period": 20, "bb_std": 2.0,
                      "min_turnover": 1e9, "min_price": 1000.0},
    ),
    "cs_adx_ma_kr": StrategySpec(
        strategy_id="cs_adx_ma_kr",
        module="backtest.strategies.cs_adx_ma_kr",
        universe="krx",
        needs_high_low=True,
        quote_volume_kw="turnover",
        extra_kwargs={"top_n": 20, "rebal_freq": 5, "fast": 5, "slow": 20,
                      "adx_period": 14, "min_turnover": 1e9, "min_price": 1000.0},
    ),
    "cs_tsmom_crypto_daily": StrategySpec(
        strategy_id="cs_tsmom_crypto_daily",
        module="backtest.strategies.cs_tsmom_crypto_daily",
        universe="binance",
        needs_high_low=False,
        quote_volume_kw="quote_volume",
        extra_kwargs={"top_n": 10, "rebal_freq": 5, "long_lb": 252, "skip_lb": 21,
                      "min_quote_vol": 1e7},
    ),
    "cs_rsi_div_crypto": StrategySpec(
        strategy_id="cs_rsi_div_crypto",
        module="backtest.strategies.cs_rsi_div_crypto",
        universe="binance",
        needs_high_low=False,
        quote_volume_kw="quote_volume",
        extra_kwargs={"top_n": 10, "rebal_freq": 5, "rsi_period": 14, "lookback": 20,
                      "min_quote_vol": 1e7},
    ),
    "cs_macd_vol_crypto": StrategySpec(
        strategy_id="cs_macd_vol_crypto",
        module="backtest.strategies.cs_macd_vol_crypto",
        universe="binance",
        needs_high_low=False,
        quote_volume_kw="quote_volume",
        extra_kwargs={"top_n": 10, "rebal_freq": 5, "macd_fast": 12, "macd_slow": 26,
                      "macd_signal": 9, "vol_window": 30, "vol_ceiling": 0.80,
                      "min_quote_vol": 1e7},
    ),
}

# Strategies that haven't been bench'd yet — focus of this script.
UNBENCHED = ["cs_rsi_div_kr", "cs_bb_macd_kr", "cs_adx_ma_kr",
             "cs_rsi_div_crypto", "cs_macd_vol_crypto"]


# --- Universe loader (delegates to existing bench helpers) -------------------

def _load_krx_panels(refresh: bool):
    """Re-use bench_cs_tsmom_kr's universe + fetch path."""
    sys.path.insert(0, str(ROOT / "scripts"))
    bench_kr = importlib.import_module("bench_cs_tsmom_kr")
    uni = bench_kr.build_universe(
        bench_kr.DEFAULT_KOSPI_TOP, bench_kr.DEFAULT_KOSDAQ_TOP,
    )
    panels = bench_kr.fetch_universe(
        uni, bench_kr.DEFAULT_START, bench_kr.DEFAULT_END, refresh,
    )
    closes = pd.DataFrame(
        {code: df["close"] for code, df in panels.items()},
    ).sort_index().dropna(how="all")
    turnovers = pd.DataFrame(
        {code: (df["close"] * df["volume"]).rename(code) for code, df in panels.items()},
    ).reindex(closes.index)
    highs = pd.DataFrame(
        {code: df["high"] for code, df in panels.items() if "high" in df.columns},
    ).reindex(closes.index)
    lows = pd.DataFrame(
        {code: df["low"] for code, df in panels.items() if "low" in df.columns},
    ).reindex(closes.index)
    return closes, turnovers, highs, lows, bench_kr


def _load_binance_panels(refresh: bool):
    sys.path.insert(0, str(ROOT / "scripts"))
    bench_bn = importlib.import_module("bench_cs_tsmom_crypto")
    universe = bench_bn.fetch_top_universe(bench_bn.DEFAULT_UNIVERSE_SIZE)
    panels = bench_bn.fetch_universe(
        universe, bench_bn.DEFAULT_START, bench_bn.DEFAULT_END,
        refresh=refresh,
    )
    panels = {s: df for s, df in panels.items() if len(df) > bench_bn.DEFAULT_LONG_LB}
    closes, quote_vol = bench_bn.build_panels(panels)
    return closes, quote_vol, None, None, bench_bn


# --- Backtest core ----------------------------------------------------------

def _backtest_weights(weights: pd.DataFrame, closes: pd.DataFrame,
                      cost_bps: float = 55.0):
    bar_ret = closes.pct_change().fillna(0.0)
    pos_y = weights.shift(1).fillna(0.0)
    port_ret_gross = (pos_y * bar_ret).sum(axis=1)
    turnover = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turnover * (cost_bps / 10_000.0) / 2.0
    port_ret = port_ret_gross - cost
    equity = (1.0 + port_ret).cumprod()
    return port_ret, equity, turnover


def _metrics(ret: pd.Series, equity: pd.Series, turnover: pd.Series,
             eval_start: str = "2020-01-01", trading_days: int = 252) -> dict:
    ret = ret.loc[eval_start:]
    equity = equity.loc[eval_start:]
    turnover = turnover.loc[eval_start:]
    if len(equity) > 0:
        equity = equity / equity.iloc[0]
    n = len(ret)
    if n == 0:
        return {"trades": 0, "sharpe": 0.0, "mdd": 0.0, "ann_return": 0.0}
    sharpe = float(ret.mean() / ret.std() * np.sqrt(trading_days)) if ret.std() > 0 else 0.0
    cum_max = equity.cummax()
    dd = equity / cum_max - 1
    mdd = float(dd.min())
    ann = float(equity.iloc[-1] ** (trading_days / n) - 1) if equity.iloc[-1] > 0 else -1.0
    avg_turnover = float(turnover.mean())
    return {
        "n_days": n,
        "sharpe": sharpe,
        "mdd": mdd,
        "ann_return": ann,
        "avg_turnover_daily": avg_turnover,
        "final_equity": float(equity.iloc[-1]),
    }


# --- Run --------------------------------------------------------------------

def run_one(strategy_id: str, refresh: bool = False, cost_bps: float = 55.0) -> dict:
    spec = REGISTRY[strategy_id]
    if spec.universe == "krx":
        closes, turnovers, highs, lows, _ = _load_krx_panels(refresh)
    else:
        closes, quote_volume, _highs, _lows, _ = _load_binance_panels(refresh)

    module = importlib.import_module(spec.module)
    compute_weights = getattr(module, "compute_weights")

    if spec.needs_high_low:
        weights = compute_weights(
            high=highs, low=lows, close=closes,
            **{spec.quote_volume_kw: turnovers},
            **spec.extra_kwargs,
        )
    elif spec.universe == "krx":
        weights = compute_weights(
            close=closes, **{spec.quote_volume_kw: turnovers}, **spec.extra_kwargs,
        )
    else:  # binance
        weights = compute_weights(
            close=closes, **{spec.quote_volume_kw: quote_volume}, **spec.extra_kwargs,
        )

    port_ret, equity, turnover = _backtest_weights(weights, closes, cost_bps)
    eval_start = "2020-01-01" if spec.universe == "krx" else "2020-01-01"
    cost_default = 55.0 if spec.universe == "krx" else 10.0
    return {
        "strategy_id": strategy_id,
        "universe": spec.universe,
        "cost_bps": cost_bps if cost_bps != 55.0 else cost_default,
        **_metrics(port_ret, equity, turnover, eval_start=eval_start),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bench_cs_universe")
    parser.add_argument("--strategy", choices=list(REGISTRY.keys()), default=None)
    parser.add_argument("--all", action="store_true",
                        help=f"run the 5 unbenched strategies: {', '.join(UNBENCHED)}")
    parser.add_argument("--refresh", action="store_true",
                        help="bypass parquet cache; refetch from FDR/Binance")
    parser.add_argument("--cost-bps", type=float, default=None,
                        help="round-trip bps (default: 55 KRX / 10 Binance)")
    parser.add_argument("--output-dir", type=str,
                        default=str(OUT_DIR))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.all:
        targets = UNBENCHED
    elif args.strategy:
        targets = [args.strategy]
    else:
        print("error: specify --strategy <id> or --all", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_results: list[dict] = []

    for sid in targets:
        spec = REGISTRY[sid]
        cost = args.cost_bps if args.cost_bps is not None else (
            55.0 if spec.universe == "krx" else 10.0
        )
        print(f"[bench] running {sid} (universe={spec.universe}, cost_bps={cost})", flush=True)
        try:
            result = run_one(sid, refresh=args.refresh, cost_bps=cost)
        except Exception as exc:
            print(f"[bench] {sid} FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            result = {"strategy_id": sid, "error": str(exc)}
        all_results.append(result)
        print(json.dumps(result, indent=2, default=str), flush=True)
        out_path = out_dir / f"{sid}_output.json"
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"[bench] wrote {out_path}", flush=True)

    summary_path = out_dir / "cs_unbenched_summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    print(f"[bench] summary → {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
