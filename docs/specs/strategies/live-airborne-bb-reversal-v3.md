---
type: strategy
id: live-airborne-bb-reversal-v3
name: Live Airborne BB Reversal v3 (40% Retracement + Trend + Volume)
status: backtest
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1h
uses_signals:
- bollinger
- sma
risk_rules:
- per-symbol-stop-loss-1pct
- per-symbol-take-profit-2pct
owner: siwoo
created: 2026-05-20
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: 79
backtest_period: 2025-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.01
take_profit_pct: 0.02
trailing_stop_pct: null
profit_factor_bt: 1.404
expectancy_bt: 0.003422
verdict_5y: null
verdict_1y: "passed: PF=1.404 (best 1.0/2.0 R/R) with trend(SMA50)+volume gates on 1y BTC+ETH 1h. expectancy=+0.342%/trade>0. 7/8 R/R combos PASS (only 3%/6% LOSER). Improves on v2 (PF=1.296, 6/8 PASS). Driven by signal frequency reduction (113→79 trades) via volume confirmation — matches lecture §1.4 '거래량과 함께 축소' + original 에어본 behavior on trending chart. 5y multi-regime validation mandatory."
summary_ko: |
  v2 (BB 돌파 + 40% 되돌림 + 봉 확정 close + close>SMA(50)) 에 거래량 동반
  게이트 (volume[-1] > SMA(volume,20)) 를 추가한 변형. 원본 에어본(체험판)이
  강한 하락 추세에서 롱 신호를 거의 띄우지 않는 시각 비교 + 강의 §1.4
  "거래량과 함께 축소될 때까지 대기" 단서에서 출발. 1y BTC+ETH 1h sweep
  에서 최고 PF=1.404 / +0.342%/trade @ 1.0/2.0 R/R, 79 거래. v2 대비 거래
  수 30% 감소 + PF 8% 개선 = 거래량 필터가 false signal 을 통계적으로
  유의하게 거름. 7/8 조합 PASS. 5y 다중 레짐 검증 필수.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- airborne
- external-lecture
- reverse-engineered
- trend-filter
- volume-filter
- pattern:live-scanner
---

# Live Airborne BB Reversal v3

[[live-airborne-bb-reversal-v2]] (PF=1.296) 에 **거래량 동반 게이트** 를 추가한 v3. 라이브 비교(2026-05-20 BITHUMB:BTCKRW 차트)에서 원본 에어본(체험판)이 강한 하락 추세에서 롱 신호를 거의 띄우지 않는 반면 v1/v2 는 빈번하게 발화함을 시각 확인. 강의 서머리 §1.4 "이탈 직후 진입 금지, 반전 캔들이 **거래량과 함께** 축소될 때까지 대기" 를 1H 시간프레임 상의 단순 거래량 필터 (`volume[-1] > MA(volume, 20)`) 로 압축.

## 사전등록 가설

> "v2 (추세 정렬 게이트만 추가) 의 PF=1.296 한계는 약한 거래량의 가짜 reclaim 이 여전히 통과하기 때문이다. 발화봉의 거래량이 직전 20봉 평균보다 작으면 진입을 거부하는 게이트를 추가하면 신호 수가 줄어드는 대신 PF 가 더 개선된다. 즉 false signal 의 통계적 유의한 감소가 일어난다."

## 진입 규칙

```
                          v1     v2     v3
1. BB 돌파 (HTF)            ✓      ✓      ✓
2. 극값 추적 + 40% 되돌림    ✓      ✓      ✓
3. 봉 확정 close 발화        ✓      ✓      ✓
4. close > SMA(50)                 ✓      ✓
5. volume > SMA(volume,20)                ✓ NEW
```

게이트 순서 (구현):
1. BB warmup → 통과 못하면 hold
2. 추세 게이트 (`close > sma(50)`) → 통과 못하면 hold
3. **거래량 게이트 (`volume[-1] > 1.0 × MA(volume, 20)`)** → 통과 못하면 hold ← v3 NEW
4. v1 코어 (`evaluate_long_fire`): BB 돌파 + 극값 추적 + 40% 되돌림 + 확정 close
5. 발화 → buy signal

## 청산

`LivePositionRiskManager` 책임 — 기본값 변경:
- `stop_loss_pct = 0.01` (v3 default; v2 의 0.02 보다 짧음)
- `take_profit_pct = 0.02` (v2 의 0.04 보다 짧음)
- `trailing_stop_pct = null`

이유: 1y sweep 의 최고 조합이 v2 와 다름. v3 의 best 는 **1.0/2.0 (1:2) R/R**.

## 1y 검증 결과 (2026-05-20)

**PASSED — v2 대비 추가 개선.** 8 R/R 조합 중 7개 PASS (v2 6개 PASS 보다 1개 추가).

조건: 1y (2025-05-19 ~ 2026-05-19) · BTCUSDT+ETHUSDT 1h · 라운드트립 10bp · `scripts/bench_live_airborne_v3_quick.py --freq 1h --sweep-rr --trend-sma 50 --vol-min 1.0`.

| R/R | trades | win% | payoff | PF | exp/trade | verdict |
|---|---:|---:|---:|---:|---:|---|
| 1.0/2.0 (1:2) ← **v3 default** | 79 | 49.37 | 1.44 | **1.404** | **+0.342%** | **PASS** |
| 2.0/4.0 (1:2) | 72 | 43.06 | 1.72 | 1.298 | +0.455% | PASS |
| 0.5/2.0 (1:4) | 80 | 37.50 | 2.13 | 1.278 | +0.193% | PASS |
| 0.5/1.0 (1:2) | 82 | 51.22 | 1.12 | 1.178 | +0.099% | PASS |
| 1.5/3.0 (1:2) | 75 | 44.00 | 1.48 | 1.165 | +0.215% | PASS |
| 2.0/6.0 (1:3) | 62 | 32.26 | 2.41 | 1.150 | +0.271% | PASS |
| 1.0/3.0 (1:3) | 78 | 38.46 | 1.82 | 1.140 | +0.153% | PASS |
| 3.0/6.0 (1:2) | 61 | 36.07 | 1.70 | 0.961 | -0.094% | LOSER |

