---
type: strategy
id: live-airborne-bb-reversal-v2
name: Live Airborne BB Reversal v2 (40% Retracement + Trend Alignment)
status: backtest
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1h
uses_signals:
- bollinger
- sma
risk_rules:
- per-symbol-stop-loss-2pct
- per-symbol-take-profit-4pct
owner: siwoo
created: 2026-05-20
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: 113
backtest_period: 2025-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.02
take_profit_pct: 0.04
trailing_stop_pct: null
profit_factor_bt: 1.296
expectancy_bt: 0.004544
verdict_5y: null
verdict_1y: "passed: PF=1.296 (best 2.0/4.0 R/R on trend_sma=50), expectancy=+0.454%/trade>0 on 1y BTC+ETH 1h, 6/8 R/R combos PASS. First BB-reversal family member to cross PF=1.0 gate. trend_sma robustness: SMA(50)=6 PASS, SMA(200)=1 PASS, SMA(100)=0 PASS — likely best-arm overfit risk; 5y multi-regime validation mandatory before paper."
summary_ko: |
  v1(rejected)의 진입 수식 (BB 돌파 + 40% 되돌림 + 봉 확정 close) 에 추세 정렬
  게이트(현재 close > SMA(50)) 를 추가한 변형. 1y BTC+ETH 1h sweep 에서 가족
  최초로 PF>1 통과 (최고 PF=1.296 / +0.454%/trade @ 2.0/4.0 R/R, 113 거래).
  trend_sma=50 에서만 6/8 조합 PASS, 100 은 v1 보다 나쁘고 200 은 1/8 만 통과
  — 최적 arm 의 overfit 가능성 존재. 5y 다중 레짐 검증 필수.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- airborne
- external-lecture
- reverse-engineered
- trend-filter
- pattern:live-scanner
---

# Live Airborne BB Reversal v2

[[live-airborne-bb-reversal]] (v1, **rejected** at PF=0.912) 에 **추세 정렬 게이트** 를 추가한 변형. v1 의 BB 돌파 + 40% 되돌림 + 봉 확정 close 핵심은 유지하고, 다음 추가 게이트로 진입 시점을 필터링:

```
Gate (new):  close[-1] > SMA(close, trend_sma_period)
```

기본값 `trend_sma_period = 50` (1h × 50 ≈ 2일 추세). 강의 §3 "큰 프레임 추세가 작은 프레임을 이긴다" 의 단순 reformulation. 추세 위에 있을 때만 BB 하단 reclaim 을 long 진입으로 받아들임.

## 사전등록 가설

> "v1 (40% 되돌림 신호 단독) 이 PF<1 (0.912) 인 이유는 진입이 추세 방향과 무관하게 발생하기 때문이다. 현재 봉이 SMA(N) 위에 있을 때만 long 진입을 허용하면 false bounce (하락 추세 중 일시 반등 후 재이탈) 가 통계적으로 유의하게 줄어들어 PF 가 1.0 을 넘는다."

## 진입 규칙

v1 의 모든 게이트 + 추가:

| 게이트 | 조건 |
|---|---|
| 1. BB 돌파 (v1) | `low[i] <= bb_lower[i]` AND `low[i-1] > bb_lower[i-1]`, i = breakout bar |
| 2. 극값 추적 (v1) | `extreme = min(low[breakout:])` |
| 3. 트리거 (v1) | `trigger = extreme + 0.4 × (base - extreme)`, `base = close[breakout]` |
| 4. 발화 (v1) | `i > breakout`, confirmed close, `close[-1] >= trigger` |
| **5. 추세 정렬 (NEW)** | `close[-1] > SMA(close, trend_sma_period)` |

v1 와 마찬가지로 long-only.

## 청산

`LivePositionRiskManager` 책임 — `stop_loss_pct = 0.02`, `take_profit_pct = 0.04`, `trailing_stop_pct = null`.

1y sweep 의 최고 조합 (2.0/4.0 R/R) 을 v2 default 로 채택. v1 default (3.0/6.0) 는 v2 에서 PF=0.933 으로 떨어졌고, 2.0/4.0 이 PF=1.296 으로 최고.

## 1y 검증 결과 (2026-05-20)

