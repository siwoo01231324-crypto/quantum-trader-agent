# [#96] KIS 분봉 시세 fetcher + momo_kis_v1 전략 (KRX 메타라벨러 선행) — 구현 계획

> 작성: 2026-04-25

---

## 완료 기준 (이슈 본문에서 추출)

- [x] `fetch_kis_intraday_ohlcv(symbol, start, end, interval)` OHLCV_SCHEMA 반환, 429 재시도 준수, KIS 분봉 제약(최근 30일) 문서화
- [x] `momo_kis_v1` AsyncStrategy 백테스트 단위 테스트 통과 (기본 symbol=005930, 15m)
- [x] `register_strategy_returns("momo_kis_v1", series)` 계약 준수
- [x] KRX 거래시간 gate + `is_krx_holiday` 2중 안전망 (harness + self-guard) 정상 동작
- [x] 단위 + 통합 테스트 20+ 건 green
- [x] `docs/work/active/000096-kis-intraday-fetcher/02_implementation.md` 에 일수익률 시계열 샘플 + 파라미터 기록
- [x] `docs/specs/strategies/momo-kis-v1.md` 작성, frontmatter `type=strategy`, `register_strategy_returns(...)` 섹션 명시

## 개발 체크리스트

- [x] 테스트 코드 포함
- [x] 해당 디렉토리 .ai.md 최신화
- [x] 불변식 위반 없음 (`python scripts/check_invariants.py --strict`)

---

## 구현 계획

### 코드베이스 실측 결과

