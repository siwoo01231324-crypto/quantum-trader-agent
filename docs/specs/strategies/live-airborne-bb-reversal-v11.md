---
type: strategy
id: live-airborne-bb-reversal-v11
name: Live Airborne BB Reversal v1.1 (Close-based Breakout + Margin + Body)
status: rejected
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1h
uses_signals:
- bollinger
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-21
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: 11611
backtest_period: 2025-05-19/2026-05-19
last_updated: 2026-05-21
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
profit_factor_bt: 0.815
expectancy_bt: -0.000422
verdict_5y: null
verdict_1y: "rejected: at realistic cost (2-4bp) all R/R sweeps PF<1 (best 0.815 @ 0.30/0.60 maker, bidir 30 sym 1h signal + 5m exit). At cost=0 (ideal) PF=1.013 borderline @ 0.20/0.60 1:3. Indicator native alpha ~0 — any cost kills it. Family base rate confirmed negative."
summary_ko: |
  v1 (high/low 기반 돌파) 의 wick-only 신호 과다 발화 문제를 해결한 정식
  역공학 본. 진입 조건을 close 기준으로 바꾸고 close-BB 마진 0.1% + 봉 body
  0.5% 게이트를 추가. 사용자 라이브 차트 비교에서 원본 에어본(체험판) 인디
  케이터의 신호 위치와 가장 가깝게 일치 (v2/v3 의 추세/거래량 게이트보다
  재현 정확도 우수). 자동 매매 알파는 ~0 — 모든 현실적 비용에서 PF<1 확정.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- airborne
- external-lecture
- reverse-engineered
- reproduction-canonical
- rejected
- pattern:live-scanner
---

# Live Airborne BB Reversal v1.1

[[live-airborne-bb-reversal]] (v1) 의 정정본. v1 이 `high/low` 기반 돌파를 사용하여 wick 만 BB 를 침범하는 봉도 setup 으로 잡았던 문제를 close 기반 + close margin + body margin 게이트로 해결.

사용자 라이브 시각 비교에서 **원본 에어본(체험판) 인디케이터의 신호 위치와 가장 잘 일치** — v2 (추세 게이트), v3 (거래량 게이트) 모두 원본보다 신호를 적게 띄우는데 비해 v1.1 은 원본 패턴을 정직하게 재현. 단 알파 자체는 ~0.

## v1 과의 차이

| | v1 | v1.1 |
|---|---|---|
| 돌파 기준 | `high >= bb_upper AND prev_high < prev_bb_upper` (wick 도 잡음) | `close > bb_upper × (1+margin) AND prev_close <= prev_thr AND body_pct >= min_body` |
| Close 마진 | 0 | **0.1%** (`min_close_margin = 0.001`) |
| 최소 봉 body | 0 | **0.5%** (`min_body_pct = 0.005`) |
| 결과 (1y 30 sym 양방향 4bp) | PF=0.960 (5y), 0/8 PASS | PF=0.815 (1y, best maker), 0/8 PASS |
| 재현 정확도 | 신호 과다 (사용자: "에어본 보다 많음") | 사용자: "거의 일치" |

## 진입 규칙

상태 머신 동일 ([[live-airborne-bb-reversal]] 참조):

```
없음 ─→ 숏 대기 ─→ 에어본 숏
없음 ─→ 롱 대기 ─→ 에어본 롱
```

### 전이 (변경 부분)

| 전이 | 조건 |
|---|---|
| 없음 → 숏 대기 | `close[-1] > bb_upper[-1] × (1+margin)` AND `close[-2] ≤ bb_upper[-2] × (1+margin)` AND `body_pct[-1] ≥ 0.005` |
| 없음 → 롱 대기 | `close[-1] < bb_lower[-1] × (1-margin)` AND `close[-2] ≥ bb_lower[-2] × (1-margin)` AND `body_pct[-1] ≥ 0.005` |

`body_pct = |close - open| / open`

### 트리거 / 발화

v1 과 동일:
```
base = 돌파봉 close, extreme = max(high) or min(low) since
trigger = extreme ∓ 0.4 × |extreme - base|
fire on confirmed close ≤/≥ trigger
```

## 청산

`LivePositionRiskManager` 책임:
- `stop_loss_pct = 0.03`, `take_profit_pct = 0.06`, `trailing_stop_pct = null`

