"""스윙 2전략: 미완성봉(forming-bar) 진입 vs 마감봉(closed-bar) 진입 비교 (1회성).

배경
----
- 대시보드 sim(`_swing_sim_symbol`)은 **마감된 4h봉** 종가에서만 진입(백테스트 정석).
- 라이브는 매 틱 평가라 **미완성 4h봉**(봉 마감 전 조건 충족 시점)에 진입 가능.
  봉이 닫히기 전 1h 시점에 들어갔다가 마감 땐 조건이 깨지는 "가짜신호" 위험 vs
  진입가 개선 — 둘 중 어느 쪽이 우세한지 PF/net/기대값으로 정량화.

방법
----
- closed-bar(기준): 현 `_swing_sim_symbol` 그대로 재사용(4h 마감봉 on_bar, 종가 진입).
- forming-bar: 각 4h봉을 구성하는 4개 1h 서브봉을 누적해 *부분 4h봉* 구성
  (open=첫1h open, high=지금까지 max, low=min, close=현재 1h close, vol=합) →
  history=[그 전 마감 4h봉들 + 이 부분봉] 으로 on_bar 평가 → buy 처음 뜨는 1h
  서브봉에서 그 1h 종가로 진입. 청산(SL/TP/채널)은 closed-bar 와 동일하게 4h봉
  기준(진입 후 t+1 부터). 마지막 서브봉(=완전형성)에는 캐시 4h 행을 그대로 써서
  forming 이 closed 트리거를 *반드시 포함*(forming ⊇ closed) 하도록 보장 →
  PF/net 차이는 "조기 진입분" 의 순효과로 해석 가능.

데이터
------
- 4h: data/cache/swing_crypto_4h/*.parquet (기존). BTC 게이트: binance_4h_btc.parquet.
- 1h: data/cache/swing_crypto_1h/*.parquet (없으면 fapi 에서 페치 후 캐시). 5y.
- 유니버스: SWING_CRYPTO_UNIVERSE top-30 (속도). 두 전략 모두 동일 30종에서 비교.

비용 10bp(0.10%/거래). 환경 Date 제약 회피용 고정 NOW_MS.
커밋 금지 — 1회성 분석 스크립트.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

try:  # 콘솔이 cp949 라도 한글/em-dash 출력 가능하게.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from src.brokers.binance.universe_quote import fetch_klines, _klines_to_dataframe  # noqa: E402
from src.dashboard.app import (  # noqa: E402
    _swing_sim_symbol,
    _swing_run_on_bar,
    SWING_WIN,
    SWING_MAX_HOLD,
)
from src.backtest.strategies.live_capitulation_bounce import LiveCapitulationBounce  # noqa: E402
from src.backtest.strategies.live_donchian_breakout_btcgate import (  # noqa: E402
    LiveDonchianBreakoutBtcGate,
)
from src.portfolio.binance_universe import SWING_CRYPTO_UNIVERSE  # noqa: E402

ONE_H_DIR = ROOT / "data" / "cache" / "swing_crypto_1h"
FOUR_H_DIR = ROOT / "data" / "cache" / "swing_crypto_4h"
BTC_FILE = ROOT / "data" / "cache" / "binance_4h_btc.parquet"

TOP_N = 30
NOW_MS = 1782000000000          # 2026-07-01 근방(고정; 환경 Date 제약 회피)
FIVE_Y_MS = NOW_MS - 5 * 365 * 24 * 3600 * 1000
PERIODS = {"5y": "2021-06-30", "2y": "2024-06-30", "1y": "2025-06-30"}
FEE_PCT = 0.10                  # 10bp/거래


# ────────────────────────── 데이터 로드/페치 ──────────────────────────
def load_4h(sym: str) -> pd.DataFrame | None:
    p = FOUR_H_DIR / f"{sym}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    if "volume" not in d.columns:
        return None
    return d[["open", "high", "low", "close", "volume"]].dropna()


def fetch_1h(sym: str, start_ms: int) -> pd.DataFrame | None:
    """1h klines 페이징 페치(1000봉/콜) → DataFrame. fetch_4h 패턴 미러."""
    frames, cur = [], start_ms
    for _ in range(60):  # 5y/1h ≈ 44 페이지
        try:
            rows = fetch_klines(sym, "1h", start_ms=cur, limit=1000)
        except Exception:
            break
        if not rows:
            break
        frames.append(_klines_to_dataframe(rows, "1h"))
        if len(rows) < 1000:
            break
        cur = rows[-1][0] + 3600 * 1000
        time.sleep(0.04)
    if not frames:
        return None
    d = pd.concat(frames)
    d = d[~d.index.duplicated(keep="first")].sort_index()
    return d[["open", "high", "low", "close", "volume"]]


def load_or_fetch_1h(sym: str) -> pd.DataFrame | None:
    ONE_H_DIR.mkdir(parents=True, exist_ok=True)
    p = ONE_H_DIR / f"{sym}.parquet"
    if p.exists():
        d = pd.read_parquet(p)
        return d[["open", "high", "low", "close", "volume"]].dropna()
    d = fetch_1h(sym, FIVE_Y_MS)
    if d is not None and len(d) > 1000:
        d.to_parquet(p)
        return d
    return None


def subs_by_4h(onehr: pd.DataFrame) -> dict[pd.Timestamp, list[dict]]:
    """1h 봉을 부모 4h봉(floor '4h') 으로 그룹 → {T: [1h row dict, ...]} (시간순)."""
    parents = onehr.index.floor("4h")
    out: dict[pd.Timestamp, list[dict]] = {}
    o = onehr["open"].values
    h = onehr["high"].values
    lo = onehr["low"].values
    c = onehr["close"].values
    v = onehr["volume"].values
    for i, T in enumerate(parents):
        out.setdefault(T, []).append(
            {"open": float(o[i]), "high": float(h[i]), "low": float(lo[i]),
             "close": float(c[i]), "volume": float(v[i])}
        )
    return out


# ────────────────────────── forming-bar 시뮬 ──────────────────────────
def forming_sim_symbol(strat, strategy_id, sym, bars, subs_by_T, btc) -> list[dict]:
    """미완성봉 진입 시뮬. closed-bar(`_swing_sim_symbol`)와 청산 로직 동일,

    진입만 1h 서브봉 누적 부분봉 평가로 조기화. 마지막 서브봉엔 캐시 4h 행을 써서
    완전형성 평가가 closed-bar 와 정확히 일치(forming ⊇ closed) 하도록 보장한다.
    """
    has_channel = hasattr(strat, "channel_exit_level")
    cols = ["open", "high", "low", "close", "volume"]
    trades: list[dict] = []
    index = bars.index
    n = len(bars)
    t = strat.MIN_HISTORY
    while t < n - 1:
        T = index[t]
        base_hist = bars.iloc[max(0, t - SWING_WIN):t]          # 마감 4h봉만
        btc_snap = btc.loc[:T].tail(SWING_WIN)
        full_row = bars.iloc[t]
        subs = subs_by_T.get(T)
        m = len(subs) if subs else 1

        entry_price = sl_pct = tp_over = None
        sub_k = None
        for k in range(m):
            if (not subs) or k == m - 1:
                # 완전형성 = 캐시 4h 행 (closed-bar 와 byte-identical 평가)
                po = float(full_row["open"]); ph = float(full_row["high"])
                pl = float(full_row["low"]); pc = float(full_row["close"])
                pv = float(full_row["volume"])
            else:
                cum = subs[: k + 1]
                po = cum[0]["open"]
                ph = max(s["high"] for s in cum)
                pl = min(s["low"] for s in cum)
                pc = cum[k]["close"]
                pv = sum(s["volume"] for s in cum)
            partial = pd.DataFrame([[po, ph, pl, pc, pv]], columns=cols, index=[T])
            hist = pd.concat([base_hist, partial])
            ctx = {"market_snapshot": {
                "history": hist, "universe_ohlcv": {"BTCUSDT": btc_snap}}}
            sig = _swing_run_on_bar(strat, ctx)
            if sig is not None and sig.action == "buy":
                entry_price = pc
                sl_pct = sig.stop_loss_pct_override or strat.stop_loss_pct
                tp_over = sig.take_profit_pct_override
                sub_k = k
                break

        if entry_price is None:
            t += 1
            continue

        # ── 청산: closed-bar 와 동일(4h봉 기준, t+1 부터) ──
        entry = float(entry_price)
        stop_px = entry * (1 - sl_pct)
        tp_px = entry * (1 + (tp_over or 9.0)) if not has_channel else None
        exit_px = reason = exit_t = None
        for j in range(t + 1, min(t + 1 + SWING_MAX_HOLD, n)):
            low_j = float(bars["low"].iloc[j])
            high_j = float(bars["high"].iloc[j])
            close_j = float(bars["close"].iloc[j])
            if low_j <= stop_px:
                exit_px, reason, exit_t = stop_px, "stop", j
                break
            if has_channel:
                lvl = strat.channel_exit_level(bars.iloc[:j + 1])
                if lvl is not None and close_j < lvl:
                    exit_px, reason, exit_t = close_j, "channel_exit", j
                    break
            else:
                if tp_px is not None and high_j >= tp_px:
                    exit_px, reason, exit_t = tp_px, "tp", j
                    break
        if exit_px is None:
            j = min(t + SWING_MAX_HOLD, n - 1)
            exit_px, reason, exit_t = float(bars["close"].iloc[j]), "open_end", j

        trades.append({
            "strategy": strategy_id, "symbol": sym,
            "entry_ts": index[t].isoformat(), "exit_ts": index[exit_t].isoformat(),
            "entry": entry, "exit": float(exit_px),
            "ret": (float(exit_px) - entry) / entry * 100.0,
            "reason": reason, "sub_k": sub_k, "n_subs": m,
        })
        t = exit_t + 1
    return trades


# ────────────────────────── 집계/출력 ──────────────────────────
def agg(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    pcts = [t["ret"] for t in rows]
    g = sum(pcts)
    los = sum(p for p in pcts if p < 0)
    pf = (sum(p for p in pcts if p > 0) / abs(los)) if los < 0 else float("inf")
    win = sum(1 for p in pcts if p > 0) / n * 100
    return {"n": n, "win": win, "pf": pf, "exp": g / n, "net": g - FEE_PCT * n}


def fmt(a: dict) -> str:
    if a["n"] == 0:
        return "거래 0"
    pfs = "inf" if a["pf"] == float("inf") else f"{a['pf']:.2f}"
    return (f"n={a['n']:>4}  승{a['win']:>4.0f}%  PF={pfs:>5}  "
            f"기대값={a['exp']:+5.2f}%  net@10bp={a['net']:+8.1f}%")


def sel(trades, since, sym_set=None):
    out = [t for t in trades if t["entry_ts"] >= since]
    if sym_set is not None:
        out = [t for t in out if t["symbol"] in sym_set]
    return out


def main() -> None:
    syms = [s for s in SWING_CRYPTO_UNIVERSE[:TOP_N] if s != "BTCUSDT"]
    print(f">>> 유니버스 top-{TOP_N}: {len(syms)}종 (BTC 제외), 비용 {FEE_PCT}%/거래\n", flush=True)

    btc = pd.read_parquet(BTC_FILE)[["open", "high", "low", "close", "volume"]]

    bars_by, subs_by = {}, {}
    print(">>> 4h 로드 + 1h 로드/페치(캐시) ...", flush=True)
    for i, s in enumerate(syms):
        d4 = load_4h(s)
        if d4 is None or len(d4) < 260:
            continue
        d1 = load_or_fetch_1h(s)
        if d1 is None:
            continue
        bars_by[s] = d4
        subs_by[s] = subs_by_4h(d1)
        if (i + 1) % 5 == 0:
            print(f"    {i+1}/{len(syms)} ...", flush=True)
    print(f"    데이터 확보 {len(bars_by)}종\n", flush=True)

    common = set(bars_by.keys())
    strategies = [
        ("live-capitulation-bounce", "투매반등(평균회귀)", LiveCapitulationBounce()),
        ("live-donchian-breakout-btcgate", "돌파/터틀(추세추종)",
         LiveDonchianBreakoutBtcGate(btc_regime_gate=True)),
    ]

    closed_trades: dict[str, list[dict]] = {}
    forming_trades: dict[str, list[dict]] = {}
    for sid, label, strat in strategies:
        print(f">>> [{label}] closed-bar 시뮬 ...", flush=True)
        ct: list[dict] = []
        for s in common:
            bars = bars_by[s]
            if len(bars) < strat.MIN_HISTORY + 5:
                continue
            ct += _swing_sim_symbol(strat, sid, s, bars, btc)
        closed_trades[sid] = ct
        print(f"    closed 거래 {len(ct)}건. forming-bar 시뮬 ...", flush=True)
        ft: list[dict] = []
        for s in common:
            bars = bars_by[s]
            if len(bars) < strat.MIN_HISTORY + 5:
                continue
            ft += forming_sim_symbol(strat, sid, s, bars, subs_by[s], btc)
        forming_trades[sid] = ft
        print(f"    forming 거래 {len(ft)}건\n", flush=True)

    # ── 결과 표 ──
    print("\n" + "=" * 92)
    print(f"미완성봉(forming) vs 마감봉(closed) — 크립토 top-{TOP_N}, 비용 {FEE_PCT}%/거래")
    print("=" * 92)
    for sid, label, _ in strategies:
        print(f"\n■ {label}  [{sid}]")
        for pname, since in PERIODS.items():
            cs = sel(closed_trades[sid], since)
            fs = sel(forming_trades[sid], since)
            ca, fa = agg(cs), agg(fs)
            print(f"  ── {pname} (entry ≥ {since}) ──")
            print(f"     closed : {fmt(ca)}")
            print(f"     forming: {fmt(fa)}")
            if ca["n"] and fa["n"]:
                extra = fa["n"] - ca["n"]
                dpf = (fa["pf"] - ca["pf"]) if (fa["pf"] != float("inf") and ca["pf"] != float("inf")) else float("nan")
                print(f"     Δ      : 거래 {extra:+d}건  PF {dpf:+.2f}  "
                      f"기대값 {fa['exp'] - ca['exp']:+.2f}%  net {fa['net'] - ca['net']:+.1f}%")

    # ── forming 조기진입 비중(서브봉 분포) ──
    print("\n" + "=" * 92)
    print("forming 진입 시점 분포 (sub_k=0 첫1h … 마지막=완전형성=closed 와 동일가)")
    print("=" * 92)
    for sid, label, _ in strategies:
        ft = forming_trades[sid]
        from collections import Counter
        early = sum(1 for t in ft if t["sub_k"] is not None and t["sub_k"] < t["n_subs"] - 1)
        last = len(ft) - early
        dist = Counter(t["sub_k"] for t in ft)
        print(f"  {label}: 총 {len(ft)}건 | 조기(k<last) {early}건 "
              f"({100*early/max(1,len(ft)):.0f}%) | 완전형성진입 {last}건 | sub_k 분포 {dict(sorted(dist.items()))}")


if __name__ == "__main__":
    main()
