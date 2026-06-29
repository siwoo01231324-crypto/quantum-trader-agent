---
type: strategy
id: live-donchian-breakout-btcgate
name: Live Donchian Breakout + BTC Regime Gate (돌파 추세추종 롱, 4h 스윙)
status: candidate
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 4h
uses_signals:
- donchian-channel
- ema-trend
- btc-regime
risk_rules:
- per-symbol-dynamic-stop-2atr
- donchian10-channel-exit
owner: siwoo
created: 2026-06-25
sharpe_bt: 1.06
sharpe_live: null
mdd_bt: 0.247
annual_return_bt: 0.265
trades_bt: 1406
backtest_period: 2021-05/2026-05
last_updated: 2026-06-25
stop_loss_pct: 0.08
take_profit_pct: 0.50
trailing_stop_pct: null
profit_factor_bt: 1.37
expectancy_bt: 0.00946
verdict_5y: "CANDIDATE (비활성, 청산배선 대기). 5y·13메이저·정직10bp·random-vs-signal 통과 — 랜덤 롱보유 baseline(+0.385%/거래) 상회(exp +0.95%/거래, PF 1.37, win 31%). BTC 레짐 게이트가 edge·MDD 둘 다 개선(게이트 없으면 PF 1.24/edge +0.26 → 게이트 PF 1.37/edge +0.56). 채널청산판 단독 basket(risk1%/top_n8/lev1): CAGR 26.5% / MDD 24.7% / Sharpe 1.06. ⚠️ 채널청산(Donchian10 하단)이 엣지의 핵심인데 LivePositionRiskManager 정적 stop/TP 로 표현 불가 → 라이브 배선(ratchet) 전까지 ① 2ATR 손절만 작동(trailing 근사판 PF 1.29 로 열화). 채널청산 배선 + testnet 검증 후 활성화. 투매반등(meanrev)과 병렬 운용 시 합성 CAGR 24% / MDD 28% (레짐 비상관 분산)."
verdict_1y: null
summary_ko: |
  4h 종가 Donchian20 상단 돌파(0.1% 버퍼) + 자기 EMA200 위 + BTC 4h close>EMA200
  레짐 게이트면 buy (long-only 추세추종). 청산 2단: ① 하드 손절 entry−2×ATR(동적
  override), ② 추세 청산 close<Donchian10 하단(채널). 채널청산은 매 봉 갱신 레벨이라
  risk manager 정적 임계로 못 해 per-bar ratchet 배선 필요(미배선 시 ①만). time-stop
  면제(추세는 길게 — timeout 은 오히려 손해). 투매반등(meanrev)의 짝 — 불장 강함,
  레짐 비상관이라 병렬 시 분산. BTC 게이트가 베어장 가짜돌파 차단(edge·MDD 핵심).
tags:
- live-scanner
- trend-following
- donchian-breakout
- btc-regime
- channel-exit
- swing
- long-only
- candidate
---

# Live Donchian Breakout + BTC Regime Gate — 돌파 추세추종 롱 (4h 스윙)

