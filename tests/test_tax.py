"""Sample fill cases (1~3 trades) → expected tax amounts."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pytest

from src.tax import (
    Dividend,
    Market,
    Side,
    TaxCalculator,
    Trade,
    write_annual_csv,
)


def _t(ts: str, symbol: str, market: Market, side: Side, qty: int, price: float, fee: float = 0.0) -> Trade:
    return Trade(datetime.fromisoformat(ts), symbol, market, side, qty, price, fee)


def test_single_kospi_sell_transaction_tax():
    """1건: KOSPI 매수 100 @ 10,000, 매도 100 @ 11,000.
    매도대금 1,100,000 × 0.20% = 2,200 거래세.
    양도차익 = 1,100,000 - 1,000,000 - 0 - 2,200 = 97,800 (일반 투자자 비과세).
    """
    cal = TaxCalculator(is_major_shareholder=False)
    trades = [
        _t("2026-01-05T10:00", "005930", Market.KOSPI, Side.BUY, 100, 10_000),
        _t("2026-02-10T10:00", "005930", Market.KOSPI, Side.SELL, 100, 11_000),
    ]
    r = cal.compute(trades)
    assert r.transaction_tax == pytest.approx(2_200, abs=0.5)
    assert r.realized_gain == pytest.approx(97_800, abs=0.5)
    assert r.capital_gains_tax == 0.0


def test_kosdaq_sell_uses_020_percent():
    cal = TaxCalculator()
    trades = [
        _t("2026-01-05T10:00", "035720", Market.KOSDAQ, Side.BUY, 50, 50_000),
        _t("2026-03-01T10:00", "035720", Market.KOSDAQ, Side.SELL, 50, 60_000),
    ]
    r = cal.compute(trades)
    # 매도 3,000,000 × 0.20% = 6,000
    assert r.transaction_tax == pytest.approx(6_000, abs=0.5)


def test_dividend_wht_154_percent():
    cal = TaxCalculator()
    divs = [Dividend(datetime(2026, 4, 1), "005930", gross=1_000_000)]
    r = cal.compute(trades=[], dividends=divs)
    assert r.dividend_wht == pytest.approx(154_000, abs=0.5)


def test_major_shareholder_capital_gains_low_bracket():
    """대주주, 양도차익 1억 → 기본공제 250만 차감 → 9750만 × 22%."""
    cal = TaxCalculator(is_major_shareholder=True)
    # 매수 1주 @ 1억, 매도 1주 @ 2억 (간단화).
    trades = [
        _t("2026-01-05T10:00", "X", Market.KOSPI, Side.BUY, 1, 100_000_000),
        _t("2026-06-05T10:00", "X", Market.KOSPI, Side.SELL, 1, 200_000_000),
    ]
    r = cal.compute(trades)
    # 거래세 = 200,000,000 × 0.002 = 400,000
    # 양도차익 = 200,000,000 - 100,000,000 - 0 - 400,000 = 99,600,000
    assert r.transaction_tax == pytest.approx(400_000, abs=1)
    assert r.realized_gain == pytest.approx(99_600_000, abs=1)
    expected_taxable = 99_600_000 - 2_500_000
    expected_cg = expected_taxable * 0.22
    assert r.capital_gains_tax == pytest.approx(expected_cg, abs=1)


def test_major_shareholder_capital_gains_progressive():
    """대주주, 양도차익 5억 → 22% (3억 한도) + 27.5% (초과분)."""
    cal = TaxCalculator(is_major_shareholder=True)
    trades = [
        _t("2026-01-05T10:00", "Y", Market.KOSDAQ, Side.BUY, 1, 100_000_000),
        _t("2026-06-05T10:00", "Y", Market.KOSDAQ, Side.SELL, 1, 600_000_000),
    ]
    r = cal.compute(trades)
    # 거래세 = 600M × 0.002 = 1,200,000
    # 양도차익 = 500,000,000 - 1,200,000 = 498,800,000
    gain = 498_800_000
    taxable = gain - 2_500_000
    expected = 300_000_000 * 0.22 + (taxable - 300_000_000) * 0.275
    assert r.capital_gains_tax == pytest.approx(expected, abs=1)


def test_fifo_partial_matching_three_trades():
    """3건: 매수 100 @ 10000, 매수 100 @ 12000, 매도 150 @ 13000.
    FIFO: 100@10000 + 50@12000 = 매수원가 1,600,000.
    매도대금 1,950,000, 거래세 3,900.
    양도차익 = 1,950,000 - 1,600,000 - 3,900 = 346,100."""
    cal = TaxCalculator()
    trades = [
        _t("2026-01-05T10:00", "Z", Market.KOSPI, Side.BUY, 100, 10_000),
        _t("2026-02-05T10:00", "Z", Market.KOSPI, Side.BUY, 100, 12_000),
        _t("2026-03-05T10:00", "Z", Market.KOSPI, Side.SELL, 150, 13_000),
    ]
    r = cal.compute(trades)
    assert r.transaction_tax == pytest.approx(3_900, abs=0.5)
    assert r.realized_gain == pytest.approx(346_100, abs=0.5)


def test_carry_loss_reduces_taxable_for_major():
    cal = TaxCalculator(is_major_shareholder=True, carry_loss=10_000_000)
    trades = [
        _t("2026-01-05T10:00", "X", Market.KOSPI, Side.BUY, 1, 100_000_000),
        _t("2026-06-05T10:00", "X", Market.KOSPI, Side.SELL, 1, 130_000_000),
    ]
    r = cal.compute(trades)
    gain = r.realized_gain
    taxable = gain - 10_000_000 - 2_500_000
    expected = taxable * 0.22
    assert r.capital_gains_tax == pytest.approx(expected, abs=1)


def test_csv_report_contains_rows(tmp_path: Path):
    cal = TaxCalculator()
    trades = [
        _t("2026-01-05T10:00", "005930", Market.KOSPI, Side.BUY, 100, 10_000),
        _t("2026-02-10T10:00", "005930", Market.KOSPI, Side.SELL, 100, 11_000),
    ]
    r = cal.compute(trades)
    out = tmp_path / "report.csv"
    write_annual_csv(r, out)
    assert out.exists()
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    # header + 1 lot + blank + 4 summary lines
    assert rows[0][0] == "종목"
    assert rows[1][0] == "005930"
    assert any("증권거래세 합계" in row[0] for row in rows if row)
