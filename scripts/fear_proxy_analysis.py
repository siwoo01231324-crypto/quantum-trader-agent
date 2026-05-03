"""extreme_fear_threshold 가격 프록시 검증 스크립트 (#121).

BTC 일봉 가격 기반 프록시(current_price / rolling_max(252))와
Alternative.me Crypto Fear & Greed Index를 비교해 임계값별 precision/recall을 산출한다.

Usage:
    python scripts/fear_proxy_analysis.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

# ── 설정 ──────────────────────────────────────────────────────────────────────
START = "2023-01-01"
END   = "2026-04-27"
WINDOW = 252          # rolling_max window (거래일 기준 ≈1년)
THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
FEAR_CUTOFF = 25      # Alternative.me 지수 ≤25 → "Extreme Fear" 구간으로 정의

# ── 1. BTC 일봉 데이터 (Binance Vision) ───────────────────────────────────────

def _fetch_binance_vision_daily(symbol: str, start: str, end: str) -> pd.Series:
    """Binance Vision monthly zip → 일봉 종가 Series (UTC date index)."""
    from io import BytesIO
    import zipfile

    base = "https://data.binance.vision/data/spot/monthly/klines"
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    months: list[tuple[int, int]] = []
    cur = datetime(start_dt.year, start_dt.month, 1, tzinfo=timezone.utc)
    while cur <= end_dt:
        months.append((cur.year, cur.month))
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            cur = datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)

    frames: list[pd.DataFrame] = []
    for year, month in months:
        fname = f"{symbol}-1d-{year:04d}-{month:02d}"
        url   = f"{base}/{symbol}/1d/{fname}.zip"
        resp  = requests.get(url, timeout=60)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            with zf.open(f"{fname}.csv") as f:
                df = pd.read_csv(f, header=None, usecols=[0, 4],
                                 names=["open_time_ms", "close"])
        # ms vs μs 정규화
        if not df.empty and df["open_time_ms"].iloc[0] > 1e14:
            df["open_time_ms"] //= 1000
        df["date"] = pd.to_datetime(df["open_time_ms"], unit="ms", utc=True).dt.normalize()
        frames.append(df[["date", "close"]])

    if not frames:
        raise RuntimeError("Binance Vision: no data returned")

    merged = pd.concat(frames).drop_duplicates("date").sort_values("date")
    mask = (merged["date"] >= pd.Timestamp(start, tz="UTC")) & \
           (merged["date"] <= pd.Timestamp(end,   tz="UTC"))
    series = merged.loc[mask].set_index("date")["close"].astype(float)
    series.name = "btc_close"
    return series


# ── 2. Alternative.me Fear & Greed Index ──────────────────────────────────────

def _fetch_fear_greed(limit: int = 1200) -> pd.Series:
    """Alternative.me API → 공포·탐욕 지수 Series (UTC date index)."""
    url  = f"https://api.alternative.me/fng/?limit={limit}&format=json"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()["data"]       # newest first
    rows = [{"date": pd.Timestamp(int(r["timestamp"]), unit="s", tz="UTC").normalize(),
             "fgi":  int(r["value"])} for r in data]
    df = pd.DataFrame(rows).drop_duplicates("date").sort_values("date")
    series = df.set_index("date")["fgi"]
    series.name = "fgi"
    return series


# ── 3. 가격 프록시 계산 ────────────────────────────────────────────────────────

def compute_proxy(close: pd.Series, window: int = WINDOW) -> pd.Series:
    rolling_max = close.rolling(window=window, min_periods=1).max()
    proxy = (close / rolling_max).clip(0.0, 1.0)
    proxy.name = "price_proxy"
    return proxy


# ── 4. Precision / Recall 계산 ────────────────────────────────────────────────

def precision_recall(proxy: pd.Series, fgi: pd.Series,
                     threshold: float, fear_cutoff: int = FEAR_CUTOFF
                     ) -> dict[str, float]:
    """공통 날짜 기준 confusion matrix 지표 산출.

    Positive = 실제 공포 구간 (fgi <= fear_cutoff).
    Predicted positive = proxy < threshold (buy 차단).
    """
    aligned = pd.DataFrame({"proxy": proxy, "fgi": fgi}).dropna()
    actual_pos  = aligned["fgi"] <= fear_cutoff
    pred_pos    = aligned["proxy"] < threshold

    tp = int((pred_pos & actual_pos).sum())
    fp = int((pred_pos & ~actual_pos).sum())
    fn = int((~pred_pos & actual_pos).sum())
    tn = int((~pred_pos & ~actual_pos).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall    = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    f1        = (2 * precision * recall / (precision + recall)
                 if not (np.isnan(precision) or np.isnan(recall) or (precision + recall) == 0)
                 else float("nan"))
    blocked_buy_pct = (tp + fp) / len(aligned) * 100 if len(aligned) > 0 else float("nan")

    return {
        "threshold": threshold,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall":    round(recall, 4),
        "f1":        round(f1, 4),
        "blocked_buy_pct": round(blocked_buy_pct, 2),
        "n_days": len(aligned),
        "n_fear_days": int(actual_pos.sum()),
    }


# ── 5. 상관계수 ────────────────────────────────────────────────────────────────

def correlation(proxy: pd.Series, fgi: pd.Series) -> dict[str, float]:
    aligned = pd.DataFrame({"proxy": proxy, "fgi": fgi}).dropna()
    pearson  = aligned["proxy"].corr(aligned["fgi"])
    spearman = aligned["proxy"].rank().corr(aligned["fgi"].rank())
    return {
        "pearson":  round(float(pearson),  4),
        "spearman": round(float(spearman), 4),
        "n_days":   len(aligned),
    }


# ── 6. 백테스트 sensitivity (MDD 기반 단순 시뮬레이션) ───────────────────────

def simulate_threshold(close: pd.Series, proxy: pd.Series,
                       threshold: float) -> dict[str, float]:
    """임계값별 단순 Buy-and-Hold 대비 MDD·Sharpe 시뮬레이션.

    전략: 매일 open에 매수 가능할 때 진입, 다음날 close에 청산 (1-bar hold).
    차단 조건: proxy(t-1) < threshold → 당일 매수 차단.
    """
    returns = close.pct_change().fillna(0.0)
    blocked = proxy.shift(1) < threshold   # 전날 프록시로 오늘 차단 여부 결정

    strat_returns = returns.copy()
    strat_returns[blocked] = 0.0           # 차단일 수익률 = 0 (현금 보유)

    # equity curve
    equity = (1 + strat_returns).cumprod()
    peak   = equity.cummax()
    dd     = (equity - peak) / peak
    mdd    = float(dd.min()) * 100         # %

    annual_factor = 365                    # BTC 365일 거래
    sharpe = float(
        strat_returns.mean() / strat_returns.std() * np.sqrt(annual_factor)
        if strat_returns.std() > 0 else float("nan")
    )
    total_return = float((equity.iloc[-1] - 1) * 100)
    n_blocked    = int(blocked.sum())
    n_total      = len(blocked)

    return {
        "threshold":       threshold,
        "sharpe":          round(sharpe, 3),
        "mdd_pct":         round(mdd, 2),
        "total_return_pct": round(total_return, 2),
        "n_blocked":       n_blocked,
        "blocked_ratio_pct": round(n_blocked / n_total * 100, 2),
        "n_trades":        int((~blocked).sum()),
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== #121 extreme_fear_threshold 가격 프록시 검증 ===\n")

    print(f"[1/4] BTC 일봉 수집 ({START} ~ {END}) …")
    close = _fetch_binance_vision_daily("BTCUSDT", START, END)
    print(f"      {len(close)} 일 수집 완료. 기간: {close.index[0].date()} ~ {close.index[-1].date()}")

    print("[2/4] Fear & Greed Index 수집 (Alternative.me) …")
    fgi = _fetch_fear_greed(limit=1500)
    start_ts = pd.Timestamp(START, tz="UTC")
    end_ts   = pd.Timestamp(END,   tz="UTC")
    fgi = fgi[(fgi.index >= start_ts) & (fgi.index <= end_ts)]
    print(f"      {len(fgi)} 일 수집 완료. 기간: {fgi.index[0].date()} ~ {fgi.index[-1].date()}")

    print("[3/4] 가격 프록시 계산 …")
    proxy = compute_proxy(close, window=WINDOW)

    corr = correlation(proxy, fgi)
    print(f"\n--- 상관계수 (n={corr['n_days']}일) ---")
    print(f"  Pearson  : {corr['pearson']}")
    print(f"  Spearman : {corr['spearman']}")

    print(f"\n--- Precision / Recall (공포 기준: FGI ≤ {FEAR_CUTOFF}) ---")
    print(f"{'Threshold':>10} | {'Precision':>9} | {'Recall':>6} | {'F1':>6} | "
          f"{'차단비율%':>8} | {'TP':>4} | {'FP':>4} | {'FN':>4} | {'TN':>4} | {'Fear일수':>8}")
    print("-" * 100)
    pr_rows = []
    for t in THRESHOLDS:
        r = precision_recall(proxy, fgi, t)
        pr_rows.append(r)
        print(f"  {t:>8.2f} | {r['precision']:>9.4f} | {r['recall']:>6.4f} | "
              f"{r['f1']:>6.4f} | {r['blocked_buy_pct']:>8.2f} | "
              f"{r['tp']:>4} | {r['fp']:>4} | {r['fn']:>4} | {r['tn']:>4} | "
              f"{r['n_fear_days']:>8}")

    print(f"\n[4/4] 백테스트 민감도 분석 (buy-and-hold 1-bar 시뮬레이션) …")
    print(f"{'Threshold':>10} | {'Sharpe':>7} | {'MDD%':>7} | {'TotalRet%':>10} | "
          f"{'차단일수':>8} | {'차단비율%':>9} | {'거래수':>6}")
    print("-" * 80)
    bt_rows = []
    for t in THRESHOLDS:
        r = simulate_threshold(close, proxy, t)
        bt_rows.append(r)
        print(f"  {t:>8.2f} | {r['sharpe']:>7.3f} | {r['mdd_pct']:>7.2f} | "
              f"{r['total_return_pct']:>10.2f} | {r['n_blocked']:>8} | "
              f"{r['blocked_ratio_pct']:>9.2f} | {r['n_trades']:>6}")

    # Buy-and-hold baseline
    bh_ret = close.pct_change().fillna(0.0)
    bh_equity = (1 + bh_ret).cumprod()
    bh_peak = bh_equity.cummax()
    bh_dd = (bh_equity - bh_peak) / bh_peak
    bh_mdd = float(bh_dd.min()) * 100
    bh_sharpe = float(bh_ret.mean() / bh_ret.std() * np.sqrt(365)) if bh_ret.std() > 0 else float("nan")
    bh_total = float((bh_equity.iloc[-1] - 1) * 100)
    print(f"  {'B&H':>8}  | {bh_sharpe:>7.3f} | {bh_mdd:>7.2f} | {bh_total:>10.2f} | "
          f"{'N/A':>8} | {'N/A':>9} | {'N/A':>6}   ← baseline")

    # ── 권고 임계값 산출 ────────────────────────────────────────────────────
    print("\n=== 권고 임계값 분석 ===")
    best_pr = max(pr_rows, key=lambda r: r["f1"] if not np.isnan(r["f1"]) else -1)
    print(f"F1 최대 임계값: {best_pr['threshold']} (F1={best_pr['f1']}, "
          f"Precision={best_pr['precision']}, Recall={best_pr['recall']})")

    print(f"\nPearson 상관: {corr['pearson']}")
    print(f"Spearman 상관: {corr['spearman']}")

    # ── 결과 요약 출력 (문서용) ──────────────────────────────────────────────
    print("\n=== 문서용 결과 JSON ===")
    import json
    result = {
        "correlation": corr,
        "precision_recall": pr_rows,
        "backtest_sensitivity": bt_rows,
        "baseline_bh": {
            "sharpe": round(bh_sharpe, 3),
            "mdd_pct": round(bh_mdd, 2),
            "total_return_pct": round(bh_total, 2),
        },
        "recommended_threshold": best_pr["threshold"],
        "fear_cutoff_used": FEAR_CUTOFF,
        "window": WINDOW,
        "period": {"start": START, "end": END},
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))

    return result


if __name__ == "__main__":
    main()
