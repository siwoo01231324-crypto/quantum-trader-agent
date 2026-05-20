---
type: strategy
id: live-scanner-ensemble-bn1d
name: Live-Scanner Ensemble (Binance USDT-perp 1d, Candidate C wrapper)
status: rejected
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1d
uses_signals:
- bollinger
- rsi
- macd
- atr
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-20
sharpe_bt: 1.085
sharpe_live: null
mdd_bt: -0.9667
annual_return_bt: 0.764
trades_bt: 4046
profit_factor_bt: 0.9517
expectancy_bt: -0.002075
backtest_period: 2021-05-20/2026-05-20
verdict_5y: "rejected: PF=0.952<1, expectancy=-0.208%/trade<0, MDD=-96.7% (5y BN USDT-perp 28 syms, cost 10bp). Wrapper 1-position OR-gate design loses the 4-parallel diversification effect that drove Candidate C's MDD -23%."
last_updated: 2026-05-20
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
summary_ko: |
  4종 live-scanner 서브전략 wrap한 단일 ensemble (Candidate C: STRONG 60+WEAK
  40 × half-kelly 0.5). 5y BN 1d 벤치에서 PF=0.952 / exp=-0.21%/trade /
  MDD=-96.67% LOSER. 원인: OR-게이트 1-position 디자인이 4-parallel 분산 효과
  (MDD -23.2%)를 파괴함. 코드는 historical record, production 미사용. 진짜
  후보 C 효과는 production.yaml 에 4 sub 를 parallel entry 로 등록해야 가능.
tags:
- live-scanner
- ensemble
- mean-reversion
- breakout
- bn1d
- half-kelly
- rejected
- architectural-mistake
---

# Live-Scanner Ensemble (BN 1d, Candidate C)

`docs/background/51-live-scanner-bn1d-ensemble-validation.md` 의 "Candidate C"
(STRONG 60% + WEAK 40% × half-kelly) 를 단일 운영 단위로 wrap한 ensemble 전략.

## 배경

- 4종 live-scanner (`live_bb_lower_bounce` · `live_rsi_oversold_volume_spike`
  · `live_oversold_with_divergence` · `live_breakout_with_atr_stop`) 가 코인
  **1분봉** 에서 모두 PF<1 LOSE → `9553e87` rejected.
- 같은 4종을 코인 **1일봉** 으로 재검정 시 PBO=0.0007 PASS + walk-forward
  평균 79% 일관성 → 통계 시그널 존재 (docs/background/51 §3).
- MDD 운영가능화 시뮬에서 STRONG 60+WEAK 40 × half-kelly 가 SR 2.52 /
  MDD -23.2% / CAGR +73% 로 단일 best 보다 risk-adjusted 우월.

본 전략은 그 Candidate C 를 **단일 instance** 로 wrap — orchestrator·
risk·sizing 관리 단순화. 4 sub-strategy 를 parallel 등록하는 대안 대비
position 1개, conviction-weighted size 라는 trade-off.

## 진입 규칙

3 게이트:

1. **Warmup**: `len(history) >= 60` (각 sub 의 MIN_HISTORY 중 max 의 안전 margin).
2. **Sub dispatch**: 4 sub-strategy 의 `on_bar(ctx)` 를 동일 ctx 로 모두 호출.
3. **Conviction**: buy 발신한 sub 가 1개 이상 → buy. 없으면 hold.

**사이즈**:
```
size = default_size × Σ(weight of buying subs) × half_kelly
```

- `default_size = 0.05` (기본)
- `half_kelly = 0.5` (Candidate C)
- 기본 weights (정규화 후 합 1.0):
  - `rsi_oversold` = 0.30 (STRONG, DSR PASS)
  - `breakout_atr` = 0.30 (STRONG, DSR PASS)
  - `bb_lower`     = 0.20 (WEAK, diversifier)
  - `oversold_div` = 0.20 (WEAK, diversifier)

**예상 사이즈 범위**:
- 단일 sub 발신: 0.05 × 0.20~0.30 × 0.5 = **0.005 ~ 0.0075**
- STRONG 2종 동시 발신: 0.05 × 0.60 × 0.5 = **0.015**
- 4 sub 모두 발신: 0.05 × 1.00 × 0.5 = **0.025**