**PASSED — 가족 최초 PF>1 통과.** R/R sweep 8 조합 × trend_sma 3 값 (50/100/200) = 24 조합 평가.

조건: 1y (2025-05-19 ~ 2026-05-19) · BTCUSDT+ETHUSDT 1h · 라운드트립 10bp · `scripts/bench_live_airborne_v2_quick.py`.

### trend_sma=50 (default, 1h × 50 ≈ 2일 추세)

| R/R | trades | win% | payoff | PF | exp/trade | verdict |
|---|---:|---:|---:|---:|---:|---|
| 2.0/4.0 (1:2) ← **v2 default** | 113 | 44.25 | 1.63 | **1.296** | **+0.454%** | **PASS** |
| 1.0/2.0 (1:2) | 134 | 48.51 | 1.35 | 1.271 | +0.238% | PASS |
| 1.5/3.0 (1:2) | 119 | 43.70 | 1.52 | 1.176 | +0.229% | PASS |
| 1.0/3.0 (1:3) | 128 | 38.28 | 1.89 | 1.172 | +0.187% | PASS |
| 0.5/2.0 (1:4) | 144 | 34.72 | 2.06 | 1.095 | +0.068% | PASS |
| 2.0/6.0 (1:3) | 95 | 31.58 | 2.32 | 1.070 | +0.134% | PASS |
| 0.5/1.0 (1:2) | 152 | 46.71 | 1.08 | 0.951 | -0.029% | LOSER |
| 3.0/6.0 (1:2) | 93 | 35.48 | 1.70 | 0.933 | -0.165% | LOSER |

**6/8 조합 PASS. 4 조합이 PF > 1.15.**

### trend_sma=100 (1h × 100 ≈ 4일 추세) — 비교

| R/R | trades | PF | verdict |
|---|---:|---:|---|
| 1.0/2.0 (1:2) | 150 | 0.893 | LOSER |
| 2.0/4.0 (1:2) | 107 | 0.886 | LOSER |
| 2.0/6.0 (1:3) | 97 | 0.837 | LOSER |
| ... (전부 LOSER) | | < 0.9 | |

**0/8 PASS. v1(0.912) 보다 나쁨.**

### trend_sma=200 (1h × 200 ≈ 8일 추세) — 비교

| R/R | trades | PF | verdict |
|---|---:|---:|---|
| 2.0/6.0 (1:3) | 89 | **1.025** | PASS (borderline) |
| 2.0/4.0 (1:2) | 102 | 0.995 | LOSER (보더라인) |
| ... | | < 0.9 | LOSER |

**1/8 PASS, borderline.**

### 통계적 우려

본 결과를 단순 채택하기 전 다음 경고를 명시한다:

1. **Best-arm overfit risk (높음)**. 24 조합 (3 trend_sma × 8 R/R) 시도 중 7개 PASS. trend_sma=50 만 일관 통과, 100 은 v1 보다 *나쁨*. 만약 v2 가 진짜 알파라면 trend_sma 50→100→200 의 결과가 매끄럽게 변할 것이라 기대되나 실제로는 **U-shape (50 통과, 100 실패, 200 보더라인)** 으로 비단조 — 선택된 arm 이 노이즈일 가능성.
2. **표본 크기 보더라인**. trend_sma=50 / 2.0/4.0 의 113 거래는 통계적 신뢰도 marginal (PBO/DSR 보정 후 살아남는지 별도 검증 필요).
3. **단일 시장 (BTC+ETH)**. 30 심볼 확장 시 PF 보존 여부 미확정. `live-bb-lower-bounce` 의 5y 30-심볼 결과 (PF=0.922) 가 1y 좁은 universe 에선 더 나아 보였던 사례.
4. **추세 정의의 자의성**. SMA(50) 가 이상적인지, EMA / Donchian / Higher-TF SMA 가 더 robust 한지 미탐색. v3 후보.
5. **밴드라이딩 잔존**. trend_gate 가 false bounce 일부를 걸러내지만 강한 상승 추세 한가운데서의 BB 하단 reclaim 은 여전히 노이즈일 가능성.

### 1y 비교 종합

