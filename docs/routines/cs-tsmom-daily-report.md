---
type: spec-architecture
id: cs-tsmom-daily-report
name: 일일 거래 리포트 (자동 + 수동 대조 분석)
status: active
target: Claude Code Routines
schedule: "daily 23:55 KST"
owner: siwoo
created: 2026-05-21
last_updated: 2026-06-30
tags:
- routine
- daily-report
- claude-code-routines
- trading-journal
- obsidian-vault
- swing-strategy
---

# 일일 거래 리포트 routine 셋업 가이드

자동 계좌 (스윙 2전략: 투매반등+돌파/터틀) + 수동 계좌 (사용자 직접 매매) 의 오늘 모든 거래를
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
- `auto_pnl_ledger` — **일일손익의 단일 진실** (거래소 청산이력 history-position,
  로컬 export). 필드: `ok`, `total_net`(=오늘 net USDT), `wins`/`losses`,
  `gross_win`/`gross_loss`, `profit_factor`, `n_positions`, `positions`
  ([{symbol, side, net, open_kst, close_kst}]). **자동 계좌의 손익·승패·PF·종목별
  실현손익은 전부 이 필드로 산출** (규칙 4). `ok:false` 면 "ledger 데이터 없음
  (로컬 export 누락)" 1줄로 넘긴다. ⚠️ **체결가(entry/exit price)·수량(qty)·명목
  금액은 이 필드에 없다** — history-position 은 netProfit 만 제공. 그 칸은 리포트에
  "(미기재 — API 제약)" 으로 둔다 (WAL auto_fills 는 금지 소스라 가격도 신뢰 불가).
- `auto_fills` — 자동 체결 WAL 이벤트 (list, strategy_id/symbol/side/qty/price/ts).
  현재 자동매매는 **스윙 2전략** — `strategy_id` 은 `live-capitulation-bounce`(투매반등)
  또는 `live-donchian-breakout-btcgate`(돌파/터틀). 둘 다 **롱전용**(side=buy 진입).
  ⚠️ **손익 계산 금지** (규칙 4 — 유령/누락 fill 로 방향·금액 틀림). **체결 시각·
  종목·신호 대조용 참고로만** 사용. 카운트·PnL 의 base 는 `auto_pnl_ledger`.
- `auto_signals` — 자동 strategy 가 발생시킨 **신호 이벤트** (list, strategy_id 포함).
  `signal_emitted` 가 신호 발사, reason 필드에 "왜 진입/청산 신호를 냈는지" 가 들어있음
  (투매반등=투매깊이+꼬리+거래량스파이크, 돌파=Donchian20 돌파+EMA200+BTC레짐게이트).
  신호가 났다고 반드시 발주된 건 아니다 (리스크 게이트·자본·피드死 가 막을 수 있음).
- `manual_trades` — 사용자가 `/manual` 폼에 입력한 수동 거래 (list). 필드:
  symbol, direction(long/short), side(buy/sell), kind(entry/exit/roundtrip),
  qty, entry_price, exit_price(option), realized_pnl(option), outcome
  (win/loss/breakeven, option), venue, note(진입/청산 근거 메모).
- `cs_tsmom_top10` — **[레거시] cs-tsmom 자동매매 중지 (2026-06-30)**. 모델 시그널
  점수표였으나 cs-tsmom 을 끄고 스윙 2전략으로 전환했으므로 이제 **무의미** — 보통
  빈값/구데이터. 있어도 분석 본문에 쓰지 않는다. ("(참고) 모델 TOP-N …" 첨부도 하지 말 것.)
- `airborne_fires` — **[레거시] 에어본 알림 중지 (2026-06-30)**. 과거 airborne v1.1
  텔레그램 알림(BB 40% 되돌림) 전수였으나 에어본 데몬을 끄면서 **보통 빈값**. 있으면
  과거 데이터일 뿐 — **분석 본문에 쓰지 않는다** (적중 분석 섹션 자체가 스윙용으로 교체됨).
- `airborne_sim` — **[레거시] 에어본 적중 sim 집계, 중지 (2026-06-30)**. 보통 빈값/`ok:false`.
  있어도 과거 데이터 — **본문에 안 씀**. (옛 규칙 6 의 klines fetch·sim 로직 일체 제거.)

