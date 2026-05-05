---
type: work-done
id: 02_implementation
name: "Issue #199 — 구현 산출물 + 사용자 운영 SOP"
status: active
---

# 구현 완료 — #199 R6 (R4 의 1h 봉 변형)

## 백테스트 결과 (1h BTCUSDT 5y OOS, 2020-01-01 ~ 2025-12-31)

| Variant | 봉 | Sharpe | MDD | Trades (5y) | 30일 환산 | 결정 |
|---|---|---|---|---|---|---|
| R0 | 1h | -0.570 | -71.6% | 2486 | ~83/30일 | 망함 |
| R4 (1h, default 4h params) | 1h | -0.302 | -52.0% | 1990 | ~66/30일 | 망함 (재튜닝 필요 입증) |
| **R6 (1h, retuned)** | **1h** | **+1.201** | **-17.4%** | **554** | **~9/30일** | **✅ 운영 진행** |
| R2/R3/R5 | 1h | error | — | — | — | hmmlearn Python 3.14 빌드 실패 |

비교 (5y backtest):

| | R4 (4h, #173 BEST) | R6 (1h, 본 이슈) |
|---|---|---|
| Sharpe | 1.218 | 1.201 |
| MDD | -9.7% | -17.4% |
| Trades | 458 | 554 |
| 30일 환산 | ~7/30일 | ~9/30일 |

## R6 파라미터 (route_r6 default)

```python
return_lookback = 720   # = 30일 (1h × 720)
entry_lookback  = 80    # = 3.3일 (Donchian breakout)
exit_lookback   = 40    # = 1.7일 (Donchian exit)
vol_lookback    = 240   # = 10일 (realized vol window)
vol_target      = 0.15  # (R4 와 동일)
```

R4 의 4h 파라미터 (180/20/10/60) 를 4배 한 값. 시간 horizon (일 단위) 보존.

## 사용자 운영 SOP (머지 후)

### Phase A — 두 번째 Task Scheduler 등록 (10분)

기존 `QuantumTrader\ShadowSwing143` (R4 4h) 그대로 두고 새로 추가:

```powershell
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>QuantumTrader r6-switch paper shadow daemon (#199, 1h variant)</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-05-05T19:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1H</Interval>
        <Duration>P30D</Duration>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>python</Command>
      <Arguments>D:\project\quantum-trader-agent\scripts\shadow_run_swing.py --strategy r6-switch --symbol BTCUSDT --max-bars 1 --log-level INFO</Arguments>
      <WorkingDirectory>D:\project\quantum-trader-agent</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
$xml | Out-File -Encoding Unicode shadow_swing_r6_task.xml
schtasks /create /xml shadow_swing_r6_task.xml /tn "QuantumTrader\ShadowSwing143-r6" /f
schtasks /run /tn "QuantumTrader\ShadowSwing143-r6"
```

R4 와의 차이:
- `<Interval>PT1H</Interval>` (R4 = PT4H)
- `--strategy r6-switch` (R4 = r4-switch)
- 작업 이름: `QuantumTrader\ShadowSwing143-r6`

### Phase B — daily_check.ps1 확장 (5분)

기존 daily_check.ps1 에 R6 항목 추가:

```powershell
$wal_r4 = "D:\project\quantum-trader-agent\logs\shadow\phase1-r4-switch-BTCUSDT\wal.jsonl"
$wal_r6 = "D:\project\quantum-trader-agent\logs\shadow\phase1-r6-switch-BTCUSDT\wal.jsonl"
$env:PYTHONUTF8 = 1
Set-Location D:\project\quantum-trader-agent

Write-Host "=== R4 (4h) Task ===" -ForegroundColor Cyan
schtasks /query /tn "QuantumTrader\ShadowSwing143" /v /fo LIST | Select-String "마지막 실행|마지막 결과|다음 실행"

Write-Host "=== R6 (1h) Task ===" -ForegroundColor Cyan
schtasks /query /tn "QuantumTrader\ShadowSwing143-r6" /v /fo LIST | Select-String "마지막 실행|마지막 결과|다음 실행"

# WAL 무결성 + 일일 리포트 — R4 / R6 각각
foreach ($pair in @(@{name="R4"; wal=$wal_r4}, @{name="R6"; wal=$wal_r6})) {
    Write-Host ""
    Write-Host "=== $($pair.name) WAL ===" -ForegroundColor Cyan
    if (Test-Path $pair.wal) {
        python -c "from src.live.wal import replay; from pathlib import Path; e,c=replay(Path(r'$($pair.wal)')); print('events=',len(e),'corruptions=',len(c))"
        $today = Get-Date -Format yyyyMMdd
        python scripts\shadow_report.py --wal $pair.wal --verify-exit --out "logs\shadow\daily_$($pair.name)_$today.md"
    } else {
        Write-Host "$($pair.name) WAL not yet (no signals)" -ForegroundColor Yellow
    }
}
```

### Phase C — 30일 후 비교 판정

| 지표 | R4 paper | R6 paper | 분석 |
|---|---|---|---|
| Sharpe | (실측) | (실측) | backtest 와 괴리율 비교 |
| MDD | (실측) | (실측) | 실전 변동성 측정 |
| Trades | ~7 예상 | ~9 예상 | 통계 신뢰도 |
| Sharpe SE | ±1.5 | ±1.3 | 표본 작아서 둘 다 노이즈 큼 |

판정 기준 (각 봉 별로):
- Sharpe ≥ 0.6 → 채택 후보
- Sharpe < 0.4 → negative result

두 봉 모두 채택 후보면 → MDD 작은 쪽 (R4) 우선 + 둘 다 60일 추가 운영
한쪽만 통과 → 통과한 봉으로 Phase 3 진입
둘 다 실패 → 본 R4/R6 logic 폐기, 다른 전략 시도

## 회귀·검증

```
pytest tests/test_paper_adapter.py — 16/16 green (R6 신규 3건 + 회귀 13건)
pytest tests/ 풀 회귀 — 회귀 0 (별도 진행)
check_invariants --strict — 167 노트 통과
shadow_run_swing.py --strategy r6-switch --max-bars 5 smoke — 무에러
shadow_run_swing.py 출력: interval=1h 자동 분류 확인
```

## R4 vs R6 운영 비교 (예상)

| 시점 | R4 (4h) | R6 (1h) |
|---|---|---|
| 첫 실행 | 2026-05-05 17:00 KST (이미 시작) | 머지 + 사용자 등록 후 |
| Cron 빈도 | 4시간마다 (6회/일) | 1시간마다 (24회/일) |
| 30일 거래 예상 | ~7건 | ~9건 |
| 30일 종료 | 2026-06-04 즈음 | 사용자 등록일 + 30일 |
- WAL 디렉토리 분리 → 두 운영 독립적으로 추적 가능
- daily_check.ps1 한 번 실행으로 둘 다 확인
- Phase 3 (#107) 진입 시 두 봉 결과 보고 결정
