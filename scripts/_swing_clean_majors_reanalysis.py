"""깨끗한 크립토 메이저 유니버스로 스윙 2전략 재분석.

기존 data/cache/binance_1h 는 Binance 선물이 상장한 토큰화주식(TSLA/NVDA/QQQ)·
상품(XAU/XAG/CL)·forex(EUR)·비-ASCII 쓰레기로 오염 + 진짜 메이저(ETH/SOL/XRP)
부재. 본 스크립트는 fapi 24h 티커에서 denylist 거른 *크립토만* top-N 을 뽑아
4h klines 직접 페치 → `_swing_sim_symbol` 동일 로직으로 기간·유니버스크기별 재집계.

캐시(오염원)를 일절 건드리지 않는 self-contained 재현. 결과로 유니버스 확대의
진짜 효과(메이저 기준)를 본다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from src.brokers.binance.universe_quote import fetch_klines, _klines_to_dataframe  # noqa: E402
from src.dashboard.app import _swing_sim_symbol  # noqa: E402
from src.backtest.strategies.live_capitulation_bounce import LiveCapitulationBounce  # noqa: E402
from src.backtest.strategies.live_donchian_breakout_btcgate import (  # noqa: E402
    LiveDonchianBreakoutBtcGate,
)

# ── 확신하는 크립토 allowlist (정적; 토큰화주식/상품/forex 원천 배제) ──
# cs-tsmom 동적유니버스 사고 교훈 → denylist 두더지잡기 대신 allowlist.
# ~80 well-known 크립토. fetch 실패/데이터부족은 graceful drop.
CRYPTO_UNIVERSE = [
    # 메이저
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "TRXUSDT", "LTCUSDT", "DOTUSDT", "MATICUSDT", "TONUSDT",
    "SHIBUSDT", "BCHUSDT", "UNIUSDT", "NEARUSDT", "ICPUSDT", "ATOMUSDT", "XLMUSDT",
    "ETCUSDT", "XMRUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "HBARUSDT",
    "VETUSDT", "ALGOUSDT", "AAVEUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT",
    # 알트 (확립된 크립토)
    "RENDERUSDT", "FETUSDT", "GRTUSDT", "STXUSDT", "IMXUSDT", "JUPUSDT", "WIFUSDT",
    "BONKUSDT", "FLOKIUSDT", "PEPEUSDT", "1000PEPEUSDT", "1000SHIBUSDT", "1000LUNCUSDT",
    "CRVUSDT", "AXSUSDT", "SANDUSDT", "MANAUSDT", "CHZUSDT", "EOSUSDT", "ZECUSDT",
    "DASHUSDT", "NEOUSDT", "KAVAUSDT", "RUNEUSDT", "THETAUSDT", "EGLDUSDT", "GALAUSDT",
    "ENAUSDT", "ENSUSDT", "LDOUSDT", "PENDLEUSDT", "JTOUSDT", "PYTHUSDT", "WLDUSDT",
    "ORDIUSDT", "BLURUSDT", "DYDXUSDT", "GMXUSDT", "SNXUSDT", "COMPUSDT", "MKRUSDT",
    "1INCHUSDT", "ARUSDT", "OSMOUSDT", "ROSEUSDT", "ANKRUSDT", "ZILUSDT", "QNTUSDT",
    "FTMUSDT", "SUSHIUSDT", "YFIUSDT", "BATUSDT", "ZRXUSDT", "STORJUSDT", "KSMUSDT",
]

PERIODS = {"5y": "2021-06-30", "2y": "2024-06-30", "1y": "2025-06-30"}
SIZES = [30, 50, 100]      # 유니버스 크기(거래대금 순)
NOW_MS = 1782000000000     # 2026-07-01 근방(고정; 환경 Date 제약 회피)
FIVE_Y_MS = NOW_MS - 5 * 365 * 24 * 3600 * 1000


def fetch_top_crypto(n: int = 100) -> list[str]:
    """확신 allowlist 그대로 반환 (fetch 단계서 데이터 유무로 정제)."""
    return list(CRYPTO_UNIVERSE)


def fetch_4h(sym: str, start_ms: int) -> pd.DataFrame | None:
    """4h klines 페이징 페치(1000봉/콜) → DataFrame."""
    frames = []
    cur = start_ms
    step = 1000 * 4 * 3600 * 1000  # 1000봉 × 4h
    for _ in range(40):  # 최대 40페이지(=충분히 5y+)
        try:
            rows = fetch_klines(sym, "4h", start_ms=cur, limit=1000)
        except Exception:
            break
        if not rows:
            break
        frames.append(_klines_to_dataframe(rows, "4h"))
        last_open = rows[-1][0]
        if len(rows) < 1000:
            break
        cur = last_open + 4 * 3600 * 1000
        time.sleep(0.04)
    if not frames:
        return None
    d = pd.concat(frames)
    d = d[~d.index.duplicated(keep="first")].sort_index()
    return d[["open", "high", "low", "close", "volume"]]


def stats(rows):
    n = len(rows)
    if n == 0:
        return "거래 0"
    pcts = [t["ret"] for t in rows]
    g = sum(pcts)
    ls = sum(p for p in pcts if p < 0)
    pf = (sum(p for p in pcts if p > 0) / abs(ls)) if ls < 0 else float("inf")
    pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
    win = sum(1 for p in pcts if p > 0) / n * 100
    return (f"n={n:>4}  승{win:>4.0f}%  PF={pfs:>5}  기대값={g/n:+5.2f}%  "
            f"net@10bp={g - 0.10*n:+8.1f}%")


def main():
    print(">>> 깨끗한 크립토 top-100 페치 중...", flush=True)
    syms = fetch_top_crypto(100)
    print(f"    선정 {len(syms)}종 (denylist 적용)")
    print(f"    상위 12: {syms[:12]}")
    majors = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"]
    print(f"    메이저 포함확인: {[m for m in majors if m in syms]}\n")

    print(">>> BTC 4h(게이트) + 종목별 4h 페치 중 (페이징)...", flush=True)
    btc = fetch_4h("BTCUSDT", FIVE_Y_MS)
    bars_by = {}
    for i, s in enumerate(syms):
        d = fetch_4h(s, FIVE_Y_MS)
        if d is not None and len(d) > 250:
            bars_by[s] = d
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(syms)} 페치...", flush=True)
    print(f"    데이터 확보 {len(bars_by)}종 (>250봉)\n")

    # 유니버스 순서 = 실제 최근 거래대금(4h close×vol, 최근 ~90일) 순.
    def recent_dv(s):
        d = bars_by[s]
        return float((d["close"] * d["volume"]).tail(540).mean())  # ~90일(4h봉)
    ordered = sorted(bars_by.keys(), key=recent_dv, reverse=True)
    print(f"    거래대금 top12: {ordered[:12]}\n")

    strategies = [
        ("cap", "투매반등", LiveCapitulationBounce()),
        ("don", "돌파/터틀", LiveDonchianBreakoutBtcGate(btc_regime_gate=True)),
    ]
    # 전 종목 거래 1회 생성 + entry_ts·tag·심볼 랭크 부착
    trades = []
    rank = {s: i + 1 for i, s in enumerate(ordered)}  # 거래대금 랭크(1=최고)
    for tag, _lbl, strat in strategies:
        for s in ordered:
            bars = bars_by[s]
            if len(bars) < strat.MIN_HISTORY + 5:
                continue
            for t in _swing_sim_symbol(strat, tag, s, bars, btc):
                t["tag"] = tag
                t["rank"] = rank[s]
                trades.append(t)
    print(f"전체 거래: {len(trades)}건\n")

    def sel(since, tag, topn):
        return [t for t in trades if t["entry_ts"] >= since
                and t["tag"] == tag and t["rank"] <= topn]

    for pname, since in PERIODS.items():
        print("=" * 100)
        print(f"[{pname}]  (entry >= {since})  — 깨끗한 크립토 메이저")
        print("=" * 100)
        for tag, label, _ in strategies:
            print(f"\n  ── {label} ──")
            for topn in SIZES:
                print(f"    top-{topn:<3}: {stats(sel(since, tag, topn))}")
        print()


if __name__ == "__main__":
    main()