- `src/brokers/kis/tr_ids.py` — daily TR `TR_ID_DAILY_PRICE = "FHKST03010100"` (line 44). 분봉 TR `FHKST03010200` 는 **부재**. `tr_ids_for(paper)` 는 주문 TR 만 다룸 (read-only inquiry TR-IDs 는 paper 변형 없음, line 30 코멘트).
- `src/brokers/kis/schemas.py` — `KISDailyBar` (lines 122-137) `date/open/high/low/close/volume/trade_amt`, `_coerce_float` 검증자. `KISIntradayBar` 부재.
- `src/brokers/kis/price_client.py` — `fetch_daily_ohlcv_raw(client, symbol, start, end, period="D") -> list[KISDailyBar]` (line 57). 헬퍼 `_call_with_429_retry(client, params)` (line 32) — `_429_MAX_RETRIES=3`, `_429_BASE_DELAY=1.0`, `_RATE_LIMIT_SLEEP=0.5`. `_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"` (line 29). KIS 분봉 API 의 path는 `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` (TR `FHKST03010200`).
- `src/data_lake/fetcher.py` — `fetch_kis_daily_ohlcv(symbol, start, end, *, auth, app_key, app_secret, cano, acnt_prdt_cd, paper=True) -> pd.DataFrame` (line 197). KIS bar→OHLCV_SCHEMA dict 컨버전 (line 244-268), `freq="1d"`, `source="kis"`, ts = `f"{date} 15:30:00 Asia/Seoul" → UTC`. KISClient 생성 후 raw 호출.
- `src/data_lake/schema.py` — `OHLCV_SCHEMA` 12 columns: `symbol, ts, freq, open, high, low, close, volume, vwap, trade_count, source, ingested_at`. `partition_path("ohlcv", symbol=..., ts_year=..., ts_month=..., freq=...)` 형식.
- `src/backtest/protocol.py` — `AsyncStrategy` Protocol: `async def on_bar(self, ctx: object) -> "Signal | None"` (line 51). `Signal(action, size, reason, *, expected_return, win_probability, confidence)` kw-only.
- `src/backtest/strategies/breakout_donchian.py` — KRX 일봉 AsyncStrategy 의 정확한 reference. `_is_my_bar_boundary(ts: pd.Timestamp) -> bool` (line 58): `from universe.krx_calendar import is_krx_holiday, KST`, `ts.astimezone(KST)`, `ts_kst.time() == time(15, 30)`, `ts_kst.weekday() < 5`, `not is_krx_holiday(ts_kst.date())`. `on_bar(ctx)` 에서 `ctx["ts"]`, `ctx["market_snapshot"]` 접근 (line 165-).
- `src/backtest/strategies/momo_btc_v2.py` — `detect_divergence(close, rsi, LOOKBACK)` from `signals.rsi`, `required_factors: ClassVar[list[str]] = ["rsi"]` (line 49), 사이저는 `risk.sizing.{ewma_sigma, kelly_continuous, fractional_kelly, vol_target, consensus_kelly}`. 단, 이 전략은 sync `Strategy` Protocol — momo_kis_v1 은 AsyncStrategy 로 가야 하므로 `breakout_donchian` 의 `on_bar(ctx)` 시그니처를 따른다.
- `src/universe/krx_calendar.py` — `KST = pytz.timezone("Asia/Seoul")`, `is_krx_holiday(d: date) -> bool`, `is_krx_trading_hours(ts: datetime) -> bool` (09:00~15:30 KST + holiday/weekend gate). 2025·2026 정적 휴일 frozenset. **장중 분봉 게이트는 `is_krx_trading_hours` 가 직접 사용 가능**.
- `src/universe/kospi200.py` — `KOSPI200_CONSTITUENTS: list[dict]`, `get_codes() -> list[str]`. 005930 포함 확인.
- `src/portfolio/_async_orchestrator.py` — `AsyncStrategyOrchestrator.register_strategy(sid, strategy)`, `register_strategy_returns(sid, series)` (line 65). `run_bar(ts, market_snapshot, strategies=...)` 가 `ctx={"ts": ts, "market_snapshot": market_snapshot}` 빌드.
- `src/brokers/kis/rest.py` — `KISClient(auth, app_key, app_secret, cano, acnt_prdt_cd, paper=True)`, paper base_url `openapivts.koreainvestment.com:29443`. `_get(path, tr_id, params)` private 메서드 (raw client에서 사용).
- `tests/test_broker_kis_price.py` + `tests/test_fetch_kis_daily_ohlcv.py` — mock 패턴 확립: `MagicMock` client, `client._get.return_value/side_effect`, `patch("src.brokers.kis.price_client.time")` 으로 sleep 우회, `requests.HTTPError(response=mock_429)` 으로 429 시뮬.
- `docs/specs/strategies/momo-btc-v2.md` — frontmatter 참조 (type: strategy, id, name, status, instruments, timeframe, uses_signals, risk_rules, owner, created, sharpe_bt, sharpe_live, tags). 본문 `## 진입 / ## 진입 크기 / ## 청산 / ## 훅 소비 / ## 관련 노트` 패턴.
- `docs/schemas/note-schemas.md` — Strategy 필수 필드 확정.
- `src/backtest/strategies/.ai.md` — "리스크 연동 (필수)" 체크리스트 4개 (returns 산출 경로, strategy_id snake_case, spec 기재, 단위 테스트 1건).

### Task Flow

#### 1) KIS 분봉 TR 상수 + Pydantic 스키마

`src/brokers/kis/tr_ids.py` 끝에 추가:
```python
TR_ID_INTRADAY_PRICE = "FHKST03010200"  # /quotations/inquire-time-itemchartprice — intraday minute bars
```

`src/brokers/kis/schemas.py` 에 `KISDailyBar` 패턴 그대로 복제하여 추가:
```python
class KISIntradayBar(BaseModel):
    """Single intraday OHLCV bar from KIS FHKST03010200."""
    date: str        # "YYYYMMDD" from stck_bsop_date
    time: str        # "HHMMSS"   from stck_cntg_hour
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_amt: float

    @field_validator("open","high","low","close","volume","trade_amt", mode="before")
    @classmethod
    def _coerce_float(cls, v): ...
```

검증: `tests/brokers/kis/test_intraday_schemas.py` — 문자열 필드 → float 강제, time HHMMSS 보존, 빈문자 → 0.0.

