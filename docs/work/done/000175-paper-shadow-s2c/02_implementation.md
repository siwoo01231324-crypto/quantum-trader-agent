---
type: work-done
id: 02_implementation
name: "Issue #175 — 데몬 셋업 + Smoke 결과 + 30일 운영 매뉴얼"
status: active
---

# 구현 완료 — #175 S2c Paper Shadow Run

## Phase A: 데몬 셋업

### PID / 로그 / WAL 경로 정의

| 항목 | 경로 |
|------|------|
| WAL | `logs/shadow/{run_id}/wal.jsonl` |
| 실행 로그 | `logs/shadow/{run_id}/run.log` |
| PID 파일 | `logs/shadow/{run_id}/daemon.pid` |
| 리포트 | `logs/shadow/{run_id}/report.md` |
| smoke-synthetic 결과 | `logs/shadow/smoke-synthetic-001/` |

`run_id` 형식: `YYYYMMDDTHHMMSSZ` (UTC) — `shadow_run_swing.py`가 자동 생성.

### 버그 수정 (이번 사이클)

- `shadow_run_swing.py`: `MarketState(symbol=..., bid=..., ...)` → `MarketState(tick=Tick(...))` 로 수정. `base.py` 의 실제 시그니처에 맞게 정렬.
- `shadow_report.py`: `sys.path` 누락으로 `ModuleNotFoundError` 발생 → 스크립트 상단에 repo root 삽입 패치 추가.

---

## Phase B: 1h Smoke 결과 (Synthetic Feed)

거래소 API key 미제공 환경에서 synthetic OHLCV (n=150 bars, 4h 단위 동등) 로 smoke 실행.

| 항목 | 결과 |
|------|------|
| 전략 | s2c-voltarget (entry=20, exit=10, vol_target=0.15, vol_lb=60) |
| 심볼 | BTCUSDT (mock feed) |
| 처리 bar 수 | 150 |
| 신호 발생 | 4건 (진입 2 + 청산 2) |
| WAL 레코드 | 8건 (order_submitted 4 + order_filled 4) |
| WAL 무결성 | 이상 없음 (corruption=0) |
| 파이프라인 | WAL → fills → daily PnL → report 정상 |
| Sharpe | nan (1 거래일 — 통계적 의미 없음, 파이프라인 검증 목적) |
| 거래소 latency | 해당 없음 (mock feed; 실거래소 연결 시 별도 측정 필요) |
| 누적 PnL | -2070 USDT (mock fill price 기반, 통계적 의미 없음) |

실거래소 연결 시 `--history-bars 500` 으로 warmup 후 매 4h bar 마다 `--max-bars 1` 로 cron 실행.

---

## Phase C: 30일 운영 Cron 매뉴얼

### Windows — Task Scheduler (권장)

```powershell
# 1. 스케줄러 XML 파일 생성 (shadow_swing_task.xml)
# 매 4h마다 실행: 01:00, 05:00, 09:00, 13:00, 17:00, 21:00 UTC
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT4H</Interval>
        <Duration>P30D</Duration>
      </Repetition>
      <StartBoundary>2026-05-05T01:00:00</StartBoundary>
    </CalendarTrigger>
  </Triggers>
  <Actions>
    <Exec>
      <Command>python</Command>
      <Arguments>D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c\scripts\shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT --exchange binance-futures --max-bars 1 --log-level INFO</Arguments>
      <WorkingDirectory>D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c</WorkingDirectory>
    </Exec>
  </Actions>
  <Settings>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
  </Settings>
</Task>
"@
$xml | Out-File -Encoding Unicode shadow_swing_task.xml

# 2. 작업 등록
schtasks /create /xml shadow_swing_task.xml /tn "QuantumTrader\ShadowSwing" /f

# 3. 상태 확인
schtasks /query /tn "QuantumTrader\ShadowSwing" /v

# 4. 수동 실행 (테스트)
schtasks /run /tn "QuantumTrader\ShadowSwing"

# 5. 30일 후 삭제
schtasks /delete /tn "QuantumTrader\ShadowSwing" /f
```

### Windows — PowerShell Start-Process (대안, 단순 백그라운드)

```powershell
# 단일 백그라운드 프로세스로 실행 (30일 데몬)
$logPath = "D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c\logs\shadow\daemon.log"
$proc = Start-Process python -ArgumentList @(
    "D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c\scripts\shadow_run_swing.py",
    "--strategy", "s2c-voltarget",
    "--symbol", "BTCUSDT",
    "--exchange", "binance-futures",
    "--log-level", "INFO"
) -WorkingDirectory "D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c" `
  -RedirectStandardOutput $logPath `
  -RedirectStandardError "$logPath.err" `
  -PassThru -WindowStyle Hidden