JSON 파일이 없거나 비어있으면 PR 만들지 말고 종료.

## 분석 규칙 (반드시 지킬 것)

### 규칙 1 — 자동 거래의 "있음/없음" 은 auto_fills 로만 판단 (스윙은 저빈도가 정상)
- 스윙 2전략은 **저빈도 4h 전략 — 대부분의 날 거래 0건이 정상**이다. 돌파/터틀은
  BTC 4h close<EMA200 베어장이면 레짐 게이트가 닫혀 진입 0, 투매반등은 급락 패닉
  국면에서만 발화. 따라서 거래 0 은 결함이 아니라 설계대로다.
- `auto_fills` 가 비어있으면 = 오늘 자동 거래 없음. 자동 계좌 섹션은
  **"오늘 스윙 거래 없음 (저빈도 4h 전략 — 정상)"** 1-2 줄로 끝낸다. 보유 포지션
  추론·"포지션 유지 중" 같은 표현 **금지**.
- `auto_fills` 가 비어있지 않으면 = 그 fill 들만 분석 대상.

### 규칙 2 — 신호 vs 실제 발주 대조 (swing auto_signals ↔ auto_fills)
- `auto_signals` 의 `signal_emitted`(strategy_id + reason) 가 `auto_fills` 의 buy 와
  매칭되는지 확인. 어느 전략(투매반등/돌파)이 어떤 종목에 진입 신호를 냈는지 본다.
- 신호는 떴는데 체결 fill 이 0이면: "투매반등/돌파 가 X 신호 → 체결 0건.
  리스크 게이트(집중도 한도)·자본 부족·피드死(체결틱 멈춤) 등 가능. 점검 필요"
  처럼 원인을 추정해 명시.
- 체결 fill 은 있는데 신호 이벤트가 없으면: 그 전략 spec
  (`docs/specs/strategies/live-*.md`) 확인 + 진입 근거 추정.

### 규칙 3 — 테스트/더미 거래는 분석 skip
다음 패턴은 더미로 간주하고 "테스트 데이터 — 분석 제외" 1줄로만 적고 넘김:
- `note` 가 "테스트"·"test"·"dummy"·빈 문자열·1글자
- `symbol` 이 "TEST"·"DUMMY" 또는 USDT/원화 종목 형식이 아닌 임의 문자
- `entry_price` ≤ 1 **그리고** `qty` ≤ 1 **그리고** `note` 가 비어있거나 1단어
- 같은 분단위 ts 에 동일 symbol 로 entry_price=exit_price=같은 정수 (1·100 등)
"잘한 점" / "개선 여지" 분석을 만들지 말 것 — 학습 가치 없음.

### 규칙 4 — 자동 거래 분석 항목 (실제 fill 있을 때만)

> ⚠️ **일일손익(PnL) source — WAL round-trip 금지, 거래소 ledger 사용 (2026-06-13 정정)**
> `auto_fills`(WAL) buy↔sell round-trip 매칭으로 realized_pnl 을 계산하면 **유령/누락
> fill 로 방향·금액이 통째로 틀린다** (6/13 BSB 숏 +58.50 발명 = 실제 롱 +1.61, 전면오류).
> **일일손익·승패·PF 는 거래소 청산이력 `/api/v2/mix/position/history-position` 의
> `netProfit`(=실현손익+펀딩−수수료, 거래소 화면 일치) 기준으로만 산출한다.**
> 입력 JSON 의 `auto_pnl_ledger` 필드(로컬 export — 아래 규칙 7 참조)에서 읽어라.
> `auto_fills`(WAL) 는 *체결 시각/종목/신호 대조*용 참고로만 쓰고 PnL 합산엔 쓰지 말 것.

- **언제 어떤 전략이 진입했나**: auto_signals.reason + auto_fills 시각
- **익절/손절 여부**: 거래소 ledger 의 포지션별 netProfit (방향·금액 신뢰). 일별
  합·승패·PF 는 ledger 기준.
