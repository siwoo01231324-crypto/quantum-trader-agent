---
type: strategy
id: live-airborne-bb-reversal
name: Live Airborne BB Reversal (40% Retracement Mean-Reversion)
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
created: 2026-05-20
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: 182
backtest_period: 2025-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
profit_factor_bt: 0.912
expectancy_bt: -0.002230
verdict_5y: null
verdict_1y: "rejected: PF=0.912<1 best combo (3%/6% R/R), expectancy=-0.223%/trade<0 on 1y BTC+ETH 1h; R/R sweep (8 combos) all PF<1 (range 0.738-0.912). Marginally better than sibling live-mg-bb-reversal (PF 0.834) but still below gate."
summary_ko: |
  외부 강의의 비공개 인디케이터 "에어본(체험판)" 의 출력값을 역공학으로 도출한
  진입 수식의 전략화. 1시간봉 볼린저 밴드(20, 2σ) 상단/하단 돌파 후 극값 추적,
  극값 대비 40% 되돌림이 봉 확정 close 에서 발생하면 역방향 진입. 자매
  ``live-mg-bb-reversal`` 의 "캔들 패턴 게이트" 가 1y 16조합 모두 PF<1 로
  falsified 된 데 비해, 본 전략은 강사 본인이 실제로 사용하는 인디케이터의
  정확한 트리거 (40% 되돌림 + 확정 close) 를 검증한다. 게이트 자체가 다르므로
  자매 결과로부터 자동 기각하지 않는다.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- airborne
- external-lecture
- reverse-engineered
- rejected
- pattern:live-scanner
---

# Live Airborne BB Reversal

외부 강의 강사가 배포하는 비공개 Pine v5 인디케이터 "에어본(체험판)" 의 진입 수식을 출력값 관찰만으로 도출한 전략. 역공학 방법론과 정합 증거는 [[38-airborne-indicator-reverse-engineering]] 참조.

## 배경 — 자매 전략과의 구분

[[live-mg-bb-reversal]] 은 동일 강의의 **일반 서술(MG 기법)** 을 reformulation 하여:
- BB 하단 lookback 터치
- 밴드 안 reclaim
- 반전 캔들 패턴(engulfing/hammer)

3 게이트로 진입했고, 1y BTC+ETH (1m+15m, 16조합) 전부 **PF<1 (0.62~0.83) falsified**.

본 전략은 같은 강의 가족이지만 **인디케이터의 실제 수식** 을 쓴다:
- BB 돌파 직후가 아니라 **돌파 → 극값 추적 → 40% 되돌림** 의 3 단
- 캔들 패턴 게이트 없음, 대신 **수치적 되돌림 비율**
- 단일 timeframe (1H) 확정 close

→ **사전등록 가설**:
> "BB 돌파 후 극값으로부터의 40% 되돌림 확정 close 트리거가, 캔들 패턴 게이트 ([[live-mg-bb-reversal]]) 보다 통계적으로 더 나은(또는 동등 이상의) 엣지를 만드는가? 구체적으로 1y BTC+ETH 1h 데이터에서 PF>1 AND expectancy>0 을 통과하는가?"

게이트 구조 자체가 다르므로 자매 결과의 자동 외삽은 부정확. 하지만 BB 평균회귀 가족 전체(`live-bb-lower-bounce` PF=0.922, `live-mg-bb-reversal` PF≤0.83)가 음의 엣지였다는 base rate 는 본 전략의 통과 확률을 낮게 본다는 prior 로 작용한다.

## 진입 규칙

상태 머신:

```
없음 ─┬─→ 숏 대기 ─→ 에어본 숏(매도)
      └─→ 롱 대기 ─→ 에어본 롱(매수)
```

본 프로젝트는 long-only (`backtest/strategies/.ai.md` §규칙) 이므로 **롱 setup 만 거래 신호로 발행**한다. 숏 setup 은 추적·통계 수집용으로만 계산.

### 상태 변수

각 봉 확정 close 시점 기준:

```
bb_upper, bb_mid, bb_lower  = BollingerBands(close, window=20, std=2.0)
```

### 전이 규칙

