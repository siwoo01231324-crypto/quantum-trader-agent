"""KIS API 응답이 paper 계정 일부 환경에서 소문자 키로 내려오는 quirk 회귀 테스트.

대시보드 "내 계좌" 카드가 5 validation errors 로 깨졌던 사고 (#215 후속) — KIS
API 가 잔고 응답 `output1[0]` 의 키를 `pdno`, `prdt_name`, `hldg_qty`,
`pchs_avg_pric`, `evlu_amt` 로 내려줘서 대문자만 받던 Pydantic 모델이 reject.
"""
from __future__ import annotations

import pytest

from src.brokers.kis.schemas import KISBalanceStock, KISBuyableOutput


class TestKISBalanceStock:
    """KIS API quirk: 동일 필드가 paper 계정에서 소문자로 응답될 수 있음."""

    def test_uppercase_keys_still_work(self) -> None:
        stock = KISBalanceStock(
            PDNO="005930",
            PRDT_NAME="삼성전자",
            HLDG_QTY="10",
            PCHS_AVG_PRIC="70000",
            EVLU_AMT="700000",
        )
        assert stock.PDNO == "005930"
        assert stock.qty == 10
        assert stock.avg_price == 70000

    def test_lowercase_keys_accepted(self) -> None:
        # 사용자가 본 실제 paper 응답 형태
        stock = KISBalanceStock.model_validate({
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "hldg_qty": "10",
            "pchs_avg_pric": "70000",
            "evlu_amt": "700000",
        })
        assert stock.PDNO == "005930"
        assert stock.PRDT_NAME == "삼성전자"
        assert stock.qty == 10
        assert stock.avg_price == 70000

    def test_extra_lowercase_fields_ignored(self) -> None:
        # KIS 응답에는 stck_loan_unpr 같은 추가 키가 같이 옴 — 무시되어야 함
        stock = KISBalanceStock.model_validate({
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "hldg_qty": "10",
            "pchs_avg_pric": "70000",
            "evlu_amt": "700000",
            "stck_loan_unpr": "0.0000",  # 알 수 없는 필드
            "evlu_pfls_amt": "100",
        })
        assert stock.PDNO == "005930"


class TestKISBuyableOutput:
    """KISBuyableOutput 도 같은 case-insensitive 패턴."""

    def test_uppercase_works(self) -> None:
        out = KISBuyableOutput(NRCVB_BUY_AMT="1000000")
        assert out.buyable_amount == 1000000

    def test_lowercase_works(self) -> None:
        out = KISBuyableOutput.model_validate({"nrcvb_buy_amt": "1000000"})
        assert out.buyable_amount == 1000000


def test_balance_response_with_lowercase_output1():
    """end-to-end: KISBalanceResponse 의 output1 이 소문자 키여도 파싱."""
    from src.brokers.kis.schemas import KISBalanceResponse

    payload = {
        "rt_cd": "0",
        "msg_cd": "OK",
        "msg1": "정상",
        "output1": [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "10",
                "pchs_avg_pric": "70000",
                "evlu_amt": "700000",
                "stck_loan_unpr": "0.0000",
            }
        ],
        "output2": [{"dnca_tot_amt": "5000000"}],
    }
    resp = KISBalanceResponse.model_validate(payload)
    assert len(resp.output1) == 1
    assert resp.output1[0].PDNO == "005930"
