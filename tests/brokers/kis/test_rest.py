from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from src.brokers.errors import RateLimitError, InvalidOrderError
from src.brokers.kis.auth import KISAuth
from src.brokers.kis.error_map import map_error
from src.brokers.kis.krx_ticks import quantize_price_krx
from src.brokers.kis.rest import KISClient
from src.brokers.kis.tr_ids import LIVE_TR_IDS, PAPER_TR_IDS, tr_ids_for
from src.brokers.base import OrderType
from src.execution.base import Side


# ---------------------------------------------------------------------------
# TR_ID paper/live 분기 테스트 (C5 Critical)
# ---------------------------------------------------------------------------

class TestTrIdPaperVsLiveMapping:
    def test_paper_buy_tr_id(self):
        ids = tr_ids_for(paper=True)
        assert ids["order_buy"] == "VTTC0802U"

    def test_paper_sell_tr_id(self):
        ids = tr_ids_for(paper=True)
        assert ids["order_sell"] == "VTTC0801U"

    def test_paper_balance_tr_id(self):
        ids = tr_ids_for(paper=True)
        assert ids["balance"] == "VTTC8434R"

    def test_paper_buyable_tr_id(self):
        ids = tr_ids_for(paper=True)
        assert ids["buyable"] == "VTTC8908R"

    def test_paper_ws_tr_id(self):
        ids = tr_ids_for(paper=True)
        assert ids["ws_execution"] == "H0STCNI9"

    def test_live_buy_tr_id(self):
        ids = tr_ids_for(paper=False)
        assert ids["order_buy"] == "TTTC0802U"

    def test_live_sell_tr_id(self):
        ids = tr_ids_for(paper=False)
        assert ids["order_sell"] == "TTTC0801U"

    def test_live_balance_tr_id(self):
        ids = tr_ids_for(paper=False)
        assert ids["balance"] == "TTTC8434R"

    def test_live_buyable_tr_id(self):
        ids = tr_ids_for(paper=False)
        assert ids["buyable"] == "TTTC8908R"

    def test_live_ws_tr_id(self):
        ids = tr_ids_for(paper=False)
        assert ids["ws_execution"] == "H0STCNI0"

    def test_paper_and_live_are_different(self):
        paper = tr_ids_for(paper=True)
        live = tr_ids_for(paper=False)
        for key in paper:
            assert paper[key] != live[key], f"TR_ID '{key}' should differ between paper and live"


# ---------------------------------------------------------------------------
# 주문 파라미터 스키마 테스트
# ---------------------------------------------------------------------------

def _make_auth(paper=True):
    auth = MagicMock(spec=KISAuth)
    auth.get_token.return_value = "fake-token"
    return auth


def _make_client(paper=True):
    return KISClient(
        auth=_make_auth(paper),
        app_key="fake-appkey",
        app_secret="fake-appsecret",
        cano="12345678",
        acnt_prdt_cd="01",
        paper=paper,
    )


class TestOrderParamsSchema:
    @responses_lib.activate
    def test_limit_order_uses_ord_dvsn_00(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0001", "ORD_TMD": "090000"}},
        )
        resp = client.place_order("005930", Side.BUY, OrderType.LIMIT, Decimal("10"), Decimal("70000"))
        req_body = responses_lib.calls[0].request.body
        import json
        body = json.loads(req_body)
        assert body["ORD_DVSN"] == "00"
        assert body["ORD_UNPR"] == "70000"
        assert body["CANO"] == "12345678"
        assert body["ACNT_PRDT_CD"] == "01"

    @responses_lib.activate
    def test_market_order_uses_ord_dvsn_01_price_0(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0002", "ORD_TMD": "090001"}},
        )
        client.place_order("005930", Side.BUY, OrderType.MARKET, Decimal("5"), None)
        import json
        body = json.loads(responses_lib.calls[0].request.body)
        assert body["ORD_DVSN"] == "01"
        assert body["ORD_UNPR"] == "0"

    @responses_lib.activate
    def test_custtype_is_P_in_header(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0003", "ORD_TMD": "090002"}},
        )
        client.place_order("005930", Side.SELL, OrderType.LIMIT, Decimal("3"), Decimal("69000"))
        assert responses_lib.calls[0].request.headers["custtype"] == "P"

    @responses_lib.activate
    def test_paper_tr_id_in_header_for_buy(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0004", "ORD_TMD": "090003"}},
        )
        client.place_order("005930", Side.BUY, OrderType.LIMIT, Decimal("1"), Decimal("70000"))
        assert responses_lib.calls[0].request.headers["tr_id"] == "VTTC0802U"

    @responses_lib.activate
    def test_live_tr_id_in_header_for_buy(self):
        client = _make_client(paper=False)
        base = "https://openapi.koreainvestment.com:9443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0005", "ORD_TMD": "090004"}},
        )
        client.place_order("005930", Side.BUY, OrderType.LIMIT, Decimal("1"), Decimal("70000"))
        assert responses_lib.calls[0].request.headers["tr_id"] == "TTTC0802U"


