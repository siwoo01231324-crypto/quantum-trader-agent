---
type: spec-architecture
id: cs-tsmom-daily-report
name: 일일 거래 리포트 (자동 + 수동 대조 분석)
status: active
target: Claude Code Routines
schedule: "daily 23:55 KST"
owner: siwoo
created: 2026-05-21
last_updated: 2026-06-25
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
- `auto_pnl_ledger` — **일일손익의 단일 진실** (거래소 청산이력 history-position,
  로컬 export). 필드: `ok`, `total_net`(=오늘 net USDT), `wins`/`losses`,
  `gross_win`/`gross_loss`, `profit_factor`, `n_positions`, `positions`
  ([{symbol, side, net, open_kst, close_kst}]). **자동 계좌의 손익·승패·PF·종목별
  실현손익은 전부 이 필드로 산출** (규칙 4). `ok:false` 면 "ledger 데이터 없음
  (로컬 export 누락)" 1줄로 넘긴다. ⚠️ **체결가(entry/exit price)·수량(qty)·명목
  금액은 이 필드에 없다** — history-position 은 netProfit 만 제공. 그 칸은 리포트에
  "(미기재 — API 제약)" 으로 둔다 (WAL auto_fills 는 금지 소스라 가격도 신뢰 불가).
- `auto_fills` — 자동 체결 WAL 이벤트 (list, strategy_id/symbol/side/qty/price/ts).
  ⚠️ **손익 계산 금지** (규칙 4 — 유령/누락 fill 로 방향·금액 틀림). **체결 시각·
  종목·신호 대조용 참고로만** 사용. 카운트·PnL 의 base 는 `auto_pnl_ledger`.
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
- `airborne_fires` — **airborne v1.1 텔레그램 알림** (BB 40% 되돌림 시그널)
  전수 (list). 각 item: `ts` (UTC ISO), `symbol`, `side` (long|short),
  `fire_close` (알림 발사 시점 1h close 가격), `trigger`. 이 알림은 BB-reversal
  시그널의 시각화 reproduction 일 뿐 **자동매매와는 분리** (qta-airborne-daemon
  컨테이너) — 사용자 의사결정 보조용 채널. 매일 적중률을 분석해 신호 품질을
  모니터링한다. `cand-c-2026-05-20-live-breakout-with-atr-stop` 같은 자동 전략
  fill 과 혼동하지 말 것.
  > ✅ **소스는 영속 store(`logs/airborne_fires/history.jsonl`) — 종일 전수**.
  > 과거 export 가 ephemeral `docker logs` 만 읽어 컨테이너 재생성/rotation 시
  > 그 이후 발화만 잡혀 "저녁 100% 집중" 잘림 착시가 났던 버그는 수정됨
  > (2026-06-25). fire 가 특정 시간대에만 몰려 보이면 컨테이너 재생성 의심 —
  > auto_fills 분포(종일)와 대조해 검증.
- `airborne_sim` — **airborne fire 적중 sim 집계** (dashboard 영속 캐시
  `logs/airborne_fires/sim_cache.jsonl` 에서 export 가 미리 계산해 넣음). 필드:
  `ok`, `rule`(적용 룰 문자열), `fires_total`/`fires_simulated`/`fires_uncached`
  (커버리지), `n`/`tp`/`sl`/`sl_first`/`timeout`, `win_rate`(0-1), `sum_pct`(gross),
  `net_pct`(fee 차감), `pf`, `mean_pct`, `by_side`{long/short}, `by_kst_bucket`[].
  **규칙 6 의 적중 분석은 이 필드를 그대로 읽어 표로 옮긴다 — 직접 klines fetch +
  sim 하지 말 것** (클라우드는 Binance proxy 403 으로 어차피 실패, 그래서 N/A 였음).
  `ok:false` 거나 `fires_simulated=0` 이면 "적중 sim 데이터 없음 (캐시 미수록)"
  1줄로 넘긴다.

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
- **방향별(long/short) 분해 — 필수**: `auto_pnl_ledger.positions` 를 `side` 로 묶어
  long·short 각각 n / 승·패 / net 합 / PF 를 낸다 (klines 불필요, 항상 산출 가능).
  손실/이익이 **한 방향에 쏠려 있으면** 그게 시간대보다 1차 변수다 — 명시하고 "내일을
  위한 한 줄"에 반영. (2026-06-24 실측: 롱 net −21.84/PF0.47 vs 숏 +1.62/PF1.06 —
  손실 전액이 롱. airborne_sim.by_side 와 방향 결론이 일치하면 교차검증으로 강조.)
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