## 1y 검증 결과 (2026-05-21)

**REJECTED.** 7개 (cost × R/R scale) 조합 sweep, 모두 현실적 cost 에서 PF<1.

조건: 1y · BTC+ETH+28 USDT-perp (30 심볼) · 신호 1h, 청산 5m · long+short 양방향 · 10x 레버리지 단타 가정.

| Cost | R/R scale | 최고 PF | PASS | 최고 R/R |
|---|---|---:|---|---|
| 4bp (taker) | 1/10 | 0.677 | 0/8 | 0.30/0.60% coin |
| 2bp (maker) | 1/10 | 0.815 | 0/8 | 0.30/0.60% |
| 4bp | 1/5 | 0.781 | 0/8 | 0.60/1.20% |
| **0bp (이상화)** | **1/10** | **1.013** | **1/8 borderline** | **0.20/0.60% (1:3)** |
| 4bp | 1/2 | 0.820 | 0/8 | 1.0/2.0% |
| 4bp | full (1:1) | 0.820 | 0/8 | 1.0/2.0% |
| **0bp (이상화)** | **1/2** | **1.020** | **1/8 borderline** | **0.25/0.50%** |

→ **모든 현실적 비용 (2~4bp) 에서 PF<1**. 비용 0 가정에서만 1/8 borderline. 인디 자체 알파 ≈ 0.

## 알파 한계 — 사용 가이드

본 spec 은 status: rejected 이지만 **재현 인디케이터로서의 가치는 보존**:

- ✅ **시각 거래 가이드** — BB 돌파 + 40% 되돌림 시점 표시
- ✅ **알람 신호** — Pine 의 alertcondition 으로 진입 후보 알림
- ✅ **체험판 만료 무관** — 사용자 TV 계정에 영구 저장 (`USER;d9f4857aaf05421ab3817870c8e99934`)
- ❌ **자동 매매에 단독 의존 금지** — 1y 모든 시나리오 PF<1 확정
- ❌ **밴드라이딩 회피 = 사용자 책임** — 강한 추세장에서 신호 무시 필요

## v2 / v3 와의 관계

[[live-airborne-bb-reversal-v2]] / [[live-airborne-bb-reversal-v3]] 는 v1.1 위에 게이트 추가:
- v2: + 추세 SMA(50) 게이트
- v3: + 거래량 MA 게이트

이는 **알파 개선 시도** 였으나 5y 다중 레짐 검증에서 둘 다 PF<1 또는 borderline → 1y best-arm overfit. **재현 측면에서도 원본보다 신호 적게 띄움 → 원본과 다름**.

→ v1.1 = 원본 재현 카논. v2/v3 = 알파 탐색 변형 (historical record).

## 코드 위치

| 파일 | 역할 |
|---|---|
| `src/signals/airborne_bb_reversal.py` | breakout / extreme / trigger 계산 헬퍼 |
| `src/backtest/strategies/live_airborne_bb_reversal_v11.py` | **v1.1 정식 strategy (close-기반)** |
| `tests/backtest/test_live_airborne_bb_reversal_v11.py` | 단위 테스트 (9건 통과) |
| `docs/specs/strategies/live-airborne-bb-reversal.pine` | TV Pine v1.1 (`USER;d9f4857...` 슬롯) |
| `scripts/bench_live_airborne_v11_5m_exit_v2.py` | 1h signal + 5m exit bench (양방향, --rr-scale 지원) |
| `scripts/sweep_airborne_v11_params.py` | margin/body 파라미터 sweep |
| `scripts/sweep_band_riding_filters.py` | A/B 필터 효과 측정 |

## 관련

- [[38-airborne-indicator-reverse-engineering]] — 역공학 방법론
- [[live-airborne-bb-reversal]] — v1 (high/low 기반, v1.1 의 모태)
- [[live-airborne-bb-reversal-v2]] — v2 (+ 추세 게이트)
- [[live-airborne-bb-reversal-v3]] — v3 (+ 거래량 게이트)
- [[airborne-family-overview]] — 가족 비교 + 사용 가이드
- [[live-bb-lower-bounce]] — BB 평균회귀 가족 단순 버전 (rejected)
- [[live-mg-bb-reversal]] — 강의 일반 서술 (rejected)
- `external-trading-lecture-techniques.md` — 강의 원본 (repo-root)
