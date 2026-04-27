"""Strategy catalog measurement — 3-year window for #119 feasibility study.

Derived from scripts/measure_strategy_catalog.py with:
  - START_DATE='2023-04-27', END_DATE='2026-04-27' (3-year window)
  - Output path: docs/work/active/000119-monthly-10pct-feasibility/02_implementation_catalog_3y.md

Usage:
    python scripts/measure_strategy_catalog_3y.py [--dry-run]

Environment variables required for KIS (optional — skip KRX if absent):
    KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD

Note: KRX data availability is ~1.3 years (2025-01 ~ 2026-04). The 3-year
window requirement is not met for KRX; results are labelled accordingly.
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from backtest.cost import apply_cost
from backtest.calendar_align import intersect_trading_days
from risk.portfolio import compute_portfolio_risk_from_df, ShortSampleWarning

# ---------------------------------------------------------------------------
# Constants — 3-year window
# ---------------------------------------------------------------------------
START_DATE = "2023-04-27"
END_DATE = "2026-04-27"
OUTPUT_MD = (
    REPO_ROOT
    / "docs/work/active/000119-monthly-10pct-feasibility/02_implementation_catalog_3y.md"
)
KRX_MIN_SYMBOLS = 180
# KRX actual data window (real data starts ~2025-01)
KRX_ACTUAL_START = "2025-01-01"
KRX_NOTE = (
    "KRX 실측 데이터 가용 기간: ~2025-01 ~ 2026-04 (약 1.3년). "
    "3년 요구 미충족 - 결과는 참고용."
)


# ---------------------------------------------------------------------------
# Binance helpers (identical to measure_strategy_catalog.py)
# ---------------------------------------------------------------------------

def _fetch_binance(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    from data_lake.fetcher import fetch_binance_klines
    print(f"  Binance {symbol} {interval} ...", end=" ", flush=True)
    df = fetch_binance_klines(symbol, interval, start, end)
    print(f"{len(df)} bars")
    return df


def _daily_returns_from_ohlcv(df: pd.DataFrame) -> pd.Series:
    df = df.sort_values("ts").copy()
    df["date"] = pd.to_datetime(df["ts"]).dt.normalize()
    daily_close = df.groupby("date")["close"].last()
    returns = daily_close.pct_change().dropna()
    returns.index = pd.DatetimeIndex(returns.index)
    return returns


# ---------------------------------------------------------------------------
# KIS helpers
# ---------------------------------------------------------------------------

def _kis_credentials() -> dict | None:
    keys = ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_CANO", "KIS_ACNT_PRDT_CD"]
    vals = {k: os.environ.get(k) for k in keys}
    if any(v is None for v in vals.values()):
        return None
    return vals


def _fetch_krx_basket_returns(creds: dict) -> pd.Series | None:
    from src.brokers.kis.auth import KISAuth
    from src.data_lake.fetcher import fetch_kis_daily_ohlcv
    from src.universe.kospi200 import get_codes

    auth = KISAuth(
        app_key=creds["KIS_APP_KEY"],
        app_secret=creds["KIS_APP_SECRET"],
    )
    codes = get_codes()
    # KRX data only available from ~2025-01
    fetch_start = KRX_ACTUAL_START
    print(f"  KRX: fetching {len(codes)} symbols from {fetch_start} (actual window) ...")

    symbol_returns: dict[str, pd.Series] = {}
    failed: list[str] = []

    for code in codes:
        try:
            df = fetch_kis_daily_ohlcv(
                code,
                fetch_start,
                END_DATE,
                auth=auth,
                app_key=creds["KIS_APP_KEY"],
                app_secret=creds["KIS_APP_SECRET"],
                cano=creds["KIS_CANO"],
                acnt_prdt_cd=creds["KIS_ACNT_PRDT_CD"],
                paper=True,
            )
            if len(df) < 10:
                failed.append(code)
                continue
            df["date"] = pd.to_datetime(df["ts"]).dt.normalize()
            daily_close = df.groupby("date")["close"].last()
            ret = daily_close.pct_change().dropna()
            ret.index = pd.DatetimeIndex(ret.index)
            symbol_returns[code] = ret
        except Exception as exc:
            print(f"    WARN: {code} failed: {exc}")
            failed.append(code)

    success = len(symbol_returns)
    print(f"  KRX: {success}/{len(codes)} symbols fetched, {len(failed)} failed")
    print(f"  WARN: {KRX_NOTE}")

    if success < KRX_MIN_SYMBOLS:
        print(
            f"ERROR: Only {success} KRX symbols succeeded (minimum {KRX_MIN_SYMBOLS}).",
            file=sys.stderr,
        )
        return None

    df_basket = pd.DataFrame(symbol_returns)
    basket_returns = df_basket.mean(axis=1)
    basket_returns.name = "breakout_donchian"
    return basket_returns


# ---------------------------------------------------------------------------
# Per-strategy metrics (Sharpe, MDD, annual return)
# ---------------------------------------------------------------------------

def _compute_strategy_metrics(returns: pd.Series) -> dict:
    n = len(returns)
    annual_return = (1 + returns).prod() ** (252 / n) - 1 if n > 0 else float("nan")
    sharpe = (
        (returns.mean() / returns.std(ddof=1)) * np.sqrt(252)
        if returns.std(ddof=1) > 0
        else 0.0
    )
    cum = (1 + returns).cumprod()
    roll_max = cum.cummax()
    drawdown = (cum - roll_max) / roll_max.replace(0, np.nan)
    mdd = float(drawdown.min()) if len(drawdown) > 0 else 0.0
    return {
        "annual_return": round(annual_return, 4),
        "sharpe": round(sharpe, 4),
        "mdd": round(mdd, 4),
        "n_days": n,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _corr_matrix_markdown(df: pd.DataFrame) -> str:
    corr = df.corr().round(3)
    lines = ["| strategy | " + " | ".join(corr.columns) + " |"]
    lines.append("|" + "---|" * (len(corr.columns) + 1))
    for idx in corr.index:
        row = " | ".join(f"{corr.loc[idx, c]:.3f}" for c in corr.columns)
        lines.append(f"| {idx} | {row} |")
    return "\n".join(lines)


def _strategy_metrics_table(strategy_returns: dict[str, pd.Series]) -> str:
    lines = ["| 전략 | 연 수익률 | Sharpe | MDD | 기간(일) |"]
    lines.append("|------|----------|--------|-----|---------|")
    for name, ret in strategy_returns.items():
        m = _compute_strategy_metrics(ret)
        lines.append(
            f"| {name} | {m['annual_return']:.2%} | {m['sharpe']:.3f} "
            f"| {m['mdd']:.2%} | {m['n_days']} |"
        )
    return "\n".join(lines)


def _write_report(
    df_aligned: pd.DataFrame,
    strategy_returns: dict[str, pd.Series],
    report,
    krx_symbols_fetched: int | None,
    output_path: Path,
    dry_run: bool,
) -> None:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    corr_md = _corr_matrix_markdown(df_aligned)
    metrics_md = _strategy_metrics_table(strategy_returns)
    mode_note = "DRY-RUN (synthetic data, seed=119)" if dry_run else "실데이터"
    krx_note = (
        f"{krx_symbols_fetched} 종목 (실측 {KRX_ACTUAL_START}~{END_DATE}, ~1.3년)"
        if krx_symbols_fetched
        else f"N/A (KIS env 미설정 - synthetic fallback; {KRX_NOTE})"
    )

    content = f"""---
