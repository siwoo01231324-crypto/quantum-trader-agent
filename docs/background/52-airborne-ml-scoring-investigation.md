---
type: research
id: 52-airborne-ml-scoring-investigation
name: Airborne Fire ML Scoring Investigation
sources:
  - scripts/_airborne_ml_edge_probe.py
  - scripts/_airborne_ml_edge_probe_1m.py
  - scripts/_airborne_ml_walkforward.py
  - scripts/_airborne_reconstruct_fires.py
  - scripts/_airborne_synth_features_v3.py
  - logs/airborne_fires/sim_cache_2pct.jsonl
  - "[[38-airborne-indicator-reverse-engineering]]"
---

# Airborne Fire ML Scoring Investigation

## 가설

에어본 BB-역추세 신호(fire)는 5y 기준 랜덤과 동일(`project_airborne_signal_equals_random_5y`).
그래도 fire **사이**에 좋은/나쁜 fire 를 가르는 **조건부 엣지**가 있어, fire 직전 차트 모양을
ML/DL 로 학습해 상위 점수만 진입하면 비용 벽을 넘을 수 있는가? (TP +2% / SL −1% 룰 기준)

## 방법

- **라벨**: `sim_cache_2pct.jsonl` — fire 이후 15m봉으로 TP+2%/SL−1% 4봉 판정.
- **피처(누수 0, fire 시점 이전만)**: 메타(side·트리거이탈·가격대·시각) + fire 직전 60분 1m봉 미시구조
  (모멘텀·변동성·거래량스파이크·범위내위치·캔들 윗꼬리/몸통·pre-MFE) + 1h 컨텍스트(EMA20/50/200·BB·
  ATR·전고점/전저점 Donchian) + BTC 레짐 + 캔들패턴(망치형·도지·인걸핑) + 섹터(L1/밈/AI/토큰화주식/원자재).
- **검증 게이트**: 시간순 OOS 상위 20% 점수 부분집합의 정직비용(10bp) 후 기대값이 같은 수의 랜덤선택을
  유의하게(z>2) 이기는가 — **다중레짐 확장윈도우 walk-forward** (단일분할 z 과신 금지).
- **데이터 확장**: 실제 fire 37일(3.6k)로는 레짐 분리 불가 → 에어본 v1.1 탐지기를 2년 1h봉 45종목에
  재구성(forward state-machine)해 **17,887 synth fire(2024~2026)** 합성, 각 fire 1m 윈도우 fetch.

## 결과 — 기각

| 단계 | 데이터 | walk-forward (top20%, z>2 폴드) |
|---|---|---|
| 메타전용 | 실제 37일 | 단일분할 z1.4 → walk-forward 1/4 |
| 메타+1m | 실제 37일 | 2/4 (레짐의존, 6월하순만 강함) |
| 메타+1m | 재구성 2년 10.4k | 1/5 (2폴드 강한 음수) |
| 풀피처(+1h/BTC/캔들/섹터) | 재구성 2년 | **단일분할 z=12 → 누수. 보정 후 0/5** |

- **z=12 는 시간정렬 누수였다**: pkl 1h봉 ts=봉 open, fire=봉 close(+1h)인데 1h 컨텍스트가 close
  (=진입 1시간 후 가격)로 계산되고 entry 는 open 이라 모델이 미래 1시간을 미리 읽었다. 1봉 시프트 →
  z 12→0/5 붕괴로 확정. 앵커를 봉 close 로 통일 후 재검증 시 **모든 피처셋 0/5 폴드**, 대부분 음수.
- close 앵커(진짜 진입시점) RAW(전체진입)도 net **−0.116%**(TP 8%). open 앵커 +0.16% 는 진입을
  1시간 이르게 잡아 bounce 를 선취한 착시. 신호가 봉 마감으로 확정될 땐 쉬운 move 가 끝나 있음.

## 결론

11,705 fire · 2년 · 다중레짐 · 풀피처(지표·캔들·전고저·BTC·섹터) · 누수보정에서 **"1m 차트모양으로
TP/SL 을 ML 판별"하는 견고한 엣지는 없다**. 실패 원인은 피처 부족이 아니라 **레짐 비정상성** —
피처는 in-sample 정보를 갖지만 학습한 패턴이 다음 레짐으로 일반화되지 않는다(폴드별 부호 비일관 =
무신호의 지문). 피처 추가·DL 로도 없는 신호는 만들 수 없다. ML 스코어링 트랙 종료 권고.

## 교훈 (재현 방지)

1. **결과가 갑자기 너무 좋으면(z 5→12) 축하 말고 누수부터 의심.** 1m/1h 시간정렬(open vs close)
   1시간 어긋남이 forward 누수의 전형. 진입 앵커와 피처 계산 시점을 반드시 일치시킬 것.
2. **단일 70/30 분할 z 과신 금지** — test 가 우호적 레짐에 떨어지면 z5 도 운. 다중레짐 walk-forward 필수.
3. **이후 봉은 라벨로만, 피처로 금지.** 진입 결정은 fire 시점이라 이후 데이터는 미래.
4. random-vs-signal + 정직비용(≥10bp) + 다중레짐 = 신호 검증 3종 세트 (`project_research_signal_screen_summary`).

## 출처

- 본 조사 스크립트(미커밋 `_` prefix): 위 sources 목록.
- 탐지기 정의: [[38-airborne-indicator-reverse-engineering]] · `src/signals/airborne_bb_reversal.py` (v1.1).
- 데이터: Binance fapi 1m/1h klines, `logs/airborne_fires/` (fire store · sim cache · 재구성 캐시).
