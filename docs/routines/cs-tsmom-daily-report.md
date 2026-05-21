---
type: spec-architecture
id: cs-tsmom-daily-report
name: 일일 거래 리포트 (자동 + 수동 대조 분석)
status: setup-required
target: Claude Code Routines
schedule: "daily 23:55 KST"
owner: siwoo
created: 2026-05-21
last_updated: 2026-05-21
tags:
- routine
- daily-report
- claude-code-routines
- trading-journal
- obsidian-vault
---

# 일일 거래 리포트 routine 셋업 가이드

자동 계좌 (cs-tsmom 등) + 수동 계좌 (사용자 직접 매매) 의 오늘 모든 거래를
**Claude Code Routines** 가 매일 자정에 분석해서 `docs/journal/YYYY-MM-DD.md`
markdown 리포트로 commit + PR 생성. 사용자는 PR 머지만 하면 됨.

## 비용

**Max 구독 ($100/$200) 안에서 100% cover** — Routines 는 interactive Claude
Code 세션과 동일한 token quota 사용. 일일 리포트 1건 (5-20k token) × 30일 ≈
한 달 150-600k token, Max 한도 220k/5h 의 1-3 회 분량 — 여유. 별도 API 결제
없음. 자세히는 [Routines docs - Usage and limits](https://code.claude.com/docs/en/routines#usage-and-limits).

## 데이터 source 옵션 (3가지)

Routines 는 **Anthropic 클라우드에서 실행** 되므로 사용자 로컬 dashboard
(localhost:8000) 에 직접 접근 불가. 3가지 옵션 중 선택:

### 옵션 A — git repo 안의 데이터 파일 (권장)

매일 자정 직전 (예: 23:50 KST) dashboard 가 `/api/journal/today` 결과를
`docs/journal_data/YYYY-MM-DD.json` 으로 commit + push. Routines 가 그 5분
후 trigger 되어 repo clone 후 그 파일 read.

**필요 작업**:
- dashboard 에 "오늘 journal git push" 버튼 추가 (follow-up PR, 또는 사용자가
  수동 curl + git commit)
- 또는 cron 작업으로 자동 export

### 옵션 B — ngrok 으로 dashboard tunnel

사용자가 ngrok 으로 로컬 dashboard 를 public URL 로 노출. Routines environment
variable 에 그 URL + bearer token 저장. Routines 가 직접 fetch.

**필요 작업**:
- ngrok 계정 + 가동
- dashboard 에 simple bearer-token auth 추가
- Routines environment 에 `DASHBOARD_URL` + `DASHBOARD_TOKEN` 설정

### 옵션 C — 사용자 매일 저녁 수동 export (가장 단순)

사용자가 매일 저녁 한 번 dashboard 에서 `/api/journal/today` 결과 보고 직접
git commit. Routines 가 자정에 그 파일 read 후 분석.

**필요 작업**:
- 사용자 매일 1단계 작업 (1분 미만)

---

## Routine 등록 절차 (옵션 A 가정)

### 1. Claude Code Web 또는 CLI 에서 routine 생성

CLI:
```text
/schedule daily 23:59 KST, cs-tsmom 일일 리포트
```

또는 [claude.ai/code/routines](https://claude.ai/code/routines) 에서
**New routine** 클릭.

### 2. 설정값

| 항목 | 값 |
|---|---|
| **Name** | `cs-tsmom-daily-report` |
| **Repository** | `siwoo01231324-crypto/quantum-trader-agent` |
| **Model** | Sonnet 4.6 (분석 충분, Opus 는 비용 큼) |
| **Trigger** | Daily 23:59 (KST timezone — 한국시간 직전) |
| **Branch push permission** | `claude/*` (기본값 유지 — 안전) |
| **Connectors** | 없음 (repo 만 사용) |

### 3. Prompt (전문 복붙)

````text
당신은 quantum-trader-agent 의 일일 거래 리포트 작성자다. 오늘 (KST date)
의 자동·수동 거래를 분석해 `docs/journal/YYYY-MM-DD.md` 를 작성하고 PR 을
연다.

## 입력 데이터
이 repo 의 `docs/journal_data/YYYY-MM-DD.json` (가장 최근 파일) 을 읽어라.
구조:
- `date_kst` — 분석 대상 날짜
- `auto_fills` — 자동 계좌 체결 list (strategy_id, symbol, side, qty, price, ts)
- `auto_signals` — 자동 strategy 신호 (reason 필드 포함 — "왜 들어갔나" 원천)
- `manual_trades` — 사용자가 폼으로 입력한 수동 거래 (symbol, side, kind, qty,
  price, venue, note — note 에 진입 근거 지표/판단 적혀있음)
- `cs_tsmom_top10` — 오늘 cs-tsmom-crypto-daily TOP-10 (참조용)

JSON 파일이 없으면 PR 만들지 말고 종료.

## 분석 항목 (각 거래별)

### 자동 거래
- **언제 어떤 전략이 진입했나** — `auto_signals` 의 reason 필드 + `auto_fills`
- **익절/손절 여부** — entry/exit pair 매칭 (같은 strategy_id + symbol 의 buy ↔ sell)
- **잘한 점/못한 점**:
  - 익절: 전략 규율대로 stop_loss/take_profit 발동? signal score 양수에서
    진입 timing OK?
  - 손절: signal 자체가 noise? metalabeler 가 막았어야? stop_loss_pct 너무 빡빡?
  - 전략 spec (`docs/specs/strategies/<strategy>.md`) 의 backtest 기대값과
    오늘 결과 대조

### 수동 거래
- **사용자가 본 지표 / 판단 근거** — `manual_trades[i].note` 그대로 인용
- **익절/손절 여부** — entry/exit pair
- **잘한 점/못한 점**:
  - 익절: note 의 지표 신호가 진짜 effective 했음. 다음 같은 패턴 봐도 따라할만.
  - 손절: 같은 note 의 지표로 들어간 다른 성공 케이스가 있나? 있다면
    **무엇이 달랐나** (시간대? 거래량? 추세?). 없다면 그 지표 자체 의문.

## 출력 형식

파일 경로: `docs/journal/2026-05-21.md` (날짜는 `date_kst` 사용).

```markdown
---
type: trading-journal
id: journal-YYYY-MM-DD
date: YYYY-MM-DD
auto_trades: <N>
manual_trades: <N>
win_count: <N>
loss_count: <N>
total_pnl_usdt: <number>
total_pnl_krw: <number>
created: YYYY-MM-DD
tags:
- trading-journal
- daily-report
- auto-account
- manual-account
---

# YYYY-MM-DD 거래 리포트

## 한눈에
- 자동: ENTER N, EXIT N → 익절 N / 손절 N (총 손익 X USDT)
- 수동: ENTER N, EXIT N → 익절 N / 손절 N (총 손익 Y KRW + Z USDT)
- 오늘의 핵심 패턴: (잘한 거래 1, 못한 거래 1 의 공통점)

## 자동 계좌 (cs-tsmom 등)
### 거래 1 — [strategy_id] [symbol] [side] @ [price]
- 시각: HH:MM:SS KST
- 진입 근거 (signal.reason): "..."
- 결과: 익절 +X% / 손절 -X%
- 분석:
  - 잘한 점: (구체적으로)
  - 개선 여지: (구체적으로)
- 관련 노트: `[[<strategy_spec>]]` [[cs-tsmom-crypto-daily]]

(거래 N건 반복)

## 수동 계좌
### 거래 1 — [symbol] [side] [kind] @ [price]
- 시각: HH:MM:SS KST
- 거래소: binance/kis
- 사용자 메모 (note): "..."
- 결과: 익절 +X% / 손절 -X%
- 분석:
  - 잘한 점: 메모의 지표 신호가 실제로 effective. 같은 패턴 다음에도 활용 가능.
  - 또는 못한 점: 같은 지표로 어제 성공한 케이스는 (조건) 였는데 오늘은
    (조건). 차이 → (가설).

(거래 N건 반복)

## 내일을 위한 한 줄
- 자동: ...
- 수동: ...

## 관련
- [[cs-tsmom-crypto-daily]]
- `[[trading-journal-template]]`
```

## 작업

1. `docs/journal_data/YYYY-MM-DD.json` 읽기 (없으면 종료)
2. 위 형식대로 `docs/journal/YYYY-MM-DD.md` 작성
3. branch `claude/journal-YYYY-MM-DD` 에 commit
4. PR 제목: `journal: YYYY-MM-DD 거래 리포트` — auto-merge 안 함, 사용자 리뷰 대기
5. PR body 에 핵심 한 줄 + "PR 머지 시 Obsidian 볼트에 자동 동기화됨" 안내
````

### 4. 검증

routine 등록 후 **Run now** 버튼으로 한 번 즉시 실행. 첫 PR 이 만들어지면
형식 OK. 이후 매일 자정에 자동 실행.

## Trigger 시각 권장

- **dashboard export** : 매일 23:50 KST (사용자 자동화 또는 수동)
- **routine fire**: 매일 23:59 KST (export 후 9분 — git push lag 대비)
- 두 시점 사이의 buffer 가 안 맞으면 routine 이 어제 파일 분석할 수 있음 — 검증 필수

## 출력 위치 + Obsidian 동기화

- routine 출력: `docs/journal/YYYY-MM-DD.md`
- Obsidian 볼트: `docs/` 자체가 Obsidian 볼트 (CLAUDE.md 참조)
- 동기화: PR 머지 후 자동. 별도 작업 무.
- 온톨로지 (`docs/ontology/trading.ttl`) 동기화는 `[[services-obsidian-mcp]]`
  (#51) 가 frontmatter `type: trading-journal` 인식 후 처리. spec 확장 필요시
  `[[note-schemas]]` 에 trading-journal 타입 추가.

## 한계 + 후속

- **수동 거래 자동 fill 감지 미구현**: Binance/KIS REST polling daemon 은
  보류 (보안상 외부 클라우드에 API key 노출 회피). 사용자가 거래 후
  `/manual` 폼에 직접 입력.
- **routine 의 LLM 분석 정확도**: 자동 거래는 strategy spec 의 backtest
  기대값과 대조 가능 (정량적). 수동 거래의 "잘한 점/못한 점" 은 메모의
  자연어 + 시장 상태 추론으로 정성적 — 정확도 한계.
- **dashboard journal export 자동화**: 옵션 A 의 git push 단계가 수동.
  follow-up PR 에서 dashboard 의 "오늘 export" 버튼 + git automation 추가.

## 관련

- [[cs-tsmom-crypto-daily]] — 자동 계좌 핵심 전략
- [Claude Code Routines docs](https://code.claude.com/docs/en/routines)
- `docs/specs/strategies/.ai.md` — 전략 spec 디렉토리
- `src/dashboard/app.py` — `/api/journal/today` endpoint, `/manual` 폼
- `services/obsidian_mcp/` — 볼트 동기화 (#51)
