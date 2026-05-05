---
type: work-done
id: 02_implementation
name: "Issue #143 — 구현 산출물 + 30일 운영 SOP"
status: active
---

# 구현 완료 — #143 Phase 1 Shadow Paper 데몬

## 산출물

### 코드

| 파일 | 변경 |
|---|---|
| `src/backtest/swing/paper_adapter.py` | `StrategyId` 에 `r4-switch` 추가, `AdapterConfig.return_lookback` 추가, `_compute_signal` 에 r4-switch 분기 추가 (route_r4 호출) |
| `src/backtest/swing/regime_switching.py` | `GaussianHMMRegime` import 를 module-top 에서 route_r2/r3/r5 함수 내부로 이동 (lazy) — threshold-only 경로 (r0/r1/r4) 가 hmmlearn 없이 동작 |
| `src/ml/regime/__init__.py` | HMM exports lazy-load 화 (`__getattr__`) — Python 3.14 처럼 hmmlearn 빌드 안 되는 환경에서도 ThresholdRegime 만 사용 가능 |
| `scripts/shadow_run_swing.py` | `--strategy choices` 에 `r4-switch` 추가, `--return-lookback` 플래그 추가, AdapterConfig 에 전달 |
| `tests/test_paper_adapter.py` | R4 테스트 3종 추가 (entry / no-signal / round-trip) |

### 테스트 결과

```
pytest tests/test_paper_adapter.py -v
12 passed in 1.62s
```

기존 9 케이스 (s2c-voltarget / s4-funding) 회귀 없음 + R4 신규 3 케이스 그린.

### 스모크 결과

```bash
python scripts/shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT --max-bars 5 --history-bars 250
```

- bars_processed=5
- WAL 디렉토리 생성됨 (logs/shadow/{run_id}/)
- 신호 0건 (R4 가 현재 시점에서 bullish/funding-negative regime 모두 미발동 → 정상)
- 예외 없음

### check_invariants 결과

```
[check_invariants] 통과 (166 노트 검증)
```

---

## 30일 운영 SOP (사용자 머지 후 행동)

### 데몬 환경 (확정)