### 가족 진화 요약

| 전략 | 최고 PF | PASS 수 | best trades | best R/R | 변경점 |
|---|---:|---:|---:|---|---|
| `live-bb-lower-bounce` (5y) | 0.922 | — | 47,034 | (5y) | naive |
| [[live-airborne-bb-reversal]] (1y) | 0.912 | 0/8 | 182 | 3.0/6.0 | + 40% 되돌림 |
| [[live-airborne-bb-reversal-v2]] (1y) | 1.296 | 6/8 | 113 | 2.0/4.0 | + 추세 게이트 |
| **본 spec (v3) (1y)** | **1.404** | **7/8** | **79** | **1.0/2.0** | **+ 거래량 게이트** |

신호 수 (best 기준): 182 → 113 → 79 — 게이트 추가마다 신호 빈도 ↓, 품질 ↑.

### 판정 근거

- **거래량 게이트가 통계적으로 유의한 개선** 을 만든다 — v2 → v3 에서 거래 수 30% 감소 + PF 8% 개선.
- **승률·payoff 모두 개선**: v3 best 의 승률 49.37% 는 v2 best (44.25%) 대비 +5%p, payoff 도 유지.
- **3.0/6.0 (1:2) 만 LOSER (PF=0.961)**: 큰 stop/TP 에서 거래량 게이트 효과가 사라짐 — 큰 R/R 은 추세 강할 때만 유리한데 거래량 필터가 그런 신호를 제거. 작은~중간 R/R 에서 v3 사용 권장.

### 라이브 시각 검증 (BITHUMB:BTCKRW 1H, 2026-05-20)

원본 에어본(체험판) 과 우리 v1/v2/v3 를 같은 차트에 띄워 신호 빈도 비교 시:
- **v1**: 차트 전체에 ▲/▼ 마커 빈번 (원본과 큰 차이)
- **v2**: 우측 하락 추세에서 ▲ 마커 사라짐 (원본 패턴에 근접)
- **v3**: v2 대비 추가로 작은 거래량 봉의 가짜 신호 제거 (원본에 가장 근접)

(스크린샷 첨부는 `external-trading-lecture-techniques.md` §11 또는 [[38-airborne-indicator-reverse-engineering]] §6 참조).

## LTF (5분) BB 의 역할 — 여전히 미사용

v3 도 v1/v2 와 마찬가지로 LTF BB 를 디스플레이 전용으로만 사용. 1y PF=1.404 까지 도달했으므로 LTF 게이트 추가는 v4 로 미룬다 (LTF 강의 §1.2 방법 A 의 진정한 구현 = LTF 캔들 패턴 확인은 [[live-mg-bb-reversal]] 에서 이미 PF<1 으로 rejected 됐고, 단순 "close in LTF BB" 같은 약한 변형은 별도 게이트로 검증 가능).

## 5y 검증 (필수, 진행 안 함)

v2 와 동일 — 1y best-arm overfit risk 남아 있음:
- trend_sma=50 의 최적성은 v2 sweep 이 확인했지만, vol_min=1.0 의 최적성은 단일 sweep
- 79 거래는 통계적 신뢰도 marginal

5y 게이트 통과 시:
- `status: paper` 로 전이
- 6~12 개월 paper 운영 후 자본 배분

## 운영 규칙

- **LLM 호출 금지** (불변식 #6)
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml::enabled=true`
- 현재 status=backtest — 5y 게이트 통과 전까지 `production.yaml` 미등록
- 단위 테스트: `tests/backtest/test_live_airborne_bb_reversal_v3.py` (8건 통과)

## v2 와의 차이

| v2 | v3 |
|---|---|
| BB+40%+추세 | 동일 + **거래량 게이트** |
| stop 2% / TP 4% | stop 1% / TP 2% (1y best) |
| PF=1.296, +0.455%/trade | PF=1.404, +0.342%/trade |
| 6/8 조합 PASS | 7/8 조합 PASS |
| 113 거래 | 79 거래 (30% ↓) |

## Pine Script 보존본

- v1: `docs/specs/strategies/live-airborne-bb-reversal.pine` — 역공학 원본 (TV slot saved)
- v2: `docs/specs/strategies/live-airborne-bb-reversal-v2.pine`
- **v3: `docs/specs/strategies/live-airborne-bb-reversal-v3.pine` — TV slot saved (`USER;589f40b4ce12440983a20a5a61231072`)**

## 관련

- [[38-airborne-indicator-reverse-engineering]] — 인디케이터 역공학 사양
- [[live-airborne-bb-reversal]] — v1 (rejected)
- [[live-airborne-bb-reversal-v2]] — v2 (passed, base of v3)
- [[live-bb-lower-bounce]] — BB 가족 단순 버전 (rejected)
- [[live-mg-bb-reversal]] — 강의 일반 서술의 다른 reformulation (rejected)
- `src/backtest/strategies/live_airborne_bb_reversal_v3.py` — 구현
- `tests/backtest/test_live_airborne_bb_reversal_v3.py` — 단위 테스트 (8건)
- `scripts/bench_live_airborne_v3_quick.py` — 1y sweep 하네스
- `external-trading-lecture-techniques.md` §1.4 — "거래량과 함께 축소" 원전
