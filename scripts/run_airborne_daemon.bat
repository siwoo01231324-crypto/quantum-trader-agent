@echo off
REM Airborne v1.1 Telegram alert daemon — Windows Task Scheduler wrapper.
REM Wired by scripts\setup_airborne_alert_task.ps1 (LogonTrigger, restart on failure).
REM Log: logs\airborne_daemon.log (append mode, rotated manually).

cd /d D:\project\quantum-trader-agent
if not exist logs mkdir logs

REM Default: top-50 USDT-perp, 6h universe refresh, INFO log.
REM Edit args here to change behaviour without touching the scheduled task.
python -u scripts\airborne_alert_daemon.py --top-n 50 --log-level INFO >> logs\airborne_daemon.log 2>&1