### 규칙 6 — airborne 알림 적중 분석 (2026-05-26 추가, 2026-06-25 sim 소스 전환)

`airborne_fires` 가 비어있지 않으면 적중 분석 섹션을 넣는다.

> ✅ **적중 수치는 `airborne_sim` 필드를 그대로 읽는다 — 직접 klines fetch·시뮬 금지**
> (2026-06-25 전환). 클라우드 routine 은 Binance FAPI proxy 403 으로 봉을 못 받아
> 적중 수치가 통째로 N/A 였다. 이제 **로컬 export 가 dashboard 영속 sim 캐시
> (`logs/airborne_fires/sim_cache.jsonl`, 이미 Binance 봉으로 계산된 결과) 를 집계해
> `airborne_sim` 으로 JSON 에 넣는다**. routine 은 그 필드를 표로 옮기기만 하면 된다.
> `airborne_sim.rule` 에 그날 적용된 룰 문자열이 들어있으니 표 헤더에 그대로 명시.
> `ok:false`/`fires_simulated=0` → "적중 sim 데이터 없음 (캐시 미수록)" 1줄로 끝.
> ⚠️ 커버리지(`fires_simulated`/`fires_total`)가 100% 미만이면 표에 "sim N/M" 명시.
> ⚠️ net%/PF 는 *알림 신호 자체* 의 sim 품질 — 실제 계좌 손익(ledger)과 별개임을 1줄로 구분.

**`airborne_sim` → 표 매핑**:
- 요약: `fires_total`/`fires_simulated`, `tp`/`sl`/`sl_first`/`timeout`, `win_rate`,
  `net_pct`, `pf`, `mean_pct`.
- 방향별: `by_side.long` / `by_side.short` (각 n/tp/sl/win_rate/sum_pct/pf). **롱·숏
  PF 비대칭이 크면 강조** — 보통 손실의 1차 변수. ledger 방향 분해(규칙 4)와 교차검증.
- 시간대별: `by_kst_bucket[]` (bucket/n/tp/sl/win_rate/sum_pct/pf) 4구간 표로.

**시간대 컨텍스트** (3일 검증 기준선 — 표본 짧음, 참고용):
- 00–06 KST 새벽: 과거 win 70% / PF 4.6 (최고). | 06–18: win 54% / PF 2.0~2.4.
- **18–24 KST 저녁: win 23% / PF 0.56 (손실)** — 저녁 신호 신뢰도 낮다는 가설.
- 오늘 `by_kst_bucket` 가 이 기준선과 부합/이탈하는지 한 줄.

**분석 항목**: 위 3개 표 + 종목 다발 발화 상위(`airborne_fires` 빈도, n≥2) + 패턴 한 줄
("저녁·롱이 손실 본체" 등) + 가능하면 어제·그제 누적 비교.

분석은 정성적으로 짧게, 수치 표는 markdown table. (참고: net%/PF 는 합산 기반이라
fee 차감률은 `airborne_sim.rule` 에 박혀 있음 — 보통 왕복 0.034%.)

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

### 규칙 8 — 필터·게이트 일일 감사 (2026-06-19 추가)

airborne_fires 시뮬 결과(규칙 6)를 *현재 라이브에 적용 중인 필터·게이트* 기준으로
교차분석해, 각 필터/게이트가 오늘(+최근 누적) 장세에서 **이기는 신호(TP)를 거르는지
(손해) vs 지는 신호(SL)를 거르는지(이득)** 를 판정한다. **레짐이 바뀌면 같은 필터가
반대로 작동**하므로(예: 하락장 기준으로 만든 BTC 롱차단이 회복장에선 이기는 롱을
죽임 — 2026-06-19 실측) 매일 점검해 필터/게이트 수정 판단 근거를 만든다.

**현재 적용 중 필터·게이트** (live, 2026-06-19 기준 — 코드 변경 시 이 목록 갱신):
- **시간 게이트**: KST `{1,2,3,5,6,7,8,23}` 진입 (kst-hours 양방향)
- **BTC 추세 롱차단**: BTC EMA200(1h) 하회 **AND** 24h<-2% 동시일 때만 LONG 차단
  (2026-06-19 AND/-2% 강화. 옛 OR/-1% 은 횡보장 롱 과차단)