> 리서치 종결: `docs/work/active/swing-strategy-research-handoff.draft.md` (재개 #3~#5).
> 짝 전략(평균회귀): [[live-capitulation-bounce]]. 메모리: `project_capitulation_bounce_edge_pass`.

## 동기

스윙 합성 전략의 추세추종 다리. 투매반등(평균회귀)이 베어·공포 국면에서 벌고, 돌파는
불장 추세에서 번다 — 둘은 레짐 비상관이라 병렬 운용 시 분산효과로 합성 MDD 가 낮아진다
(cap+breakout lev1: CAGR 24% / MDD 28% / Sharpe 1.0; cap 단독 9%/13%, breakout 단독 27%/25%).

검증된 일봉 터틀([[project_turtle_daily_candidate]])의 4h 변형. **1h 돌파는 실패**(타임프레임)
했으나 4h 는 random-vs-signal + 정직비용 통과. **BTC 레짐 게이트**가 핵심 — 베어장 가짜돌파를
차단해 per-trade edge(+0.26→+0.56%) 와 MDD(39→25%) 를 동시에 개선.

## 진입 규칙 (per-symbol, 4h 종가)

```
close[-1] > max(high[-21:-1]) * 1.001      # Donchian20 상단 0.1% 명확 돌파
AND close[-1] > EMA200                       # 자기 상승추세
AND BTC 4h close >= BTC EMA200                # BTC 레짐 게이트 (btc_regime_gate=True)
```

BTC ohlcv 는 `market_snapshot["universe_ohlcv"]["BTCUSDT"]` 로 공급(`live_macross_regime_v1` 동일 패턴).
BTC 데이터 부재 → 보수적 hold.

## 청산 (2단)

1. **하드 손절** = `entry − 2×ATR(14)` → 진입 시 `stop_loss_pct_override` 로 전달
   (`live_breakout_with_atr_stop` ATR override 패턴). 정적 `stop_loss_pct=0.08` 은 fallback.
2. **추세 청산 (엣지 핵심)** = `close < Donchian10 하단` → `channel_exit_level(history)` 가
   매 봉 그 레벨(`min(low[-11:-1])`) 반환. 가격 임계가 아니라 매 봉 갱신되는 채널이라
   `LivePositionRiskManager` 의 정적 stop/TP/trailing 으로 표현 불가.

time-stop **면제** (`max_hold_sec=None`) — 추세는 수일~수주 길게. #5 검증: max_hold timeout 은
오히려 손해(승자 조기절단). TP 는 추세청산이 주청산이라 넓게(0.50).

### ⚠️ 채널청산 라이브 배선 (활성화 게이트)

`channel_exit_level()` 은 구현·단위테스트됐으나, 이를 라이브에서 소비하는 **ratchet 배선**이
아직 없다. 라이브 아키텍처는 진입(전략)/청산(risk manager) 을 엄격 분리하고 live-scanner sell 은
숏-진입으로 해석되어(보유 중 차단) 전략이 청산 sell 을 못 낸다. 설계(핸드오프 "남은 작업"):

- **권장**: `LivePositionRiskManager` 에 additive `sweep_channel_exits(now, history_lookup)` —
  채널청산 등록 전략의 보유분을 순회, `history_lookup(symbol)` 으로 봉 받아 `channel_exit_level`
  계산, `close < level` 이면 reduce_only sell emit. 기존 `evaluate`/`sweep_timeouts` race-path
  무변경(additive). 루프가 주기 호출(이미 universe OHLCV 보유). **testnet 검증 후 활성화.**

배선 전까지 라이브는 ① 2ATR 손절만 → 백테스트(채널청산) 와 다름(trailing 근사 PF 1.29 vs 채널 1.37).
그래서 production.yaml **commented candidate** 유지.

## 종목 유니버스 / 봉

- `get_interval()="4h"`, `get_universe()`=기본 `BINANCE_USDT_TOP30`(유동성 동적 top-N).
- **Universe pin-date: 2026-05-19** (majors13). 생존편향 disclosure — top30(최근상장 포함) 재검증서
  더 강해 cherry-pick 아님 확인.

## 5y 검증 결과 (정직 10bp, random-vs-signal, 채널청산판)

| 지표 | 값 | 비고 |
|---|---|---|
| 거래당 기대값 | +0.95% | 랜덤 롱보유 +0.385% 상회 (edge +0.56%) |
| Profit Factor | 1.37 | BTC 게이트 효과 (게이트 없으면 1.24) |
| 승률 | 31% | 추세추종 저승률·고손익비 |
| 거래수(5y·13코인) | 1406 | |
| 단독 basket(risk1%/top_n8/lev1) | CAGR 26.5% / MDD 24.7% / Sharpe 1.06 | 채널청산판 |

검증: `scripts/_swing_composite.py`, `scripts/_swing_final.py`, `scripts/_swing_live_semantics.py` (미커밋).

## 리스크 연동 (#70 mandatory)

```python
orch.register_strategy("live-donchian-breakout-btcgate", strategy)
orch.register_strategy_returns("live-donchian-breakout-btcgate", daily_return_series)
orch.refresh_portfolio_risk()
```

## 운영 규칙 / 활성화 게이트

- status=candidate, production.yaml **commented**. 활성화 선결: ① **채널청산 ratchet 배선 + testnet 검증**
  (위), ② 투매반등과 **병렬 2-전략** 사이징 통합(앙상블 wrapper 금지 — 분산파괴 REJECTED 교훈).
- CLAUDE.md 5y 게이트(PF>1·expectancy>0) 충족(채널청산판). 배선 전 라이브는 미검증 → 비활성 필수.

## 활성화 선결 — 4h 피드 부재 (2026-06-30 발견)

⚠️ **라이브 파이프라인은 `get_interval()` 을 소비하지 않는다** (loop/snapshot_builder/_async_orchestrator
어디서도 안 읽음 — 전략 파일·대시보드 sim 에서만 참조). 전략이 받는 `market_snapshot["history"]`
= `_universe_ohlcv[symbol]` = SnapshotBuilder `_universe_cache` = **universe_quote_provider 의 단일 인터벌**.
`scripts/live_run.py::_build_universe_quote_provider` 가 `fetch_universe_klines(..., interval="1d")` 로
**일봉 하드코딩** (binance/bitget 양쪽, cs-tsmom 일봉용).

→ **스윙 4h 전략을 현 orchestrator 에 등록하면 일봉을 받아** Donchian20=20일·EMA200=200일·
채널청산 Donchian10=10일 로 **silent 오작동**(4h 백테스트와 완전 불일치). 진입·청산 둘 다 깨짐.

**활성화 선결(채널청산 배선보다 먼저)**:
1. **4h 유니버스 피드** — 스윙용 `fetch_universe_klines(universe, interval="4h")`. 라이브가 orchestrator
   당 단일 인터벌이라 (a) 스윙 전용 별도 orchestrator/process(interval=4h), 또는 (b) per-strategy
   interval 피드(get_interval 소비 배선) 중 택1. **(a) 가 격리·저위험.**
2. 그 위에 채널청산 sweep 배선 — `history_lookup` 은 그 4h 피드 또는 sweep 전용 4h fetch. env-guard
   default-off + sweep_timeouts 회귀박제.
3. testnet 검증 → 활성화.

**유니버스/사이징 확정(2026-06-30, `swing-strategy-research-handoff.draft.md`)**: 돌파=동적 top-N broad
(EMA200+BTC게이트 생존편향 면역) / 투매반등=유동성 메이저 고정(falling knife 회피). 공통 제외
토큰화주식·스테이블·레버토큰·신규<60일·저유동. 전략별 사이징 버킷 분리(공유 basket MDD↑). A: 둘 다
cost wall 통과(net 10bp 투매+1.95%/돌파+1.06%). D: 페어 CAGR30%/MDD46%(→majors+버킷분리로 제어).
