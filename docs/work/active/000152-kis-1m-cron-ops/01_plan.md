---
type: work-plan
id: 01_plan
---

# 01_plan — #152 KIS 1분봉 cron 운영 시작 + 누적 데이터 모니터링

> ⚠️ 이 문서는 `/si` 가 생성한 **AC 체크리스트 초안**이다.
> 구현 시작 전 반드시 `/plan` 으로 구체적 운영 계획을 채워야 한다.

## AC 체크리스트
- [ ] KIS 인증 환경변수 영구 설정 — `.env` 또는 시스템 env (`KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_CANO`, `KIS_ACNT_PRDT_CD`)
- [ ] crontab/Task Scheduler 등록 — `0 7 * * 1-5 ...` UTC = 16:00 KST 평일
- [ ] cron 실행 명령: `python scripts/cron_fetch_kis_daily.py --n-pool 30 --interval 1m`
- [ ] 첫 주 (5 거래일) 누적 검증 — 종목당 5×8,602≈43,010 bars 도달 확인
- [ ] 주간 누적 모니터 노트 (`02_implementation.md`): bar 수 / 종목별 결측 / VI 빈도 / cron 실행 로그
- [ ] cron 실패 알림 채널 (telegram QTA 봇)

## 의존성 / 사전 확인
- [x] #97 머지 확인 (2026-05-03 COMPLETED) — `cron_fetch_kis_daily.py` + 30종목 universe 존재 가정
- [ ] `scripts/cron_fetch_kis_daily.py` 현재 스펙·옵션 정독 (--n-pool, --interval, --sleep-between, lake 경로 등)
- [ ] `src/universe/krx_pool.py` (또는 동등한 30종목 풀 정의) 확인
- [ ] 현재 `.env` 의 KIS_* 변수 상태 점검 (이미 #133 운영 셋업에서 등록됐을 가능성)
- [ ] Telegram QTA 봇 토큰/chat_id 환경변수 (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) 등록 여부 점검 (#133)

## 운영 환경 결정 (확정)
- **Docker Desktop 컨테이너 추가** — #133 운영 패턴 재사용 (`docker-compose.live.yml` 에 4번째 service 추가).
- env 변수: 시우님 `.env` 의 `HANTOO_FAKE_*` 표준명 사용 (KIS broker config 와 일관).
- 알림: **매 fetch 결과 telegram + 주간 누적 summary**.

## 진단 (현행)

| 위치 | 현상 |
|------|------|
| `scripts/cron_fetch_kis_daily.py:150-199` | env 변수가 `KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_CANO`/`KIS_ACNT_PRDT_CD` 로 분리. 시우님 `.env` 의 `HANTOO_FAKE_*` 와 매핑 안 됨 |
| `src/brokers/config.py:13-15, 35-46` | 표준은 `HANTOO_FAKE_API_KEY`, `HANTOO_FAKE_SECRET_API_KEY`, `HANTOO_CREDIT_NUMBER` (`^[0-9]{8}-[0-9]{2}$` 형식, dash 로 cano/acnt_prdt_cd 분할) |
| `docker-compose.live.yml` | `live-daemon` + `report-cron` + `telegram-notifier` 3 service. KIS 1m fetch service 없음 |
| `scripts/cron_loop.sh` | sleep-loop 패턴 (`seconds_until_next_run` + `run_report` + 60s buffer). 그대로 차용 가능 |
| `scripts/telegram_alert.py` | `send_telegram` 헬퍼 + `--report PATH` 모드 노출. fetch 결과 요약 발송에 그대로 활용 |

## 구현 계획 (옵션 A — 확정)

### 변경 파일

| # | 변경 | 파일 |
|---|------|------|
| 1 | env 매핑 fix — `HANTOO_FAKE_API_KEY`/`HANTOO_FAKE_SECRET_API_KEY`/`HANTOO_CREDIT_NUMBER` (dash 분할) 우선, `KIS_APP_KEY` 등은 fallback | `scripts/cron_fetch_kis_daily.py` |
| 2 | KIS 1분봉 fetch loop 신규 — KST 16:00 매 평일 fetch + 매 결과 telegram + 주간 summary | `scripts/kis_1m_fetch_loop.sh` |
| 3 | lake 누적 모니터 신규 — bar 수/종목별 결측/마지막 fetch ts/누적 거래일 → markdown 요약 | `scripts/kis_lake_monitor.py` |
| 4 | `kis-1m-fetch-cron` service 추가 + `kis-weekly-summary` 별도 weekly trigger | `docker-compose.live.yml` |
| 5 | monitor 단위 테스트 — synthetic parquet → bar count + 결측 + last_ts 검증 | `tests/scripts/test_kis_lake_monitor.py` (신규) |

### Fetch 흐름

```
매 평일 KST 16:00 (kis_1m_fetch_loop.sh)
  ├─ python scripts/cron_fetch_kis_daily.py --n-pool 30 --interval 1m
  │     ├─ HANTOO_FAKE_* 자격 로드
  │     ├─ KIS API → 30종목 그날 1분봉
  │     └─ lake/ohlcv/freq=1m/... 에 parquet 저장
  ├─ exit code 0 → "✅ KIS fetch OK: 30종목 / 누적 N bars" telegram
  └─ exit code != 0 → "❌ KIS fetch FAIL ..." telegram + stderr 첨부

매 일요일 KST 09:00 (별도 trigger 또는 sleep-loop 분기)
  └─ python scripts/kis_lake_monitor.py --weekly
        └─ 종목별 bar 수, 결측 거래일, 누적 거래일, 진척도 (X/90) → markdown → telegram
```

### Docker compose service

```yaml
kis-1m-fetch-cron:
  image: qta-phase2:latest
  entrypoint: ["/bin/bash", "/app/scripts/kis_1m_fetch_loop.sh"]
  env_file: ./.env
  environment:
    TZ: Asia/Seoul
    LAKE_DIR: /data/lake
    FETCH_HOUR_KST: "16"  # KRX 마감 30분 후
    WEEKLY_SUMMARY_DAY: "0"  # 0=일요일
    WEEKLY_SUMMARY_HOUR: "9"  # KST 09:00
    N_POOL: "30"
    INTERVAL: "1m"
  volumes:
    - ./lake:/data/lake
    - ./logs/kis-fetch:/data/logs
  restart: unless-stopped
  deploy:
    resources:
      limits:
        memory: 256M
```

## TDD 순서

1. **Red**: `tests/scripts/test_kis_lake_monitor.py` — synthetic hive-partitioned parquet → bar 수 / 결측 거래일 / last_ts / 진척도 검증.
2. **Green**: `scripts/kis_lake_monitor.py` 구현.
3. env 매핑 fix — `cron_fetch_kis_daily.py` 의 env read 함수 헬퍼 추출 + 단위 테스트.
4. `kis_1m_fetch_loop.sh` 작성 — `cron_loop.sh` 패턴 차용 + telegram per-fetch + weekly trigger 분기.
5. `docker-compose.live.yml` service 추가.
6. 수동 dry-run 검증: `python scripts/cron_fetch_kis_daily.py --dry-run --n-pool 30 --interval 1m` 으로 자격 매핑 확인.

## 영향 범위
- `scripts/cron_fetch_kis_daily.py` (env 매핑 헬퍼 추가)
- `scripts/kis_1m_fetch_loop.sh` (신규)
- `scripts/kis_lake_monitor.py` (신규)
- `docker-compose.live.yml` (service 1개 추가)
- `tests/scripts/test_kis_lake_monitor.py` (신규)
- `scripts/.ai.md` (3개 신규 스크립트 등록)

## 비목표
- 새 종목 추가 (현재 #133 의 005930+035720+000660 외 30종목 universe 는 `krx_pool.get_pool_codes(30)` 가 이미 결정)
- 분봉 외 interval (15m/5m 등) 추가
- lake 스키마 변경
- Phase B 본판정 로직 (`#153` 별도)

## 리스크
- **KIS 토큰 만료**: 24h. cron 실행 시 자동 재발급 (#127 후속 fix 머지됨)
- **rate limit**: 30종목 × 약 13페이지 ≈ 390 요청/일. `--sleep-between 0.6` default 안전
- **휴장일**: `cron_fetch_kis_daily.py` 가 빈 dataframe 반환. 빈 결과를 fail 로 오판하지 않도록 telegram 알림 분기 — exit 0 + 빈 df = "휴장 skip" 로그, exit 0 + non-empty = OK, exit !=0 = FAIL
- **시우님 PC 절전**: #133 운영에서 이미 절전 끄기 가이드 존재 (`scripts/local_setup_windows.md`). 동일 가정
- **telegram 스팸**: 매 fetch 알림이 평일 1회 = 주 5회. 운영자가 받아들일 만한 빈도. 추가로 weekly summary 1회.

## 영향 범위
- `docker-compose.live.yml` (또는 별도 cron 셋업)
- `scripts/cron_fetch_kis_daily.py` (안정성 보강 가능성)
- `scripts/telegram_alert.py` (cron 실패 알림 hook)
- `docs/work/active/000152-kis-1m-cron-ops/02_implementation.md` (주간 모니터)

## 리스크 / 비목표
- **비목표**: 새 종목 추가, 분봉 외 interval 추가, lake 스키마 변경
- **리스크**: KIS 토큰 만료/재발급 실패 → cron 무음 실패. 알림 hook 필수
- **리스크**: 휴장일/장애 시간 lake 누적 결측 → 주간 모니터에서 결측 카운트
- **리스크**: 디스크 공간 — 90일 후 ~100MB 예상이지만 압축 비효율 시 더 클 수 있음
