"""5y backtest of all 7 universe-scan strategies (#218 Phase 1 verification).

Reuses cached parquet data from prior bench runs:
  - data/cache/krx_daily/{code}.parquet (~334 KOSPI200 + KOSDAQ150 tickers)
  - data/cache/binance_daily/{symbol}.parquet (~29 Binance USDT pairs)

Strategies covered:
  KRX  : cs_tsmom_kr_daily, cs_rsi_div_kr, cs_bb_macd_kr, cs_adx_ma_kr
  Crypto: cs_tsmom_crypto_daily, cs_rsi_div_crypto, cs_macd_vol_crypto

Output:
  docs/work/active/000218-universe-scan-pivot/cs_bench_all.json
  docs/work/active/000218-universe-scan-pivot/cs_bench_all.md
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KRX_CACHE = ROOT / "data" / "cache" / "krx_daily"
CRYPTO_CACHE = ROOT / "data" / "cache" / "binance_daily"
OUT_DIR = ROOT / "docs" / "work" / "active" / "000218-universe-scan-pivot"
TRADING_DAYS_KRX = 252
TRADING_DAYS_CRYPTO = 365


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_strategies():
    """Bypass `src.backtest.__init__` side-effects by direct file load."""
    helpers = _load_module("_cs_helpers", ROOT / "src" / "backtest" / "strategies" / "_cs_helpers.py")
    sys.modules["_cs_helpers"] = helpers

    def _load(name):
        # patch import path to use local helpers
        path = ROOT / "src" / "backtest" / "strategies" / f"{name}.py"
        text = path.read_text(encoding="utf-8")
        text = text.replace("from backtest.strategies._cs_helpers", "from _cs_helpers")
        text = text.replace("from backtest.strategies.cs_rsi_div_kr",
                            "from cs_rsi_div_kr")
        text = text.replace("from backtest.strategies.cs_tsmom_kr_daily",
                            "from cs_tsmom_kr_daily")
        spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
        mod = importlib.util.module_from_spec(spec)
        exec(compile(text, str(path), "exec"), mod.__dict__)
        sys.modules[name] = mod
        return mod

    mods = {
        "cs_tsmom_kr_daily": _load("cs_tsmom_kr_daily"),
        "cs_rsi_div_kr": _load("cs_rsi_div_kr"),
        "cs_bb_macd_kr": _load("cs_bb_macd_kr"),
        "cs_adx_ma_kr": _load("cs_adx_ma_kr"),
        "cs_tsmom_crypto_daily": _load("cs_tsmom_crypto_daily"),
        "cs_rsi_div_crypto": _load("cs_rsi_div_crypto"),
        "cs_macd_vol_crypto": _load("cs_macd_vol_crypto"),
    }
    return helpers, mods


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_krx_panels(start: str = "2020-01-01"):
    panels = {}
    for p in KRX_CACHE.glob("*.parquet"):
        if p.stem.startswith("_"):
            continue
        try:
            df = pd.read_parquet(p)
            if len(df) > 252:
                panels[p.stem] = df
        except Exception:
            pass
    closes = pd.DataFrame({c: df["close"] for c, df in panels.items()}).sort_index().dropna(how="all")
    highs = pd.DataFrame({c: df["high"] for c, df in panels.items()}).reindex(closes.index)
    lows = pd.DataFrame({c: df["low"] for c, df in panels.items()}).reindex(closes.index)
    turnovers = pd.DataFrame({
        c: df["close"] * df["volume"] for c, df in panels.items()
    }).reindex(closes.index)
    return closes, highs, lows, turnovers


def load_crypto_panels():
    panels = {}
    for p in CRYPTO_CACHE.glob("*.parquet"):
        try:
            df = pd.read_parquet(p)
            if len(df) > 252:
                panels[p.stem] = df
        except Exception:
            pass
    closes = pd.DataFrame({c: df["close"] for c, df in panels.items()}).sort_index().dropna(how="all")
    highs = pd.DataFrame({c: df["high"] for c, df in panels.items()}).reindex(closes.index)
    lows = pd.DataFrame({c: df["low"] for c, df in panels.items()}).reindex(closes.index)
    qv = pd.DataFrame({
        c: df["quote_volume"] for c, df in panels.items() if "quote_volume" in df.columns
    }).reindex(closes.index)
    return closes, highs, lows, qv


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(weights: pd.DataFrame, closes: pd.DataFrame,
                    cost_bps: float, eval_start: str, trading_days: int) -> dict:
    bar_ret = closes.pct_change().fillna(0.0)
    pos_y = weights.shift(1).fillna(0.0)
    gross = (pos_y * bar_ret).sum(axis=1)
    turnover = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turnover * (cost_bps / 10_000.0) / 2.0
    port_ret = gross - cost

    eval_mask = port_ret.index >= pd.Timestamp(eval_start)
    ret = port_ret[eval_mask]
    n = len(ret)
    if n == 0 or ret.std() == 0:
        return {"sharpe": 0.0, "mdd": 0.0, "ann_return": 0.0, "exposure": 0.0,
                "n_trades": 0, "n_days": n, "avg_holdings": 0.0}
    equity = (1 + ret).cumprod()
    sharpe = float(ret.mean() / ret.std() * np.sqrt(trading_days))
    cum_max = equity.cummax()
    dd = equity / cum_max - 1
    mdd = float(dd.min())
    ann = float(equity.iloc[-1] ** (trading_days / n) - 1) if equity.iloc[-1] > 0 else -1.0
    n_holdings = (weights[eval_mask] > 0).sum(axis=1)
    exposure = float((n_holdings > 0).mean())
    avg_hold = float(n_holdings[n_holdings > 0].mean()) if (n_holdings > 0).any() else 0.0
    n_trades = int((turnover[eval_mask] > 0.01).sum())
    return {
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd, 4),
        "ann_return": round(ann, 4),
        "exposure": round(exposure, 3),
        "n_trades": n_trades,
        "n_days": n,
        "avg_holdings": round(avg_hold, 2),
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main():
    helpers, mods = _import_strategies()
    EVAL = "2020-01-01"

    print("[load] KRX panels ...", flush=True)
    krx_close, krx_high, krx_low, krx_turn = load_krx_panels()
    print(f"  shape={krx_close.shape}", flush=True)
    print("[load] crypto panels ...", flush=True)
    cr_close, cr_high, cr_low, cr_qv = load_crypto_panels()
    print(f"  shape={cr_close.shape}", flush=True)

    results: dict = {}

    krx_runs = [
        ("cs_tsmom_kr_daily",
         lambda: mods["cs_tsmom_kr_daily"].compute_weights(
             krx_close, krx_turn, top_n=20, rebal_freq=5,
             min_turnover=1e9, min_price=1000)),
        ("cs_rsi_div_kr",
         lambda: mods["cs_rsi_div_kr"].compute_weights(
             krx_close, krx_turn, top_n=20, rebal_freq=5,
             min_turnover=1e9, min_price=1000)),
        ("cs_bb_macd_kr",
         lambda: mods["cs_bb_macd_kr"].compute_weights(
             krx_close, krx_turn, top_n=20, rebal_freq=5,
             min_turnover=1e9, min_price=1000)),
        ("cs_adx_ma_kr",
         lambda: mods["cs_adx_ma_kr"].compute_weights(
             krx_high, krx_low, krx_close, krx_turn, top_n=20, rebal_freq=5,
             min_turnover=1e9, min_price=1000)),
    ]

    crypto_runs = [
        ("cs_tsmom_crypto_daily",
         lambda: mods["cs_tsmom_crypto_daily"].compute_weights(
             cr_close, cr_qv, top_n=10, rebal_freq=5, min_quote_vol=1e7)),
        ("cs_rsi_div_crypto",
         lambda: mods["cs_rsi_div_crypto"].compute_weights(
             cr_close, cr_qv, top_n=10, rebal_freq=5, min_quote_vol=1e7)),
        ("cs_macd_vol_crypto",
         lambda: mods["cs_macd_vol_crypto"].compute_weights(
             cr_close, cr_qv, top_n=10, rebal_freq=5, min_quote_vol=1e7,
             vol_ceiling=2.0)),  # 80% 너무 strict, 2.0 (200%) 알트 적합
    ]

    for name, fn in krx_runs:
        print(f"[bench] {name} ...", flush=True)
        w = fn()
        m = compute_metrics(w, krx_close, cost_bps=55, eval_start=EVAL,
                            trading_days=TRADING_DAYS_KRX)
        results[name] = m
        print(f"  sharpe={m['sharpe']:.3f}  mdd={m['mdd']*100:.2f}%  "
              f"ann={m['ann_return']*100:.2f}%  trades={m['n_trades']}  "
              f"holdings={m['avg_holdings']}  exposure={m['exposure']*100:.1f}%",
              flush=True)

    for name, fn in crypto_runs:
        print(f"[bench] {name} ...", flush=True)
        w = fn()
        m = compute_metrics(w, cr_close, cost_bps=16, eval_start=EVAL,
                            trading_days=TRADING_DAYS_CRYPTO)
        results[name] = m
        print(f"  sharpe={m['sharpe']:.3f}  mdd={m['mdd']*100:.2f}%  "
              f"ann={m['ann_return']*100:.2f}%  trades={m['n_trades']}  "
              f"holdings={m['avg_holdings']}  exposure={m['exposure']*100:.1f}%",
              flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "cs_bench_all.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Universe-Scan Strategies — 5y Bench (Phase 1 verification)",
        "",
        f"- Period: {EVAL} .. 2025-12-31",
        f"- KRX universe: KOSPI top-200 + KOSDAQ top-150 (cached, current Marcap pin)",
        f"- Crypto universe: Binance USDT spot top-30 (cached, current 24h volume pin)",
        f"- Cost: KRX 55bp / Crypto 16bp round-trip",
        "",
        "## Results",
        "",
        "| Strategy | Sharpe | MDD | Ann.Return | Avg Holdings | Trades | Exposure |",
        "|----------|-------:|----:|-----------:|-------------:|-------:|---------:|",
    ]
    for name, m in results.items():
        lines.append(
            f"| {name} | {m['sharpe']:.3f} | {m['mdd']*100:.2f}% | "
            f"{m['ann_return']*100:.2f}% | {m['avg_holdings']:.1f} | "
            f"{m['n_trades']} | {m['exposure']*100:.1f}% |"
        )
    (OUT_DIR / "cs_bench_all.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {OUT_DIR / 'cs_bench_all.md'}", flush=True)


if __name__ == "__main__":
    main()
