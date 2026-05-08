"""Cross-sectional TSMOM 12-1 backtest on KRX universe.

Goal: backtest a portfolio that monitors a wide KRX universe (KOSPI top-200
by mktcap + KOSDAQ top-150 by mktcap) and, at each weekly rebalance,
holds the top-N tickers ranked by 12-1 month time-series momentum
(Moskowitz/Ooi/Pedersen 2012 cross-sectional adaptation).

Universe selection uses *current* Marcap as a proxy for "names a retail
trader would realistically watch". This introduces survivorship bias —
delisted names and stocks that dropped out of the leaders cohort during
the period are not included. Reported metrics will be optimistic vs a
true PIT (point-in-time) universe; flagged in the report.

Pipeline:
  1. Load universe (top-200 KOSPI + top-150 KOSDAQ by current Marcap).
  2. Fetch 5y daily OHLCV per ticker (FDR), cache to parquet.
  3. Build wide panels: close[date, ticker], turnover[date, ticker].
  4. Weekly rebalance (every Friday close):
       a. Liquidity filter: 60d avg turnover >= 1e9 KRW, close >= 1000 KRW.
       b. TSMOM 12-1 score: log(close[t-21]/close[t-252]).
       c. Take top-N by score where score > 0 (long-only).
       d. Equal-weight, holdings revalued daily until next rebal.
  5. Crash guard: KOSPI 252d drawdown <= -15% → liquidate to cash.
  6. Costs: 55 bps round-trip on portfolio turnover (25 commission + 30
     slippage; KOSDAQ liquidity tax).
  7. Metrics: Sharpe, MDD, Calmar, ann.return vs KOSPI benchmark, turnover,
     avg holdings count.

Output:
  docs/work/active/swing-strategy-portfolio/cs_tsmom_output.json
  docs/work/active/swing-strategy-portfolio/cs_tsmom_report.md
  data/cache/krx_daily/{ticker}.parquet (cached)

Usage:
  python scripts/bench_cs_tsmom_kr.py              # full default run
  python scripts/bench_cs_tsmom_kr.py --top-n 30   # hold top 30
  python scripts/bench_cs_tsmom_kr.py --rebal 20   # monthly rebal
  python scripts/bench_cs_tsmom_kr.py --kospi 200 --kosdaq 150 --refresh
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import socket
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# FDR uses requests/urllib under the hood with no default timeout — a slow
# endpoint can hang the worker forever. Set a global socket timeout so any
# blocked request raises after 20s and the retry loop kicks in.
socket.setdefaulttimeout(20)

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "krx_daily"
OUT_DIR = ROOT / "docs" / "work" / "active" / "swing-strategy-portfolio"

DEFAULT_START = "2019-01-01"   # +1y warmup before 2020 backtest start
DEFAULT_END = "2025-12-31"
BACKTEST_START = "2020-01-01"  # actual evaluation start (after 252d warmup)

DEFAULT_KOSPI_TOP = 200
DEFAULT_KOSDAQ_TOP = 150
DEFAULT_TOP_N = 20
DEFAULT_REBAL = 5              # weekly (Friday close)
DEFAULT_LONG_LB = 252          # ~12 months
DEFAULT_SKIP_LB = 21           # skip last month (reversal)
DEFAULT_LIQ_TURNOVER = 1e9     # 1B KRW avg daily turnover
DEFAULT_LIQ_PRICE = 1000
DEFAULT_DD_GUARD = -0.15
DEFAULT_COST_BPS = 55          # round-trip per ticker rebalanced
TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Universe + fetching
# ---------------------------------------------------------------------------

def build_universe(kospi_top: int, kosdaq_top: int) -> pd.DataFrame:
    import FinanceDataReader as fdr
    ks = fdr.StockListing("KOSPI")
    kq = fdr.StockListing("KOSDAQ")
    ks = ks.sort_values("Marcap", ascending=False).head(kospi_top).copy()
    kq = kq.sort_values("Marcap", ascending=False).head(kosdaq_top).copy()
    ks["board"] = "KOSPI"
    kq["board"] = "KOSDAQ"
    uni = pd.concat([ks, kq], ignore_index=True)[["Code", "Name", "board", "Marcap"]]
    uni = uni.rename(columns={"Code": "code", "Name": "name", "Marcap": "marcap"})
    # Filter ETF/ETN/SPAC noise: code endings 'K' or 'L' patterns occasionally appear;
    # keep only 6-digit numeric codes.
    uni = uni[uni["code"].str.match(r"^\d{6}$")].reset_index(drop=True)
    return uni


def _fetch_one(ticker: str, start: str, end: str, retries: int = 3) -> pd.DataFrame | None:
    import FinanceDataReader as fdr
    last_err = None
    for attempt in range(retries):
        try:
            df = fdr.DataReader(ticker, start, end)
            if df is None or df.empty:
                return None
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[keep].copy()
            df.index = pd.to_datetime(df.index)
            return df
        except Exception as e:
            last_err = e
            time.sleep(0.5 + attempt * 0.5)
    print(f"  [fetch-fail] {ticker}: {last_err}", flush=True)
    return None


def fetch_universe(uni: pd.DataFrame, start: str, end: str, refresh: bool,
                   max_workers: int = 8) -> dict[str, pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    panels: dict[str, pd.DataFrame] = {}
    todo: list[str] = []
    for code in uni["code"]:
        cache_path = CACHE_DIR / f"{code}.parquet"
        if cache_path.exists() and not refresh:
            try:
                df = pd.read_parquet(cache_path)
                panels[code] = df
                continue
            except Exception:
                pass
        todo.append(code)

    print(f"[fetch] cached={len(panels)}, todo={len(todo)}", flush=True)
    if not todo:
        return panels

    completed = 0
    started = time.time()
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_fetch_one, code, start, end): code for code in todo}
        for fut in cf.as_completed(futs):
            code = futs[fut]
            df = fut.result()
            completed += 1
            if df is not None and len(df) > DEFAULT_LONG_LB:
                df.to_parquet(CACHE_DIR / f"{code}.parquet")
                panels[code] = df
            if completed % 25 == 0 or completed == len(todo):
                elapsed = time.time() - started
                eta = elapsed / max(completed, 1) * (len(todo) - completed)
                print(f"  [fetch] {completed}/{len(todo)}  elapsed={elapsed:.0f}s  eta={eta:.0f}s",
                      flush=True)
    return panels


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------

def build_panels(panels: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    closes = pd.DataFrame({code: df["close"] for code, df in panels.items()})
    closes = closes.sort_index()
    # Common trading day grid (KRX business days inferred from data)
    closes = closes.dropna(how="all")
    turnovers = pd.DataFrame({
        code: (df["close"] * df["volume"]).rename(code) for code, df in panels.items()
    }).reindex(closes.index)
    return closes, turnovers


def fetch_kospi_index(start: str, end: str) -> pd.Series:
    import FinanceDataReader as fdr
    cache = CACHE_DIR / "_KS11.parquet"
    if cache.exists():
        try:
            return pd.read_parquet(cache)["close"]
        except Exception:
            pass
    df = fdr.DataReader("KS11", start, end)
    df = df.rename(columns={"Close": "close"})[["close"]]
    df.index = pd.to_datetime(df.index)
    df.to_parquet(cache)
    return df["close"]


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

def cs_tsmom_signals(closes: pd.DataFrame, turnovers: pd.DataFrame,
                     long_lb: int, skip_lb: int, top_n: int,
                     liq_turnover: float, liq_price: float,
                     rebal_freq: int) -> pd.DataFrame:
    """Return a wide weights DataFrame [date x ticker] in [0, 1]; rows sum to <=1.

    Equal-weight across selected names; rows between rebal dates carry forward
    last weights (with daily price drift applied later in P&L).
    """
    n = len(closes)
    rebal_idx = list(range(long_lb, n, rebal_freq))
    # Init with NaN so ffill carries last *rebal* row (not stale zeros)
    weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)

    avg_turnover = turnovers.rolling(60, min_periods=30).mean()

    for i in rebal_idx:
        date = closes.index[i]
        c_t = closes.iloc[i]
        c_skip = closes.iloc[i - skip_lb]
        c_long = closes.iloc[i - long_lb]
        score = np.log(c_skip / c_long)
        liquid = (avg_turnover.iloc[i] >= liq_turnover) & (c_t >= liq_price)
        eligible = score[liquid & score.notna() & (score > 0)]
        # Always write a full row (zeros for non-picks) so dropped names exit
        row = pd.Series(0.0, index=closes.columns)
        if not eligible.empty:
            picks = eligible.nlargest(top_n).index
            row.loc[picks] = 1.0 / len(picks)
        weights.loc[date] = row

    # ffill the populated rebal rows across non-rebal days; pre-warmup stays 0
    weights = weights.ffill().fillna(0.0)
    return weights


def apply_crash_guard(weights: pd.DataFrame, kospi: pd.Series,
                      lb: int, dd_threshold: float) -> pd.DataFrame:
    aligned = kospi.reindex(weights.index).ffill()
    rolling_max = aligned.rolling(lb, min_periods=20).max()
    dd = aligned / rolling_max - 1
    mask = (dd <= dd_threshold).fillna(False)
    weights = weights.copy()
    weights.loc[mask] = 0.0
    return weights


def apply_regime_filter(weights: pd.DataFrame, kospi: pd.Series,
                        ma_window: int) -> pd.DataFrame:
    """Zero weights on days when KOSPI close <= its rolling MA(ma_window).

    Classic Faber-style regime overlay: only hold equities when the index is
    above its long-term moving average. Reduces drawdown at the cost of
    missing some V-shape recoveries.
    """
    if ma_window <= 0:
        return weights
    aligned = kospi.reindex(weights.index).ffill()
    ma = aligned.rolling(ma_window, min_periods=ma_window // 2).mean()
    bear_mask = (aligned <= ma).fillna(False)
    weights = weights.copy()
    weights.loc[bear_mask] = 0.0
    return weights


# ---------------------------------------------------------------------------
# P&L + metrics
# ---------------------------------------------------------------------------

def backtest(weights: pd.DataFrame, closes: pd.DataFrame,
             cost_bps: float) -> dict:
    bar_ret = closes.pct_change().fillna(0.0)
    pos_y = weights.shift(1).fillna(0.0)  # apply previous day's signal
    # daily portfolio gross return
    port_ret_gross = (pos_y * bar_ret).sum(axis=1)
    # turnover = sum |delta weight| per day; cost = turnover * cost_bps/10000 / 2
    turnover = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turnover * (cost_bps / 10_000.0) / 2.0
    port_ret = port_ret_gross - cost
    equity = (1.0 + port_ret).cumprod()
    return {
        "ret": port_ret, "equity": equity, "turnover": turnover,
        "weights": weights, "n_holdings": (weights > 0).sum(axis=1),
    }


def metrics(ret: pd.Series, equity: pd.Series, turnover: pd.Series,
            n_holdings: pd.Series, eval_start: str) -> dict:
    ret = ret.loc[eval_start:]
    equity = equity.loc[eval_start:]
    if len(equity) > 0:
        equity = equity / equity.iloc[0]  # rebase to 1.0 at eval start
    turnover = turnover.loc[eval_start:]
    n_holdings = n_holdings.loc[eval_start:]
    n = len(ret)
    if n == 0:
        return {}
    sharpe = float(ret.mean() / ret.std() * np.sqrt(TRADING_DAYS)) if ret.std() > 0 else 0.0
    cum_max = equity.cummax()
    dd = equity / cum_max - 1
    mdd = float(dd.min())
    ann = float(equity.iloc[-1] ** (TRADING_DAYS / n) - 1) if equity.iloc[-1] > 0 else -1.0
    calmar = float(ann / abs(mdd)) if mdd < 0 else 0.0
    avg_hold = float(n_holdings[n_holdings > 0].mean()) if (n_holdings > 0).any() else 0.0
    avg_turnover_daily = float(turnover.mean())
    # Annual one-way turnover
    avg_turnover_ann = avg_turnover_daily * TRADING_DAYS
    exposure = float((n_holdings > 0).mean())
    return {
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd, 4),
        "calmar": round(calmar, 3),
        "ann_return": round(ann, 4),
        "final_equity": round(float(equity.iloc[-1]), 4),
        "avg_holdings": round(avg_hold, 2),
        "avg_turnover_ann": round(avg_turnover_ann, 2),
        "exposure_pct_days": round(exposure, 3),
        "n_days": n,
    }


def benchmark_metrics(kospi: pd.Series, eval_start: str) -> dict:
    s = kospi.loc[eval_start:].copy()
    if len(s) == 0:
        return {}
    ret = s.pct_change().fillna(0.0)
    equity = (1 + ret).cumprod()
    n = len(ret)
    sharpe = float(ret.mean() / ret.std() * np.sqrt(TRADING_DAYS)) if ret.std() > 0 else 0.0
    cum_max = equity.cummax()
    dd = equity / cum_max - 1
    mdd = float(dd.min())
    ann = float(equity.iloc[-1] ** (TRADING_DAYS / n) - 1) if equity.iloc[-1] > 0 else -1.0
    return {"sharpe": round(sharpe, 3), "mdd": round(mdd, 4), "ann_return": round(ann, 4)}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_report(out: dict, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")

    cfg = out["config"]
    res = out["results"]
    bm = out["benchmark"]
    top10 = out["top_holdings_recent"]

    lines = [
        "# Cross-Sectional TSMOM 12-1 — KRX Universe Bench",
        "",
        f"- Universe: KOSPI top-{cfg['kospi_top']} + KOSDAQ top-{cfg['kosdaq_top']} by current Marcap",
        f"  → {cfg['universe_size']} tickers (after 6-digit code filter), "
        f"{cfg['fetched_size']} fetched / sufficient history.",
        f"- Period: {cfg['eval_start']} .. {cfg['end']}  "
        f"(warmup from {cfg['warmup_start']})",
        f"- Strategy: TSMOM 12-1 (long={cfg['long_lb']}, skip={cfg['skip_lb']}), "
        f"top-{cfg['top_n']} equal-weight, rebal every {cfg['rebal_freq']} bars",
        f"- Liquidity filter: 60d avg turnover ≥ {cfg['liq_turnover']:,.0f} KRW, "
        f"close ≥ {cfg['liq_price']:,} KRW",
        f"- Crash guard: KOSPI 252d drawdown ≤ {cfg['dd_guard']:.0%}",
        f"- Regime filter: KOSPI close > MA{cfg['ma_filter']}"
        if cfg.get('ma_filter', 0) > 0
        else "- Regime filter: off",
        f"- Costs: {cfg['cost_bps']} bps round-trip on rebalance turnover",
        "",
        "## Strategy vs KOSPI",
        "",
        "| Metric | Strategy | KOSPI |",
        "|--------|---------:|------:|",
        f"| Sharpe | {res['sharpe']:.3f} | {bm['sharpe']:.3f} |",
        f"| MDD | {res['mdd']*100:.2f}% | {bm['mdd']*100:.2f}% |",
        f"| Ann. Return | {res['ann_return']*100:.2f}% | {bm['ann_return']*100:.2f}% |",
        f"| Calmar | {res['calmar']:.3f} | — |",
        f"| Final Equity (rebased 1.0) | {res['final_equity']:.3f} | — |",
        f"| Avg Holdings (when invested) | {res['avg_holdings']:.1f} | — |",
        f"| Avg Annual Turnover (one-way) | {res['avg_turnover_ann']:.2f}× | — |",
        f"| Exposure (days invested) | {res['exposure_pct_days']*100:.1f}% | 100% |",
        "",
        "## Most Recent Rebalance — Top Picks",
        "",
        "| Ticker | Name | Board | Weight |",
        "|--------|------|-------|-------:|",
    ]
    for row in top10:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['board']} | {row['weight']*100:.2f}% |"
        )
    lines += [
        "",
        "## Caveats",
        "",
        "- **Survivorship bias**: universe selected by *current* Marcap. Names that "
        "were leaders in 2020-2021 but later dropped out (e.g., delisted, demoted) "
        "are not in this run. Live results would likely be lower.",
        "- **Liquidity**: KOSDAQ 소형주 슬리피지가 30bp 보다 클 수 있음. 실거래에서는 "
        "실제 호가창 깊이로 보수적 추정 필요.",
        "- **Costs**: 55bp는 commission + slippage 평균값. KOSPI 대형주는 더 낮고 "
        "KOSDAQ 중소형주는 더 높음. 종목별 차등 적용 안 했음.",
        "- **No risk-free rate**: Sharpe is gross of risk-free (KRW 3-month CD ≈ 3.5%).",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kospi", type=int, default=DEFAULT_KOSPI_TOP)
    p.add_argument("--kosdaq", type=int, default=DEFAULT_KOSDAQ_TOP)
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--rebal", type=int, default=DEFAULT_REBAL)
    p.add_argument("--long-lb", type=int, default=DEFAULT_LONG_LB)
    p.add_argument("--skip-lb", type=int, default=DEFAULT_SKIP_LB)
    p.add_argument("--liq-turnover", type=float, default=DEFAULT_LIQ_TURNOVER)
    p.add_argument("--liq-price", type=float, default=DEFAULT_LIQ_PRICE)
    p.add_argument("--dd-guard", type=float, default=DEFAULT_DD_GUARD)
    p.add_argument("--ma-filter", type=int, default=0,
                   help="KOSPI MA-N regime filter (0=off, 200=Faber default)")
    p.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--eval-start", default=BACKTEST_START)
    p.add_argument("--refresh", action="store_true",
                   help="Force refetch even if cache exists")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    print(f"[universe] KOSPI top-{args.kospi} + KOSDAQ top-{args.kosdaq}", flush=True)
    uni = build_universe(args.kospi, args.kosdaq)
    print(f"  {len(uni)} tickers (post-filter)", flush=True)

    print(f"[fetch] {args.start} .. {args.end}  (cache={CACHE_DIR})", flush=True)
    panels = fetch_universe(uni, args.start, args.end, refresh=args.refresh,
                            max_workers=args.workers)
    print(f"  {len(panels)} tickers with sufficient history "
          f"(>= {DEFAULT_LONG_LB} bars)", flush=True)

    closes, turnovers = build_panels(panels)
    print(f"[panel] shape={closes.shape}, "
          f"date range {closes.index.min().date()} .. {closes.index.max().date()}",
          flush=True)

    print("[signal] computing TSMOM 12-1 weights ...", flush=True)
    weights = cs_tsmom_signals(
        closes, turnovers,
        long_lb=args.long_lb, skip_lb=args.skip_lb, top_n=args.top_n,
        liq_turnover=args.liq_turnover, liq_price=args.liq_price,
        rebal_freq=args.rebal,
    )
    n_rebals = (weights.diff().abs().sum(axis=1) > 1e-9).sum()
    print(f"  {int(n_rebals)} non-trivial rebal events", flush=True)

    kospi = fetch_kospi_index(args.start, args.end)
    if args.ma_filter > 0:
        weights = apply_regime_filter(weights, kospi, ma_window=args.ma_filter)
        bear_days = int((weights.sum(axis=1) == 0).sum())
        print(f"[regime] MA{args.ma_filter} filter zeroed {bear_days} bear-regime days",
              flush=True)
    weights = apply_crash_guard(weights, kospi, lb=DEFAULT_LONG_LB,
                                dd_threshold=args.dd_guard)

    print("[backtest] running daily P&L ...", flush=True)
    bt = backtest(weights, closes, cost_bps=args.cost_bps)

    res = metrics(bt["ret"], bt["equity"], bt["turnover"], bt["n_holdings"],
                  eval_start=args.eval_start)
    bm = benchmark_metrics(kospi, eval_start=args.eval_start)

    # Most recent rebalance top picks
    last_w = weights.iloc[-1]
    last_w = last_w[last_w > 0].sort_values(ascending=False).head(20)
    name_lookup = uni.set_index("code")[["name", "board"]]
    top_recent = []
    for code, w in last_w.items():
        nm = name_lookup.loc[code, "name"] if code in name_lookup.index else "?"
        bd = name_lookup.loc[code, "board"] if code in name_lookup.index else "?"
        top_recent.append({"code": code, "name": str(nm), "board": str(bd),
                           "weight": float(w)})

    out = {
        "config": {
            "kospi_top": args.kospi, "kosdaq_top": args.kosdaq,
            "top_n": args.top_n, "rebal_freq": args.rebal,
            "long_lb": args.long_lb, "skip_lb": args.skip_lb,
            "liq_turnover": args.liq_turnover, "liq_price": args.liq_price,
            "dd_guard": args.dd_guard, "ma_filter": args.ma_filter,
            "cost_bps": args.cost_bps,
            "warmup_start": args.start, "end": args.end,
            "eval_start": args.eval_start,
            "universe_size": int(len(uni)),
            "fetched_size": int(len(panels)),
        },
        "results": res,
        "benchmark": bm,
        "top_holdings_recent": top_recent,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_report(out, OUT_DIR / "cs_tsmom_output.json", OUT_DIR / "cs_tsmom_report.md")
    print(f"\n[result] strategy sharpe={res['sharpe']} mdd={res['mdd']*100:.2f}% "
          f"ann={res['ann_return']*100:.2f}%  vs  KOSPI sharpe={bm['sharpe']} "
          f"ann={bm['ann_return']*100:.2f}%", flush=True)
    print(f"[result] avg_holdings={res['avg_holdings']} "
          f"turnover_ann={res['avg_turnover_ann']}  exposure={res['exposure_pct_days']*100:.1f}%",
          flush=True)
    print(f"\nWrote {OUT_DIR / 'cs_tsmom_output.json'}", flush=True)
    print(f"Wrote {OUT_DIR / 'cs_tsmom_report.md'}", flush=True)


if __name__ == "__main__":
    main()
