import pandas as pd
from backtest.protocol import Bar, Signal, Strategy
from signals.rsi import compute_rsi, detect_divergence


class MomoBtcV2:
    """BTC 15m Momentum v2 (MVP: long-only).

    Bullish divergence -> buy (100% equity).
    Bearish divergence -> exit to cash.
    """

    RSI_PERIOD: int = 14
    LOOKBACK: int = 14

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        min_bars = self.RSI_PERIOD + self.LOOKBACK * 2 + 1
        if len(history) < min_bars:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        rsi = compute_rsi(close, self.RSI_PERIOD)
        div = detect_divergence(close, rsi, self.LOOKBACK)

        latest = div.iloc[-1]
        if latest == "bullish":
            return Signal(action="buy", size=1.0, reason="bullish divergence")
        elif latest == "bearish":
            return Signal(action="sell", size=1.0, reason="bearish divergence")
        return Signal(action="hold", size=0.0, reason="no signal")