#### 2) raw client `fetch_intraday_ohlcv_raw`

`src/brokers/kis/price_client.py` 안에 추가하되 `_PATH_INTRADAY` 상수 분리:

```python
_PATH_INTRADAY = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"

def _call_intraday_with_429_retry(client, params): ...

def fetch_intraday_ohlcv_raw(
    client: "KISClient",
    symbol: str,
    target_date: str,           # "YYYYMMDD" — KIS 분봉은 "당일" 단위 호출
    interval: str = "15",       # "1"|"3"|"5"|"10"|"15"|"30"|"60"
    *,
    end_hhmmss: str = "153000",
) -> list[KISIntradayBar]:
```

KIS 분봉 API 파라미터 (실측):
- `FID_COND_MRKT_DIV_CODE="J"`, `FID_INPUT_ISCD=symbol`,
- `FID_INPUT_HOUR_1=end_hhmmss` (조회 종료시각, 분봉 API 는 종료시각부터 역방향 30개씩 반환),
- `FID_PW_DATA_INCU_YN="N"` 시작 (시간외 제외; 실측 후 결정),
- `FID_ETC_CLS_CODE=""`, continuation 토큰은 `CTX_AREA_FK100/NK100`.

분봉 API 의 `output2` 가 newest-first 30 행씩 반환 → 일봉 패턴처럼 페이지네이션 + 시각이 09:00 미만으로 떨어질 때 break. 일봉과 똑같이 마지막에 reverse() 로 chronological 정렬.

`_RATE_LIMIT_SLEEP = 0.5`, `_429_*` 상수 재사용 (모듈 레벨 공유).

검증: `tests/brokers/kis/test_intraday_price_client.py` — 단일 페이지, 페이지네이션 (2 pages), 빈 응답, 429 retry 성공, 429 exhausted, sleep 0.5s 보장. mock client 패턴은 기존 `test_broker_kis_price.py` 그대로 답습.

#### 3) data_lake 어댑터 `fetch_kis_intraday_ohlcv`

`src/data_lake/fetcher.py` 끝에 추가:

```python
def fetch_kis_intraday_ohlcv(
    symbol: str,
    start: str,                  # "YYYY-MM-DD"
    end: str,                    # "YYYY-MM-DD"
    interval: str = "15",        # "1|3|5|10|15|30|60" minute
    *,
    auth: "KISAuth",
    app_key: str,
    app_secret: str,
    cano: str,
    acnt_prdt_cd: str,
    paper: bool = True,
) -> pd.DataFrame:
    """KIS 분봉 OHLCV → OHLCV_SCHEMA DataFrame.

    KIS API 제약: 당일 + 최근 30일까지만 조회 가능 (docstring 명시 + warning 로그).
    1년치 데이터는 일자별 loop + 0.5s sleep + 429 재시도.
    Holiday/weekend skip via is_krx_holiday + weekday().
    """
```

내부 로직:
1. `start/end` → datetime, KRX `pd.bdate_range` 로 영업일 enumerate.
2. 각 영업일마다 `is_krx_holiday(d)` 로 추가 필터.
3. `today = datetime.now(KST).date()`; `if d < today - timedelta(days=30): log.warning("KIS intraday >30d limit"); continue` (또는 `raise ValueError` — 가이드에서는 문서화 + 정상 진행 권장).
4. KISClient 1회 생성 (loop 밖) → 일자별 `fetch_intraday_ohlcv_raw(client, symbol, d.strftime("%Y%m%d"), interval=interval)` 호출 + 호출 사이 `time.sleep(0.5)`.
5. 각 bar → OHLCV record. `ts` 계산: `f"{bar.date[:4]}-{bar.date[4:6]}-{bar.date[6:8]} {bar.time[:2]}:{bar.time[2:4]}:{bar.time[4:6]}"` 를 `tz="Asia/Seoul"` 으로 파싱 → `tz_convert("UTC")`. `freq=f"{interval}m"` (e.g. "15m"). `source="kis"`. `vwap = trade_amt/volume if volume>0 else 0.0`. `trade_count=0`.
6. 빈 결과는 `pd.DataFrame(columns=list(OHLCV_SCHEMA.keys()))`.

