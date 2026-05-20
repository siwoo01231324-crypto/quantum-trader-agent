---
type: research
id: 51-live-scanner-bn1d-ensemble-validation
name: Live-Scanner 4종 Binance 1d Ensemble 검증 (DSR/PBO/Walk-Forward/MDD)
sources:
  - reports/eval_live_scanners_5y.json
  - reports/bench_krx_live_*.json
  - reports/bench_bn1d_live_*.json
  - reports/validate_krx_1d_v3.json
  - reports/validate_bn_1d_v3.json
  - reports/robustness_krx_1d.json
  - reports/robustness_bn_1d.json
  - scripts/bench_live_scanner.py
  - scripts/bench_live_mg_quick.py
  - scripts/validate_live_scanners.py
  - scripts/analyze_live_scanners_robustness.py
  - https://www.jstor.org/stable/24038884
  - https://www.jstor.org/stable/24587864
last_updated: 2026-05-20
tags:
  - validation
  - dsr
  - pbo
  - walk-forward
  - ensemble
  - half-kelly
  - live-scanner
  - mdd
---

# Live-Scanner 4종 Binance 1d Ensemble 검증

> **TL;DR** — `9553e87` 에서 live-scanner 4종이 코인 1분봉에서 모두 PF<1 음의엣지로 rejected. **본 후속 검증**은 (a) 같은 4종을 KRX 일봉·Binance 일봉으로 재검정, (b) DSR/PBO 다중검정 보정, (c) walk-forward 연도별 일관성, (d) ensemble + half-kelly MDD 운영가능성을 측정. **결론: KRX 일봉은 PBO 0.95 = 결과 우연. Binance 일봉은 통계적 시그널 존재 (PBO 0.0007, walk-forward 79% 평균 일관성). 4종 equal-weight + half-kelly 또는 STRONG 60%+WEAK 40% × half-kelly 가 MDD -21~-23% 의 운영가능 후보**. 단 운영 진입은 본 노트의 권고일 뿐, production.yaml flip 은 보류.

## 1. 배경

- `9553e87` 가 live-scanner 5종 + `momo-btc-v2` 를 5y Binance 1분봉 벤치에서 모두 PF<1 NET LOSER 로 확정 → `production.yaml` 비활성화 + spec `status: rejected` + 게이트 강화 (PF/expectancy 우선, Sharpe 단독 금지).
- 본 노트는 사용자 질문 "같은 4종을 KRX 일봉 + 코인 일봉으로 적용하면 어떻게 되나?" 에 대한 정량 응답.
- 대상 4종: `live_bb_lower_bounce`, `live_rsi_oversold_volume_spike`, `live_oversold_with_divergence`, `live_breakout_with_atr_stop`.

## 2. 방법론

### 2.1 벤치 (1차)
- 도구: `scripts/bench_live_scanner.py` (production exit 로직 = `LivePositionRiskManager` 와 동일 stop/TP/trailing 적용)
- 조건:
  - KRX 1d: 345 심볼, 5y, cost 55bp
  - Binance 1d: 28 심볼 (한자 종목 1개 fetch-fail 제외), 5y, cost 10bp
  - (참고) Binance 1m: `reports/eval_live_scanners_5y.json` — `9553e87` 의 원본 결과 재사용

### 2.2 DSR/PSR/PBO (다중검정 보정)
- 도구: `scripts/validate_live_scanners.py` + `src/ml/validation/{deflated_sharpe, pbo}`.
- 핵심 수정 (v3 발견): 1차 시도(v1) 에서는 `_daily_pnl_series` 가 거래일 있던 날만 시리즈에 포함 → **분산 과소평가 → SR 1.8~4.6배 부풀림**. v3 에서 panel 의 전체 거래일 인덱스로 reindex(fill=0) → 진짜 daily series 로 재측정.
- 게이트: PSR ≥ 0.95, DSR ≥ 0.95, PBO ≤ 0.20 (프로젝트 SOP §3.7).

### 2.3 Walk-Forward 일관성
- 도구: `scripts/analyze_live_scanners_robustness.py`.
- 방법: 각 전략의 daily PnL 시리즈를 calendar year 단위로 분할 → 연도별 PF/exp/SR/MDD 산출 → "PF>1 ∧ exp>0 인 연도 / 활성 연도" 비율로 일관성 측정.

