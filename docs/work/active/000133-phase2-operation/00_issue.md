# chore: Phase 2 KIS 모의계좌 4주 실측 운영 (#105 Stage 7b 후속)

## 목적
#105 (Phase 2 KIS 모의계좌 + AsyncOrderRouter) PR 머지 후, 4주(20 거래일) 가짜 돈 실측 운영을 통해 AC2/AC3/AC4 의 실측 검증과 Phase 3 진입 결정을 추적한다.

## 배경
#105 의 Stage 7a (PR 머지 게이트) 는 코드 + nightly E2E green 까지만 충족. AC2 (4주 실측), AC3 (실측 N≥100 + fill rate), AC4 (tracking_error p95 < 0.5% 실측) 와 Phase 3 진입 결정은 본 운영 이슈로 분리한다 (`docs/work/active/000105-phase2-paper-live/01_plan.md` Stage 7b).

## 의존성
- ✅ #105 머지 (코드/테스트 51/51 pass, .ai.md 갱신, 02_implementation.md, Grafana 대시보드 6 패널)
- nightly E2E (`.github/workflows/kis-paper-nightly.yml`) 동작 확인
- KIS 모의계좌 키 (HANTOO_FAKE_API_KEY / HANTOO_FAKE_SECRET_API_KEY / HANTOO_FAKE_CREDIT_NUMBER) 운영 환경 주입

