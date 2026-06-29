# 인트라데이→스윙 전략 리서치 핸드오프 (2026-06-24)

> 다음 세션 재개용. `.draft.md` 라 invariant 검증 제외. 정식화 시 프론트매터 붙여 승격.

## 목표 (사용자 요청)
"한 종목에 하루 몇 번씩 저~중 레버리지로 짧게 먹고 나오는, 안정적이지만 폭발적인 전략."
→ 처음엔 인트라데이 스캘프로 접근했으나 **비용 벽**으로 기각, 최종적으로 **스윙 전략**으로
방향 전환(차트 캡처 `C:\Users\watch\Desktop\CAPTURE\퀀텀\74.png` 의 추세 타점 잡기).

## 이번 세션 결론 (재조사 금지 — 이미 확정)

### 1. 인트라데이 confluence 스캘프 = 비용 벽으로 죽음 (확정)
- BTC 1m/5m/15m, 지표 1~3개 AND 전수탐색(RSI·EMA추세·BB·돌파·거래량·캔들), train70/OOS30 분할.
- **랜덤 baseline(3x, 실효비용 0.034%) ≈ −0.10%/거래, 보수 10bp 면 −0.32%/거래.**
  수수료×레버리지가 매 거래 시작부터 까는 고정손실. 자주·잘게 칠수록 누적돼 죽음.
- **레버리지는 흑자 여부·PF 불변** (수익 크기만 비례 스케일). 흑자조건 = 평균 price-move > 수수료.
- 타임프레임별(LEV=1, 실효비용): **1m 흑자 0개**(신호 122개 랜덤 이겨도 움직임이 작아 비용 못넘김),
  **15m 0개**(신호 희소), **5m 8개**(유일 스윗스팟) — 그러나 8개 전부 정직비용이면 적자.
- 살아남은 패턴은 항상 **추세정렬 평균회귀**: `rsi_os+상승추세+BB하단→롱` / `rsi_ob+하락추세→숏`.
  추세 거스르는 진입은 다 죽음.

### 2. B 2차검증(5m 추세정렬 평균회귀, 5 메이저) = 보조 edge로도 기각
| 셋업 | 실효(0.034%) | 정직(10bp) | 2026 |
|---|---|---|---|
| LONG | +0.017% / PF 1.08 | **−0.049% / PF 0.80** | −0.02 적자 |
| SHORT | +0.019% / PF 1.13 | **−0.047% / PF 0.73** | −0.01 적자 |
- 다중코인 재현은 되나(신호엔 정보 있음), **정직비용이면 둘 다 적자 + 2026 감쇠**. 단독 기각.
- 병목은 신호가 아니라 **비용** → A(메이커-온리 진입)로만 살릴 수 있음. (A는 미실행, 추후 옵션)

### 3. 핵심 통찰
- **스윙(거래당 큰 움직임)이 정답** — 고정수수료 비중이 작아져 인트라데이의 비용 벽을 정면 회피.
  검증 끝난 일봉 터틀(`project_turtle_daily_candidate`)이 작동한 이유와 동일.
- 크립토 **숏은 구조적 손실**(상방 drift) — 반복 확인. 롱 편향이 정답.

## 설계한 스윙 전략 — "EMA 리본 + 투매반등 + 추세전환" (3-셋업)

차트 해부: MA = **EMA 리본**(빨강 EMA200 / 주황 EMA50 / 가격붙은 EMA20).
- 🔴 빨강동그라미(숏) = 하락추세서 리본까지 반등→거부음봉→숏.
- 🟢 초록동그라미(롱) = (a)긴 아랫꼬리 투매바닥 반등, (b)리본 전체 모멘텀 돌파 추세전환.

**레짐(EMA20/50/200):** 하락=20<50<200 하향 / 상승=20>50>200 상향.