### 2.4 MDD 운영가능화 시뮬
- 도구: 위 동일 분석 스크립트.
- 시나리오:
  1. 단일 best 전략 vs 4종 equal-weight ensemble
  2. half-kelly (사이즈 × 0.5), quarter-kelly (× 0.25)
  3. weighted: STRONG 60% (rsi_oversold + breakout_atr) + WEAK 40% (bb_lower + oversold_div)

## 3. 결과

### 3.1 PF/expectancy (trustworthy 지표, 합산 기반)

| 전략 | KRX 1d (55bp) | Binance 1d (10bp) | Binance 1m (10bp) |
|---|---|---|---|
| `live_bb_lower_bounce` | PF=1.200 exp=+0.62% ✅ | PF=0.646 exp=-1.72% ❌ | PF=0.922 exp=-0.18% ❌ |
| `live_rsi_oversold_volume_spike` | PF=1.183 exp=+0.57% ✅ | **PF=2.049 exp=+3.16%** ✅ | PF=0.919 exp=-0.19% ❌ |
| `live_oversold_with_divergence` | PF=0.892 exp=-0.37% ❌ | PF=0.729 exp=-1.24% ❌ | PF=0.911 exp=-0.20% ❌ |
| `live_breakout_with_atr_stop` | PF=1.132 exp=+0.51% ✅ | **PF=1.326 exp=+1.43%** ✅ | PF=0.868 exp=-0.24% ❌ |

### 3.2 DSR/PSR/PBO (다중검정 보정, v3 진짜 daily series)

**KRX 1d (n_days=1808)**

| 전략 | SR_ann | skew | kurt_e | MDD | PSR | DSR |
|---|---:|---:|---:|---:|---:|---:|
| `live_bb_lower_bounce` | +1.13 | +0.80 | +6.40 | -81.4% | 1.000 | 1.000 |
| `live_rsi_oversold_volume_spike` | +0.45 | +1.89 | +26.53 | -45.2% | 1.000 | 1.000 |
| `live_oversold_with_divergence` | +1.12 | +1.07 | +6.40 | -82.1% | 1.000 | 1.000 |
| `live_breakout_with_atr_stop` | +0.75 | +2.46 | +15.78 | -99.9% | 1.000 | 1.000 |
| **PBO (4 trials, CSCV)** | | | | | | **0.9486 ❌** |

→ 개별 DSR 4/4 PASS 이나 **PBO 0.95 FAIL** = IS-best 가 OOS-best 일 확률 5%. 결과 우연 가능성 압도적.

**Binance 1d (n_days=2557)**

| 전략 | SR_ann | skew | kurt_e | MDD | PSR | DSR |
|---|---:|---:|---:|---:|---:|---:|
| `live_bb_lower_bounce` | +0.22 | -0.92 | +36.34 | -71.3% | 1.000 | **0.000** |
| `live_rsi_oversold_volume_spike` | +1.14 | +4.29 | +59.91 | -43.3% | 1.000 | **0.968** ✅ |
| `live_oversold_with_divergence` | +0.77 | +1.28 | +11.48 | -75.3% | 1.000 | **0.000** |
| `live_breakout_with_atr_stop` | +2.44 | +2.93 | +13.24 | -88.9% | 1.000 | **1.000** ✅ |
| **PBO (4 trials, CSCV)** | | | | | | **0.0007 ✅** |

→ 2종 DSR PASS + **PBO 0.0007 PASS** = best 가 OOS 에서도 일관됨. 통계 시그널 존재.

### 3.3 Walk-Forward 연도별 일관성

| 전략 | KRX 1d | Binance 1d |
|---|---|---|
| `live_bb_lower_bounce` | 3/8 (37%) | 5/7 (71%) |
| `live_rsi_oversold_volume_spike` | 5/8 (62%) | **6/7 (86%)** |
| `live_oversold_with_divergence` | 4/8 (50%) | 5/7 (71%) |
| `live_breakout_with_atr_stop` | 2/8 (25%) | **6/7 (86%)** |
| **평균** | **44%** ❌ | **79%** ✅ |

→ KRX 일관성 44% = 노이즈 수준 (PBO 0.95 와 정성 일치). Binance 평균 79%, DSR PASS 2종이 86% = 진짜 시그널.

### 3.4 MDD 운영가능화 (Binance 1d)

