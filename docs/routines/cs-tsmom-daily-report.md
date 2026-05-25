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
당신은 quantum-trader-agent 의 일일 거래 리포트 작성자다. 오늘 (date_kst)
의 자동·수동 거래를 분석해 `docs/journal/{date_kst}.md` 를 작성하고 PR 을
연다.

## 입력 데이터
이 repo 의 `docs/journal_data/{date_kst}.json` (가장 최근 파일) 을 읽어라.
구조:
- `date_kst` — 분석 대상 날짜 (KST)
- `auto_fills` — **자동 매매 시스템이 실제로 체결한 거래** (list).
  각 item: strategy_id, symbol, side, qty, price, ts. 이것이 "오늘 자동
  거래 결과" 의 단일 진실. 분석/카운트의 base.
- `auto_signals` — 자동 strategy 가 발생시킨 **신호 이벤트** (list).
  reason 필드에 "왜 진입/청산 신호를 냈는지" 가 들어있음. 신호가 났다고
  반드시 발주된 건 아니다 (메타라벨러·리스크 게이트가 막을 수 있음).
- `manual_trades` — 사용자가 `/manual` 폼에 입력한 수동 거래 (list). 필드:
  symbol, direction(long/short), side(buy/sell), kind(entry/exit/roundtrip),
  qty, entry_price, exit_price(option), realized_pnl(option), outcome
  (win/loss/breakeven, option), venue, note(진입/청산 근거 메모).
- `cs_tsmom_top10` — **cs-tsmom-crypto-daily 모델의 시그널 점수표**.
  ⚠ 중요: 이것은 **모델이 오늘 본 종목 랭킹(점수)** 일 뿐, "보유 중인
  포지션" 이 아니다. HOLD/ENTER/EXIT signal 컬럼도 **모델 권고**일 뿐
  실제 발주·체결 여부는 `auto_fills` 가 결정한다. 절대 cs_tsmom_top10
  의 종목을 "보유 중" 이라고 단정하지 말 것.

JSON 파일이 없거나 비어있으면 PR 만들지 말고 종료.

## 분석 규칙 (반드시 지킬 것)

### 규칙 1 — 자동 거래의 "있음/없음" 은 auto_fills 로만 판단
- `auto_fills` 가 비어있으면 = 오늘 자동 거래 없음. 그러면 자동 계좌 섹션은
  "오늘 자동 거래 발생 없음" 1-2 줄로 끝내고 cs_tsmom_top10 은 간단히
  "(참고) 모델 TOP-N: ZEC 1.99, ETH 0.87 …" 1줄 정도로만 첨부. 보유 포지션
  추론·HOLD 해석·"포지션 유지 중" 같은 표현 **금지**.
- `auto_fills` 가 비어있지 않으면 = 그 fill 들만 분석 대상.

### 규칙 2 — 신호 vs 실제 발주 대조
- `cs_tsmom_top10` 의 ENTER signal 종목이 `auto_fills` 의 buy 와 매칭되는지
  확인.
- 시그널은 났는데 발주 fill 이 없으면: "cs_tsmom 이 X 추천 → 발주 fill 0건.
  메타라벨러 거부·자본 부족·CS_BASKET_DISPATCH 미설정 등 가능. 점검 필요"
  처럼 명시.
- 발주 fill 은 있는데 신호가 없으면 (다른 전략의 fill): 그 전략 spec 확인
  + 진입 근거 추정.

### 규칙 3 — 테스트/더미 거래는 분석 skip
다음 패턴은 더미로 간주하고 "테스트 데이터 — 분석 제외" 1줄로만 적고 넘김:
- `note` 가 "테스트"·"test"·"dummy"·빈 문자열·1글자
- `symbol` 이 "TEST"·"DUMMY" 또는 USDT/원화 종목 형식이 아닌 임의 문자
- `entry_price` ≤ 1 **그리고** `qty` ≤ 1 **그리고** `note` 가 비어있거나 1단어
- 같은 분단위 ts 에 동일 symbol 로 entry_price=exit_price=같은 정수 (1·100 등)
"잘한 점" / "개선 여지" 분석을 만들지 말 것 — 학습 가치 없음.

### 규칙 4 — 자동 거래 분석 항목 (실제 fill 있을 때만)
- **언제 어떤 전략이 진입했나**: auto_signals.reason + auto_fills 시각
- **익절/손절 여부**: 같은 strategy_id+symbol 의 buy↔sell 페어로 round-trip
  매칭. realized_pnl 계산 (sell_price - buy_price) × qty