# PID 저장
$proc.Id | Out-File "D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c\logs\shadow\daemon.pid"
Write-Host "Daemon PID: $($proc.Id)"

# 종료
Stop-Process -Id (Get-Content logs\shadow\daemon.pid)
```

### Linux — crontab (서버 배포 시)

```cron
# /etc/cron.d/shadow-swing 또는 crontab -e
# 매 4h bar 종료 후 실행 (UTC 01, 05, 09, 13, 17, 21시)
0 1,5,9,13,17,21 * * * cd /path/to/quantum-trader-agent && \
  PYTHONUTF8=1 python scripts/shadow_run_swing.py \
    --strategy s2c-voltarget \
    --symbol BTCUSDT \
    --exchange binance-futures \
    --max-bars 1 \
    --log-level INFO \
    >> logs/shadow/cron.log 2>&1

# 헬스체크 (10분 단위)
*/10 * * * * cd /path/to/quantum-trader-agent && \
  python -c "
from pathlib import Path
import json, time
pid_f = Path('logs/shadow/daemon.pid')
if pid_f.exists():
    import os
    try: os.kill(int(pid_f.read_text()), 0)
    except ProcessLookupError: print('DAEMON DEAD — restart needed')
" >> logs/shadow/healthcheck.log 2>&1

# 일일 리포트 (UTC 23:50)
50 23 * * * cd /path/to/quantum-trader-agent && \
  PYTHONUTF8=1 python scripts/shadow_report.py \
    --wal logs/shadow/$(ls -t logs/shadow/ | head -1)/wal.jsonl \
    --verify-exit \
    --out logs/shadow/daily_report_$(date +%Y%m%d).md 2>&1
```

### 데몬 재시작 로직

```bash
#!/bin/bash
# restart_daemon.sh — 데몬 재시작 스크립트
PID_FILE="logs/shadow/daemon.pid"
LOG_DIR="logs/shadow/$(date -u +%Y%m%dT%H%M%SZ)-restart"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill -TERM "$OLD_PID" 2>/dev/null || true
    sleep 2
fi

mkdir -p "$LOG_DIR"
nohup python scripts/shadow_run_swing.py \
    --strategy s2c-voltarget \
    --symbol BTCUSDT \
    --exchange binance-futures \
    --log-dir "$LOG_DIR" \
    --log-level INFO \
    > "$LOG_DIR/run.log" 2>&1 &

echo $! > "$PID_FILE"
echo "Restarted daemon PID=$(cat $PID_FILE)"
```

---

## Phase D: 일일 운영 점검 체크리스트

매일 UTC 00:00 기준으로 아래 항목을 점검한다.

### 1. WAL 무결성 (jsonl validate)

```bash
# WAL JSONL 무결성 검사 — corruption 개수 0 이어야 함
python -c "
from src.live.wal import replay
from pathlib import Path
events, corruptions = replay(Path('logs/shadow/\$(ls -t logs/shadow/ | head -1)/wal.jsonl'))
print(f'events={len(events)} corruptions={len(corruptions)}')
assert len(corruptions) == 0, f'WAL corruption detected: {corruptions}'
"
```

### 2. 거래소 latency 추세

```bash
# 일일 리포트에서 p50/p95 latency 확인 (실거래소 연결 시)
# 기대치: p50 < 100ms, p95 < 500ms
# 임계 초과 시 R5 트리거 검토
grep "latency" logs/shadow/cron.log | tail -20
```

### 3. 신호 발생 빈도 (vs backtest 기대치)

```bash
# WAL에서 order_submitted 건수 집계
python -c "
import json
from pathlib import Path
wal = Path('logs/shadow/\$(ls -t logs/shadow/ | head -1)/wal.jsonl')
lines = [json.loads(l) for l in wal.read_text().strip().split('\n') if l]
submitted = [e for e in lines if e['event_type'] == 'order_submitted']
filled = [e for e in lines if e['event_type'] == 'order_filled']
print(f'submitted={len(submitted)} filled={len(filled)}')
# backtest 기대: 30일 중 약 6-10회 신호
"
```

### 4. 데몬 alive (PID + heartbeat)

```bash
# Windows
$pid = Get-Content logs\shadow\daemon.pid
Get-Process -Id $pid -ErrorAction SilentlyContinue | Select-Object Id, CPU, WorkingSet

# Linux
PID=$(cat logs/shadow/daemon.pid)
kill -0 $PID && echo "ALIVE" || echo "DEAD — restart required"
```

### 5. 일일 리포트 생성

```bash
PYTHONUTF8=1 python scripts/shadow_report.py \
  --wal logs/shadow/$(ls -t logs/shadow/ | head -1)/wal.jsonl \
  --verify-exit \
  --out logs/shadow/daily_$(date -u +%Y%m%d).md
