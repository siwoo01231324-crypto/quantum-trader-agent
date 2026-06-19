"""PPP 반전 스캘핑 시그널 데몬 (페이퍼 데이터 수집) — 고변동 알트 5m.

`scripts/ma_cross_alert_daemon.py` 패턴 미러. 매 5m 봉 마감마다 고변동 알트
유니버스에서 **검증된 PPP 반전 변형**을 평가:
  과매도(<25) QPP 골든크로스 + 횡보레짐(Choppiness≥61.8) → LONG
  과매수(>75) QPP 데드크로스 + 횡보레짐                  → SHORT
시그널을 ① stdout 로그(``PPP-SIGNAL ...``) ② Telegram ③ 영속 store
(``logs/ppp/history.jsonl``, `PppSignalStore`) 에 누적한다.

⚠️ live-ppp-scalping-v1 은 5y 백테스트 OOS 과적합으로 **라이브 미활성**. 본 데몬은
**페이퍼 시그널 수집(라이브 OOS 데이터 축적)** 전용 — 실발주 안 함. ablation 에서
횡보레짐 필터가 PF 를 0.87→0.96~1.0 으로 올렸으나(다이버전스·S/R 은 표본만 깎아
제외) WLD 외 종목·OOS test 에서 무너짐 → 실거래 라이브로 진짜 OOS 확인용.

Usage:
    python scripts/ppp_signal_daemon.py
    python scripts/ppp_signal_daemon.py --symbols WLDUSDT,ORDIUSDT --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _autoload_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for cand in (Path.cwd(), _ROOT, _ROOT.parent):
        if (cand / ".env").exists():
            load_dotenv(cand / ".env"); return


_autoload_dotenv()

from backtest.strategies.live_ppp_scalping_v1 import stoch_rsi  # noqa: E402
from dashboard.ppp_signal_store import PppSignalStore  # noqa: E402
from observability.alerts import notify  # noqa: E402

log = logging.getLogger("ppp_signal_daemon")

DAEMON_VERSION = "v0.1.0"
_FAPI = "https://fapi.binance.com/fapi/v1/klines"
# 고변동 알트 (검증서 WLD/BEAT 만 in-sample 통과 — 페이퍼로 진짜 OOS 확인).
DEFAULT_UNIVERSE = ["WLDUSDT", "ORDIUSDT", "WIFUSDT", "1000PEPEUSDT"]
OB, OS = 75.0, 25.0
CHOP_THR = 61.8
MIN_BARS = 300
BAR_MS_5M = 300_000


def _fetch_5m(symbol: str, limit: int = 400) -> pd.DataFrame | None:
    try:
        u = f"{_FAPI}?symbol={symbol}&interval=5m&limit={limit}"
        data = json.load(urllib.request.urlopen(u, timeout=20))
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch %s failed: %s", symbol, exc)
        return None
    if not data or len(data) < MIN_BARS:
        return None
    df = pd.DataFrame(data, columns=["t", "o", "h", "l", "c", "v", "ct", "qv",
                                     "n", "tb", "tq", "ig"])
    df = df.astype({"o": float, "h": float, "l": float, "c": float})
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df


def _choppiness(high, low, close, n=14):
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr = np.concatenate([[np.nan], tr])
    s = pd.Series(tr).rolling(n).sum().to_numpy()
    hh = pd.Series(high).rolling(n).max().to_numpy()
    ll = pd.Series(low).rolling(n).min().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * np.log10(s / (hh - ll)) / np.log10(n)


def evaluate(symbol: str, df: pd.DataFrame) -> dict | None:
    """마지막 *확정* 봉(–1, 이미 닫힌 봉은 –2)에서 반전 시그널 판정.

    fapi 마지막 row 는 forming 봉 → 직전(–2) 을 확정봉으로 사용.
    """
    if df is None or len(df) < MIN_BARS:
        return None
    close = df["c"]
    main, sig = stoch_rsi(close)
    m = main.to_numpy(); sg = sig.to_numpy()
    ci = _choppiness(df["h"].to_numpy(), df["l"].to_numpy(), close.to_numpy(), 14)
    i = len(df) - 2  # 확정봉
    if i < 2 or np.isnan(m[i]) or np.isnan(sg[i]) or np.isnan(ci[i]):
        return None
    golden = m[i - 1] <= sg[i - 1] and m[i] > sg[i]
    dead = m[i - 1] >= sg[i - 1] and m[i] < sg[i]
    if ci[i] < CHOP_THR:
        return None
    side = None
    if golden and m[i] <= OS:
        side = "long"
    elif dead and m[i] >= OB:
        side = "short"
    if side is None:
        return None
    ts = pd.Timestamp(df.index[i]).isoformat()
    return {
        "ts": ts, "symbol": symbol, "side": side,
        "close": float(close.iloc[i]), "qpp_main": round(float(m[i]), 2),
        "qpp_sig": round(float(sg[i]), 2), "choppiness": round(float(ci[i]), 1),
        "regime": "range",
    }


def _next_wakeup(now: datetime) -> datetime:
    """다음 5m 경계 +5s (UTC)."""
    base = now.replace(second=5, microsecond=0)
    minute = (now.minute // 5) * 5
    cand = now.replace(minute=minute, second=5, microsecond=0)
    while cand <= now:
        cand += timedelta(minutes=5)
    return cand


def run(symbols: list[str], *, dry_run: bool, store: PppSignalStore) -> None:
    last_ts: dict[str, str] = {}
    log.info("ppp signal daemon %s — %d symbols, 5m", DAEMON_VERSION, len(symbols))
    while True:
        now = datetime.now(timezone.utc)
        nxt = _next_wakeup(now)
        time.sleep(max(1.0, (nxt - now).total_seconds()))
        new = []
        for s in symbols:
            sigd = evaluate(s, _fetch_5m(s))
            if sigd is None:
                continue
            if last_ts.get((s)) == sigd["ts"]:
                continue
            last_ts[s] = sigd["ts"]
            new.append(sigd)
            line = (f"PPP-SIGNAL {s} {sigd['side']} close={sigd['close']:.6g} "
                    f"qpp={sigd['qpp_main']}/{sigd['qpp_sig']} chop={sigd['choppiness']}")
            log.info(line)
            if not dry_run:
                icon = "🟢⬆️ 롱" if sigd["side"] == "long" else "🔴⬇️ 숏"
                notify("info", f"{icon} 반전 시그널 — {s} (5m, 횡보)",
                       f"QPP {sigd['side']} @ {sigd['close']:.6g}\n"
                       f"본선 {sigd['qpp_main']} / 시그널 {sigd['qpp_sig']} / "
                       f"Choppiness {sigd['choppiness']}", sigd)
            else:
                print(f"[DRY] {line}", flush=True)
        if new:
            added = store.append_many(new)
            log.info("appended %d/%d signals to store (total=%d)",
                     added, len(new), store.count())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PPP reversion signal daemon (paper)")
    ap.add_argument("--symbols", default=os.environ.get("PPP_SYMBOLS", ",".join(DEFAULT_UNIVERSE)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--store", default="logs/ppp/history.jsonl")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    store = PppSignalStore(args.store)
    try:
        run(symbols, dry_run=args.dry_run, store=store)
    except KeyboardInterrupt:
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