| # | 셋업 | 진입 | 청산 | 성격 |
|---|---|---|---|---|
| 1 | 리본눌림 **롱** | 상승레짐 + EMA20/50 눌림 + 반등확인(아랫꼬리/재돌파) | EMA50−ATR손절, EMA20 트레일 | 추세추종 중빈도 |
| 2 | 투매반등 **롱** ⭐ | 가격 EMA20 −N×ATR 이상 이탈 + 아랫꼬리≥2×몸통 + 거래량스파이크 | 꼬리저점 타이트손절, R:R 큼 | **최고가치·저빈도** |
| 3 | 리본거부 **숏** | 하락레짐 + 리본까지 반등 + 거부음봉 | EMA50+ATR손절, 빠른청산 | 위험(보조, 생략가능) |

**설계 원칙:**
- TF = **1h~4h 스윙**(일봉보다 자주, 인트라데이보다 비용 안전). 보유 수일~수주.
- **②투매반등 롱이 핵심** — 차트 최대 수익자 + "큰 추세 길게" 정합 + 상방 drift 보상.
- ③숏은 보조(크립토 숏 구조적 손실) — 깊은 확정 하락레짐만·빠른청산, 생략 가능.
- 추세전환 롱 = 사실상 **터틀 돌파를 1h에 얹은 형태**(이미 검증된 로직).

## 현재 시황 (설계 반영, 2026-06-24)
BTC ~$63k, **YTD −30%, 약세, Fear&Greed 20(극공포)**, 4h 50MA 하락, ETF 유출 사상최대.
→ 지금은 **하락레짐**. 당장 유효한 건 ③숏-반등(위험) 또는 ②**투매반등 롱 대기**.
추세전환 롱은 리본 반등 전까지 보류. (출처: Yahoo Finance / Intellectia BTC June 2026)

## 재개 결과 #1 — ②투매반등 롱 5y 백테스트 (2026-06-25, ✅ edge 게이트 통과)

스크립트 `scripts/_capitulation_bounce_backtest.py` (13 메이저 풀-5y, random-vs-signal, 정직 10bp).
신호 = (low ≤ EMA20 − N×ATR 투매이탈) AND (아랫꼬리 ≥ wick_m×몸통) AND (close>open) AND (거래량 > vol_m×MA20).

**핵심 결론: edge 는 진짜다.** 랜덤진입 baseline 은 정직비용에서 −0.13~−0.23%/거래(적자)인데,
신호는 양수로 뒤집힘 → 투매반등 신호가 랜덤+drift 를 실제로 이긴다. **에어본(=랜덤)·인트라데이(비용벽)·
TOTAL3(=중복) 다 실패한 뒤 일봉터틀에 이어 두 번째로 edge 게이트를 통과한 신호.** 스윙 가설 검증됨.

| TF | best robust config (n≥200) | 청산 | n(5y) | exp%/거래 | PF | win% | 흑자연도 |
|---|---|---|---|---|---|---|---|
| **4h** | Ndev2.5·vol2.0·wick1.5·RR2.0 | **꼬리저점손절(geomB)** | 247 | **+0.73** | **1.30** | 41 | **5/6** |
| 4h | (동 config) | 고정ATR손절(geomA) | 247 | +0.22 | 1.09 | 32 | 5/6 |
| 1h | Ndev2.0·vol2.0·wick1.5·RR3.0 | 고정ATR/꼬리저점 비슷 | 1431 | +0.12 | 1.08 | 32 | 5/6 |

- **4h + 꼬리저점 손절(설계 원안 geomB)이 스윗스팟** — 고정ATR PF1.09 → 꼬리저점 PF1.30/exp+0.73%.
  긴 아랫꼬리 아래에 손절을 두면 반등 여유가 생겨 비대칭 R:R 이 살아남(설계 의도대로). 1h 는 개선 없음.
- vol3.0(깊은투매+큰거래량)은 PF 1.4~1.67 로 더 강하지만 n<85/5y → **표본부족·과적합 위험으로 단독신뢰 불가**.
- 레짐 의존: 2022(베어)·2023(횡보) 약세, 2025 강세가 견인. 흑자연도 5/6 이 한계선.