- **머신**: 사용자 본인 PC (Windows 11) — 메모리 `project_30day_daemon_hosting.md` 참조
- **거래소**: Binance Futures USDT-M (read-only public API, 인증 키 불필요)
- **종목**: BTCUSDT 단일 (KRX 005930 등은 #133 KIS 모의계좌 트랙으로 분리)
- **전략**: r4-switch (#173 BEST, 5년 OOS Sharpe 1.218)
- **타임프레임**: 4h
- **사이클**: 매 4h bar 종료 후 cron 1회 실행 (UTC 01/05/09/13/17/21시)

### Phase A — 환경 준비 (5분, 1회만)

```powershell
cd D:\project\quantum-trader-agent
pip install -e .
pip install hmmlearn  # Python 3.11 환경. 3.14 는 lazy-load 로 회피 가능.
mkdir logs\shadow -ErrorAction SilentlyContinue
```

### Phase B — 1회 수동 검증 (5분)

```powershell
$env:PYTHONUTF8=1
python scripts\shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT --max-bars 5 --log-level INFO
```

기대: `logs\shadow\<UTC타임스탬프>\` 생성. 무에러.

### Phase C — Task Scheduler 등록 (10분)

**`run_id` 고정 핵심 (#143)**: 모든 cron 실행이 같은 WAL 디렉토리 (`logs/shadow/phase1-r4-switch-BTCUSDT/`) 를 공유한다. 매 시작 시 WAL replay 로 broker 포지션·잔고 복원. 따라서 `--run-id` 인자는 명시하지 않거나, 명시 시 `phase1-r4-switch-BTCUSDT` 같은 고정 ID 사용.

```powershell
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <Repetition><Interval>PT4H</Interval><Duration>P30D</Duration></Repetition>
      <StartBoundary>2026-05-05T01:00:00</StartBoundary>
    </CalendarTrigger>
  </Triggers>
  <Actions>
    <Exec>
      <Command>python</Command>
      <Arguments>D:\project\quantum-trader-agent\scripts\shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT --exchange binance-futures --max-bars 1 --log-level INFO</Arguments>
      <WorkingDirectory>D:\project\quantum-trader-agent</WorkingDirectory>
    </Exec>
  </Actions>
  <Settings><Enabled>true</Enabled><ExecutionTimeLimit>PT10M</ExecutionTimeLimit></Settings>
</Task>
"@
$xml | Out-File -Encoding Unicode shadow_swing_task.xml
schtasks /create /xml shadow_swing_task.xml /tn "QuantumTrader\ShadowSwing143" /f
```

확인:
```powershell
schtasks /query /tn "QuantumTrader\ShadowSwing143" /v
```

수동 실행 테스트:
```powershell
schtasks /run /tn "QuantumTrader\ShadowSwing143"
```

**상태 복원 동작 검증** — 1차 실행 후 2차 실행 시 로그 확인:
```
1차: "Fresh broker: WAL not found, starting from initial_balance=100000"
2차: "WAL replay: restored broker state from logs/.../wal.jsonl (positions=N)"
     (포지션 보유 시) "Adapter state restored: in_position=True symbol=BTCUSDT qty=... entry=..."
```

### Phase D — 일일 점검 (5분/일)

**WAL 경로 고정** (`logs\shadow\phase1-r4-switch-BTCUSDT\wal.jsonl`):

```powershell
$wal = "logs\shadow\phase1-r4-switch-BTCUSDT\wal.jsonl"

# 1. 데몬 alive 확인
schtasks /query /tn "QuantumTrader\ShadowSwing143" /v | findstr "Last Run"

# 2. WAL 무결성 (corruption=0 기대)
python -c "from src.live.wal import replay; from pathlib import Path; e,c=replay(Path('$wal')); print(f'events={len(e)} corruptions={len(c)}'); assert len(c)==0"

# 3. 일일 리포트 생성
$env:PYTHONUTF8=1
python scripts\shadow_report.py --wal $wal --verify-exit --out "logs\shadow\daily_$(Get-Date -Format yyyyMMdd).md"

# 4. 현재 포지션·잔고 확인 (실시간)
python -c "
from src.execution.paper_broker import PaperBroker
from src.execution.mock_matching import MockMatchingEngine
from src.ops.kill_switch import KillSwitch
import asyncio
b = PaperBroker.from_wal('$wal', KillSwitch(), MockMatchingEngine())
async def show():
    bal = await b.get_balances()
    pos = await b.get_positions('BTCUSDT')
    print(f'Balances: {bal}')
    print(f'Positions: {pos}')
asyncio.run(show())
"
```

### 결과 데이터 위치 (사용자가 매일 보는 곳)

| 무엇 | 어디 | 형식 | 언제 갱신 |
|---|---|---|---|
| **모든 거래 기록 (raw)** | `logs\shadow\phase1-r4-switch-BTCUSDT\wal.jsonl` | JSONL (한 줄당 1 이벤트) | 매 신호 발생 시 (entry/exit) |
| **일일 마크다운 리포트** | `logs\shadow\daily_YYYYMMDD.md` | Markdown (PnL/Sharpe/거래수) | 매일 점검 명령 실행 시 |
| **데몬 실행 로그** | Task Scheduler 로그 (`Get-WinEvent -LogName 'Microsoft-Windows-TaskScheduler/Operational'`) | Windows event log | 매 cron 실행 시 |
| **30일 최종 리포트** | `logs\shadow\final_phase1.md` | Markdown (Phase 1 exit criteria 4 조건 판정) | Phase E 실행 시 1회 |

WAL 한 줄 예시 (JSONL):
```json
{"ts": "2026-05-05T05:00:01Z", "event_type": "order_filled", "payload": {"client_order_id": "r4-switch-entry-a3f...", "symbol": "BTCUSDT", "side": "BUY", "fill_qty": "0.123456", "fill_price": "98765.4", "fees": "9.876"}}
```

일일 리포트 예시 컬럼: `날짜 / 신규 entry / 신규 exit / 일일 PnL (USDT) / 누적 PnL / Sharpe 30d`

### Phase E — 30일 후 채택/기각 SOP

#### 판정 기준 (Phase 1 exit criteria, 29-paper-to-live-protocol §7.1)

| 지표 | 기준 | 판정 |
|---|---|---|
| Paper Sharpe vs Backtest Sharpe 괴리 | ≤ 50% (즉 paper Sharpe ≥ 0.609) | 채택 후보 |
| Paper monthly hit rate | ≥ 0.40 | 채택 |
| Paper MDD | ≤ 14.5% (backtest -9.7% × 1.5 buffer) | OK |
| Paper Sharpe | < 0.609 | Negative result |

R4 backtest Sharpe = 1.218 → 50% 보존 임계치 = 0.609.

#### 채택 절차

```powershell
# 1. 30일 누적 WAL → Sharpe / MDD / mhr 계산
python scripts\shadow_report.py --wal logs\shadow\<run_id>\wal.jsonl --verify-exit

# 2. 4 조건 비교
python scripts\shadow_report.py --wal logs\shadow\<run_id>\wal.jsonl --compare-backtest <backtest_wal> --threshold 0.609

# 3. 통과 시
#    - docs/specs/strategies/r4-switch.md 채택 기록
#    - 백서 §11-5 Phase 1 가동 시작일·누적일·결과 갱신
#    - #138 (Whitepaper v0.2) 작업 진입
#    - #133 KIS Phase 2 + #107 Phase 3 결정 게이트 통과
```

#### 기각 절차

```powershell
# 1. WAL 아카이브
Move-Item logs\shadow\<run_id> docs\work\done\143-negative\

# 2. docs/specs/strategies/r4-switch.md 에 negative result 기록
# 3. 백서 §11-5 negative result 명시
# 4. 후속 실험 이슈 생성 (예: r4 파라미터 재조정, 다른 R 변형)
```

### Phase F — Halt Trigger R1-R5 (실전 위험 한계)

| ID | 트리거 | 자동 액션 | 담당 |
|---|---|---|---|
| R1 | MDD > 15% (R4 backtest -9.7% × 1.5x) | KillSwitch.trip() → 프로세스 종료 | `src/ops/kill_switch.py` ✅ |
| R2 | 일일 손실 > 3% | 당일 신호 무시 | (별도 구현 필요) |
| R3 | 연속 3일 손실 | Telegram 알람 | `scripts/telegram_alert.py` (#150 머지) |
| R4 | WAL 쓰기 실패 | KillSwitch.trip() | `src/live/wal.py` ✅ |
| R5 | Binance API 연결 끊김 3회+ | Telegram 알람 + 재시도 | (별도 구현 필요) |

R1/R4 는 코드 완성. R2/R3/R5 는 30일 운영 중 incident 발생 시 추가 구현 권장 (본 PR 범위 밖).

---

## 사용자 행동 항목 (머지 후)

1. **PC 24/7 가동 정책 확정** — 절전 끄기, Windows Update 자동재부팅 시간 야간으로 설정
2. **Phase A** 환경 준비 (5분)
3. **Phase B** 수동 검증 (5분)
4. **Phase C** Task Scheduler 등록 (10분)
5. **30일간 Phase D** 매일 5분 점검
6. **30일 후 Phase E** SOP 따라 채택/기각 결정
7. **이슈 #143 닫기** (결과 기록 후)

총 사용자 행동 시간: **머지 후 셋업 20분 + 30일 동안 매일 5분 = 약 3-4시간 / 30일**.

---

## 향후 후속 (이슈 분리)

- **#138 (Whitepaper v0.2)** — Phase 1 결과 활용 → 본 이슈 채택 후 트리거
- **#133 (KIS Phase 2)** — KIS 모의계좌 4주 운영 (별도 트랙, 사용자 행동 4건 게이트)
- **#152 (KIS 1분봉 cron)** — KIS 데이터 누적 (#133 의 KIS API key 공유)
- **#107 (Phase 3 Live Pilot)** — #133 통과 후 실자금 5%