## 청산

`LivePositionRiskManager` 가 자동 처리:
- `stop_loss_pct = 0.03`
- `take_profit_pct = 0.06`
- `trailing_stop_pct = null`

본 wrapper 는 sell signal 을 발행하지 않음.

## 리스크 연동

```python
orch.register_strategy(
    "live_scanner_ensemble_bn1d", LiveScannerEnsembleBn1d(),
)
orch.register_strategy_returns(
    "live_scanner_ensemble_bn1d", daily_returns_series,
)
orch.refresh_portfolio_risk()
```

## 5y 벤치 결과 (2026-05-20) — **REJECTED**

```
조건: BN USDT-perp 28 심볼 × 5y × cost 10bp × bench_live_scanner._replay_symbol
원자료: reports/bench_ensemble_bn1d_5y.json

trades       = 4,046
win_rate     = 42.02%
payoff       = 1.313
PF           = 0.9517    ← 1 미만 ❌
expectancy   = -0.2075%/trade ← 음수 ❌
MDD          = -96.67%   ← 살인적
sharpe       = +1.085 (벤치 inflated, 신뢰불가)
ann_return   = +76.4%  (벤치 inflated)
avg_hold_days= 3.6
VERDICT      = LOSER (PF<1 AND exp<0)
```

## 진단 — 왜 wrapper 가 4-parallel 시뮬과 다른가

후보 C 의 분산 효과 (MDD -23.2%) 가 **본 wrapper 디자인에서 사라짐**. 근본 원인:

1. **OR-게이트 진입의 부작용**: wrapper 는 4 sub 중 하나만 firing 해도 1 position
   진입. 한 시점 1 position 만 유지. 4-parallel 디자인은 각 sub 가 자기 position
   을 따로 들고 평균 PnL → 시간적·전략적 분산. wrapper 는 그 분산을 못 얻음.
2. **`in_pos` 게이트의 함정**: wrapper 진입 후 stop/tp 청산 전까지 다른 sub
   firing 무시 → 단독 sub 의 profit 기회 누락. 4-parallel 은 각자 독립 trade.
3. **분산 효과의 본질**: 4-parallel MDD -23% 는 **4 독립 PnL series 평균**의
   산물. wrapper 는 하나의 PnL series 만 생성 — 분산 효과 자체가 발생 불가.
4. **conviction-weighted size 의 비효율**: bench replay 가 % return 만 추적
   하니 size 변화는 PF/expectancy 에 무영향. 즉 wrapper 의 핵심 가치 (사이즈
   가변) 가 backtest 에서 검증 불가, 실제 운영에서도 분산 효과를 만들지 못함.

요약: **본 wrapper 는 architectural mistake**. "4 sub-strategy 를 한 단위로 묶어
관리하기 쉽게" 라는 의도였지만, 그 묶음 자체가 후보 C 의 핵심인 분산 효과를
파괴함. 4-parallel orchestrator 등록만이 후보 C 효과를 실제로 만들 수 있음.

## 후속 권고 (별도 PR — wrapper 폐기, 4-parallel 등록)

본 wrapper spec 은 historical record 로 보존하고, 진짜 후보 C 운영 진입은
다음 경로로:

1. `production.yaml` 에 4 sub-strategy 를 **parallel entry** 로 추가 (4종 모두
   uncomment + `enabled: true`):
   ```yaml
   - id: live-rsi-oversold-volume-spike
     class: backtest.strategies.live_rsi_oversold_volume_spike.LiveRsiOversoldVolumeSpike
     kwargs: { default_size: 0.015 }  # 0.05 × 0.30 × half_kelly
   - id: live-breakout-with-atr-stop
     class: backtest.strategies.live_breakout_with_atr_stop.LiveBreakoutWithAtrStop
     kwargs: { default_size: 0.015 }  # 0.05 × 0.30 × half_kelly
   - id: live-bb-lower-bounce
     class: backtest.strategies.live_bb_lower_bounce.LiveBbLowerBounce
     kwargs: { default_size: 0.010 }  # 0.05 × 0.20 × half_kelly
   - id: live-oversold-with-divergence
     class: backtest.strategies.live_oversold_with_divergence.LiveOversoldWithDivergence
     kwargs: { default_size: 0.010 }  # 0.05 × 0.20 × half_kelly
   ```
   사이즈 = `default_size_baseline × Candidate-C weight × half_kelly`. 각 entry
   가 독립 position 유지 → 진짜 분산.