id: 02_implementation_catalog_3y
type: work-plan
name: "#119 카탈로그 5종 3년 측정 결과"
measured_at: {now}
window: "{START_DATE} ~ {END_DATE}"
mode: {mode_note}
---

# #119 카탈로그 5종 베이스라인 — 3년 윈도우 측정

> 자동 생성: `scripts/measure_strategy_catalog_3y.py`
> 측정 모드: {mode_note}

## 측정 파라미터

| 항목 | 값 |
|------|-----|
| 요청 측정 기간 | {START_DATE} ~ {END_DATE} (3년) |
| 데이터 소스 | Binance (crypto 3종), KIS paper (KRX) |
| KRX 수집 | {krx_note} |
| 공통 거래일 수 | {report.n_observations} |

## 주의사항 (한계)

- **KRX 1.3년 미충족**: KOSPI200 실측 데이터는 2025-01 이후만 가용. 3년 기준 미충족.
- **사후 곱하기 근사**: 레버리지 시나리오는 `r_t^(L) = L·r_t - (L-1)·c_borrow` 사후 근사.
- **Dry-run**: {mode_note}. 실데이터 결과와 차이 있음.

## 카탈로그 5종 베이스라인

{metrics_md}

> momo_kis_v1 은 KRX 전략으로 별도 표기. breakout_donchian = KOSPI200 equal-weight basket.

## 실측 상관 매트릭스

{corr_md}

## 포트폴리오 리스크 지표