검증: `tests/data_lake/test_fetch_kis_intraday_ohlcv.py` 5건 — schema 컬럼 보장, source==kis, freq=="15m", ts UTC + KST 변환 정확성, 빈 응답, 30일 제약 워닝 발생, 일자별 sleep 0.5s 호출 횟수 검증 (mocked `time.sleep`).

#### 4) AsyncStrategy `momo_kis_v1`

신설: `src/backtest/strategies/momo_kis_v1.py` (`breakout_donchian.py` 의 AsyncStrategy 패턴 + `momo_btc_v2.py` 의 RSI divergence 로직 결합).

```python
from datetime import time
from typing import ClassVar
import pandas as pd
from backtest.protocol import AsyncStrategy, Signal
from signals.rsi import detect_divergence
from risk.sizing import ewma_sigma, kelly_continuous, fractional_kelly

class MomoKisV1:
    required_factors: ClassVar[list[str]] = ["rsi"]
    SYMBOL_DEFAULT = "005930"
    RSI_PERIOD = 14
    LOOKBACK = 14
    INTERVAL_MIN = 15

    def __init__(
        self,
        *,
        symbol: str = "005930",
        sizing_mode: str = "half-kelly",
        sizing_lookback: int = 60,
        kelly_k: float = 0.5,
        target_annual: float = 0.15,
        periods_per_year: int = 26 * 252,   # 6.5h * 4 (15m bars/day) * 252 trading days
        ewma_lam: float = 0.94,
    ): ...

    def _is_my_bar_boundary(self, ts: pd.Timestamp) -> bool:
        from universe.krx_calendar import KST, is_krx_holiday
        ts_kst = ts.astimezone(KST) if ts.tzinfo else ts
        if ts_kst.weekday() >= 5 or is_krx_holiday(ts_kst.date()):
            return False
        t = ts_kst.time()
        if not (time(9, 0) <= t <= time(15, 30)):
            return False
        return (t.minute % self.INTERVAL_MIN == 0) and t.second == 0

    async def on_bar(self, ctx) -> Signal | None:
        ts = ctx["ts"]
        if not self._is_my_bar_boundary(ts):
            return Signal(action="hold", size=0.0, reason="not my bar")
        snap = ctx["market_snapshot"]
        history: pd.DataFrame = snap.get("history")
        rsi: pd.Series = ctx.get("factors", {}).get("rsi", pd.Series(dtype=float))
        # warmup
        min_bars = self.RSI_PERIOD + self.LOOKBACK*2 + 1
        if history is None or len(history) < min_bars:
            return Signal(action="hold", size=0.0, reason="warmup")
        div = detect_divergence(history["close"], rsi, self.LOOKBACK)
        latest = div.iloc[-1]
        if latest == "bullish":
            size = self._entry_size(history["close"])
            return Signal(action="buy", size=size, reason="bullish divergence", ...)
        if latest == "bearish":
            return Signal(action="sell", size=1.0, reason="bearish divergence")
        return Signal(action="hold", size=0.0, reason="no signal")
```

`_is_my_bar_boundary` 의 **2중 안전망**: (a) harness (orchestrator) 가 KRX 거래시간 외에는 ctx 를 안 흘려도 (b) 전략 자체에서 holiday + 거래시간 + 15m 바운더리 모두 검사 → 어느 한쪽이 무너져도 leakage 차단.

검증: `tests/backtest/strategies/test_momo_kis_v1.py` 7건 — `_is_my_bar_boundary` (장중 15분 단위 True / 09:07 False / 16:00 False / 토요일 False / 휴일 False / KST aware vs naive), warmup 단계 hold, bullish→buy size>0, bearish→sell, 휴일 입력 → hold "not my bar", VI 단일가 close 동일가 (returns=0) 시 sigma=0 → kelly=0 → size=0 fallback hold.