| 구성 | SR | MDD | CAGR | 평가 |
|---|---:|---:|---:|:---:|
| 단일 best (`breakout_with_atr_stop`) | 2.44 | -88.9% | +971% | ❌ |
| best × half-kelly | 2.44 | -64.1% | +285% | ❌ |
| STRONG 2종 ensemble full | **2.64** | -66.3% | +359% | ❌ |
| STRONG 2종 × half-kelly | 2.64 | -40.4% | +125% | △ |
| **STRONG 2종 × quarter-kelly** | **2.64** | **-22.3%** | **+52%** | ✅ |
| ALL 4종 equal full | 2.38 | -38.9% | +150% | △ |
| **ALL 4종 × half-kelly** | **2.38** | **-21.5%** | **+62%** | ✅ |
| STRONG 60% + WEAK 40% full | 2.52 | -43.3% | +185% | △ |
| **STRONG 60% + WEAK 40% × half-kelly** | **2.52** | **-23.2%** | **+73%** | ✅ |

→ **WEAK 2종이 분산자산 역할**. 자체 알파 약(DSR FAIL)하나 STRONG 2종과 상관성 낮아 합산 시 MDD 큰 폭 감소 (-89% → -39% → half-kelly 적용 시 -21.5%).

### 3.5 KRX 운영가능화 (참고용, PBO FAIL 이라 사실상 무의미)

| 구성 | SR | MDD | CAGR |
|---|---:|---:|---:|
| 단일 best (`bb_lower_bounce`) | 1.13 | -81.4% | +54% |
| ALL 4종 equal full | 1.29 | -87.8% | +49% |
| ALL 4종 × half-kelly | 1.29 | -63.3% | +24% |
| best × half-kelly | 1.13 | -54.0% | +28% |

→ KRX 4종이 같은 매크로 리스크 노출 → 분산 효과 거의 없음 (MDD -88% → -63%). 운영불가.

## 4. 핵심 발견

1. **시장·timeframe 매트릭스**: 같은 알파 가설(mean-reversion / oversold-bounce) 도 (시장 × 봉단위) 조합마다 결과 정반대.
   - KRX 1d: 개별 PF>1 이나 PBO FAIL → selection bias
   - Binance 1m: 4종 모두 LOSE (`9553e87` 결과)
   - Binance 1d: 2종 DSR PASS + PBO PASS + WF 86% = 진짜 시그널
2. **분산자산의 가치**: BN 1d 에서 DSR FAIL 한 2종 (`bb_lower_bounce`, `oversold_with_divergence`) 도 STRONG 2종과 합치면 MDD 절반 (-66% → -39%). 단독으로는 약해도 분산자산 가치.
3. **half-kelly + 4종 ensemble** 이 **single-best + half-kelly** 보다 risk-adjusted 우월:
   - single-best × half-kelly: SR 2.44, MDD -64.1%
   - 4종 × half-kelly: SR 2.38, MDD -21.5%
4. **STRONG 가중치 ↑ + 분산 유지** 가 균형점:
   - STRONG 60+WEAK 40 × half-kelly: SR 2.52, MDD -23.2%, CAGR +73%
5. **v1 → v3 의 SR 부풀림 1.8~4.6배** — `_daily_pnl_series` 가 거래 없는 날을 시리즈에서 빠뜨리면 분산 과소평가. 향후 모든 SR 계산은 panel 거래일 전체 인덱스로 reindex(fill=0) 필수.

## 5. Caveat (정직성)

1. **5y single-window backtest**: walk-forward 가 보완하나, 진짜 미래 OOS 와는 다름.
2. **현실 비용 모델 단순**: cost_bps 만. 펀딩비 (USDT-perp), exchange outage, API rate limit, 부분체결, 가격 갭, 슬리피지 미반영. 보수적 페널티: SR -20~30%, MDD +25~50%, CAGR -30~50%.
3. **다중검정 다층**: 4 전략 × 3 (KRX 1d / BN 1d / BN 1m) = 12 검정. PBO 가 strategy-level 보정만, timeframe/market 선택은 별도 selection bias.
4. **skew/kurt 극단** (kurt 6~59): PSR/DSR 의 비정규성 보정도 한계. CAPM 식 SR 자체가 fat-tail 분포에선 단순 해석 어려움.
5. **MDD -21.5% 도 운영 압박**: 대형 헤지펀드 risk limit -25% 가까운 임계. 1인 운영자 입장 멘탈/자금 압박 큼.
6. **CAGR 비현실적 절대값**: 5y `final_eq` 가 10800배 같은 수치는 매일 자본 비례 사이즈 가정에 따른 종이 복리. 코인 USDT-perp 유동성 / exchange limit / 슬리피지가 실제 그 복리를 막음. SR/MDD 비율로만 평가.

