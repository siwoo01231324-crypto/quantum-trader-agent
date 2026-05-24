---
type: work-done
id: 01_plan
name: "#173 HMM regime detection 구현 계획"
status: active
---

# #173 HMM Regime Detection — 구현 계획

## 목표

PR #172의 PBO=0.714 미통과 게이트를 regime-conditional strategy switching으로 공략.
Hamilton (1989) 2-state HMM으로 시장 regime을 판별한 후, regime별로 S2c (Donchian vol-target)과 S4 (funding carry)를 자동 전환하여 PBO와 통합 Sharpe를 동시 개선한다.

## 단계별 계획

### Phase 1: HMM 모듈 구현 (src/ml/regime/)

1. **`hmm.py`** — `GaussianHMMRegime` 클래스
   - Hamilton (1989) 2/3-state Gaussian HMM
   - hmmlearn의 `GaussianHMM` 래핑 (Baum-Welch EM + Viterbi decoding)
   - `RegimeResult` dataclass: states, means, variances, transmat, score
   - 상태 라벨링: variance 기준으로 low_vol/high_vol (2-state) 또는 low/mid/high (3-state)
   - 검증 게이트: 합성 2-state 데이터에서 accuracy > 80%

2. **`threshold.py`** — `ThresholdRegime` 클래스
   - 규칙 기반 분류: rolling return + funding rate 임계값
   - State 0 (bullish): rolling return > 0 → S2c
   - State 1 (funding_negative): funding < 0 → S4
   - State 2 (neutral): 기본
   - 검증 게이트: shift(1)로 lookahead 방지 확인

### Phase 2: 단위 테스트 (TDD)

3. **`tests/test_regime_hmm.py`** — HMM 테스트 12건
   - 합성 2-state 데이터 (mu1=+0.001/sigma1=0.005, mu2=-0.001/sigma2=0.02)
   - 상태 복원 accuracy > 80%
   - transition matrix row sum = 1
   - state persistence > 0.90 (clean data)
   - 3-state HMM 정합성
   - edge cases: NaN, 짧은 데이터, fit 전 predict

4. **`tests/test_regime_threshold.py`** — Threshold 테스트 8건
   - 기본 분류, funding 포함/미포함
   - 강한 상승장에서 bullish 비율 > 90%
   - lookback 변화 영향
   - lookahead 방지 (초기 바 = neutral)

### Phase 3: Regime Switching Router

5. **`src/backtest/swing/regime_switching.py`** — R0-R5 router
   - R0: S2c always (baseline)
   - R1: S4 always (baseline 2)
   - R2: HMM-2state → high-vol=S4, low-vol=S2c
   - R3: HMM-3state → bull=S2c, bear/sideways=S4, crash=flat
   - R4: Threshold → return>0=S2c, funding<0=S4
   - R5: Ensemble (R2+R3+R4 majority vote)
   - `VARIANT_REGISTRY` dict로 frozen registry 제공

### Phase 4: Bench 스켈레톤

6. **`scripts/bench_regime_switching.py`**
   - iter 5 bench 패턴 재사용 (data load, resample, metrics, sha256 witness)
   - `--smoke` 모드: 90일 합성 데이터 (funding rate 포함)
   - 6개 variant 순차 실행 → JSON output
   - variant_registry_sha256 기록

### Phase 5: 문서

7. **`docs/background/49-hmm-regime-detection.md`** — 학술 노트
8. **`01_plan.md`** — 본 문서
9. **.ai.md 갱신** — `src/ml/regime/.ai.md`, `src/backtest/swing/.ai.md`

## 검증 게이트

| 게이트 | 기준 | 통과 조건 |
|--------|------|-----------|
| HMM 정합성 | 합성 2-state 데이터 | accuracy > 80% |
| Transition matrix | row sum | = 1.0 (atol 1e-6) |
| State persistence | 합성 block data | > 0.90 |
| Lookahead 방지 | threshold 초기 바 | = neutral (state 2) |
| pytest | test_regime_hmm + test_regime_threshold | 전체 통과 |
| CI invariants | check_invariants.py --strict | 통과 |

## 리스크

| 리스크 | 영향 | 완화 |
|--------|------|------|
| hmmlearn 미설치 | import 실패 | pyproject.toml에 추가 |
| HMM EM 미수렴 | 잘못된 state 할당 | converged 플래그 + n_iter=100 |
| 과적합 (K>3) | PBO 악화 | K=2,3만 사용, BIC 선택은 후속 |
| funding data 부재 | R1/R4 무신호 | DATA_UNAVAILABLE 처리 (기존 S4 패턴) |
| 합성 데이터 과신 | 실데이터에서 다른 결과 | smoke=dry-run, 풀런은 별도 단계 |

## Variant Matrix (frozen)

| ID | 정의 | 학술 근거 |
|----|------|-----------|
| R0 | S2c always | baseline (no regime) |
| R1 | S4 always | baseline 2 |
| R2 | HMM-2state vol regime | Hamilton (1989) |
| R3 | HMM-3state bull/bear/crash | Ang & Bekaert (2002) |
| R4 | Threshold switch | rule-based baseline |
| R5 | Ensemble (R2+R3+R4 vote) | model averaging |

## 다음 단계 (본 사이클 범위 밖)

1. 5년 BTC@4h 풀 backtest 실행
2. PBO 게이트 통과 검증 (≤ 0.20 목표)
3. Online HMM (실시간 학습) 구현
4. Orchestrator 등록 + 일수익률 시계열 export

## 출처

- Hamilton, J.D. (1989). Econometrica 57(2), 357-384.
- Ang, A. & Bekaert, G. (2002). RFS 15(4), 1137-1187.
- Liu, Y., Tsyvinski, A., Wu, X. (2022). JF 77(2), 1133-1177.
- PR #172 02_implementation.md
- [[30-market-regime-detection]]