#### 5) Spec 노트 + 카탈로그 + .ai.md 갱신

**신설** `docs/specs/strategies/momo-kis-v1.md`:
```yaml
---
type: strategy
id: momo-kis-v1
name: KIS KRX 15m Momentum v1
status: backtest
instruments: [krx-005930]
timeframe: 15m
uses_signals: [rsi-divergence]
risk_rules: [max-drawdown-5pct]
owner: siwoo
created: 2026-04-25
sharpe_bt: null
sharpe_live: null
tags: [momentum, krx, intraday]
---
```
본문 섹션 (`momo-btc-v2.md` 형식 답습):
- `## 진입` — bullish RSI divergence 시 long
- `## 진입 크기` — half-kelly + ewma_sigma(λ=0.94)
- `## 청산` — bearish divergence 시 전량 청산, KRX 마감 시 강제 평탄
- `## 바 바운더리` — KST 09:00~15:30, 15분 단위, `is_krx_holiday` 게이트
- `## 리스크 연동` — `register_strategy_returns("momo_kis_v1", series)` 코드 블록 명시 (필수)
- `## 관련 노트` — `[[rsi-divergence]]` `[[max-drawdown-5pct]]` `[[19-portfolio-risk]]` `[[20-position-sizing]]`

**갱신** `src/backtest/strategies/.ai.md` — 구조 섹션에 `momo_kis_v1.py` 한 줄 추가 ("KIS KRX 15m 모멘텀 — RSI divergence 진입, 단일종목 005930, KRX 거래시간 gate. (#96)").

#### 6) Orchestrator 등록 + 일수익률 export + 실측 샘플

`tests/backtest/strategies/test_momo_kis_v1.py` (또는 `tests/test_portfolio_orchestrator_async.py` 확장) 에 통합 테스트 2건:
1. `MomoKisV1` instance → `AsyncStrategyOrchestrator.register_strategy("momo_kis_v1", s)` → mock daily returns Series → `register_strategy_returns` → `refresh_portfolio_risk()` 가 None 이 아닌 report 반환.
2. `run_bar(ts=KST 10:00, market_snapshot={"symbol":"005930","price":..., "history":..., "ohlcv_history":...})` 호출 → 진입 신호 발생 시 `OrderIntent` 생성, 거래시간 외 ts → 빈 리스트.

**문서 산출** `docs/work/active/000096-kis-intraday-fetcher/02_implementation.md`:
- 짧은 backtest snippet 작성 (mocked or 실제 30일 분봉) → daily returns Series 인쇄/기록.
- 파라미터 (sizing_mode, kelly_k, ewma_lam, RSI_PERIOD, LOOKBACK) 와 결과 (sharpe, mdd) 기록.

### 변경 파일 매트릭스

| 파일 | 변경 종류 | AC 매핑 |
|------|----------|---------|
| `src/brokers/kis/tr_ids.py` | append (`TR_ID_INTRADAY_PRICE`) | AC1 |
| `src/brokers/kis/schemas.py` | append (`KISIntradayBar`) | AC1 |
| `src/brokers/kis/price_client.py` | append (`fetch_intraday_ohlcv_raw`, `_PATH_INTRADAY`, `_call_intraday_with_429_retry`) | AC1, AC4 |
| `src/data_lake/fetcher.py` | append (`fetch_kis_intraday_ohlcv`) | AC1, AC4 |
| `src/backtest/strategies/momo_kis_v1.py` | new | AC2, AC3, AC4 |
| `src/backtest/strategies/.ai.md` | edit (한 줄 추가) | AC2 |
| `docs/specs/strategies/momo-kis-v1.md` | new | AC7 |
| `docs/work/active/000096-kis-intraday-fetcher/02_implementation.md` | new | AC6 |
| `tests/brokers/kis/test_intraday_schemas.py` | new (3건) | AC5 |
| `tests/brokers/kis/test_broker_kis_intraday.py` | new (5건) | AC1, AC5 |
| `tests/data_lake/test_fetch_kis_intraday_ohlcv.py` | new (5건) | AC1, AC4, AC5 |
| `tests/backtest/strategies/test_momo_kis_v1.py` | new (7건) | AC2, AC4, AC5 |
| `tests/test_portfolio_orchestrator_async.py` | edit (2건 추가) | AC3, AC5 |

