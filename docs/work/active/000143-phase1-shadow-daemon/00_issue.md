---
type: work-done
id: 00_issue
name: "Issue #143 — Phase 1 Shadow Paper 데몬 실가동 + 30거래일 누적"
status: active
---

# chore: Phase 1 Shadow Paper 데몬 실가동 + 30거래일 누적 운영

## 배경
#80 (PaperBroker + Phase 1) 머지 완료 — 코드는 준비됨. 하지만 **현재 시점(2026-04-27) 데몬이 실제 가동 중이 아님.** logs/shadow/ 디렉토리 부재로 확인됨. v0.2 (#138) 발행 + Phase 2 진입 (#105) 의 전제 조건.

## Phase / 월 10% 컨텍스트
- Phase 1 그림자 운영의 실가동 자체 — 본 이슈 완료 = Phase 1 진입 완료 (그동안은 코드만 준비됨)
- 월 10% 공격 정책 도입 결정 전 실가동 안정성 검증 필수

## AC
- [ ] `scripts/shadow_run.py --symbols BTCUSDT,005930 --duration 24h` 자동 재기동 운영 시작
- [ ] WAL 파일 (`logs/shadow/{run_id}/wal.jsonl`) 일자별 누적 30 거래일
- [ ] 자동 재시작 시스템 (윈도우 작업 스케줄러 또는 systemd-equivalent)
- [ ] `scripts/shadow_report.py --verify-exit` 30일 누적 후 Phase 1 exit criteria 4 조건 검증 통과
- [ ] 비교 4조건 강제: 동일 데이터 소스 / 동일 슬리피지 / 동일 수수료 / 동일 사이징 (29-paper-to-live-protocol §7.1)
- [ ] Phase 1 가동 시작일·누적일 백서 §11-5 갱신
- [ ] 메타라벨러 ON Sharpe vs OFF Sharpe 차이 실측 (목표: 차이 ≤ 0.3)

