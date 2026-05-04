---
type: work-done
id: 02_real_validation
name: "#154 실 KRX 데이터 multi-symbol bench 검증"
status: done
---

# 02 Real Validation — bench_metalabeler_kis multi-symbol equity (#154)

> 작성: 2026-05-04~05 · KRX 1m lake 백필 → multi-symbol pooled bench → verdict 자동 판정
> 목적: 본 PR 의 채점지가 실 KRX 데이터로 작동하는지 + 메타라벨러 효과 평가

## 백필 환경
- 데이터 소스: KIS 모의계좌 API (`HANTOO_FAKE_*` 자격증명, paper)
- 기간: 최근 30 캘린더일 (KIS API 한도) → 약 21 영업일
- 봉: 1m, KRX 시장시간 09:00–15:30 (390분/일)
- 종목당 평균 8,211 봉 (≈ 21영업일 × 390분 = 8,190 ± 21)
- 백필 결과: 11종목 (30 시도, KIS API connect timeout 으로 11에서 중단)
- events 발생 종목: 8 / 11 (3종목은 RSI bullish divergence 0건)

## OOF Probability 분포 (n=128)
메타라벨러 underfit — prob 분포가 매우 좁음:
- min=0.195, max=0.272, mean=0.228, median=0.238
- quantile: 25%=0.195, 75%=0.250, 90%=0.272, 99%=0.272

| Threshold | n_on / n_off | % pass |
|-----------|---|---|
| 0.1 | 128 / 128 | 100% |
| 0.2 | 73 / 128 | 57% |
| 0.3 | 0 / 128 | 0% |
| 0.4 | 0 / 128 | 0% |
| 0.5 | 0 / 128 | 0% |

**해석**: 21영업일·11종목 데이터로 메타라벨러는 거의 균등한 prob 만 출력 (학습 부족). 운영 default threshold 0.5 로는 거래 자체가 발생 안 함.

## Run 결과 (3 thresholds)

### 공통 (모두 동일)
- `n_symbols`: 8 (11종목 중 events 발생만)
- `n_eff`: 8.0 (rho_avg=0, 게이트 통과)
- `cv_mean_accuracy`: 0.775
- `positive_rate`: 0.315
- `n_events_off`: 128
- `sr_off`: -6.46, `sortino_off`: -18.51, `mdd_off`: 0.054, `dsr_off`: 0.051

### threshold=0.5 / 0.3 (default)
- `n_events_on`: 0
- `sr_on/sortino_on/mdd_on`: 0.0 (거래 없음)
- `dsr_on`: 0.117 (거래 없음 = "결정 안 함" artifact)
- `dsr_delta`: +0.066
- **verdict**: `HOLD (dsr_on<0.3)`

### threshold=0.2 (자연 컷오프, 절반 통과)
- `n_events_on`: 73 (57%)
- `sr_on`: **-60.76** ← OFF (-6.46) 보다 **9.4배 더 나쁨**
- `sortino_on`: -183.31 (OFF 의 9.9배 나쁨)
- `mdd_on`: 0.068 (OFF 0.054 보다 큼)
- `dsr_on`: 0.075
- `dsr_delta`: +0.024
- **verdict**: `HOLD (dsr_on<0.3)`

## 결론

### 채점지 자체 (본 PR 의 산출물)
| 검증 항목 | 결과 |
|---|---|
| 실 KRX 1m 데이터로 정상 작동 | ✓ |
| n_eff 게이트 (≥5) | ✓ (5종목 → HOLD, 11종목 → 통과) |
| DSR 임계 게이트 (≥0.3) | ✓ |
| 4종 verdict 분기 (`PASS`/`HOLD (n_eff<5)`/`HOLD (dsr_on<0.3)`) 모두 검증 | ✓ |
| OOF prob 기반 ON 필터 | ✓ |
| Sortino · DSR delta · sharpe alias | ✓ |

### 메타라벨러 효과 (현 데이터)
**메타라벨러가 OFF 보다 ON 이 더 나쁜 신호를 고름 → 미효과**.

threshold 를 OOF 분포 안쪽 (0.2) 으로 낮춰도 ON Sharpe -60.76 vs OFF -6.46 으로 9배 악화. 이는 다음을 시사:
- 학습 데이터 부족 (4종목 × 21일 ≈ 80 events 학습용, 권장 1000+)
- 단기 21일 자체가 시장 음의 평균 (sr_off=-6.46)
- 메타라벨러 학습이 prob 분포를 좁게 평탄화시킴 (underfit)

### Verdict 자동 판정의 정당성
본 채점지가 **잘못된 메타라벨러 (현 학습본) 를 운영에 투입하지 않도록 자동 차단** ✓.
threshold 어떤 값을 써도 verdict=HOLD → 운영 게이트 작동.

## 한계 (KIS API + 본 PR scope)
- KIS intraday API 가 **최근 30일** 데이터만 제공 → 더 긴 검증 불가
- 30종목 백필이 connection timeout 으로 11에서 중단 (재시도 가능하지만 시간 추가 소요)
- 본 PR 은 채점지 자체가 산출물이라, 메타라벨러 학습 개선은 별도 이슈

## 후속 작업 권장
1. **#133 Phase 2 운영 진행 중 lake 자동 누적** → 3-6개월 후 본 채점지 재실행
2. **백필 robustness** — `fetch_kis_backfill.py` 가 connection timeout 자동 재시도하도록 보강 (별도 이슈)
3. **Daily 봉 bench 추가** — KOSPI 일봉으로 더 긴 history (2-3년) 확보 후 별도 채점지 만들기 (별도 이슈)
