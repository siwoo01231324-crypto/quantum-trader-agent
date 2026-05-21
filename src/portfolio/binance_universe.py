"""Single source of truth for Binance USDT spot universe (#218 follow-up).

cs-tsmom-crypto-daily 가 (1) dashboard 측 신호 계산, (2) live broker
universe-klines fetch, (3) backtest 에서 모두 **같은 30종 universe** 를
보도록 hardcoded pin 을 제공한다.

이전 버그 (2026-05-21): dashboard 의 ``cs_tsmom_signals._refresh()`` 가
``bench_cs_tsmom_crypto.fetch_top_universe(30)`` 으로 매 호출마다 24h 거래량
상위 30 을 동적으로 잡는 바람에 메이저(BTC/ETH/SOL/...)가 거래량 변동으로
빠지면 score 가 NaN 으로 채워져 BUY 후보가 0 이 되는 문제. live 측은
이미 같은 모듈에 hardcoded 30종이 있었지만 두 곳이 분리되어 있어
inconsistency 가 surface 안 되고 잠복.

본 모듈은 단일 진실: 갱신 시 여기 한 곳만 수정하면 dashboard + live +
backtest 가 동시에 따라간다.

Pin policy:
- 6개월마다 (5/1, 11/1 부근) 24h 거래량 상위 30 재선정 → 본 list 교체
- 동시에 ``docs/specs/strategies/cs-tsmom-crypto-daily.md`` 의 "결과
  (YYYY-MM-DD)" 섹션과 ``PIN_DATE`` 갱신
- 변경 PR 의 description 에 (a) 빠진 종목, (b) 새로 들어온 종목,
  (c) 이유 (해당 일자 거래량 ranking 캡쳐) 명시
"""
from __future__ import annotations

PIN_DATE: str = "2026-05-21"

# Binance USDT spot 24h 거래량 상위 30종 (PIN_DATE 기준).
# Source: ``scripts/live_run.py`` 의 prior ``_BINANCE_TOP30`` (production 이
# 이미 사용 중이던 universe — backward-compat 우선).
BINANCE_USDT_TOP30: tuple[str, ...] = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT", "LTCUSDT", "UNIUSDT",
    "NEARUSDT", "ICPUSDT", "AAVEUSDT", "INJUSDT", "TAOUSDT", "ONDOUSDT",
    "TONUSDT", "SUIUSDT", "PEPEUSDT", "LAYERUSDT", "OSMOUSDT", "SAGAUSDT",
    "EURUSDT", "ZECUSDT", "AIUSDT", "KITEUSDT", "SPKUSDT", "CHIPUSDT",
)

assert len(BINANCE_USDT_TOP30) == 30, "BINANCE_USDT_TOP30 must contain exactly 30 symbols"


def get_universe() -> list[str]:
    """Return a mutable list copy (caller can sort/filter without mutating pin)."""
    return list(BINANCE_USDT_TOP30)
