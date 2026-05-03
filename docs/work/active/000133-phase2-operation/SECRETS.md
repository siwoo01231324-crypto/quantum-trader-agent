---
type: runbook
id: SECRETS
name: phase2-secrets-setup
severity: medium
title: Phase 2 운영 시크릿 셋업 가이드 (#133)
created: 2026-04-28
tags: [phase2, ops, secrets]
---

# Phase 2 4주 운영 — `.env` 시크릿 셋업

`docker compose -f docker-compose.live.yml up -d` 실행 전, 아래 변수를 **`D:/project/quantum-trader-agent/.env`** 에 모두 등록한다. (worktree 의 `docker-compose.live.yml` 은 `env_file: ../../.env` 로 main repo `.env` 를 직접 참조하므로 한 곳만 관리하면 OK.)

`.env` 는 `.gitignore` 적용 — 절대 commit 금지. `.env.example` 만 commit.

## 1. KIS 모의계좌 (한국투자증권)

| 변수 | 값 | 발급 경로 |
|------|-----|-----------|
| `HANTOO_FAKE_API_KEY` | `PSf...` | KIS 개발자 포털 → 앱 등록 (모의투자) |
| `HANTOO_FAKE_SECRET_API_KEY` | `(secret)` | 동일 |
| `HANTOO_FAKE_CREDIT_NUMBER` | `12345678-01` 형식 (8자리-2자리) | KIS 모의투자 가입 후 모의계좌 번호 |
| `HANTOO_HTS_ID` | (예: `myhts123`) | KIS 본인 HTS ID — WS 구독 시 필요. 미설정 시 smoke 는 `'smoke'` default 사용 |
| `KIS_PAPER` | `true` | 고정 (`false` 는 실거래 — #107 까지 금지) |

이미 `.env` 에 `HANTOO_FAKE_API_KEY/SECRET/CREDIT_NUMBER` 가 등록된 상태 (실증 OK: KRW 1천만원 정확 출력). `HANTOO_HTS_ID` 만 추가하면 됨.

## 2. Telegram bot

알림 채널. 미설정 시 daemon 은 정상 작동하지만 알림 skip.

### 발급 절차 (5분)

1. Telegram 앱에서 [@BotFather](https://t.me/BotFather) 검색 → 대화 시작
2. `/newbot` 입력 → bot 이름 (예: `QTA Phase2 Alert Bot`) → username (예: `qta_phase2_alert_bot`, 끝이 `bot`)
3. **token 받음** (예: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`) — 아래 변수에 복사
4. 본인이 만든 bot 검색 → `/start` 메시지 보내기 (chat_id 발급 트리거)
5. 브라우저: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   → JSON 응답에서 `"chat":{"id":<NUMBER>}` 의 **id 숫자** 복사

### `.env` 등록

```
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 검증

```powershell
cd D:\project\quantum-trader-agent\.worktree\000133-phase2-operation
docker compose -f docker-compose.live.yml run --rm telegram-notifier python /app/scripts/telegram_alert.py --test
```

→ Telegram 으로 "✅ QTA Phase 2 telegram_alert test ping" 메시지 수신.

## 3. 운영 옵션 변수 (선택)

| 변수 | default | 설명 |
|------|---------|------|
| `MAX_ORDERS` | `30` | 첫 1주 보수 운영. 안정 확인 후 unset (또는 `1000+`) |
| `KIS_FILL_MISSING_HALT_THRESHOLD` | `1` | 체결 누락 halt 임계 (R2 트리거) — Phase 2 한정 보수적, Phase 3 진입 시 재검토 |
| `FX_USD_KRW_TTL_SEC` | `300` | USD/KRW 환율 캐시 TTL (초) |

## 4. 보안 체크리스트

- [ ] `.env` 가 `.gitignore` 에 포함됨 (`grep -E "^\.env$" .gitignore`)
- [ ] `.env` 파일 권한 본인만 (Windows: 우클릭 → 속성 → 보안 → 본인 외 권한 제거)
- [ ] Telegram bot token 외부 공유 금지 (실제로 token 탈취 시 daemon 알림 spoof 가능)
- [ ] `git status` 결과에 `.env` 가 안 보이는지 매 commit 전 확인
- [ ] PR diff 에 `.env` 가 포함되지 않는지 확인 (실수 push 방지)

## 5. 운영 시작 게이트 (Stage 5)

위 1, 2 모두 등록 완료 후:

1. 다음 KRX 영업일 KST 10:00 GitHub Actions nightly cron 발화 확인:
   ```powershell
   gh run list -w kis-paper-nightly.yml --limit 3
   ```
2. 한국투자 모바일 앱 → 모의투자 메뉴에서 005930 1주 매수/매도 거래 기록 확인
3. 정상 → `local_setup_windows.md` 의 Stage 4 단계로 daemon 시작
