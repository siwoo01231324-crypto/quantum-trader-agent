---
type: runbook
id: 02_operation
name: phase2-operation-daily-log
severity: low
title: Phase 2 4주 운영 일지 (#133)
created: 2026-04-28
tags: [phase2, ops, daily-log]
---

# Phase 2 4주 운영 일지 (#133)

운영 환경: 로컬 PC + Docker Desktop (Windows 11, x86_64). `D:/project/quantum-trader-agent/.worktree/000133-phase2-operation/`.
거래 종목: 005930 (삼성전자), 035720 (카카오), 000660 (SK하이닉스).
운영 형태: **사용자 1인 운영** (자가 승인 + ADR 작성).

## 운영 일정

| 단계 | 기간 | 상태 |
|------|------|------|
| Stage 4 — 로컬 셋업 (Docker Desktop, 절전 끄기, 첫 빌드) | 2026-04-28 ~ ? | 진행 중 |
| Stage 5 — 운영 시작 게이트 (한투 앱 nightly cron 거래 확인) | 2026-04-28 KST 10:00 (월) | 대기 |
| Stage 6 — 첫 1주 보수 운영 (`MAX_ORDERS=30`) | T+0 ~ T+7일 | 대기 |
| Stage 7 — 풀 운영 3주 (`MAX_ORDERS` unset) | T+7일 ~ T+28일 | 대기 |
| Stage 8 — ADR + Phase 3 진입 결정 | T+28일 이후 | 대기 |

## 일일 일지

> 매일 16:00 KST `cron_loop.sh` 가 `data/reports/{YYYY-MM-DD}.md` 자동 생성.
> 사용자는 Telegram 요약 확인 + 이상치만 아래에 기록.

### 2026-04-28 (월, T-?)

**상태**: Stage 4 진행 중 — daemon 미시작.

**오늘 한 일**:
- (사용자) Telegram bot 생성 → token + chat_id `.env` 등록 — 결과: ?
- (사용자) Windows 절전 / Update 정책 적용 — 결과: ?
- (사용자) Docker Desktop 4GB+ 메모리 할당 — 결과: ?
- (Claude) Dockerfile + docker-compose.live.yml + cron_loop.sh + telegram_alert.py + 단위테스트 작성 — telegram_alert 15/15 pass

**내일 (화요일) 할 일**:
- KST 10:00 GitHub Actions nightly cron 발화 + 한투 앱 거래 기록 확인 (Stage 5 게이트)
- 정상 시 `docker compose -f docker-compose.live.yml up -d` 로 daemon 시작

**이슈/위험**:
- Oracle Cloud ARM 미가용 → 로컬 PC 폴백. 정전/재부팅 리스크 인지.
- Telegram bot 발급 + .env 등록 사용자 직접 작업 대기.

---

### YYYY-MM-DD (요일, T+N)

**상태**: ?

**일일 리포트**: `data/reports/YYYY-MM-DD.md` 참조.

**Telegram 알림**: ? (mode_switched, fill_anomaly, kill_switch_tripped 발생 시 기록)

**누적 메트릭**:
- 거래일 수: ? / 20
- placed: ? / 100
- filled: ? / placed * 0.95
- tracking_error p95: ? / 0.5%
- WS reconnect (KIS): ?건
- KIS 5xx error rate: ?%
- 토큰 재발급 실패: ?건
- 잔고 불일치: ?%

**이슈/위험/액션**: ?

---

## 주간 리뷰

> 매주 금요일 마감 후 작성. `docs/background/29-paper-to-live-protocol.md` §8.2 의제 사용.

### Week 1 (T+0 ~ T+5 거래일)

- 누적 PnL / Sharpe / MDD: ?
- Tracking Error 추세: ?
- kill-switch trip 이력: ?
- WS 단절 / reconnect 율: ?
- **첫 1주 게이트 통과 여부** (R1~R5 trip 0건 + tracking_error p95 < 0.5% + daemon 다운타임 < 2시간):
  - [ ] 통과 → `MAX_ORDERS` unset 후 풀 운영 진입
  - [ ] 미통과 → 사후 분석 + plan 보강

### Week 2 / 3 / 4

(Week 1 와 동일 frame)

---

## 자동 롤백 트리거 발생 이력

| 일시 (KST) | 트리거 | 사유 | 자동 액션 | 사후 조치 |
|-----------|--------|------|-----------|-----------|
| (없음) | | | | |

---

## Phase 3 진입 결정 게이트 (Stage 8)

T+28일 (또는 20 거래일 누적) 시:

- [ ] AC2: distinct trading dates ≥ 20
- [ ] AC3: placed ≥ 100 AND filled ≥ placed * 0.95
- [ ] AC4: tracking_error p95 < 0.5%
- [ ] R1~R5 자동 트리거 발생 0건 또는 사후 분석 문서화 완료
- [ ] daemon 누적 다운타임 < 24시간 (4주 = 672시간 중 < 4%)
- [ ] 사용자 1인 자가 승인 + `03_adr.md` 작성

**결정**: ☐ Phase 3 (#107) 진입 / ☐ 보강 후 재시도 / ☐ 폐기

---

## Memo

(자유 형식 — 운영 중 깨달음, 재현 안 되는 이상 현상, KIS API 거동 등)
