#!/bin/bash
# Universe-scan paper rebalance cron (#218 후속).
#
# 평일 KRX 금요일 마감 (15:30 KST) → KRX universe-scan 전략 발주
# 일요일 00:00 UTC → Crypto universe-scan 전략 발주
#
# kis_1m_fetch_loop.sh 패턴 차용. sleep loop + 분 단위 시간 체크.
# 결과 + Telegram digest 는 cron_paper_universe_rebal.py 가 자동 발송.

set -euo pipefail

cd /app

# #231 S6 — stdout 손실 fix. 모든 echo 를 docker logs + 호스트 마운트 로그파일
# 양쪽에 기록. 기존엔 docker daemon log rotation 으로 lifetime 누적 stdout
# 0 lines 발생 (#231 진단 — 5/10 첫 가동 시 발주 시도 stdout 추적 불가).
# PYTHONUNBUFFERED=1 (compose env) + tee 조합으로 즉시 flush + 호스트 영구 보존.
LOOP_LOG_DIR="${LOG_DIR:-/data/logs}"
mkdir -p "$LOOP_LOG_DIR"
exec > >(tee -a "$LOOP_LOG_DIR/universe-rebal-loop.log") 2>&1

KRX_REBAL_HOUR_KST="${KRX_REBAL_HOUR_KST:-15}"   # 15시
KRX_REBAL_MIN_KST="${KRX_REBAL_MIN_KST:-32}"     # 마감 후 2분 (15:32)
CRYPTO_REBAL_HOUR_UTC="${CRYPTO_REBAL_HOUR_UTC:-0}"  # 00:00 UTC
CRYPTO_REBAL_DOW_UTC="${CRYPTO_REBAL_DOW_UTC:-0}"    # 0=Sunday

KRX_STRATEGIES="${KRX_STRATEGIES:-cs-tsmom-kr-daily cs-rsi-div-kr cs-adx-ma-kr}"
CRYPTO_STRATEGIES="${CRYPTO_STRATEGIES:-cs-tsmom-crypto-daily cs-rsi-div-crypto cs-macd-vol-crypto}"

LAST_RUN_FILE="${LAST_RUN_FILE:-/data/logs/universe-rebal-last-run.txt}"
mkdir -p "$(dirname "$LAST_RUN_FILE")"
touch "$LAST_RUN_FILE"

run_strategy() {
    local sid="$1"
    echo "[$(date +%FT%T%z)] universe_rebal start strategy=$sid"
    python scripts/cron_paper_universe_rebal.py --strategy "$sid" --log-level INFO || {
        echo "[$(date +%FT%T%z)] universe_rebal FAIL strategy=$sid (exit $?)"
        return 1
    }
    echo "[$(date +%FT%T%z)] universe_rebal complete strategy=$sid"
}

last_krx_date=""
last_crypto_date=""

if [ -s "$LAST_RUN_FILE" ]; then
    last_krx_date=$(grep "^krx=" "$LAST_RUN_FILE" | tail -1 | cut -d= -f2 || echo "")
    last_crypto_date=$(grep "^crypto=" "$LAST_RUN_FILE" | tail -1 | cut -d= -f2 || echo "")
fi

while true; do
    # KST 기준 (KRX)
    KST_NOW=$(TZ=Asia/Seoul date +%FT%H:%M)
    KST_DATE=$(TZ=Asia/Seoul date +%F)
    KST_DOW=$(TZ=Asia/Seoul date +%u)        # 1=Mon..7=Sun
    KST_HOUR=$(TZ=Asia/Seoul date +%H | sed 's/^0//')
    KST_MIN=$(TZ=Asia/Seoul date +%M | sed 's/^0//')

    # KRX rebal: 금요일 (DOW=5) + KRX_REBAL_HOUR_KST + KRX_REBAL_MIN_KST 이후 + 오늘 미실행
    if [ "$KST_DOW" = "5" ] && \
       [ "$KST_HOUR" -ge "$KRX_REBAL_HOUR_KST" ] && \
       { [ "$KST_HOUR" -gt "$KRX_REBAL_HOUR_KST" ] || [ "$KST_MIN" -ge "$KRX_REBAL_MIN_KST" ]; } && \
       [ "$KST_DATE" != "$last_krx_date" ]; then
        for sid in $KRX_STRATEGIES; do
            run_strategy "$sid" || true
        done
        last_krx_date="$KST_DATE"
        echo "krx=$KST_DATE" >> "$LAST_RUN_FILE"
    fi

    # UTC 기준 (Crypto)
    UTC_DATE=$(date -u +%F)
    UTC_DOW=$(date -u +%w)                   # 0=Sun..6=Sat
    UTC_HOUR=$(date -u +%H | sed 's/^0//')

    if [ "$UTC_DOW" = "$CRYPTO_REBAL_DOW_UTC" ] && \
       [ "$UTC_HOUR" -ge "$CRYPTO_REBAL_HOUR_UTC" ] && \
       [ "$UTC_DATE" != "$last_crypto_date" ]; then
        for sid in $CRYPTO_STRATEGIES; do
            run_strategy "$sid" || true
        done
        last_crypto_date="$UTC_DATE"
        echo "crypto=$UTC_DATE" >> "$LAST_RUN_FILE"
    fi

    # 5분 sleep — 시간 체크 빈도 + 부하 trade-off
    sleep 300
done
