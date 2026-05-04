---
type: work-done
id: 00_issue
name: "Issue #175 — S2c Paper Shadow Run"
status: active
---

# feat: 1-month paper trading shadow run for S2c (vol-target Donchian, #172 후속)

## 사용자 관점 목표
PR #172 의 W1 = S2c (Donchian + vol-target) 가 5년 BTC@4h backtest 에서 **3/5 게이트 통과** (Sharpe 0.814, MDD -18.7%, mhr 0.51) — real-money 검증 자격 있음. 5년 backtest 결과를 1개월 실시간 **paper trading** (#80 paper broker) 으로 검증하여 **net Sharpe vs backtest Sharpe 괴리** 측정.

## 배경 — Backtest vs 실전 괴리의 본질
- 5년 backtest 는 **slippage 0** 가정 (단순 next-bar entry)
- 실전 = 시장 충격 + bid-ask spread + 거래소 latency + 부분 체결 + funding cost
- López de Prado AFML §13: **실전 Sharpe = backtest Sharpe × 0.5 ~ 0.7** 일반적 (slippage 충격)
- W1 backtest 0.814 → 실전 0.40~0.57 예상 (여전히 중간급)

## 가설
1. W1 paper Sharpe ≥ 0.40 (backtest 의 50% 보존) → 채택 가능
2. W1 paper monthly hit rate ≥ 0.40 (backtest 0.51 의 80%) → mhr 게이트 보존
3. 실전 MDD ≤ 25% (backtest -18.7% × 1.3 buffer = -24%)

## 활용 가능 인프라 (#80 머지)
- `src/live/feed.py::BinancePublicFeed` — aggTrade WS feed
- `src/execution/paper_broker.py::PaperBroker` — paper broker
- `scripts/shadow_run.py` — Phase 1 Shadow Live Loop CLI
- `scripts/shadow_report.py` — WAL → daily PnL → Sharpe 비교 4조건
- `src/backtest/swing/strategies.py::s2_donchian_voltarget` (PR #172 머지 후)

## Variant Matrix (사전등록 frozen)
| ID | 구성 |
|----|------|
| P1 | W1 = s2_donchian_voltarget (entry=20, exit=10, vol_target=0.15) — PR #172 winning |
| P2 | W1 grid optimum (entry=30, exit=20, vol_lb=30, vol_target=0.10) — PR #172 iter5 in-sample best |
| P3 | S4 funding carry (long-only, threshold -0.005%) — Sharpe 0.961 baseline |

## Phase
1. **Phase A (Shadow Run, 30일)**:
   - 매일 4h bar 종료 후 신호 산출 → paper broker 에 entry/exit submit
   - WAL 로 모든 trade 기록
   - 거래소: Binance Futures USDT-M (testnet 또는 mainnet read-only + paper)
2. **Phase B (Validation, 30일 종료 후)**:
   - WAL parse → daily PnL → 30 거래일 Sharpe
   - 4 조건 비교 (동일 데이터 소스/슬리피지/수수료/사이징)
   - net Sharpe vs backtest Sharpe 괴리 분석

## 완료 기준
- [ ] Paper adapter 구현 + 단위 테스트
- [ ] 30일 shadow run 완료 (BTCUSDT 또는 ETHUSDT)
- [ ] WAL 기반 daily PnL → Sharpe 산출
- [ ] **Backtest Sharpe vs Paper Sharpe 괴리 < 50%** 시 채택 후보 / 그 이상 시 negative result
- [ ] AC 6 (PR #172 W1 backtest 재현) 자동 검증
- [ ] R1-R5 halt trigger 평가 (실전 위험 한계)
- [ ] 정식 보고서

## 의존성
- **하드 선결**: PR #172 머지 (S2c, S4 strategy 코드)
- **하드 선결**: #80 머지 (paper broker, shadow_run, shadow_report) — 이미 머지
- 권장: #105 (Phase 2 KIS 모의계좌) 의 live framework 패턴

## 범위 밖 (별도 후속)
- Real-money (실자금) 진입 — paper 만
- Multi-strategy 운영 — 단일 strategy 만
- Risk module register_strategy_returns 통합 — 보고서에서 수동 호출

## 출처
- PR #172 02_implementation.md (W1 = S2c 결과)
- López de Prado AFML §13 (paper-to-live 협의)
- `docs/background/29-paper-to-live-protocol.md`

## 작업 내역

| 날짜 | 내용 |
|------|------|
| 2026-05-04 | 이슈 분석 및 작업 계획 수립 |
| 2026-05-04 | `src/backtest/swing/paper_adapter.py` 구현 |
| 2026-05-04 | `tests/test_paper_adapter.py` TDD 작성 |
| 2026-05-04 | `scripts/shadow_run_swing.py` 스켈레톤 작성 |
| 2026-05-04 | 문서 (`00_issue.md`, `01_plan.md`) 작성 |
