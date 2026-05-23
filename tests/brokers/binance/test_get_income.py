"""Binance sync 클라이언트 ``get_income`` (`/fapi/v1/income`) + ``IncomeItem``.

거래소 income 원장 — 대시보드 PnL 카드의 실현손익 권위 출처 (WAL 재구성 폐기).
Binance 제약 두 가지를 검증한다: 응답당 1000건 페이지네이션 + 7일 윈도우 분할.
"""
from __future__ import annotations

from decimal import Decimal

from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.binance.schemas import IncomeItem
from src.brokers.rate_limiter import RateLimiter

_DAY_MS = 24 * 60 * 60 * 1000


def _client() -> BinanceFuturesClient:
    return BinanceFuturesClient(
        api_key="k", secret="s", base_url="http://x",
        rate_limiter=RateLimiter(),
    )


def _rec(time: int, income: str = "1.0", itype: str = "REALIZED_PNL") -> dict:
    return {
        "symbol": "BTCUSDT", "incomeType": itype, "income": income,
        "asset": "USDT", "time": time, "tranId": time, "tradeId": str(time),
    }


# ── IncomeItem 스키마 ───────────────────────────────────────────────────────

def test_income_item_parses_and_coerces():
    it = IncomeItem.model_validate(_rec(1700000000000, "-0.42"))
    assert it.incomeType == "REALIZED_PNL"
    assert it.income == Decimal("-0.42")
    assert it.time == 1700000000000
    assert it.tradeId == "1700000000000"


def test_income_item_int_trade_id_coerced_to_str():
    it = IncomeItem.model_validate(
        {"incomeType": "COMMISSION", "income": "-0.1", "time": 1, "tradeId": 12345}
    )
    assert it.tradeId == "12345"


def test_income_item_missing_optional_fields():
    it = IncomeItem.model_validate(
        {"incomeType": "FUNDING_FEE", "income": "0.05", "time": 1}
    )
    assert it.symbol == "" and it.asset == "" and it.tranId == 0


# ── get_income 페이지네이션 + 7일 윈도우 ────────────────────────────────────

def test_get_income_single_page():
    client = _client()
    client._get = lambda path, params: [
        _rec(params["startTime"] + i) for i in range(3)
    ]
    items = client.get_income(start_time=0, end_time=1000)
    assert len(items) == 3
    assert all(isinstance(x, IncomeItem) for x in items)


def test_get_income_paginates_past_1000():
    """한 윈도우에 1000+ 건 → startTime 을 마지막 레코드 +1 로 밀며 페이징."""
    client = _client()
    calls: list[int] = []

    def fake_get(path, params):
        calls.append(params["startTime"])
        size = 1000 if len(calls) == 1 else 250
        return [_rec(params["startTime"] + i) for i in range(size)]

    client._get = fake_get
    items = client.get_income(start_time=0, end_time=5000)
    assert len(items) == 1250
    assert len(calls) == 2
    assert calls[1] == 1000  # 1페이지 마지막 time(999) + 1


def test_get_income_splits_7day_windows():
    """20일 범위 → 7일 단위 3개 윈도우. 경계 비중복(다음 start = 직전 end+1)."""
    client = _client()
    windows: list[tuple[int, int]] = []

    def fake_get(path, params):
        windows.append((params["startTime"], params["endTime"]))
        return []

    client._get = fake_get
    client.get_income(start_time=0, end_time=20 * _DAY_MS)
    assert len(windows) == 3
    assert windows[1][0] == windows[0][1] + 1
    assert windows[2][0] == windows[1][1] + 1
    assert windows[2][1] == 20 * _DAY_MS  # 마지막 윈도우는 end_time 에서 종료


def test_get_income_income_type_filter_passed():
    client = _client()
    seen: list = []

    def fake_get(path, params):
        seen.append(params.get("incomeType"))
        return []

    client._get = fake_get
    client.get_income(start_time=0, end_time=1000, income_type="REALIZED_PNL")
    assert seen == ["REALIZED_PNL"]
