"""cand-c-2026-05-20-live-* 4종 단타(scalping) 재파라미터 sweep (2026-05-21).

가설: cand-c-* 원본 backtest 의 넓은 TP(+6%)/SL(-3%) 밴드 → 라이브에서 안 팔리고
long-hold. 10x leverage 가정 + 좁은 TP(0.5~2% price-move) 로 좁히면 단타 알파 가능?

평가 지표 (Sharpe 단독 신뢰 금지 — 이전 finding: bench Sharpe 3.0+ 면서 PF<1 케이스):
  - Profit Factor > 1.0 (필수, 게임 불가능)
  - expectancy per trade > 0 (필수)
  - trades >= 1000 (표본)

각 cand-c 전략 클래스는 원본 live_*.py 재사용 (production.yaml 의 cand-c entry 가
같은 class 가리킴). 인스턴스 속성 override 로 stop_loss_pct / take_profit_pct /
trailing_stop_pct 만 변형.

사용:
    # BTC-only fast probe (1 symbol, ~3분 32조합)
    python scripts/sweep_candc_scalping.py --universe btc-only --output reports/sweep_candc_scalping_btc.json

    # 30-universe full (~2.5시간 32조합)
    python scripts/sweep_candc_scalping.py --universe full --output reports/sweep_candc_scalping_1y.json

    # 5y validation finalists
    python scripts/sweep_candc_scalping.py --universe full --period 5y --max-combos 3 --output reports/sweep_candc_scalping_5y.json
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_scanner")
logger = logging.getLogger("sweep_candc_scalping")


# cand-c-* 의 base class. production.yaml 의 cand-c entry class = 이 4개.
CAND_C_STRATEGIES = [
    "live_rsi_oversold_volume_spike",
    "live_breakout_with_atr_stop",
    "live_bb_lower_bounce",
    "live_oversold_with_divergence",
]


# Iteration 2 grid (wider, 단타 ↔ 원본 사이 중간지대). Iteration 1 (sl 0.5-0.8 /
# tp 1-1.5 / tr 0-0.5%) 0/32 PASS — noise 진폭(~0.1%) + 20bp cost 가 좁은 TP 잠식.
# 더 wider 영역에서 raw 신호 알파 잡히는지 재시험.
GRID_STOP_LOSS = [0.015, 0.025]      # 1.5%, 2.5% — 노이즈 충분히 흡수
GRID_TAKE_PROFIT = [0.030, 0.060]    # 3%, 6% — 원본 영역까지
GRID_TRAILING = [None, 0.020]        # OFF or 2% — 1m 단타엔 너무 좁으면 whipsaw


def _combos() -> list[dict]:
    """Cartesian grid. 2 × 2 × 2 = 8 combos per strategy (32 total across 4 strats)."""
    out: list[dict] = []
    for sl in GRID_STOP_LOSS:
        for tp in GRID_TAKE_PROFIT:
            for trail in GRID_TRAILING:
                tag = f"sl{sl*100:.1f}/tp{tp*100:.1f}/tr{trail*100 if trail else 0:.1f}"
                out.append({
                    "stop_loss_pct": sl,
                    "take_profit_pct": tp,
                    "trailing_stop_pct": trail,
                    "_tag": tag,
                })
    return out


def _safe_float(v) -> float | None:
    """NaN → None, JSON 직렬화 안전."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _edge(metrics: dict) -> dict:
    """bench._aggregate 결과에서 PF/expectancy/payoff 추출. Sharpe 는 보조 only."""
    n = int(metrics.get("trades", 0))
    w = float(metrics.get("win_rate", 0.0))
    P = float(metrics.get("realized_pnl_profit", 0.0))
    L = float(metrics.get("realized_pnl_loss", 0.0))
    nw = round(n * w)
    nl = n - nw
    avg_w = (P / nw) if nw else 0.0
    avg_l = (L / nl) if nl else 0.0
    pf = (P / abs(L)) if L else float("inf")
    payoff = (avg_w / abs(avg_l)) if avg_l else float("inf")
    exp = ((P + L) / n) if n else 0.0
    return {
        "trades": n, "win_rate": w, "avg_win": _safe_float(avg_w),
        "avg_loss": _safe_float(avg_l), "payoff": _safe_float(payoff),
        "profit_factor": _safe_float(pf), "expectancy": _safe_float(exp),
        "sharpe_bench": _safe_float(metrics.get("sharpe", 0.0)),
        "mdd_bench": _safe_float(metrics.get("mdd", 0.0)),
        "ann_bench": _safe_float(metrics.get("ann_return", 0.0)),
    }


def _select_panels(all_panels: dict, universe: str) -> dict:
    """btc-only 모드는 BTCUSDT 1개만; full 은 전부."""
    if universe == "btc-only":
        return {k: v for k, v in all_panels.items() if k == "BTCUSDT"}
    return all_panels


