#!/usr/bin/env bash
# Issue #133 Phase 2 일일 리포트 cron (Docker container 내부 실행).
#
# Cron daemon 설치 회피 — bash sleep loop 으로 매일 KST $REPORT_HOUR_KST:00 에 발화.
# scripts/live_report.py 실행 → /data/reports/{YYYY-MM-DD}.md 생성 → telegram_alert.py 로 요약 발송.
#
# Env:
#   LOG_DIR              WAL 검색 디렉토리 (default: /data/logs)
#   REPORTS_DIR          일일 리포트 출력 (default: /data/reports)
#   REPORT_HOUR_KST      발화 시각 (24h, default: 16 = KRX 마감 30분 후)
#   TELEGRAM_BOT_TOKEN   (선택) 있으면 요약 자동 발송
#   TELEGRAM_CHAT_ID     (선택)
#
# Container TZ=Asia/Seoul 전제 (docker-compose.live.yml 에서 강제).

set -euo pipefail

LOG_DIR="${LOG_DIR:-/data/logs}"
REPORTS_DIR="${REPORTS_DIR:-/data/reports}"
REPORT_HOUR_KST="${REPORT_HOUR_KST:-16}"

mkdir -p "$REPORTS_DIR"

# #152 텔레그램 변수명 fallback — 모든 알림 LIVE 봇 단일 채널로 통일.
# legacy TELEGRAM_BOT_TOKEN/CHAT_ID 가 비어있으면 LIVE > QTA 순으로 보강.
: "${TELEGRAM_BOT_TOKEN:=${TELEGRAM_LIVE_BOT_TOKEN:-${TELEGRAM_QTA_BOT_TOKEN:-}}}"
: "${TELEGRAM_CHAT_ID:=${TELEGRAM_LIVE_CHAT_ID:-${TELEGRAM_QTA_CHAT_ID:-}}}"
export TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

# graceful shutdown
trap 'echo "[cron_loop] received TERM, exiting"; exit 0' TERM INT

log() { echo "[cron_loop $(date +%H:%M:%S)] $*"; }

# 다음 발화까지 sleep 초 계산 (KST 기준).
seconds_until_next_run() {
    local now_h now_m now_s target now_total target_total diff
    now_h=$(date +%H); now_m=$(date +%M); now_s=$(date +%S)
    # 10진수 강제 (08, 09 등 octal 해석 회피)
    now_h=$((10#$now_h)); now_m=$((10#$now_m)); now_s=$((10#$now_s))
    target=$((10#$REPORT_HOUR_KST))
    now_total=$((now_h * 3600 + now_m * 60 + now_s))
    target_total=$((target * 3600))
    if [ "$now_total" -lt "$target_total" ]; then
        diff=$((target_total - now_total))
    else
        diff=$((86400 - now_total + target_total))
    fi
    echo "$diff"
}

run_report() {
    local wal_file today report_file
    wal_file=$(find "$LOG_DIR" -name 'wal.jsonl' -type f 2>/dev/null | sort | tail -1)
    if [ -z "$wal_file" ]; then
        log "no WAL file found in $LOG_DIR; skipping report"
        return 0
    fi
    today=$(date +%Y-%m-%d)
    report_file="$REPORTS_DIR/$today.md"
    log "generating $report_file from $wal_file"
    if python /app/scripts/live_report.py \
            --wal "$wal_file" \
            --date "$today" \
            --out "$report_file"; then
        log "report OK: $report_file"
    else
        log "live_report.py failed (exit $?)"
        return 1
    fi
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] && [ -f "$report_file" ]; then
        log "sending telegram summary"
        python /app/scripts/telegram_alert.py --report "$report_file" \
            || log "telegram alert failed (non-fatal)"
    else
        log "TELEGRAM_BOT_TOKEN/CHAT_ID not set; skipping summary"
    fi
}

log "cron_loop starting (LOG_DIR=$LOG_DIR REPORTS_DIR=$REPORTS_DIR REPORT_HOUR_KST=$REPORT_HOUR_KST)"

while true; do
    sleep_sec=$(seconds_until_next_run)
    log "sleeping ${sleep_sec}s until ${REPORT_HOUR_KST}:00 KST"
    sleep "$sleep_sec" &
    wait $! || true  # SIGTERM 시 즉시 깨움
    run_report || true
    # 동일 시각 중복 발화 방지: 60초 버퍼
    sleep 60 &
    wait $! || true
done