| 전략 | 가족 | 최고 PF | freq | 게이트 |
|---|---|---:|---|---|
| `live-bb-lower-bounce` (5y) | BB | 0.922 | 1m | naive reclaim + volume MA |
| `live-airborne-bb-reversal` (1y, v1) | BB | 0.912 | 1h | BB break + 40% retrace |
| `live-mg-bb-reversal` (1y) | BB | 0.834 | 1m | BB break + reclaim + 캔들 패턴 |
| **`live-airborne-bb-reversal-v2` (1y)** | BB | **1.296** | **1h** | **v1 + close>SMA(50)** |

추세 정렬 게이트가 BB 평균회귀 가족을 **PF<1 영역 (0.83~0.92) 에서 PF>1 영역 (1.07~1.30) 으로 점프**시키는 첫 사례. *방향 자체가 맞다*는 증거지만 표본/multiple testing 우려가 남아 있어 5y 검증 없이 paper 진입 금지.

## 5y 검증 (다음 게이트, 필수)

본 spec 의 다음 단계는 **5y 다중 레짐 검증**:

- **데이터**: 5y (2021-05 ~ 2026-05), 30 USDT-perp 심볼
- **하네스**: `scripts/eval_live_scanners_5y.py` 동등 (v2 strategy 추가)
- **통과 조건**: PF > 1.0 AND expectancy > 0 (1y 와 동일 게이트, 다중 레짐 + 더 큰 universe 에서 보존)
- **추가 검증**:
  - 1y best-arm (trend_sma=50, 2.0/4.0) 의 5y 결과
  - trend_sma 의 안정성 (5y 에서 sweet spot 이 동일한지)
  - Walk-forward analysis (rolling 6mo train / 3mo test) — 시간에 따른 알파 안정성
  - PBO (Probability of Backtest Overfitting) 계산

5y 미통과 시:
- `status: rejected`
- `verdict_5y` 채움
- v3 spec 새로 작성 (다른 추세 정의 또는 game 변경)

5y 통과 시:
- `status: paper` 로 전이
- 6~12 개월 paper 운영
- 그 후 production.yaml commented entry → PR 리뷰 → 자본 배분

## 운영 규칙

- **LLM 호출 금지** (불변식 #6)
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml::enabled=true` 양쪽 ON
- 현재 status=backtest — 5y 게이트 통과 전까지 `production.yaml` 미등록
- 단위 테스트: `tests/backtest/test_live_airborne_bb_reversal_v2.py` (7건 통과)

## v1 과의 차이

| v1 | v2 |
|---|---|
| BB 돌파 + 40% 되돌림 + 확정 close | 동일 + **close > SMA(50)** 게이트 |
| stop 3% / TP 6% (1:2) | stop 2% / TP 4% (1:2) — 1y sweep 최고 조합 |
| PF=0.912, exp=-0.223% | **PF=1.296, exp=+0.454%** |
| status: rejected | status: backtest |

v1 코드 (`live_airborne_bb_reversal.py`) 는 v2 가 활용한다 (`evaluate_long_fire` 등 공통 모듈). v1 spec/코드는 historical record 로 보존.

## Pine Script 보존본

- v1 사양: `docs/specs/strategies/live-airborne-bb-reversal.pine` (역공학 결과의 정확한 1:1 재현)
- v2 사양 Pine 은 별도 파일 (`docs/specs/strategies/live-airborne-bb-reversal-v2.pine`) 로 작성 가능 — 추세 게이트 추가만 차이

## 관련

- [[38-airborne-indicator-reverse-engineering]] — 인디케이터 역공학 사양
- [[live-airborne-bb-reversal]] — v1 (rejected). 본 spec 의 base
- [[live-mg-bb-reversal]] — 강의 일반 서술 reformulation (rejected)
- [[live-bb-lower-bounce]] — BB 평균회귀 가족 단순 버전 (rejected)
- [[live-universe-scanner-paradigm]] — live-scanner 패러다임 spec
- `src/backtest/strategies/live_airborne_bb_reversal_v2.py` — 구현
- `tests/backtest/test_live_airborne_bb_reversal_v2.py` — 단위 테스트 (7건)
- `scripts/bench_live_airborne_v2_quick.py` — 1y sweep 하네스
- `external-trading-lecture-techniques.md` §3 — 강의의 "큰 프레임 추세가 작은 프레임 이긴다" 원전