- **모멘텀**: 숏 24h>+20%(펌핑) / 롱 24h<-10%(폭락) → skip
- **변동성**: 코인 평균 1h 변동폭 >5%/h → skip (양방향)

**각 필터/게이트 절차**:
1. 통과(필터 후 진입) fire 와 차단된 fire 를 분리.
2. 차단된 fire 의 시뮬 outcome(TP/SL/timeout) 분포 + net% 합 계산.
3. **판정**:
   - 차단분 net < 0 (주로 SL 거름) → ✅ "정상 작동 (손실 회피)".
   - 차단분 net > 0 (주로 TP 거름) → ⚠️ "이 필터가 이기는 신호를 거름 — 현 레짐
     역효과 가능. 완화 검토" + 거른 **TP 건수·net%** 명시.
4. **게이트**: 게이트 *안* vs *밖* 시각의 win%/net 비교. 밖이 더 좋으면 "게이트
   재검토 플래그" + 어느 시각이 좋은지.

**데이터**:
- **시간게이트 안/밖 비교**: `airborne_sim.by_kst_bucket` 의 구간별 win%/net 으로 한다
  (klines 불필요). ⚠️ auto_fills 로 "게이트 밖 진입 N건" 을 세지 말 것 — 진입·청산 fill 이
  섞여 있고(청산은 게이트 무관) 전략별 게이트 룰이 달라(bb-reversal 만 시각 제한,
  short-whitelist 24h) confound 다. 정량 위반 판정 보류, 구간 win/net 패턴만 본다.
- **BTC 추세 / 모멘텀 / 변동성**: BTC·코인 1h klines 가 필요한데 **클라우드 routine 은
  Binance proxy 403 으로 못 받는다**. 입력 JSON 에 이 수치가 따로 없으면 해당 필터는
  "데이터 부족(klines 차단) — 감사 skip" 으로 둔다. (사후 재구성 캐시 대상도 아님 —
  그 시점 게이트 차단 여부는 별도 export 가 생기기 전까지 정량화 불가.)

⚠️ **단일일로 필터/게이트 바꾸지 말 것** — 7일 미만 누적은 "참고(표본 작음)" 명시.
**5일+ 연속 같은 방향**(예: BTC롱차단이 5일째 TP 거름)일 때만 조정 신호로 제안.
과적합 방지 — 라이브 누적이 진짜 out-of-sample 검증.

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
airborne_fires: <airborne_fires 개수 (store 기준 종일)>
airborne_tp: <airborne_sim.tp (없으면 N/A)>
airborne_sl: <airborne_sim.sl + sl_first (없으면 N/A)>
airborne_net_pct: <airborne_sim.net_pct, 소수 2자리 (없으면 N/A)>
created: {date_kst}
tags:
- trading-journal
- daily-report
- auto-account
- manual-account
- airborne-alerts
---

# {date_kst} 거래 리포트

## 한눈에
- 자동: 청산 N건 → 익절 N / 손절 N (net X USDT, PF X). **롱 net X(PF X) vs 숏 net X(PF X)** — 손실 쏠린 방향 명시.
- 수동: 체결 N건 → 익절 N / 손절 N (총 Y KRW + Z USDT)
- 알림: airborne FIRE N건(종일) → sim N/M 적중 승률 X% / PF X / net X%. 롱·숏 PF 비대칭 1줄.
- 필터 감사: (klines 차단 시 "BTC/모멘텀/변동성 감사 skip"; 시간대 win/net 패턴은 한 줄)
- 오늘의 핵심 패턴: (방향·시간대 중 손실 1차 변수 1-2줄 — sim·ledger 교차검증)

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

### 방향별 PnL (ledger — 규칙 4, 항상 산출)

| 방향 | n | 승 | 패 | net USDT | PF |
|---|---:|---:|---:|---:|---:|
| long | N | N | N | +X | X |
| short | N | N | N | +X | X |

(손실/이익 쏠린 방향 1줄 + airborne_sim.by_side 와 일치 여부.)

### cs_tsmom 시그널 ↔ 실제 발주 대조
- 시그널 ENTER: [...]
- 실제 fill 매칭: [...]
- 격차: (시그널 떴는데 발주 없으면 원인 추정)

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