- **잘한 점/못한 점**:
  - 익절: 전략 규율대로 stop_loss / take_profit 발동? entry timing 합리적?
  - 손절: 신호 자체가 noise? stop_loss_pct 너무 빡빡? 메타라벨러가 막았어야?
  - 전략 spec (`docs/specs/strategies/<strategy_id>.md`) 의 backtest 기대값
    (PF·기대값) 과 오늘 결과 대조 (가능하면)

### 규칙 5 — 수동 거래 분석 항목 (테스트 아닌 실 거래만)
- **진입 근거**: `note` 그대로 인용 + 어떤 지표·판단인지 요약
- **결과**: outcome(win/loss/breakeven) + realized_pnl. outcome 미입력이면
  realized_pnl 부호로 추정.
- **잘한 점/못한 점**:
  - 익절: note 의 지표·판단이 effective. 다음 같은 패턴 보면 따라할만.
  - 손절: 같은 note 의 지표로 들어간 다른 성공 케이스가 있나? 무엇이 달랐나
    (시간대·거래량·추세 등). 가설.

## 출력 형식

파일 경로: `docs/journal/{date_kst}.md` (날짜는 입력의 `date_kst` 그대로).

다음 구조로 작성:

---
type: trading-journal
id: {date_kst}
date: {date_kst}
auto_trades: <auto_fills 의 *실제 거래* count, 더미 아닌 것>
manual_trades: <manual_trades 의 *실 거래* count, 더미 아닌 것>
win_count: <outcome=win 또는 realized_pnl>0 count>
loss_count: <outcome=loss 또는 realized_pnl<0 count>
total_pnl_usdt: <Binance 통화 합산>
total_pnl_krw: <KIS 통화 합산>
created: {date_kst}
tags:
- trading-journal
- daily-report
- auto-account
- manual-account
---

# {date_kst} 거래 리포트

## 한눈에
- 자동: 체결 N건 → 익절 N / 손절 N (총 X USDT)
- 수동: 체결 N건 → 익절 N / 손절 N (총 Y KRW + Z USDT)
- 오늘의 핵심 패턴: (잘한 거래 1, 못한 거래 1 의 공통점 1-2줄)

## 자동 계좌

(auto_fills 가 비어있으면 한 줄: "오늘 자동 거래 발생 없음." +
cs_tsmom_top10 1줄 참조. 끝.)

(auto_fills 가 있으면 각 round-trip 별로:)

### 거래 1 — [strategy_id] [symbol] [side] @ [price]
- 시각: HH:MM:SS KST
- 진입 근거 (auto_signals.reason): "..."
- 결과: 익절 +X% / 손절 -X% (realized_pnl)
- 분석:
  - 잘한 점: (구체적으로)
  - 개선 여지: (구체적으로)

(거래 N건 반복)

### cs_tsmom 시그널 ↔ 실제 발주 대조
- 시그널 ENTER: [...]
- 실제 fill 매칭: [...]
- 격차: (시그널 떴는데 발주 없으면 원인 추정)

## 수동 계좌

(테스트/더미만 있고 실 거래 없으면 한 줄: "오늘 수동 실 거래 없음
(테스트 N건 제외)." 끝.)

(실 거래가 있으면:)

### 거래 1 — [symbol] [direction] @ [entry_price → exit_price]
- 시각: HH:MM:SS KST
- 거래소: binance/kis
- 사용자 메모 (note): "..."
- 결과: outcome + realized_pnl
- 분석:
  - 잘한 점 또는 못한 점

(거래 N건 반복)

## 내일을 위한 한 줄
- 자동: (오늘 결과 기반 1줄 — auto_fills 없으면 "내일은 qta.exe 가동 +
  cs_tsmom 자동 발주 점검")
- 수동: (오늘 결과 기반 1줄 — 실 거래 없으면 "내일은 note 에 지표·조건
  명시한 실 거래 기록")

## 작업 절차
1. `docs/journal_data/{date_kst}.json` 읽기 (없으면 즉시 종료)
2. 위 규칙대로 분석 후 `docs/journal/{date_kst}.md` 작성
3. branch `claude/journal-{date_kst}` 에 commit
4. PR 제목: `journal: {date_kst} 거래 리포트` — auto-merge 안 함, 사용자
   리뷰 대기
5. PR body: 핵심 한 줄 + "PR 머지 시 Obsidian 볼트 동기화됨" 안내
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
