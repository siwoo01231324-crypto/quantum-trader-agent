"""One-off 1m × 5y eval — re-validates LiveBreakoutWithAtrStop after #256
anti-spike filter hardening. Reuses bench_live_scanner._replay_symbol and
eval_live_scanners_5y._edge so the methodology matches the rejected v1
baseline (reports/eval_live_scanners_5y.json). Delete after PR merge.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_scanner")
eval_mod = importlib.import_module("eval_live_scanners_5y")

logger = logging.getLogger("eval_breakout_atr_v2_1m")


async def _run(cost_bps: float = 10.0, strategy_id: str = "live_breakout_with_atr_stop") -> dict:
    t0 = time.time()
    logger.info("loading 5y 1m binance panels...")
    panels = bench._load_binance_universe("5y", bar="1m")
    if not panels:
        raise SystemExit("binance_1m cache empty — run fetch_binance_1m_5y.py first")
    logger.info("loaded %d symbols in %.1fs", len(panels), time.time() - t0)

    strat = bench._load_strategy(strategy_id)
    c0 = time.time()
    all_trades: list[dict] = []
    for symbol, panel in panels.items():
        all_trades.extend(
            await bench._replay_symbol(strat, symbol, panel, cost_bps=cost_bps)
        )
    edge = eval_mod._edge(bench._aggregate(all_trades))
    edge["strategy_id"] = strategy_id
    edge["stop_loss_pct"] = getattr(strat, "stop_loss_pct", None)
    edge["take_profit_pct"] = getattr(strat, "take_profit_pct", None)
    edge["trailing_stop_pct"] = getattr(strat, "trailing_stop_pct", None)
    edge["max_bar_jump_pct"] = getattr(strat, "max_bar_jump_pct", None)
    edge["approach_pct"] = getattr(strat, "approach_pct", None)
    edge["vol_mult"] = getattr(strat, "vol_mult", None)
    edge["elapsed_sec"] = round(time.time() - c0, 1)
    return edge


def main() -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    )
    out = asyncio.run(_run())
    out_path = _REPO_ROOT / "reports" / "eval_breakout_atr_v2_1m.json"
    out_path.write_text(json.dumps(out, indent=2))
    logger.info("wrote %s", out_path)
    print(json.dumps(out, indent=2))

    print("\n=== Comparison vs v1 baseline (reports/eval_live_scanners_5y.json) ===")
    v1 = {
        "PF": 0.8683, "expectancy_%": -0.2429, "win_%": 34.3,
        "payoff": 1.666, "trades": 50088,
    }
    print(
        f"v1 (naked):   PF={v1['PF']:.3f}  exp={v1['expectancy_%']:+.4f}%  "
        f"win={v1['win_%']:.1f}%  payoff={v1['payoff']:.2f}  trades={v1['trades']}"
    )
    verdict = "OK" if (out["profit_factor"] > 1.0 and out["expectancy"] > 0) else "LOSER"
    print(
        f"v2 (#256):    PF={out['profit_factor']:.3f}  "
        f"exp={out['expectancy']*100:+.4f}%  win={out['win_rate']*100:.1f}%  "
        f"payoff={out['payoff']:.2f}  trades={out['trades']}   [{verdict}]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