총 신규 테스트 22건, AC5 (20+) 충족.

### 검증 / 테스트 전략

**단위 테스트 매트릭스 (목표 20+, 합계 22):**

- **tr_ids/schemas (3)**: `TR_ID_INTRADAY_PRICE == "FHKST03010200"` 상수, `KISIntradayBar` float coerce, `KISIntradayBar` time HHMMSS 보존.
- **raw client mock (5)**: 단일페이지 정상, 두페이지 페이지네이션 + ctx token, 빈 output2, 429 retry 성공 (3회 시도 중 마지막 성공), 429 exhausted raise + 페이지간 0.5s sleep.
- **data_lake fetcher (5)**: OHLCV_SCHEMA 12개 컬럼 모두 존재, `source=="kis"` `freq=="15m"`, ts UTC 변환 (KST 09:00 → UTC 00:00), 휴일/주말 자동 skip (영업일만 호출), 30일 초과 요청 시 warning 로그 + 가능한 일자만 반환, 일자별 loop 사이 sleep(0.5) 호출 검증.
- **strategy unit (7)**: warmup hold, `_is_my_bar_boundary` 5case (15m 정시 True / 09:07 False / 토 False / 휴일 False / 16:00 False), bullish→buy size>0, bearish→sell size=1.0, sigma=0 (VI 단일가) fallback hold.
- **integration (2)**: `register_strategy + register_strategy_returns + refresh_portfolio_risk` 사이클 통과, `run_bar` 거래시간 외/내 분기.

**실행 명령**: `pytest tests/brokers/kis/test_broker_kis_intraday.py tests/data_lake/test_fetch_kis_intraday_ohlcv.py tests/backtest/strategies/test_momo_kis_v1.py tests/brokers/kis/test_intraday_schemas.py -v` 22건 green.

### Guardrails

**Must Have**:
- 일봉 2-layer 구조 정확 복제 — `fetch_intraday_ohlcv_raw` (raw KISClient 의존) → `fetch_kis_intraday_ohlcv` (DataFrame 어댑터, KISClient 직접 인스턴스화). 일봉 모듈 의존성 다이어그램과 동형.
- 0.5s sleep + 429/Retry-After 재시도는 일봉 헬퍼 패턴 그대로 (3회, 1.0s base, 2배 백오프).
- `register_strategy_returns("momo_kis_v1", series)` 호출 경로 spec 문서 + 단위 테스트 양쪽에서 검증.
- `_is_my_bar_boundary` = (KST 변환) ∧ (weekday<5) ∧ (¬is_krx_holiday) ∧ (09:00≤t≤15:30) ∧ (minute % 15 == 0 ∧ second == 0).
- AsyncStrategy Protocol 준수: `async def on_bar(self, ctx) -> Signal | None`.
- 신규 strategy spec frontmatter `type: strategy` + `[[위키링크]]` 대상 모두 실존 (rsi-divergence, max-drawdown-5pct, 19-portfolio-risk, 20-position-sizing 사전 확인).

**Must NOT Have**:
- Live KIS 계좌 호출 (`paper=True` default 강제, 테스트는 전부 mock).
- Lookahead leakage — `detect_divergence` 는 momo_btc_v2 와 동일 로직 (bar-by-bar window slicing); KIS 분봉은 KIS API 의 close 가 confirmed bar (다음 분봉 시작 시점에 확정).
- LLM 위임 — Signal 의 expected_return/confidence 는 kelly_continuous + RSI 거리 / ATR 등 결정적 함수만.
- 새 디렉토리 .ai.md 누락 (현재 계획상 신규 디렉토리 없음).
- `.ai.md` 갱신 누락 → CI 차단; `src/backtest/strategies/.ai.md` 한 줄 반드시 추가.

