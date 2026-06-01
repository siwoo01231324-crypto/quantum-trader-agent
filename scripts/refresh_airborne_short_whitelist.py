"""Weekly refresh of ``config/airborne_short_whitelist.yaml``.

매주 토요일 KST 02:00 cron 실행을 가정. 본 스크립트는 yaml 을 *제안* 으로
생성하며, **사람 review 후 git commit** 필요 (자동 커밋 금지).

동작:
  1. 기존 yaml 로드 (state 머신)
  2. 직전 6개월 (rolling) 데이터로 per-symbol SHORT 3%/6% PF 재계산
     - universe = live fires 의 unique symbols (or --symbols-file)
  3. 지속성 규칙으로 state 전이:
       candidate (3주 연속 PF>=1.0 + n>=30) → active
       active    (1주 PF<0.95)            → warning
       warning   (PF<0.95 또 한주)         → removed
       warning   (PF<0.85 1주)            → removed (즉시)
       warning   (PF>=1.00)               → active 복귀
       active    (PF>=1.0)                → active 유지, consecutive_pass++
  4. 결과 yaml 출력 + diff (added / removed / status_changed)

Output:
  - ``config/airborne_short_whitelist.yaml.proposed`` (default — review 용)
  - ``--write`` 플래그 시 ``config/airborne_short_whitelist.yaml`` 덮어쓰기

상세: ``docs/specs/strategies/live-airborne-short-whitelist-v1.md`` §
"동적 Whitelist — Drift 대응".
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

import signals  # noqa: E402

from src.live.airborne_short_whitelist.whitelist_loader import (  # noqa: E402
    WhitelistConfig,
    WhitelistEntry,
    load_whitelist,
)

logger = logging.getLogger("refresh_airborne_short_whitelist")

CACHE_1H_DIR = _REPO_ROOT / "data" / "cache" / "binance_1h"
CACHE_1M_DIR = _REPO_ROOT / "data" / "cache" / "binance_1m"
DEFAULT_CONFIG = _REPO_ROOT / "config" / "airborne_short_whitelist.yaml"
COST_BPS_ROUNDTRIP = 10.0
LOOKAHEAD_BARS = 48
ROLLING_MONTHS = 6

ENTRY_PARAMS = dict(
    retrace=0.6, bb_window=20, bb_std=2.0,
    min_margin=0.001, atr_body_mult=0.3, atr_period=14,
)
STOP_PCT = 0.03
TP_PCT = 0.06
SIDE = "short"

# Persistence rules
PROMOTE_CONSECUTIVE = 3   # candidate → active
WARN_PF_THRESHOLD = 0.95
HARD_FAIL_PF = 0.85
MIN_TRADES_FOR_DECISION = 30


def _resample_1h(df_1m: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (df_1m[cols]
            .resample("1h", label="right", closed="right")
            .agg({k: agg[k] for k in cols})
            .dropna(subset=["close"]))


def load_panel(symbol: str, *, months: int) -> pd.DataFrame | None:
    end = pd.Timestamp.utcnow()
    start = end - pd.DateOffset(months=months)
    p_1h = CACHE_1H_DIR / f"{symbol}.parquet"
    if p_1h.exists():
        try:
            df = pd.read_parquet(p_1h)
            if df.index.tz is None:
                df = df.tz_localize("UTC")
            df = df.loc[start:end]
            if len(df) >= 50:
                return df
        except Exception:
            pass
    p_1m = CACHE_1M_DIR / f"{symbol}.parquet"
    if p_1m.exists():
        try:
            df_1m = pd.read_parquet(p_1m)
            if df_1m.index.tz is None:
                df_1m = df_1m.tz_localize("UTC")
            df_1m = df_1m.loc[start:end]
            panel = _resample_1h(df_1m)
            if len(panel) >= 50:
                return panel
        except Exception:
            pass
    return None


def _wilder_atr(high, low, close, period):
    n = len(close); atr = np.full(n, np.nan)
    if n < period + 1: return atr
    tr = np.zeros(n); tr[0] = high[0] - low[0]
    for i in range(1, n):
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i] - close[i - 1])
        tr[i] = max(a, b, c)
    atr[period] = tr[1:period + 1].mean()
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _sim(side, highs, lows, closes, idx, entry_px, sl, tp, cost):
    n = len(closes)
    end = min(idx + LOOKAHEAD_BARS + 1, n)
    if end - idx - 1 < 1: return 0.0
    for j in range(idx + 1, end):
        if side == "long":
            ret_low = (lows[j] - entry_px) / entry_px
            ret_high = (highs[j] - entry_px) / entry_px
        else:
            ret_low = (entry_px - highs[j]) / entry_px
            ret_high = (entry_px - lows[j]) / entry_px
        if ret_low <= -sl: return -sl - cost
        if ret_high >= tp: return tp - cost
    last = closes[end - 1]
    if side == "long":
        return (last - entry_px) / entry_px - cost
    return (entry_px - last) / entry_px - cost


def compute_rolling_pf(panel: pd.DataFrame, *, side: str = SIDE) -> dict:
    """Return {n, PF, sumR, exp, win_rate}. SHORT-only baseline 3%/6%."""
    bb = signals.compute("bollinger", close=panel["close"],
                         window=ENTRY_PARAMS["bb_window"],
                         n_std=ENTRY_PARAMS["bb_std"])
    upper = bb["upper"].to_numpy(); lower = bb["lower"].to_numpy()
    closes = panel["close"].to_numpy(); opens = panel["open"].to_numpy()
    highs = panel["high"].to_numpy(); lows = panel["low"].to_numpy()
    body_abs = np.abs(closes - opens)
    atr = _wilder_atr(highs, lows, closes, ENTRY_PARAMS["atr_period"])
    upper_thr = upper * (1 + ENTRY_PARAMS["min_margin"])
    lower_thr = lower * (1 - ENTRY_PARAMS["min_margin"])
    n = len(panel)
    state = 0; base = np.nan; extreme = np.nan
    cost_frac = COST_BPS_ROUNDTRIP / 10000.0
    rets: list[float] = []
    for i in range(1, n):
        if state == 0:
            if (not np.isnan(upper_thr[i]) and not np.isnan(upper_thr[i-1])
                    and not np.isnan(atr[i])
                    and body_abs[i] >= ENTRY_PARAMS["atr_body_mult"] * atr[i]):
                if closes[i] > upper_thr[i] and closes[i-1] <= upper_thr[i-1]:
                    state, base, extreme = 2, closes[i], highs[i]; continue
                if closes[i] < lower_thr[i] and closes[i-1] >= lower_thr[i-1]:
                    state, base, extreme = 1, closes[i], lows[i]; continue
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, lows[i])
            trig = extreme + ENTRY_PARAMS["retrace"] * (base - extreme)
            if closes[i] >= trig:
                if side == "long":
                    rets.append(_sim("long", highs, lows, closes, i,
                                     float(closes[i]), STOP_PCT, TP_PCT, cost_frac))
                state, base, extreme = 0, np.nan, np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, highs[i])
            trig = extreme - ENTRY_PARAMS["retrace"] * (extreme - base)
            if closes[i] <= trig:
                if side == "short":
                    rets.append(_sim("short", highs, lows, closes, i,
                                     float(closes[i]), STOP_PCT, TP_PCT, cost_frac))
                state, base, extreme = 0, np.nan, np.nan
    if not rets:
        return {"n": 0, "PF": None, "sumR": 0.0, "exp": 0.0, "win_rate": 0.0}
    arr = np.asarray(rets)
    wins = arr[arr > 0]; losses = arr[arr <= 0]
    gw = float(wins.sum()); gl = float(-losses.sum())
    return {
        "n": len(arr),
        "PF": round(gw / gl, 4) if gl > 0 else None,
        "sumR": round(float(arr.sum()), 4),
        "exp": round(float(arr.mean()), 6),
        "win_rate": round(float(len(wins) / len(arr)), 4),
    }


# ──────────────────────────────────────────────────────────────────────────
# STATE MACHINE
# ──────────────────────────────────────────────────────────────────────────


def transition(
    entry: WhitelistEntry,
    metric: dict,
) -> tuple[str, int, int, str]:
    """Compute next (status, pass, fail, note) given current entry + rolling PF.

    Returns: (new_status, consecutive_pass, consecutive_fail, reason)
    """
    pf = metric.get("PF")
    n = metric.get("n", 0)
    cur = entry.status
    cp = entry.consecutive_pass
    cf = entry.consecutive_fail
    # Insufficient data — no change
    if pf is None or n < MIN_TRADES_FOR_DECISION:
        return cur, cp, cf, f"hold:insufficient_data n={n} pf={pf}"

    is_pass = pf >= 1.0
    is_warn = pf < WARN_PF_THRESHOLD
    is_hard_fail = pf < HARD_FAIL_PF

    if cur == "candidate":
        if is_pass:
            cp_next = cp + 1; cf_next = 0
            if cp_next >= PROMOTE_CONSECUTIVE:
                return "active", cp_next, cf_next, f"promote_to_active pf={pf:.3f}"
            return "candidate", cp_next, cf_next, f"candidate_pass {cp_next}/{PROMOTE_CONSECUTIVE}"
        return "candidate", 0, cf + 1, f"candidate_fail pf={pf:.3f}"

    if cur == "active":
        if is_warn:
            return "warning", cp, cf + 1, f"degrade_to_warning pf={pf:.3f}"
        return "active", cp + 1, 0, f"active_continue pf={pf:.3f}"

    if cur == "warning":
        if is_hard_fail:
            return "removed", 0, cf + 1, f"hard_fail_remove pf={pf:.3f}"
        if is_pass:
            return "active", cp + 1, 0, f"recover_to_active pf={pf:.3f}"
        if is_warn:
            return "removed", 0, cf + 1, f"second_warn_remove pf={pf:.3f}"
        # 0.95 <= PF < 1.0 — neutral, 그대로 warning 유지
        return "warning", cp, cf, f"warning_neutral pf={pf:.3f}"

    if cur == "removed":
        if is_pass and cp >= 1:  # 회복 신호
            return "candidate", 1, 0, f"removed_recover pf={pf:.3f}"
        return "removed", 0, 0, "removed_hold"

    # 알 수 없는 상태 — 변경 없음
    return cur, cp, cf, f"unknown_status={cur}"


# ──────────────────────────────────────────────────────────────────────────
# OUTPUT
# ──────────────────────────────────────────────────────────────────────────


def render_yaml(
    cfg: WhitelistConfig,
    new_state: dict[str, dict],
    metrics: dict[str, dict],
    new_as_of: str,
) -> str:
    """Produce the new yaml string preserving header + sorted state."""
    syms = sorted(new_state.keys())
    lines = [
        "# Airborne SHORT-only Whitelist (auto-refreshed)",
        "#",
        "# Generated by ``scripts/refresh_airborne_short_whitelist.py``.",
        "# 사람 review 후 git commit. 자동 커밋 금지.",
        "",
        f"version: {cfg.version}",
        f"strategy_id: {cfg.strategy_id}",
        f"as_of: {new_as_of}",
        f"selection_method: rolling_{ROLLING_MONTHS}mo_short_only",
        f"min_train_pf: 1.00",
        f"min_train_n: {MIN_TRADES_FOR_DECISION}",
        f"entry_params:",
        f"  retrace_ratio: {ENTRY_PARAMS['retrace']}",
        f"  bb_window: {ENTRY_PARAMS['bb_window']}",
        f"  bb_std: {ENTRY_PARAMS['bb_std']}",
        f"  min_close_margin: {ENTRY_PARAMS['min_margin']}",
        f"  atr_body_mult: {ENTRY_PARAMS['atr_body_mult']}",
        f"exit_params:",
        f"  stop_loss_pct: {STOP_PCT}",
        f"  take_profit_pct: {TP_PCT}",
        "",
        "state:",
    ]
    for s in syms:
        st = new_state[s]
        m = metrics.get(s, {})
        n = m.get("n", 0); pf = m.get("PF")
        pf_str = f"{pf:.3f}" if pf is not None else "n/a"
        lines.append(f"  {s}:")
        lines.append(f"    status: {st['status']}")
        lines.append(f"    consecutive_pass: {st['consecutive_pass']}")
        lines.append(f"    consecutive_fail: {st['consecutive_fail']}")
        note = st.get("note") or f"rolling_PF={pf_str} n={n}"
        # quote note if contains special chars
        if any(ch in note for ch in [':', '#', '"']):
            note_str = '"' + note.replace('"', "'") + '"'
        else:
            note_str = note
        lines.append(f"    note: {note_str}")
    return "\n".join(lines) + "\n"


def print_diff(cfg: WhitelistConfig, new_state: dict[str, dict]) -> None:
    old = {s: e.status for s, e in cfg.entries.items()}
    new = {s: st["status"] for s, st in new_state.items()}
    added = sorted(set(new) - set(old))
    removed_keys = sorted(set(old) - set(new))
    status_changed = sorted(
        s for s in set(old) & set(new) if old[s] != new[s]
    )
    print("\n=== DIFF vs previous whitelist ===")
    if added:
        print(f"  NEW symbols ({len(added)}):")
        for s in added:
            print(f"    + {s}  (status={new[s]})")
    if removed_keys:
        print(f"  DROPPED symbols ({len(removed_keys)}):")
        for s in removed_keys:
            print(f"    - {s}  (was {old[s]})")
    if status_changed:
        print(f"  STATUS changed ({len(status_changed)}):")
        for s in status_changed:
            print(f"    ~ {s}: {old[s]} → {new[s]}")
    if not (added or removed_keys or status_changed):
        print("  no changes")
    print()


# ──────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--out", default=None,
                   help="기본 ``<config>.proposed``. --write 시 무시.")
    p.add_argument("--write", action="store_true",
                   help="config 파일 직접 덮어쓰기 (review 생략, 권장 X)")
    p.add_argument("--universe-file", default=None,
                   help="symbols list (newline 또는 comma). 미설정 시 "
                   "logs/airborne_fires/history.jsonl 사용")
    p.add_argument("--as-of", default=None,
                   help="ISO date — 기본 오늘. backtest reproducibility 용")
    p.add_argument("--months", type=int, default=ROLLING_MONTHS,
                   help=f"rolling window (default: {ROLLING_MONTHS})")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg_path = Path(args.config)
    try:
        cfg = load_whitelist(cfg_path)
    except Exception as err:  # noqa: BLE001
        logger.error("load whitelist failed: %s", err)
        return 2
    logger.info("loaded cfg: as_of=%s entries=%d", cfg.as_of, len(cfg.entries))

    # universe = 기존 entries + 라이브 fire universe
    if args.universe_file:
        text = Path(args.universe_file).read_text(encoding="utf-8")
        live_syms = [s.strip().upper() for s in text.replace(",", "\n").splitlines()
                     if s.strip()]
    else:
        fires_p = _REPO_ROOT / "logs" / "airborne_fires" / "history.jsonl"
        if fires_p.exists():
            fires = [json.loads(l) for l in fires_p.read_text(encoding="utf-8").splitlines() if l.strip()]
            live_syms = sorted({f["symbol"].upper() for f in fires})
        else:
            live_syms = []
    universe = sorted(set(cfg.entries) | set(live_syms))
    logger.info("universe: %d (existing %d + live %d)",
                len(universe), len(cfg.entries), len(live_syms))

    # compute rolling PF
    metrics: dict[str, dict] = {}
    t0 = time.time()
    for i, sym in enumerate(universe):
        panel = load_panel(sym, months=args.months)
        if panel is None or len(panel) < 50:
            metrics[sym] = {"n": 0, "PF": None}
            continue
        try:
            metrics[sym] = compute_rolling_pf(panel)
        except Exception as err:  # noqa: BLE001
            logger.warning("metric err %s: %s", sym, err)
            metrics[sym] = {"n": 0, "PF": None}
        if (i + 1) % 25 == 0:
            logger.info("[%d/%d] elapsed=%.1fs", i+1, len(universe), time.time()-t0)

    # state transitions
    new_state: dict[str, dict] = {}
    for sym in universe:
        cur_entry = cfg.entries.get(sym)
        if cur_entry is None:
            # 신규 — candidate 로 시작
            cur_entry = WhitelistEntry(
                symbol=sym, status="candidate",
                consecutive_pass=0, consecutive_fail=0, note="",
            )
        new_status, cp, cf, reason = transition(cur_entry, metrics[sym])
        new_state[sym] = {
            "status": new_status,
            "consecutive_pass": cp,
            "consecutive_fail": cf,
            "note": reason,
        }

    new_as_of = args.as_of or datetime.now(timezone.utc).date().isoformat()
    out_yaml = render_yaml(cfg, new_state, metrics, new_as_of)

    # decide output path
    if args.write:
        target = cfg_path
    elif args.out:
        target = Path(args.out)
    else:
        target = cfg_path.with_suffix(cfg_path.suffix + ".proposed")
    target.write_text(out_yaml, encoding="utf-8")
    logger.info("wrote %s", target)

    print_diff(cfg, new_state)
    # Summary stats
    counts = {s: 0 for s in ("active", "candidate", "warning", "removed")}
    for st in new_state.values():
        counts[st["status"]] = counts.get(st["status"], 0) + 1
    print("=== Proposed state distribution ===")
    for s, n in counts.items():
        print(f"  {s:>10}: {n}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
