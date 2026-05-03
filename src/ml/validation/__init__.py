"""AFML-based multi-testing correction toolkit.

Implements the deflated Sharpe ratio (DSR), the probabilistic Sharpe ratio
(PSR), and the combinatorially symmetric cross-validation (CSCV) used to
estimate the probability of backtest overfitting (PBO).

References
----------
Bailey, D.H. & López de Prado, M. (2014). The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting, and Non-Normality.
    Journal of Portfolio Management, 40(5), 94-107.
Bailey, D.H., Borwein, J.M., López de Prado, M., Zhu, Q.J. (2014).
    The Probability of Backtest Overfitting. Journal of Computational
    Finance, 20(4), 39-69.
"""
from __future__ import annotations

from src.ml.validation.deflated_sharpe import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
from src.ml.validation.cscv import combinatorial_symmetric_cv
from src.ml.validation.pbo import probability_of_backtest_overfitting

__all__ = [
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "combinatorial_symmetric_cv",
    "probability_of_backtest_overfitting",
]
