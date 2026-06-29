# 스윙 4h 데모 한 줄 실행 — 투매반등 + 돌파(채널청산), bitget-demo 페이퍼, 실거래와 격리.
#
# 사용:
#   .\scripts\run_swing_demo.ps1
#   .\scripts\run_swing_demo.ps1 --symbols BTCUSDT,XRPUSDT   # 인자 추가/override 가능
#
# 끄기: Ctrl+C
# 로그: logs/shadow-swing/live_run.log  (WAL: logs/shadow-swing/<run_id>/wal.jsonl)
# 대시보드: http://localhost:8001  (스윙 누적뷰 /swing)

$env:SWING_CHANNEL_SWEEP = "1"     # 돌파 채널청산(Donchian10 하단) 발동
$env:SWING_SIGNAL_ALERT  = "1"     # 스윙 진입 텔레그램 알림
$env:QTA_LOG_FILE        = "logs/shadow-swing/live_run.log"   # 실거래 로그와 분리

python scripts/live_run.py `
  --symbols BTCUSDT,ETHUSDT,SOLUSDT `
  --broker bitget-demo `
  --production-yaml configs/orchestrator/swing.yaml `
  --feed binance `
  --log-dir logs/shadow-swing `
  --dashboard-port 8001 `
  @args