# ---------------------------------------------------------------------------
# Hashkey optional
# ---------------------------------------------------------------------------

class TestHashkeyOptional:
    @responses_lib.activate
    def test_hashkey_generation_path(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/hashkey",
            json={"HASH": "fake-hashkey-value"},
        )
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0006", "ORD_TMD": "090005"}},
        )
        client.place_order("005930", Side.BUY, OrderType.LIMIT, Decimal("1"), Decimal("70000"), use_hashkey=True)
        import json
        order_body = json.loads(responses_lib.calls[1].request.body)
        assert order_body.get("hashkey") == "fake-hashkey-value"

    @responses_lib.activate
    def test_order_succeeds_without_hashkey(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "0", "msg_cd": "OK", "msg1": "ok", "output": {"ODNO": "0007", "ORD_TMD": "090006"}},
        )
        resp = client.place_order("005930", Side.BUY, OrderType.LIMIT, Decimal("1"), Decimal("70000"), use_hashkey=False)
        assert resp.output.ODNO == "0007"


# ---------------------------------------------------------------------------
# Error map: rt_cd / msg_cd 매핑
# ---------------------------------------------------------------------------

class TestErrorMapRtCdMsgCd:
    def test_egw00201_maps_to_rate_limit_error(self):
        err = map_error("EGW00201", "호출 유량 초과")
        assert isinstance(err, RateLimitError)
        assert "EGW00201" in str(err)

    def test_apbk0013_maps_to_invalid_order_error(self):
        err = map_error("APBK0013", "주문 거부")
        assert isinstance(err, InvalidOrderError)

    def test_unknown_msg_cd_maps_to_unknown_error(self):
        from src.brokers.errors import UnknownError
        err = map_error("ZZZZZZZZ", "알 수 없는 오류")
        assert isinstance(err, UnknownError)

    @responses_lib.activate
    def test_rt_cd_1_raises_broker_error(self):
        client = _make_client(paper=True)
        base = "https://openapivts.koreainvestment.com:29443"
        responses_lib.add(
            responses_lib.POST,
            f"{base}/uapi/domestic-stock/v1/trading/order-cash",
            json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "호출 유량 초과"},
        )
        with pytest.raises(RateLimitError):
            client.place_order("005930", Side.BUY, OrderType.MARKET, Decimal("1"), None)


# ---------------------------------------------------------------------------
# KRX 호가단위 quantize (코스피/코스닥/ETF)
# ---------------------------------------------------------------------------

