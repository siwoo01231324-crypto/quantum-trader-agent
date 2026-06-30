"""스윙 깨끗한 크립토 4h 캐시 구축 + 유동성 순서 산출 (1회성 셋업).

오염된 data/cache/binance_1h(토큰화주식·상품·forex) 대신, 확신 크립토 allowlist
의 4h klines 를 data/cache/swing_crypto_4h/ 에 저장. BTC 4h 게이트 파일도 갱신.
최근 거래대금 순 ordered 리스트를 swing_crypto_universe.json + Python tuple
리터럴로 출력 → binance_universe.py 상수에 반영.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from src.brokers.binance.universe_quote import fetch_klines, _klines_to_dataframe  # noqa: E402
from scripts._swing_clean_majors_reanalysis import CRYPTO_UNIVERSE, FIVE_Y_MS  # noqa: E402

OUT_DIR = ROOT / "data" / "cache" / "swing_crypto_4h"
BTC_FILE = ROOT / "data" / "cache" / "binance_4h_btc.parquet"
UNIVERSE_JSON = ROOT / "data" / "cache" / "swing_crypto_universe.json"


def fetch_4h(sym: str, start_ms: int) -> pd.DataFrame | None:
    frames, cur = [], start_ms
    for _ in range(40):
        try:
            rows = fetch_klines(sym, "4h", start_ms=cur, limit=1000)
        except Exception:
            break
        if not rows:
            break
        frames.append(_klines_to_dataframe(rows, "4h"))
        if len(rows) < 1000:
            break
        cur = rows[-1][0] + 4 * 3600 * 1000
        time.sleep(0.04)
    if not frames:
        return None
    d = pd.concat(frames)
    d = d[~d.index.duplicated(keep="first")].sort_index()
    return d[["open", "high", "low", "close", "volume"]]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f">>> BTC 4h 게이트 갱신 → {BTC_FILE.name}", flush=True)
    btc = fetch_4h("BTCUSDT", FIVE_Y_MS)
    if btc is not None:
        btc.to_parquet(BTC_FILE)
        print(f"    BTC {len(btc)}봉 ({btc.index[0].date()}~{btc.index[-1].date()})")

    print(f">>> 크립토 {len(CRYPTO_UNIVERSE)}종 4h 페치 → {OUT_DIR}", flush=True)
    dv = {}
    saved = 0
    for i, s in enumerate(CRYPTO_UNIVERSE):
        if s == "BTCUSDT":
            continue
        d = fetch_4h(s, FIVE_Y_MS)
        if d is not None and len(d) > 250:
            d.to_parquet(OUT_DIR / f"{s}.parquet")
            dv[s] = float((d["close"] * d["volume"]).tail(540).mean())  # ~90일
            saved += 1
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(CRYPTO_UNIVERSE)} ...", flush=True)
    print(f"    저장 {saved}종")

    ordered = sorted(dv.keys(), key=lambda s: -dv[s])
    UNIVERSE_JSON.write_text(json.dumps(ordered, indent=0), encoding="utf-8")
    print(f"\n>>> 유동성 순서 {len(ordered)}종 → {UNIVERSE_JSON.name}")
    print(f"    top12: {ordered[:12]}")

    # binance_universe.py 상수용 tuple 리터럴
    import textwrap
    lit = ", ".join(f'"{s}"' for s in ordered)
    print("\n# ── 아래를 SWING_CRYPTO_UNIVERSE 상수로 ──")
    print("SWING_CRYPTO_UNIVERSE: tuple[str, ...] = (")
    print(textwrap.fill(lit, width=88, initial_indent="    ", subsequent_indent="    "))
    print(")")


if __name__ == "__main__":
    main()