### 엣지 케이스 / 주의

- **30일 boundary**: `today - timedelta(days=30) ≤ d ≤ today` 범위 외 일자 입력 시 warning 로그 + skip (raise 하지 않음). 1년치 학습 데이터는 cron 으로 매일 1회 누적 적재 가정 → `.ai.md` 또는 fetcher docstring 에 명시.
- **휴일 / 임시 휴장**: `is_krx_holiday(d)` + `weekday >= 5` 양쪽 게이트 — fetcher loop 단계와 strategy `_is_my_bar_boundary` 양쪽에 적용 (이중 안전망).
- **timezone**: KIS API 는 KST 기준 HHMMSS; pandas `pd.Timestamp(...,tz="Asia/Seoul")` → `tz_convert("UTC")` 항상 통과. naive datetime 은 `_is_my_bar_boundary` 에서도 raise 없이 KST 가정 (breakout_donchian 패턴 따름).
- **분봉 KST 09:00~15:30**: `_MARKET_OPEN/CLOSE` 와 일치. 15:30 마감 bar 도 valid (inclusive). 15:35 같은 시간외 분봉은 KIS 가 반환해도 `_is_my_bar_boundary` 에서 reject.
- **VI 단일가 구간**: 단일가 매매 중 close 가 정체 → `pct_change()` 가 0 연속 → `ewma_sigma=0` → `kelly_continuous(mu, 0)` 분기 (`risk.sizing` 의 0-sigma 가드 확인 필요; 없으면 size=0 fallback).
- **첫 호출 워밍업**: `min_bars = RSI_PERIOD + LOOKBACK*2 + 1 = 14+28+1 = 43` 분봉 필요. 15m → 약 11시간 거래 = 영업일 1.7일.
- **KIS 분봉 newest-first 반환**: 일봉 코드처럼 마지막에 `all_bars.reverse()` 필수. 분봉 페이지네이션은 시각 token 으로 역방향 추적 → 09:00 미만 수신 시 break.

### 의존성 / 차단 요인

- **선행 완료된 산출물 (#79)**: `KISClient`, `KISAuth`, `tr_ids_for`, `is_krx_holiday`, `KOSPI200_CONSTITUENTS`, `OHLCV_SCHEMA`, `partition_path`, `AsyncStrategy` Protocol, `AsyncStrategyOrchestrator`, `signals.rsi.detect_divergence`, `risk.sizing.{ewma_sigma, kelly_continuous, fractional_kelly}` — 모두 실측 확인됨, 차단 없음.
- **순서 의존성**: tr_ids → schemas → raw client → fetcher → strategy → spec/.ai.md → orchestrator 통합 테스트. tr_ids/schemas 는 독립이므로 병렬 가능.
- **외부 차단 요인**: KIS 분봉 TR `FHKST03010200` 의 정확한 path/param 은 KIS 공식 문서 또는 `wikidocs.net/239581` 재확인 필요 (현재 `inquire-time-itemchartprice` 가 통설). 첫 mock 테스트 작성 시 docstring 으로 path 고정 후 paper 실호출 1회로 검증 권장 — 테스트는 mock 만이지만 한 번은 실측 raw response 캡처 (cassette) 권장.
- **risk.sizing 의 0-sigma 가드**: `kelly_continuous(mu=0, sigma=0)` 동작 미확인 — 필요시 `sigma <= 1e-9 → return 0.0` 가드를 strategy 의 `_entry_size` 에 fallback 으로 추가.
- **frontmatter wikilink 검증**: spec 작성 후 `python scripts/check_invariants.py --strict` 로컬 실행 필수 — `[[max-drawdown-5pct]]` 등 노트가 모두 실존하는지 사전 grep.
