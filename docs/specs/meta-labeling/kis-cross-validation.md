---
type: research
id: kis-cross-validation
name: "메타라벨러 KRX × BTC 교차 검증 Phase A"
status: active
created: 2026-04-27
sources:
  - "https://github.com/siwoo01231324/quantum-trader-agent/issues/97"
  - "https://github.com/siwoo01231324/quantum-trader-agent/issues/85"
  - "https://github.com/siwoo01231324/quantum-trader-agent/issues/96"
  - "Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio."
---

# 메타라벨러 KRX × BTC 교차 검증 Phase A

## 가설

메타라벨러(LightGBM 2차 필터)는 BTC뿐 아니라 KRX 전략(`momo-kis-v1`)에서도 PR-AUC / DSR 개선 효과를 보인다.
두 자산군에서 모두 DSR 개선 ≥ 0.3이면 가설 채택, 1개만이면 재설계 검토, 0개이면 기각.

## 메타라벨러 가설 (세부)

- **BTC** (`momo-btc-v2`, Binance Futures 15분봉): 기존 구현 (#85) — PR-AUC / DSR 기준점 제공.
- **KRX** (`momo-kis-v1`, KIS 005930 15분봉): 비용 구조 상이 (BUY 1.5bps + SELL 24.5bps = 26bps 총비용).
  KRX `periods_per_year=6552` (26 bars × 252 거래일), `holding_bars=26` (1 거래일 = 26 × 15분).

## 데이터 가용성

KIS API 당일+30일 제약 → Phase A에서 기대 이벤트 ≈ 2개 (BTC 95 evt 기준 비례).
30 이벤트 미만으로 통계적 판정 불가 → **Phase A 결론: 판정 보류**.

## 결론 (Phase A 보류)

- 파이프라인 구조 검증 완료: `src/ml/pipelines/kis_cross_validation.py` E2E 동작 확인.
- 교차 비교 모듈 구현 완료: `src/ml/reporting/cross_asset_compare.py` (PR-AUC, DSR, Sharpe 비교).
- 실데이터 통계 판정은 Phase B(후속 이슈)에서 KIS 분봉 3개월 이상 누적 후 재실행.

## Phase B 계획

1. KIS 분봉 3개월 이상 누적 (`scripts/cron_fetch_kis_daily.py` 매일 1회 실행).
2. `scripts/bench_metalabeler_kis.py` 재실행 → 실데이터 이벤트 수 확인.
3. `scripts/cross_asset_compare.py` 재실행 → Phase B 판정 (`02_implementation.md` 갱신).
4. 채택 시: 메타라벨러 KRX paper live 검증 (#80 Phase 1 Shadow 연동).

## 출처

- 이슈 #97: 본 작업 (KRX × BTC 교차 검증)
- 이슈 #85: 메타라벨러 BTC 기준점 구현
- 이슈 #96: KIS 분봉 fetcher + `momo-kis-v1` 전략
- Bailey & López de Prado (2014): Deflated Sharpe Ratio 공식
