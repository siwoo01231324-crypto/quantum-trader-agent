"""Annual report writer (CSV).

CSV columns aligned with KR 양도소득세 신고서식 보조자료 (간이 버전).
"""
from __future__ import annotations

import csv
from pathlib import Path

from .calculator import TaxResult


CSV_HEADER = [
    "종목",
    "매도일",
    "수량",
    "매도대금(KRW)",
    "취득원가(KRW)",
    "매도수수료(KRW)",
    "증권거래세(KRW)",
    "양도차익(KRW)",
]


def write_annual_csv(result: TaxResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for lot in result.realized_lots:
            w.writerow([
                lot.symbol,
                lot.sell_ts.date().isoformat(),
                lot.qty,
                f"{lot.proceeds:.0f}",
                f"{lot.cost:.0f}",
                f"{lot.sell_fee:.0f}",
                f"{lot.txn_tax:.0f}",
                f"{lot.gain:.0f}",
            ])
        w.writerow([])
        w.writerow(["합계 양도차익", "", "", "", "", "", "", f"{result.realized_gain:.0f}"])
        w.writerow(["증권거래세 합계", "", "", "", "", "", f"{result.transaction_tax:.0f}", ""])
        w.writerow(["배당 원천징수", "", "", "", "", "", f"{result.dividend_wht:.0f}", ""])
        w.writerow(["양도소득세(추정)", "", "", "", "", "", "", f"{result.capital_gains_tax:.0f}"])
    return path
