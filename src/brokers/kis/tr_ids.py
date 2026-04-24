from __future__ import annotations

# KIS TR_ID 상수 테이블 (C5 Critical)
# 실전(live): TTTC*, 모의(paper): VTTC*
# 출처: wikidocs.net/239581, KIS Developers 공식 포털

PAPER_TR_IDS: dict[str, str] = {
    "order_buy": "VTTC0802U",
    "order_sell": "VTTC0801U",
    "order_modify_cancel": "VTTC0803U",
    "balance": "VTTC8434R",
    "buyable": "VTTC8908R",
    "ws_execution": "H0STCNI9",
}

LIVE_TR_IDS: dict[str, str] = {
    "order_buy": "TTTC0802U",
    "order_sell": "TTTC0801U",
    "order_modify_cancel": "TTTC0803U",
    "balance": "TTTC8434R",
    "buyable": "TTTC8908R",
    "ws_execution": "H0STCNI0",
}


def tr_ids_for(paper: bool) -> dict[str, str]:
    return PAPER_TR_IDS if paper else LIVE_TR_IDS


# Read-only inquiry TR-IDs (no paper variant — asymmetry vs order TR-IDs intentional).
# Verified 2026-04-24 via live paper call to openapivts.koreainvestment.com:29443.
# Corrections from initial stub:
#   - FHKST66430100 is BALANCE_SHEET, not financial-ratio (was mis-labelled)
#   - FID_DIV_CLS_CODE is a REQUIRED parameter (was missing → OPSQ2001 error)
#   - output is a LIST of quarterly records, not a single dict
TR_ID_BALANCE_SHEET      = "FHKST66430100"  # /finance/balance-sheet       — assets/liabilities
TR_ID_INCOME_STATEMENT   = "FHKST66430200"  # /finance/income-statement    — revenue/profit
TR_ID_FINANCIAL_RATIO    = "FHKST66430300"  # /finance/financial-ratio     — EPS/BPS/ROE/SPS/growth
TR_ID_PROFIT_RATIO       = "FHKST66430400"  # /finance/profit-ratio        — margins
TR_ID_OTHER_MAJOR_RATIOS = "FHKST66430500"  # /finance/other-major-ratios  — EBITDA/EV_EBITDA/payout
TR_ID_STABILITY_RATIO    = "FHKST66430600"  # /finance/stability-ratio     — current/quick ratio

TR_ID_INQUIRE_PRICE      = "FHKST01010100"  # /quotations/inquire-price    — PER/PBR/EPS/BPS (market multiples)