## 6. 권고

### 6.1 production 진입 후보 (사용자 결정)

| 후보 | SR | MDD | CAGR | 단순성 |
|---|---:|---:|---:|---|
| A: STRONG 2종 × quarter-kelly | 2.64 | -22.3% | +52% | ✅ 전략 2개만 |
| B: ALL 4종 × half-kelly | 2.38 | -21.5% | +62% | △ 약한 2종 모니터링 |
| **C: STRONG 60% + WEAK 40% × half-kelly** | **2.52** | **-23.2%** | **+73%** | △ 가중치 관리 |

→ **후보 C** 권고 (SR/MDD/CAGR 균형, STRONG 가중치 + 분산).

### 6.2 production 진입 전 조건 (사용자가 결정)

본 노트는 **운영 권고 자료**일 뿐, 자동 production 적용은 하지 않는다.

1. **spec 변경**: 4종의 `status: rejected` → `status: experimental-bn1d`. 코인 1분봉은 영구 lock, 코인 일봉은 paper 운영 후보.
2. **production.yaml 갱신**: 4종 entry 추가하되 default `enabled: false`. 활성화는 ENV gate (예: `LIVE_SCANNER_BN1D_ENSEMBLE_ENABLED=1`).
3. **paper 운영 6개월** + 월별 PF/exp/MDD 모니터링. 3개월 이상 PF<1 시 자동 trip.
4. **추가 검증** (후속 작업):
   - 펀딩비 시뮬 (USDT-perp 8h 펀딩 누적)
   - 10y 데이터 확장 (가능 시)
   - 보수적 비용 모델 (15~20bp)
   - 슬리피지 시뮬레이션
   - 멀티전략 risk DSL 조합 게이트 적용 검증

### 6.3 후속 spec 작업 (사용자 결정 후 진행)

- `docs/specs/strategies/live-bb-lower-bounce.md` 등 4종 spec 의 본문에 §"Binance 1d ensemble 후보 (51-live-scanner-bn1d-ensemble-validation)" 추가
- 또는 단일 통합 운영 spec `docs/specs/strategies/ensemble-bn1d-live-scanner.md` 신설
- 새 portfolio risk rule: ensemble weight constraint + half-kelly enforcement

## 7. 재현 방법

```bash
# 1. 4종 KRX/Binance 1d 벤치 (요지표)
python scripts/bench_live_scanner.py --strategy <SID> --universe krx --bar 1d \
    --period 5y --output reports/bench_krx_<SID>.json
python scripts/bench_live_scanner.py --strategy <SID> --universe binance --bar 1d \
    --period 5y --output reports/bench_bn1d_<SID>.json

# 2. DSR/PSR/PBO (다중검정 보정) + series dump
python scripts/validate_live_scanners.py --universe krx --bar 1d \
    --output reports/validate_krx_1d_v3.json
python scripts/validate_live_scanners.py --universe binance --bar 1d \
    --output reports/validate_bn_1d_v3.json

# 3. Walk-forward + Ensemble + half-kelly (후처리)
python scripts/analyze_live_scanners_robustness.py
```

## 8. 출처 / 자료

- Bailey, D.H. & López de Prado, M. (2014). The Deflated Sharpe Ratio. *Journal of Portfolio Management* 40(5), 94-107.
- Bailey, Borwein, López de Prado, Zhu (2014). The Probability of Backtest Overfitting. *Journal of Computational Finance* 20(4), 39-69.
- `reports/eval_live_scanners_5y.json` (`9553e87` 의 원 자료)
- `reports/bench_{krx,bn1d}_live_*.json` (4종 × 2 유니버스, 본 노트에서 생성)
- `reports/validate_{krx,bn}_1d_v3.json` (DSR/PSR/PBO + daily series)
- `reports/robustness_{krx,bn}_1d.json` (walk-forward + ensemble + half-kelly)
- 관련: [[12-validation-protocol]], [[50-live-universe-scanner-paradigm]]