## 의존성·참고
- 선행: #80 (머지 완료), #94 메타라벨러 활성화 (머지 완료), #106 Binance loader (머지 완료), #96 KIS 분봉 (머지 완료) — 모두 ✅
- 후행: #138 (v0.2 본 가동 데이터 활용), #105 (Phase 2 진입 결정)
- ⚠️ 주의: PC 24/7 가동 필요 — Phase 3+ 에서는 OCO 사전 등록 (#127) 으로 보호되지만 Phase 1 은 PC 다운 시 데이터 갭 발생
- 백서 §11-5, 부록 B-4

## 작업 내역

### 본 PR 의 범위 (2026-05-05)

**도구 + 매뉴얼** 를 머지. **30일 실가동은 사용자 PC 행동 게이트** (Task Scheduler 등록) → 머지 후 사용자 손.

#### 코드 변경

- `src/backtest/swing/paper_adapter.py`: `r4-switch` 전략 분기 + `return_lookback` 파라미터 추가. 5년 OOS bench 1등 (#173, Sharpe 1.218 / MDD -9.7%) variant 를 paper trading 에 연결.
- `src/backtest/swing/regime_switching.py`: `GaussianHMMRegime` import 를 함수 내부 lazy 로 이동 → `route_r0/r1/r4` 가 hmmlearn 없이 동작.
- `src/ml/regime/__init__.py`: `__getattr__` lazy-load 로 변경 → Python 3.14 처럼 hmmlearn prebuilt wheel 부재 환경에서도 ThresholdRegime 만 import 가능.
- `scripts/shadow_run_swing.py`:
  - `--strategy` choices 에 `r4-switch` 추가 + `--return-lookback` CLI flag.
  - **`run_id` 기본값을 timestamp → `phase1-{strategy}-{symbol}` 고정**. 30일 cron 운영 시 WAL 디렉토리 공유 → 매 cron 시작 시 WAL replay 로 broker 포지션·잔고 복원.
  - PaperAdapter 도 `broker.get_positions(symbol)` 으로 `_in_position` 동기화 → 청산 신호 누락 방지.

#### 테스트

- `tests/test_paper_adapter.py`: R4 시나리오 3건 (entry/no-signal/round-trip) + 상태 복원 1건 추가 → **13/13 green**.
- `tests/test_regime_hmm.py`: `pytest.importorskip("hmmlearn")` 추가 (Python 3.14 환경에서 깔끔히 1 skip).
- 풀 회귀: **1835 pass, 11 skip, 0 fail** (회귀 0).

#### 운영 매뉴얼

- `docs/work/active/000143-phase1-shadow-daemon/02_implementation.md` 신규:
  - Phase A: 환경 준비 (`pip install -e .`, hmmlearn, 디렉토리)
  - Phase B: 1회 수동 검증 (`--max-bars 5` smoke)
  - Phase C: Task Scheduler XML 등록 (`QuantumTrader\ShadowSwing143`)
  - Phase D: 일일 점검 5분 — WAL 무결성 / 데몬 alive / 일일 리포트 생성 / 현재 포지션 확인
  - Phase E: 30일 후 채택/기각 SOP — paper Sharpe vs backtest 1.218 의 50% 보존 임계 0.609
  - Phase F: Halt trigger R1-R5 (R1/R4 코드 완성, R2/R3/R5 인시던트 시 추가 구현 권장)

#### `.ai.md` 갱신

- `src/backtest/swing/.ai.md`: `paper_adapter` 섹션에 r4-switch + 12 → 13 테스트 + Phase 1 #143 default 명시. `regime_switching` 의 lazy-import 패턴 신설.
- `src/ml/regime/.ai.md`: lazy-load 패턴 + Python 3.14 호환 명시.

### AC 갱신 (2026-05-05)

원 AC 는 #80 머지 시점 기준 (`shadow_run.py --symbols BTCUSDT,005930`) 으로 stale. 다음과 같이 정정:

| 원 AC | 정정 후 | 본 PR 충족? |
|---|---|---|
| `shadow_run.py --symbols BTCUSDT,005930 --duration 24h` | `shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT` (KRX 005930 은 #133 KIS 트랙 분리) | ✅ 도구 ready |
| WAL 일자별 30 거래일 누적 | (사용자 30일 운영 후) | ⏳ 30일 가동 게이트 |
| 자동 재시작 (Task Scheduler) | Task Scheduler XML 매뉴얼 제공 | ✅ 매뉴얼 ready |
| `shadow_report.py --verify-exit` exit criteria 4 조건 | 동일 (Phase E SOP) | ⏳ 30일 후 |
| 비교 4조건 강제 | 동일 (29-paper-to-live-protocol §7.1) | ⏳ 30일 후 |
| 백서 §11-5 갱신 | 동일 | ⏳ 30일 후 |
| 메타라벨러 ON/OFF Sharpe 차이 | **R4 vs R0 (bare S2c) Sharpe 비교** 로 갱신 (#173 R0 Sharpe 0.825 / R4 1.218 backtest 기준, 차이 ≤ 0.3 목표) | ⏳ 30일 후 |

→ 본 PR 머지로 **도구·매뉴얼 부분 (3건)** 충족. **운영 부분 (4건)** 은 사용자 30일 가동 후 결과로 충족.

### 사용자 머지 후 행동 (필수)

1. `git pull origin master`
2. `pip install -e .` + `pip install hmmlearn` (Python 3.11 환경. 3.14 는 lazy-load 우회.)
3. `02_implementation.md` Phase B 수동 검증 (5분)
4. `02_implementation.md` Phase C Task Scheduler 등록 (10분)
5. 매일 Phase D 점검 (5분/일 × 30일)
6. 30일 후 Phase E SOP 따라 채택/기각 결정
7. 결과 기록 + 이슈 닫기

운영 머신 = **로컬 PC** (메모리 `project_30day_daemon_hosting.md` 확정).
