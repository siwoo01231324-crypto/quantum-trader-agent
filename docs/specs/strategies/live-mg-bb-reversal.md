---
type: strategy
id: live-mg-bb-reversal
name: Live MG Bollinger Band Reversal
status: rejected
paradigm: live-scanner
instruments:
- KRX_UNIVERSE
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 15m
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
trades_bt: 297
backtest_period: 2025-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
profit_factor_bt: 0.812
expectancy_bt: -0.004354
verdict_5y: null
verdict_1y: "rejected: PF=0.812<1, expectancy=-0.435%/trade<0 at default 3%/6% R/R on 1m bars; R/R sweep (8 combos × 2 freq) all PF<1 (range 0.62-0.83)"
summary_ko: |
  외부 강의 MG(Momentum Gap) 매매법의 핵심 진입 규칙을 단일 timeframe 로 압축한
  실험 전략. 직전 봉이 볼린저 하단을 터치/이탈하고, 당일 봉이 다시 밴드 안으로
  들어오면서 반전 캔들 패턴(bullish engulfing 또는 hammer) 이 출현하면 매수.
  사전등록 가설은 1y BTC+ETH 1m/15m sweep (8 R/R × 2 freq = 16조합) 에서 모두
  PF<1 로 falsified. ``live-bb-lower-bounce`` (PF=0.922) 의 volume-MA 게이트를
  캔들 구조 게이트로 교체해도 음의 엣지 해소되지 않음.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- candle-pattern
- rejected
- external-lecture
---

# Live MG Bollinger Band Reversal

외부 강의에서 가르친 **MG(Momentum Gap) 매매법** 의 진입 규칙을 본 프로젝트의
live-scanner 패러다임으로 이식한 실험 전략. 출처·원본 기법 정리는 repo-root
`external-trading-lecture-techniques.md` 참조.

## 배경 — 왜 또 BB 평균회귀인가

기존 `live-bb-lower-bounce` 는 동일한 BB 하단 평균회귀 가설에서 출발했지만 5y
bench 에서 **REJECTED** 됐다 (PF=0.922, 거래당 기대값 -0.179%, 47,034 거래).
실패의 가설된 원인: "단순 reclaim → buy" 가 추세 끝과 추세 한가운데
밴드라이딩을 구분 못 함 → false bounce 누적 → 음의 엣지.

본 전략의 사전등록 가설:
> **"BB 하단 터치 + 반전 캔들 패턴 + reclaim" 의 3중 게이트가 단순 reclaim 의
> false bounce 를 통계적으로 유의하게 걸러내는가?"**

강의의 핵심 메시지 ("캔들은 매수·매도 심리의 결과", "추세 끝에서만 유효") 가
실제 데이터에서도 PF>1 / exp>0 을 만들어내는지 본다.

## 진입 규칙

3 게이트 모두 통과 시 매수:

**Gate 1 — 지속 dip (band touch)**
- 직전 `DIP_LOOKBACK=2` 봉 ([-2, -3]) 중 어느 한 봉이라도
  `low <= bb_lower` 였으면 통과.
- 강의의 "4h 이탈 → 대기" 두 단계 진입을 단일 timeframe 안에서 근사.

**Gate 2 — 밴드 안 reclaim (밴드라이딩 회피)**
- `bb_lower[-1] < close[-1] < bb_upper[-1]` — 당일 봉이 다시 밴드 안으로 들어와
  있고, 상단을 뚫지도 않음.

**Gate 3 — 반전 캔들 패턴 on 당일 봉**
- **Bullish Engulfing**: 직전 봉 약세 (`close[-2] < open[-2]`) +
  당일 봉 강세 (`close[-1] > open[-1]`) + 당일 봉 body 가 직전 봉 body 를 완전
  포함 (`open[-1] <= close[-2]` AND `close[-1] >= open[-2]`).
