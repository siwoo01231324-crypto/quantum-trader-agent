---
type: runbook
id: universe-scan-runbook
name: Universe-Scan Strategy Operations Runbook
status: active
severity: medium
owner: siwoo
created: 2026-05-08
last_updated: 2026-05-08
tags:
- runbook
- universe-scan
- operations
---

# Universe-Scan Strategy Operations Runbook

universe-scan 패턴 (#218) 기반 전략의 paper / live 운영을 위한 점검·트러블슈팅 절차. 전략 일반 spec → [[universe-scan-strategy-pattern]].

## 정상 동작 시나리오

### 주간 리밸 (KRX 일봉, 매 금요일 KST 15:30)

1. `live_run.py` 가 트리거 시각에 `should_rebal()` true 판정
2. `src/universe/krx_top.py` `combined_top_n(snapshot, kospi_n=200, kosdaq_n=150)` 호출 → 350개 코드 리스트
3. 각 전략 모듈 (`cs_*_kr.py`) 의 `compute_weights(close_panel, turnover_panel, ...)` 호출
4. weights 비교 → 이번 주 picks vs 직전 주 picks 차이 산출
5. `weights → orders` 변환 (단주 반올림, 잔여 현금)
6. paper broker 또는 KIS broker 로 발주
7. Telegram rebal report 발송

### 정상 신호

`docker logs qta-live-daemon --tail 200`:
```
[INFO] universe_built strategy=cs_tsmom_kr_daily codes=347 (target=350)
[INFO] weights_computed strategy=cs_tsmom_kr_daily holdings=20 turnover=0.30
[INFO] orders_placed strategy=cs_tsmom_kr_daily buys=4 sells=4 holds=16
[INFO] telegram_sent kind=weekly_rebal strategy=cs_tsmom_kr_daily
```

## 트러블슈팅

### Universe stale (시총 데이터 1주 이상 미갱신)

- 증상: rebal 시점에 universe 가 작음 (예: 350 목표 → 200 fetched), 또는 fetch 실패 alert.
- 원인: FDR / Binance API 일시 장애 또는 rate-limit.
- 조치:
  1. `python scripts/fetch_krx_marcap_snapshot.py --refresh` (또는 binance 동등) 으로 직접 재수집.
  2. 캐시 디렉토리 (`data/cache/marcap_snapshot/`) 권한 / 디스크 확인.
  3. 직전 정상 스냅샷 fall-back 정책 (1주일 이내 stale 허용) → 그 이상이면 paper STOP.

### 리밸 시점 KIS rate-limit (#212/#213) spike

- 증상: 매주 금요일 15:30 직후 EGW00201 rate-limit 폭주, 일부 종목 발주 실패.
- 원인: 350 종목 호가 fetch + 20+ 주문 동시 발생.
- 조치:
  1. broker 의 backoff 로직 (#213) 가 동작 중인지 로그 확인 (`backoff_applied=true`).
  2. `attempt 3/3 final failure` count > 0 이면 미체결 종목 수동 점검.
  3. 미체결 종목은 다음 봉 (16:00) 에 retry — 단 미체결 + cash 잔여 시 risk evaluator 가 size 0 반환 가능.
  4. 영구 fix: `--rebal-stagger` 플래그로 호가 fetch 30초 간격 분산.

### Telegram noise (주간 알림 누락 또는 폭주)

- 증상: 주간 rebal report 누락 / 종목별 entry/exit alert 가 모두 발송됨.
- 원인: `TELEGRAM_LIVE_*` env 누락 → fallback chain 으로 wrong 봇.
- 조치:
  1. `scripts/telegram_alert.py _resolve_telegram_credentials` 확인 — `TELEGRAM_LIVE_TOKEN` / `TELEGRAM_LIVE_CHAT_ID` env 존재?
  2. 종목별 alert 가 올라오면 `WEEKLY_REBAL_DIGEST=true` env 설정 검증.
  3. 즉시 청산 (crash guard 발동) 알림은 **항상 즉시 발송** — 주간 다이제스트와 별개.

### Crash guard 발동

- 증상: KOSPI / BTC 252d drawdown ≤ -15% / -30% threshold → weights 전량 0.
- 정상 동작:
  1. 보유 종목 전량 청산 발주.
  2. Telegram 즉시 alert: `[CRASH GUARD] strategy=... exposure=0%`.
  3. 다음 리밸 (1주 후) 에 자동 재평가 — drawdown 회복되면 진입 재개.

### 백테스트 vs 라이브 발산

- 증상: 백테스트 Sharpe 0.871 인데 라이브 Sharpe 가 큰 폭으로 낮음 (예: 0.3 미만).
- 가능 원인:
  1. **Survivorship bias** — current Marcap universe → 라이브에서는 감지 못 한 부진 종목 영향. 보수적 기대치 수용.
  2. **Slippage 과소 추정** — 백테스트 25-55bp 단순 차감, 라이브 KOSDAQ 소형주 슬리피지 더 큼. KOSDAQ pick 비중 모니터링.
  3. **Universe drift** — 시총 top-N 이 분기마다 회전 → backtest 의 fixed pin-date 와 라이브 dynamic universe 차이.
  4. 후속 정밀화: PIT 시총 스냅샷 도입.

## 관련 노트

- [[universe-scan-strategy-pattern]]
- [[cs-tsmom-kr-daily]]
- [[cs-tsmom-crypto-daily]]
