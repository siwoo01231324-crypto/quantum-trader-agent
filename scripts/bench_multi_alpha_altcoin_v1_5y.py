"""5y bench for multi-alpha-altcoin-v1.

spec: docs/specs/strategies/multi-alpha-altcoin-v1.md

CLAUDE.md "PF·기대값 우선" 게이트:
    PF > 1.0 AND expectancy > 0  (5y · 다중 레짐 · 비용 ≥ 10bp).

Output: ``reports/eval_multi_alpha_altcoin_v1_5y.json``

Usage::

    python scripts/bench_multi_alpha_altcoin_v1_5y.py --months 60
    python scripts/bench_multi_alpha_altcoin_v1_5y.py --months 12   # 1y mini

Cost: 5y, 24 종목, 1h 봉, 매일 cointegration test (daily cache)
      → ~44k Engle-Granger test × ~50ms = ~37분 (statsmodels 의존)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from backtest.strategies.multi_alpha_altcoin_v1 import (  # noqa: E402
    MultiAlphaAltcoinV1,
)

logger = logging.getLogger("bench_multi_alpha_v1")

COST_BPS = 10.0   # round-trip 비용 0.1%
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT", "TRXUSDT",
    "LTCUSDT", "ATOMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT", "INJUSDT",
    "OPUSDT", "ARBUSDT", "ICPUSDT", "ALGOUSDT", "HBARUSDT", "BCHUSDT",
]


def _fetch_klines_paginated(symbol: str, start_ms: int, end_ms: int,
                            interval: str = "1h") -> pd.DataFrame:
    """Binance Futures klines paginated fetch — 5y 분량 OK."""
    from src.brokers.binance.universe_quote import fetch_klines
    chunk = 1000
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        batch = fetch_klines(symbol, interval=interval, start_ms=cur,
                             end_ms=end_ms, limit=chunk)
        if not batch:
            break
        rows.extend(batch)
        last_close_time = int(batch[-1][6])
        cur = last_close_time + 1
        if len(batch) < chunk:
            break
        time.sleep(0.05)  # rate-limit safety
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "tb_base", "tb_quote", "_",
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume"]]


def _simulate_trade(
    entry_idx: int, entry_price: float, direction: int,
    alt_hist: pd.DataFrame, stop_loss_pct: float, take_profit_pct: float,
    timeout_bars: int, cost_bps: float,
) -> dict:
    """단일 trade 시뮬 — entry 다음 봉부터 TP/SL/timeout 평가.

    direction: +1 long, -1 short.
    Returns: {outcome, pct, bar_idx}
    """
    if direction > 0:
        tp_px = entry_price * (1 + take_profit_pct)
        sl_px = entry_price * (1 - stop_loss_pct)
    else:
        tp_px = entry_price * (1 - take_profit_pct)
        sl_px = entry_price * (1 + stop_loss_pct)
    n = len(alt_hist)
    for i in range(1, timeout_bars + 1):
        if entry_idx + i >= n:
            break
        bar = alt_hist.iloc[entry_idx + i]
        hi, lo = float(bar["high"]), float(bar["low"])
        if direction > 0:
            hit_tp = hi >= tp_px
            hit_sl = lo <= sl_px
        else:
            hit_tp = lo <= tp_px
            hit_sl = hi >= sl_px
        if hit_tp and hit_sl:
            return {"outcome": "SL_first", "pct": -stop_loss_pct - cost_bps / 1e4, "bar_idx": i}
        if hit_sl:
            return {"outcome": "SL", "pct": -stop_loss_pct - cost_bps / 1e4, "bar_idx": i}
        if hit_tp:
            return {"outcome": "TP", "pct": +take_profit_pct - cost_bps / 1e4, "bar_idx": i}
    # timeout — 마지막 봉 close 로 정산
    last_idx = min(entry_idx + timeout_bars, n - 1)
    last_close = float(alt_hist.iloc[last_idx]["close"])
    pct = ((last_close - entry_price) / entry_price) * direction - cost_bps / 1e4
    return {"outcome": "timeout", "pct": pct, "bar_idx": timeout_bars}


def _simulate_symbol(
    btc_hist: pd.DataFrame, alt_hist: pd.DataFrame, alt_symbol: str,
    strat: MultiAlphaAltcoinV1,
    *, cost_bps: float = COST_BPS,
) -> list[dict]:
    """단일 alt 의 5y 시뮬 — 매 봉 평가 + entry 시 시뮬."""
    trades: list[dict] = []
    # 두 series 시간 정렬 (intersection)
    common_idx = btc_hist.index.intersection(alt_hist.index)
    if len(common_idx) < strat.MIN_HISTORY + 10:
        return trades
    btc = btc_hist.loc[common_idx]
    alt = alt_hist.loc[common_idx]
    n = len(common_idx)
    last_exit_idx = -1
    cooldown_bars = int(strat.cooldown_after_stop_sec / 3600)  # 30분 → 0 (1h 봉)
    for t in range(strat.MIN_HISTORY, n - 1):
        if t < last_exit_idx + cooldown_bars:
            continue
        ts = common_idx[t]
        btc_w = btc.iloc[:t + 1]
        alt_w = alt.iloc[:t + 1]
        action, direction, diag = strat.evaluate(btc_w, alt_w, ts, alt_symbol)
        if action == "hold":
            continue
        entry_price = float(alt.iloc[t]["close"])
        result = _simulate_trade(
            t, entry_price, direction, alt,
            strat.stop_loss_pct, strat.take_profit_pct,
            strat.timeout_bars, cost_bps,
        )
        trades.append({
            "ts": ts.isoformat(),
            "symbol": alt_symbol,
            "direction": "long" if direction > 0 else "short",
            "layer": diag.get("layer", ""),
            "regime": diag.get("regime", ""),
            "pvalue": diag.get("pvalue"),
            "corr": diag.get("corr"),
            **result,
        })
        last_exit_idx = t + result["bar_idx"]
    return trades


def _aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "PF": None, "expectancy": None, "win_rate": None,
                "sum_pct": 0.0, "by_layer": {}}
    pcts = np.array([t["pct"] for t in trades])
    wins = pcts[pcts > 0]
    losses = pcts[pcts < 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.size else float("inf")
    expectancy = pcts.mean()
    win_rate = float((pcts > 0).mean())
    # by layer
    by_layer: dict[str, dict] = {}
    for layer in {t["layer"] for t in trades}:
        sub = [t for t in trades if t["layer"] == layer]
        sub_pcts = np.array([t["pct"] for t in sub])
        sub_wins = sub_pcts[sub_pcts > 0]
        sub_losses = sub_pcts[sub_pcts < 0]
        sub_pf = (sub_wins.sum() / abs(sub_losses.sum())) if sub_losses.size else float("inf")
        by_layer[layer] = {
            "n": len(sub),
            "PF": sub_pf if np.isfinite(sub_pf) else None,
            "expectancy": float(sub_pcts.mean()),
            "win_rate": float((sub_pcts > 0).mean()),
            "sum_pct": float(sub_pcts.sum()),
        }
    return {
        "n": len(trades),
        "PF": pf if np.isfinite(pf) else None,
        "expectancy": float(expectancy),
        "win_rate": win_rate,
        "sum_pct": float(pcts.sum()),
        "by_layer": by_layer,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=60, help="lookback months (default 60 = 5y)")
    p.add_argument("--symbols", type=str, default=None,
                   help="comma-separated, default = built-in 24 USDT-perp")
    p.add_argument("--output", type=str, default="reports/eval_multi_alpha_altcoin_v1_5y.json")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    symbols = (args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS)
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if "BTCUSDT" not in symbols:
        symbols.insert(0, "BTCUSDT")

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.months * 30 * 86400 * 1000

    logger.info("fetching %d symbols (%d months 1h klines)", len(symbols), args.months)
    hist: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = _fetch_klines_paginated(sym, start_ms, end_ms)
            if len(df) > 100:
                hist[sym] = df
                logger.info("  %s: %d bars", sym, len(df))
            else:
                logger.warning("  %s: insufficient (%d bars) — skip", sym, len(df))
        except Exception as err:
            logger.warning("  %s: fetch failed: %s", sym, err)

    if "BTCUSDT" not in hist:
        logger.error("BTCUSDT data missing — abort")
        return 1
    btc_hist = hist.pop("BTCUSDT")

    strat = MultiAlphaAltcoinV1()
    all_trades: list[dict] = []
    t0 = time.time()
    for i, (sym, alt_hist) in enumerate(hist.items(), 1):
        sym_t0 = time.time()
        trades = _simulate_symbol(btc_hist, alt_hist, sym, strat)
        all_trades.extend(trades)
        logger.info(
            "[%d/%d] %s: %d trades  (%.1fs)",
            i, len(hist), sym, len(trades), time.time() - sym_t0,
        )

    elapsed = time.time() - t0
    agg = _aggregate(all_trades)
    gate_pass = (
        agg["PF"] is not None and agg["PF"] > 1.0
        and agg["expectancy"] is not None and agg["expectancy"] > 0
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": args.months,
        "symbols_count": len(hist) + 1,
        "cost_bps": COST_BPS,
        "strategy_params": {
            "stop_loss_pct": strat.stop_loss_pct,
            "take_profit_pct": strat.take_profit_pct,
            "timeout_bars": strat.timeout_bars,
            "coint_window_bars": strat.coint_window_bars,
            "corr_window_bars": strat.corr_window_bars,
            "regime_corr_high": strat.regime_corr_high,
            "regime_corr_low": strat.regime_corr_low,
            "lead_lag_ret_threshold": strat.lead_lag_ret_threshold,
            "zscore_threshold": strat.zscore_threshold,
        },
        **agg,
        "gate_pass": gate_pass,
        "gate_rule": "PF > 1.0 AND expectancy > 0 (CLAUDE.md 5y backtest gate)",
        "elapsed_sec": elapsed,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(
        "DONE — n=%d  PF=%s  exp=%.4f%%  wr=%.1f%%  gate_pass=%s  elapsed=%.0fs",
        agg["n"],
        f"{agg['PF']:.3f}" if agg["PF"] is not None else "None",
        (agg["expectancy"] or 0) * 100,
        (agg["win_rate"] or 0) * 100,
        gate_pass, elapsed,
    )
    print(json.dumps({k: payload[k] for k in
                      ("n", "PF", "expectancy", "win_rate", "sum_pct",
                       "gate_pass", "elapsed_sec")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
