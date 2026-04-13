"""KR personal tax calculator (2026 law).

Spec: docs/specs/tax-automation.md
- Transaction tax: KOSPI 0.05% + 농특세 0.15% = 0.20%; KOSDAQ/K-OTC 0.20%
- Dividend WHT: 15.4%
- Capital gains (대주주 only): 22% up to 3억 KRW, 27.5% above; 250만 KRW basic deduction
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable


class Market(str, Enum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    K_OTC = "K-OTC"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


# 매도가 기준 합계 (거래세 + 농특세)
TRANSACTION_TAX_RATES: dict[Market, float] = {
    Market.KOSPI: 0.0005 + 0.0015,   # 0.20%
    Market.KOSDAQ: 0.0020,           # 0.20%
    Market.K_OTC: 0.0020,            # 0.20%
}

DIVIDEND_WHT_RATE = 0.154            # 15.4% (소득세 14% + 지방세 1.4%)

CAPITAL_GAINS_BASIC_DEDUCTION = 2_500_000     # 연 250만 원
CAPITAL_GAINS_BRACKET_LIMIT = 300_000_000     # 3억 원
CAPITAL_GAINS_RATE_LOW = 0.22                 # 22%
CAPITAL_GAINS_RATE_HIGH = 0.275               # 27.5%


@dataclass
class Trade:
    ts: datetime
    symbol: str
    market: Market
    side: Side
    qty: int
    price: float
    fee: float = 0.0


@dataclass
class Dividend:
    ts: datetime
    symbol: str
    gross: float


@dataclass
class RealizedLot:
    symbol: str
    sell_ts: datetime
    qty: int
    proceeds: float       # 매도대금 (gross, before tax/fee)
    cost: float           # 대응 매수원가
    sell_fee: float       # 증권사 수수료 (매도)
    txn_tax: float        # 증권거래세
    @property
    def gain(self) -> float:
        # 양도차익 = 매도대금 - 매수원가 - 매도수수료 - 거래세
        return self.proceeds - self.cost - self.sell_fee - self.txn_tax


@dataclass
class TaxResult:
    transaction_tax: float
    dividend_wht: float
    realized_gain: float          # 통산 양도차익 (대주주 여부 무관, 정보용)
    capital_gains_tax: float      # 대주주 모드일 때만 양수
    realized_lots: list[RealizedLot] = field(default_factory=list)


@dataclass
class TaxCalculator:
    """FIFO matching across BUY/SELL of same symbol.

    `is_major_shareholder=True` 일 때만 양도세를 산정한다 (일반 투자자는 상장주식 비과세).
    """
    is_major_shareholder: bool = False
    carry_loss: float = 0.0       # 전기 이월결손금 (양수로 입력, 차감 시 음의 효과)

    def compute(
        self,
        trades: Iterable[Trade],
        dividends: Iterable[Dividend] = (),
    ) -> TaxResult:
        trades = sorted(trades, key=lambda t: t.ts)
        # symbol -> deque of [remaining_qty, price_per_share, fee_per_share]
        books: dict[str, deque[list[float]]] = {}
        lots: list[RealizedLot] = []
        total_txn_tax = 0.0

        for t in trades:
            if t.side is Side.BUY:
                fee_per_share = (t.fee / t.qty) if t.qty else 0.0
                books.setdefault(t.symbol, deque()).append(
                    [float(t.qty), float(t.price), fee_per_share]
                )
                continue

            # SELL
            proceeds = t.price * t.qty
            txn_rate = TRANSACTION_TAX_RATES[t.market]
            txn_tax = proceeds * txn_rate
            total_txn_tax += txn_tax

            remaining = t.qty
            cost = 0.0
            book = books.get(t.symbol)
            if book is None:
                # 빈 매도(공매도 등) — 본 v1 범위 외, 비용 0 처리하되 경고용 lot 기록
                book = books.setdefault(t.symbol, deque())
            while remaining > 0 and book:
                lot = book[0]
                take = min(remaining, lot[0])
                cost += take * lot[1] + take * lot[2]   # 매수원가 + 매수수수료 안분
                lot[0] -= take
                remaining -= take
                if lot[0] == 0:
                    book.popleft()

            lots.append(RealizedLot(
                symbol=t.symbol,
                sell_ts=t.ts,
                qty=t.qty,
                proceeds=proceeds,
                cost=cost,
                sell_fee=t.fee,
                txn_tax=txn_tax,
            ))

        realized_gain = sum(l.gain for l in lots)

        # 배당
        div_wht = sum(d.gross for d in dividends) * DIVIDEND_WHT_RATE

        # 양도세 (대주주만)
        cg_tax = 0.0
        if self.is_major_shareholder:
            taxable = realized_gain - self.carry_loss - CAPITAL_GAINS_BASIC_DEDUCTION
            if taxable > 0:
                low_part = min(taxable, CAPITAL_GAINS_BRACKET_LIMIT)
                high_part = max(0.0, taxable - CAPITAL_GAINS_BRACKET_LIMIT)
                cg_tax = low_part * CAPITAL_GAINS_RATE_LOW + high_part * CAPITAL_GAINS_RATE_HIGH

        return TaxResult(
            transaction_tax=total_txn_tax,
            dividend_wht=div_wht,
            realized_gain=realized_gain,
            capital_gains_tax=cg_tax,
            realized_lots=lots,
        )
