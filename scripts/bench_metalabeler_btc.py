"""Benchmark: MomoBtcV2 메타라벨러 on vs off Sharpe/MDD 비교 (Task #8, Issue #85).

Usage:
    python scripts/bench_metalabeler_btc.py [--data-path PATH] [--output-md PATH]

AC4 기준:
  on Sharpe >= off Sharpe + 0.2  OR  on MDD <= off MDD - 10%p
  미달 시 → 02_implementation.md 에 "momo-btc-v2 메타라벨러 disable 유지" 기록.

환경 실패 시 "실행 환경 실패 로그" 섹션에 기록하고 retry 필요 표시.
데이터 파일 없으면 합성 데이터로 구조 검증만 수행 (AC4 판정 불가).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

WORKTREE = Path(__file__).resolve().parent.parent
REPORT_PATH = WORKTREE / "docs/work/active/000085-meta-labeler-lightgbm/02_implementation.md"

# Ensure src/ is importable when this script is run directly.
_SRC = WORKTREE / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _sharpe(returns: pd.Series, periods_per_year: int = 365 * 96) -> float:
    """Annualised Sharpe (mean/std * sqrt(N))."""
    if returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown as a positive fraction (e.g. 0.25 = 25%)."""
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    return float(-dd.min())


def _sortino(returns: pd.Series, periods_per_year: int = 365 * 96) -> float:
    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(returns.mean() / downside.std() * np.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def _run(ohlcv: pd.DataFrame, metalabeler=None, threshold: float = 0.5) -> dict:
    from backtest.engine import BacktestConfig, run_backtest
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strategy = MomoBtcV2(metalabeler=metalabeler, metalabeler_threshold=threshold)
    config = BacktestConfig(initial_cash=10_000.0, max_drawdown_halt_pct=1.0)
    result = run_backtest(ohlcv, strategy, config)

    equity = result.equity_curve
    rets = equity.pct_change().dropna()
    trades = result.trades

    buy_trades = [t for t in trades if t.get("action") == "buy"]
    sell_trades = [t for t in trades if t.get("action") == "sell"]
    n_trades = min(len(buy_trades), len(sell_trades))

    win_count = 0
    hold_bars_list = []
    for bt, st in zip(buy_trades[:n_trades], sell_trades[:n_trades]):
        if st.get("price", 0) > bt.get("price", 0):
            win_count += 1
        bt_ts = bt.get("ts")
        st_ts = st.get("ts")
        if bt_ts is not None and st_ts is not None:
            try:
                diff = (pd.Timestamp(st_ts) - pd.Timestamp(bt_ts)).total_seconds() / (15 * 60)
                hold_bars_list.append(diff)
            except Exception:
                pass

    win_rate = (win_count / n_trades) if n_trades > 0 else 0.0
    avg_hold_bars = float(np.mean(hold_bars_list)) if hold_bars_list else 0.0

    return {
        "sharpe": _sharpe(rets),
        "sortino": _sortino(rets),
        "mdd": _max_drawdown(equity),
        "win_rate": win_rate,
        "n_trades": n_trades,
        "avg_hold_bars": avg_hold_bars,
        "turnover": n_trades / max(len(ohlcv), 1),
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(off: dict, on: dict, ac4_pass: bool, ac4_reason: str, data_info: str, env_error: str | None = None) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    verdict = "PASS" if ac4_pass else "FAIL — momo-btc-v2 메타라벨러 disable 유지"

    rows = []
    for metric, label in [
        ("sharpe", "Sharpe"),
        ("sortino", "Sortino"),
        ("mdd", "MDD"),
        ("win_rate", "승률"),
        ("avg_hold_bars", "평균 보유바"),
        ("n_trades", "거래수"),
        ("turnover", "Turnover"),
    ]:
        off_val = off.get(metric, "N/A")
        on_val = on.get(metric, "N/A")
        fmt = ".4f" if isinstance(off_val, float) else ""
        rows.append(f"| {label} | {off_val:{fmt}} | {on_val:{fmt}} |")

    table = "\n".join(rows)

    content = f"""# [#85] 메타라벨러 벤치마크 결과

> 생성: {datetime.utcnow().isoformat(timespec='seconds')}Z
> 데이터: {data_info}

## AC4 판정

**{verdict}**

근거: {ac4_reason}

## 성능 비교표

| 지표 | OFF (baseline) | ON (메타라벨러) |
|------|---------------|----------------|
{table}

## 판정 기준

- on Sharpe ≥ off Sharpe + 0.2  **OR**  on MDD ≤ off MDD − 10%p
- 미달 시: "momo-btc-v2 메타라벨러 disable 유지" — 별도 후속 이슈로 원인 분석
"""

    if env_error:
        content += f"""
## 실행 환경 실패 로그

```
{env_error}
```

> retry 필요: 위 오류 해결 후 `python scripts/bench_metalabeler_btc.py` 재실행
"""

    REPORT_PATH.write_text(content, encoding="utf-8")
    print(f"Report written to: {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _make_synthetic_ohlcv(n: int = 5000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.standard_normal(n) * 0.3)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="MetaLabeler on/off benchmark")
    parser.add_argument("--data-path", type=Path, default=None, help="BTC 15m OHLCV CSV/Parquet path")
    parser.add_argument("--model-path", type=Path, default=None, help="Trained MetaLabeler dir path")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output-md", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    # --- check imports ---
    env_error = None
    try:
        import lightgbm  # noqa: F401
        from ml.meta_labeler import MetaLabeler
    except ImportError as e:
        env_error = traceback.format_exc()
        _write_report(
            off={}, on={},
            ac4_pass=False,
            ac4_reason="환경 오류 — LightGBM 또는 ml.meta_labeler import 실패",
            data_info="N/A",
            env_error=env_error,
        )
        print(f"[FAIL] Import error: {e}", file=sys.stderr)
        return 1

    # --- load or synthesize data ---
    if args.data_path and args.data_path.exists():
        suffix = args.data_path.suffix.lower()
        if args.data_path.is_dir():
            # Lake partition layout: <dir>/ohlcv/freq=*/year=*/month=*/symbol=*/part-*.parquet
            shards = sorted(args.data_path.glob("ohlcv/freq=*/year=*/month=*/symbol=*/part-*.parquet"))
            if not shards:
                shards = sorted(args.data_path.rglob("*.parquet"))
            frames = [pd.read_parquet(s) for s in shards]
            ohlcv = pd.concat(frames, axis=0, ignore_index=True)
            if "ts" in ohlcv.columns:
                ohlcv["ts"] = pd.to_datetime(ohlcv["ts"], utc=True)
                ohlcv = ohlcv.set_index("ts").sort_index()
            ohlcv = ohlcv[~ohlcv.index.duplicated(keep="first")]
            data_info = f"{args.data_path} lake ({len(ohlcv)} bars from {len(shards)} shards)"
        elif suffix in (".parquet",):
            ohlcv = pd.read_parquet(args.data_path)
            if "ts" in ohlcv.columns:
                ohlcv["ts"] = pd.to_datetime(ohlcv["ts"], utc=True)
                ohlcv = ohlcv.set_index("ts").sort_index()
            data_info = f"{args.data_path} ({len(ohlcv)} bars)"
        else:
            ohlcv = pd.read_csv(args.data_path, index_col=0, parse_dates=True)
            data_info = f"{args.data_path} ({len(ohlcv)} bars)"
    else:
        print("[WARN] No data path provided or file not found - using synthetic data (5000 bars). AC4 inconclusive.", file=sys.stderr)
        ohlcv = _make_synthetic_ohlcv()
        data_info = "synthetic 5000 bars (AC4 inconclusive - real data needed)"
        env_error = (env_error or "") + "\n[DATA] No real data - synthetic only, retry required for AC4."

    # --- load or skip metalabeler ---
    metalabeler = None
    if args.model_path and args.model_path.exists():
        from ml.meta_labeler import MetaLabeler
        metalabeler = MetaLabeler.load(args.model_path)
        print(f"[INFO] MetaLabeler loaded from {args.model_path}")
    else:
        print("[WARN] No model path provided — on-path will use untrained stub (zeros). AC4 판정 불가.", file=sys.stderr)
        # Stub that always returns 0.0 — all signals rejected → no trades
        class _ZeroStub:
            def win_probability(self, X):
                return np.zeros(len(X))
        metalabeler = _ZeroStub()

    # --- run ---
    print("[INFO] Running OFF backtest...")
    try:
        off_metrics = _run(ohlcv, metalabeler=None)
    except Exception:
        err = traceback.format_exc()
        _write_report({}, {}, False, "OFF 백테스트 실패", data_info, err)
        print(f"[FAIL] OFF backtest error:\n{err}", file=sys.stderr)
        return 1

    print("[INFO] Running ON backtest...")
    try:
        on_metrics = _run(ohlcv, metalabeler=metalabeler, threshold=args.threshold)
    except Exception:
        err = traceback.format_exc()
        _write_report(off_metrics, {}, False, "ON 백테스트 실패", data_info, err)
        print(f"[FAIL] ON backtest error:\n{err}", file=sys.stderr)
        return 1

    # --- AC4 판정 ---
    sharpe_delta = on_metrics["sharpe"] - off_metrics["sharpe"]
    mdd_delta = off_metrics["mdd"] - on_metrics["mdd"]  # positive = improvement
    ac4_pass = (sharpe_delta >= 0.2) or (mdd_delta >= 0.10)

    if ac4_pass:
        ac4_reason = (
            f"Sharpe delta={sharpe_delta:+.4f} (≥0.2)" if sharpe_delta >= 0.2
            else f"MDD delta={mdd_delta:+.4f} (≥0.10)"
        )
    else:
        ac4_reason = (
            f"Sharpe delta={sharpe_delta:+.4f} (<0.2) AND MDD delta={mdd_delta:+.4f} (<0.10) "
            "→ momo-btc-v2 메타라벨러 disable 유지"
        )

    _write_report(off_metrics, on_metrics, ac4_pass, ac4_reason, data_info, env_error or None)

    print(f"\n=== AC4 판정: {'PASS' if ac4_pass else 'FAIL'} ===")
    print(f"  OFF Sharpe: {off_metrics['sharpe']:.4f}  |  ON Sharpe: {on_metrics['sharpe']:.4f}  |  delta: {sharpe_delta:+.4f}")
    print(f"  OFF MDD:    {off_metrics['mdd']:.4f}  |  ON MDD:    {on_metrics['mdd']:.4f}  |  delta: {mdd_delta:+.4f}")

    return 0 if ac4_pass else 2


if __name__ == "__main__":
    sys.exit(main())
