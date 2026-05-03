# [#133] Phase 2 KIS 모의계좌 4주 실측 운영 — 작업 계획 (초안)

> 작성: 2026-04-27
> 본 문서는 `/start-issue` 가 생성한 **AC 체크리스트 초안** 이다.
> 구현 시작 전 `/plan 133` 으로 구체적 운영 플랜을 작성해야 한다.

## 완료 기준 — Exit Criteria

- [ ] AC2: 20 거래일 (약 4주) 실측 운영 로그 (`reports/{YYYY-MM-DD}.md` daily, distinct dates ≥ 20)
- [ ] AC3: 실측 placed ≥ 100 AND filled ≥ placed * 0.95
- [ ] AC4: tracking_error = mean(|kis_fill_price - sim_fill_price| / sim_fill_price) p95 < 0.5%
- [ ] 일일 cron 으로 `scripts/live_report.py` 자동 실행
- [ ] kill-switch 자동 트리거 발생 시 사후 검토 + WAL 보존
- [ ] 자동 롤백 트리거 5종 (R1~R5) 발동 이력 0건 또는 사후 분석 문서화
- [ ] ADR 작성 (Decision / Drivers / Alternatives / Why-chosen / Consequences / Follow-ups)
- [ ] 운영자 2인 + 1 reviewer 승인
- [ ] Phase 3 (#107) 진입 결정 게이트 통과 또는 롤백 결정

## 의존성

- ✅ #105 머지 (PR #144, 2026-04-26) — 코드/테스트 51/51 pass, .ai.md 갱신, 02_implementation.md, Grafana 대시보드 6 패널
- ✅ nightly E2E (`.github/workflows/kis-paper-nightly.yml`) — KST 월~금 10:00 cron
- ✅ KIS 모의계좌 키 (HANTOO_FAKE_*) GitHub Secret 등록 완료
- ✅ 로컬 .env 에도 키 설정 완료 (실증 OK: 잔고 1천만원 정확 출력)

## 운영 절차 (이슈 본문 요약)

1. **daemon 시작**: `python scripts/live_run.py --broker kis-paper-shadow --symbols 005930,... --duration 4w --auto-fallback --schedule krx`
2. **일일 cron**: `0 16 * * 1-5` (KRX 마감 후) → `scripts/live_report.py --wal /path/to/wal --date $(date +%F) --out reports/$(date +%F).md`
3. **주간 리뷰**: `docs/background/29-paper-to-live-protocol.md` §8.2 의제 (PnL/Sharpe/MDD, Tracking Error 추세, kill-switch 이력, WS 단절률)
4. **자동 롤백 모니터링** (live_report 내장):
   - R1: KIS 5xx > 10% (15분) → daemon halt + PaperBroker 폴백
   - R2: 체결 누락 ≥ KIS_FILL_MISSING_HALT_THRESHOLD (default 1) → halt
   - R3: tracking_error > 0.5% 5분 연속 → halt + 폴백
   - R4: 토큰 재발급 실패 연속 3회 → halt
   - R5: 잔고 불일치 > 1% → halt
5. **종료 게이트**: 20 거래일 + AC2~4 모두 충족 → 운영자 2인 승인 → ADR 작성 → Phase 3 (#107) 진입 결정

## 산출물

- `docs/work/active/000133-phase2-operation/02_operation.md` (운영 일지)
- `docs/work/active/000133-phase2-operation/reports/*.md` (20+ 일일 리포트)
- `docs/work/active/000133-phase2-operation/03_adr.md` (ADR)

## 주의사항

- LLM 라이브 결정 직접 개입 금지 (CLAUDE.md 불변식 #6)
- 실자금 사용 금지 (모의계좌만 — 실자금은 #107 Phase 3)
- 자동 commit 금지 (사용자 승인 후 수동)

## 구현 계획

> 작성: 2026-04-27 (사용자 결정 후 직접 작성, ralplan 미사용 — 인프라 task 라 over-engineering 회피)

### 결정사항 (사용자 승인)

| 항목 | 결정 | 사유 |
|------|------|------|
| **daemon 환경** | **로컬 PC + Docker Desktop (Windows 11, x86)** | Oracle Cloud ARM 시도 → AD-1 capacity 부족 + single-AD region 으로 fallback 불가 → 로컬 채택. 0원, 즉시 시작. Phase 3 진입 시 클라우드 이전 검토 (#107) |
| **알림** | Telegram bot | 사용자 선호 |
| **일일 리포트 commit** | 파일 작성만 (cron) + Telegram 요약 발송. **git commit/push 는 사용자 주간 리뷰 후 수동** | CLAUDE.md "git push 전 사용자 확인" 불변식 준수 |
| **모니터링 stack** | Telegram + 파일 로그만 (prometheus/grafana 미설치) | ARM 1GB 컨테이너 부담 회피, Phase 3 부터 추가 검토 |
| **운영 시작 게이트** | 내일 (월) 한투 앱에서 nightly cron (KST 10:00) 첫 발화 확인 후 시작 | 코드 path 실증 후 진입 |
| **첫 1주 보수 운영** | `--max-orders 30` 제한 + tracking_error 일일 모니터링 | 안정 확인 후 풀 운영 |
| **거래 종목** | 005930 (삼성전자), 035720 (카카오), 000660 (SK하이닉스) — 3종 | 사용자 확정 |
| **운영 형태** | **1인 운영 (사용자 단독)** — 자가 승인 + ADR 작성 | Stage 8 의 "운영자 2인" 게이트는 "사용자 1인 자가 승인" 으로 대체 |

### Stage 1 — Docker 이미지 (의존성 0)

신규 파일:
- `Dockerfile` (multi-stage, python:3.12-slim base, ARM/AMD64 multi-arch)
- `docker-compose.yml` (live-daemon + report-cron + telegram-notifier 3 service)
- `.dockerignore` (.git, .venv, tests/fixtures, *.wal, .env 제외)

Dockerfile 구성:
```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && uv pip install --system .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY src ./src
COPY scripts ./scripts
ENV PYTHONPATH=/app/src:/app
USER 1000:1000
ENTRYPOINT ["python"]
```

검증:
- `docker buildx build --platform linux/arm64,linux/amd64 -t qta-phase2:latest .` → 둘 다 성공
- `docker run --rm qta-phase2:latest -m pytest tests/observability/ -q` → green

### Stage 2 — docker-compose 3 service (의존성: Stage 1)

```yaml
# docker-compose.yml (요지)
services:
  live-daemon:
    image: qta-phase2:latest
    command: ["scripts/live_run.py", "--broker", "kis-paper-shadow",
              "--symbols", "005930,035720,000660", "--duration", "4w",
              "--max-orders", "30",  # 첫 1주 (이후 환경변수로 풀어줌)
              "--auto-fallback", "--schedule", "krx",
              "--log-dir", "/data/logs"]
    env_file: .env
    volumes:
      - ./data/logs:/data/logs    # WAL + run logs
      - ./data/state:/app/.omc/state  # token cache, process lock
    restart: always
    healthcheck:
      test: ["CMD", "test", "-f", "/data/logs/.live_loop.lock"]
      interval: 60s
    deploy:
      resources: { limits: { memory: 512M } }

  report-cron:
    image: qta-phase2:latest
    entrypoint: ["/app/scripts/cron_loop.sh"]  # 신규 wrapper (Stage 3)
    env_file: .env
    volumes:
      - ./data/logs:/data/logs:ro
      - ./data/reports:/data/reports
    restart: always
    deploy:
      resources: { limits: { memory: 256M } }

  telegram-notifier:
    image: qta-phase2:latest
    entrypoint: ["python", "/app/scripts/telegram_alert.py", "--watch", "/data/logs"]  # 신규
    env_file: .env  # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    volumes:
      - ./data/logs:/data/logs:ro
    restart: always
    deploy:
      resources: { limits: { memory: 128M } }
```

검증:
- `docker compose config` 통과
- `docker compose up -d && docker compose ps` → 3 service healthy

### Stage 3 — cron wrapper + 일일 리포트 (의존성: Stage 1)

신규 파일:
- `scripts/cron_loop.sh` — Bash sleep-loop (cron daemon 별도 설치 회피, 컨테이너 단순화)
  - 매일 KST 16:00 (KRX 마감 30분 후) 까지 sleep → `live_report.py --wal /data/logs/.../wal.jsonl --date $(date -u +%F) --out /data/reports/$(date -u +%F).md` 실행 → 다음 날까지 sleep
- `scripts/telegram_alert.py` — 신규
  - `--watch /data/logs` mode: WAL 의 `mode_switched` / `kill_switch_tripped` event tail → 즉시 Telegram 발송
  - `--report /data/reports/{date}.md` mode: 일일 리포트 생성 직후 trigger → 요약 (PnL, placed/filled, tracking_error, R1~R5 trip count) + 첨부

Telegram bot 설정:
1. 사용자가 @BotFather 로 bot 생성 → `TELEGRAM_BOT_TOKEN` 발급
2. bot 과 첫 대화 시작 → `https://api.telegram.org/bot{TOKEN}/getUpdates` 로 chat_id 조회 → `TELEGRAM_CHAT_ID` 등록
3. .env 에 두 변수 추가

검증:
- `python scripts/telegram_alert.py --test` → 실 Telegram 메시지 수신
- mock report 1건 생성 → telegram_alert.py 가 요약 발송

### Stage 4 — 로컬 PC + Docker Desktop 셋업 (의존성: Stage 1, 2, 3)

환경:
- Windows 11 (사용자 PC), x86_64
- Docker Desktop with WSL2 backend
- 작업 경로: `D:/project/quantum-trader-agent/.worktree/000133-phase2-operation/`
- bind mount: `./data/{logs,reports,state}` (호스트 경로 직접 inspect 가능)

사용자 직접 셋업 (`scripts/local_setup_windows.md` 가이드 신규):
1. **Docker Desktop 설치** — https://www.docker.com/products/docker-desktop
   - Settings → General → "Start Docker Desktop when you sign in" ✅
   - Settings → Resources → Memory **4GB+** 할당
   - Settings → Resources → Disk image size **50GB+** (4주 WAL 누적 ~1.5GB + Docker layer ~3GB)
2. **Windows 절전 / 재부팅 정책** (4주 24/7 운영 핵심)
   - 제어판 → 전원 옵션 → 모니터 끄기 / 절전 모드 → **사용 안 함**
   - Windows Update → "사용 시간" 설정 → 새벽 03:00 (KRX 외) 만 재부팅 허용
   - 노트북이면 덮개 닫기 시 동작 → "아무 것도 안 함"
   - 정전 위험 있으면 UPS 권고
3. **Docker 이미지 빌드** (Claude 가 만든 Dockerfile + compose 사용):
   ```powershell
   cd D:\project\quantum-trader-agent\.worktree\000133-phase2-operation
   docker compose build
   docker compose up -d
   docker compose ps          # 3 service healthy 확인
   docker compose logs -f live-daemon
   ```
4. **PC 재부팅 후 자동 시작**: Docker Desktop 시작 시 `restart: unless-stopped` policy 로 자동 재기동

검증:
- `docker compose ps` 3 service 모두 healthy
- 첫 KRX 영업일 09:00 KST 에 live-daemon 이 KIS 모의계좌 발주 → 한투 앱에서 거래 기록 확인
- Telegram 으로 daemon 시작 알림 수신

### Stage 5 — 운영 시작 게이트 (의존성: Stage 4 + nightly cron 첫 발화)

체크리스트:
- [ ] **내일 (월) KST 10:00** GitHub Actions nightly cron 발화 확인 (`gh run list -w kis-paper-nightly.yml`)
- [ ] 한국투자 모바일 앱 모의투자 메뉴에서 005930 1주 매수/매도 거래 기록 확인
- [ ] 거래 기록 정상 → VPS 에서 `docker compose up -d` 로 daemon 시작
- [ ] 첫 1시간 동안 `docker compose logs -f` 모니터링 (인증/주문/체결 path 정상)
- [ ] Telegram 알림 1회 수신 확인
- [ ] 첫 일일 리포트 (KST 16:00 발생) 생성 확인 → Telegram 요약 발송 확인

문제 발생 시:
- `docker compose down` → 즉시 중단
- WAL/logs 보존 → 사후 분석 → 원인 fix → PR

### Stage 6 — 첫 1주 보수 운영 (max-orders 30)

목적: 누적 30 주문 안에 다음 검증:
- daemon stability (4주 24/7 견딜 수 있는지)
- KIS API 안정성 (5xx rate, 토큰 갱신 주기)
- Tracking error 분포 (p95 < 0.5% 충족 가능성)
- Telegram 알림 빈도/유용성

매일 일일 리포트 review (사용자 매뉴얼):
- `~/qta/data/reports/{date}.md` 확인
- Telegram 요약과 일치 확인
- 이상치 발견 시 운영 일지 (`docs/work/active/000133-phase2-operation/02_operation.md`) 에 기록

7일 후 게이트:
- [ ] R1~R5 자동 halt 트리거 발생 0건 또는 1건만 (사후 검토 OK)
- [ ] daemon 다운타임 < 2시간 (네트워크 / KIS 점검 시간 제외)
- [ ] Tracking error p95 < 0.5%
- [ ] 통과 → max-orders 제한 해제 → 풀 운영

### Stage 7 — 풀 운영 3주 (max-orders 무제한)

목적: AC2/AC3/AC4 실측 데이터 누적
- 매주 금요일 마감 후 주간 리뷰 (29-paper-to-live-protocol.md §8.2)
- 누적 PnL/Sharpe/MDD, Tracking Error 추세, kill-switch 이력, WS 단절률

운영자 책임 분담:
- **개발자 (사용자)**: 일일 Telegram 알림 모니터링, 주간 리뷰 주관, 이상치 대응
- **2nd 검토자**: (지정 필요 — 또는 사용자 1인 운영 시 명시)

### Stage 8 — 4주 종료 + ADR + Phase 3 결정 (의존성: Stage 7)

종료 시점 (T+20 거래일):
1. `live_report.py` 가 AC2/AC3/AC4 모두 PASS 출력 확인
2. `docs/work/active/000133-phase2-operation/02_operation.md` 운영 일지 완성 (4주 누적)
3. **ADR 작성** (`docs/work/active/000133-phase2-operation/03_adr.md`):
   - **Decision**: Phase 3 진입 가/부
   - **Drivers**: AC 충족 데이터, 운영 안정성, 발견된 위험
   - **Alternatives**: (대안 — 슬리피지 모델 추가 후 재시도 / Partial fill 활성화 / 폐기)
   - **Why-chosen**: 정량 근거
   - **Consequences**: 실자금 5% 노출 위험, 롤백 정책
   - **Follow-ups**: #107 Phase 3 진입 또는 #109/#110 보강 후 #133 재시도
4. 운영자 2인 + 1 reviewer 승인 (slack thread 또는 GitHub PR review)
5. `docs/work/active/000133-phase2-operation/` → `docs/work/done/000133-phase2-operation/` 이동
6. 본 issue close, Phase 3 (#107) 진입 결정 게이트 ready

## 변경/생성 파일 목록

**신규 (8)**
- `Dockerfile` — multi-stage build, multi-arch (ARM/AMD64)
- `docker-compose.yml` — 3 service (live-daemon + report-cron + telegram-notifier)
- `.dockerignore` — .git/.venv/.env/*.wal 제외
- `scripts/cron_loop.sh` — bash sleep-loop wrapper (cron daemon 회피)
- `scripts/telegram_alert.py` — WAL tail + 일일 리포트 요약 → Telegram 발송
- `scripts/local_setup_windows.md` — Windows + Docker Desktop 24/7 운영 셋업 가이드 (절전 끄기, Docker 메모리 할당, 재부팅 정책)
- `docs/work/active/000133-phase2-operation/SECRETS.md` — 로컬 .env 에 추가해야 할 변수 목록 (값은 사용자 작성)
- `docs/work/active/000133-phase2-operation/02_operation.md` — 운영 일지 (4주 누적)

**수정 (3)**
- `.env.example` — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 추가
- `scripts/.ai.md` — cron_loop, telegram_alert, vps_bootstrap 추가
- `docs/work/active/000133-phase2-operation/00_issue.md` — 작업 내역 갱신

**테스트 (2)**
- `tests/scripts/test_telegram_alert.py` — mock requests, 메시지 형식 검증
- `tests/scripts/test_cron_loop_sh.py` — bash 스크립트 dry-run

**최종 산출물 (4주 후)**
- `docs/work/active/000133-phase2-operation/reports/*.md` (20+ 일일 리포트)
- `docs/work/active/000133-phase2-operation/03_adr.md` (ADR)

## 위험·엣지케이스

1. **로컬 PC 24/7 안정성 (가장 큰 위험)** — 정전, Windows Update 재부팅, Docker Desktop crash, 사용자 실수로 PC 종료 등.
   - 완화: 절전 끄기, Update 새벽 03:00 (KRX 외) 만 재부팅, `restart: unless-stopped` policy, UPS (가능 시), 일일 Telegram 알림으로 daemon alive 확인 (heartbeat — 24시간 무응답 시 사용자 PC 점검).
2. **로컬 네트워크 단절 → KIS WS reconnect 폭주** — `qta_broker_ws_reconnect_total{broker="kis"}` 1시간 10건 이상 시 Telegram alert 신설. 가정용 인터넷 안정성 (공유기 재부팅 / KT/SKT 점검 등) 영향.
3. **KRX 휴장일** — `--schedule krx` 가 KRX 캘린더를 어떻게 처리하는지 미검증. 일단 영업시간만 sleep, 휴장일 발주는 KIS 측 거부 코드로 reject (운영 일지에 기록).
4. **시간대** — Docker container `TZ=Asia/Seoul` 강제. cron_loop.sh 는 명시적 KST 사용. WAL ts 는 UTC 저장 + report 시 KST 변환.
5. **Telegram bot 토큰 노출** — `.env` git commit 금지 (.gitignore 확인). 로컬 .env 파일은 NTFS ACL 본인만 (Windows 권한).
6. **Docker 이미지 크기** — python:3.12-slim + native deps (lightgbm/scipy 컴파일) ~800MB-1.2GB. Docker Desktop 50GB 디스크에 충분.
7. **WAL 파일 크기 폭증** — 4주 운영 후 ~수GB 가능. cron_loop.sh 가 일일 rotate (`{date}.wal`).
8. **사용자 1인 운영 — 2nd reviewer 부재** — `02_operation.md` 에 "사용자 단독 운영 + 자가 승인" 명시. ADR 결정 시 self-review 책임 자각.
9. **Phase 3 (실자금) 진입 시 클라우드 이전** — 로컬 PC 운영은 Phase 2 (모의 자금) 한정. Phase 3 에서는 #107 plan 시 Oracle Cloud 재시도 또는 Lightsail 등 클라우드 환경 강제 (실자금 운영을 PC 정전 리스크에 노출 금지).

## 운영 모니터링 단계 (요약)

매일 (자동):
- KST 10:00 GitHub Actions nightly cron → KIS 모의계좌 smoke
- KST 09:00-15:30 daemon 발주 (`--schedule krx`)
- KST 16:00 일일 리포트 생성 → Telegram 요약 발송

매주 (사용자 매뉴얼):
- 금요일 마감 후 주간 리뷰 — `02_operation.md` 업데이트
- 일일 리포트 일괄 git commit + push (수동)

자동 halt 트리거 (Telegram 즉시 알림):
- R1: KIS 5xx > 10% (15분) → daemon halt + PaperBroker 폴백
- R2: 체결 누락 ≥ 1 → halt
- R3: tracking_error > 0.5% 5분 연속 → halt + 폴백
- R4: 토큰 재발급 실패 연속 3회 → halt
- R5: 잔고 불일치 > 1% → halt

## 다음 단계 (즉시 실행 순서)

1. **이 plan 검토 + 승인** (사용자 read + OK)
2. **Stage 1+2 구현** (Dockerfile + docker-compose) — 로컬에서 `docker compose up -d` smoke 검증
3. **Stage 3 구현** (cron + telegram alert) + Telegram bot 사용자 직접 생성 (BotFather)
4. **Stage 4 — Oracle Cloud 셋업** (사용자 수동: 콘솔에서 ARM instance 발급 + SSH 키 설정)
5. **VPS 배포 + smoke** (docker compose ps healthy + 첫 telegram 알림 수신)
6. **내일 (월) KST 10:00 nightly cron 확인** + 한투 앱 거래 기록 확인 → Stage 5 게이트 통과 시 daemon 시작
7. **첫 1주 보수 운영** (max-orders 30) + 일일 리포트 모니터링
8. **Stage 7 풀 운영 3주** + 주간 리뷰
9. **Stage 8 ADR + Phase 3 진입 결정**
