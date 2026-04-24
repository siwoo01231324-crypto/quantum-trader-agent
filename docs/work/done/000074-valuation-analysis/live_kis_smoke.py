"""Live KIS valuation smoke test (paper account).

Fetches quarterly financial-ratio series + current market multiples for
삼성전자 (005930) + NAVER (035420) + 현대차 (005380) + 카카오 (035720),
prints parsed values, and produces a combined FUNDAMENTALS_PIT_SCHEMA DataFrame.

Requires .env in repo root with HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY,
HANTOO_CREDIT_NUMBER (format: "12345678-01"). .env is symlinked into worktrees
by /si command.

Run:
    cd .worktree/000074-valuation-analysis
    PYTHONPATH=".;src" python docs/work/active/000074-valuation-analysis/live_kis_smoke.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

load_dotenv(dotenv_path=ROOT / ".env")

from src.brokers.kis.auth import KISAuth
from src.brokers.kis.fundamentals_client import (
    fetch_financial_ratio_series,
    fetch_market_multiples,
)
from src.data_lake.fundamentals_store import to_fundamentals_frame


SYMBOLS = [
    ("005930", "삼성전자"),
    ("035420", "NAVER"),
    ("005380", "현대차"),
    ("035720", "카카오"),
]


def main() -> None:
    app_key = os.getenv("HANTOO_FAKE_API_KEY")
    app_secret = os.getenv("HANTOO_FAKE_SECRET_API_KEY")
    credit_number = os.getenv("HANTOO_CREDIT_NUMBER", "")
    if not app_key or not app_secret or "-" not in credit_number:
        raise SystemExit("HANTOO_FAKE_API_KEY / HANTOO_FAKE_SECRET_API_KEY / HANTOO_CREDIT_NUMBER not set in .env")

    cano, acnt = credit_number.split("-", 1)

    print("▶ KIS paper auth …")
    auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)
    token = auth.get_token()
    print(f"  access_token obtained ({len(token)} chars)\n")

    all_records = []
    for symbol, name in SYMBOLS:
        print(f"▶ {symbol} ({name})")

        # 1) financial-ratio (quarterly series)
        try:
            series = fetch_financial_ratio_series(
                symbol=symbol, auth=auth,
                app_key=app_key, app_secret=app_secret,
                cano=cano, acnt_prdt_cd=acnt, paper=True,
            )
        except Exception as exc:
            print(f"    financial-ratio ERROR: {exc}")
            series = []

        if series:
            latest = series[0]
            print(f"    [fin-ratio] {latest.fiscal_date}  "
                  f"EPS={latest.eps}  BPS={latest.bps}  ROE={latest.roe_val}%  "
                  f"op_margin={latest.bsop_prfi_inrt}%  growth={latest.grs}%  "
                  f"(+{len(series) - 1} older quarters)")
            all_records.extend(series[:4])  # keep recent 4 quarters
        else:
            print("    [fin-ratio] no data")

        # 2) market multiples (point-in-time)
        try:
            mm = fetch_market_multiples(
                symbol=symbol, auth=auth,
                app_key=app_key, app_secret=app_secret,
                cano=cano, acnt_prdt_cd=acnt, paper=True,
            )
        except Exception as exc:
            print(f"    inquire-price ERROR: {exc}")
            mm = None

        if mm and (mm.per is not None or mm.pbr is not None):
            print(f"    [multiples] PER={mm.per}  PBR={mm.pbr}")
            all_records.append(mm)
        else:
            print("    [multiples] no data")
        print()

        # paper server rate limit: 2 req/sec. Two calls per symbol above,
        # so pause briefly before next symbol to stay well under the limit.
        time.sleep(0.5)

    if all_records:
        print("▶ Combined FUNDAMENTALS_PIT_SCHEMA frame")
        df = to_fundamentals_frame(all_records)
        print(f"  shape={df.shape}")
        print(f"  sources: {sorted(df['source'].unique())}")
        print(f"  metrics: {sorted(df['metric'].unique())}")
        print(f"  symbols: {sorted(df['symbol'].unique())}")
        print()
        print("  --- sample rows (삼성전자 latest quarter + multiples) ---")
        sample = df[df["symbol"] == "005930"].sort_values(["source", "period_end"], ascending=[True, False]).head(15)
        print(sample.to_string(index=False))


if __name__ == "__main__":
    main()
