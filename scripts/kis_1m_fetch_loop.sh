#!/usr/bin/env bash
# Issue #152 KIS 1m fetch + weekly summary cron (Docker container 내부 실행).
#
# `cron_loop.sh` (#133 report-cron) 패턴 차용 — bash sleep loop 으로 매일 KST $FETCH_HOUR_KST:00
# 발화. 평일(Mon-Fri) 은 `cron_fetch_kis_daily.py` 실행 + 매 결과 Telegram, $WEEKLY_DOW (default
# 0=Sun) 은 `kis_lake_monitor.py` 로 누적 요약 Telegram 발송.
#
# Env:
#   LAKE_DIR              parquet 누적 디렉토리 (default: /data/lake)
#   LOG_DIR               fetch stdout/stderr 로그 (default: /data/logs)
#   FETCH_HOUR_KST        매일 발화 시각 (24h, default: 16 = KRX 마감 30분 후)
#   N_POOL                cron_fetch_kis_daily 의 --n-pool (default: 30)
#   INTERVAL              cron_fetch_kis_daily 의 --interval (default: 1m)
#   WEEKLY_DOW            주간 요약 요일 (date +%w 형식, 0=Sun ... 6=Sat, default: 0)
#   TARGET_DAYS           kis_lake_monitor 의 --target-days (default: 90)
#   TELEGRAM_BOT_TOKEN    (선택) 매 fetch 결과 + 주간 요약 발송
#   TELEGRAM_CHAT_ID      (선택)
#
# Container TZ=Asia/Seoul 전제 (docker-compose.live.yml 에서 강제).

set -euo pipefail

LAKE_DIR="${LAKE_DIR:-/data/lake}"
LOG_DIR="${LOG_DIR:-/data/logs}"
FETCH_HOUR_KST="${FETCH_HOUR_KST:-16}"
N_POOL="${N_POOL:-30}"
INTERVAL="${INTERVAL:-1m}"
WEEKLY_DOW="${WEEKLY_DOW:-0}"
TARGET_DAYS="${TARGET_DAYS:-90}"

mkdir -p "$LOG_DIR" "$LAKE_DIR"

# #152 텔레그램 변수명 fallback — 모든 알림 LIVE 봇 단일 채널로 통일.
# legacy TELEGRAM_BOT_TOKEN/CHAT_ID 가 비어있으면 LIVE > QTA 순으로 보강.
: "${TELEGRAM_BOT_TOKEN:=${TELEGRAM_LIVE_BOT_TOKEN:-${TELEGRAM_QTA_BOT_TOKEN:-}}}"
: "${TELEGRAM_CHAT_ID:=${TELEGRAM_LIVE_CHAT_ID:-${TELEGRAM_QTA_CHAT_ID:-}}}"
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

trap 'echo "[kis_1m_fetch] received TERM, exiting"; exit 0' TERM INT

log() { echo "[kis_1m_fetch $(date +%Y-%m-%dT%H:%M:%S)] $*"; }

# Seconds until next FETCH_HOUR_KST:00 (today or tomorrow).
seconds_until_next_run() {
    local now_h now_m now_s target now_total target_total diff
    now_h=$(date +%H); now_m=$(date +%M); now_s=$(date +%S)
    now_h=$((10#$now_h)); now_m=$((10#$now_m)); now_s=$((10#$now_s))
    target=$((10#$FETCH_HOUR_KST))
    now_total=$((now_h * 3600 + now_m * 60 + now_s))
    target_total=$((target * 3600))
    if [ "$now_total" -lt "$target_total" ]; then
        diff=$((target_total - now_total))
    else
        diff=$((86400 - now_total + target_total))
    fi
    echo "$diff"
}

send_telegram() {
    local msg="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        python -c "
import sys
sys.path.insert(0, '/app/scripts')
from telegram_alert import send_telegram
send_telegram(sys.argv[1])
" "$msg" 2>&1 || log "telegram alert failed (non-fatal)"
    else
        log "TELEGRAM_BOT_TOKEN/CHAT_ID not set — skip"
    fi
}

run_fetch() {
    local today log_file rc tail_summary tail_err
    today=$(date +%Y-%m-%d)
    log_file="$LOG_DIR/kis_fetch_${today}.log"
    log "fetching n_pool=$N_POOL interval=$INTERVAL → $log_file"
    if python /app/scripts/cron_fetch_kis_daily.py \
            --n-pool "$N_POOL" \
            --interval "$INTERVAL" \
            --lake-dir "$LAKE_DIR" \
            >"$log_file" 2>&1; then
        tail_summary=$(tail -n 20 "$log_file" | tr -d '\r')
        log "fetch OK"
        send_telegram "✅ KIS 1m fetch OK ($today)
\`\`\`
$tail_summary
\`\`\`"
    else
        rc=$?
        tail_err=$(tail -n 30 "$log_file" | tr -d '\r')
        log "fetch FAILED (exit $rc)"
        send_telegram "❌ KIS 1m fetch FAIL ($today, exit $rc)
\`\`\`
$tail_err
\`\`\`"
    fi
}

run_weekly_summary() {
    local out
    out="$LOG_DIR/weekly_summary_$(date +%Y-%m-%d).md"
    log "running weekly summary → $out"
    python /app/scripts/kis_lake_monitor.py \
        --lake-dir "$LAKE_DIR" \
        --interval "$INTERVAL" \
        --target-days "$TARGET_DAYS" \
        --out "$out" \
        --telegram \
        || log "weekly summary failed (non-fatal)"
}

log "starting (LAKE_DIR=$LAKE_DIR FETCH_HOUR_KST=$FETCH_HOUR_KST N_POOL=$N_POOL INTERVAL=$INTERVAL WEEKLY_DOW=$WEEKLY_DOW)"

while true; do
    sleep_sec=$(seconds_until_next_run)
    log "sleeping ${sleep_sec}s until ${FETCH_HOUR_KST}:00 KST"
    sleep "$sleep_sec" &
    wait $! || true

    dow_w=$(date +%w)  # 0=Sun ... 6=Sat
    if [ "$dow_w" = "$WEEKLY_DOW" ]; then
        log "today=DOW $dow_w (weekly summary day)"
        run_weekly_summary || true
    elif [ "$dow_w" -ge "1" ] && [ "$dow_w" -le "5" ]; then
        log "today=DOW $dow_w (weekday — fetch)"
        run_fetch || true
    else
        log "today=DOW $dow_w (Sat — skip)"
    fi

    # 동일 시각 중복 발화 방지: 60초 버퍼
    sleep 60 &
    wait $! || true
done
