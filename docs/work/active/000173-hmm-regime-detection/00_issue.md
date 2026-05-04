# feat: HMM regime detection + S2c/S4 strategy switching (#172 후속)

## 사용자 관점 목표
PR #172 (단기 스윙 전략 5 iter ralph) 의 유일한 미통과 게이트 **PBO=0.714** 를 직접 공략. Regime detection 으로 시장 상태를 판별한 후, regime 별 best strategy 를 자동 전환하여 PBO 와 통합 Sharpe 를 동시 개선한다.

## 배경 — PR #172 의 한계
- best variant: S4 funding carry (Sharpe 0.961, MDD -17.1%, mhr 0.29)
- best W1: S2c Donchian + vol-target (Sharpe 0.814, MDD -18.7%, mhr 0.51)
- **PBO=0.714** > 0.20 게이트 — variant 7→3 축소해도 0.779→0.714 (regime-dep 본질)
- 5 iter 모두 PBO 미해결 — algorithmic 단순 변형으로는 한계

## 가설
- BTC **상승장 regime** = S2c Donchian 우세 (trend-following, mhr 0.51)
- BTC **funding 음수 regime** = S4 funding carry 우세 (mean-revert structural premium, Sharpe 0.96)
- HMM 으로 regime 판별 → conditional strategy switching → **PBO ~0.40 + 통합 Sharpe ~1.2** 기대

## 학술 근거
- Hamilton, J.D. (1989). A New Approach to the Economic Analysis of Nonstationary Time Series. Econometrica 57(2), 357-384. https://doi.org/10.2307/1912559 — 2-state HMM 시초
- Ang, A. & Bekaert, G. (2002). International Asset Allocation with Regime Shifts. RFS 15(4), 1137-1187. https://doi.org/10.1093/rfs/15.4.1137 — regime-dependent factor returns
- Liu, Y., Tsyvinski, A., Wu, X. (2022). JF 77(2), 1133-1177 — crypto risk premia regime
- 기존 인프라: `docs/background/30-market-regime-detection.md` (Hurst exponent, MS-Multifractal)

## 활용 가능 인프라 (#99 + #172 머지 후)
- `src/ml/cv.py::PurgedKFold`
- `src/ml/validation/{deflated_sharpe,cscv,pbo}.py`
- `src/backtest/swing/strategies.py` — S2c, S4, W1 그대로 재사용
- `lake/ohlcv/freq=1m/`, `lake/funding_rate/`
- `scripts/bench_swing_iter5_stability.py` — bench 패턴 재사용

## 사전등록 Variant Matrix (frozen)
| ID | 정의 |
|----|------|
| R0 | S2c always (baseline, no regime) — PR #172 의 W1 재현 |
| R1 | S4 always (baseline 2) — PR #172 의 W2 재현 |
| R2 | HMM-2state on returns (vol regime) → high-vol=S4, low-vol=S2c |
| R3 | HMM-3state (returns + funding) → bull=S2c, bear/sideways=S4, crash=flat |
| R4 | Threshold-based switch (BTC 30d return > 0 = S2c, funding < 0 = S4) — simple baseline |
| R5 | Ensemble vote (R2 + R3 + R4 majority) |

## 변경/추가 파일
- `src/ml/regime/__init__.py`, `hmm.py`, `threshold.py` — regime 판별
- `src/backtest/swing/regime_switching.py` — conditional strategy router
- `scripts/bench_regime_switching.py` — R0-R5 사전등록 bench
- `tests/test_regime_*.py`
- `docs/background/49-hmm-regime-detection.md` — 학술 근거 노트
- `docs/work/active/<NEW>/00_issue.md/01_plan.md/02_implementation.md`

## 완료 기준
- [ ] HMM 모듈 구현 + 단위 테스트 (Hamilton 1989 정합성)
- [ ] 6 variant (R0-R5) 사전등록 backtest 실행
- [ ] 게이트 평가 — 특히 **PBO ≤ 0.20** 통과 여부
- [ ] R2 또는 R3 가 R0/R1 baseline 대비 Sharpe 개선 입증
- [ ] 정식 보고서 + Architect verification

## 의존성
- **하드 선결**: PR #172 머지 (S2c, S4 strategy 코드 + bench framework)
- 권장: #99 머지 (DSR/PBO 인프라 — 이미 머지)

## 범위 밖 (별도 후속)
- 다른 자산 (ETH, SOL) regime — 본 이슈는 BTC only
- Online HMM (실시간 학습) — backtest 만
- Deep learning regime detection — 후속

## 출처
- PR #172 02_implementation.md (5 iter 결과)
- Hamilton 1989, Ang/Bekaert 2002, Liu et al. 2022

## 작업 내역
