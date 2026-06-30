---
type: strategy
id: live-capitulation-bounce
name: Live Capitulation Bounce (투매반등 평균회귀 롱, 4h 스윙)
status: candidate
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 4h
uses_signals:
- ema-deviation
- hammer-candle
- volume-spike
risk_rules:
- per-symbol-dynamic-stop-wick-low
- per-symbol-take-profit-2R
owner: siwoo
created: 2026-06-25
sharpe_bt: 1.07
sharpe_live: null
mdd_bt: 0.129
annual_return_bt: 0.093
trades_bt: 237
backtest_period: 2021-05/2026-05
last_updated: 2026-06-25
stop_loss_pct: 0.05
take_profit_pct: 0.10
trailing_stop_pct: null
profit_factor_bt: 1.63
expectancy_bt: 0.0173
verdict_5y: "CANDIDATE (비활성). 5y·13메이저·정직10bp·random-vs-signal 게이트 통과 — 랜덤 롱보유 baseline(+0.385%/거래) 을 신호가 상회(exp +1.73%/거래, PF 1.63, win 39%). 라이브 청산 의미론(꼬리저점 손절 + 2R TP, no-timeout)에서 백테스트 timeout판(PF 1.37)보다 강함. 단독 basket(risk2%/top_n8/lev1): CAGR 9.3% / MDD 12.9% / Sharpe 1.07 — 거래 희소(47/yr)라 저CAGR 저MDD '안정 앵커'. 설계상 돌파(trend, BTC게이트) 와 병렬 운용 시 합성 CAGR 24% / MDD 28% (research handoff). 생존편향(폐지코인 부재) 잔존 — top30 robustness 로 일부 완화. production 활성화는 사이징·포트폴리오 통합 후."
verdict_1y: null
summary_ko: |
  4h 종가 기준 per-symbol 투매반등(capitulation bounce) 평균회귀 롱. 가격이 EMA20
  아래로 2.5×ATR 이상 투매(long 아랫꼬리 hammer) + 거래량 스파이크(2×MA20) + 반등
  양봉이면 buy. 청산은 LivePositionRiskManager 가 진입 시점 동적 거리로 — 손절은
  신호봉 꼬리저점, 익절은 2R(꼬리저점 손절거리의 2배). 스윙이라 time-stop 면제.
  인트라데이가 비용벽으로 죽은 뒤(스윙이 정답) 일봉 터틀에 이어 5y edge 게이트를
  통과한 두 번째 유효 신호. 평균회귀 — 공포·투매 국면(베어장) 에서 강함.
tags:
- live-scanner
- mean-reversion
- capitulation
- ema-deviation
- volume-spike
- swing
- long-only
- candidate
---

# Live Capitulation Bounce — 투매반등 평균회귀 롱 (4h 스윙)

