---
id: 01_plan
type: work-plan
name: "#79 전략 카탈로그 확장 — 구현 계획 (B안 / multi-venue)"
status: approved
issue: 79
---

# #79 전략 카탈로그 확장 — 구현 계획 (B안 / multi-venue)

> Consensus 플랜 (Planner → Architect → Critic). 2026-04-25 APPROVE.
> B안 pivot: #74 (KIS 재무 어댑터) CLOSED 확인 → `breakout_donchian` 을 KOSPI200 KRX 1d 로 전면 대체.
> 실제 코드는 `docs/work/active/000079-strategy-catalog-expansion/02_implementation.md` 에 기록한다.

## AC 체크리스트 (이슈 body에서 추출)

- [ ] 3개 신규 전략 구현 + 각각 백테스트 통과
- [ ] 각 전략별 일수익률 시계열을 T×N DataFrame 으로 결합 가능한 구조
- [ ] 결합 DataFrame → `risk.compute_portfolio_risk_from_df` → Report 생성 smoke test
- [ ] 결합 ENB/N ≥ 0.5 (=진짜 다변화 입증. 권고 §7 최소선 0.3 대비 여유)
- [ ] 각 전략-기존 momo 간 평균 ρ ≤ 0.6 (상관 낮은 페어링 입증)
- [ ] `docs/specs/strategies/` 에 3개 스펙 파일 추가 (프론트매터 `type: strategy`)
- [ ] `tests/` 에 단위 + 통합 테스트
- [ ] `02_implementation.md` 에 **실측 상관매트릭스** 첨부

## 의존성 체크

### 하드 블로커 (모두 CLOSED)
- `#71` 알파 팩터 파이프라인 -- Merged (`59d26b3`)
- `#74` KIS 재무/PER·PBR 어댑터 -- **CLOSED** (`99f12aa`). `fundamentals_client.py`, `schemas.py(FinancialRatio, MarketMultiples)`, inquiry TR-IDs 모두 사용 가능.
- `#76` Signal 인터페이스 확장 -- Merged (`c7c54a8`)
- `#78` 멀티 전략 async 오케스트레이터 -- Merged (`994ea11`)

### 참고
- CLAUDE.md "새 전략 추가 시 필수 (#70 이후, #78 확장)" 규칙 준수
- `AsyncStrategyOrchestrator.register_strategy` + `register_strategy_returns` 둘 다 호출 필수
- `#73` (브로커 어댑터 async 마이그레이션) OPEN — 신규 fetcher 는 sync 본체 + `asyncio.to_thread` 래핑 가능 이중 구조 권장

---

## 구현 계획

### A) RALPLAN-DR Summary (Short Mode)

#### Principles

1. **Signal layer adds reusable factors, not strategy-specific code** -- Donchian channel 과 z-score 는 `src/signals/` 에 `@register` 팩터로 등록하여 다른 전략에서도 재사용 가능하게 한다.
2. **Daily returns contract is non-negotiable** -- 모든 전략은 `register_strategy_returns(strategy_id, series)` 를 호출해야 한다. 누락 시 포트폴리오 리스크 평가가 무력화된다.
3. **Multi-venue universe (Binance + KRX)** -- #74 (KIS 재무 어댑터) CLOSED 확인으로 KIS API 키 활용 가능. `meanrev_pairs` / `momo_vol_filtered` 는 Binance, `breakout_donchian` 은 KIS KOSPI200 일봉을 사용하여 암호화폐-주식 간 저상관을 구조적으로 확보한다.
4. **Uniform orchestrator integration via AsyncStrategy** -- 3개 전략 모두 `AsyncStrategy` protocol (`async on_bar(ctx) -> Signal | None`) 을 준수하여 `AsyncStrategyOrchestrator` 와 일관된 통합을 보장한다.
5. **TDD Red-Green-Refactor** -- 테스트 먼저 작성, 실패 확인, 최소 구현, 리팩토링 순서 엄수.
6. **Instrument-type 에 따른 비용 모델 분기** -- crypto 는 대칭 편도 0.10%, KRX 는 매도 거래세 비대칭 (매수 0.015% / 매도 0.245%). 동일한 `_apply_cost` 헬퍼가 `instrument_type` 인자로 분기한다.

#### Decision Drivers (Top 3)

1. **enb_ratio >= 0.5 gate** -- 포트폴리오 실질 분산 입증. 전략 간 상관이 높으면 ENB 가 낮아 이 게이트를 통과하지 못한다. 전략 설계의 핵심 제약.
2. **avg rho <= 0.6 gate** -- 각 신규 전략과 기존 `momo_btc_v2` 간 평균 상관 0.6 이하. 서로 다른 알파 소스(mean-reversion vs breakout vs vol-filtered momentum) 와 서로 다른 시장(crypto vs KRX) 을 강제한다.
3. **Instrument-type 비용 분기** -- crypto 와 KRX 의 거래비용 구조가 근본적으로 다르므로, 비용 공식이 instrument_type 을 인자로 받아 분기해야 실측 수익률 산출이 정확하다.

#### Viable Options

**핵심 설계 질문: KIS OHLCV 수집 지점**

