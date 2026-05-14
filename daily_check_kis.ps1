# 일일 점검 스크립트 (KIS)
# .\daily_check_kis.ps1

$env:PYTHONUTF8 = 1
# PowerShell 5.x 기본 콘솔 인코딩은 CP949 → UTF-8 한글 주석/출력 깨짐. 강제 UTF-8.
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
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
    @{ name = "qta-live-daemon";         label = "live-daemon" },
    @{ name = "qta-report-cron";         label = "report-cron" },
    @{ name = "qta-telegram-notifier";   label = "telegram-notifier" },
    @{ name = "qta-telegram-control";    label = "telegram-control" },
    @{ name = "qta-kis-1m-fetch-cron";   label = "kis-1m-fetch-cron" },
    @{ name = "qta-universe-rebal-cron"; label = "universe-rebal-cron" }
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
Write-Host "=== Universe-rebal (#218) ===" -ForegroundColor Cyan
# last-run.txt 는 `krx=YYYY-MM-DD` / `crypto=YYYY-MM-DD` 라인을 append. 두 트랙 분리 표시.
# 호스트 마운트: ./logs/universe-rebal/universe-rebal-last-run.txt (container: /data/logs/...)
$lastRun = "logs\universe-rebal\universe-rebal-last-run.txt"
if (Test-Path $lastRun) {
    $krxLast    = (Get-Content $lastRun | Select-String "^krx="    | Select-Object -Last 1)
    $cryptoLast = (Get-Content $lastRun | Select-String "^crypto=" | Select-Object -Last 1)
    if ($krxLast)    { Write-Host ("  KRX last run:    {0}" -f $krxLast.ToString().Trim()) }
    else             { Write-Host "  KRX last run:    (never — next: Friday 15:32 KST)" -ForegroundColor Yellow }
    if ($cryptoLast) { Write-Host ("  Crypto last run: {0}" -f $cryptoLast.ToString().Trim()) }
    else             { Write-Host "  Crypto last run: (never — next: Sunday 00:00 UTC)" -ForegroundColor Yellow }
} else {
    Write-Host "  last-run.txt 미생성 (컨테이너 첫 가동 직후)" -ForegroundColor Yellow
}
# strategy 별 paper WAL — cron_paper_universe_rebal.py 가 logs/shadow/cron-{sid}/wal.jsonl 로 분리 기록.
$rebalWals = Get-ChildItem "logs\shadow\cron-*\wal.jsonl" -ErrorAction SilentlyContinue
if ($rebalWals) {
    foreach ($w in $rebalWals) {
        $count = (Get-Content $w.FullName | Measure-Object).Count
        $sid = Split-Path $w.Directory -Leaf
        $color = if ($count -gt 2) { "Green" } else { "Yellow" }   # >2 = run_started/session_open 외 신호/주문 발생
        Write-Host ("  {0,-40} {1} events" -f $sid, $count) -ForegroundColor $color
    }
} else {
    Write-Host "  per-strategy WAL 0건 (발주 이력 없음 — 컨테이너 가동 후 첫 금/일 대기)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== KIS 1m lake (#152) ===" -ForegroundColor Cyan
# kis_1m_fetch_loop.sh 가 평일 16:00 KST 에 partitioned parquet 적재.
# 구조: lake/ohlcv/freq=1m/year=YYYY/month=MM/symbol=<code>/part-0.parquet
$lake = "lake"
if (Test-Path $lake) {
    $parquets = @(Get-ChildItem "$lake" -Recurse -Filter "*.parquet" -ErrorAction SilentlyContinue)
    $symbols = @($parquets | ForEach-Object {
        if ($_.FullName -match "symbol=([^\\]+)") { $matches[1] }
    } | Select-Object -Unique)
    $latest = $parquets | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $color = if ($symbols.Count -gt 0) { "Green" } else { "Yellow" }
    Write-Host ("  symbols cached: {0} (parquet files: {1})" -f $symbols.Count, $parquets.Count) -ForegroundColor $color
    if ($latest) {
        $sym = if ($latest.FullName -match "symbol=([^\\]+)") { $matches[1] } else { $latest.Name }
        Write-Host ("  latest update:  symbol={0} ({1})" -f $sym, $latest.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
    }
} else {
    Write-Host "  lake/ 미존재 (fetch 미실행)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Telegram-control audit (#216) ===" -ForegroundColor Cyan
# telegram_control.py 가 /kill /release /status 등 명령 처리 후 audit WAL append.
$ctlWal = "logs\shadow\telegram_control.wal.jsonl"
if (Test-Path $ctlWal) {
    $count = (Get-Content $ctlWal | Measure-Object).Count
    $last  = (Get-Content $ctlWal -Tail 1 -ErrorAction SilentlyContinue)
    Write-Host ("  audit events:   {0}" -f $count)
    if ($last) { Write-Host ("  last command:   {0}" -f $last) }
} else {
    Write-Host "  audit WAL 없음 (명령 전송 이력 없음 — 정상)" -ForegroundColor Yellow
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