- **전략별 분해 — 필수** (스윙 2전략은 둘 다 롱전용이라 방향분해 대신 전략분해):
  `auto_pnl_ledger.positions` 를 종목·시각으로 `auto_fills`/`auto_signals` 의
  `strategy_id`(투매반등 vs 돌파/터틀)에 매칭해 각 전략 n / 승·패 / net 합 / PF 를 낸다
  (매칭 불가하면 "전략 미상" 으로 묶어 표기). 손실/이익이 **한 전략에 쏠려 있으면**
  명시하고 "내일을 위한 한 줄"에 반영 (예: 추세장 아닌데 돌파만 손실 / 베어장에 투매반등만 이익).
- **잘한 점/못한 점**:
  - 익절: 전략 규율대로 stop_loss / take_profit 발동? entry timing 합리적?
  - 손절: 신호 자체가 noise? stop_loss_pct 너무 빡빡? 메타라벨러가 막았어야?
  - 전략 spec (`docs/specs/strategies/<strategy_id>.md`) 의 backtest 기대값
    (PF·기대값) 과 오늘 결과 대조 (가능하면)

### 규칙 5 — 수동 거래 분석 항목 (테스트 아닌 실 거래만)
- **종목 배경 한 줄** (2026-05-27 추가): 매매한 종목별로 *무엇인지* 1-2줄.
  카테고리 / 섹터 / 시장 포지션 / 주요 catalyst. 사용자가 *왜 그 종목을 골랐는지*
  맥락 학습 보조.
  예시:
  - `NEARUSDT` — Layer-1 블록체인, NEAR Foundation 의 AI agents 인프라 (NEAR Intents).
    2024-2025 AI 토큰 내러티브에 자주 묶임.
  - `SOLUSDT` — 고성능 L1, Solana 생태계 (memecoin·DePIN 허브). BTC 와 상관 높음.
  - `ZECUSDT` — privacy coin (zk-SNARK). 거래소 상장 제한 risk + 알트 강세장 후행.
  - `005930` — 삼성전자, 한국 반도체 대장주. KOSPI 시총 1위, HBM/foundry catalyst.
  - `035720` — 카카오, 한국 인터넷 플랫폼. 카카오모빌리티·페이 사업 비중 큼.
  종목 정보가 분명히 알려진 메이저 (BTC/ETH/SOL/삼전 등) 는 정보 정확. 마이너·신규
  코인 (예: PUMPUSDT, BSBUSDT) 은 "정확히 모름" 명시 권장 — fabrication 금지.
- **진입 근거**: `note` 그대로 인용 + 어떤 지표·판단인지 요약
- **결과**: outcome(win/loss/breakeven) + realized_pnl. outcome 미입력이면
  realized_pnl 부호로 추정.
- **잘한 점/못한 점**:
  - 익절: note 의 지표·판단이 effective. 다음 같은 패턴 보면 따라할만.
  - 손절: 같은 note 의 지표로 들어간 다른 성공 케이스가 있나? 무엇이 달랐나
    (시간대·거래량·추세 등). 가설.

### 규칙 5-bis — 다음 학습 추천 (2026-05-27 추가, 수동 거래가 있을 때만)
오늘 거래한 종목·진입 근거·결과를 종합해 사용자가 *다음에 공부하면 도움될
토픽* 2-3개 추천. 일반 권면 (예: "리스크 관리 공부하세요") 금지 — 오늘 거래의
구체적 갭에서 도출. 네 가지 축에서 선택:
- **시장 구조 / 섹터**: 오늘 거래한 종목의 카테고리 동향
  (예: NEAR 거래 → AI agent 토큰 펀더멘털 / NEAR Intents 백서)
- **기술적 지표**: 오늘 note 에 나온 지표의 심화
  (예: "RSI 다이버전스" → classic vs hidden divergence + MTF 활용)
- **리스크 관리**: 오늘 익절/손절 시점이 잘못됐다면 그 영역
  (예: 진입가 -1% 손절 → ATR-based dynamic stop, Kelly criterion)
- **백테스트 검증**: 사용자가 쓴 룰의 5y 성능 확인 — quantum-trader-agent 의
  spec md / bench script 참조 (예: `scripts/bench_*.py`, `docs/specs/strategies/`)

각 추천 1-2줄로 *오늘 어떤 거래·결과 와 연결되는지* + 시작점 (책 / 유튜브 채널 /
지표명 / 우리 repo 의 spec md 경로 / 외부 URL — 정확히 알면).
모르는 자료를 fabricate 금지 — "공식 docs 참조" 같은 안전한 추천 OK.