| 전이 | 조건 |
|---|---|
| 없음 → 숏 대기 | `high[-1] ≥ bb_upper[-1]` AND `high[-2] < bb_upper[-2]` (HIGH 의 BB 상단 상향 돌파) |
| 없음 → 롱 대기 | `low[-1] ≤ bb_lower[-1]` AND `low[-2] > bb_lower[-2]` (LOW 의 BB 하단 하향 돌파) |
| 숏 대기 → 에어본 숏 | 다음 봉 이후 `close[-1] ≤ trigger` |
| 롱 대기 → 에어본 롱 | 다음 봉 이후 `close[-1] ≥ trigger` (← **매수 신호**) |

### 상태 진입 시 초기화

```
base    = breakout_bar.close          # 다음 봉 open 과 같음
extreme = breakout_bar.high (숏) or .low (롱)
breakout_bar_index = i
```

### 상태 유지 중 매 봉 갱신

숏 setup:
```
extreme = max(extreme, bar.high)
trigger = extreme - 0.4 * (extreme - base)
```

롱 setup (대칭):
```
extreme = min(extreme, bar.low)
trigger = extreme + 0.4 * (base - extreme)
```

### 발화 제약

- **인트라바 발화 금지**. 봉 확정 close 만 평가 — 라이브 차트에서 인디케이터가 실제로 그렇게 동작함을 확인 ([[38-airborne-indicator-reverse-engineering]] §2.6).
- **breakout 같은 봉 발화 금지**. `i > breakout_bar_index` 일 때만 트리거 평가.

### 미구현/연구 필요 항목 (본 spec v1 범위 밖)

본 spec v1 은 위 핵심 규칙만 구현. 다음 항목은 [[38-airborne-indicator-reverse-engineering]] §6 의 후속 검증 작업으로 분리하며, v1 백테스트 후 결과에 따라 v2 추가 검토:

- **LTF (5분) BB 의 필터 역할**: 인디케이터는 `request.security` 로 5분 BB 를 별도 출력하지만 발화 게이트 관여 여부 미확정. v1 은 **5분 BB 미사용**.
- **상태 색상(🔴/🟡/🟢) 의 의미**: 신뢰도 등급 추정, 미확정. v1 은 색상 무시.
- **재진입/쿨다운**: 신호 발화 직후 즉시 새 setup 받는지 미확정. v1 은 **발화 후 즉시 state=None 으로 리셋**.
- **숏 진입**: 본 프로젝트 long-only 정책으로 미구현.

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager` 책임 (live-scanner 패러다임 공통):
- `stop_loss_pct = 0.03`
- `take_profit_pct = 0.06`
- `trailing_stop_pct = null`

손익비 1:2 — 자매 live-scanner 들과 동일 기본값. 사전등록 가설 검증을 위해 진입 신호 자체 차이만 본다 (R/R 조합 sweep 은 검증 사이클 1차 통과 후 진행).

## 리스크 연동

```python
from src.backtest.strategies.live_airborne_bb_reversal import LiveAirborneBbReversal

