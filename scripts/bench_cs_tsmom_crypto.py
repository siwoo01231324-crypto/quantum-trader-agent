"""Cross-sectional TSMOM 12-1 backtest on Binance crypto universe.

Mirror of `bench_cs_tsmom_kr.py` for Binance spot USDT pairs. Same pattern:
universe of top-N pairs by 24h quote volume → log(close[t-21]/close[t-252])
ranking → top-10 equal-weight, weekly rebalance, BTC drawdown crash guard.

Data source: Binance public klines endpoint (no auth, rate-limited per IP).
Cache:       data/cache/binance_daily/{symbol}.parquet
Output:      docs/work/active/swing-strategy-portfolio/cs_tsmom_crypto_*.{json,md}

Universe filter:
- Stablecoins: USDC, USD1, FDUSD, BUSD, TUSD, DAI, USDP — excluded.
- Wrapped/commodity-pegged: PAXG (gold), WBTCUSDT (rare on spot) — excluded.
- Leveraged tokens (UPUSDT, DOWNUSDT) — excluded by suffix.
- Defunct: LUNCUSDT (LUNA classic, post-collapse residue) — excluded.

Survivorship bias: universe is *current* top-N by 24h volume. Symbols listed
mid-period (e.g., TONUSDT in 2024) get whatever history Binance returns and
contribute only after their first 252-bar warmup. Newer-listing bias is
documented in the report.

Usage:
  python scripts/bench_cs_tsmom_crypto.py
  python scripts/bench_cs_tsmom_crypto.py --top-n 5 --universe 20 --rebal 5
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

socket.setdefaulttimeout(20)

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "binance_daily"
OUT_DIR = ROOT / "docs" / "work" / "active" / "swing-strategy-portfolio"

DEFAULT_START = "2019-01-01"
DEFAULT_END = "2025-12-31"
BACKTEST_START = "2020-01-01"

DEFAULT_UNIVERSE_SIZE = 30   # screen top 30 by 24h volume
DEFAULT_TOP_N = 10           # hold top 10 by score
DEFAULT_REBAL = 5            # weekly
DEFAULT_LONG_LB = 252
DEFAULT_SKIP_LB = 21
DEFAULT_DD_GUARD = -0.30     # crypto is more volatile, use looser threshold
DEFAULT_COST_BPS = 16        # ~8bp commission + 8bp slippage round-trip
TRADING_DAYS = 365           # crypto trades 24/7

EXCLUDED_BASES = {
    # Stablecoins & fiat-pegged
    "USDC", "USD1", "FDUSD", "BUSD", "TUSD", "DAI", "USDP", "PYUSD", "USDD",
    # Commodity-pegged
    "PAXG", "XAUT",
    # Defunct / collapsed
    "LUNC", "USTC", "FTT",
}
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


# ---------------------------------------------------------------------------
# Universe + fetching
# ---------------------------------------------------------------------------

def fetch_top_universe(n: int) -> list[str]:
    """Return top-N USDT spot symbols by 24h quote volume, filtered."""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    usdt = [d for d in data if d["symbol"].endswith("USDT")]

    def keep(d) -> bool:
        sym = d["symbol"]
        if any(sym.endswith(suf) for suf in EXCLUDED_SUFFIXES):
            return False
        base = sym[: -len("USDT")]
        if base in EXCLUDED_BASES:
            return False
        # filter near-stable (price within 1% of $1, low volatility)
        try:
            last = float(d["lastPrice"])
            change = abs(float(d["priceChangePercent"]))
        except (KeyError, ValueError):
            return True
        if 0.98 <= last <= 1.02 and change < 0.5:
            return False
        return True

    usdt = [d for d in usdt if keep(d)]
    usdt.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
    return [d["symbol"] for d in usdt[:n]]


def _fetch_klines(symbol: str, start_ms: int, end_ms: int,
                  retries: int = 3) -> list[list]:
    """Fetch all daily klines for symbol in [start_ms, end_ms]. Paginates."""
    all_rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?"
               f"symbol={symbol}&interval=1d"
               f"&startTime={cursor}&endTime={end_ms}&limit=1000")
        last_err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    rows = json.loads(r.read())
                break
            except (urllib.error.HTTPError, urllib.error.URLError,
                    socket.timeout) as e:
                last_err = e
                time.sleep(0.8 + attempt * 0.5)
        else:
            raise RuntimeError(f"{symbol}: {last_err}")
        if not rows:
            break
        all_rows.extend(rows)
        last_close_ms = rows[-1][6]  # kline close time
        if len(rows) < 1000:
            break
        cursor = last_close_ms + 1
        time.sleep(0.05)  # be polite to Binance
    return all_rows


def fetch_one(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    try:
        rows = _fetch_klines(symbol, start_ms, end_ms)
    except Exception as e:
        print(f"  [fetch-fail] {symbol}: {e}", flush=True)
        return None
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "tb_base", "tb_quote", "_",
    ])
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df.index = pd.to_datetime(df["open_time"], unit="ms").dt.normalize()
    df = df[["open", "high", "low", "close", "volume", "quote_volume"]]
    return df


def fetch_universe(symbols: list[str], start: str, end: str, refresh: bool,
                   max_workers: int = 4) -> dict[str, pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    panels: dict[str, pd.DataFrame] = {}
    todo: list[str] = []
    for sym in symbols:
        cache = CACHE_DIR / f"{sym}.parquet"
        if cache.exists() and not refresh:
            try:
                panels[sym] = pd.read_parquet(cache)
                continue
            except Exception:
                pass
        todo.append(sym)
    print(f"[fetch] cached={len(panels)}, todo={len(todo)}", flush=True)
    if not todo:
        return panels

    started = time.time()
    completed = 0
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(fetch_one, sym, start, end): sym for sym in todo}
        for fut in cf.as_completed(futs):
            sym = futs[fut]
            df = fut.result()
            completed += 1
            if df is not None and len(df) > 30:
                df.to_parquet(CACHE_DIR / f"{sym}.parquet")
                panels[sym] = df
                print(f"  [{completed}/{len(todo)}] {sym}: {len(df)} bars",
                      flush=True)
            else:
                print(f"  [{completed}/{len(todo)}] {sym}: insufficient",
                      flush=True)
    elapsed = time.time() - started
    print(f"  fetch elapsed={elapsed:.1f}s", flush=True)
    return panels


# ---------------------------------------------------------------------------
# Panel + signal
# ---------------------------------------------------------------------------

def build_panels(panels: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    closes = pd.DataFrame({s: df["close"] for s, df in panels.items()}).sort_index()
    closes = closes.dropna(how="all")
    quote_vol = pd.DataFrame({s: df["quote_volume"] for s, df in panels.items()}).reindex(closes.index)
    return closes, quote_vol


def cs_tsmom_signals(closes: pd.DataFrame, quote_vol: pd.DataFrame,
                     long_lb: int, skip_lb: int, top_n: int,
                     min_quote_vol: float, rebal_freq: int) -> pd.DataFrame:
    n = len(closes)
    rebal_idx = list(range(long_lb, n, rebal_freq))
    weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    avg_qv = quote_vol.rolling(60, min_periods=20).mean()

    for i in rebal_idx:
        date = closes.index[i]
        c_skip = closes.iloc[i - skip_lb]
        c_long = closes.iloc[i - long_lb]
        score = np.log(c_skip / c_long)
        liquid = (avg_qv.iloc[i] >= min_quote_vol)
        eligible = score[liquid & score.notna() & (score > 0)]
        row = pd.Series(0.0, index=closes.columns)
        if not eligible.empty:
            picks = eligible.nlargest(top_n).index
            row.loc[picks] = 1.0 / len(picks)
        weights.loc[date] = row

    weights = weights.ffill().fillna(0.0)
    return weights


def apply_btc_crash_guard(weights: pd.DataFrame, btc: pd.Series,
                          lb: int, dd_threshold: float) -> pd.DataFrame:
    btc = btc.reindex(weights.index).ffill()
    rmax = btc.rolling(lb, min_periods=20).max()
    dd = btc / rmax - 1
    mask = (dd <= dd_threshold).fillna(False)
    weights = weights.copy()
    weights.loc[mask] = 0.0
    return weights


# ---------------------------------------------------------------------------
# Backtest + metrics
# ---------------------------------------------------------------------------

def backtest(weights: pd.DataFrame, closes: pd.DataFrame,
             cost_bps: float) -> dict:
    bar_ret = closes.pct_change().fillna(0.0)
    pos_y = weights.shift(1).fillna(0.0)
    port_gross = (pos_y * bar_ret).sum(axis=1)
    turnover = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turnover * (cost_bps / 10_000.0) / 2.0
    port_ret = port_gross - cost
    equity = (1.0 + port_ret).cumprod()
    return {
        "ret": port_ret, "equity": equity, "turnover": turnover,
        "weights": weights, "n_holdings": (weights > 0).sum(axis=1),
    }


def metrics(ret, equity, turnover, n_holdings, eval_start: str) -> dict:
    ret = ret.loc[eval_start:]
    equity = equity.loc[eval_start:]
    if len(equity) > 0:
        equity = equity / equity.iloc[0]
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
    avg_turnover_ann = float(turnover.mean()) * TRADING_DAYS
    exposure = float((n_holdings > 0).mean())
    return {
        "sharpe": round(sharpe, 3), "mdd": round(mdd, 4),
        "calmar": round(calmar, 3), "ann_return": round(ann, 4),
        "final_equity": round(float(equity.iloc[-1]), 4),
        "avg_holdings": round(avg_hold, 2),
        "avg_turnover_ann": round(avg_turnover_ann, 2),
        "exposure_pct_days": round(exposure, 3), "n_days": n,
    }


def benchmark_metrics(btc: pd.Series, eval_start: str) -> dict:
    s = btc.loc[eval_start:].copy()
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
    top_recent = out["top_holdings_recent"]
    universe = out["universe"]

    lines = [
        "# Cross-Sectional TSMOM 12-1 — Binance Crypto Universe",
        "",
        f"- Universe: top-{cfg['universe_size']} USDT spot pairs by 24h quote volume "
        f"(stablecoins, wrapped, leveraged 제외)",
        f"  → {len(universe)} 심볼 fetch / {cfg['fetched_size']} 충분한 history",
        f"- Period: {cfg['eval_start']} .. {cfg['end']} (warmup from {cfg['warmup_start']})",
        f"- Strategy: TSMOM 12-1 (long={cfg['long_lb']}, skip={cfg['skip_lb']}), "
        f"top-{cfg['top_n']} equal-weight, rebal every {cfg['rebal_freq']} bars",
        f"- Liquidity: 60d 평균 quote_volume ≥ {cfg['min_quote_vol']:,.0f} USDT",
        f"- Crash guard: BTC 252d drawdown ≤ {cfg['dd_guard']:.0%}",
        f"- Cost: {cfg['cost_bps']} bps round-trip on rebal turnover (Binance taker × 2 + slippage)",
        "",
        "## Strategy vs BTC",
        "",
        "| Metric | Strategy | BTC |",
        "|--------|---------:|----:|",
        f"| Sharpe | {res['sharpe']:.3f} | {bm['sharpe']:.3f} |",
        f"| MDD | {res['mdd']*100:.2f}% | {bm['mdd']*100:.2f}% |",
        f"| Ann. Return | {res['ann_return']*100:.2f}% | {bm['ann_return']*100:.2f}% |",
        f"| Calmar | {res['calmar']:.3f} | — |",
        f"| Final Equity (rebased 1.0) | {res['final_equity']:.3f} | — |",
        f"| Avg Holdings | {res['avg_holdings']:.1f} | — |",
        f"| Avg Annual Turnover | {res['avg_turnover_ann']:.2f}× | — |",
        f"| Exposure | {res['exposure_pct_days']*100:.1f}% | 100% |",
        "",
        "## Universe (current top by 24h volume)",
        "",
        "```",
        ", ".join(universe),
        "```",
        "",
        "## Most Recent Rebal — Top Picks",
        "",
        "| Symbol | Weight |",
        "|--------|-------:|",
    ]
    for row in top_recent:
        lines.append(f"| {row['symbol']} | {row['weight']*100:.2f}% |")
    lines += [
        "",
        "## Caveats",
        "",
        "- **Survivorship + listing bias**: 현재 24h 거래량 기준 top-N → 2020-2024 사이에 listing 된 신생 코인 (TON, SUI, TAO 등) 은 첫 252-bar warmup 후에야 진입 가능. 더 오래된 토큰은 처음부터 풀에 포함. 실거래 결과는 listing date 알고 있는 PIT 데이터 대비 다를 수 있음.",
        "- **Volatility regime**: 크립토는 KRX 보다 변동성·스큐 모두 높음 — 동일 cost_bps 가 KRX 보다 영향이 작음 (% 기준).",
        "- **24/7 시장**: TRADING_DAYS=365 로 annualization. KRX (252) 와 직접 비교 시 환산 필요.",
        "- **Cost**: Binance taker 0.04% × 2 = 8bp + 8bp slippage = 16bp. VIP/maker 적용하면 더 낮음.",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--universe", type=int, default=DEFAULT_UNIVERSE_SIZE,
                   help="Top-N pairs by 24h volume to scan")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--rebal", type=int, default=DEFAULT_REBAL)
    p.add_argument("--long-lb", type=int, default=DEFAULT_LONG_LB)
    p.add_argument("--skip-lb", type=int, default=DEFAULT_SKIP_LB)
    p.add_argument("--min-quote-vol", type=float, default=1e7,
                   help="60d avg quote volume threshold (USDT)")
    p.add_argument("--dd-guard", type=float, default=DEFAULT_DD_GUARD)
    p.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--eval-start", default=BACKTEST_START)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()

    print(f"[universe] top-{args.universe} USDT pairs (filtered)", flush=True)
    universe = fetch_top_universe(args.universe)
    print(f"  {len(universe)} symbols: {', '.join(universe)}", flush=True)

    print(f"[fetch] {args.start} .. {args.end}  (cache={CACHE_DIR})", flush=True)
    panels = fetch_universe(universe, args.start, args.end,
                            refresh=args.refresh, max_workers=args.workers)
    panels = {s: df for s, df in panels.items() if len(df) > DEFAULT_LONG_LB}
    print(f"  {len(panels)} symbols with ≥{DEFAULT_LONG_LB} bars", flush=True)

    closes, quote_vol = build_panels(panels)
    print(f"[panel] shape={closes.shape}, "
          f"date range {closes.index.min().date()} .. {closes.index.max().date()}",
          flush=True)

    print("[signal] computing TSMOM 12-1 weights ...", flush=True)
    weights = cs_tsmom_signals(
        closes, quote_vol,
        long_lb=args.long_lb, skip_lb=args.skip_lb, top_n=args.top_n,
        min_quote_vol=args.min_quote_vol, rebal_freq=args.rebal,
    )

    btc = closes.get("BTCUSDT")
    if btc is None:
        raise RuntimeError("BTCUSDT missing from panel; cannot apply BTC crash guard")
    weights = apply_btc_crash_guard(weights, btc, lb=DEFAULT_LONG_LB,
                                    dd_threshold=args.dd_guard)

    print("[backtest] running daily P&L ...", flush=True)
    bt = backtest(weights, closes, cost_bps=args.cost_bps)
    res = metrics(bt["ret"], bt["equity"], bt["turnover"], bt["n_holdings"],
                  eval_start=args.eval_start)
    bm = benchmark_metrics(btc, eval_start=args.eval_start)

    last_w = weights.iloc[-1]
    last_w = last_w[last_w > 0].sort_values(ascending=False)
    top_recent = [{"symbol": s, "weight": float(w)} for s, w in last_w.items()]

    out = {
        "config": {
            "universe_size": args.universe, "top_n": args.top_n,
            "rebal_freq": args.rebal, "long_lb": args.long_lb,
            "skip_lb": args.skip_lb, "min_quote_vol": args.min_quote_vol,
            "dd_guard": args.dd_guard, "cost_bps": args.cost_bps,
            "warmup_start": args.start, "end": args.end,
            "eval_start": args.eval_start, "fetched_size": len(panels),
        },
        "universe": universe,
        "results": res,
        "benchmark": bm,
        "top_holdings_recent": top_recent,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_report(out, OUT_DIR / "cs_tsmom_crypto_output.json",
                 OUT_DIR / "cs_tsmom_crypto_report.md")
    print(f"\n[result] strategy sharpe={res['sharpe']} mdd={res['mdd']*100:.2f}% "
          f"ann={res['ann_return']*100:.2f}%  vs  BTC sharpe={bm['sharpe']} "
          f"ann={bm['ann_return']*100:.2f}%", flush=True)
    print(f"[result] avg_holdings={res['avg_holdings']} "
          f"turnover_ann={res['avg_turnover_ann']}  "
          f"exposure={res['exposure_pct_days']*100:.1f}%", flush=True)


if __name__ == "__main__":
    main()