### 규칙 6 — 스윙 신호·게이트 분석 (2026-06-30, 에어본 적중분석 대체)

오늘 `auto_signals` 에 `signal_emitted` 가 있으면 스윙 신호·게이트 섹션을 넣는다.
없으면 "오늘 스윙 신호 없음 (저빈도 4h — 정상)" 1줄로 끝낸다.

> ⚠️ **에어본 klines fetch / sim 캐시 로직은 전부 제거됨** — 이 섹션은 입력 JSON 의
> `auto_signals` / `auto_fills` 와 spec 의 5y 기대값만으로 정성 분석한다. 봉을 직접
> fetch 하지 않는다 (클라우드 routine 은 Binance proxy 403).

**분석 항목**:
- **발화한 신호**: `signal_emitted` 마다 어느 전략(투매반등/돌파)·종목·진입사유(reason)
  + 같은 종목·시각에 `auto_fills` buy 가 있어 **체결됐는지** 여부.
- **BTC 레짐 게이트 상태**: 돌파/터틀은 **BTC 4h close ≥ BTC EMA200** 일 때만 진입한다
  — 오늘 게이트가 열렸나 닫혔나 한 줄. 입력 JSON 에 BTC 레짐 수치가 따로 없으면, 돌파
  신호 유무로 역추정 (돌파 신호가 떴다 = 게이트 열림 / 베어장 0건 = 게이트 닫힘 가능).
- **5y 기대값 대조** (가능하면): spec 의 백테스트 기대값 — 투매반등 PF≈1.6 / 돌파 PF≈1.4
  (정확 수치는 `docs/specs/strategies/live-capitulation-bounce.md`,
  `live-donchian-breakout-btcgate.md` 의 "5y 검증 결과" 표 참조 — 새로 지어내지 말 것)
  과 오늘 체결 결과를 대조해 정상 범위인지 한 줄. 단일일 표본이므로 "참고(표본 1일)" 명시.

분석은 정성적으로 짧게. 스윙은 저빈도라 대부분의 날 이 섹션은 1-2줄로 끝난다.

### 규칙 7 — 잔고 검산 (bill 원장, 2026-06-19 추가)

일일손익(ledger netProfit)이 **실제 계좌 잔고 흐름**과 앞뒤로 맞는지 검산한다.
입력 JSON 의 `account_reconciliation` 필드(로컬 export — `scripts/
bitget_account_reconcile.py {date_kst}` 결과)에서 읽어라. 필드:
`open_balance`(전일 종료잔고)·`close_balance`(당일 종료)·`balance_delta`·
`trade_flow`·`fees`·`transfers_deposits`·`no_external_flow`.

> ⚠️ **클라우드 routine 은 Bitget API 직접 접근 불가**(creds 로컬 전용·보안).
> 따라서 bill/ledger 데이터는 **로컬에서 JSON 으로 export 된 것을 읽기만** 한다.
> `account_reconciliation` 필드가 없으면 이 섹션은 "잔고 검산 데이터 없음
> (로컬 export 누락)" 1줄로 넘긴다.

**검증 항목**:
- **잔고 연속성**: `balance_delta`(종료−시작) 가 당일 거래 흐름과 일치하는지.
- **입출금/이체 격리**: `no_external_flow=true` 면 "잔고변동 100% 트레이딩
  (외부 유입 없음)". false 면 입출금/이체액 분리 표기 — 손익 해석에서 제외.
- **tie-out**: 잔고 Δ ↔ 당일 ledger netProfit(규칙4 합). 자정 걸친 포지션의
  open-leg/close-leg 시점차로 ~0.1~0.3 USDT 차이는 정상. 그 이상 벌어지면
  "검산 불일치 — 점검 필요" 명시.
- **수수료**: `fees` 가 거래flow 의 큰 비중이면(예: gross 작은 날) 과매매 경보.

### 규칙 8 — 스윙 게이트 상태 점검 (2026-06-30, 에어본 필터감사 대체)

