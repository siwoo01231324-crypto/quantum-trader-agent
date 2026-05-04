---
type: work-done
id: 01_plan
name: "Issue #175 — 구현 계획"
status: active
---

# 구현 계획 — #175 S2c Paper Shadow Run

## Phase A: Shadow Run (30일 운영)

### 목표
`s2_donchian_voltarget` 및 `s4_funding_carry` 전략을 paper broker 에 연결하여 30일간 BTCUSDT 4h bar 기반으로 실시간 신호 → 주문 → WAL 기록 루프를 운영한다.

### 아키텍처 흐름

```
Binance REST (4h candles)
        │
        ▼
PaperAdapter.on_bar(ohlcv_row)
        │  ← 신호 계산: s2_donchian_voltarget / s4_funding_carry
        ▼
PaperBroker.place_order(OrderRequest)
        │  ← WAL write (order_submitted / order_filled)
        ▼
WAL JSONL (logs/shadow/{run_id}/wal.jsonl)
```

### 운영 파라미터 (사전등록 frozen)
| ID | 전략 함수 | 파라미터 |
|----|-----------|---------|
| P1 | `s2_donchian_voltarget` | entry=20, exit=10, vol_target=0.15, vol_lb=60 |
| P2 | `s2_donchian_voltarget` | entry=30, exit=20, vol_target=0.10, vol_lb=30 |
| P3 | `s4_funding_carry` | threshold=-0.005% |

- 심볼: BTCUSDT (Binance Futures USDT-M)
- 타임프레임: 4h
- 초기 잔고: 100,000 USDT (paper)
- WAL 위치: `logs/shadow/{run_id}/wal.jsonl`
- 실행 방법: `python scripts/shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT --exchange binance-futures`

### Halt Trigger (R1-R5)
| ID | 조건 | 액션 |
|----|------|------|
| R1 | MDD > 25% | KillSwitch 트립 → 운영 중단 |
| R2 | 일일 손실 > 5% | 당일 신호 무시 |
| R3 | 연속 3일 손실 | 알람 발송 |
| R4 | WAL 쓰기 실패 | KillSwitch 트립 → 즉시 중단 |
| R5 | 거래소 연결 끊김 3회 이상 | 알람 발송 |

## Phase B: Validation (30일 종료 후)

### 목표
WAL → daily PnL → 30거래일 Sharpe 산출 후 backtest Sharpe 와 비교.

### 분석 절차
1. `shadow_report.py --wal logs/shadow/{run_id}/wal.jsonl --verify-exit`
2. Sharpe 비교 4조건 검증:
   - 동일 데이터 소스: binance_futures_usdtm
   - 동일 슬리피지 모델: zero_slip
   - 동일 수수료: 5 bps
   - 동일 사이징: resolve_size_v1
3. 괴리 계산: `|paper_sharpe - backtest_sharpe|`
4. 채택 기준: 괴리 < 50% AND paper Sharpe ≥ 0.40

### 판정 기준표
| 조건 | 판정 |
|------|------|
| paper Sharpe ≥ 0.40 AND 괴리 ≤ 0.3 | 채택 후보 (Phase C 진행) |
| paper Sharpe ≥ 0.40 AND 괴리 > 0.3 | 조건부 (슬리피지 모델 재검토) |
| paper Sharpe < 0.40 | Negative result (후속 변형 실험) |

## 구현 파일 목록

| 파일 | 설명 | 상태 |
|------|------|------|
| `src/backtest/swing/paper_adapter.py` | 전략 → paper broker 어댑터 | 완료 |
| `tests/test_paper_adapter.py` | TDD 단위 테스트 | 완료 |
| `scripts/shadow_run_swing.py` | 4h bar 기반 shadow run CLI | 완료 |
| `docs/work/active/000175-paper-shadow-s2c/00_issue.md` | 이슈 기록 | 완료 |
| `docs/work/active/000175-paper-shadow-s2c/01_plan.md` | 계획 문서 | 완료 |

## 의존성 그래프

```
#80 (paper broker) ──┐
#172 (S2c strategy) ─┤──▶ #175 (paper shadow run)
shadow_report.py ────┘
```

## 실행 스케줄 (cron 예시)
```cron
# 매 4시간마다 shadow run (UTC 기준 4h bar 종료 후)
0 1,5,9,13,17,21 * * * cd /path/to/quantum-trader-agent && python scripts/shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT --exchange binance-futures --max-iterations 1 >> logs/shadow/cron.log 2>&1
```

## 출처
- `docs/background/29-paper-to-live-protocol.md` — 비교 4조건
- López de Prado AFML §13 — paper-to-live 괴리 분석
- PR #172 bench_output_iter5_grid.json — W1 backtest Sharpe 0.814
