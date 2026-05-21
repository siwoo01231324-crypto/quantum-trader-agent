---
type: spec-architecture
id: airborne-family-overview
name: Airborne BB Reversal Family — Reverse-engineered indicator + alpha variants
owner: siwoo
status: accepted
tags:
- airborne
- bollinger
- mean-reversion
- family-overview
---

# Airborne BB Reversal Family — Overview

본 프로젝트의 **airborne 가족** 전체 정리. 외부 강의의 비공개 인디케이터 "에어본(체험판)" 을 역공학한 결과로 시작된 strategy spec 4개 + 보조 자료를 한 곳에서 navigate 할 수 있게 만든 entry point.

> **다른 에이전트/팀원이 이 폴더에서 작업할 때 먼저 이 파일을 읽으세요.** 어떤 spec 이 *재현 카논* 인지, 어떤 게 *알파 변형 실험* 인지, 코드/Pine 위치가 어디인지 한눈에 파악됩니다.

## TL;DR

| 묻고싶은 것 | 답 |
|---|---|
| "에어본 인디케이터를 본인 차트에 띄우고 싶다" | **v1.1** Pine 사용. TV 슬롯에 영구 저장됨 (`USER;d9f4857aaf05421ab3817870c8e99934`). 체험 만료 무관. |
| "자동 매매에 쓸 알파가 있나" | **❌ 없음.** 가족 전체가 5y multi-regime 에서 PF<1. v3 만 1y borderline (overfit 의심). 자동 매매 의존 금지. |
| "원본 인디케이터 동작이 궁금" | [[38-airborne-indicator-reverse-engineering]] 읽기. 트리거 = 40% 되돌림 + 봉 확정 close. |
| "강의 원본 기법" | repo-root `external-trading-lecture-techniques.md` |

## 가족 구성원 (4 strategies + 1 research + 1 overview)

```
docs/background/
└── 38-airborne-indicator-reverse-engineering.md  ← 역공학 방법론 (type: research)

docs/specs/strategies/
├── airborne-family-overview.md                   ← 이 파일 (entry point)
├── live-airborne-bb-reversal.md                  ← v1 (high/low 기반, historical)
├── live-airborne-bb-reversal-v11.md              ← v1.1 (close 기반, 재현 카논 ★)
├── live-airborne-bb-reversal-v2.md               ← v2 (+ 추세 게이트, 알파 실험)
├── live-airborne-bb-reversal-v3.md               ← v3 (+ 거래량 게이트, 알파 실험)
├── live-airborne-bb-reversal.pine                ← v1.1 Pine 보존본
├── live-airborne-bb-reversal-v2.pine
├── live-airborne-bb-reversal-v3.pine
└── live-airborne-bb-reversal-v12.pine            ← v1.2 Pine (실험: 밴드라이딩 필터)
```

## 4개 변형 비교

| | v1 | **v1.1 ★** | v2 | v3 |
|---|---|---|---|---|
| **목적** | 초기 역공학 | 정식 재현 카논 | 알파 개선 시도 | 알파 개선 시도 |
| **돌파 기준** | high/low | **close + margin + body** | close + trend gate | close + trend + volume |
| **추가 게이트** | 없음 | 0.1% margin, 0.5% body | + SMA(50) | + SMA(50), + vol MA |
| **재현 정확도** (사용자 시각) | 신호 과다 | **원본과 거의 일치** | 신호 과소 | 신호 더 과소 |
| **1y 알파** | PF≈0.91 | PF<0.82 (all cost) | PF=1.296 | PF=1.404 |
| **5y 알파** | PF=0.960 | n/a | PF=0.913 (overfit) | PF=1.012 borderline |
| **status** | rejected | **rejected (재현용)** | rejected (overfit) | rejected (borderline) |
| **Pine 슬롯** | `USER;51422dd3...` | `USER;d9f4857a...` | (별도 슬롯) | `USER;589f40b4...` |

★ = 사용자가 원본 재현으로 가장 가깝다고 시각 확인한 본.

## 핵심 메커니즘 (모든 변형 공통)

```
1. BB(20, 2σ) 돌파 검출 → 상태 = 숏 대기 (high 돌파) 또는 롱 대기 (low 돌파)
2. base = 돌파봉 close, extreme = 돌파 이후 max(high) / min(low) 추적
3. trigger = extreme ∓ 0.4 × |extreme - base|       (40% 되돌림)
4. 확정 close ≤/≥ trigger → 신호 발화
```

변형들의 차이는 *돌파 검출 조건* + *추가 게이트* 만. 핵심 트리거 수식은 동일.

## 결정 흐름

```
원본 에어본 인디케이터 사용 환경                답:
├─ "체험 만료 후에도 시각적으로 같은 인디 쓰고 싶다"  → v1.1 (TV slot 그대로 사용)
├─ "자동 매매 알파 있나"                            → 없음 (가족 전체 rejected)
├─ "강한 추세에서 손실 신호 줄이고 싶다"             → v3 (덜 띄움, but overfit)
├─ "원본과 가장 가까운 시각 재현"                    → v1.1 (★)
└─ "역공학 과정/근거 알고 싶다"                      → 38-airborne-indicator-reverse-engineering.md
```

## 5년 알파 검증 결과 (BB-reversal 가족 전체)

