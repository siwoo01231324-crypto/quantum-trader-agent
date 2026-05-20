"""Ensemble wrapper: 4 live-scanner sub-strategies for Binance USDT-perp 1d.

Background: ``docs/background/51-live-scanner-bn1d-ensemble-validation.md``
("Candidate C" — STRONG 60% + WEAK 40%).

Pattern: a single strategy instance holds 4 sub-strategy instances. ``on_bar``
dispatches the same ctx to each, sums the *weights* of sub-strategies that
emit buy, scales by half-kelly, and returns one buy intent (or hold). The
wrapper maintains ONE position whose size is conviction-weighted by how
many sub-rules concur.

NOT equivalent to running the 4 strategies in parallel (which would maintain
4 independent positions). The robustness analysis (Candidate C in §3.4 of
the background note) modelled 4-parallel; wrapper results may differ. A
wrapper-specific bench is included in ``scripts/bench_live_scanner.py``
under the ``live_scanner_ensemble_bn1d`` entry — see §"Verified" in the
spec for the actual wrapper performance numbers.

Status: experimental. ``production.yaml`` entry is commented (gated by
``LIVE_SCANNER_BN1D_ENSEMBLE_ENABLED=1``). Do not enable live without paper
running 6 months + monthly PF/exp/MDD monitoring + 3-month PF<1 auto-trip.

Note on paradigm: this is a ``live-scanner`` paradigm strategy by frontmatter
declaration (intended ``LivePositionRiskManager`` automatic stop/TP), but the
empirically validated timeframe is ``1d`` — Binance 1m runs of the same 4
sub-strategies have PF<1 (9553e87 rejected). Live activation must pin
``timeframe: 1d``.
"""
from __future__ import annotations

from typing import ClassVar

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_bb_lower_bounce import LiveBbLowerBounce
from backtest.strategies.live_breakout_with_atr_stop import LiveBreakoutWithAtrStop
from backtest.strategies.live_oversold_with_divergence import LiveOversoldWithDivergence
from backtest.strategies.live_rsi_oversold_volume_spike import LiveRsiOversoldVolumeSpike


class LiveScannerEnsembleBn1d(LiveScannerMixin):
    """Ensemble wrapper — Candidate C (STRONG 60% + WEAK 40%, half-kelly).

    Sub-strategy weights (from validation §3.4):
      ``live_rsi_oversold_volume_spike``  0.30  (STRONG — DSR PASS)
      ``live_breakout_with_atr_stop``     0.30  (STRONG — DSR PASS)
      ``live_bb_lower_bounce``            0.20  (WEAK   — diversifier)
      ``live_oversold_with_divergence``   0.20  (WEAK   — diversifier)
    Sum = 1.0.

    Final size = ``default_size × Σ(weight of buying subs) × half_kelly``.
    So a single sub firing yields ``default_size × 0.2~0.3 × 0.5`` ≈ 0.005;
    all 4 firing yields ``default_size × 1.0 × 0.5`` = ``default_size × 0.5``.
    """

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    # Default sub-strategy weights — match Candidate C in background/51.
    SUB_WEIGHTS: ClassVar[dict[str, float]] = {
        "rsi_oversold": 0.30,
        "breakout_atr": 0.30,
        "bb_lower":     0.20,
        "oversold_div": 0.20,
    }

    # Half-Kelly scale — Candidate C uses 0.5 to bring MDD inside operable band.
    DEFAULT_HALF_KELLY: ClassVar[float] = 0.5

    MIN_HISTORY: ClassVar[int] = 60  # max(sub-strategy MIN_HISTORY) — safety margin

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        half_kelly: float | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size

        hk = half_kelly if half_kelly is not None else self.DEFAULT_HALF_KELLY
        if not 0 < hk <= 1.0:
            raise ValueError(f"half_kelly must be in (0, 1], got {hk}")
        self.half_kelly = hk

        w = dict(weights) if weights is not None else dict(self.SUB_WEIGHTS)
        # Strict validation — caller is expected to pass exactly the 4 keys.
        expected = set(self.SUB_WEIGHTS)
        if set(w) != expected:
            raise ValueError(
                f"weights must have keys exactly {sorted(expected)}, got "
                f"{sorted(w)}"
            )
        if any(v < 0 for v in w.values()):
            raise ValueError(f"weights must be non-negative, got {w}")
        total = sum(w.values())
        if total <= 0:
            raise ValueError(f"weights sum must be > 0, got {w}")
        # Normalise so callers don't have to sum to exactly 1.0.
        self.weights = {k: v / total for k, v in w.items()}

        # Sub-strategy instances. ``default_size`` is passed through but is
        # unused by the wrapper (we recompute size from weight × half_kelly).
        # Passing the same default keeps sub instances valid for direct
        # inspection / unit tests.
        self._subs: list[tuple[str, object, float]] = [
            ("rsi_oversold",
             LiveRsiOversoldVolumeSpike(default_size=default_size),
             self.weights["rsi_oversold"]),
            ("breakout_atr",
             LiveBreakoutWithAtrStop(default_size=default_size),
             self.weights["breakout_atr"]),
            ("bb_lower",
             LiveBbLowerBounce(default_size=default_size),
             self.weights["bb_lower"]),
            ("oversold_div",
             LiveOversoldWithDivergence(default_size=default_size),
             self.weights["oversold_div"]),
        ]

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        # Dispatch the same ctx to each sub. Collect those that emit "buy".
        firing: list[tuple[str, float]] = []
        for name, sub, w in self._subs:
            sig = await sub.on_bar(ctx)  # type: ignore[union-attr]
            if sig is not None and sig.action == "buy":
                firing.append((name, w))

        if not firing:
            return Signal(action="hold", size=0.0, reason="no_sub_buy")

        weight_sum = sum(w for _, w in firing)
        size = self.default_size * weight_sum * self.half_kelly
        names = ",".join(n for n, _ in firing)
        return Signal(
            action="buy",
            size=size,
            reason=(
                f"ensemble({names})|wsum={weight_sum:.2f}|hk={self.half_kelly}|"
                f"sz={size:.4f}"
            ),
        )
