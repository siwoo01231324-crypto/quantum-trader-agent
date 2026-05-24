---
id: 00_issue
type: work-done
name: "#147 VWMA stop-loss/take-profit 이슈 원문"
status: active
---

# Issue #147 — VWMA + stop-loss/take-profit 통합 backtest

## 원본 이슈 요약

**제목**: feat: VWMA + 추세 필터 + stop-loss/take-profit 통합 backtest (#99 후속)

**목표**: #99 의 Variant B (VWMA100 + ema_slope > 0) 를 baseline 으로, 영상 화자의 1% stop-loss / 7% take-profit (1:7 R:R) 룰을 backtest 모델에 통합하여 재평가. #99 의 가장 큰 약점 (MDD -60%) 직접 공략.

## 배경

- #99 5년 SOP run: best Sharpe B (+0.346), MDD -60%, monthly_hit_rate 40%
- 4 게이트 모두 FAIL — MDD (-60% vs limit -25%), mhr (40% vs 50%)
- MDD 근본 원인: backtest 모델에 stop-loss 미구현
- 영상 화자 명시 룰: "-1% 깨지면 무조건 컷, 7% 익절" (iranyi-vwma-2026-04-27.md 라인 327-335)

## 사전 등록 Variant Matrix

| ID | 구성 | 의미 |
|----|------|------|
| B0 | A (VWMA cross 단독) | #99 baseline 재현 |
| B1 | B0 + stop_loss(1%) | stop-loss 단독 효과 |
| B2 | B0 + take_profit(7%) | take-profit 단독 효과 |
| B3 | B0 + stop(1%) + take(7%) | 영상 R:R 전체 |
| B4 | B (VWMA + ema_slope > 0) + stop(1%) + take(7%) | 본 이슈 핵심 가설 |
| B5 | B4 + 가변 stop (ATR-based, 2*ATR) | ATR 적응형 stop |

## 완료 기준

- [ ] `src/backtest/risk/stop_take.py` 구현 + 단위 테스트
- [ ] 6 variant 일괄 backtest (PurgedKFold + DSR + PBO + sha256 무결성)
- [ ] 게이트 통과 시: strategy 등록, spec 문서, orchestrator 연동
- [ ] 게이트 미통과 시: 정식 negative result 문서화

## 의존성

- 하드 선결: #99 머지 (validation 인프라 + features + bench framework)
- 데이터: lake/ (재fetch 불필요)

## 작업 내역

### 2026-05-04 — 초기 구현 (worker-147)

- `src/backtest/risk/__init__.py` + `stop_take.py` 생성
  - `StopTakeConfig`, `StopTakeResult`, `simulate_stop_take()` 구현
  - intra-bar conservative 가정 (stop > take 동시 hit 시 stop 우선)
  - gap-down/gap-up 처리 (open price exit, slippage 없음)
  - slippage 0.05% (5 bps) 가정
- `tests/test_stop_take.py` — 15개 케이스 전부 GREEN
- `scripts/bench_vwma_stoploss_variants.py` — B0~B5 variant matrix 스켈레톤 + dry-run
- `docs/work/active/000147-vwma-stoploss/01_plan.md` 작성