> 리서치 종결 핸드오프: `docs/work/active/swing-strategy-research-handoff.draft.md` (재개 #1~#5).
> 메모리: `project_capitulation_bounce_edge_pass`, `project_intraday_cost_wall`.

## 동기

크립토 인트라데이 스캘프는 **비용 벽**(수수료×레버리지가 매 거래 고정손실)으로 죽는다 —
1m/5m/15m confluence 전수탐색에서 신호가 랜덤을 이겨도 거래당 움직임이 작아 정직비용을
못 넘겼다 ([[project_intraday_cost_wall]]). 결론은 **스윙**: 거래당 큰 움직임이라 고정수수료
비중이 작아 비용 벽을 정면 회피한다 (검증된 일봉 터틀 [[project_turtle_daily_candidate]] 과 동일 원리).

투매반등(capitulation bounce)은 그 스윙 신호 중 5y·정직비용·random-vs-signal 을 통과한
평균회귀 셋업이다. 차트 해부(긴 아랫꼬리 투매바닥 반등)에서 도출.

## 진입 규칙 (per-symbol, 4h 종가)

```
low[-1]  <= EMA20[-1] - 2.5 * ATR(14)          # 투매 깊이 (EMA20 아래 2.5×ATR 이탈)
AND  lower_wick >= 1.5 * body                   # 긴 아랫꼬리 (hammer)
AND  close[-1] > open[-1]                        # 반등 양봉
AND  volume[-1] > 2.0 * mean(volume[-21:-1])     # 거래량 스파이크
```

`lower_wick = min(close, open) - low`, `body = |close - open|`. 모두 충족 시 `buy`.

## 청산 (LivePositionRiskManager — 동적 override)

live-scanner 는 sell 을 직접 발행하지 않고, 진입 시점에 변동성 비례 **동적 거리**를
Signal override 로 전달한다 (`live_breakout_with_atr_stop` 의 ATR override 패턴 미러):

- `stop_loss_pct_override   = (entry - wick_low) / entry`  — 신호봉 꼬리저점까지
- `take_profit_pct_override = 2 * (entry - wick_low) / entry`  — 2R (RR=2)

긴 아랫꼬리 아래에 손절을 두면 반등 여유가 생겨 비대칭 R:R 이 산다(고정 ATR 손절 PF 1.09 →
꼬리저점 PF 1.30~1.63). 정적 `stop_loss_pct=0.05` / `take_profit_pct=0.10` 은 override
미전달 시 fallback. **time-stop 면제** (`max_hold_sec=None`) — 평균회귀는 반등까지 보유
(no-timeout 가 timeout 판보다 강함: PF 1.37 → 1.63).

## 종목 유니버스 / 봉

- `get_interval() = "4h"`, `get_universe()` = **깨끗한 크립토 top-100**
  (`SWING_CRYPTO_UNIVERSE[:100]`, src/portfolio/binance_universe.py). 데이터 anomaly
  guard(기본 ON)가 0/NaN 가격 진입 차단.
- **유니버스 확대 결정 (2026-06-30)**: 기존 `BINANCE_USDT_TOP30` 은 Binance 선물이
  토큰화주식(TSLA/NVDA)·상품(XAU)·forex(EUR)를 상장하면서 비-크립토가 섞임. 깨끗한
  크립토 메이저 재분석(scripts/_swing_clean_majors_reanalysis.py)에서 투매반등은
  **확대할수록 PF 유지/상승** (top-30→100: 5y PF 1.28→1.38, 2y 2.19→2.14, 1y 2.27→2.54)
  + 거래수 2.3배 → top-100 채택. (돌파는 반대로 top-30 집중 — 비대칭.)
- **생존편향**: 폐지코인 부재는 잔존하나, 상장연차 코호트(veteran ≥ newcomer)·시점별
  유동성 게이트 통과로 일부 완화. 확대가 신규 펌핑빨이 아님은 깨끗한 크립토에서 재확인.

## 5y 검증 결과 (정직 10bp, random-vs-signal)

| 지표 | 값 | 비고 |
|---|---|---|
| 거래당 기대값 | **+1.73%** | 라이브 의미론(no-timeout). 백테스트 timeout판 +0.88% |
| Profit Factor | **1.63** | 랜덤 롱보유 baseline +0.385%/거래 상회 (edge +1.35%) |
| 승률 | 39% | R:R 2:1 → breakeven 33% 초과 |
| 거래수(5y·13코인) | 237 | 희소(≈47/yr) — 저빈도 평균회귀 |
| 단독 basket(risk2%/top_n8/lev1) | CAGR 9.3% / MDD 12.9% / Sharpe 1.07 | 저MDD '안정 앵커' |

연도별(라이브 합성): 2021 +4.67% / 2022 −1.12% / 2023 +2.28% / 2024 +1.31% / 2025 +0.66% /
2026 −1.26%(부분). 평균회귀는 베어장에 강하고 추세장 돌파와 비상관 → 합성 시 MDD 완화.

검증 스크립트: `scripts/_capitulation_bounce_backtest.py`, `scripts/_capitulation_portfolio.py`,
`scripts/_swing_live_semantics.py` (미커밋, research 단계).

## 리스크 연동 (#70 mandatory)

```python
orch.register_strategy("live-capitulation-bounce", strategy)
orch.register_strategy_returns("live-capitulation-bounce", daily_return_series)
orch.refresh_portfolio_risk()
```

- `daily_return_series` = 본 전략 청산거래의 일별 실현수익률 (라이브 loop 또는 bench runner 산출).
- live-scanner 공통: 청산은 `LivePositionRiskManager`, 사이징은 orchestrator `resolve_size` +
  `risk.evaluate` 포트폴리오 집중도 한도.

## 운영 규칙 / 활성화 게이트

- status=candidate, production.yaml **commented** 등록 (활성화는 사이징·포트폴리오 통합 검증 후).
- CLAUDE.md 5y 게이트(PF>1 AND expectancy>0, 정직비용) **충족**. 남은 선결: ① 합성 포트폴리오
  사이징(돌파 BTC게이트와 병렬, risk1%/top_n8) 라이브 통합, ② 생존편향 정밀(PIT) 후속.
- 단독 운용 시 저CAGR(9%) — 설계 의도는 돌파(trend)와 **병렬 2-전략** 운용(앙상블 wrapper 금지
  — 분산 파괴, `src/backtest/strategies/.ai.md` live-scanner-ensemble REJECTED 교훈).

## 활성화 선결 — 4h 피드 collision (2026-06-30, 정정)

라이브 provider 는 **`get_interval()` 을 소비한다** — `scripts/live_run.py::_build_universe_quote_provider`
의 binance/bitget closure 가 `_collect_strategy_universes()` 로 등록 전략의 (get_universe, get_interval)
을 모아 **per-interval `fetch_universe_klines(syms, interval=...)`**. 즉 투매반등(4h) 등록 시 그 universe
는 **4h 로 fetch**된다. (loop/snapshot_builder/_async_orchestrator 는 get_interval 미소비 — provider 가 담당.)

⚠️ **단 cross-interval symbol collision**: merge 가 `for interval in sorted(...): ohlcv.setdefault(sym, df)`
= **first-wins(알파벳)**. "1d" < "1h" < "4h" 라 **스윙 종목이 cs(1d)/airborne(1h) universe 와 겹치면
그 종목만 1d/1h 봉을 받아** EMA20/ATR/투매깊이가 4h 아닌 값으로 silent 오작동. 코드 주석도 "Phase 3
symbol-major 분리 검토" 로 플래그. 투매반등은 채널청산 없음(고정 꼬리저점손절+2R TP)이라 sweep 무관.

**활성화 선결**:
1. **4h collision-safety** — 투매반등 universe(유동성 메이저 고정)를 1d/1h 전략과 disjoint 하게 하거나,
   provider merge 를 symbol+interval-aware 로 수정(전략별 자기 interval 보장).
2. testnet 검증 → 활성화. (채널청산 없어 sweep 배선 불요 — 고정 stop/TP override 로 라이브 fit 깨끗.)

**유니버스/사이징 확정(2026-06-30, `swing-strategy-research-handoff.draft.md`)**: 돌파=동적 top-N broad
(EMA200+BTC게이트 생존편향 면역) / 투매반등=유동성 메이저 고정(falling knife 회피). 공통 제외
토큰화주식·스테이블·레버토큰·신규<60일·저유동. 전략별 사이징 버킷 분리(공유 basket MDD↑). A: 둘 다
cost wall 통과(net 10bp 투매+1.95%/돌파+1.06%). D: 페어 CAGR30%/MDD46%(→majors+버킷분리로 제어).
