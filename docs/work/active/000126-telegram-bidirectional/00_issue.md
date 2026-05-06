---
type: work-done
id: 00_issue
name: "Issue #126 — Telegram 양방향 제어 봇"
status: active
---

# feat: 텔레그램 봇 양방향 제어 (/kill /release /status /policy) (#126)

## 사용자 관점 목표
localhost 대시보드는 모바일 불가. 외출 중·장외시간에도 휴대폰으로 비상정지 발동/해제 + 상태 조회. 백서 §10-4 / 부록 B-3.

## 산출물 (본 PR)

### `scripts/telegram_control.py` 신규 — 폴링 봇

Telegram `getUpdates` long-poll (30s) → 명령 파싱 → localhost FastAPI dashboard REST API 호출 → 결과 텔레그램 응답 + WAL 감사 로그.

**의존성 분리**: 봇은 dashboard HTTP API 만 호출. KillSwitch / DashboardState 직접 import 안 함 → dashboard 와 독립 프로세스로 실행 가능.

### 4 명령

| 명령 | 동작 | 호출 endpoint |
|---|---|---|
| `/kill <reason>` | 비상정지 발동 | `POST /api/kill-switch/trigger` |
| `/release` | **2-step 확인** (60초 내 두 번 보내야 실행) | `POST /api/kill-switch/reset` |
| `/status` | PnL + 한도 사용률 + KillSwitch 상태 | `GET /api/pnl /api/limits /api/kill-switch` |
| `/policy` | configs/policy.yaml top-level 키 요약 | (로컬 파일) |
| `/help` | 명령 목록 | (정적 응답) |

### 보안

- **chat_id 화이트리스트**: `TELEGRAM_CHAT_ID` 환경변수 (콤마/공백 구분 다수 가능). 화이트리스트 외 chat 은 즉시 거부 + 감사 로그.
- **2-step /release**: 단일 메시지로 비상정지 해제 안 됨. 2번 보내야 실행. 실수 방지.
- **감사 로그**: 모든 명령 수신 (수락/거부 둘 다) 을 `command_received` event_type 으로 WAL 에 기록. 누가 (chat_id/user_id), 언제 (UTC ISO), 무엇을 (cmd/args), accepted, reason 추적 가능.

## 완료 기준 (AC)

- [x] `/kill <사유>` — 비상정지 발동 + 신규 차단
- [x] `/release` — 비상정지 해제 (사용자 승인 흐름 = 2-step confirm)
- [x] `/status` — 현재 포지션 + PnL + 한도 사용률 조회
- [x] `/policy` — 현재 정책 파일 요약
- [x] 사용자 인증 (chat_id 화이트리스트)
- [x] 명령 감사 로그 (누가·언제·무엇을)

## 변경 파일

| 파일 | 역할 |
|---|---|
| `scripts/telegram_control.py` (신규) | 폴링 봇 메인 — 명령 파싱 / 인증 / dispatch / 감사 |
| `tests/test_telegram_control.py` (신규) | 25 케이스 — 파싱 / 인증 / 4 명령 / 감사 / 엣지 |

## 검증

- [x] `pytest tests/test_telegram_control.py -v` — **25/25 green**
- [x] 25 케이스 분포:
  - parse_command: 6
  - 화이트리스트: 3
  - /kill: 3 (success / 500 / 연결실패)
  - /release: 3 (1차 확인 요청 / 2차 실행 / window 만료 후 reset)
  - /status: 2 (정상 / 활성 KillSwitch 표시)
  - /policy: 2 (정상 / 미설정)
  - dispatch: 4 (인증실패 / 미지원 / help / 비명령)
  - 감사 로그: 2 (정상 append / I/O 실패 swallow)

## 운영 (사용자 머지 후)

```powershell
# qta.exe / dashboard 가 가동된 상태에서:
$env:PYTHONUTF8 = 1
python scripts\telegram_control.py --dashboard http://localhost:8000 --audit-wal logs\shadow\phase1-r4-switch-BTCUSDT\wal.jsonl
```

또는 docker-compose (#133) 의 4번째 서비스로 추가 (후속 작업).

휴대폰에서 너 봇한테 `/help` 보내서 명령 목록 확인 → `/status` 로 첫 테스트.

## 의존성·참고
- 선행: #125 FastAPI 대시보드 (REST API 사용) ✅
- 선행: #133 Telegram bot 토큰 (사용자 Action 1) ✅
- 후행: #133 docker-compose.live.yml 에 telegram-control 서비스 추가 (선택)

## 위험·롤백
- dashboard 가 죽어있으면 `/status` 등 fail. 봇 자체는 살아있고 응답 메시지 출력. 별도 헬스 명령은 후속 (필요 시).
- chat_id 노출 시 누구나 명령 가능 — `.env` 보안 관리 필수.
- 봇 토큰 노출 시 누구나 봇 행세 가능 — `.env` 보안 관리 필수.