스윙 2전략의 진입 게이트가 오늘 열렸는지 닫혔는지만 간단히 점검한다 (정량 sim 판정 아님 —
봉 fetch 불가). 목적은 "거래 0건이 게이트 닫힘 때문인지(정상) 배선 결함인지" 구분.

**점검 항목** (입력 JSON 의 `auto_signals`/`auto_fills` 로 추정, 봉 불필요):
- **돌파/터틀 — BTC 레짐 게이트**: BTC 4h close ≥ EMA200 일 때만 진입. 오늘 돌파 신호가
  하나라도 떴으면 게이트 열림(불장), 0건이면 닫힘(베어장) 가능. **open/closed 한 줄**.
- **투매반등 — 패닉 조건**: 가격이 EMA20 아래 2.5×ATR 투매 + 긴 아랫꼬리 + 거래량 스파이크.
  급락 패닉 때만 발화 — 오늘 투매반등 신호 종목 유무 한 줄 (없으면 "패닉 셋업 미충족").

⚠️ **단일일로 전략을 바꾸지 말 것** — 7일 미만 누적은 "참고(표본 작음)" 명시. 게이트가
며칠째 닫혀 거래 0이어도 그것이 설계대로(베어장엔 돌파 안 함, 평온장엔 투매반등 안 함)임을
기억. 전략 수정 판단은 **라이브 누적 + 5y 백테스트 재검증** 후에만 — 과적합 방지.

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
total_pnl_usdt: <거래소 ledger netProfit 합산 (규칙4 — WAL 금지)>
total_pnl_krw: <KIS 통화 합산>
pnl_source: bitget-exchange-history-position
swing_signals: <auto_signals 의 signal_emitted 개수 (없으면 0)>
swing_capitulation_fills: <live-capitulation-bounce buy fill 개수 (없으면 0)>
swing_breakout_fills: <live-donchian-breakout-btcgate buy fill 개수 (없으면 0)>
btc_regime_gate: <open|closed|unknown — 돌파 신호 유무로 추정>
created: {date_kst}
tags:
- trading-journal
- daily-report
- auto-account
- manual-account
- swing-strategy
---

# {date_kst} 거래 리포트

## 한눈에
- 자동: 청산 N건 → 익절 N / 손절 N (net X USDT, PF X). **투매반등 net X(PF X) vs 돌파/터틀 net X(PF X)** — 손실 쏠린 전략 명시. (거래 0이면 "스윙 거래 없음 — 저빈도 4h 정상".)
- 수동: 체결 N건 → 익절 N / 손절 N (총 Y KRW + Z USDT)
- 스윙 신호: signal_emitted N건 (투매반등 N / 돌파 N) → 체결 N건. BTC 레짐 게이트 open/closed 한 줄.
- 게이트 상태: (돌파 BTC-EMA200 open/closed + 투매반등 패닉 셋업 충족 유무)
- 오늘의 핵심 패턴: (손실 쏠린 전략 / 레짐 적합성 1-2줄)

## 자동 계좌

(auto_fills 가 비어있으면 한 줄: "오늘 스윙 거래 없음 (저빈도 4h 전략 — 정상)." 끝.
보유 추론·cs_tsmom 첨부 금지.)

(auto_fills 가 있으면 각 round-trip 별로:)

### 거래 1 — [strategy_id] [symbol] [side] @ [price]
- 시각: HH:MM:SS KST
- 진입 근거 (auto_signals.reason): "..."
- 결과: 익절 +X% / 손절 -X% (realized_pnl)
- 분석:
  - 잘한 점: (구체적으로)
  - 개선 여지: (구체적으로)

(거래 N건 반복)

### 전략별 PnL (ledger — 규칙 4, 둘 다 롱전용이라 전략 분해)

| 전략 | n | 승 | 패 | net USDT | PF |
|---|---:|---:|---:|---:|---:|
| 투매반등 (live-capitulation-bounce) | N | N | N | +X | X |
| 돌파/터틀 (live-donchian-breakout-btcgate) | N | N | N | +X | X |

(손실/이익 쏠린 전략 1줄. 전략 매칭 불가 포지션은 "전략 미상" 행으로.)

### 스윙 신호 ↔ 실제 발주 대조
- signal_emitted: [전략/종목/reason ...]
- 실제 fill 매칭: [...]
- 격차: (신호 떴는데 체결 0이면 원인 추정 — 리스크 게이트·자본·피드死)