class TestKRXTickSizeQuantize:
    # 코스피 경계값
    def test_kospi_below_1000_tick_1(self):
        assert quantize_price_krx(Decimal("999"), "KOSPI") == Decimal("999")

    def test_kospi_1000_tick_1(self):
        assert quantize_price_krx(Decimal("1000"), "KOSPI") == Decimal("1000")

    def test_kospi_1001_tick_5_rounds_down(self):
        result = quantize_price_krx(Decimal("1003"), "KOSPI")
        assert result == Decimal("1000")  # floor to nearest 5

    def test_kospi_4999_tick_5(self):
        assert quantize_price_krx(Decimal("4999"), "KOSPI") == Decimal("4995")

    def test_kospi_5000_tick_10(self):
        assert quantize_price_krx(Decimal("5007"), "KOSPI") == Decimal("5000")

    def test_kospi_9999_tick_10(self):
        assert quantize_price_krx(Decimal("9999"), "KOSPI") == Decimal("9990")

    def test_kospi_10000_tick_50(self):
        assert quantize_price_krx(Decimal("10030"), "KOSPI") == Decimal("10000")

    def test_kospi_49999_tick_50(self):
        assert quantize_price_krx(Decimal("49999"), "KOSPI") == Decimal("49950")

    def test_kospi_50000_tick_100(self):
        assert quantize_price_krx(Decimal("50070"), "KOSPI") == Decimal("50000")

    def test_kospi_99999_tick_100(self):
        assert quantize_price_krx(Decimal("99999"), "KOSPI") == Decimal("99900")

    def test_kospi_100000_tick_500(self):
        assert quantize_price_krx(Decimal("100300"), "KOSPI") == Decimal("100000")

    def test_kospi_499999_tick_500(self):
        assert quantize_price_krx(Decimal("499999"), "KOSPI") == Decimal("499500")

    def test_kospi_500000_tick_1000(self):
        assert quantize_price_krx(Decimal("500700"), "KOSPI") == Decimal("500000")

    def test_kospi_exact_boundary_no_change(self):
        assert quantize_price_krx(Decimal("70000"), "KOSPI") == Decimal("70000")

    # 코스닥
    def test_kosdaq_below_1000_tick_1(self):
        assert quantize_price_krx(Decimal("500"), "KOSDAQ") == Decimal("500")

    def test_kosdaq_1003_tick_5_rounds_down(self):
        assert quantize_price_krx(Decimal("1003"), "KOSDAQ") == Decimal("1000")

    def test_kosdaq_above_100000_tick_100(self):
        assert quantize_price_krx(Decimal("150070"), "KOSDAQ") == Decimal("150000")

    # ETF
    def test_etf_tick_5(self):
        assert quantize_price_krx(Decimal("10007"), "ETF") == Decimal("10005")

    def test_etf_exact_multiple_no_change(self):
        assert quantize_price_krx(Decimal("10010"), "ETF") == Decimal("10010")


# ---------------------------------------------------------------------------
# reduce_only 정책 테스트
# ---------------------------------------------------------------------------

class TestReduceOnlyPolicy:
    def test_reduce_only_buy_raises_unsupported(self):
        from src.brokers.kis.adapter import KISAdapter
        from src.brokers.errors import UnsupportedOperationError
        from src.brokers.base import OrderRequest, OrderType, PositionSide
        from src.execution.base import Side, TimeInForce

        adapter = KISAdapter(
            app_key="k", app_secret="s",
            credit_number="12345678-01", paper=True
        )
        req = OrderRequest(
            client_order_id="test-001",
            symbol="005930",
            side=Side.BUY,
            qty=Decimal("1"),
            order_type=OrderType.LIMIT,
            price=Decimal("70000"),
            tif=TimeInForce.DAY,
            reduce_only=True,
        )
        with pytest.raises(UnsupportedOperationError):
            with patch.object(adapter._client, "place_order"):
                adapter.place_order(req)

    def test_reduce_only_sell_allowed(self):
        from src.brokers.kis.adapter import KISAdapter
        from src.brokers.base import OrderRequest, OrderType, PositionSide
        from src.execution.base import Side, TimeInForce

        adapter = KISAdapter(
            app_key="k", app_secret="s",
            credit_number="12345678-01", paper=True
        )
        req = OrderRequest(
            client_order_id="test-002",
            symbol="005930",
            side=Side.SELL,
            qty=Decimal("1"),
            order_type=OrderType.LIMIT,
            price=Decimal("70000"),
            tif=TimeInForce.DAY,
            reduce_only=True,
        )
        mock_resp = MagicMock()
        mock_resp.output.ODNO = "9999"
        with patch.object(adapter._client, "place_order", return_value=mock_resp):
            ack = adapter.place_order(req)
        assert ack.broker_order_id == "9999"
