---
type: work-done
id: 000152-kis-1m-cron-ops-00-issue
name: "#152 KIS 1분봉 cron 운영 시작 + 누적 데이터 모니터링"
status: active
---

# chore: KIS 1분봉 cron 운영 시작 + 누적 데이터 모니터링 (#97 후속)

## 사용자 관점 목표
#97 v5 인프라 완성. KIS API 한계로 historical 분봉 적재 불가능 → 매일 cron 으로 1일치씩 누적이 유일한 경로. 본 이슈는 cron 운영 + 주간 모니터링 체계 가동.

## 배경
#97 v5 검증에서 확정: KIS `FHKST03010200` 분봉 API 는 `FID_INPUT_DATE_1` 무시 → 오직 당일만 조회 가능. 30일 backfill 자체가 KIS 에서 지원 안 함. 매일 cron 누적 → 90일 후 약 28,000 events 확보 가능 (이벤트 빈도 그대로면).

## 완료 기준
- [x] env 매핑 — `HANTOO_FAKE_API_KEY` / `HANTOO_FAKE_SECRET_API_KEY` / `HANTOO_CREDIT_NUMBER` (#133 SECRETS.md 표준) 우선, 레거시 `KIS_APP_KEY` 등 fallback (`resolve_kis_credentials` 헬퍼). 시우님 `.env` 는 #133 운영에서 이미 등록됨.
- [x] cron 등록 인프라 — `docker-compose.live.yml` 에 `kis-1m-fetch-cron` service 추가 (4번째 컨테이너). bash sleep-loop (`scripts/kis_1m_fetch_loop.sh`) 가 평일 KST 16:00 발화. 별도 crontab/Task Scheduler 불필요.
- [x] 명령: `python scripts/cron_fetch_kis_daily.py --n-pool 30 --interval 1m` — 컨테이너 entrypoint 가 자동 실행.
- [ ] 첫 주 (5 거래일) 누적 검증 — `docker compose -f docker-compose.live.yml up -d` 후 5 거래일 경과 시 `python scripts/kis_lake_monitor.py` 로 검증 (운영 후 가능, PR 머지 후 시우님 직접).
- [x] 주간 누적 모니터 인프라 — `scripts/kis_lake_monitor.py` 가 종목별 bar 수 / 거래일 / first/last ts / 진척도 (X/90) markdown digest 산출. 일요일 KST 16:00 자동 Telegram 발송.
- [x] cron 실패 알림 채널 — 매 fetch 결과 + 주간 누적 모두 Telegram (`scripts/telegram_alert.py:send_telegram` 재사용). 토큰 미설정 시 silent skip.

## 운영 시작 체크리스트 (시우님 직접 — PR 머지 후)
- [ ] `.env` 에 `HANTOO_FAKE_API_KEY` / `HANTOO_FAKE_SECRET_API_KEY` / `HANTOO_FAKE_CREDIT_NUMBER` 등록 확인 (이미 등록됨)
- [ ] `.env` 에 `TELEGRAM_LIVE_BOT_TOKEN` / `TELEGRAM_LIVE_CHAT_ID` 등록 확인 (이미 등록됨, `@quantum_trader_live_bot` 단일 채널)
- [ ] **컨테이너 재빌드 필수** — 현재 운영 컨테이너는 #213 (KIS rate-limit backoff fix) + #152 (텔레그램 fallback) 머지 전 이미지로, EGW00201 으로 시세 못 받는 중 + 텔레그램 silent skip 중. `docker compose -f docker-compose.live.yml down && build && up -d` 로 4 service 재기동
- [ ] `docker compose -f docker-compose.live.yml logs -f live-daemon` 에서 EGW00201 retry 빈도가 정상 수준으로 감소하고 trade event 가 WAL 에 쌓이는지 확인
- [ ] `docker compose -f docker-compose.live.yml logs -f kis-1m-fetch-cron` 로 첫 발화 확인 (다음 평일 KST 16:00)
- [ ] 첫 발화 다음날: `lake/ohlcv/freq=1m/year=2026/month=05/symbol=*/part-0.parquet` 30종목 생성 확인
- [ ] LIVE 봇 (`@quantum_trader_live_bot`) 으로 매 fetch 결과 + 다음 평일 16:00 일일 리포트 + 다음 일요일 16:00 주간 summary 도착 확인

## 의존성
- **#97 머지 필수** (cron 스크립트 + 30종목 universe) ✅ (2026-05-03 COMPLETED)
- KIS 인증 토큰 영구 보관

## 주의사항
- **KIS 토큰 만료**: 24시간 (`_MIN_REISSUE_INTERVAL_SEC=60`). cron 실행 시 자동 재발급
- **rate limit**: 분당 한도. 30종목 × 13페이지 ≈ 390 요청/일. `--sleep-between 0.6` default 안전
- **휴장일**: `is_krx_holiday` 자동 skip
- **lake 누적 사이즈**: 30종목 × 8,600 bars/일 × 90일 ≈ 23M rows. parquet 압축 후 약 100MB 예상

## 후속
- 90일+ 누적 시 #97 후속 B-2 (가설 본판정) 트리거 — `#153` 으로 분리

---

## 작업 내역
- 2026-05-06: `/si 152` 워크트리 생성, Backlog → Ready 이동, assign 완료. #97 머지 확인 완료.
- 2026-05-06: 옵션 A 구현 완료 (TDD Red→Green).
  - `tests/scripts/test_kis_lake_monitor.py` 신규 — scan_lake/aggregate_stats/render_markdown/CLI 9 케이스
  - `scripts/kis_lake_monitor.py` 신규 — hive lake walk + 종목별 (bar 수, 거래일, first/last ts) + 누적 진척도 markdown digest + Telegram 발송 옵션
  - `scripts/cron_fetch_kis_daily.py` — `resolve_kis_credentials` 헬퍼 추출 (`HANTOO_FAKE_*` 우선, `KIS_APP_KEY` fallback, `HANTOO_FAKE_CREDIT_NUMBER`/`HANTOO_CREDIT_NUMBER` dash 분할)
  - `tests/scripts/test_cron_fetch_kis_env.py` 신규 — env 매핑 6 케이스 (primary/fallback/우선순위/empty/dash 미사용 polyfill + `HANTOO_FAKE_CREDIT_NUMBER` 우선순위)
  - `scripts/kis_1m_fetch_loop.sh` 신규 — `cron_loop.sh` 패턴 차용. 평일 KST 16:00 fetch + 매 결과 Telegram, 일요일 kis_lake_monitor 주간 summary Telegram
  - `docker-compose.live.yml` — `kis-1m-fetch-cron` service 추가 (4번째 컨테이너)
  - `scripts/.ai.md` — 신규 3개 스크립트 행 추가 + Phase 2 운영 4 service 갱신
- 2026-05-07: simplify 리뷰 + 텔레그램 라우팅 진단 → 3건 fix (CRITICAL).
  - `scripts/cron_fetch_kis_daily.py:resolve_kis_credentials` 에 `HANTOO_FAKE_CREDIT_NUMBER` 1순위 추가. 시우님 paper trading `.env` 가 이 키를 쓰는데 누락 시 모든 fetch 가 silent skip 되는 버그.
  - 텔레그램 변수명 mismatch + 봇/채팅 라우팅 수정. 시우님 `.env` 는 `TELEGRAM_QTA_*`/`TELEGRAM_LIVE_*` 만 가짐. 진단 결과 QTA 봇은 `/start` 이력 없어 `chat not found (400)` — LIVE 봇은 정상 발송 확인. **모든 알림을 LIVE 봇 단일 채널로 통일** 결정 (시우님 컨펌).
    - `scripts/telegram_alert.py:_resolve_telegram_credentials()` 헬퍼 추가 — fallback chain `TELEGRAM_LIVE_*` (1순위) > `TELEGRAM_QTA_*` (2순위) > legacy `TELEGRAM_BOT_TOKEN/CHAT_ID`.
    - `scripts/kis_1m_fetch_loop.sh` + `scripts/cron_loop.sh` 시작부에 동일 우선순위 alias export 추가.
    - `src/observability/alerts.py:notify()` 도 동일 fallback chain 적용 (`_resolve_telegram_env()` 헬퍼). → metalabeler 주간 재학습 등 ML pipeline 알림도 LIVE 봇으로 통일.
    - → #133 운영 daemon 의 텔레그램 알림 (운영 시작 후 안 오던 것) 도 동시 해결.
  - `tests/scripts/test_telegram_alert.py` + `tests/observability/test_alerts.py` 에 fallback chain 케이스 추가 (LIVE priority, QTA fallback, legacy fallback). 기존 테스트는 `_clear_telegram_env` 헬퍼로 격리.
  - 회귀: `tests/scripts/` 57/57 + `tests/observability/test_alerts.py` 6/6 통과. `check_invariants --strict` 통과 (182 노트). bash 문법 OK. 실 LIVE 봇 발송 테스트 200 OK 확인.