2. 각 sub-strategy 의 spec `status: rejected → active-bn1d-ensemble-member` 갱신
   (또는 별도 ensemble 자식 spec 신설).
3. 4 sub entry 모두에 ENV gate `LIVE_SCANNER_BN1D_ENSEMBLE_ENABLED=1` 적용 — 한
   번에 켜고 끌 수 있게.
4. wrapper 코드 (`live_scanner_ensemble_bn1d.py`) 는 코드는 유지하되 production
   미사용. 미래 wrapper-style ensemble 패턴 비교용 reference.

## 재활성화 조건 (만약 wrapper 살리려면 — 어려움)

본 wrapper 가 production 후보가 되려면:

1. **OR-게이트 + 1-position 디자인 폐기**. 대신 sub-aware allocation logic 필요
   (예: 각 sub 의 신호 강도 × weight 로 sub-position 4개 유지).
2. 그러나 그건 본질적으로 4-parallel 와 동일 — wrapper 의 단순성 가치 상실.
3. 결론: wrapper 디자인 자체가 후보 C 와 양립 불가. **재활성화 권고 안 함**.

## 운영 규칙

**LLM 호출 금지** (불변식 #6).

**활성화 게이트 — 2중**:
1. `production.yaml` entry `enabled: true`
2. ENV `LIVE_SCANNER_BN1D_ENSEMBLE_ENABLED=1`

본 spec 작성 시점에 production.yaml 의 entry 는 **commented** — 사람이 의도적으로
주석 풀어야 활성화 가능.

**활성화 조건** (production gate):
1. wrapper-specific 5y bench 가 PF>1 / exp>0 통과
2. paper 6개월 운영 (실거래소 testnet 또는 paper broker) 무사고
3. 월별 monitoring: PF, exp/trade, MDD, trade count
4. **자동 trip 게이트**: 3개월 rolling PF < 1 → ENV gate 자동 OFF + 알람

## 함정 — production 진입 전 알아둘 것

(docs/background/51 §5 와 동일하나 wrapper 한정 추가)

1. **wrapper ≠ 4-parallel**: 4-parallel 시뮬레이션이 보여준 MDD -23.2% 는 4
   independent positions 평균 가정. wrapper 의 1-position 사이즈는 conviction-
   weighted 라 sub 들이 분산해서 발신하면 사이즈 작아짐 (단일 sub = 0.005,
   default size 의 10%). 절대 PnL 변동성 작아지나 trade count 도 줄어듦.
2. **conviction-weighted 의 함정**: 4 sub 동시 발신은 드물게 발생. 대부분의
   trade 가 단일/2 sub 발신 size 라 expectancy 가 정확히 1/4 ~ 1/2 비례 작아짐.
3. **5y backtest 한계**: walk-forward 보완하나 진짜 미래 OOS 와 다름.
4. **비용 모델 단순**: cost_bps 10bp 만. 펀딩비·갭·rate-limit·슬리피지 미반영.
   보수적 페널티: SR -20~30%, MDD +25~50%, CAGR -30~50%.
5. **MDD -23% 운영 압박**: 대형 헤지펀드 limit -25% 임계. 1인 운영 멘탈/자금
   압박 큼.

## 관련

- `docs/background/51-live-scanner-bn1d-ensemble-validation.md` — 출처 검증 노트
- `scripts/validate_live_scanners.py` — DSR/PSR/PBO 도구
- `scripts/analyze_live_scanners_robustness.py` — walk-forward + ensemble + half-kelly
- [[live-bb-lower-bounce]] · [[live-rsi-oversold-volume-spike]] ·
  [[live-oversold-with-divergence]] · [[live-breakout-with-atr-stop]] — sub-strategies
- 단위 테스트: `tests/backtest/test_live_scanner_ensemble_bn1d.py` (20건)
