"""Regression test — fetch_klines 가 반드시 Futures endpoint (fapi.binance.com)
사용해야 함 (Spot endpoint 사용 시 Futures-only 심볼 fetch fail 회귀).

2026-06-02 incident:
  ``binance_top_dynamic`` 가 top-100 USDT-perp 를 가져온 후, kst-hours +
  short-whitelist 가 그 universe 의 1h klines 를 ``fetch_klines`` 로 요청.
  당시 ``fetch_klines`` 가 ``api.binance.com/api/v3/klines`` (Spot) 사용 →
  1000PEPEUSDT / AMDUSDT / BSBUSDT / ARMUSDT / BZUSDT 등 Futures-only 다수
  HTTP 400 → retry 3회 × 0.8s = ~3s × 40+ 종목 = dispatch 2분+ 지연 →
  orchestrator 가 universe panel 못 받고 시그널 emit 0 건. 동시 가동 중
  ``live-airborne-bb-reversal-kst-hours`` 와 ``live-airborne-short-
  whitelist-v1`` 둘 다 영향. 이전 잘 돌던 run 의 13884 signals 시점은 PR
  머지 직전이라 동작했지만 머지 후 재시작 시점부터 회귀.

본 테스트는 함수 소스가 ``fapi.binance.com`` 을 contain 하고 ``api.binance.com``
(top-level Spot host) 는 사용 안 한다는 정적 검증.
"""
from __future__ import annotations

import inspect


def test_fetch_klines_uses_futures_endpoint() -> None:
    from brokers.binance import universe_quote

    src = inspect.getsource(universe_quote.fetch_klines)
    assert "fapi.binance.com/fapi/v1/klines" in src, (
        "fetch_klines must use Binance USDT-M Futures endpoint "
        "(fapi.binance.com/fapi/v1/klines). Spot endpoint causes HTTP 400 "
        "on Futures-only symbols (1000PEPEUSDT / AMDUSDT etc.) and stalls "
        "orchestrator universe fetch."
    )
    # Spot host 의 klines path 는 등장하면 안 됨 (24h ticker 는 별도 함수에 있음)
    assert "api.binance.com/api/v3/klines" not in src, (
        "fetch_klines must NOT use Spot klines endpoint — Futures-only "
        "symbols return HTTP 400."
    )


def test_fetch_24h_tickers_unchanged() -> None:
    """``fetch_24h_tickers`` 는 Spot ticker 유지 (별도 의도). 본 fix 의 영향 범위 아님."""
    from brokers.binance import universe_quote

    src = inspect.getsource(universe_quote.fetch_24h_tickers)
    assert "api.binance.com/api/v3/ticker/24hr" in src
