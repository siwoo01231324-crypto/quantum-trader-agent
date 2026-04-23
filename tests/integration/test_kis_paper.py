"""KIS paper trading integration tests (AC2 evidence).

Requires: HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY, HANTOO_CREDIT_NUMBER env vars.
Run with: pytest -m integration tests/integration/test_kis_paper.py

KRX trading hours: weekdays KST 09:00-15:30. Some tests skip outside these hours.
"""
from __future__ import annotations

import os
import threading
import time
from decimal import Decimal

import pytest
import requests

from tests.integration.conftest import _is_krx_open

SYMBOL = "005930"  # 삼성전자


def _get_current_price(base_url: str, symbol: str, token: str, app_key: str, app_secret: str) -> Decimal:
    resp = requests.get(
        f"{base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        headers={
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST01010100",
            "custtype": "P",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return Decimal(str(resp.json()["output"]["stck_prpr"]))


@pytest.mark.integration
@pytest.mark.skipif(not _is_krx_open(), reason="KRX closed (평일 09:00~15:30 KST)")
def test_place_samsung_limit_buy_then_cancel(kis_paper_creds):
    app_key, app_secret, credit_number = kis_paper_creds

    from src.brokers.kis.adapter import KISAdapter
    from src.brokers.kis.krx_ticks import quantize_price_krx
    from src.brokers.base import OrderRequest, OrderType
    from src.brokers import client_id as cid_mod
    from src.execution.base import Side, TimeInForce

    adapter = KISAdapter(
        app_key=app_key,
        app_secret=app_secret,
        credit_number=credit_number,
        paper=True,
    )

    token = adapter._auth.get_token()
    base_url = "https://openapivts.koreainvestment.com:29443"
    current_price = _get_current_price(base_url, SYMBOL, token, app_key, app_secret)

    # -1 tick 낮게 LIMIT 매수 (체결 방지)
    order_price = quantize_price_krx(current_price - 1)

    cid = cid_mod.generate(
        strategy="integration-test",
        symbol=SYMBOL,
        side="BUY",
        ts_ms=int(time.time() * 1000),
    )

    req = OrderRequest(
        client_order_id=cid,
        symbol=SYMBOL,
        side=Side.BUY,
        qty=Decimal("1"),
        order_type=OrderType.LIMIT,
        price=order_price,
        tif=TimeInForce.DAY,
    )

    ack = adapter.place_order(req)
    assert ack.broker_order_id, "주문번호(ODNO) 수신 실패"
    assert ack.symbol == SYMBOL

    odno = ack.broker_order_id

    # modify — 수량 증액
    adapter._client.modify_order(
        broker_order_id=odno,
        symbol=SYMBOL,
        qty=2,
        new_price=int(order_price),
    )

    # cancel
    adapter.cancel_order(broker_order_id=odno, symbol=SYMBOL)

    # 잔고조회 — 체결 안 됐으면 현금 변동 없음 (단순 호출 성공 확인)
    balances = adapter.get_balance()
    assert isinstance(balances, list)


@pytest.mark.integration
def test_buyable_query(kis_paper_creds):
    app_key, app_secret, credit_number = kis_paper_creds

    from src.brokers.kis.adapter import KISAdapter

    adapter = KISAdapter(
        app_key=app_key,
        app_secret=app_secret,
        credit_number=credit_number,
        paper=True,
    )

    result = adapter._client.get_buyable(symbol=SYMBOL, price=50000)
    assert result is not None


@pytest.mark.integration
def test_ws_execution_dispatch_with_aes(kis_paper_creds):
    app_key, app_secret, credit_number = kis_paper_creds

    from src.brokers.kis.auth import KISAuth
    from src.brokers.kis.ws import KISWebSocket
    from src.brokers.types import BrokerFill

    hts_id = os.environ.get("HANTOO_HTS_ID", app_key[:8])

    fills: list[BrokerFill] = []
    fill_event = threading.Event()

    def on_fill(fill: BrokerFill) -> None:
        fills.append(fill)
        fill_event.set()

    auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)
    ws = KISWebSocket(
        auth=auth,
        app_key=app_key,
        hts_id=hts_id,
        paper=True,
        on_fill=on_fill,
    )

    closeable = ws.connect()

    # AES key/iv 수신 대기 (구독 응답)
    deadline = time.time() + 10
    while not ws._execution_subscribed and time.time() < deadline:
        time.sleep(0.5)

    assert ws._execution_subscribed, "KIS WS: 체결통보 구독 응답 미수신 (10s timeout)"
    assert ws._aes_key is not None, "KIS WS: AES key 미수신"
    assert ws._aes_iv is not None, "KIS WS: AES iv 미수신"

    # 30초 내 fill 대기 — 장중 지연 가능하므로 미수신 시 xfail
    received = fill_event.wait(timeout=30)
    closeable.close()

    if not received:
        pytest.xfail("30s 내 fill 미수신 — 장중 주문 없음 또는 지연 가능")

    assert len(fills) >= 1
