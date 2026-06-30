# 스윙 4h MAINNET 실거래 — 검증용 (자본 1%/포지션, 1x). ⚠️⚠️ REAL MONEY ⚠️⚠️
#
# 사용 (아침에, 맑은 정신에):
#   1. 먼저 airborne(본거래) 정지   ← 같은 계좌 충돌 방지(4h collision + 레버리지 + fill)
#   2. .\scripts\run_swing_mainnet.ps1   (YES 입력해야 시작)
#   3. 첫 거래 뜨면 로그/텔레그램에서 TP/SL 라인 눈으로 확인:
#        protective_coordinator: registered ... SL=Y TP=Z
#        → 투매반등 SL=꼬리저점/TP=2R, 돌파 SL=2ATR 인지 (정적 8%/50% 아님)
#   4. 채널청산은 봇 감시 — 로그 `live_risk CHANNEL-EXIT`
#
# 끄기: 그 창 닫기 또는
#   Get-CimInstance Win32_Process -Filter "name='python.exe'" | ? CommandLine -like '*swing_mainnet*' | % { Stop-Process -Id $_.ProcessId -Force }

Write-Host "==================================================================" -ForegroundColor Red
Write-Host " ⚠️  스윙 MAINNET 실거래 (REAL MONEY) — 자본 1%/포지션, 1x 레버리지" -ForegroundColor Red
Write-Host " ⚠️  airborne(본거래) 정지했는지 확인! (같은 계좌 동시가동 금지)" -ForegroundColor Red
Write-Host "==================================================================" -ForegroundColor Red
$confirm = Read-Host "정말 실거래 시작? airborne 껐으면 YES 입력"
if ($confirm -ne "YES") { Write-Host "취소됨." -ForegroundColor Yellow; exit }

$env:QTA_TARGET_LEVERAGE  = "1"      # 거래소 종목 레버리지 1x 강제 (executor)
$env:SWING_CHANNEL_SWEEP  = "1"      # 돌파 채널청산(Donchian10) 발동
$env:SWING_SIGNAL_ALERT   = "1"      # 스윙 진입 텔레그램 알림
$env:SWING_EVAL_TIMER_SEC = "60"     # 체결틱 죽어도 60s마다 4h 평가(백업)
$env:QTA_LOG_FILE         = "logs/shadow-swing-mainnet/live_run.log"

python scripts/live_run.py `
  --symbols BTCUSDT,ETHUSDT,SOLUSDT `
  --broker bitget-mainnet `
  --production-yaml configs/orchestrator/swing_mainnet.yaml `
  --feed binance `
  --log-dir logs/shadow-swing-mainnet `
  --dashboard-port 8003 `
  @args