## airborne 알림 적중 분석 (BB 40% 되돌림 시그널)

(airborne_fires 가 비어있으면 한 줄: "오늘 airborne 알림 없음." 끝.)

(있으면 규칙 6 의 시뮬레이션 룰로 분석:)

### 시뮬레이션 요약 (`airborne_sim` 그대로 — 헤더에 `airborne_sim.rule` 명시)

| 항목 | 값 |
|---|---|
| FIRE 총 건수 | {fires_total} (store 종일) |
| sim 커버리지 | {fires_simulated}/{fires_total} |
| TP / SL / timeout | {tp} / {sl+sl_first} / {timeout} |
| 승률 / PF / net% | {win_rate}% / {pf} / {net_pct}% (fee {rule}) |

> net%/PF 는 *알림 신호 sim 품질* — 실제 계좌 손익(ledger)과 별개.

### 방향별 (`airborne_sim.by_side`) — 손실의 1차 변수

| 방향 | n | TP | SL | 승률 | sum% | PF |
|---|---:|---:|---:|---:|---:|---:|
| long | N | N | N | X% | +X% | X |
| short | N | N | N | X% | +X% | X |

→ 롱·숏 PF 비대칭 강조 + 규칙 4 ledger 방향 분해와 일치 여부(교차검증).

### KST 시간대별 (`airborne_sim.by_kst_bucket`)

| 구간 | n | TP | SL | 승률 | sum% | PF | 비고 |
|---|---:|---:|---:|---:|---:|---:|---|
| 00-06 새벽 | N | N | N | X% | +X% | X | 기준선 70% / PF 4.6 |
| 06-12 오전 | N | N | N | X% | +X% | X | |
| 12-18 오후 | N | N | N | X% | +X% | X | |
| 18-24 저녁 | N | N | N | X% | +X% | X | **기준선 23% / PF 0.56** |

### 종목 · 패턴
- 종목 다발 발화 상위 (`airborne_fires` 빈도, n≥2)
- 패턴 한 줄: (예 "저녁·롱이 손실 본체 — sim·ledger 양쪽 일치")

## 필터·게이트 일일 감사

(규칙 8. airborne_fires 없으면 한 줄: "감사 데이터 없음." 끝.)

### 적용 중 필터/게이트가 오늘 도움됐나? (차단분의 TP/SL)

| 필터/게이트 | 차단 n | 차단분 TP/SL | 차단분 net% | 판정 |
|---|---:|---:|---:|---|
| 시간게이트 (밖 시각) | N | t/s | +X% | 밖이 +net 이면 ⚠️ 재검토 |
| BTC추세 롱차단 | N | t/s | +X% | net>0 면 ⚠️ 이기는 롱 거름 |
| 모멘텀 (숏펌핑/롱폭락) | N | t/s | −X% | net<0 면 ✅ 정상 |
| 변동성 >5%/h | N | t/s | −X% | net<0 면 ✅ 정상 |

- **⚠️ 역효과 플래그**: (오늘 차단분 net>0 인 필터 + 거른 TP 건수·net% 명시. 없으면
  "오늘 모든 필터 정상(손실 회피)".)
- **게이트 안/밖**: 게이트내 win%·net vs 게이트외 win%·net. (밖이 뚜렷이 좋으면 어느
  시각인지.)
- **누적 추세**: (각 필터/게이트가 며칠째 같은 방향인지 — 5일+ 지속 시 조정 제안.
  표본 7일 미만이면 "참고만, 단일일 조정 금지".)

## 내일을 위한 한 줄
- 자동: (오늘 결과 기반 1줄 — auto_fills 없으면 "내일은 qta.exe 가동 +
  cs_tsmom 자동 발주 점검")
- 수동: (오늘 결과 기반 1줄 — 실 거래 없으면 "내일은 note 에 지표·조건
  명시한 실 거래 기록")
- 알림: (오늘 airborne 적중률 기반 1줄 — 시간대·종목 패턴 다음날 확인 포인트)

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

- [[cs-tsmom-crypto-daily]] — 자동 계좌 핵심 전략
- [Claude Code Routines docs](https://code.claude.com/docs/en/routines)
- `docs/specs/strategies/.ai.md` — 전략 spec 디렉토리
- `src/dashboard/app.py` — `/api/journal/today` endpoint, `/manual` 폼
- `services/obsidian_mcp/` — 볼트 동기화 (#51)
