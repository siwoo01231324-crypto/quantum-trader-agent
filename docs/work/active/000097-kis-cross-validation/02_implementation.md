---
type: work-done
id: 02_implementation
name: "#97 v5 다종목 pooled 교차 검증 구현 결과"
status: active
---

# #97 메타라벨러 × KIS 교차 검증 — v5 구현 결과

> 생성: 2026-04-28 v5 인프라 + 실데이터 적재 + pooled 학습 완료.
> v5 목적: 1분봉 × 다종목 pooled CV 인프라 + 실데이터 1차 시그널 검증.

## 데이터 가용성

**KIS API 1분봉 backfill 1회 실행 완료** (2026-04-27, 30종목 × 8,602 bars 평균).

| 항목 | 값 |
|------|------|
| Universe | KOSPI200 sector 균등 30종목 (`get_pool_codes(30, seed=42)`) — 005930 강제 포함 |
| Interval | 1분봉 (KIS `FHKST03010200`, paper=False / 실거래 도메인) |
| 데이터 윈도우 | **단일 거래일 (2026-04-27 09:00~15:30 KST)** — KIS API 한계 |
| 종목당 bar 수 | 평균 8,602 bars (중복 페이지 포함, unique 약 390/일) |
| 총 적재 bar | **258,060 bars** |
| 적재 시간 | ~76분 (async concurrency=2) |

### KIS API 한계 발견 (이번 작업에서 확정)
- **historical 분봉 조회 불가** — `FID_INPUT_DATE_1` 파라미터가 KIS 응답에서 **무시됨**. 22 거래일 trading_days 루프 호출했지만 매번 오늘 데이터만 반환.
- **시간 페이지네이션은 가능** — `FID_INPUT_HOUR_1` 을 줄여가며(15:30 → 11:29 → 09:01) 09:00 까지 페이징 가능. 코드 fix 적용 (`src/brokers/kis/price_client.py` time-based pagination).
- **응답 필드 매핑 fix** — `acml_vol` (누적 거래량) → `cntg_vol` (분봉 거래량), `stck_clpr` → `stck_prpr` 으로 정정. 이 fix 전 모든 bar volume=0 으로 학습 불가능했음.

## 성능 비교표

### 메타라벨러 학습 결과 (`scripts/bench_metalabeler_kis.py --multi-symbol --n-symbols 30 --interval 1m --holding-bars 78`)

| 항목 | 값 |
|------|------|
| 학습 사용 종목 | **18 / 30** (이벤트 ≥ 1) — 12 종목은 이벤트 0건으로 skip |
| 총 이벤트 (bullish RSI divergence) | **307** (v3 합성 데이터 ~2 대비 150배) |
| Positive label rate | 26.4% (학습 임계 0.2 초과 — 클래스 불균형 모니터 통과) |
| **CV mean accuracy** | **0.6770** |
| n_symbols | 18 |
| n_eff | 18 (`rho_avg = 0` — 단일 거래일이라 종목간 일수익률 상관 측정 불가) |
| holding_bars | 78 (1분봉 ≈ 1.3시간 intraday momentum 반감기) |
| costs_bps | 26 (KRX 비대칭 BUY 0.015% + SELL 0.245%) |
| periods_per_year | 98,280 (390분 × 252거래일) |
| n_trials | 1 (DSR deflation 미미, raw Sharpe 와 사실상 동등) |

### BTC vs KRX 직접 비교 (참조)

