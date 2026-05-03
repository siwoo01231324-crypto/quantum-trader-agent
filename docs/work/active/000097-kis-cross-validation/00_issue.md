# feat: 메타라벨러 × KIS 교차 검증 — BTC/KRX 두 자산군 DSR·PR 비교

## 사용자 관점 목표
#85 에서 구축한 메타라벨링 레이어 (LightGBM 2차 필터 + purged CV + walk-forward) 를 BTC 계열뿐 아니라 KRX 계열 전략에도 적용해, 메타라벨러 가설이 **단일 자산군 과적합이 아님** 을 교차 검증한다.

## 배경
#85 는 BTC 단일 자산군으로 완주 / 검증했다. 메타라벨러 가설("2차 필터가 primary 전략 샤프를 유의미하게 개선한다") 이 일반적 통계 효과인지 BTC 과적합인지 확인이 필요하다. #79(전략 카탈로그 multi-venue) + #96(KIS 분봉 + momo_kis_v1) 이 머지되면 BTC 계열과 KRX 계열 전략이 양립하므로 cross-asset 비교가 가능해진다.

## 완료 기준
- [ ] KRX 전략 각각의 primary 일수익률 시계열 산출 (`momo_kis_v1` 필수, `breakout_donchian` 선택)
- [ ] 메타라벨러 학습 + walk-forward 평가 KRX 전략 ≥ 1건 완료
- [ ] PR-AUC / DSR / 샤프 개선률 BTC 결과와 병렬 비교 테이블 생성
- [ ] **통계적 유의 판정**: 사전에 정한 임계치 (예: DSR 개선 ≥ 0.3) 기준으로 두 자산군 모두 개선 → 가설 채택, 1개만 개선 → 재설계 검토, 0개 개선 → 가설 기각
- [ ] `docs/work/active/<번호>/02_implementation.md` 에 비교 결과 + 결론(채택/기각/추가 조사) 기록
- [ ] 결과에 따른 후속 이슈 생성 (채택 시 → paper live 검증, 기각 시 → 메타라벨러 재설계)

## 구현 플랜
1. **데이터 파이프라인 점검**: `momo_kis_v1` (KRX 15m) 의 일수익률 시계열 + 피처 매트릭스 재생성
2. **파이프라인 재사용**: `src/meta_labeling/` (#85 산출물) 의 학습/평가 파이프라인을 KRX 전략 입력으로 실행
3. **신규 스크립트**: `src/meta_labeling/pipelines/kis_cross_validation.py` — KIS 데이터 fit/evaluate
4. **비교 리포팅**: `src/meta_labeling/reporting/cross_asset_compare.py` — BTC/KRX 결과 병렬 집계 (DSR, PR-AUC, brier, calibration)
5. **연구 노트**: `docs/specs/meta-labeling/kis-cross-validation.md`
6. **통계 판정** 섹션: 사전에 정의한 임계치 기반 결론 + `02_implementation.md` 비교 테이블

## 의존성
- **#96 머지 필수** (`momo_kis_v1` 일수익률 시계열)
- **#85 머지 확인** (메타라벨러 레이어)
- #79 머지 권장 (`breakout_donchian` 을 확장 비교에 사용)

## 주의사항
- **데이터 누수 주의**: purged CV + walk-forward 는 #85 패턴 엄수. KRX 평일 vs crypto 24/7 에서 leakage 가능성 재점검.
- **샘플 수 확보**: KRX 15m 은 일 ~26 bar × 250 거래일 = 약 6500 bar/year. 메타라벨러가 요구하는 최소 이벤트 수 확인 필요.
- **비용 모델**: KRX 비대칭 0.015% / 0.245% (#79 `src/backtest/cost.py::apply_cost` 재사용, `instrument_type=\"krx\"`).
- **결과가 기각이어도 가치 있는 발견**. 가설 유지보다 정직한 기록 우선.

## 후속
- 가설 **채택 시**: paper live 검증 (#80 실행 프레임워크 연동)
- 가설 **기각 시**: 메타라벨러 재설계 이슈 (feature engineering / 2차 모델 선택 / 레이블링 방식 재검토)

## 개발 체크리스트
- [ ] 테스트 코드 포함 (cross-asset compare 테이블 생성 단위 테스트)
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음

## 작업 내역

### 2026-04-27

**현황**: 0/6 완료 (구현 대기 — Step 0 데이터 가용성 + cron 부터 시작)
**완료된 항목**:
- (없음)
**미완료 항목**:
- KRX 전략 primary 일수익률 시계열 산출
- 메타라벨러 학습 + walk-forward 평가 KRX 전략 ≥ 1건
- BTC/KRX 병렬 비교 테이블 생성
- DSR 기반 통계적 유의 판정 (Phase A에서는 "보류" 가능)
- `02_implementation.md` 비교 결과 + 결론 기록
- 후속 이슈 생성 (Phase B 재실행 + 채택/기각 시 추가 이슈)

**진행 메모**:
- `01_plan.md` ralplan v3 합의 완료 (Architect ENDORSE_WITH_REVISIONS → Critic APPROVE)
- 핵심 결정: Phase A/B 분리 — Phase A 에서 구조 구현 + 합성 검증 + KIS 일일 cron, Phase B 후속 이슈에서 실데이터 통계 판정
- 데이터 실측: `lake/ohlcv/freq=15m/symbol=005930/` 파티션 부재 (KIS API 당일+30일 제약)
- 산출물 21개 파일 (신규 18, 변경 3) — `src/ml/scoring.py`, `src/ml/pipelines/`, `src/ml/reporting/`, `scripts/cron_fetch_kis_daily.py`, `scripts/{train,bench}_metalabeler_kis.py`, `scripts/cross_asset_compare.py`
**변경 파일**: 1개 (`docs/work/active/000097-kis-cross-validation/01_plan.md` ralplan v3 작성)