## 완료 기준
- [ ] AC2: 20 거래일 (약 4주) 실측 운영 로그 (`reports/{YYYY-MM-DD}.md` daily, distinct dates ≥ 20)
- [ ] AC3: 실측 placed ≥ 100 AND filled ≥ placed * 0.95
- [ ] AC4: tracking_error = mean(|kis_fill_price - sim_fill_price| / sim_fill_price) p95 < 0.5%
- [ ] 일일 cron 으로 `scripts/live_report.py` 자동 실행
- [ ] kill-switch 자동 트리거 발생 시 사후 검토 + WAL 보존
- [ ] 자동 롤백 트리거 5종 (R1~R5) 발동 이력 0건 또는 사후 분석 문서화
- [ ] ADR 작성 (Decision / Drivers / Alternatives / Why-chosen / Consequences / Follow-ups)
- [ ] 운영자 2인 + 1 reviewer 승인
- [ ] Phase 3 (#107) 진입 결정 게이트 통과 또는 롤백 결정

## 구현 플랜
1. **운영 작업폴더 생성**: `docs/work/active/000xxx-phase2-operation/` (`02_operation.md` 일지, `reports/` 일일, `03_adr.md`)
2. **daemon 시작**: \`python scripts/live_run.py --broker kis-paper-shadow --symbols 005930,... --duration 4w --auto-fallback --schedule krx\`
3. **일일 cron**: \`0 16 * * 1-5\` (KRX 마감 후) → \`scripts/live_report.py --wal /path/to/wal --date \$(date +%F) --out reports/\$(date +%F).md\`
4. **주간 리뷰**: \`docs/background/29-paper-to-live-protocol.md\` §8.2 의제 (PnL/Sharpe/MDD, Tracking Error 추세, kill-switch 이력, WS 단절률)
5. **자동 롤백 모니터링** (live_report 내장):
   - R1: KIS 5xx > 10% (15분) → daemon halt + PaperBroker 폴백
   - R2: 체결 누락 ≥ KIS_FILL_MISSING_HALT_THRESHOLD → halt
   - R3: tracking_error > 0.5% 5분 연속 → halt + 폴백
   - R4: 토큰 재발급 실패 연속 3회 → halt
   - R5: 잔고 불일치 > 1% → halt
6. **종료 게이트**: 20 거래일 + AC2~4 모두 충족 → 운영자 2인 승인 → ADR 작성 → Phase 3 (#107) 진입 결정

## 주의사항
- LLM 라이브 결정 직접 개입 금지 (CLAUDE.md 불변식 #6)
- 실자금 사용 금지 (모의계좌만 — 실자금은 #107 Phase 3)
- 자동 commit 금지 (사용자 승인 후 수동)

## 후속
- 통과: #107 Phase 3 Live Pilot (실자금 5%) 진입
- 롤백: #109 슬리피지 모델 / #110 Partial fill 등 보강 후 재시도

## 참고
- #105 본 이슈 (코드 PR 게이트)
- #107 Phase 3 Live Pilot (실자금)
- \`docs/work/active/000105-phase2-paper-live/01_plan.md\` Stage 7b 정의
- \`docs/work/active/000105-phase2-paper-live/02_implementation.md\` 코드 산출물 매핑
- \`docs/background/29-paper-to-live-protocol.md\` §3.3 exit criteria, §3.4 롤백 트리거

## 개발 체크리스트
- [ ] 운영 작업폴더 \`.ai.md\` 최신화 (운영 결과 / ADR 위치 / 다음 단계)

## 작업 내역

### 2026-04-30 — 운영 인프라 PR (코드 + 문서)

**상태**: 운영 인프라 코드 완성 + PR 머지 준비. 4주 실측 운영 (AC2/3/4) 은 PR 머지 후 시작.

**구현**:
- `Dockerfile` (python:3.12-slim, native deps + libgomp1) + `.dockerignore`
- `docker-compose.live.yml` 3 service:
  - `live-daemon` — `scripts/live_run.py --broker kis-paper-shadow --symbols 005930,035720,000660 --max-orders 30 --auto-fallback --schedule krx`
  - `report-cron` — `scripts/cron_loop.sh` (KST 16:00 sleep-loop, 일일 리포트 + Telegram 요약)
  - `telegram-notifier` — `scripts/telegram_alert.py --watch /data/logs` (WAL tail kill-switch/mode_switched/fill_anomaly 알림)
- `scripts/cron_loop.sh` (75 line) — bash sleep-loop, cron daemon 회피
- `scripts/telegram_alert.py` (174 line) — `--watch DIR` / `--report PATH` / `--test` 3 mode. 토큰 미설정 시 skip (daemon 정상 작동)
- `scripts/local_setup_windows.md` — Windows 24/7 운영 가이드
- `docs/work/active/000133-phase2-operation/SECRETS.md` — `.env` 변수 등록 가이드 (Telegram bot 발급 절차)
- `docs/work/active/000133-phase2-operation/02_operation.md` — 운영 일지 frame (4주 누적용)

**테스트**:
- `tests/scripts/test_telegram_alert.py` — 15 tests, all pass
- 전체 pytest 회귀 1305 passed (447s) — #105 회귀 0
- check_invariants --strict PASS (116 notes)
- Docker build 성공 (이미지 1.36 GB, 3분), import chain + CLI scripts 정상

**.env.example 정정**:
- 미사용 변수 제거 (`KIS_APP_KEY/SECRET/HTS_ID` — #105 worker 잔재)
- `HANTOO_FAKE_CREDIT_NUMBER`, `HANTOO_HTS_ID` 추가 (실 코드 변수명)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MAX_ORDERS` 추가

**운영 환경 결정**:
- Oracle Cloud ARM Ampere 시도 → AD-1 capacity 부족 + single-AD region 으로 fallback 불가 → 폐기
- AWS Lightsail / DigitalOcean ($4-5/월) 검토 → 사용자 0원 선호 → **로컬 PC + Docker Desktop (Windows 11) 채택**
- Phase 3 진입 시 클라우드 이전 권고 (#107)

**거래 종목 / 운영 형태**:
- 005930 (삼성전자) + 035720 (카카오) + 000660 (SK하이닉스) 3종
- **사용자 1인 운영 (자가 승인)**

**남은 사용자 직접 작업**:
1. Telegram bot @BotFather 발급 → token + chat_id `.env` 추가
2. Windows 절전/Update 정책 + Docker Desktop 4GB+ 메모리 할당 (`scripts/local_setup_windows.md`)
3. 다음 KRX 영업일 KST 10:00 nightly cron 한투 앱 거래 기록 확인
4. 정상 → `docker compose -f docker-compose.live.yml up -d`