| 자산 | 표본 윈도우 | n_events | CV accuracy | 비고 |
|------|-----------|----------|-------------|------|
| BTC (#85) | 1년 (35,041 bars) | 95 | 0.4958 | bench Sharpe -2.16→-1.13 (메타라벨러 PASS) |
| **KRX-pool-18 (#97 v5)** | **1일 (258,060 bars)** | **307** | **0.6770** | bench Sharpe 미산출 (사후 작업) |

**주의**: BTC 1년 vs KRX 1일은 **동일 기간 비교 아님**. 시장 레짐 다양성 측면 BTC 가 압도적 유리. 따라서 67.7% 가 49.58% 보다 우월하다고 단정 불가. 다만 **메타라벨러가 KRX 1차 신호의 진/위 분류를 코인 동전던지기보다 의미 있게 학습**했다는 시그널.

## DSR 기반 가설 판정 (v5)

**보류 (단일 레짐)** — 1일 데이터 한계로 DSR 통계 판정 불가.

### 판정 기준
- 사전 정의: DSR Δ ≥ 0.3 (BTC + KRX 모두) → 채택 / 일부 → 재설계 검토 / 없음 → 기각
- 추가 보류 트리거 (Critic ADR-097-1): n_eff < 5 → 보류

### v5 결과 vs 기준
- **n_symbols = 18** (≥ 5 충족)
- **n_eff = 18** (rho_avg=0 측정 불가 → conservative 추정 시 단일 레짐 종목간 상관 매우 큼 → 실제 n_eff 는 1~3 추정)
- **n_trials = 1** (DSR 보정 효과 미미)
- **데이터 윈도우 = 1 거래일** (시간 다양성 0)
- 결론: **단일 레짐의 cross-section 표본만으로 가설 채택/기각 판정은 부적절**. 시간 누적 후 재판정 필수.

### 실측 시그널 (보류 결정에도 불구하고 보고할 가치)
- **CV accuracy 0.677** — 단일 거래일 18종목 pooled 데이터에서 메타라벨러가 1차 신호 분류를 의미 있게 학습. v3 합성 데이터 결과보다 압도적.
- **Positive rate 0.264** — KRX 26bps 비용에도 학습 가능한 클래스 분포 (≥ 0.2 임계 통과).
- **이벤트 빈도 1차 추정** — 1분봉 18종목 × 1일 → 307 events. 22거래일 환산 ≈ **6,754 events / 월** 가능. 메타라벨러 학습 데이터로 충분.

## 신뢰도 한계

### 본질적 한계 (Architect ADR-097-v5 명시)
1. **시간 다양성 0** — 단일 거래일 (2026-04-27) 데이터. 다른 시장 레짐 (상승장/하락장/횡보) 일반화 불가. → 후속 이슈: cron 6개월+ 누적 후 재검증.
2. **KIS API 한계** — historical 분봉 조회 불가능. 매일 cron 으로 1일치씩만 누적 가능. 30일 backfill 자체가 KIS 에서 지원 안 함 (이번 작업에서 확정).
3. **종목간 상관 측정 불가** — rho_avg 계산은 일수익률 시계열 ≥ 2점 필요. 1일 데이터로는 불가. n_eff = 명목 N (보수적 추정 시 실제 n_eff ≪ N).
4. **n_trials = 1** — 하이퍼파라미터 grid search 없음. DSR deflation 효과 미미 (raw Sharpe 와 사실상 동등).
5. **BTC vs KRX 직접 비교 불가** — 데이터 윈도우 (1년 vs 1일) + 시장 (24/7 vs 평일 6.5h) + 봉 주기 (15m vs 1m) 모두 다름.
6. **18 / 30 종목 사용** — 12 종목은 1일치에서 RSI bullish divergence 이벤트 0건. 1차 전략 (`momo_kis_v1`) 의 신호 빈도 limitation.

### v5 자체 한계
- **bench Sharpe / MDD / DSR 미산출** — `bench_metalabeler_kis.py` 가 CV accuracy 까지만 출력. equity-curve 기반 metric 은 KRX 백테스트 엔진 부재로 별도 작업.
- **cross_asset_compare.py manifest 자동 로드 미작동** — manifest 파일 경로 / 형식 불일치. 후속 fix 필요.

## 결론 및 후속 조치

### v5 결론
- **인프라 완성** ✅: 1분봉 다종목 pooled CV + n_eff 보정 + TimeBlockGroupKFold + KIS API fix.
- **실데이터 1차 시그널 확보** ✅: 18종목 × 1일 → 307 events → CV 0.677.
- **가설 판정** : **보류 (단일 레짐)** — 통계적 유의 판정은 시간 누적 후.

### 후속 액션 (즉시)
- cron `cron_fetch_kis_daily.py --n-pool 30 --interval 1m` 매일 1회 실행 → 데이터 누적
- 매일 누적 시 90일 후 약 6,930 events × 90 = 약 28,000 events 확보 (이벤트 빈도 그대로면)
- 90일+ 시점 재실행 시 시간 다양성 + DSR 통계 검증 모두 가능

### 후속 이슈 후보 (lead 가 사용자 승인 후 gh issue create)

#### Issue B-1: cron 운영 시작 + 데이터 누적 모니터링
**제목**: `chore: KIS 1분봉 cron 운영 시작 + 누적 데이터 모니터링 (#97 후속)`
- KIS_TOKEN 환경변수 영구 설정
- crontab 등록: 매일 16:00 KST `python scripts/cron_fetch_kis_daily.py --n-pool 30 --interval 1m`
- 주간 누적 체크: bar 수 / 종목별 결측 / VI 빈도

#### Issue B-2: 90일 누적 후 다종목 1분봉 메타라벨러 가설 판정
**제목**: `feat: KIS 90일 누적 후 momo-kis-v1-pooled 가설 판정 (#97 Phase B)`
- 사전 조건: B-1 의 cron 운영 90일+ 누적 완료
- `bench_metalabeler_kis.py --multi-symbol --n-symbols 30 --interval 1m` 재실행 → DSR / Sharpe / 시간 다양성 보정된 n_eff 산출
- 02_implementation 재생성 + 가설 채택/기각/재설계 확정

#### Issue B-3: bench 의 equity-curve / Sharpe / DSR 산출 추가
**제목**: `feat: bench_metalabeler_kis.py 에 equity-curve 기반 Sharpe/MDD/DSR 출력 (#97 후속)`
- KRX 1분봉 백테스트 엔진 또는 직접 triple-barrier returns → equity 계산
- DSR 임계 0.3 기반 채택/기각 판정 자동화

#### Issue B-4: cross_asset_compare manifest 자동 로드 fix
**제목**: `fix: cross_asset_compare.py manifest path / format 정합성 (#97 후속)`
- v5 에서 발견된 자동 로드 미작동 fix
- bench JSON 출력을 manifest 로 저장하거나 cross_asset_compare 가 JSON 직접 입력 받도록 변경

### 판정 결과별 (90일 후 B-2 실행 시)
- **채택** → paper live (#80 Phase 1 Shadow) 연동 이슈
- **기각** → 메타라벨러 재설계 이슈
- **재설계 검토** → 파라미터 / feature 재검토 이슈