**⚠️ 미해결 = MDD.** 순차 단일슬롯 full-notional 복리로 보면 MDD 82~92%(LEV=1). edge 는 건전하나
**리스크 구성이 아직 안정적이지 않다.** 터틀이 risk1%/top_n 분산으로 MDD 22% 만든 것처럼,
이건 **사이징(fractional risk)+분산보유**로 MDD 를 잡아야 "안정적+폭발적" 목표 충족. = 아래 2번 작업.

## 재개 결과 #2~#4 — 포트폴리오 사이징 + 합성 + 최종확정 (2026-06-25)

스크립트 `_capitulation_portfolio.py`(#2), `_swing_composite.py`(#3), `_swing_final.py`(#4).

**#2 사이징으로 MDD 해결됨.** 투매반등 단독을 risk1%/거래 + top_n8 동시보유로 사이징하니 MDD 82%→12%.
하지만 거래 희소(237/5y)로 자본가동률 낮아 CAGR 4.5%뿐 → **단독은 안정하나 폭발 부족.** 합성 필요.

**#3 합성 — 셋업별 random gate 적용 (롱보유 랜덤 baseline +0.385%):**
| 셋업 | exp%/거래 | PF | edge | 판정 |
|---|---|---|---|---|
| ① 투매반등(mean-rev) | +0.88 | 1.37 | +0.50 | ★PASS |
| ② 리본눌림(trend pull) | +0.19 | 1.09 | **−0.20** | **FAIL — 랜덤보다 못함, 제외** |
| ③ 돌파(turtle 4h) | +0.65 | 1.24 | +0.26 | ★PASS |
| ③' **돌파+BTC레짐게이트** | **+0.95** | **1.37** | **+0.56** | ★PASS (게이트가 edge·MDD 둘다 개선) |

- **②리본눌림은 random gate 탈락** — 설계했으나 랜덤 롱보유보다 못해 제외(랜덤비교 원칙의 승리).
- **핵심: BTC 4h 상승레짐(close>EMA200) 게이트를 돌파에 적용** → 베어장 가짜돌파 차단.
  edge +0.26→+0.56, 그리고 합성 MDD 39%→27%. 평균회귀(투매반등=베어장 강함) + 추세추종(돌파=불장 강함)
  의 레짐 비상관이 분산효과의 본질.

**#4 최종확정 검증 (4h, 2-셋업, risk1%/top_n8, 정직10bp):**
| 유니버스 | lev | CAGR% | MDD% | Sharpe | Calmar | 최종x |
|---|---|---|---|---|---|---|
| **majors13** | 1.0 | 23.5 | **27.8** | 1.01 | 0.85 | 2.9 |
| majors13 | 1.5 | 30.2 | 36.3 | 1.01 | 0.83 | 3.7 |
| majors13 | 2.0 | 37.0 | 40.6 | 1.05 | 0.91 | 4.8 |
| top30(생존편향완화축) | 1.0 | 34.9 | 35.5 | 1.21 | 0.98 | 4.5 |
| top30 | 2.0 | 49.7 | 41.3 | 1.21 | 1.20 | 7.5 |

- **생존편향 민감도 통과** — 최근상장 포함 top30 이 오히려 더 강함(엣지가 생존자 cherry-pick 아님).
- **연도별 robust** majors13: 2021 PF1.68 / 2022 PF1.05 / 2023 PF1.35 / 2024 PF1.49 / 2025 PF1.31 / 2026(부분)PF0.84.
  현 하락레짐(2026)에서도 near-flat — BTC게이트가 돌파 차단, 투매반등이 버팀.
- per-coin 분산 양호(SOL/XRP/AVAX/DOGE/ADA/BTC). top30 은 PEPE·SPK(9거래) 등 meme/저유동 과대 → majors13 채택.

## ✅ 확정 전략 (research 종결, 2026-06-25)

**이름(가칭):** `swing-meanrev-breakout-4h` · **패러다임:** universe-scan · **TF:** 4h · **방향:** 롱only.
**유니버스:** 유동성 메이저 13 — BTC ETH SOL XRP BNB BCH DOGE TRX LTC ZEC ADA LINK AVAX.

**셋업 ① 투매반등 (mean-reversion):**
- 진입: `low ≤ EMA20 − 2.5×ATR(14)` AND `아랫꼬리 ≥ 1.5×몸통` AND `volume > 2×MA20` AND `close>open`
- 청산: 손절 = 신호봉 꼬리저점, TP = 2R(=entry+2×(entry−저점)), timeout 30봉(~5일)

**셋업 ③ 돌파 BTC게이트 (trend):**
- 진입: `close > Donchian20 상단` AND `close > EMA200` AND `BTC 4h close > EMA200`(레짐게이트)
- 청산: 손절 = entry−2×ATR(14), 추세청산 = `close < Donchian10 하단`, timeout 60봉(~10일)

**사이징:** risk 1%/거래(손절거리 기준), 동시 top_n=8 슬롯, 슬롯상한=자본×(1/8)×lev.
- **lev 1.0 = 안정(권고 기본):** CAGR ~24%(majors)/~35%(top30), MDD ~28~36%, Sharpe ~1.0~1.2.
- **lev 1.5 = 중레버(권고 적극):** CAGR ~30~43%, MDD ~36~40%, Sharpe ~1.0~1.2. (사용자 "저~중레버" 부합)

**기대수익(5y·정직10bp·majors13·lev1.0):** CAGR ~24% / MDD ~28% / Sharpe ~1.0 / Calmar ~0.85 / 5∼6년 흑자.

**남은 caveat(정직):** ① 진성 생존편향(폐지코인 부재) — 과대평가 가능, 단 top30 robustness 로 일부 완화.
② 2026 현 하락레짐 약세(PF0.84) — 라이브 시작 시 DD 구간 가능. ③ basket sim 은 단일 equity·일별 Sharpe 가정.

## 재개 #5 — 라이브 청산 의미론 재검증 (2026-06-25, 코딩 전 게이트) ⚠️

`_swing_live_semantics.py`. live-scanner 는 `LivePositionRiskManager` 가 **stop/TP/trailing 만** 평가 —
백테스트의 timeout·Donchian채널청산 **표현 불가**. 코딩 전 "청산룰 번역 후 엣지 생존?" 확인.

- **① 투매반등은 라이브에서 더 좋아짐**(no-timeout): PF 1.37→**1.63**, exp +0.88→**+1.73%**. 깨끗한 fit, 즉시 코딩 가능.
- **③ 돌파는 라이브에서 열화**: 엣지의 핵심이 Donchian10 **채널청산**인데 risk manager 가 못 함.
  trailing 근사 best=15% 일 때 PF 1.29(vs 채널 1.35), 좁은 trailing(4~8%)은 PF 0.54~0.90 붕괴.
- **합성 라이브(lev1):** CAGR **21%** / MDD **32%** / Sharpe **0.82** (vs 백테스트 24%/28%/1.0). 게이트는 통과하나
  Sharpe 하락 + 2022·2026 음수. 돌파는 본질이 **채널-트레일링 추세전략**이라 stateless buy-only live-scanner 와 안 맞음.

**→ 결정 필요(사용자):** (A) 투매반등만 live-scanner 로 먼저(깨끗·PF1.63·단 저CAGR) / (B) 둘 다 live-scanner,
돌파는 trailing15%(CAGR21%/MDD32% 정직열화) / (C) 채널청산 인프라 구축(돌파가 sell 신호 발행 or
LivePositionRiskManager 확장 → 백테스트 풀엣지 보존, 인프라 추가공수).

## 재개 #6 — 정식 전략화: 투매반등 구현 완료 ✅ (2026-06-25, 미커밋)

사용자 결정: lev1.0 기본 / 동적 top-N(유동성) / 바로 구현 / 청산열화는 **C(채널청산 인프라 구축)**.

**투매반등(capitulation) = live-scanner 패러다임으로 완전 구현** (깨끗한 fit, 인프라 무변경):
- `src/backtest/strategies/live_capitulation_bounce.py` — `LiveCapitulationBounce(LiveScannerMixin)`.
  진입 4조건 + 동적 override(손절=꼬리저점, TP=2R) + time-stop 면제 + meanrev regime. get_interval="4h".
- `tests/backtest/test_live_capitulation_bounce.py` — 12건 통과(buy/warmup/4게이트 boundary/동적stop/검증).
- `docs/specs/strategies/live-capitulation-bounce.md` — type:strategy, paradigm:live-scanner, 5y수치 frontmatter.
- `configs/orchestrator/production.yaml` — commented candidate entry.
- `python scripts/check_strategy_completeness.py --id live-capitulation-bounce` → **통과(8레이어)**.
- 패러다임 결정: universe-scan(=주간리밸 compute_weights)은 stop/TP 이벤트청산 표현 불가 → **live-scanner**가
  기계적으로 맞음(Candidate-C가 1d봉 live-scanner 재검증한 선례). 앙상블 wrapper는 분산파괴로 REJECTED → **2전략 병렬**.

## 재개 #7 — 돌파 전략 + 채널청산 인프라 구현 ✅ (2026-06-25, 미커밋)

사용자 결정: **C 채널청산 인프라, 지금 신중히 진행**. additive·회귀박제 원칙으로 구현:

- `src/backtest/strategies/live_donchian_breakout_btcgate.py` — `LiveDonchianBreakoutBtcGate`.
  Donchian20 돌파 + EMA200 + BTC 4h 레짐게이트(universe_ohlcv["BTCUSDT"]) → buy. 2ATR 손절 override +
  `channel_exit_level(history)`=Donchian10 하단. time-stop 면제, trend regime.
- `tests/backtest/test_live_donchian_breakout_btcgate.py` — 12건 통과(buy/BTC게이트3/돌파·EMA boundary/채널레벨/검증).
- **채널청산 인프라(`LivePositionRiskManager`, additive — evaluate/sweep_timeouts race-path 무변경):**
  - `register_channel_exit(sid, level_fn)` + `sweep_channel_exits(now, history_lookup)` — 보유분 순회,
    `close < level` 이면 reduce_only sell. in-flight guard·entry_ts reset·WAL·on_exit 동일 재사용.
  - `tests/portfolio/test_live_position_channel_exit.py` — 11건(발동/미발동/long-only/None/guard/통합/
    **회귀: evaluate·sweep_timeouts 무영향 박제**). 기존 risk manager 52건 + 신규 = **87건 전부 통과**.
  - `scripts/live_run.py::_register_exit_policies` — `channel_exit_level` 메서드 가진 전략 자동 등록(guarded).
- `docs/specs/strategies/live-donchian-breakout-btcgate.md` + production.yaml commented candidate.
- `check_strategy_completeness` → 37 전략 **error 0** (두 신규 전략 통과).

**남은 1조각 (loop 배선, testnet 게이트):** `_run_timeout_sweep` 옆에 `sweep_channel_exits` 주기호출 +
**recent-OHLCV-bars 캐시**(history_lookup 소스) 필요 — timeout sweep 은 last-price 만 있어 봉 부재.
이 캐시 배선 + testnet 검증 전까지 돌파 라이브는 ① 2ATR 손절만 작동(채널청산 ② 미동작) → commented
candidate 유지(자동활성 안 됨). 봉캐시는 snapshot builder 가 채우게 하거나 sweep 전용 fetch.

## 남은 작업 — (구버전 설계, #7 에서 대부분 구현됨)

돌파는 Donchian10 **채널청산**이 엣지의 핵심인데 `LivePositionRiskManager.evaluate(last_price)`는
지표 접근 불가(가격 임계만). max_hold 도 무용(타임아웃은 오히려 손해 — #5 확인). 채널청산 구현 설계:

**설계 (position 가시성 + 전략 sell 발행):**
1. `_async_orchestrator.py` per_symbol_snap(line~422)에 해당 (sid,symbol) **현재 포지션**(held qty + entry)
   주입 — `universe_ohlcv`처럼. (position_store/pnl `_cost_basis` 접근 배선 필요.)
2. 돌파 전략 `on_bar`: 보유 중 AND `close < Donchian10 하단` → `Signal(action="sell", reduce_only)`.
   미보유 + 돌파신호 → buy(2ATR 손절 override). LivePositionRiskManager의 2ATR 손절과 공존(먼저 닿는 쪽 청산).
3. orchestrator가 live-scanner sell → reduce_only 청산 intent 라우팅(현재 buy-only 컨벤션 → sell 경로 검증/배선).
4. 회귀 박제: 리스크매니저 ~15건 race-fix(in-flight guard·cost-basis sign·entry_ts reset 등) 무영향 테스트.

**주의(feedback_trading_logic_regression_check):** 실거래 머니 코드라 증상땜질 금지·근본진단·과거방어 박제.
marathon 세션 꼬리에 급하게 하지 말고 집중 패스 권장. 돌파 미구현 시 단독 투매반등은 저CAGR(9%) 앵커.

## 다음 세션 할 일 (= 정식 전략화 구현, 커밋 전 사용자 확인)
1. ~~#1 투매반등 단독 / #2 사이징 / #3 합성 / #4 최종확정~~ ✅ **전부 완료. 전략·사이징·수익·종목 확정.**
2. **정식 전략화 (CLAUDE.md 8레이어):** `docs/specs/strategies/swing-meanrev-breakout-4h.md`(type:strategy,
   paradigm:universe-scan) + `src/backtest/strategies/` 모듈(universe-scan + LiveScannerMixin 아님) +
   단위테스트 + `production.yaml` candidate + orchestrator 등록 + `register_strategy_returns` +
   `python scripts/check_strategy_completeness.py` 통과. 5y 게이트(PF>1·expectancy>0)는 이미 충족.
3. lev(1.0 vs 1.5)·유니버스(13 고정 vs 동적 top-N) 최종 결정은 구현 착수 전 사용자 확인.
4. (선택) A: 메이커-온리 비용구조로 인트라데이 재검증 — 병목이 비용임이 확정됐으니 유효한 시도.

## 이번 세션 산출 스크립트 (scripts/, 임시 `_` prefix, 미커밋)
- `_intraday_confluence_search.py` — BTC 인트라데이 전수탐색(train/OOS·random게이트). FEE/LEV 모듈변수.
- `_intraday_tf_sweep.py` — 1m/5m/15m × 저레버 비교.
- `_intraday_b_validate.py` — 5m 추세정렬 평균회귀 5메이저·연도별 검증.
- (참고) `_turtle_explosive_sweep.py`, `_turtle_final_concentrated.py` — 직전 터틀 집중/레버 탐색.
- **`_capitulation_bounce_backtest.py`** (2026-06-25) — ②투매반등 롱 5y sweep + random-vs-signal +
  연도별 robust + geometry A/B(꼬리저점) + 단일슬롯 포트폴리오. `--tf 1h|4h`. 윈도우는 `PYTHONUTF8=1` 필요.
- **`_capitulation_portfolio.py`** (#2) — 투매반등 거래 stateful 생성 + basket 사이징 sweep(top_n×risk×lev). MDD 제압.
- **`_swing_composite.py`** (#3) — 3-셋업(투매반등/리본눌림/돌파±BTC게이트) 셋업별 edge gate + 합성 사이징.
- **`_swing_final.py`** (#4) — 확정 2-셋업 최종검증: majors13 vs top30(생존편향), per-coin·연도별·곡선. 모두 `PYTHONUTF8=1`.

## 관련 메모리
- `project_turtle_daily_candidate` — 검증완료 일봉 터틀(롱), 이미 PR #482 candidate.
- `project_research_signal_screen_summary` — 신호 리서치 종합(4원리).
- `project_airborne_signal_equals_random_5y` — 에어본=랜덤.
- (신규 예정) intraday-cost-wall — 인트라데이는 비용 벽으로 죽는다.
