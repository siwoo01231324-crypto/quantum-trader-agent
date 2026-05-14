$pythonExe = "C:\Users\watch\AppData\Local\Programs\Python\Python314\python.exe"
$worktree  = "D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval"
$script    = Join-Path $worktree "scripts\cron_fetch_screener_universe.py"

$action  = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "-X utf8 `"$script`"" `
    -WorkingDirectory $worktree

$daysOfWeek = @("Monday","Tuesday","Wednesday","Thursday","Friday")
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $daysOfWeek -At "16:30"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName "QTA-Screener-Fetch" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Daily KIS 1m + daily fetch for HTS screener universe (#230 option B)" `
    -Force | Out-Null

Write-Host "Registered: QTA-Screener-Fetch (weekdays 16:30 KST)"
Get-ScheduledTask -TaskName "QTA-Screener-Fetch" | Format-List TaskName, State, Triggers, Actions