async def _run_combo(panels: dict, sid: str, combo: dict, cost_bps: float) -> dict:
    """Fresh strategy instance, override exit attrs, replay panels, aggregate."""
    strat = bench._load_strategy(sid)
    strat.stop_loss_pct = combo["stop_loss_pct"]
    strat.take_profit_pct = combo["take_profit_pct"]
    # trailing_stop_pct: None 이면 attr 제거 (getattr 의 default 가 None 반환).
    if combo["trailing_stop_pct"] is None:
        # 트레일 disable 의 의미 — 절대 트리거 안 되는 큰 값으로 세팅. None 으로
        # 직접 세팅하면 일부 strategy(breakout)가 reason 문자열 안에서
        # f'{self.trailing_stop_pct:.2%}' 포맷팅하다 TypeError. 1.0=100% 면
        # 진입 대비 -100% 후퇴 = 영원히 미발동, 사실상 OFF.
        strat.trailing_stop_pct = 1.0
    else:
        strat.trailing_stop_pct = combo["trailing_stop_pct"]

    all_trades: list[dict] = []
    for symbol, panel in panels.items():
        trades = await bench._replay_symbol(strat, symbol, panel, cost_bps=cost_bps)
        all_trades.extend(trades)
    metrics = bench._aggregate(all_trades)
    edge = _edge(metrics)
    edge.update({
        "strategy_id": sid,
        "stop_loss_pct": combo["stop_loss_pct"],
        "take_profit_pct": combo["take_profit_pct"],
        "trailing_stop_pct": combo["trailing_stop_pct"],
        "_tag": f"{sid}@{combo['_tag']}",
    })
    return edge


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    logger.info("loading 1m panels (once, reused across all combos+strategies)...")
    panels_all = bench._load_binance_universe(args.period, bar="1m")
    if not panels_all:
        logger.error("binance_1m cache empty — run fetch_binance_1m_5y.py first.")
        return 2
    panels = _select_panels(panels_all, args.universe)
    logger.info("loaded %d symbols (universe=%s)", len(panels), args.universe)

    combos = _combos()
    if args.max_combos:
        combos = combos[: args.max_combos]
    sids = args.strategies.split(",") if args.strategies else CAND_C_STRATEGIES
    sids = [s.strip() for s in sids if s.strip()]
    total = len(combos) * len(sids)
    logger.info("sweep: %d combos x %d strategies = %d runs", len(combos), len(sids), total)

    rows: list[dict] = []
    i = 0
    for sid in sids:
        for combo in combos:
            i += 1
            c0 = time.time()
            try:
                row = await _run_combo(panels, sid, combo, args.cost_bps)
            except Exception as err:  # noqa: BLE001
                logger.warning("  [%d/%d] %s FAILED: %s", i, total, combo.get("_tag"), err)
                row = {"strategy_id": sid, "_tag": combo.get("_tag"),
                       "error": f"{type(err).__name__}: {err}",
                       "profit_factor": None, "expectancy": None, "trades": 0}
            rows.append(row)
            pf = row.get("profit_factor")
            exp = row.get("expectancy")
            pf_s = f"{pf:.3f}" if pf is not None else "—"
            exp_s = f"{exp * 100:+.4f}%" if exp is not None else "—"
            logger.info(
                "  [%d/%d] %-50s PF=%s exp=%s trades=%d (%.0fs)",
                i, total, row.get("_tag", "?"), pf_s, exp_s,
                row.get("trades", 0), time.time() - c0,
            )

    # PF 기준 정렬 — PF>1 통과 후보를 위에 모음.
    def _pf_key(r):
        pf = r.get("profit_factor")
        return -(pf if pf is not None else -1e9)
    rows_sorted = sorted(rows, key=_pf_key)

    # 결과 영구화 (콘솔 사고 무관)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps({
            "period": args.period, "universe": args.universe,
            "n_symbols": len(panels), "cost_bps": args.cost_bps,
            "strategies": sids, "n_combos": len(combos),
            "results": rows_sorted,
        }, indent=2, default=str))
        logger.info("wrote %s", args.output)

    # 표 출력
    print("\n" + "=" * 110)
    print(
        f"cand-c scalping sweep — universe={args.universe} period={args.period} "
        f"cost_bps={args.cost_bps}  PASS = PF>1.0 AND exp>0"
    )
    print("=" * 110)
    print(f"{'combo':<54}{'PF':>8}{'exp/trade':>12}{'trades':>9}{'win%':>7}{'verdict':>10}")
    print("-" * 110)
    for r in rows_sorted:
        if "error" in r:
            print(f"{r['_tag']:<54} ERROR: {r['error'][:40]}")
            continue
        pf = r["profit_factor"]
        exp = r["expectancy"]
        pf_s = f"{pf:7.3f}" if pf is not None else "      —"
        exp_s = f"{exp * 100:+10.4f}%" if exp is not None else "         —"
        wr = (r.get("win_rate") or 0.0) * 100
        verdict = "PASS" if (pf is not None and pf > 1.0 and exp is not None and exp > 0) else "fail"
        print(f"{r['_tag']:<54}{pf_s}{exp_s}{r['trades']:9d}{wr:6.1f}%{verdict:>10}")
    print("=" * 110)
    logger.info("total %.1f min", (time.time() - t0) / 60)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sweep_candc_scalping")
    p.add_argument(
        "--universe", choices=["btc-only", "full"], default="full",
        help="btc-only: BTCUSDT 1심볼 빠른 probe (~수분). full: 30종목 전체 (~수시간).",
    )
    p.add_argument("--period", default="1y")
    p.add_argument(
        "--cost-bps", type=float, default=20.0,
        help="라운드트립 bp. 단타 = taker 4bp x2 + 슬리피지 4bp + 펀딩버퍼 ~4bp = 20bp 보수적.",
    )
    p.add_argument("--strategies", default="",
                   help="comma-separated strategy_ids; empty=4 cand-c default.")
    p.add_argument("--output", default="reports/sweep_candc_scalping.json")
    p.add_argument("--max-combos", type=int, default=0,
                   help="cap per-strategy combos (0=all 8). timing probe 용.")
    args = p.parse_args(argv)

    # 콘솔 인코딩 안전 (Windows cp949 회피).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
