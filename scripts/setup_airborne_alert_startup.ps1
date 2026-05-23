# Fallback: register the Airborne daemon via the per-user Startup folder
# (no admin required, no Task Scheduler permissions).
#
# Run ONCE:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_airborne_alert_startup.ps1
#
# What it does:
#   - Creates a .lnk in shell:startup pointing at run_airborne_daemon.bat
#     → next logon auto-starts the daemon.
#   - Spawns a detached minimized instance NOW so the current session is
#     covered without waiting for a re-logon.
#
# Trade-off vs Task Scheduler:
#   - No automatic restart on crash (Task Scheduler had RestartOnFailure).
#   - Daemon resilience comes from its own internal reconnect logic
#     (BinanceMarketDataStream uses exponential-backoff reconnect, max 20).
#
# Uninstall:
#   Remove-Item "$([Environment]::GetFolderPath('Startup'))\QuantumTrader_AirborneAlert.lnk"
#   Get-Process python | Where-Object { $_.MainWindowTitle -match 'airborne' } | Stop-Process

$ErrorActionPreference = "Stop"

$RepoRoot = "D:\project\quantum-trader-agent"
$BatchFile = Join-Path $RepoRoot "scripts\run_airborne_daemon.bat"

if (-not (Test-Path $BatchFile)) {
    Write-Error "Wrapper batch not found: $BatchFile"
    exit 1
}

# 1. Create per-user Startup shortcut
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup "QuantumTrader_AirborneAlert.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $BatchFile
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.WindowStyle = 7  # minimized
$shortcut.Description = "Airborne v1.1 Telegram alert daemon (auto-start on logon)"
$shortcut.Save()
Write-Host "[OK] Startup shortcut created: $linkPath" -ForegroundColor Green

# 2. Stop any existing daemon instance (silent if none)
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'airborne_alert_daemon\.py' }
if ($existing) {
    Write-Host "[INFO] Stopping prior daemon instance(s) — PID: $($existing.ProcessId -join ', ')" -ForegroundColor Yellow
    $existing | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

# 3. Start the daemon now, detached + minimized (survives current shell exit)
Start-Process -FilePath $BatchFile -WorkingDirectory $RepoRoot -WindowStyle Minimized | Out-Null
Write-Host "[OK] Daemon launched detached (minimized window)" -ForegroundColor Green
Write-Host ""

# 4. Brief verification — confirm process is alive
Start-Sleep -Seconds 3
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'airborne_alert_daemon\.py' }
if ($running) {
    Write-Host "[OK] Daemon running — PID: $($running.ProcessId -join ', ')" -ForegroundColor Green
} else {
    Write-Host "[WARN] Daemon process not detected after 3s — check logs\airborne_daemon.log" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Log:     $RepoRoot\logs\airborne_daemon.log"
Write-Host "Tail:    Get-Content $RepoRoot\logs\airborne_daemon.log -Wait -Tail 20"
Write-Host "Stop:    Get-Process python | Where { (Get-CimInstance Win32_Process -Filter `"ProcessId=`$(`$_.Id)`").CommandLine -match 'airborne' } | Stop-Process"
Write-Host "Remove:  Remove-Item '$linkPath'"