- **OR Hammer**: 당일 봉 body > 0, 아래꼬리 >= 2× body, 위꼬리 <= body.
- 둘 중 하나라도 충족하면 통과.

볼린저 파라미터: `BB_WINDOW=20, BB_STD=2.0` (기존 BB 전략과 동일).
워밍업 최소: `MIN_HISTORY=24` 봉.

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager`
책임 (live-scanner 패러다임 공통 계약):
- `stop_loss_pct = 0.03`
- `take_profit_pct = 0.06`
- `trailing_stop_pct = null`

손익비 약 1:2 — 기존 live-scanner 5 종과 동일 기본값.

## 리스크 연동

```python
from src.backtest.strategies.live_mg_bb_reversal import LiveMgBbReversal

orch.register_strategy("live_mg_bb_reversal", LiveMgBbReversal())
orch.register_strategy_returns("live_mg_bb_reversal", daily_returns_series)
orch.refresh_portfolio_risk()
```

## 1y 검증 결과 (2026-05-20)

**REJECTED.** R/R sweep × 2 freq, 16 조합 모두 PF<1.

조건: 1y (2025-05-19 ~ 2026-05-19) · BTCUSDT+ETHUSDT 1m 캐시 (Binance USDT-perp)
· 라운드트립 비용 10bp · ``scripts/bench_live_mg_quick.py``.

### 1m bars

| R/R | trades | win% | payoff | PF | exp/trade |
|---|---:|---:|---:|---:|---:|
| 2.0/6.0 (1:3) | 429 | 24.9 | 2.51 | **0.834** | -0.295% |
| 3.0/6.0 (1:2) ← class default | 297 | 31.7 | 1.75 | 0.812 | -0.435% |
| 1.5/3.0 (1:2) | 1,009 | 33.5 | 1.61 | 0.810 | -0.230% |
| 2.0/4.0 (1:2) | 602 | 32.2 | 1.68 | 0.797 | -0.324% |
| 1.0/3.0 (1:3) | 1,421 | 26.1 | 2.20 | 0.777 | -0.219% |
| 1.0/2.0 (1:2) ← 강의 default | 1,917 | 34.1 | 1.46 | 0.756 | -0.213% |
| 0.5/2.0 (1:4) | 3,211 | 22.1 | 2.39 | 0.680 | -0.200% |
| 0.5/1.0 (1:2) | 4,933 | 35.5 | 1.13 | 0.619 | -0.195% |

### 15m bars

| R/R | trades | win% | payoff | PF | exp/trade |
|---|---:|---:|---:|---:|---:|
| 1.5/3.0 (1:2) | 517 | 35.0 | 1.54 | **0.831** | -0.226% |
| 3.0/6.0 (1:2) | 228 | 32.5 | 1.70 | 0.817 | -0.447% |
| 2.0/6.0 (1:3) | 313 | 24.6 | 2.42 | 0.789 | -0.406% |
| 1.0/3.0 (1:3) | 661 | 27.2 | 2.04 | 0.762 | -0.267% |
| 2.0/4.0 (1:2) | 388 | 31.4 | 1.66 | 0.759 | -0.421% |
| 1.0/2.0 (1:2) | 770 | 34.7 | 1.40 | 0.744 | -0.254% |
| 0.5/2.0 (1:4) | 1,014 | 24.4 | 2.16 | 0.695 | -0.224% |
| 0.5/1.0 (1:2) | 1,179 | 37.5 | 1.08 | 0.645 | -0.211% |

### 판정 근거

- **PF 0.83 이 천장**. 어떤 R/R 조합도, 어떤 freq 도 PF>1 못 넘김.
- 사전등록 가설 **falsified**: "캔들 패턴(engulfing/hammer) 게이트가 자매 ``live_bb_lower_bounce`` (PF=0.922) 의 false bounce 결함을 해소한다" — 본 결과는 같은 결함이 그대로 유지됨을 보임. 캔들 구조 필터는 통계적 엣지를 만들지 못함.
- 1m / 15m 가 거의 동일한 PF 범위 (0.62~0.83) → 1h 봉 이산화 인공물은 주범 아님. 음의 엣지는 진입 신호 자체의 문제.
- **승률 × payoff 모두 부족**. 가령 1m 1:2 R/R 에서 승률 34% 라면 손익분기점 payoff ≈ 1/(0.34)−1 = 1.94 필요한데 실제 1.46. 1:3 으로 payoff 늘려도 (2.20) 승률이 26% 로 떨어져 상쇄.

### 재활성화 조건

이 spec 은 잠긴다. 재활성화는:
1. **진입 신호 재설계** — stop/TP 튜닝으로는 PF<1 못 넘김이 sweep 으로 증명됨 (sibling ``live_bb_lower_bounce`` 의 verdict 와 동일 결론). 캔들 패턴이 아닌 다른 컨텍스트 필터 (예: 다중 TF 추세 정렬, 거래량 z-score, 다이버전스 동반 확인) 가 필요.
2. **5y full bench 게이트 통과** — ``scripts/eval_live_scanners_5y.py`` 동등 조건에서 **PF > 1.0 AND expectancy > 0**.
3. 후에 spec 새로 작성 (이 spec 은 historical record 로 보존).

원자료: ``scripts/bench_live_mg_quick.py`` 실행 로그 (이 결과는 reports/ 미저장 — 실패 결과라 노이즈 회피).

## 운영 규칙

- **LLM 호출 금지** (불변식 #6).
- `LIVE_SCANNER_ENABLED=1` + `production.yaml` `enabled: true` 양쪽 ON 일 때만
  라이브 dispatch. 현재는 **production.yaml 미등록** — 의도적 게이팅.
- 5y bench 결과가 PF>1 / exp>0 통과 시:
  1. spec `status: active`, frontmatter `sharpe_bt`/`mdd_bt`/`annual_return_bt`/
     `trades_bt`/`backtest_period`/`profit_factor_bt`/`expectancy_bt`/`verdict_5y`
     필드 채움.
  2. `production.yaml` 에 commented entry 추가 후 PR 리뷰.
  3. 6 → 12 개월 paper 운영 후에만 자본 배분.
- 단위 테스트: `tests/backtest/test_live_mg_bb_reversal.py`.

## 강의 원본과의 의도적 차이

| 강의 원본 | 본 구현 | 이유 |
|---|---|---|
| 4h 이탈 + 15m 캔들 confirmation (2 timeframe) | 단일 timeframe 안에서 lookback 윈도우로 근사 | live-scanner 패러다임이 단일 freq snapshot 기준 — 멀티 TF 구현 시 별도 spec 필요 |
| 80% / 20% 분할 익절 (2 target) | 단일 6% take-profit | `LivePositionRiskManager` 가 현재 단일 TP 만 지원, 멀티 TP 는 별 이슈 |
| 양방향 (롱+숏) | long-only | 프로젝트 MVP 룰 (`backtest/strategies/.ai.md` §규칙) |
| 강사 자체 "에어본 지표" | 직접 미사용 | 비공개 코드; 같은 시그널을 `signals.compute("bollinger", …)` 로 직접 산출 |
| 풀시드/박스 베팅 등 사이징 | `default_size=0.05` (5%) + risk DSL | 본 프로젝트 risk policy (`max_leverage: 1.0`, per-position cap) 준수 |

## 관련

- `external-trading-lecture-techniques.md` — 강의 원본 기법 정리 (repo-root)
- [[live-bb-lower-bounce]] — REJECTED 된 자매 전략 (본 전략의 대조군)
- [[live-universe-scanner-paradigm]] — 패러다임 spec
- `src/backtest/strategies/_live_scanner_helpers.py` — `LiveScannerMixin`
- `src/signals/bollinger.py` — `compute_bollinger`