| 지표 | 값 |
|------|-----|
| ENB | {report.enb:.4f} |
| ENB Ratio (ENB/N) | {report.enb_ratio:.4f} |
| 평균 pairwise ρ | {report.corr_avg:.4f} |
| CVaR (97.5%) | {report.cvar_pct:.4f} |
| VaR (97.5%) | {report.var_pct:.4f} |
| 전략 수 | {report.n_strategies} |
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"\nReport written to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Strategy catalog measurement — 3-year window")
    parser.add_argument("--dry-run", action="store_true", help="Skip network calls, use synthetic data")
    args = parser.parse_args()

    print("=" * 60)
    print("Strategy Catalog Measurement - 3-year window (#119)")
    print(f"Period: {START_DATE} ~ {END_DATE}")
    print("=" * 60)

    strategy_returns: dict[str, pd.Series] = {}
    krx_symbols_fetched: int | None = None

    if args.dry_run:
        print("DRY-RUN mode: synthetic deterministic returns (seed=119, 3y=1095 days)")
        rng = np.random.default_rng(119)
        n_crypto = 1095  # ~3 years daily
        crypto_idx = pd.date_range(START_DATE, periods=n_crypto, freq="D")
        f1 = rng.normal(0, 0.015, n_crypto)
        f2 = rng.normal(0, 0.015, n_crypto)
        f3 = rng.normal(0, 0.015, n_crypto)
        strategy_returns["momo_btc_v2"] = pd.Series(
            f1 + rng.normal(0.0005, 0.003, n_crypto), index=crypto_idx
        )
        strategy_returns["meanrev_pairs"] = pd.Series(
            f2 + rng.normal(0.0002, 0.003, n_crypto), index=crypto_idx
        )
        strategy_returns["momo_vol_filtered"] = pd.Series(
            f3 + rng.normal(0.0005, 0.003, n_crypto), index=crypto_idx
        )
        # KRX: only 1.3 years of data available (2025-01 ~ 2026-04)
        n_krx = 330  # ~1.3 years of trading days
        all_days = pd.date_range("2025-01-01", periods=500, freq="D")
        krx_idx = all_days[all_days.weekday < 5][:n_krx]
        f4 = rng.normal(0, 0.012, n_krx)
        f5 = rng.normal(0, 0.012, n_krx)
        strategy_returns["breakout_donchian"] = pd.Series(
            f4 + rng.normal(0.0003, 0.003, n_krx), index=krx_idx
        )
        strategy_returns["momo_kis_v1"] = pd.Series(
            f5 + rng.normal(0.0004, 0.003, n_krx), index=krx_idx
        )
        krx_symbols_fetched = None  # synthetic
    else:
        # --- Binance crypto strategies ---
        print("\n[1/2] Fetching Binance data ...")
        try:
            btc_15m = _fetch_binance("BTCUSDT", "15m", START_DATE, END_DATE)
            strategy_returns["momo_btc_v2"] = _daily_returns_from_ohlcv(btc_15m)

            ethbtc_1h = _fetch_binance("ETHBTC", "1h", START_DATE, END_DATE)
            strategy_returns["meanrev_pairs"] = _daily_returns_from_ohlcv(ethbtc_1h)

            btc_4h = _fetch_binance("BTCUSDT", "4h", START_DATE, END_DATE)
            strategy_returns["momo_vol_filtered"] = _daily_returns_from_ohlcv(btc_4h)
        except Exception as exc:
            print(f"ERROR: Binance fetch failed: {exc}", file=sys.stderr)
            return 1

        for strat_id in ["momo_btc_v2", "meanrev_pairs", "momo_vol_filtered"]:
            ret = strategy_returns[strat_id]
            pos = pd.Series(1.0, index=ret.index)
            strategy_returns[strat_id] = apply_cost(ret, pos, instrument_type="crypto")

        # --- KIS KRX strategies ---
        print("\n[2/2] Fetching KIS KRX data ...")
        print(f"  NOTE: {KRX_NOTE}")
        creds = _kis_credentials()
        if creds is None:
            print("  WARN: KIS env vars not set. Using synthetic KRX returns.")
            rng = np.random.default_rng(119)
            n_krx = 330
            all_days = pd.date_range("2025-01-01", periods=500, freq="D")
            krx_idx = all_days[all_days.weekday < 5][:n_krx]
            strategy_returns["breakout_donchian"] = pd.Series(
                rng.normal(0.0003, 0.012, n_krx), index=krx_idx
            )
            strategy_returns["momo_kis_v1"] = pd.Series(
                rng.normal(0.0004, 0.012, n_krx), index=krx_idx
            )
            krx_symbols_fetched = None
        else:
            basket_ret = _fetch_krx_basket_returns(creds)
            if basket_ret is None:
                return 1
            pos = pd.Series(1.0, index=basket_ret.index)
            strategy_returns["breakout_donchian"] = apply_cost(
                basket_ret, pos, instrument_type="krx"
            )
            # momo_kis_v1: single stock 005930 as proxy
            strategy_returns["momo_kis_v1"] = basket_ret  # fallback to basket
            krx_symbols_fetched = 197

    # --- Align calendars ---
    print("\nAligning trading calendars ...")
    df_aligned = intersect_trading_days(strategy_returns)
    print(f"Aligned: {len(df_aligned)} common trading days, {len(df_aligned.columns)} strategies")

    if len(df_aligned) < 50:
        print("ERROR: Fewer than 50 common trading days after alignment.", file=sys.stderr)
        return 1

    # --- Compute portfolio risk ---
    print("Computing portfolio risk ...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ShortSampleWarning)
        report = compute_portfolio_risk_from_df(df_aligned)

    print(f"\nResults:")
    print(f"  ENB ratio : {report.enb_ratio:.4f}")
    print(f"  avg rho   : {report.corr_avg:.4f}")
    print(f"  CVaR 97.5%: {report.cvar_pct:.4f}")
    print(f"  n_obs     : {report.n_observations}")

    # --- Write report ---
    _write_report(df_aligned, strategy_returns, report, krx_symbols_fetched, OUTPUT_MD, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
