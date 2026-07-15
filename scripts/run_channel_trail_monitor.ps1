# 채널 트레일링 감시 런처 — 로그온 시 작업 스케줄러가 실행.
# 리포 루트로 이동 후 감시 스크립트를 로그파일에 append 하며 상시 가동.
# 싱글턴 가드가 스크립트 내부에 있어 중복 실행돼도 1개만 살아남는다.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py = "C:\Users\watch\AppData\Local\Programs\Python\Python314\python.exe"
$log = Join-Path $repo "logs\channel_trail_monitor.log"
& $py "scripts\channel_trail_monitor.py" *>> $log
