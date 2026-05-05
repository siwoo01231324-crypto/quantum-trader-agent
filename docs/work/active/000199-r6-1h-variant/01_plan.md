---
type: work-done
id: 01_plan
name: "Issue #199 — 구현 계획"
status: active
---

# 구현 계획 — #199 R6 (R4 의 1h 봉 변형)

## 완료 기준 (00_issue.md 참조)

본 PR 머지로 충족 (도구):
- [x] route_r6 + VARIANT_REGISTRY entry
- [x] PaperAdapter r6-switch 분기
- [x] shadow_run_swing.py r6-switch + --interval
- [x] R6 1h 5년 backtest Sharpe ≥ 0.6 (실측 1.201)
- [x] 단위 테스트 16/16 green

머지 후 사용자 행동 (운영):
- [ ] Task Scheduler XML 등록 (1h 간격, 720h)
- [ ] daily_check.ps1 R6 추가
- [ ] 30일 후 R4 vs R6 비교

## 구현 단계 (실제 진행 순서)

1. **route_r6 추가** — R4 와 동일 로직 + 1h-tuned default s2c_params 내장
2. **VARIANT_REGISTRY R6 등록** — bench_regime_switching.py 가 자동으로 R6 검증
3. **R6 1h 5년 backtest 실행** — Sharpe 1.201 확인 → 진행 결정
4. **PaperAdapter r6-switch 분기** — paper_adapter.py 의 _compute_signal 에 추가
5. **shadow_run_swing.py 확장** — --strategy choices, --interval flag, 자동 lookback 4배
6. **R6 단위 테스트 3건 추가** — test_paper_adapter.py
7. **CLI smoke test** — `--strategy r6-switch --max-bars 5` 정상 확인
8. **풀 회귀 + invariants** — 16/16 + 1838+ pass / 0 fail / 167 nodes

## 결정 게이트 (R6 backtest Sharpe)

| 결과 | 결정 |
|---|---|
| < 0.4 | 채택 안 함, 코드만 머지 (운영 X) |
| 0.4-0.6 | 회의적, 사용자 결정 |
| **≥ 0.6** | **운영 진행** ← 실측 1.201 통과 |

## 변경 파일

| 파일 | 역할 |
|---|---|
| `src/backtest/swing/regime_switching.py` | route_r6 + R6 entry |
| `src/backtest/swing/paper_adapter.py` | r6-switch StrategyId + 분기 |
| `scripts/shadow_run_swing.py` | r6-switch CLI + --interval flag |
| `tests/test_paper_adapter.py` | R6 케이스 3건 |
| `src/backtest/swing/.ai.md` | R6 문서화 |
