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


# ── 스윙 4h 전략 전용 크립토 유니버스 (2026-06-30) ────────────────────────────
# 깨끗한 크립토 메이저 only — BINANCE_USDT_TOP30 와 달리 토큰화주식(TSLA/NVDA)·
# 상품(XAU/XAG)·forex(EUR) 가 섞이지 않는다. Binance 선물이 토큰화자산을 상장하면서
# 24h-거래량 동적 top-N 이 비-크립토로 오염되던 문제(2026-06-30 발견 — data/cache/
# binance_1h 에 TSLA·币安人生 등) 회피용 정적 allowlist. **유동성(최근 90일 4h
# close×volume) 내림차순** 으로 정렬 — get_universe()[:N] 슬라이스가 곧 top-N.
#
# 검증(2026-06-30 깨끗한 크립토 재분석, scripts/_swing_clean_majors_reanalysis.py):
#   - 투매반등: 확대할수록 PF 유지/상승 (2y top-100 PF 2.14, 1y 2.54) → top-100
#   - 돌파:    top-30 이 전 기간 최고 PF, 확대 시 단조 열화 (1y top-100 PF 1.05) → top-30
# 데이터: data/cache/swing_crypto_4h/*.parquet (scripts/_swing_setup_clean_cache.py).
# 갱신 시 셋업 스크립트 재실행 → 본 list 교체.
SWING_CRYPTO_UNIVERSE: tuple[str, ...] = (
    "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "WLDUSDT",
    "1000PEPEUSDT", "NEARUSDT", "SUIUSDT", "ADAUSDT", "EOSUSDT", "TONUSDT", "XLMUSDT",
    "MATICUSDT", "AVAXUSDT", "ENAUSDT", "FILUSDT", "LINKUSDT", "AAVEUSDT", "BCHUSDT",
    "ORDIUSDT", "DOTUSDT", "UNIUSDT", "LTCUSDT", "INJUSDT", "TRXUSDT", "DASHUSDT",
    "FETUSDT", "JTOUSDT", "XMRUSDT", "ARBUSDT", "ICPUSDT", "1000SHIBUSDT", "APTUSDT",
    "WIFUSDT", "HBARUSDT", "1000LUNCUSDT", "RENDERUSDT", "CHZUSDT", "OPUSDT", "TIAUSDT",
    "CRVUSDT", "AXSUSDT", "ALGOUSDT", "ETCUSDT", "PENDLEUSDT", "SEIUSDT", "LDOUSDT",
    "GALAUSDT", "ATOMUSDT", "JUPUSDT", "BLURUSDT", "DYDXUSDT", "STORJUSDT", "SANDUSDT",
    "ARUSDT", "PYTHUSDT", "COMPUSDT", "RUNEUSDT", "STXUSDT", "ENSUSDT", "VETUSDT",
    "THETAUSDT", "SNXUSDT", "GRTUSDT", "MANAUSDT", "ROSEUSDT", "QNTUSDT", "IMXUSDT",
    "KSMUSDT", "SUSHIUSDT", "NEOUSDT", "EGLDUSDT", "BATUSDT", "KAVAUSDT", "ANKRUSDT",
    "ZILUSDT", "1INCHUSDT", "YFIUSDT", "ZRXUSDT", "GMXUSDT", "MKRUSDT", "FTMUSDT",
)
