"""Zipline-reloaded hello-world: SMA-cross on a single symbol.

Run:
    zipline run -f samples/zipline_hello.py \\
        --bundle quantopian-quandl --start 2022-01-01 --end 2023-12-31 \\
        -o out.pickle

For KRX usage, register a custom bundle `kr_daily` (see docs/background/11).
"""
from zipline.api import order_target_percent, record, symbol


def initialize(context):
    context.asset = symbol("AAPL")
    context.short = 20
    context.long = 60


def handle_data(context, data):
    hist = data.history(context.asset, "price", context.long, "1d")
    short_ma = hist[-context.short:].mean()
    long_ma = hist.mean()
    if short_ma > long_ma:
        order_target_percent(context.asset, 1.0)
    else:
        order_target_percent(context.asset, 0.0)
    record(short=short_ma, long=long_ma)
