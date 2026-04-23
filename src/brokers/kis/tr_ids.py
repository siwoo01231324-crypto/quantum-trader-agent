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
