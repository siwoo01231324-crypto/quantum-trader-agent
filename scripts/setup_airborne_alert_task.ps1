# Register the Airborne v1.1 alert daemon as a Windows Scheduled Task.
#
# Run ONCE (in PowerShell, no admin required for the current user):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_airborne_alert_task.ps1
#
# What it does:
#   - Registers task "QuantumTrader_AirborneAlert" that runs
#     scripts\run_airborne_daemon.bat at each user logon.
#   - ExecutionTimeLimit = 0 (unlimited; daemon stays up between reboots).
#   - RestartOnFailure: 1 min interval, 10 retries.
#   - Runs even on battery (laptop-safe).
#   - Starts the task immediately so the current session is covered without
#     waiting for the next logon.
#
# Uninstall:
#   Unregister-ScheduledTask -TaskName "QuantumTrader_AirborneAlert" -Confirm:$false

$ErrorActionPreference = "Stop"

$TaskName = "QuantumTrader_AirborneAlert"
$RepoRoot = "D:\project\quantum-trader-agent"
$BatchFile = Join-Path $RepoRoot "scripts\run_airborne_daemon.bat"

if (-not (Test-Path $BatchFile)) {
    Write-Error "Wrapper batch not found: $BatchFile"
    exit 1
}

# Action: run the .bat wrapper in the repo root
$action = New-ScheduledTaskAction `
    -Execute $BatchFile `
    -WorkingDirectory $RepoRoot

# Trigger: at any user logon
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Settings: unlimited runtime, retry on failure, laptop-safe.
# PowerShell 5.x cmdlet only accepts the positive-form switches; we cannot
# pass -DisallowStartIfOnBatteries:$false / -StopIfGoingOnBatteries:$false.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Clean any prior task with the same name (silent if missing).
# A partially-registered task from a previous run can cause HRESULT 0x80070005
# on re-register when -UserId mismatches the current identity.
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

# Register WITHOUT an explicit Principal — PowerShell auto-fills the current
# interactive user, which avoids "Access is denied" on Microsoft Account /
# AzureAD-joined boxes where USERDOMAIN\USERNAME doesn't resolve cleanly.
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Airborne BB-reversal v1.1 Telegram alert daemon (Binance USDT-perp top-50, 1h kline, long+short). docs/specs/live-airborne-alert-daemon.md" `
    -Force | Out-Null

Write-Host "[OK] Registered scheduled task: $TaskName" -ForegroundColor Green
Write-Host "     Wrapper: $BatchFile"
Write-Host "     Trigger: At Logon"
Write-Host "     ExecutionTimeLimit: 0 (unlimited)"
Write-Host ""

# Start immediately so the current session is covered (no reboot needed)
Start-ScheduledTask -TaskName $TaskName
Write-Host "[OK] Task started — daemon should be running within a few seconds." -ForegroundColor Green
Write-Host ""
Write-Host "Log: $RepoRoot\logs\airborne_daemon.log"
Write-Host "Status:  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "Stop:    Stop-ScheduledTask -TaskName $TaskName"
Write-Host "Remove:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
