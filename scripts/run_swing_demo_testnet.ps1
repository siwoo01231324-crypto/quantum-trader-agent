# 스윙 4h 데모 — binance-testnet-shadow (페이퍼 + 연속 피드). bitget-demo 좀비 회피.
#
# 사용:  .\scripts\run_swing_demo_testnet.ps1
# 끄기:  Ctrl+C
# 로그:  logs/shadow-swing-binance/live_run.log  (WAL: 같은 디렉토리 <run_id>/wal.jsonl)
# 대시보드: http://localhost:8002  (스윙 누적뷰 /swing)
#
# ⚠️ binance testnet creds 필요(.env): BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
#    (또는 BINANCE_DEMO_API_KEY / BINANCE_API_KEY — fallback chain). airborne-trader 와 동일 키.

$env:SWING_CHANNEL_SWEEP = "1"     # 돌파 채널청산(Donchian10 하단) 발동
$env:SWING_SIGNAL_ALERT  = "1"     # 스윙 진입 텔레그램 알림
$env:SWING_EVAL_TIMER_SEC = "60"     # 체결틱 죽은 환경 대비 — 60s마다 4h 평가
$env:QTA_LOG_FILE        = "logs/shadow-swing-binance/live_run.log"   # 실거래/bitget데모 로그와 분리

python scripts/live_run.py `
  --symbols BTCUSDT,ETHUSDT,SOLUSDT `
  --broker binance-testnet-shadow `
  --production-yaml configs/orchestrator/swing.yaml `
  --feed binance `
  --log-dir logs/shadow-swing-binance `
  --dashboard-port 8002 `
  @args