## 잔고 검산 (bill 원장)

(규칙 7. 입력 JSON 의 `account_reconciliation` 필드에서 읽는다. 없으면 한 줄:
"잔고 검산 데이터 없음 (로컬 export 누락)." 끝.)

| 항목 | 값 |
|---|---|
| 시작잔고(전일 종료) | {open_balance} USDT |
| 종료잔고 | {close_balance} USDT |
| **잔고 Δ** | **{balance_delta:+} USDT** |
| 거래 flow (수수료 포함) | {trade_flow:+} (fee {fees:+}) |
| 입출금/이체 | {transfers_deposits:+} ({no_external_flow ? "외부 유입 없음 — 트레이딩 100%" : "⚠️ 외부 유입 분리"}) |

- **tie-out**: 잔고 Δ {balance_delta:+} ↔ 자동 ledger netProfit {total_pnl_usdt:+}
  — {차이 ~0.1~0.3 면 "정합(자정 걸친 포지션 시점차)", 그 이상이면 "⚠️ 불일치 점검"}.
- (외부 유입 있으면 손익 해석에서 제외했음을 명시.)

## 수동 계좌

(테스트/더미만 있고 실 거래 없으면 한 줄: "오늘 수동 실 거래 없음
(테스트 N건 제외)." 끝.)

(실 거래가 있으면:)

### 거래 1 — [symbol] [direction] @ [entry_price → exit_price]
- 시각: HH:MM:SS KST
- 거래소: binance/kis
- **종목 배경**: (규칙 5 — 1-2줄. 카테고리/섹터/주요 catalyst. 마이너 종목은
  "정확히 알 수 없음" 명시 권장.)
- 사용자 메모 (note): "..."
- 결과: outcome + realized_pnl
- 분석:
  - 잘한 점 또는 못한 점

(거래 N건 반복)

### 다음 학습 추천 (수동 거래가 있을 때만)

(규칙 5-bis 참조. 오늘 거래·메모·결과 기반 *구체적* 학습 토픽 2-3개.)

1. **[토픽 명]** — 오늘 어떤 거래·갭과 연결되는지 1줄. 시작점: (책/유튜브/지표/
   spec md 경로/외부 docs URL).
2. **[토픽 명]** — ...
3. **[토픽 명]** — ...

## 스윙 신호·게이트

(auto_signals 에 signal_emitted 가 없으면 한 줄: "오늘 스윙 신호 없음 (저빈도 4h — 정상)." 끝.)

(있으면 규칙 6 으로 분석:)

### 발화한 신호

| 전략 | 종목 | 진입사유(reason 요약) | 체결? |
|---|---|---|---|
| 투매반등/돌파 | SYMBOL | (투매깊이+꼬리+거래량 / Donchian돌파+EMA200+BTC게이트) | ✅/❌ |

### BTC 레짐 게이트 · 5y 기대값 대조
- **BTC 레짐 게이트**: open / closed (돌파 신호 유무로 추정). 돌파는 BTC 4h close≥EMA200 일 때만 진입.
- **투매반등 패닉 셋업**: 충족 종목 유무 (EMA20 아래 2.5×ATR 투매 + 아랫꼬리 + 거래량 스파이크).
- **5y 기대값 대조**: 오늘 체결 결과 vs spec 백테스트 기대값 (투매반등 PF≈1.6 / 돌파 PF≈1.4,
  정확 수치는 `docs/specs/strategies/live-*.md` 참조). 단일일 표본 — "참고(표본 1일)" 명시.

## 스윙 게이트 상태

(규칙 8. signal_emitted·fill 없으면 한 줄: "오늘 스윙 게이트 모두 닫힘 (거래 0 — 설계대로)." 끝.)

- **돌파/터틀 — BTC 레짐 게이트**: open(불장, 돌파 신호 떴음) / closed(베어장, 0건) 한 줄.
- **투매반등 — 패닉 조건**: 충족 종목 유무 (없으면 "패닉 셋업 미충족 — 평온장").
- **참고**: 게이트가 며칠째 닫혀 거래 0이어도 설계대로 (베어장엔 돌파 안 함, 평온장엔 투매반등
  안 함). 단일일로 전략 수정 금지 — 라이브 누적 + 5y 재검증 후에만. 7일 미만은 "참고(표본 작음)".

## 내일을 위한 한 줄
- 자동: (오늘 결과 기반 1줄 — auto_fills 없으면 "내일은 스윙 봇 가동 + 신호↔체결
  배선 점검 (저빈도라 거래 0은 정상)")
- 수동: (오늘 결과 기반 1줄 — 실 거래 없으면 "내일은 note 에 지표·조건
  명시한 실 거래 기록")
- 스윙: (오늘 신호·게이트 기반 1줄 — BTC 레짐·손실 쏠린 전략 다음날 확인 포인트)

## 작업 절차

⚠️ **각 step 의 명령을 *명시적으로 실행* 한다**. 명령 빠뜨리면 결과물이
사라진 것처럼 보임 (5/27 HrUdm / 5/29 4bQdE·e78BM / 6/1 PK98f 사고 — branch
+ commit 까진 origin 에 올라갔으나 PR 생성을 안 해서 사용자 입장에서 "리포트가
안 만들어진 것"처럼 보였다).

### Step 1 — base 동기화 (STALE CLONE 차단)

routine cloud 환경의 clone 이 master 보다 며칠 뒤처질 수 있다. 그 상태에서
바로 commit 하면 "이미 master 에 머지된 수천~수만 줄" 이 *deletion* 으로
포함되어 PR 머지 시 master 박살. 작업 시작 전 반드시:

```bash
git fetch origin master
git checkout master
git reset --hard origin/master
```

### Step 2 — 입력 검증

```bash
test -f docs/journal_data/{date_kst}.json || { echo "no input — exit"; exit 0; }
```

없으면 즉시 종료 (PR 만들지 말 것).

### Step 3 — 분석 + 리포트 작성

위 규칙 1~6 대로 `docs/journal_data/{date_kst}.json` 분석 후 결과를
`docs/journal/{date_kst}.md` 에 작성.

### Step 4 — branch + commit

⚠️ **branch 명은 정확히 `claude/journal-{date_kst}`** (예:
`claude/journal-2026-06-01`). random 이름 (예: `nice-pascal-XXXX`,
`jolly-allen-XXXX`) **금지** — 사용자 PR 추적 + 사후 정리가 깨진다.

```bash
git checkout -b claude/journal-{date_kst}
git add docs/journal/{date_kst}.md
git commit -m "journal: {date_kst} 거래 리포트"
git push -u origin claude/journal-{date_kst}
```

### Step 5 — PR 생성 (명령 실행 필수)

⚠️ **`gh pr create` 명령을 *실제로 실행* 한다**. "PR 만들겠습니다" 만 적고
넘기지 말 것. 누락 시 사용자 PR 목록에 안 나타나 routine 결과물이 사라진 것처럼
보인다.

```bash
gh pr create \
  --base master \
  --head claude/journal-{date_kst} \
  --title "journal: {date_kst} 거래 리포트" \
  --body "(한눈에 섹션 핵심 1-2줄 + 'PR 머지 시 Obsidian 볼트 동기화됨' 안내)"
```

auto-merge **금지** — 사용자 리뷰 후 수동 머지.

### Step 6 — 최종 확인

```bash
gh pr view --json url --jq .url
```

PR URL 이 출력되면 성공. 출력 없으면 Step 5 실패한 것 — 다시 실행.
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

- [[live-capitulation-bounce]] — 자동 계좌 전략 1 (투매반등 평균회귀, `docs/specs/strategies/live-capitulation-bounce.md`)
- [[live-donchian-breakout-btcgate]] — 자동 계좌 전략 2 (돌파/터틀 추세, `docs/specs/strategies/live-donchian-breakout-btcgate.md`)
- `docs/specs/strategies/cs-tsmom-crypto-daily.md` — [레거시] 중지된 cs-tsmom 전략
- [Claude Code Routines docs](https://code.claude.com/docs/en/routines)
- `docs/specs/strategies/.ai.md` — 전략 spec 디렉토리
- `src/dashboard/app.py` — `/api/journal/today` endpoint, `/manual` 폼
- `services/obsidian_mcp/` — 볼트 동기화 (#51)