```

---

## Phase E: 30일 후 채택/기각 SOP

### 판정 기준표

| 지표 | 기준 | 판정 |
|------|------|------|
| net Sharpe vs backtest Sharpe 괴리 | ≤ 50% (괴리 ≤ 0.407) | 채택 후보 |
| paper monthly hit rate (mhr) | ≥ 0.40 | 채택, 미만 → 재평가 |
| 최대 낙폭 (MDD) | ≤ 25% | OK |
| MDD | > 25% | Halt 후 검토 |
| paper Sharpe | ≥ 0.40 | 채택 후보 |
| paper Sharpe | < 0.40 | Negative result |

W1 backtest Sharpe = 0.814 → 괴리 50% 이내 기준치 = 0.407.

### 채택 절차

```
1. shadow_report.py --compare-backtest <backtest_wal> 실행
2. compare_result["passed"] == True 확인
3. docs/specs/strategies/s2c-voltarget.md 채택 여부 업데이트
4. 실거래 전환 시 KIS Phase 2 (#105) 연동 검토
```

### 기각 절차

```
1. WAL 아카이브: logs/shadow/{run_id}/ → docs/work/done/175-negative/
2. docs/specs/strategies/s2c-voltarget.md 에 negative result 기록
3. 파라미터 재조정 후 후속 실험 이슈 생성
```

---

## Phase F: Halt Trigger R1-R5 (실전 위험 한계, 자동 정지 룰)

| ID | 트리거 조건 | 자동 액션 | 담당 코드 |
|----|------------|----------|----------|
| R1 | MDD > 25% | KillSwitch.trip() → 프로세스 종료 | `src/ops/kill_switch.py` |
| R2 | 일일 손실 > 5% | 당일 신호 무시 (on_bar skip) | `paper_adapter.py` 확장 필요 |
| R3 | 연속 3일 손실 | Telegram 알람 발송 | `scripts/telegram_alert.py` |
| R4 | WAL 쓰기 실패 | KillSwitch.trip() → 즉시 중단 | `src/live/wal.py` |
| R5 | 거래소 연결 끊김 3회+ | Telegram 알람 + 재연결 시도 | `src/live/reconnect.py` |

R1/R4 는 이미 `KillSwitch` 구현 완료 (#27).
R2/R3/R5 는 30일 운영 시 `shadow_run_swing.py` 에 추가 구현 권장.

### R1 MDD 모니터링 예시

```python
# shadow_run_swing.py 내 bar 루프 확장 예시
from src.ops.kill_switch import KillSwitch

equity_peak = initial_balance
for bar in bars:
    ack = await adapter.on_bar(df_slice)
    current_equity = await broker.get_balance()
    if current_equity > equity_peak:
        equity_peak = current_equity
    mdd = (equity_peak - current_equity) / equity_peak
    if mdd > 0.25:  # R1: MDD > 25%
        kill_switch.trip(reason="R1_MDD_EXCEEDED")
        break
```

---

## 회귀 테스트 결과

```
pytest tests/test_paper_adapter.py -v
9 passed in 3.62s
```

모든 단위 테스트 통과.

## check_invariants --strict 결과

```
[check_invariants] 통과 (153 노트 검증)
```

---

## 30일 운영 시작 가이드 (사용자 전달)

### 준비 사항

1. Python 환경: `pip install -e .` (pyproject.toml)
2. (선택) 거래소 API key: testnet 전용, 실주문 없음. API key 없으면 mock feed 사용.
3. 로그 디렉토리 생성: `mkdir -p logs/shadow`

### Windows에서 30일 운영 시작

```powershell
cd D:\project\quantum-trader-agent\.worktree\000175-paper-shadow-s2c

# 방법 A: Task Scheduler (권장 — 시스템 재시작 후에도 지속)
# shadow_swing_task.xml 생성 후:
schtasks /create /xml shadow_swing_task.xml /tn "QuantumTrader\ShadowSwing" /f

# 방법 B: 단순 백그라운드 실행 (현재 세션 종료 시 중단)
Start-Process python -ArgumentList "scripts\shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT --exchange binance-futures --log-level INFO" -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru | Select-Object Id | ForEach-Object { $_.Id | Out-File logs\shadow\daemon.pid }
```

### 일일 리포트 확인

```powershell
# 매일 확인
$env:PYTHONUTF8=1
python scripts\shadow_report.py --wal (Get-ChildItem logs\shadow -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { "$($_.FullName)\wal.jsonl" }) --verify-exit --out logs\shadow\daily_report.md
```

### 30일 후 결과 판정

```powershell
# Backtest WAL 이 있다면 비교 실행
python scripts\shadow_report.py --wal logs\shadow\{run_id}\wal.jsonl --compare-backtest logs\backtest\wal.jsonl --threshold 0.407
```
