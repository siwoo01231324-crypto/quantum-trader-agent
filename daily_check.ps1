# 일일 점검 스크립트
# .\daily_check.ps1
$wal_r4 = "D:\project\quantum-trader-agent\logs\shadow\phase1-r4-switch-BTCUSDT\wal.jsonl"
$wal_r6 = "D:\project\quantum-trader-agent\logs\shadow\phase1-r6-switch-BTCUSDT\wal.jsonl"
$env:PYTHONUTF8 = 1
Set-Location "D:\project\quantum-trader-agent"

Write-Host "`n=== R4 (4h) Task ===" -ForegroundColor Cyan
schtasks /query /tn "QuantumTrader\ShadowSwing143" /v /fo LIST | Select-String "마지막|다음|상태"

Write-Host "`n=== R6 (1h) Task ===" -ForegroundColor Cyan
schtasks /query /tn "QuantumTrader\ShadowSwing143-r6" /v /fo LIST | Select-String "마지막|다음|상태"

# 전략별 순회 점검
foreach ($pair in @(@{name="R4"; wal=$wal_r4}, @{name="R6"; wal=$wal_r6})) {
    Write-Host "`n=== $($pair.name) WAL Integrity & Report ===" -ForegroundColor Cyan
    if (Test-Path $pair.wal) {
        # WAL 무결성 검사
        python -c "from src.live.wal import replay; from pathlib import Path; e,c=replay(Path(r'$($pair.wal)')); print(f'events={len(e)} corruptions={len(c)}')"

        # 리포트 생성
        $today = Get-Date -Format yyyyMMdd
        $outPath = "logs\shadow\daily_$($pair.name)_$today.md"
        python scripts\shadow_report.py --wal "$($pair.wal)" --verify-exit --out "$outPath"
        Write-Host "Report saved: $outPath" -ForegroundColor Green
    } else {
        Write-Host "$($pair.name) WAL not created yet (no entry signal fired - normal)" -ForegroundColor Yellow
    }
}

Write-Host "`n[Done] Press any key to close..." -ForegroundColor Green
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
