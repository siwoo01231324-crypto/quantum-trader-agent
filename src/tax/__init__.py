"""Tax automation module (KR personal, 2026 law).

See docs/specs/tax-automation.md
"""
from .calculator import (
    Dividend,
    Market,
    Side,
    TaxCalculator,
    TaxResult,
    Trade,
    TRANSACTION_TAX_RATES,
)
from .reporter import write_annual_csv

__all__ = [
    "Dividend",
    "Market",
    "Side",
    "TaxCalculator",
    "TaxResult",
    "Trade",
    "TRANSACTION_TAX_RATES",
    "write_annual_csv",
]