orch.register_strategy("live_airborne_bb_reversal", LiveAirborneBbReversal())
orch.register_strategy_returns("live_airborne_bb_reversal", daily_returns_series)
orch.refresh_portfolio_risk()
```

## 1y 검증 결과 (2026-05-20)

**REJECTED.** R/R sweep 8 조합 모두 PF<1, expectancy<0.

조건: 1y (2025-05-19 ~ 2026-05-19) · BTCUSDT+ETHUSDT 1h (Binance USDT-perp 1m 캐시 → 1h 리샘플) · 라운드트립 10bp · `scripts/bench_live_airborne_quick.py --freq 1h --sweep-rr`.

### 결과 표 (PF 내림차순)

| R/R | trades | win% | payoff | PF | exp/trade | verdict |
|---|---:|---:|---:|---:|---:|---|
| 3.0/6.0 (1:2) ← spec default | 182 | 35.71 | 1.64 | **0.912** | -0.223% | LOSER |
| 1.5/3.0 (1:2) | 355 | 38.31 | 1.44 | 0.892 | -0.158% | LOSER |
| 1.0/3.0 (1:3) | 431 | 31.32 | 1.93 | 0.880 | -0.144% | LOSER |
| 2.0/6.0 (1:3) | 236 | 27.97 | 2.27 | 0.880 | -0.245% | LOSER |
| 2.0/4.0 (1:2) | 281 | 35.59 | 1.58 | 0.871 | -0.234% | LOSER |
| 1.0/2.0 (1:2) | 480 | 38.33 | 1.36 | 0.843 | -0.166% | LOSER |
| 0.5/1.0 (1:2) | 669 | 42.00 | 1.04 | 0.755 | -0.166% | LOSER |
| 0.5/2.0 (1:4) | 594 | 27.44 | 1.95 | 0.738 | -0.226% | LOSER |

### 판정 근거

- **PF 0.912 가 천장**. 어떤 R/R 조합도 PF>1 못 넘김.
- **사전등록 가설 falsified**: "BB 돌파 + 40% 되돌림 + 봉 확정 close 의 수치적 게이트가 캔들 패턴 게이트 ([[live-mg-bb-reversal]]) 보다 통계적으로 더 나은 엣지를 만든다" — 우리 비교는 **40%-retrace PF=0.912 > 캔들-게이트 PF=0.834** 로 *방향상* 우월하지만 **둘 다 PF<1 영역**에 있다. 더 나은 진입 신호로는 부족하고, PF<1 가족 안에서의 순위 차이일 뿐. 게이트 통과 못함.
- **승률 × payoff 모두 부족**. 가령 3.0/6.0 (1:2) 에서 승률 35.71% 라면 손익분기점 payoff ≈ 1/0.357−1 = 1.80 필요한데 실제 1.64. payoff 끌어올리려 1:3 으로 가도 (2.27) 승률이 27.97% 로 떨어져 PF 0.880 으로 천장 도달.
- 자매와의 직접 비교 (모두 동일 cost=10bp 가정):

  | 전략 | 최고 PF | freq | 게이트 |
  |---|---:|---|---|
  | `live-bb-lower-bounce` (5y) | 0.922 | 1m | naive reclaim + volume MA |
  | **`live-airborne-bb-reversal` (1y)** | **0.912** | **1h** | **BB break + 40% retrace** |
  | `live-mg-bb-reversal` (1y) | 0.834 | 1m | BB break + reclaim + 캔들 패턴 |
  | `live-mg-bb-reversal` (1y) | 0.831 | 15m | (동일) |

  같은 BB-평균회귀 가족 안에서 게이트 정교화가 PF 를 0.83 → 0.91 → 0.92 로 점진적으로 끌어올리지만 **모두 PF=1 미만**. 가족 자체의 엣지 부재가 강하게 시사된다.

### 재활성화 조건

이 spec 은 잠긴다. 재활성화는:
1. **진입 신호 *재설계*** — BB 평균회귀 가족 안에서 게이트 추가/교체로는 PF<1 못 넘김이 sweep 으로 증명됨. 완전히 다른 컨텍스트 필터 (예: 다중 TF 추세 정렬, 변동성 레짐, 펀딩비, 다이버전스 동반 확인) 가 필요.
2. **5y 게이트 통과** — `scripts/eval_live_scanners_5y.py` 동등 조건에서 **PF > 1.0 AND expectancy > 0**.
3. 후에 spec 새로 작성 (이 spec 은 historical record 로 보존).

원자료: `scripts/bench_live_airborne_quick.py --freq 1h --months 12 --sweep-rr` 의 stdout (이 결과는 reports/ 미저장 — 실패 결과라 노이즈 회피).

## LTF (5분) BB 역할 — 단일 스냅샷 추론

[[38-airborne-indicator-reverse-engineering]] §2 의 스냅샷 정합 검증에서 다음이 확정됨:

- **트리거 공식은 HTF (=차트 TF 1H) BB 와 base/extreme 만으로 ±0.2 tick 내 정확히 재현된다**.
- 만약 LTF BB 가 트리거 가격 산정에 관여했다면 위 정합이 깨졌어야 한다. → **LTF BB 는 트리거 가격 산정에 사용되지 않음**.

남은 가능성:
- (a) LTF BB 는 **차트 표시 전용** (사용자가 분 단위 변동성을 시각적으로 파악하도록 보조).
- (b) LTF BB 가 발화 **게이트** 일 가능성 (예: "트리거가 ≥ LTF BB 하단" 등의 조건). 본 1y 백테스트는 LTF 미사용으로 진행됐고 PF=0.912 천장. LTF 게이트를 추가했을 때 PF 가 의미 있게 개선될지는 추가 실험 필요하지만, **가족 천장이 0.92 인 점을 감안하면 LTF 게이트만으로 PF>1 진입은 통계적으로 매우 어렵다**.

본 spec v1 은 LTF BB 미사용으로 결정되었고, 결과가 rejected 이므로 LTF 추가 실험은 §"재활성화 조건" 의 "진입 신호 재설계" 범주로 흡수된다 (LTF 게이트 단독은 진입 신호 재설계로 간주하지 않음).

## 5y 검증 (보류)

게이트 1 (1y) 미통과로 5y 진행 안 함.

## production 등록 (영구 lock)

❌ `production.yaml` 미등록. 재활성화 조건 충족 전까지 lock.

## v1.1 close-기반 게이트 + 1/10 R/R 양방향 30sym sweep (2026-05-21)

사용자가 라이브 차트 비교에서 v1 의 "wick-only 돌파" 까지 잡는 점 지적 → v1.1 로 close 기반 + 마진 0.1% + body 0.5% 게이트 추가. 그 후 단타 시뮬 (양방향, 30 USDT-perp 심볼, 1h 신호 → 5m 청산) 로 7개 cost/R/R 조합 sweep.

### 결과 표 (1y BTCUSDT+others 30 sym)

| Sweep | Cost | R/R scale | 최고 PF | PASS | 최고 조합 |
|---|---|---|---:|---|---|
| 1 | 4bp (taker) | 1/10 | 0.677 | 0/8 | 0.30/0.60% |
| 2 | 2bp (maker) | 1/10 | 0.815 | 0/8 | 0.30/0.60% |
| 3 | 4bp | 1/5 | 0.781 | 0/8 | 0.60/1.20% |
| **4** | **0bp (ideal)** | **1/10** | **1.013** | **1/8 borderline** | **0.20/0.60%** |
| 5 | 4bp | 1/2 | 0.820 | 0/8 | 1.0/2.0% |
| 6 | 4bp | full | 0.820 | 0/8 | 1.0/2.0% |
| **7** | **0bp (ideal)** | **1/2** | **1.020** | **1/8 borderline** | **0.25/0.50%** |

### 결정적 결론

- **모든 현실적 비용 (2~4bp) 에서 PF<1** — 어떤 R/R scale 도, 어떤 방향 (long/short/양방향) 도, 30 sym 어떤 universe 에서도 PASS 없음.
- **비용 0 (이상화) 에서만 borderline PF=1.013~1.020** — 단순히 비용 없으면 break-even 보더라인. 실효 알파 ≈ 0.
- **인디케이터 자체 알파 = 0 + 비용** — BB 평균회귀 가족의 음의 엣지가 v1.1 close-기반 게이트로도 해소되지 않음. 단타 R/R 에서 비용이 압도적.

### 가족 base rate 일관성 확인

| 전략 | 5y/1y PF | 게이트 |
|---|---|---|
| live-bb-lower-bounce | 0.922 (5y) | naive reclaim + volume MA |
| live-mg-bb-reversal | 0.834 (1y) | BB + 캔들 패턴 |
| live-airborne-bb-reversal v1 | 0.960 (5y) | high/low + 40% retrace |
| **v1.1 (close-기반)** | **<0.82 (1y, 모든 cost/R/R)** | **close + margin + body + 40% retrace** |
| v2 (+trend) | 0.913 (5y) | + trend SMA(50) |
| v3 (+trend+vol) | 1.012 (5y, borderline) | + volume MA |

→ **BB 평균회귀 가족 전체가 음의 엣지** 확정. 진입 신호의 재설계 (단순 변형 아닌 본질적 변경) 없이는 PF>1 진입 불가.

### v1.1 의 가치 — 알파 아닌 *재현 정확도*

사용자 차트 시각 비교 결과: **v1.1 이 원본 에어본(체험판) 인디케이터의 신호 위치와 가장 가깝게 일치**. v2/v3 의 추세/거래량 게이트는 알파 측면이 아닌 *재현 측면* 에서 원본과 멀어짐 (원본은 그런 게이트 없음).

**결론**: v1.1 = **원본 인디케이터의 충실한 재현본**. 시각적 거래 가이드로 사용 가능하나 자동 매매 알파 없음. 차트에서 인디 사용 시 *사용자 본인의 추세 판단* 으로 밴드라이딩 회피 필요. 본 spec 의 frontmatter `status: rejected` 는 *알파 측면의 판정* 이며 *시각 인디로서의 가치* 와 별개.

### 단타 시뮬 한계

- **신호 1h, 청산 5m** 모델은 봉 안 stop/tp 동시 도달 시 stop 우선 처리 — 보수적 가정. 실제 매매에서는 entry candle path 에 따라 결과 다를 수 있음.
- 비용 0bp 시뮬은 알파 측정용으로만 의미 — 실거래 불가능.

원자료: `scripts/bench_live_airborne_v11_5m_exit_v2.py` 실행 로그 (reports/ 미저장 — 모두 LOSER).


## 운영 규칙

- **LLM 호출 금지** (불변식 #6)
- `LIVE_SCANNER_ENABLED=1` + `production.yaml::enabled=true` 양쪽 ON 일 때만 라이브 dispatch. 현재는 **production.yaml 미등록** — 의도적 게이팅 (status=draft).
- 단위 테스트 (구현 시): `tests/backtest/test_live_airborne_bb_reversal.py`. synthetic OHLCV 로 다음 시나리오 검증:
  - BB 상단 돌파 → 숏 setup 진입 (signal 미발행, long-only)
  - BB 하단 돌파 → 롱 setup 진입
  - 극값 갱신 시 trigger 재계산
  - 다음 봉 close 가 trigger 이상 → buy signal 발행
  - 같은 봉에서 발화 시도 → 미발행 (breakout 봉 제외)
  - 인트라바 close 가 trigger 도달 후 finalize 전 → 미발행

## 인디케이터 원본과의 의도적 차이

| 인디케이터 (라이브 관찰) | 본 구현 v1 | 이유 |
|---|---|---|
| HTF BB + LTF (5분) BB 동시 사용 | HTF BB only (1h 차트 = 1h BB) | LTF 의 필터 역할 미확정 — v2 검토 |
| `🔴/🟡/🟢` 상태 색상 출력 | 무시 | 의미 미확정 |
| 양방향 (롱 + 숏) | long-only | 본 프로젝트 MVP 룰 |
| 단일 종목 차트 (사용자는 BITMEX:BTCUSD.P) | BINANCE_USDT_PERP_UNIVERSE | live-scanner 패러다임 = universe 검색 |
| 작성자 자체 익절/손절 표시 (관찰 안 됨) | live-scanner 공통 3%/6% | 인디케이터 차원의 출구 미관찰 |

## 코드 위치 (예정)

| 파일 | 역할 |
|---|---|
| `src/backtest/strategies/live_airborne_bb_reversal.py` | Strategy 클래스 (LiveScannerMixin 상속) |
| `src/signals/airborne_bb_reversal.py` | breakout / extreme / trigger 계산 함수 (재사용 가능) |
| `tests/backtest/test_live_airborne_bb_reversal.py` | 단위 테스트 |
| `scripts/bench_live_airborne_quick.py` | 1y 사전등록 sweep (또는 기존 `bench_live_mg_quick.py` 에 strategy id 추가) |

본 spec v1 작성 시점에 위 코드는 모두 미구현. 다음 작업 사이클의 입력.

## 관련

- [[38-airborne-indicator-reverse-engineering]] — 본 spec 의 수식 도출 근거 (반드시 먼저 읽을 것)
- [[live-mg-bb-reversal]] — 같은 강의의 일반 서술 reformulation, **1y rejected**
- [[live-bb-lower-bounce]] — BB 평균회귀 가족 단순 버전, **5y rejected**
- [[live-universe-scanner-paradigm]] — live-scanner 패러다임 spec
- `src/backtest/strategies/_live_scanner_helpers.py` — `LiveScannerMixin`
- `src/signals/bollinger.py` — `compute_bollinger`
- `external-trading-lecture-techniques.md` — 강의 원본 (repo-root)