**Option A -- 2-layer 구조 (선택됨)**
- `src/brokers/kis/price_client.py` (원시 TR `FHKST03010100` 호출 + pydantic 응답 `KISDailyBar`) + `src/data_lake/fetcher.py` 에 `fetch_kis_daily_ohlcv(symbol, start, end) -> DataFrame` (OHLCV_SCHEMA 정규화).
- Pros:
  - Binance fetcher (`fetch_binance_klines`) 와 동일한 시그니처·스키마로 universe-agnostic 호출 가능.
  - `price_client.py` 는 `fundamentals_client.py` 와 대칭 구조 -- 재무(#74) 와 시세가 분리된 모듈 아키텍처 유지.
  - `price_client.py` 를 향후 다른 소비자(밸류에이션 스크리너, 라이브 모니터링 등)가 재사용 가능.
- Cons:
  - 파일 2개 신설 + 테스트 2개 -- 1-layer 대비 코드량 증가.

**Option B -- 1-layer 구조**
- `src/data_lake/fetcher.py` 에 TR 호출 직접 내장.
- Pros:
  - 파일 1개만 수정하므로 PR 크기 소폭 감소.
- Cons:
  - `fetcher.py` 가 KIS REST 프로토콜(헤더, 페이지네이션, rate limit)을 직접 담당 -- 책임 혼재.
  - `fundamentals_client.py` 와 시세 클라이언트가 분리된 `src/brokers/kis/` 모듈 구조 파괴.
  - 재사용성 하락 (TR 호출 로직이 fetcher 에 매몰).
- **Invalidation rationale**: #74 산출물인 `fundamentals_client.py` 가 이미 "TR 호출 = `src/brokers/kis/`" 패턴을 확립. 시세도 동일 패턴을 따라야 모듈 경계가 일관된다. 1-layer 는 이 경계를 깨뜨림.

**결론: Option A 채택.** KIS OHLCV 수집은 `price_client.py` (raw TR) + `fetcher.py` (DataFrame 정규화) 2-layer 로 구현한다.


### B) Algorithmic Design per Strategy

#### B0. Historical Data Flow -- `ctx` 계약 (A안 계승 + KRX 확장)

현재 `AsyncStrategyOrchestrator.run_bar(ts, market_snapshot, ...)` 의 `market_snapshot` 은 thin dict 라 rolling 팩터 계산에 필요한 60+ bar 의 OHLCV history 가 없다. `_async_orchestrator.py` 자체를 수정하지 않고 **호출자(backtest harness / live feeder)가 `market_snapshot` 을 채우는 계약**으로 해결한다.

**계약** (#79 에서 새로 고정):

```python
market_snapshot: dict[str, Any] = {
    # (기존) 필수: tick 시점 최신가
    "<SYMBOL>": {"open": ..., "high": ..., "low": ..., "close": ..., "volume": ...},
    # (#79 에서 추가) 선택: rolling 팩터가 필요한 전략이 읽는 history
    "ohlcv_history": {
        "<SYMBOL>": pd.DataFrame,   # columns: [open, high, low, close, volume], index=ts, tz=UTC
    },
}
```

- `ohlcv_history[SYMBOL]` 은 최소 `max(각 전략 lookback) + 1` 행. 일반적 값:
  - `meanrev_pairs` (ETHBTC, 1h): >= 60 + 1 행
  - `breakout_donchian` (KOSPI200 종목들, 1d): >= 20 + 1 행 (× 최대 10 종목 동시 보유)
  - `momo_vol_filtered` (BTCUSDT, 4h): >= max(MACD=26, vol=20) + 1 행
- 없거나 행이 부족하면 전략은 `Signal(action="hold", size=0.0, reason="insufficient history")` 반환.
- **이 계약은 backtest harness 와 단위 테스트에서만 강제된다.** Live feed 연결(별도 이슈)에서도 동일 key 를 채우면 된다. 오케스트레이터는 불변 -- 단순히 `market_snapshot` 을 그대로 전달.
- 전략 내부는 `signals.compute("<factor>", **cols)` 를 직접 호출해 팩터를 계산한다. `context["factors"]` 자동 주입은 sync 엔진 전용이라 AsyncStrategy 경로에는 없음.

**KRX / Binance 네임스페이스 규칙**:
- KRX 종목: **6자리 종목코드** (예: `"005930"` = 삼성전자).
- Binance: ticker (예: `"BTCUSDT"`, `"ETHBTC"`).
- 두 네임스페이스는 `ohlcv_history` dict 안에서 key 로만 분리. 6자리 숫자 vs 영문 ticker 라 conflict 없음.

**Universe expansion 시 메모리 관리**: KOSPI200 = 200 종목 × lookback 20 행 → dict 에 전체를 적재해도 수 MB 수준으로 합리적. 그러나 harness 는 전략이 declare 한 `universe` 속성의 종목만 주입하는 lazy pattern 권장 -- `breakout_donchian` 이 top-10 을 선택하더라도 비교 대상인 200 종목의 lookback 은 모두 필요하므로, donchian 전략에 한해 전체 KOSPI200 history 를 주입.

**Tick Scheduler — 주파수·시장 미스매치 해결 (B안 신규)**

4 전략은 timeframe 과 시장 스케줄이 서로 달라 매 tick 마다 전부 깨우면 **96번 중 95번 헛일**. `AsyncStrategyOrchestrator.run_bar(ts, snap, strategies=...)` 의 `strategies: list[str] | None` 인자 (`_async_orchestrator.py:87-93`) 로 "해당 tick 에 깨울 전략 ID 목록" 을 명시 전달한다. harness 책임.

| Tick boundary | 깨울 전략 `strategies=` | 비고 |
|---------------|-------------------------|------|
| 매 15m | `["momo_btc_v2"]` | BTC 15m 전용 |
| 매 1h (:00) | `["momo_btc_v2", "meanrev_pairs"]` | ETHBTC 1h 추가 |
| 매 4h (00/04/08/12/16/20 UTC) | `[..., "momo_vol_filtered"]` | BTCUSDT 4h 추가 |
| KRX 장마감 15:30 KST (평일) | `["breakout_donchian"]` | KIS 일봉 확정 시점 |
| KRX 장외 시간·휴장일 | breakout_donchian 제외 | 호출 자체 생략 |

- 위 매핑은 backtest harness·live feeder 양쪽에서 동일 계약.
- CI synthetic 테스트(`test_strategy_catalog_integration.py`) 는 시간 재구성 없이 전략별 수익률 시계열을 직접 공급하므로 tick scheduler 우회 — mechanics 검증용.
- 실측 스크립트(`scripts/measure_strategy_catalog.py`) 는 위 스케줄에 따라 `run_bar` 호출. `krx_handler.MarketState` 와 미래에 추가될 거래시간 판정기(`is_krx_trading_hours(ts)`) 로 KRX tick 필터링.

**2중 안전망 — 전략 내부 self-guard**

전략 코드 자체에도 "내 tick 이 아니면 즉시 hold" 가드를 둔다. harness 필터가 실수로 잘못 호출해도 조용히 0 비용 반환:

```python
async def on_bar(self, ctx) -> Signal | None:
    ts = ctx["ts"]
    if not self._is_my_bar_boundary(ts):
        return Signal(action="hold", size=0.0, reason="not my bar")
    snap = ctx["market_snapshot"]
    hist = snap.get("ohlcv_history", {}).get(self.symbol)
    if hist is None or len(hist) < self.min_history:
        return Signal(action="hold", size=0.0, reason="insufficient history")
    # ... signals.compute(...) + 진입/청산 판정
```

- `_is_my_bar_boundary(ts)` 구현 기준:
  - `momo_btc_v2`: `ts.minute % 15 == 0 and ts.second == 0`
  - `meanrev_pairs`: `ts.minute == 0 and ts.second == 0`
  - `momo_vol_filtered`: `ts.hour % 4 == 0 and ts.minute == 0 and ts.second == 0` (UTC 기준)
  - `breakout_donchian`: `ts.time() == time(15, 30) and ts.weekday() < 5 and not is_krx_holiday(ts.date())`

**전략 공통 on_bar 골격** (위 self-guard 포함):

```python
async def on_bar(self, ctx) -> Signal | None:
    if not self._is_my_bar_boundary(ctx["ts"]):
        return Signal(action="hold", size=0.0, reason="not my bar")
    hist = ctx["market_snapshot"].get("ohlcv_history", {}).get(self.symbol)
    if hist is None or len(hist) < self.min_history:
        return Signal(action="hold", size=0.0, reason="insufficient history")
    # ... signals.compute(...) + 진입/청산 판정
```

**부하 정량 (평가)**: `asyncio.gather` 병렬 실행(`_async_orchestrator.py:107`)으로 tick 당 지연 = 가장 느린 전략 1개. 200종목 Donchian rolling = numpy vectorized 수 ms, 메모리 ~160KB. 실질 과부하 없음.

#### B1. `meanrev_pairs` -- Crypto Cross-Pair Mean Reversion (유지)

- **Universe & Timeframe**: Binance `ETHBTC` 1h bars. "long the ratio" = ETH/BTC 비율의 평균회귀. 단일 티커이므로 숏 레그 불필요.
- **Entry Rule**:
  - `z = (log(close) - rolling_mean(log(close), 60)) / rolling_std(log(close), 60)`
  - z < -2.0 이면 **buy** (비율이 평균 이하로 떨어짐 -- 회복 기대)
  - z > +2.0 이면 **sell** (비율이 평균 이상으로 올라감 -- 포지션 청산)
  - |z| <= 2.0 이면 **hold**
- **Exit Rule**: z 가 반대 임계치를 돌파하거나 0 교차 시 청산 (보수적: z > 0 이면 sell)
- **Position Sizing**: `vol_target(sigma_period, target_annual=0.15, periods_per_year=365*24)` -- 1h 기준 연 15% 목표. `ewma_sigma(returns, lam=0.94)`.
- **Required Factors**: `zscore` (new), `realized_vol` (existing, 파라미터 override: `window=60, annualize=365*24`)
- **Confidence**: `clip01(1 - |z| / 4.0)` -- z 가 극단일수록 신뢰도 높음 (역수 관계로 표현하되, 매우 큰 z 는 regime break 우려로 신뢰도 감소)
- **expected_return**: `mu_hat = rolling_mean(returns, 60).iloc[-1]`
- **Instrument type**: `crypto`. 비용: 대칭 `cost_rate_per_side = 0.001`.

#### B2. `breakout_donchian` -- KRX KOSPI200 Donchian Channel Breakout (B안 전면 재작성)

- **Scope 경계**: #79 는 **backtest-only**. `Signal` 프로토콜(`protocol.py:23-30`)에 `symbol` 필드가 없고 orchestrator 의 OrderIntent 경로는 단일 symbol 가정이므로, `breakout_donchian.on_bar()` 는 **포트폴리오 레벨 (top-10 바스켓 집계) Signal 을 1건 반환**한다. 개별 종목 Order 생성은 #79 범위 외 (live 실행 = #80 후속).
- **일수익률 시계열 정의 (P2 계약 충족)**:
  - 매 KRX 거래일, 활성 top-N 슬롯(N ≤ 10) 각 종목의 당일 수익률을 구하고 equal-weight 평균 = 바스켓 raw_return.
  - 비용은 `_apply_cost(raw_return, position=basket_weight, instrument_type="krx")` 로 종목 단위 `Δposition` 별 차감 후 합산 (진입/청산 모두 반영).
  - 이 단일 시계열을 `register_strategy_returns("breakout_donchian", series)` 로 orchestrator 에 공급.
- **Universe & Timeframe**: KOSPI200 constituents (pin-date 2026-04-25 기준 snapshot), 일봉. KIS TR `FHKST03010100` 으로 수집.
- **Entry Rule**:
  - 종목 각각에 대해 `upper_t = rolling_max(high, 20).shift(1)`, `lower_t = rolling_min(low, 10).shift(1)`.
  - `close[t] > upper_t` 이면 buy 후보. 복수 종목 돌파 시 **`atr_14.shift(1)` (전일 기준, look-ahead 제거) 정규화 돌파 강도** `(close[t] - upper_t) / atr_14.shift(1)` 순으로 내림차순 정렬 후 상위 N 종목 (기본: top-10) 만 동시 보유.
- **Exit Rule**: `close[t] < lower_t` (10일 저점 이탈) 에서 개별 종목 청산. 신규 돌파 종목으로 빈 슬롯 채움.
- **Position Sizing**: Equal-weight 10 slots (각 1/10) x `fractional_kelly(k=0.5)` 적용으로 총 노출 조절. `vol_target` annual=0.15 (주식은 암호화폐 대비 저변동성).
- **Required Factors**: `donchian` (new), `atr` (existing, `.shift(1)` 적용 후 소비).
- **Confidence**: `clip01(abs(close - upper_t) / atr_14.shift(1))` -- 돌파 강도가 클수록 신뢰도 up, 전일 ATR 기준.
- **expected_return**: `mean(basket_daily_returns[-60:])` (바스켓 수익률 기준 60일 평균).
- **거래시간**: KRX 평일 09:00~15:30. 일봉 entry/exit 는 장마감 EOD 기반이라 영향 없음. 휴장일 판정은 **`src/universe/krx_calendar.py::is_krx_holiday(date)`** 사용 (§C4 에서 신규 작성, `src/execution/krx_handler.py` 는 단일가 매매/거래정지 버퍼 전용이라 부적합).
- **Instrument type**: `krx`. 비용: 비대칭 `cost_buy = 0.00015`, `cost_sell = 0.00245`.

#### B3. `momo_vol_filtered` -- Volatility-Filtered Momentum (유지)

- **Universe & Timeframe**: Binance `BTCUSDT` 4h bars. MomoBtcV2 와 시간축(15m)과 구간(4h)이 달라 상관 감소 기대.
- **Entry Rule**:
  - MACD histogram > 0 AND MACD line > signal line (모멘텀 확인)
  - **vol filter**: `realized_vol(close, 20) < vol_ceiling` (기본 vol_ceiling = 연 80%). 고변동 구간에서는 진입 차단.
  - 두 조건 모두 충족 시 **buy**
- **Exit Rule**: MACD histogram < 0 이면 **sell**. 또는 `realized_vol > vol_ceiling * 1.5` (극단 변동성 비상 청산).
- **Position Sizing**: `vol_target(sigma_period, target_annual=0.20, periods_per_year=365*6)` -- 4h 기준 연 20% 목표.
- **Required Factors**: `macd` (existing), `realized_vol` (existing, 파라미터 override: `window=20, annualize=365*6`)
- **Confidence**: `clip01(abs(macd_histogram) / atr)` -- MACD 히스토그램 크기를 ATR 로 정규화.
- **expected_return**: `mu_hat = mean(returns[-60:])` (최근 60 bar 평균 수익률)
- **Instrument type**: `crypto`. 비용: 대칭 `cost_rate_per_side = 0.001`.

**상관 감소 설계 근거**:
- `meanrev_pairs`: 다른 종목(ETHBTC), 다른 알파(mean-reversion) -- momo_btc_v2(BTCUSDT momentum) 와 낮은 상관 기대.
- `breakout_donchian`: **다른 시장(KRX KOSPI200)**, 다른 알파(채널 돌파). 암호화폐-주식 간 구조적 저상관.
- `momo_vol_filtered`: 같은 종목이지만 시간축(4h vs 15m), 변동성 필터로 고변동 구간 차단 -- momo_btc_v2 가 진입하는 구간과 다를 수 있음.


### C) File-Level Change List (Grouped by AC)

#### C1. 신규 KIS OHLCV fetcher (B안 신규)

| File | Action | Purpose |
|------|--------|---------|
| `src/brokers/kis/tr_ids.py` | MODIFY | `TR_ID_DAILY_PRICE = "FHKST03010100"` 상수 추가. inquiry TR 이라 paper/live 구분 없음. |
| `src/brokers/kis/schemas.py` | MODIFY | `KISDailyBar(date: str, open: float, high: float, low: float, close: float, volume: float, trade_amt: float)` pydantic v2 모델 추가. `@field_validator` 로 문자열→float 변환. |
| `src/brokers/kis/price_client.py` | **CREATE** | `fetch_daily_ohlcv_raw(client: KISClient, symbol: str, start: str, end: str, period: str = "D") -> list[KISDailyBar]`. 페이지네이션(`tr_cont` + `CTX_AREA_FK100`/`CTX_AREA_NK100` 연속 조회) + rate limit 준수 (요청 간 sleep 0.5s, paper 2rps 대비 여유 50%). **429 재시도는 자체 구현 필수** — 기존 `KISClient._request_with_retry` (`rest.py:74-121`) 는 5xx 만 처리하므로 본 모듈에서 429 감지 시 `Retry-After` 헤더 존중 + 지수 백오프 재시도 래퍼 작성 (최대 3회). 5xx 는 `_request_with_retry` 계승. |
| `src/data_lake/fetcher.py` | MODIFY | `fetch_kis_daily_ohlcv(symbol: str, start: str, end: str, *, auth: KISAuth, app_key: str, app_secret: str, cano: str, acnt_prdt_cd: str, paper: bool = True) -> pd.DataFrame` 추가. OHLCV_SCHEMA 매핑 (source="kis"), `fetch_binance_klines` 와 동일한 반환 스키마. 내부에서 `price_client.fetch_daily_ohlcv_raw` 호출. |
| `src/brokers/kis/.ai.md` | MODIFY | `price_client.py` 진입점 기술 |
| `src/data_lake/.ai.md` | MODIFY | `fetch_kis_daily_ohlcv` 진입점 기록 |
| `tests/test_broker_kis_price.py` | **CREATE** | `price_client` 단위 테스트 (mocked HTTP 응답: 정상 1페이지, 연속 조회 2페이지, 빈 응답, 429 재시도) |
| `tests/test_fetch_kis_daily_ohlcv.py` | **CREATE** | fetcher 단위 (스키마 정규화, 빈 응답, 페이지네이션 mock, OHLCV_SCHEMA 컬럼 일치) |

#### C2. 신규 팩터 2건

| File | Action | Purpose |
|------|--------|---------|
| `src/signals/donchian.py` | **CREATE** | Donchian channel factor (`@register("donchian", inputs=["high", "low"], signal_type="breakout")`) -- upper/lower/middle 반환 |
| `src/signals/zscore.py` | **CREATE** | Rolling z-score factor (`@register("zscore", inputs=["close"], signal_type="mean_reversion")`) -- **log(close) 도메인** 기반 raw z-value 반환. Docstring 에 `bollinger.pct_b` 와 차이 명시 필수: "pct_b 는 선형 가격 + band-scaled [0,1], zscore 는 log-가격 + band 구조 없는 raw z-value" |
| `src/signals/__init__.py` | MODIFY | `donchian`, `zscore` import 추가 + `__all__` 갱신 |
| `src/signals/.ai.md` | MODIFY | 신규 팩터 2건(donchian, zscore) 카탈로그 추가 + 각각의 signal_type/inputs 기재 |

#### C3. 전략 3건

| File | Action | Purpose |
|------|--------|---------|
| `src/backtest/strategies/meanrev_pairs.py` | **CREATE** | ETHBTC 1h mean-reversion 전략 (AsyncStrategy) |
| `src/backtest/strategies/breakout_donchian.py` | **CREATE** | KOSPI200 1d Donchian breakout 전략 (AsyncStrategy). 멀티 종목 top-N 슬롯 로직 포함. |
| `src/backtest/strategies/momo_vol_filtered.py` | **CREATE** | BTCUSDT 4h vol-filtered momentum 전략 (AsyncStrategy) |

#### C4. KOSPI200 유니버스 정의 (B안 신규)

| File | Action | Purpose |
|------|--------|---------|
| `src/universe/kospi200.py` | **CREATE** | KOSPI200 constituents 정적 리스트 (pin-date 2026-04-25). `KOSPI200_CONSTITUENTS: list[dict]` -- 각 항목 `{"code": "005930", "name": "삼성전자", "sector": "반도체"}`. `get_codes() -> list[str]` 헬퍼. |
| `src/universe/krx_calendar.py` | **CREATE** | KRX 거래일/휴장일 판정 헬퍼 (**B안 신규, Architect I7**). `is_krx_holiday(date: date) -> bool`, `is_krx_trading_hours(ts: datetime) -> bool`. 2025-2026 KRX 정적 휴장일 리스트 내장. `src/execution/krx_handler.py` (단일가 매매 버퍼 전용) 과는 별개 모듈. |
| `src/universe/__init__.py` | MODIFY | `kospi200`, `krx_calendar` 모듈 import 추가 |
| `src/universe/.ai.md` | MODIFY | kospi200, krx_calendar 진입점 기록 |
| `tests/test_universe_kospi200.py` | **CREATE** | 리스트 길이 190~210 허용 (편입/편출 변동 감안), code 6자리 문자열 검증, 중복 없음, 필수 종목(005930, 000660) 포함 확인 |
| `tests/test_krx_calendar.py` | **CREATE** | 평일 vs 주말, 대체공휴일(예: 2025-05-06 어린이날 대체), 임시휴장일 포함 테스트. `is_krx_trading_hours` 시간대(KST) 변환 검증. |

#### C5. 일수익률 결합 + 리스크 통합 + ENB/rho gate

| File | Action | Purpose |
|------|--------|---------|
| `src/backtest/cost.py` | **CREATE** | 공통 비용 헬퍼 (**Architect I5**). `apply_cost(returns: pd.Series, positions: pd.Series, instrument_type: Literal["crypto", "krx"]) -> pd.Series`. 한 곳에 상수·공식 유지, 테스트+실측 스크립트 양쪽에서 import. 상수: `COST_CRYPTO_PER_SIDE=0.001`, `COST_KRX_BUY=0.00015`, `COST_KRX_SELL=0.00245`. |
| `src/backtest/calendar_align.py` | **CREATE** | 교차 캘린더 얼라인먼트 헬퍼 (**Architect I2**). `intersect_trading_days(returns_by_strategy: dict[str, pd.Series]) -> pd.DataFrame`. 각 전략 시계열의 index 교집합 = "모든 전략이 실거래한 날" 만 유지. 이 DataFrame 을 `compute_portfolio_risk_from_df` 에 투입. KRX 휴장일 0-fill 금지 (ENB/ρ 왜곡 방지). |
| `tests/test_backtest_cost.py` | **CREATE** | `apply_cost` 단위: crypto 왕복 0.20%, krx 비대칭 (buy 0.015% / sell 0.245%), 포지션 무변동 시 0 비용, 다회 turnover 누적. |
| `tests/test_calendar_align.py` | **CREATE** | 교집합 정답 case: crypto 365일 + KRX 250일 → 약 250일(KRX 휴장일 제외) 반환. KRX 전용 날짜는 drop, 양쪽 모두 존재하는 날만 유지. |
| `tests/test_strategy_catalog_integration.py` | **CREATE** | (Layer 1 CI) synthetic seed=79 기반 파이프라인 mechanics 테스트 -- 4 전략 수익률 -> `intersect_trading_days` -> T x N DataFrame -> `compute_portfolio_risk_from_df` -> `enb_ratio >= 0.5`, `max rho <= 0.6` assertion + report 필드 존재 smoke. synthetic data 도 crypto 365일 + KRX ~250일 패턴으로 생성. |
| `scripts/measure_strategy_catalog.py` | **CREATE** | (Layer 2 PR gate) Binance + KIS paper 실측 데이터 기반 4 전략 백테스트 -> `apply_cost` 로 instrument_type 별 비용 차감 -> `intersect_trading_days` 로 교집합 정렬 -> 상관매트릭스/ENB/CVaR 산출 -> `02_implementation.md` append. **PR merge 게이트**. Partial failure 처리: KIS 일부 종목 수집 실패 시 기록 + 최소 N=180 KOSPI200 종목 달성 전에는 fail-fast. |

#### C6. 스펙 파일 + 문서 갱신

| File | Action | Purpose |
|------|--------|---------|
| `docs/specs/strategies/meanrev-pairs.md` | **CREATE** | frontmatter `type: strategy`, 진입/청산/훅 소비/리스크 연동 섹션 |
| `docs/specs/strategies/breakout-donchian.md` | **CREATE** | frontmatter `type: strategy`, universe=KOSPI200, instruments=KRX 명시 |
| `docs/specs/strategies/momo-vol-filtered.md` | **CREATE** | frontmatter `type: strategy`, 진입/청산/훅 소비/리스크 연동 섹션 |
| `docs/work/active/000079-strategy-catalog-expansion/02_implementation.md` | **CREATE** | 실측 상관매트릭스 + ENB + CVaR 결과 + KOSPI200 pin-date snapshot 기록 + 파라미터 조정 이력 |
| `src/backtest/strategies/.ai.md` | MODIFY | 카탈로그 인덱스에 3개 전략 추가 |

#### C7. 단위 테스트

| File | Action | Purpose |
|------|--------|---------|
| `tests/test_donchian_signal.py` | **CREATE** | donchian factor 단위 테스트 (upper/lower 값, edge cases) |
| `tests/test_zscore_signal.py` | **CREATE** | zscore factor 단위 테스트 (known-value, NaN 처리) |
| `tests/test_meanrev_pairs.py` | **CREATE** | z-score 계산 검증, 진입/청산 Signal 필드, sizing 검증 |
| `tests/test_breakout_donchian.py` | **CREATE** | Donchian 돌파 로직, dual exit, top-N 종목 선택, confidence 공식 검증 |
| `tests/test_momo_vol_filtered.py` | **CREATE** | vol filter 차단, MACD 진입, 비상 청산 검증 |


### D) Step-by-Step TDD Execution Order

#### Step 0: KIS OHLCV fetcher 2-layer 구현 (B안 신규)
- **AC**: B안 전제조건 (KRX 데이터 수집 인프라)
- **Red (price_client)**: `tests/test_broker_kis_price.py` 작성 -- `fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260425")` 호출 시 `list[KISDailyBar]` 반환, 페이지네이션 연속 조회 테스트 (mocked HTTP). `from src.brokers.kis.price_client import fetch_daily_ohlcv_raw` -- ImportError.
- **Green**: `src/brokers/kis/price_client.py` 생성.
  - `fetch_daily_ohlcv_raw(client: KISClient, symbol: str, start: str, end: str, period: str = "D") -> list[KISDailyBar]`.
  - 페이지네이션: `tr_cont="N"` (첫 조회) → 응답 헤더 `tr_cont="F"/"M"` 이면 `CTX_AREA_FK100`/`CTX_AREA_NK100` 값을 다음 요청에 실어 연속 조회. `tr_cont=""` 또는 output 빈 리스트이면 종료.
  - Rate limit: 요청 간 `time.sleep(0.5)` (paper 2rps 대비 여유).
  - 기존 `KISClient._get()` 메서드 재사용 (retry 로직 포함).
- **Refactor**: `src/brokers/kis/tr_ids.py` 에 `TR_ID_DAILY_PRICE = "FHKST03010100"` 추가. `src/brokers/kis/schemas.py` 에 `KISDailyBar` 모델 추가.
- **Red (fetcher)**: `tests/test_fetch_kis_daily_ohlcv.py` 작성 -- `fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-04-25", auth=..., ...)` 호출 시 DataFrame 반환, OHLCV_SCHEMA 컬럼 매칭 (mocked `price_client`). ImportError.
- **Green**: `src/data_lake/fetcher.py` 에 `fetch_kis_daily_ohlcv` 추가. `price_client.fetch_daily_ohlcv_raw` 호출 → `KISDailyBar` 리스트 → OHLCV_SCHEMA DataFrame 변환 (source="kis").

#### Step 1: KOSPI200 유니버스 정적 리스트 (B안 신규)
- **AC**: B안 전제조건 (breakout_donchian 유니버스)
- **Red**: `tests/test_universe_kospi200.py` 작성 -- `from src.universe.kospi200 import KOSPI200_CONSTITUENTS, get_codes`. 리스트 길이 190~210, 코드 6자리, 중복 없음, "005930" 포함.
- **Green**: `src/universe/kospi200.py` 생성 -- pin-date 2026-04-25 기준 KOSPI200 구성 종목 정적 리스트.
- **Refactor**: `src/universe/__init__.py` 에 import 추가.

#### Step 2: Donchian factor 추가
- **AC**: AC 1 (전략 구현 전제조건)
- **Red**: `tests/test_donchian_signal.py` 작성 -- `compute_donchian(high, low, window=20)` 호출 시 `upper`, `lower`, `middle` 컬럼 반환, 알려진 값 테스트. `from src.signals.donchian import compute_donchian` -- ImportError.
- **Green**: `src/signals/donchian.py` 생성. `@register("donchian", inputs=["high", "low"], signal_type="breakout", bar_interval="1d", alpha_horizon_bars=20, window=20)`. `upper = high.rolling(window).max()`, `lower = low.rolling(window).min()`, `middle = (upper + lower) / 2`. DataFrame 반환.
- **Refactor**: `src/signals/__init__.py` 에 import 추가.

#### Step 3: Z-score factor 추가
- **AC**: AC 1 (전략 구현 전제조건)
- **Red**: `tests/test_zscore_signal.py` 작성 -- `compute_zscore(close, window=60)` 호출 시 Series 반환, known-value 테스트 (`z = (log(close) - rolling_mean) / rolling_std`). ImportError.
- **Green**: `src/signals/zscore.py` 생성. `@register("zscore", inputs=["close"], signal_type="mean_reversion", bar_interval="1h", alpha_horizon_bars=5, window=60)`. `log_close = np.log(close)`, `z = (log_close - log_close.rolling(window).mean()) / log_close.rolling(window).std()`. Series 반환.
- **Refactor**: `src/signals/__init__.py` 에 import 추가.

#### Step 4: `meanrev_pairs` 전략 구현
- **AC**: AC 1, AC 2
- **Red**: `tests/test_meanrev_pairs.py` 작성 -- (a) z < -2 시 buy Signal 반환, (b) z > 0 시 sell Signal 반환, (c) Signal 에 expected_return/confidence 존재, (d) sizing 이 vol_target 범위 내, (e) warmup 기간 hold. `from src.backtest.strategies.meanrev_pairs import MeanrevPairs` -- ImportError.
- **Green**: `src/backtest/strategies/meanrev_pairs.py` -- `class MeanrevPairs` (AsyncStrategy). `async def on_bar(self, ctx)` -- market_snapshot 에서 OHLCV 추출, `signals.compute("zscore", close=close)`, z-score 기반 buy/sell/hold, `vol_target()` 으로 sizing.
- **Refactor**: confidence 공식을 별도 메서드로 분리.

#### Step 5: `breakout_donchian` 전략 구현 (B안 재작성)
- **AC**: AC 1, AC 2
- **Red**: `tests/test_breakout_donchian.py` 작성 -- (a) close > upper.shift(1) 시 buy 후보 등록, (b) top-N ATR 정규화 선택, (c) close < exit_lower.shift(1) 시 sell, (d) Signal 필드 검증, (e) half-kelly sizing 범위 검증, (f) KRX 6자리 종목코드 입력. ImportError.
- **Green**: `src/backtest/strategies/breakout_donchian.py` -- `class BreakoutDonchian` (AsyncStrategy). `async def on_bar(self, ctx)`:
  - `ohlcv_history` 에서 KOSPI200 종목별 OHLCV 추출.
  - 각 종목에 `signals.compute("donchian", high=high, low=low, window=20)` + `signals.compute("atr", ...)` 적용.
  - `close > upper.shift(1)` 인 종목 필터링 → ATR-정규화 상위 top-10 선택.
  - `close < lower_exit.shift(1)` 인 기존 보유 종목 청산.
  - Equal-weight 10 slots × `fractional_kelly(k=0.5)`.
- **Refactor**: dual Donchian (entry=20, exit=10) 파라미터화. 종목 선택 로직을 `_rank_breakout_candidates` 메서드로 분리.

#### Step 6: `momo_vol_filtered` 전략 구현
- **AC**: AC 1, AC 2
- **Red**: `tests/test_momo_vol_filtered.py` 작성 -- (a) MACD 양수 + vol < ceiling 시 buy, (b) vol > ceiling 시 hold (필터 차단), (c) MACD 음수 시 sell, (d) vol > ceiling*1.5 시 비상 sell, (e) Signal 필드 검증. ImportError.
- **Green**: `src/backtest/strategies/momo_vol_filtered.py` -- `class MomoVolFiltered` (AsyncStrategy). `signals.compute("macd", ...)`, `signals.compute("realized_vol", ...)`, vol filter + MACD 기반 진입/청산.
- **Refactor**: vol_ceiling 을 생성자 파라미터로 노출.

#### Step 7: 통합 테스트 (ENB/rho gate)
- **AC**: AC 2, AC 3, AC 4, AC 5
- **Red**: `tests/test_strategy_catalog_integration.py` 작성 --
  - 4개 전략(momo_btc_v2 + 3 신규) 각각에 대해 252일 synthetic daily returns 생성 (deterministic seed=79).
  - `pd.DataFrame` 결합 -> `compute_portfolio_risk_from_df(df)` 호출.
  - Assert: `report.enb_ratio >= 0.5`, 각 신규 전략 vs momo_btc_v2 pairwise corr <= 0.6.
  - Assert: `report.cvar_pct is not None`, `report.enb is not None`, `report.n_strategies == 4`.
- **Green**: synthetic returns 생성 시 서로 다른 상관 구조를 가진 데이터 사용 (seed 기반).
  - meanrev_pairs: momo_btc_v2 와 부분 역상관 (rho ~ -0.1 ~ 0.2)
  - breakout_donchian: momo_btc_v2 와 매우 낮은 상관 (rho ~ 0.0 ~ 0.15) -- 다른 시장이므로 crypto 대비 저상관
  - momo_vol_filtered: momo_btc_v2 와 중간 상관 (rho ~ 0.3 ~ 0.5)
- **Refactor**: 상관 생성 로직을 fixture 로 분리.

**Note**: 통합 테스트는 **synthetic returns** 를 사용한다. 실제 백테스트 수익률은 `02_implementation.md` 에서 별도 측정한다. 테스트의 목적은 "파이프라인이 올바르게 작동하는가" 이지 "전략이 실제로 낮은 상관을 갖는가" 가 아니다. 후자는 측정 스크립트에서 검증.

#### Step 8: 스펙 파일 + 문서 갱신
- **AC**: AC 6, AC 8
- 3개 `docs/specs/strategies/*.md` 작성 -- `momo-btc-v2.md` 템플릿 준수:
  - frontmatter: `type: strategy`, `id`, `name`, `status: backtest`, `instruments`, `timeframe`, `uses_signals`, `risk_rules`, `owner`, `created`, `tags`
  - `breakout-donchian.md` 에는 `instruments: [KOSPI200]`, `market: krx` 명시
  - 섹션: 진입 / 진입 크기 / 청산 / 훅 소비 / 리스크 연동 (`register_strategy_returns(...)` 명시) / 관련 노트
- `src/backtest/strategies/.ai.md` 갱신 -- 구조 섹션에 3개 전략 추가
- `.ai.md` 파일 갱신: `src/brokers/kis/.ai.md`, `src/data_lake/.ai.md`, `src/signals/.ai.md`, `src/universe/.ai.md`

#### Step 9: 실측 measurement 스크립트 실행
- **AC**: AC 4, AC 5, AC 8
- `scripts/measure_strategy_catalog.py` 실행:
  - Binance (meanrev_pairs: ETHBTC 1h, momo_vol_filtered: BTCUSDT 4h) + KIS paper (breakout_donchian: KOSPI200 종목 일봉) 데이터 fetch.
  - Instrument-type 별 비용 공식 적용 후 일수익률 시계열 산출.
  - 4 전략 결합 DataFrame -> `compute_portfolio_risk_from_df`.
  - 상관매트릭스 + ENB + CVaR + KOSPI200 pin-date snapshot 을 `02_implementation.md` 에 기록.


### E) Guardrails

#### Must Have
- **Long-only simulation** -- 모든 전략은 buy/hold/sell 만 사용. sell = 포지션 청산 (숏 진입 아님).
- **Instrument-type 분기 비용 공식** -- `_apply_cost(returns, positions, instrument_type)` 공통 헬퍼 (harness 내부).
  - `instrument_type: Literal["crypto", "krx"]` 인자로 분기.
  - crypto: 대칭 `cost_rate_per_side = 0.001` (편도 0.10%, 왕복 0.20%).
  - krx: 비대칭 `cost_buy = 0.00015` (매수 수수료 0.015%), `cost_sell = 0.00245` (매도 수수료 0.015% + 매도 거래세 0.23%).
  - 공식: `daily_return[t] = raw_return[t] - cost_buy * max(delta_position, 0) - cost_sell * max(-delta_position, 0)`.
  - 수치 예시 (crypto): 진입(size=1.0) 비용 0.001, 청산 비용 0.001, 왕복 0.002 = 0.20%.
  - 수치 예시 (krx): 진입(size=1.0) 비용 0.00015, 청산 비용 0.00245, 왕복 0.0026 = 0.26%.
  - **적용 위치**: `tests/test_strategy_catalog_integration.py` fixture 와 `scripts/measure_strategy_catalog.py` 만. 전략의 `on_bar` 내부, orchestrator 실행 경로에는 비용 로직을 주입하지 않는다 (전략 = 순수 신호 생성, harness = 비용/일수익률 생성으로 분리).
- **AsyncStrategy protocol** -- 3개 전략 모두 `async on_bar(self, ctx) -> Signal | None` 준수.
- **register_strategy_returns 호출 필수** -- 모든 전략이 orchestrator 에 일수익률 시계열 공급 (비용 차감된 시계열).
- **스펙 파일에 리스크 연동 기재** -- 각 `docs/specs/strategies/<id>.md` 에 `register_strategy_returns(...)` 텍스트 포함.
- **실측 상관매트릭스 PR merge gate** -- `scripts/measure_strategy_catalog.py` 결과가 `02_implementation.md` 에 첨부되어야 PR 머지 가능.
- **param grid <= 3 axes (코드 리뷰로 강제)** -- 각 전략 하이퍼파라미터 최대 3개. meanrev: (window, z_threshold, vol_target). breakout: (entry_window, exit_window, kelly_k). momo_vol: (vol_ceiling, macd_params, vol_target). 각 전략 클래스 docstring 에 튜너블 파라미터 목록 명시 필수.
- **KIS 페이퍼 환경에서 실측 스크립트 검증** -- 실제 live 계좌 호출 금지.
- **KOSPI200 universe pin-date 기록** -- `02_implementation.md` 에 pin-date 2026-04-25 명시 (survivorship 투명성).
- **Rate limit 준수** -- `price_client.py` 의 요청 간 sleep `0.5s` (paper 2rps 대비 여유 50%).
- **Tick scheduler — harness 가 `strategies=` 필터로 tick 별 호출 전략 선택** -- §B0 "Tick Scheduler" 표 대로. 15m tick 에 `breakout_donchian` 을 깨우는 등의 낭비 호출 금지. 실측 스크립트(`scripts/measure_strategy_catalog.py`) 와 향후 live feeder 양쪽에 동일 규칙 적용.
- **Self-guard — 전략 내부 `_is_my_bar_boundary(ts)` 가드 필수** -- 각 AsyncStrategy 는 자기 bar boundary 가 아닌 tick 에 대해 즉시 `Signal(action="hold", reason="not my bar")` 반환. harness 필터 실수 방지용 2중 안전망.
- **KRX 거래시간 gate — `breakout_donchian` 은 평일 KRX 장마감 15:30 KST tick 에만 호출** -- 장외 시간·주말·휴장일에는 harness 가 `strategies=` 에서 제외. 전략 내부 self-guard 도 `is_krx_holiday(ts.date())` 로 재확인.

#### Must NOT Have
- **LLM 호출 금지** -- 전략 코드 내 LLM API 호출 일체 금지 (CLAUDE.md invariant #6).
- **Live 계좌 TR 호출 금지** -- 반드시 paper 환경에서 실행. inquiry TR 은 paper/live 공용이지만, 기본적으로 paper 환경(`openapivts`) 에서 실행.
- **KIS 토큰 노출 금지** -- 코드/로그/PR 에 토큰 노출 불가. 기존 `auth.py` 의 디스크 캐시 재사용.
- **`.draft.md` 미승격 상태 PR 금지** -- 모든 문서는 정식 `.md` 확장자.
- **자동 커밋 금지** -- 모든 커밋은 사용자 확인 후 수동 실행.
- **외부 라이브러리 신규 추가 금지** -- numpy, pandas, pydantic, requests, sklearn 등 기존 의존성만 사용.


### F) Risks & Edge Cases

1. **R1: rho <= 0.6 gate failure** -- `momo_vol_filtered` 가 `momo_btc_v2` 와 같은 종목(BTCUSDT) 사용으로 상관이 높을 위험.
   - Mitigation: 시간축 차이(4h vs 15m) 와 vol filter 로 진입 구간을 다르게 함. 상관이 0.6 을 초과하면 `vol_ceiling` 을 60% 로 낮춰 필터를 강화하고, 실패 시 시간축을 1d 로 변경.
   - Retry 문서화: `02_implementation.md` 에 파라미터 조정 이력 기록.

2. **R2: ENB 계산을 위한 최소 관측치** -- `compute_portfolio_risk_from_df` 는 T(관측일수) 가 충분해야 Ledoit-Wolf shrinkage 가 안정. T < 60 이면 ENB 가 불안정.
   - Mitigation: 통합 테스트에서 `T >= 252` (1년) synthetic data 사용. 실측 시에도 최소 60일 확보 후 측정.

3. **R3: Binance rate limit / 429** -- `fetch_binance_klines` 호출 시 rate limit.
   - Mitigation: 기존 `src/data_lake/fetcher.py` 의 retry 경로 재사용. 테스트는 synthetic data 로 작성하여 네트워크 의존성 제거.

4. **R4: Survivorship bias (pairs)** -- ETHBTC 단일 페어만 사용하므로 다른 cross pair 가 더 나을 수 있음.
   - Mitigation: #79 scope 에서는 ETHBTC 고정. 유니버스 선정 기준일(pin date) 을 spec 파일에 명시.

5. **R5: Walk-forward validation** -- #79 AC 에 walk-forward 는 포함되지 않음.
   - Mitigation: 각 전략 spec 에 "하이퍼파라미터는 in-sample 결과. Walk-forward validation 은 후속 이슈에서 수행" 으로 명시.

6. **R6: AsyncStrategy ctx 형식 의존** -- `ctx = {"ts": ts, "market_snapshot": dict}` 형식이 변경되면 3개 전략 모두 영향.
   - Mitigation: key 이름을 상수로 관리. 형식 변경은 #78 API freeze 로 방지됨.

7. **R7 (B안 신규): KIS rate limit 초과로 수집 중단** -- KOSPI200 200종목 x 252일봉 = 대량 요청. paper 2rps 에서 수백 요청 소요.
   - Mitigation: `price_client.py` 에 요청 간 0.5s sleep + 429 시 지수 backoff 재시도. 부분 수집 재개를 위해 start_date 기준 incremental fetch. 실측 스크립트에서 종목별 수집 진행률 로깅.

8. **R8 (B안 신규): KOSPI200 구성 변경** -- 편입/편출로 과거 데이터 미수집 종목 발생.
   - Mitigation: pin-date 2026-04-25 snapshot 고정. 편출 종목에 대해서도 상장 폐지 전까지는 데이터 수집 시도. `02_implementation.md` 에 pin-date 와 수집 실패 종목 목록 기록.

9. **R9 (B안 신규): FX 변환 미적용** -- 원화/달러 동시 운용 시 환율 변동.
   - `daily_return` 은 **각 전략 단위의 로컬 통화 수익률** 로 정의:
     - ETHBTC = BTC-상대 수익률
     - KOSPI200 = KRW-상대 수익률
     - BTCUSDT = USD-상대 수익률
   - 포트폴리오 수준 FX 정규화는 #79 scope 외 -- follow-up 이슈.

10. **R10 (B안 신규, Architect I2 반영): KRX 휴장일 + 암호화폐 24/7 스케줄 불일치**.
    - **결합 DataFrame 은 교집합 캘린더(intersection)** 로 구성. `src/backtest/calendar_align.py::intersect_trading_days` 가 모든 전략의 index 교집합(= 모든 전략이 실제 거래한 날) 만 유지.
    - **0-fill 금지**: 휴장일을 0 으로 채우면 KRX 분산이 인위적으로 축소 → ENB 상승 → 게이트 거짓 통과. NaN drop(`compute_portfolio_risk_from_df` 의 `dropna(how="any")`) 도 크립토 주말 데이터 손실. 두 방식 모두 통계적으로 부정확.
    - 최종 규칙: 개별 전략 시계열은 각자 달력에 맞게 계산(크립토 ~365/yr, KRX ~250/yr) → 교집합 정렬 후에야 portfolio 수준 상관/ENB 계산.
    - 연환산 샤프 등 per-strategy 지표는 각 전략 고유 달력 기준으로 별도 계산(`02_implementation.md` 에 명시).
    - 일봉 기반 전략이므로 KRX **거래일 여부만 판정** 하면 충분 (`src/universe/krx_calendar.py::is_krx_holiday`). `src/execution/krx_handler.py` 는 주문-레이어 단일가 매매 버퍼라 여기서 부적합.


### G) Verification & Measurement Plan

#### Layer 1 (CI synthetic, 기존 유지): `tests/test_strategy_catalog_integration.py`

```
test_combined_portfolio_risk:
  - 4 전략 deterministic synthetic returns (seed=79):
      momo_btc_v2        -> crypto calendar (약 365일)
      meanrev_pairs      -> crypto calendar (약 365일)
      momo_vol_filtered  -> crypto calendar (약 365일)
      breakout_donchian  -> KRX calendar    (약 250일, 주말+휴장일 없음)
  - df = intersect_trading_days({sid: series, ...})  # 교집합 ≈ 250일, 0-fill 금지
  - report = compute_portfolio_risk_from_df(df)
  - assert report.enb_ratio >= 0.5
  - for new_strat in ["meanrev_pairs","breakout_donchian","momo_vol_filtered"]:
      pairwise_corr = df["momo_btc_v2"].corr(df[new_strat])
      assert abs(pairwise_corr) <= 0.6
  - assert report.cvar_pct is not None
  - assert report.enb is not None
  - assert report.n_strategies == 4

test_portfolio_risk_report_fields:
  - smoke test: PortfolioRiskReport 가 cvar_pct, var_pct, corr_avg, enb, enb_ratio,
    n_strategies, n_observations, alpha, ts 필드를 모두 가짐
```

#### Layer 2 (PR gate, B안 확장): `scripts/measure_strategy_catalog.py`

- **입력 데이터**:
  - Binance: `fetch_binance_klines` 로 meanrev_pairs (ETHBTC 1h) + momo_vol_filtered (BTCUSDT 4h) 수집. 최소 T=252 bar.
  - KIS paper: `fetch_kis_daily_ohlcv` 로 breakout_donchian (KOSPI200 종목 일봉) 수집. 최소 T=252 거래일.
  - Binance API 제약 시 `tests/fixtures/` 에 동결한 historical snapshot 을 fallback 으로 사용.
- **실행**: 4 전략(momo_btc_v2 포함) 의 on_bar 루프를 harness 로 돌려 일수익률 시계열 산출.
  - **Instrument-type 별 비용 차감**: `_apply_cost(returns, positions, instrument_type)`.
  - crypto (meanrev_pairs, momo_vol_filtered, momo_btc_v2): `cost_rate_per_side = 0.001`.
  - krx (breakout_donchian): `cost_buy = 0.00015`, `cost_sell = 0.00245`.
- **출력**: `compute_portfolio_risk_from_df(df)` 호출 후 Markdown 테이블을 `02_implementation.md` 에 append:
  ```
  |                    | momo_btc_v2 | meanrev_pairs | breakout_donchian | momo_vol_filtered |
  |--------------------|-------------|---------------|-------------------|-------------------|
  | momo_btc_v2        | 1.000       | 0.xxx         | 0.xxx             | 0.xxx             |
  | meanrev_pairs      | 0.xxx       | 1.000         | 0.xxx             | 0.xxx             |
  | breakout_donchian  | 0.xxx       | 0.xxx         | 1.000             | 0.xxx             |
  | momo_vol_filtered  | 0.xxx       | 0.xxx         | 0.xxx             | 1.000             |

  ENB: X.XX / ENB ratio: X.XX / CVaR(97.5%): X.XX%
  T=<관측일수> / 수집 기간: <start>~<end>
  Instrument cost model: crypto=0.20% RT, krx=0.26% RT (asymmetric)
  KOSPI200 pin-date: 2026-04-25 (N=<구성 종목수>)
  ```
- **이 파일이 첨부되지 않으면 AC 4/5/8 미충족 -> PR merge 차단.**
- 실패 시 R1 mitigation (vol_ceiling 하향, timeframe 변경, breakout window 확대) 후 재측정.

#### ENB/rho gate 실패 시 워크플로우

- **Layer 1 (CI, synthetic)**: `test_strategy_catalog_integration.py::test_combined_portfolio_risk` 는 seed=79 synthetic 데이터로 파이프라인 mechanics 를 검증한다. 결정적이므로 실패하면 코드 버그 -> 바로 수정.
- **Layer 2 (PR review, real)**: `scripts/measure_strategy_catalog.py` 결과가 `enb_ratio < 0.5` 또는 `max |rho| > 0.6` 이면 CI 는 통과하지만 PR merge 불가.
  - 조치 체인:
    1. `momo_vol_filtered` 의 `vol_ceiling` 을 80% -> 60% 로 낮춤.
    2. `momo_vol_filtered` timeframe 을 4h -> 1d 로 변경.
    3. `breakout_donchian` entry_window 를 20 -> 55 로 확대.
  - 각 조치 후 `02_implementation.md` "파라미터 조정 이력" 섹션에 before/after ENB/rho 기록.
  - 3단계 조치 후에도 게이트 통과 실패하면: PR 에서 실패 사유 명시 + 후속 이슈 생성 후 reviewer 판단으로 merge 여부 결정.

#### 단위 테스트 커버리지 목표

| Test file | Key assertions |
|-----------|---------------|
| `test_broker_kis_price.py` | mocked HTTP 1페이지/2페이지 연속 조회, 빈 응답, 429 재시도, KISDailyBar 필드 |
| `test_fetch_kis_daily_ohlcv.py` | OHLCV_SCHEMA 컬럼 일치, 빈 응답 시 빈 DataFrame, source="kis" |
| `test_universe_kospi200.py` | 리스트 길이 190~210, 6자리 코드, 중복 없음, 필수 종목 포함 |
| `test_donchian_signal.py` | upper = rolling max, lower = rolling min, NaN for warmup, window param |
| `test_zscore_signal.py` | z = 0 at mean, z = +/-2 at 2 sigma, NaN handling, window param |
| `test_meanrev_pairs.py` | buy at z<-2, sell at z>0, hold in band, Signal fields, sizing range |
| `test_breakout_donchian.py` | buy on upper break, sell on exit lower break, top-N selection, dual window, confidence, KRX code input |
| `test_momo_vol_filtered.py` | vol filter blocks entry, MACD entry, emergency exit, Signal fields |
| `test_strategy_catalog_integration.py` | enb_ratio, pairwise corr, report fields, n_strategies |


### H) ADR -- 전략 카탈로그 확장 B안 (#79)

- **Decision**: #74 (KIS 재무) CLOSED 확인 후 B안(Multi-venue) 으로 확장. `breakout_donchian` 을 KOSPI200 KRW 시장에 배치해 암호화폐-주식 간 저상관을 구조적으로 확보. KIS OHLCV fetcher 를 `src/brokers/kis/price_client.py` + `src/data_lake/fetcher.py::fetch_kis_daily_ohlcv` 2-layer 로 신설.
- **Drivers**:
  1. 실 KIS API 키 활용 (A안 crypto-only 는 키 낭비)
  2. 암호화폐-주식 저상관 -> AC 5 (avg rho <= 0.6) 달성 확률 대폭 상승
  3. `meanrev_pairs` / `momo_vol_filtered` 는 crypto 에 남겨 시장 커버리지 균형
- **Alternatives considered**:
  - A안 (crypto-only) -- KIS 키 사장, 3개 전략 모두 Binance 로 상관 감소 여지 제한적. `breakout_donchian` (BTCUSDT 1d) 과 `momo_btc_v2` (BTCUSDT 15m) 간 상관이 AC 5 마진을 위협.
  - #79 scope 에 KIS 시세 fetcher 만 추가하고 전략은 crypto 로 유지 -- 전략-데이터 mismatch, fetcher 활용 증빙 불가.
  - `breakout_donchian_btc` + `breakout_donchian_krx` 병행 (전략 4개) -- scope 20% 증가 대비 한계효용 낮음. 전략 3개 → 4개로 늘리면 ENB 마진은 소폭 증가하지만 복잡도 대비 부적합.
- **Why chosen**: Binance 만으로는 AC 4/5 마진이 작아 실패 리스크. KIS 추가로 근본적인 상관 축소 확보. 2-layer fetcher 는 기존 `fundamentals_client.py` 패턴과 대칭이라 유지보수 부담 낮음. KRX 거래비용 비대칭을 `_apply_cost` 에서 명시적으로 처리해 실측 정확도 보장.
- **Consequences**:
  - (+) 암호화폐-주식 포트폴리오 토대 확보 -> 후속 이슈에서 확장 용이.
  - (+) KIS 시세 fetcher 는 다른 전략(밸류에이션 스크리너 등) 에서도 재사용.
  - (+) instrument_type 비용 분기 인프라가 향후 추가 시장(미국주식 등) 확장의 기반.
  - (-) PR 크기 30~50% 증가 (fetcher 2파일 + universe 파일 + 테스트).
  - (-) KIS paper 환경 의존 -- CI 에서는 mocked 응답으로 대체, 실측은 개발자 로컬.
  - (-) FX / 거래시간 불일치에 대한 추가 규칙 명시 필요 (R9/R10 반영).
- **Follow-ups**:
  - `SyncToAsyncAdapter` 로 sync Strategy 와 호환 (기존 follow-up 유지)
  - FX 정규화 포트폴리오 수익률 (별도 이슈)
  - KOSPI200 survivorship 편향 대응 -- delisted stock 포함 historical snapshot (별도 이슈)
  - KIS WS 실시간 시세 -> 라이브 실행 (#80)
  - KIS 호가/체결 실시간 feed 로 일봉 외 시간축 확장
  - `src/universe/krx_calendar.py` 의 2027+ 휴장일 업데이트 경로 (연간 갱신 스크립트 또는 외부 라이브러리 도입) — Critic 3e 권장

---

### I) Changelog (Consensus 반영)

A안 (crypto-only) -> B안 (multi-venue) pivot. 아래 변경 사항 반영:

1. A안 P3 "Crypto-only universe" 삭제 -> P3 "Multi-venue universe (Binance + KRX)" 신설.
2. P6 "Instrument-type 비용 모델 분기" 신규 추가.
3. `#74` 의존성 상태 CLOSED 로 정정 (이전 플랜은 "진행 중" 으로 오기재).
4. `breakout_donchian` 을 BTCUSDT 1d -> KOSPI200 1d 로 전면 재설계 (B2 섹션).
5. §C1 신설: KIS OHLCV fetcher 2-layer (`price_client.py` + `fetch_kis_daily_ohlcv`).
6. §C4 신설: KOSPI200 유니버스 정적 리스트 (`src/universe/kospi200.py`).
7. §D Step 0/1 신설: KIS fetcher + KOSPI200 universe TDD (기존 Step 1~7 -> Step 2~9 로 재번호).
8. §E Must Have: instrument_type 분기 비용 공식 추가, KIS paper 검증 필수, pin-date 기록, rate limit 준수.
9. §E Must NOT: live 계좌 호출 금지, KIS 토큰 노출 금지.
10. §F R7~R10 신규: KIS rate limit, KOSPI200 구성 변경, FX 미적용, 휴장일 불일치.
11. §G Layer 2 확장: Binance + KIS 데이터 소스, instrument_type 별 비용 분기, KOSPI200 pin-date snapshot 출력.
12. §H ADR 전면 재작성: B안 Decision/Drivers/Alternatives/Consequences/Follow-ups.
13. Option A/B 설계 질문을 "전략 protocol 선택" 에서 "KIS OHLCV 수집 지점" 으로 변경 (전략 protocol 은 A안에서 이미 확정).
14. A안 Consensus 반영 사항 (Architect/Critic 피드백 9건) 은 본 B안에 전부 계승:
    - Option B invalidation = signature shape mismatch (유지).
    - measure 스크립트 = MUST HAVE PR gate (유지).
    - §B0 ohlcv_history 계약 정식화 (유지 + KRX 확장).
    - 비용 공식 harness 전용 적용 (유지 + instrument_type 분기 확장).
    - ENB/rho 2-layer 워크플로우 (유지).
    - `src/signals/.ai.md` MODIFY (유지).
    - param grid <= 3 axes 코드 리뷰 강제 (유지).
    - zscore docstring 에 bollinger.pct_b 차이 명시 (유지).
15. **§B0 Tick Scheduler 신설** — 4 전략의 timeframe·시장 스케줄 미스매치로 인한 헛호출 방지. harness 가 `AsyncStrategyOrchestrator.run_bar(..., strategies=[...])` 인자로 tick 별 호출 전략을 선택 (15m → momo_btc_v2 / 1h → +meanrev_pairs / 4h → +momo_vol_filtered / KRX 15:30 KST → breakout_donchian). `asyncio.gather` 병렬 실행 특성상 tick 당 지연 = 가장 느린 전략 1개 분량이므로 과부하 우려 없음.
16. **§B0 Self-guard 신설** — 전략 내부 `_is_my_bar_boundary(ts)` 가드로 harness 필터 실수 시에도 즉시 `Signal(action="hold", reason="not my bar")` 반환 (2중 안전망).
17. **§E Must Have 3조항 추가** — (a) Tick scheduler 필터 규칙 준수, (b) self-guard 필수, (c) `breakout_donchian` 은 KRX 장마감 15:30 KST tick 에만 호출 + `is_krx_holiday()` 재확인.
18. **Architect 재리뷰 반영 (I1~I7, 2026-04-25)**:
    - I1: `price_client.py` 429 재시도 자체 구현 명시 (기존 `_request_with_retry` 는 5xx 만 처리하므로 계승 불가).
    - I2: `src/backtest/calendar_align.py::intersect_trading_days` 신설 + R10 재작성 (0-fill 폐기, 교집합 캘린더로 통계 무결성 확보).
    - I3/I6: §B2 에 "backtest-only, portfolio-level Signal + top-N equal-weight basket 일수익률 = mean_returns" 명시하여 P2 (register_strategy_returns) 계약 충족.
    - I4: `breakout_donchian` 의 ATR 정규화·confidence 에 `atr_14.shift(1)` 명시 (look-ahead 제거).
    - I5: `src/backtest/cost.py::apply_cost` 공통 헬퍼 신설 (테스트 + 실측 스크립트 공유).
    - I7: `src/universe/krx_calendar.py::is_krx_holiday / is_krx_trading_hours` 신설 (기존 `src/execution/krx_handler.py` 는 주문-레이어 단일가 버퍼라 부적합).
    - Layer 1 통합 테스트 pseudocode 를 교집합 캘린더 반영해서 재작성 (crypto ~365 + KRX ~250 → 교집합 ~250).
