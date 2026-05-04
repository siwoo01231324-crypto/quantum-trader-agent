---
id: 01_plan
type: work-done
name: "#147 VWMA stop-loss/take-profit 구현 계획"
status: active
---

# 000147 — VWMA stop-loss/take-profit 구현 계획

## 목표

#99 의 Variant B (VWMA100 + ema_slope > 0) 에 Iranyi 화자의 1%/7% stop/take 룰을 통합하여 MDD -60% 문제를 직접 해결하고 게이트 통과 여부를 검증한다.

## 단계

### Phase 1 — 핵심 모듈 (이번 사이클)

| 산출물 | 상태 |
|--------|------|
| `src/backtest/risk/stop_take.py` | DONE |
| `tests/test_stop_take.py` (15 cases) | DONE — 15/15 PASS |
| `scripts/bench_vwma_stoploss_variants.py` (B0-B5 스켈레톤) | DONE |
| `docs/work/active/000147-vwma-stoploss/00_issue.md` | DONE |
| `docs/work/active/000147-vwma-stoploss/01_plan.md` | DONE |

### Phase 2 — 풀 backtest (후속 사이클)

- 5년 BTC/ETH 데이터로 B0~B5 전체 실행
- PurgedKFold CV + DSR + PBO + CSCV
- 게이트 판정:
  - DSR >= 0.95
  - PBO <= 0.20
  - OOS MDD < -25%
  - monthly_hit_rate >= 50%

### Phase 3 — 전략 등록 (게이트 통과 시에만)

- `src/backtest/strategies/vwma_cross_v2.py` (AsyncStrategy 프로토콜)
- `docs/specs/strategies/vwma-cross-v2.md` (프론트매터 `type: strategy`)
- orchestrator 등록 + 수익률 시계열 공급

### Phase 3alt — Negative result 문서화 (게이트 미통과 시)

- `docs/research/` 에 정식 negative result 노트 추가
- #99 누적 사례에 병합

## 설계 결정 사항

### intra-bar conservative 가정

같은 bar 에서 stop 과 take 가 모두 hit 가능할 때 stop 을 우선한다.  
근거: anti-overfit 원칙 — 실제 execution 에서는 어떤 쪽이 먼저 hit 되는지 알 수 없으므로 불리한 쪽을 채택.

### gap-down 처리

bar open 이 stop level 아래인 경우 open price 에서 즉시 exit, 추가 slippage 없음.  
근거: 갭 자체가 슬리피지이므로 중복 적용하지 않는다.

### slippage 가정

stop/take 트리거 시 0.05% (5 bps) 추가. signal_exit 시에는 엔진이 별도 적용.

### ATR-based stop (B5)

entry bar 의 t-1 ATR(14) × 2 를 stop distance 로 사용.  
ATR 미계산 구간 (첫 14 bar) fallback: 2%.

## 범위 밖 (이번 사이클)

- 5년 풀 backtest 실행
- strategy 정식 등록
- `src/backtest/strategies/vwma_cross_v2.py` 본 구현
- git commit
