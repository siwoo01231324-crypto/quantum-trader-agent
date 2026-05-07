# 일일 점검 스크립트 (KIS)
# .\daily_check_kis.ps1

$env:PYTHONUTF8 = 1
Set-Location D:\project\quantum-trader-agent

function Get-DockerStatus {
    param([string]$name)
    $line = docker ps -a --filter "name=$name" --format "{{.Status}}" 2>$null
    if (-not $line) { return "(not found)" }
    return $line
}

function Get-DaemonLogs {
    param([string]$name, [int]$tail = 300)
    return (& docker logs --tail $tail $name 2>&1 | Out-String)
}

Write-Host ""
Write-Host "=== Containers ===" -ForegroundColor Cyan
$containers = @(
    @{ name = "qta-live-daemon";       label = "live-daemon" },
    @{ name = "qta-report-cron";       label = "report-cron" },
    @{ name = "qta-telegram-notifier"; label = "telegram-notifier" }
)
foreach ($c in $containers) {
    $status = Get-DockerStatus $c.name
    $color = if ($status -match "^Up") { "Green" } else { "Red" }
    Write-Host ("  {0,-22} {1}" -f $c.label, $status) -ForegroundColor $color
}

Write-Host ""
Write-Host "=== Daemon log ===" -ForegroundColor Cyan
$daemonLogs = Get-DaemonLogs "qta-live-daemon" 300
# 카운터는 24h 윈도우 — `--tail 300` 만 보면 마감 직전 버스트 후 잘려 부하 과소평가됨 (#213 학습).
$daemonLogs24h = (& docker logs --since 24h qta-live-daemon 2>&1 | Out-String)
if ($daemonLogs) {
    $lines = $daemonLogs -split "`n"
    $lines24h = $daemonLogs24h -split "`n"

    $lastWarmup = ($lines | Select-String "warmup_loaded" | Select-Object -Last 1)
    $lastSignal = ($lines | Select-String "signal_emitted|order_filled|order_submitted" | Select-Object -Last 1)
    $err500       = ($lines24h | Select-String "returned 500").Count
    $errRateLimit = ($lines24h | Select-String "EGW00201").Count
    $errGaveUp    = ($lines24h | Select-String "attempt 3/3").Count
    # `-CaseSensitive` + 행 시작 timestamp anchor → "network error" / "fetch_failed
    # error=..." 같은 lowercase WARNING 라인을 ERROR 로 오인하지 않음 (false positive 0).
    $errOther     = ($lines24h | Select-String "^\d{4}-\d{2}-\d{2}.*\sERROR\s" -CaseSensitive).Count
    $lastReconn   = ($lines | Select-String "feed reconnected" | Select-Object -Last 1)

    if ($lastWarmup) {
        $tsRaw = ($lastWarmup.ToString().Trim() -split " ")[0..1] -join " "
        $sym = ($lastWarmup.ToString() -replace ".*symbol=([0-9]+).*", '$1')
        $bars = ($lastWarmup.ToString() -replace ".*bars=([0-9]+).*", '$1')
        Write-Host ("  Last warmup:    {0,-22} {1} (bars={2})" -f $tsRaw, $sym, $bars)
    }
    if ($lastSignal) {
        Write-Host ("  Last signal:    {0}" -f $lastSignal.ToString().Trim()) -ForegroundColor Green
    } else {
        Write-Host "  Last signal:    none (KRX opens at 09:00 KST)" -ForegroundColor Yellow
    }
    # `returned 500` 의 정체는 KIS EGW00201 (초당 rate limit). 위험 신호는 `attempt 3/3` 카운트 — 0 이면 모두 회복.
    Write-Host ("  500 retries (24h):       {0}" -f $err500)
    if ($errRateLimit -gt 0) {
        Write-Host ("    rate-limit (EGW00201): {0} (KIS per-second cap)" -f $errRateLimit) -ForegroundColor Yellow
    }
    if ($errGaveUp -gt 0) {
        Write-Host ("    final failures (3/3):  {0}" -f $errGaveUp) -ForegroundColor Red
    } else {
        Write-Host ("    final failures (3/3):  0 (all recovered)") -ForegroundColor Green
    }
    Write-Host ("  Errors (24h):            {0}" -f $errOther)
    if ($lastReconn) {
        $tsRaw = ($lastReconn.ToString().Trim() -split " ")[0..1] -join " "
        Write-Host ("  Last reconnect: {0}" -f $tsRaw) -ForegroundColor Yellow
    }
} else {
    Write-Host "  (no logs available)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== WAL ===" -ForegroundColor Cyan
$walFound = $false
foreach ($p in @("logs\shadow", "data\logs")) {
    if (Test-Path $p) {
        Get-ChildItem "$p\*\wal.jsonl" -ErrorAction SilentlyContinue | ForEach-Object {
            $count = (Get-Content $_.FullName | Measure-Object).Count
            $rid = Split-Path $_.Directory -Leaf
            $color = if ($count -gt 0) { "Green" } else { "Yellow" }
            Write-Host ("  {0,-40} {1} events" -f $rid, $count) -ForegroundColor $color
            $walFound = $true
        }
    }
}
if (-not $walFound) {
    Write-Host "  no WAL yet (first signal will create wal.jsonl)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Daily report ===" -ForegroundColor Cyan
$reportFound = $false
foreach ($d in @("logs\shadow\reports", "data\reports")) {
    if (Test-Path $d) {
        $latest = Get-ChildItem "$d\*.md" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host ("  Latest: {0} ({1})" -f $latest.Name, $latest.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
            $reportFound = $true
            break
        }
    }
}
if (-not $reportFound) {
    Write-Host "  no daily report yet (cron runs at 16:00 KST)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[Done] Press any key to close..." -ForegroundColor Green
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
