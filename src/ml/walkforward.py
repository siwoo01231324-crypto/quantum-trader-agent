"""Expanding / rolling walk-forward splitter."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal
import numpy as np
import pandas as pd


@dataclass
class WalkForwardConfig:
    """Configuration for walk-forward splitting.

    Parameters
    ----------
    mode:
        ``"expanding"`` grows the training window each step.
        ``"rolling"`` uses a fixed-size sliding training window.
    train_window:
        Initial (expanding) or fixed (rolling) training size.
        ``pd.Timedelta`` or integer bar count.
    test_window:
        Out-of-sample window size per fold.
    step:
        Advance between consecutive test windows.
    min_train_samples:
        Minimum required training samples; folds with fewer are skipped.
    """

    mode: Literal["expanding", "rolling"] = "expanding"
    train_window: "pd.Timedelta | int" = 500
    test_window: "pd.Timedelta | int" = 100
    step: "pd.Timedelta | int" = 100
    min_train_samples: int = 500


class WalkForwardSplitter:
    """Generate train/test index pairs for walk-forward validation."""

    def __init__(self, config: WalkForwardConfig) -> None:
        self.config = config

    def split(self, index: pd.DatetimeIndex) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield (train_indices, test_indices) integer-position arrays.

        Parameters
        ----------
        index:
            Full DatetimeIndex of the dataset.
        """
        cfg = self.config
        use_timedelta = isinstance(cfg.train_window, pd.Timedelta)

        if use_timedelta:
            yield from self._split_timedelta(index, cfg)
        else:
            yield from self._split_int(len(index), cfg)

    # ------------------------------------------------------------------
    # Integer-bar implementation
    # ------------------------------------------------------------------

    def _split_int(
        self, n: int, cfg: WalkForwardConfig
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        train_w = int(cfg.train_window)
        test_w = int(cfg.test_window)
        step = int(cfg.step)

        test_start = train_w
        while test_start < n:
            test_end = min(test_start + test_w, n)

            if cfg.mode == "expanding":
                train_start = 0
            else:  # rolling
                train_start = max(0, test_start - train_w)

            train_idx = np.arange(train_start, test_start)
            test_idx = np.arange(test_start, test_end)

            if len(train_idx) >= cfg.min_train_samples and len(test_idx) > 0:
                yield train_idx, test_idx

            test_start += step

    # ------------------------------------------------------------------
    # Timedelta implementation
    # ------------------------------------------------------------------

    def _split_timedelta(
        self, index: pd.DatetimeIndex, cfg: WalkForwardConfig
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        train_w: pd.Timedelta = cfg.train_window  # type: ignore[assignment]
        test_w: pd.Timedelta = cfg.test_window  # type: ignore[assignment]
        step: pd.Timedelta = cfg.step  # type: ignore[assignment]

        test_start_time = index[0] + train_w

        while test_start_time < index[-1]:
            test_end_time = test_start_time + test_w

            if cfg.mode == "expanding":
                train_mask = index < test_start_time
            else:  # rolling
                train_mask = (index >= test_start_time - train_w) & (
                    index < test_start_time
                )

            test_mask = (index >= test_start_time) & (index < test_end_time)
            train_idx = np.where(train_mask)[0]
            test_idx = np.where(test_mask)[0]

            if len(train_idx) >= cfg.min_train_samples and len(test_idx) > 0:
                yield train_idx, test_idx

            test_start_time += step