| 전략 | 패러다임 | 5y PF | 5y verdict |
|---|---|---|---|
| `live-bb-lower-bounce` | live-scanner naive | 0.922 | rejected |
| `live-mg-bb-reversal` | live-scanner candle-gate | 0.834 (1y) | rejected |
| `live-airborne-bb-reversal` (v1) | live-scanner high-low | 0.960 | rejected |
| **`live-airborne-bb-reversal-v11`** | **live-scanner close-gate** | **<0.82 (all cost/R/R)** | **rejected** |
| `live-airborne-bb-reversal-v2` | live-scanner + trend | 0.913 | rejected (1y overfit) |
| `live-airborne-bb-reversal-v3` | live-scanner + trend + vol | 1.012 | borderline (overfit risk) |

→ **BB 평균회귀 가족 전체 구조적 음의 엣지**. 진입 신호의 본질적 재설계 없이 PF>1 불가능.

## 시도된 알파 개선 가설 (모두 falsified)

1. **캔들 패턴 게이트** (`live-mg-bb-reversal`): bullish engulfing OR hammer → 1y 16 combos PF<1
2. **추세 정렬 게이트** (v2): close > SMA(50) → 1y PF=1.296 but 5y PF=0.913 (overfit)
3. **거래량 동반 게이트** (v3): vol > vol MA → 1y PF=1.404 but 5y PF=1.012 borderline (overfit)
4. **밴드라이딩 직접 필터** (v1.2 Pine, code 미구현): 직전 N봉 BB 외부 카운트 → 신호 빈도 -84%, 손실률 -4.6pp but 너무 깐깐
5. **BB squeeze 필터** (v1.2 Pine 옵션): bandwidth percentile 25th 이하만 → 동일하게 너무 깐깐

## 코드/스크립트 위치

### Strategy 클래스 (`src/backtest/strategies/`)
- `live_airborne_bb_reversal.py` — v1
- `live_airborne_bb_reversal_v11.py` — **v1.1 (정식 재현)**
- `live_airborne_bb_reversal_v2.py` — v2
- `live_airborne_bb_reversal_v3.py` — v3

### 신호 헬퍼 (`src/signals/`)
- `airborne_bb_reversal.py` — `evaluate_long_fire`, `find_active_long_setup`, `AirborneSetup`, `RETRACE_RATIO`

### 단위 테스트 (`tests/backtest/`)
- `test_live_airborne_bb_reversal.py` — 17 PASS
- `test_live_airborne_bb_reversal_v11.py` — 9 PASS
- `test_live_airborne_bb_reversal_v2.py` — 7 PASS
- `test_live_airborne_bb_reversal_v3.py` — 8 PASS

### 백테스트 하네스 (`scripts/`)
- `bench_live_airborne_quick.py` — v1 1y/5y sweep (long-only, 코인 % R/R)
- `bench_live_airborne_v2_quick.py` — v2 sweep (trend_sma 옵션)
- `bench_live_airborne_v3_quick.py` — v3 sweep (trend + vol)
- `bench_live_airborne_v11_bidir.py` — v1.1 양방향 30 sym 1h-only
- `bench_live_airborne_v11_5m_exit.py` — v1.1 양방향 신호 1h + 청산 5m
- `bench_live_airborne_v11_5m_exit_v2.py` — 위 + `--rr-scale` 옵션 (10x 레버리지 단타)
- `sweep_airborne_v11_params.py` — v1.1 margin/body 36 조합 sweep
- `sweep_band_riding_filters.py` — A (밴드라이딩 count) vs B (BB squeeze) 필터 효과

### Pine Script 보존본 (`docs/specs/strategies/*.pine`)
- 모두 사용자 TV 계정의 별도 슬롯에 저장됨 (체험판 만료 무관)
- `live-airborne-bb-reversal.pine` ↔ TV slot "Airborne BB Reversal (RE v1) 1" = v1.1 code
- `live-airborne-bb-reversal-v2.pine` ↔ 별도 슬롯
- `live-airborne-bb-reversal-v3.pine` ↔ TV slot "Airborne BB Reversal v3 (RE + Trend + Vol)"
- `live-airborne-bb-reversal-v12.pine` ↔ TV slot "Airborne BB Reversal (RE v1.2)" (필터 토글)

## 외부 강의와의 관계

원본 강의 = `external-trading-lecture-techniques.md` (repo-root). 강의는 **MG (Momentum Gap)** 기법을 일반적으로 서술하고 "에어본 지표" 를 도구로 언급. 본 가족은 다음 분기:

- [[live-mg-bb-reversal]] = **강의 서술 그대로** reformulation (캔들 패턴 게이트) → 1y rejected
- airborne 가족 = **강사가 실제 사용하는 인디케이터의 정확한 수식** 역공학 → 강의 서술과 다름 (40% 되돌림 = 수치적 트리거)

→ **강의 서술 ≠ 인디케이터 실제 구현**. airborne 가족이 진짜 강사 매매법에 더 가까움.

## 윤리 / 면책

- v1.1 의 Pine 코드는 *출력값 관찰* 만으로 도출. 보호된 Pine 소스 코드 복호화/우회 없음.
- 원본 작성자 (강의 강사) / 강의 / 인디케이터 / 강의 결제자에 대한 평판 훼손 의도 없음.
- 본 가족 모든 status: rejected — 사용자 본인 책임의 시각 가이드 외 자동 매매 의존 금지.

## 외부 참조

- [[38-airborne-indicator-reverse-engineering]] — 역공학 사양 (research note)
- [[live-universe-scanner-paradigm]] — live-scanner 패러다임 spec
- [[live-bb-lower-bounce]] — BB 평균회귀 단순 버전 (대조군)
- `external-trading-lecture-techniques.md` — 강의 원본 (repo-root)
- `tradesdontlie/tradingview-mcp` — 라이브 인디 출력값 추출에 사용한 외부 도구 (MIT, 비제휴)
