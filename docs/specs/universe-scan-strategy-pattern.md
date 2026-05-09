---
type: spec-architecture
id: universe-scan-strategy-pattern
name: Universe-Scan Strategy Pattern
title: Universe-Scan Strategy Pattern (default for new equity strategies)
status: adopted
owner: siwoo
created: 2026-05-06
last_updated: 2026-05-06
tags:
- pattern
- strategy
- portfolio
- equities
---

# Universe-Scan Strategy Pattern

> **방향 결정 (2026-05-06).** 신규 주식 전략은 단일/소수 종목 고정이 아니라 **유니버스 전체 스캔 → 랭킹 → 상위 N 보유** 패턴을 기본으로 한다. KIS 모의 운영 중인 단일종목 전략 (`momo_kis_v1` 005930)은 legacy 로 유지하되 신규 KRX 주식 전략은 본 패턴을 따른다.

## 동기

- 1인 운용·현금 시장(KRX) 환경에서 특정 종목에 묶이면 시장 국면 (테마 회전·시총 변동) 에 적응 못 함.
- 모멘텀·평균회귀·이벤트 팩터는 **횡단면(cross-sectional)** 으로 평가될 때 신호 밀도가 가장 높다 ([[42-cross-sectional-momentum-crypto]], [[44-time-series-momentum-crypto]]).
- 본 프로젝트는 이미 `breakout_donchian.py` (#79) 에서 KOSPI200 전체 스캔 → top-10 으로 구현 경험 보유 → 패턴화하여 재사용한다.
- 2026-05-06 cross-sectional TSMOM 12-1 backtest 결과 (Sharpe 0.871, Ann 23.0% vs KOSPI 0.656/12.0%, MDD -43%) — 단일종목 swing 4개 (대부분 0 trades) 대비 명백히 우위 ([[cs-tsmom-kr-daily]]).

## 적용 자산 군

본 패턴은 자산군에 무관하다. 같은 컴포넌트를 자산군별 인스턴스로 구체화한다.

| 자산군 | universe_builder 예 | rebal 주기 (제안) | 비용 (라운드트립) |
|-------|--------------------|-------------------|-------------------|
| KRX 주식 | `top_n_by_marcap("KOSPI", 200) + top_n_by_marcap("KOSDAQ", 150)` | 주간 (Friday 15:30 KST) | ~55bp (commission + slippage + 거래세) |
| Binance 무기한 | `top_n_by_volume("USDT-PERP", 20, exclude=stablecoins)` | 주간 또는 8h funding cycle 정렬 | ~10bp + funding 보정 |
| Binance 현물 | `top_n_by_marketcap("USDT-spot", 20)` | 주간 (UTC 일요일 마감) | ~10bp (taker) |
| 향후 (해외주식) | top by ADV in S&P500 (#19 broker 추가 시) | 주간 | TBD |

**자산군 specific 주의**:
- 크립토는 24/7 시장 → bar boundary 정의 (UTC vs KST) 필수, weekend 데이터 포함.
- 크립토는 stablecoin/wrapped (USDC, WBTC 등) 제외 필터 필수 — 모멘텀 0 으로 풀이 오염.
- KRX 는 거래정지·관리종목·우선주 제외 필터 권장.
- 해외주식 (#19 broker 확장 시) 은 시간대·환율 보정 필요.

## 패턴 정의

```
유니버스 스캔 전략 = (
    universe_builder   # 어떤 종목들을 감시할지
  + data_panel         # 종목×시점 OHLCV 패널
  + liquidity_filter   # 거래 가능 종목만
  + ranker             # 신호 점수 (모멘텀/평균회귀/이벤트)
  + selector           # 상위 N 픽 (또는 임계 통과 전부)
  + sizer              # 동일/역변동성/Half-Kelly 가중치
  + scheduler          # 리밸 주기 (주간/월간/이벤트 트리거)
  + cost_model         # 종목당 회전율 × 라운드트립 비용
  + risk_integration   # daily_return_series → orchestrator
)
```

### 필수 컴포넌트

| 컴포넌트 | 책임 | 코드 위치 (제안) |
|---------|-----|------------------|
| `universe_builder` | 종목 코드 리스트 (시총·지수 편입·테마 등 기준) | `src/universe/krx_top.py`, `src/universe/kospi200.py` 등 |
| `data_panel` | 종목×날짜 OHLCV+turnover 패널 (parquet 캐시 권장) | `src/data_lake/` (#20) 확장 |
| `liquidity_filter` | 거래대금/가격 임계 적용한 마스크 | strategy 내부 또는 `src/universe/filters.py` |
| `ranker` | 종목별 점수 (예: log(close[t-skip]/close[t-long])) | strategy 모듈 내부 함수 |
| `selector` | 상위 N (또는 score > threshold) 결정 | strategy 모듈 |
| `sizer` | weight ∈ [0, 1], Σ weight ≤ 1 | `src/risk/sizing.py` 활용 |
| `scheduler` | 리밸 트리거 (Friday close, Monthly, ...) | strategy `on_bar` 분기 |
| `cost_model` | turnover 기반 비용 차감 | `src/backtest/cost.py` 의 `apply_cost(returns, positions, "krx")` |
| `risk_integration` | 바스켓 일수익률 → `register_strategy_returns` | orchestrator 등록 (#70) |

## Strategy protocol 와의 관계

`AsyncStrategy` (`src/backtest/protocol.py`) 는 단일 신호를 반환하지만, 유니버스 전략은 **바스켓 수준에서 단일 합성 Signal 또는 다중 종목 Signal 리스트** 를 반환한다.

- backtest 단계 (#79 기준): 바스켓 일수익률만 산출하면 충분 — `breakout_donchian.py` 가 본 패턴.
- 라이브 단계 (#80 후속 트랙): 종목별 주문은 별도 처리 (orchestrator 단에서 weights → orders 변환). 이는 본 패턴의 후속 이슈.

## 진입 / 청산 / 리밸 — 표준 흐름

```python
# 매 리밸 시점 (예: 매주 금요일 마감)
def rebalance(date, panel):
    universe = universe_builder.list(as_of=date)         # 350개
    panel_t = panel.at_date(date)
    score = ranker.score(panel_t)                       # Series[code → float]
    score = score[liquidity_filter.mask(panel_t)]
    picks = selector.top_n(score, n=20)                 # 상위 20
    weights = sizer.equal_weight(picks)                 # 5%씩
    return weights                                      # Series[code → float]
```

비-리밸일은 weights 유지 (drift 허용). 다음 리밸에 가중치 재계산.

## 비용 모델

- KRX 매수 수수료 + 슬리피지 약 30bp, 매도 약 25bp + 거래세 25bp 보수 추정.
- 본 패턴 default: **55bp 라운드트립** (commission + slippage 포함).
- 종목별 차등 (대형 vs 중소형) 은 후속 정밀화. 1차 검증은 평균값 사용.

## 리스크 연동 (#70 mandatory)

본 패턴 전략도 단일종목 전략과 동일하게 `daily_return_series` 공급 의무.

```python
orch.register_strategy(strategy_id, strategy)
orch.register_strategy_returns(strategy_id, daily_return_series)
orch.refresh_portfolio_risk()
```

- `daily_return_series` = 바스켓 일수익률 (비용 차감 후).
- `intersect_trading_days` 로 다른 전략 (crypto / single-ticker) 과 정렬 후 ENB/CVaR 평가.

## Survivorship bias 가이드

- "현재 시점 시총 상위 N" 으로 universe 정의하면 백테스트 시 이미 사라진 종목 누락 → 결과가 낙관 편향.
- 1차 검증에서는 인정·기재 (`Universe pin-date: YYYY-MM-DD`).
- 정밀 검증 (PR 머지 조건) 은 PIT (point-in-time) 시총 스냅샷 필요 — 후속 이슈에서 KRX 자료 수집.

## 새 패턴-기반 전략 PR 체크리스트

- [ ] `docs/specs/strategies/<id>.md` 프론트매터 + universe pin-date 기재
- [ ] universe_builder 가 결정적 (`as_of` 인자 받음)
- [ ] liquidity_filter 임계값 명시 (turnover, price)
- [ ] ranker / selector 가 look-ahead-free (`shift(1)` 등)
- [ ] cost_model = `apply_cost(..., "krx")` 또는 동등 함수
- [ ] `register_strategy_returns(...)` 호출 1건 단위 테스트
- [ ] 백테스트 결과 (Sharpe / MDD / Ann / Turnover / 평균 보유종목수) frontmatter 갱신
- [ ] benchmark (KOSPI / KOSDAQ150) 대비 알파 수치 명시

## 관련 노트

- [[breakout-donchian]] — 본 패턴의 첫 사례 (#79)
- [[cs-tsmom-kr-daily]] — 본 패턴의 모멘텀 사례 (이 문서와 함께 도입)
- [[19-portfolio-risk]] — 다전략 리스크 통합
- [[20-position-sizing]] — 사이징 이론
- [[42-cross-sectional-momentum-crypto]] — cross-sectional 학술 배경
- [[44-time-series-momentum-crypto]] — 시계열 모멘텀 학술 배경

## 출처

- Moskowitz, Ooi, Pedersen (2012) — *Time Series Momentum*, JFE.
- Asness, Moskowitz, Pedersen (2013) — *Value and Momentum Everywhere*, JoF.
- Faber (2007) — *A Quantitative Approach to Tactical Asset Allocation*.
- 본 레포 #79 (전략 카탈로그 확장), #70 (리스크 모듈), #78 (multi-strategy async 오케).
