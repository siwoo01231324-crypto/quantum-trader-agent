# feat: bench_metalabeler_kis.py 에 equity-curve 기반 Sharpe/MDD/DSR 출력 (#97 후속)

## 사용자 관점 목표
#97 v5 의 \`bench_metalabeler_kis.py\` 가 CV mean accuracy 까지만 출력 → DSR 임계 0.3 자동 판정 불가. equity-curve 기반 Sharpe/MDD/DSR 산출 추가.

## 배경
#97 v5 결과 02_implementation.md 의 한계:
> "bench Sharpe / MDD / DSR 미산출 — equity-curve 기반 metric 은 KRX 백테스트 엔진 부재로 별도 작업"

KRX 1분봉 백테스트 엔진이 없어 v5 에서 미산출. triple-barrier returns → equity 직접 계산하는 우회 경로로 해결 가능 (BTC #85 bench 패턴 참조).

## 완료 기준
- [x] `bench_metalabeler_kis.py` 출력 JSON 에 추가:
  - sharpe_off / sharpe_on (annualized, periods_per_year=98280)
  - mdd_off / mdd_on
  - sortino_off / sortino_on
  - dsr_off / dsr_on / dsr_delta
- [x] OFF 경로: 모든 RSI bullish divergence 신호 → triple-barrier returns
- [x] ON 경로: 메타라벨러 win_probability ≥ threshold 만 → triple-barrier returns
- [x] DSR 임계 (≥ 0.3) 기반 자동 판정 출력 (참고용 — n_eff < 5 시 보류 강제)
- [x] 단위 테스트: `tests/ml/test_bench_kis_equity.py` (합성 데이터 → equity 산출 검증)

## 의존성
- **#97 머지 필수** (run_kis_pipeline_pooled, scoring.py)
- BTC \`bench_metalabeler_btc.py::_run\` 패턴 참조 (\`backtest.engine.run_backtest\` 제외하고 triple-barrier 결과만 활용)

## 주의사항
- **KRX 백테스트 엔진 부재**: 본 이슈에서 풀 백테스트 엔진 만들지 않음. triple-barrier 결과만으로 simplified equity 계산.
- **costs_bps=26 KRX 비대칭** 적용
- **n_trials=1 시 DSR ≈ raw Sharpe** 명시

---

## 작업 내역
<!-- /remind-issue 와 작업 진행 시 여기에 누적 -->
