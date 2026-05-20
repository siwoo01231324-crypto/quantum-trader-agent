---
type: strategy
id: live-oversold-with-divergence
name: Live RSI Bullish Divergence in Downtrend
status: rejected
paradigm: live-scanner
instruments:
- KRX_UNIVERSE
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1m
uses_signals:
- rsi
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-11
sharpe_bt: 3.216
sharpe_live: null
mdd_bt: -0.2675
annual_return_bt: 2.7183
trades_bt: 44589
backtest_period: 2021-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
summary_ko: |
  장중 실시간 검색식. 가격은 21봉 전 대비 하락 (downtrend) + RSI 다이버전스
  (가격은 신저점인데 RSI 는 더 안 떨어짐) 발생 시 매수. momo_kis_v1 의
  단일종목 룰을 universe-wide 로 확장한 변형. 청산은 손절 -3% / 익절 +6%.
tags:
- live-scanner
- rsi
- divergence
- intraday
- mean-reversion
profit_factor_bt: 0.9108
expectancy_bt: -0.002032
verdict_5y: "rejected: PF=0.911<1, expectancy=-0.203%/trade<0 (5y/30 syms/10bp)"
---

# Live RSI Bullish Divergence in Downtrend

장중 실시간 검색식 (#227 S4). 하락 추세 안에서 RSI 다이버전스 발견 시 변곡점 진입. `momo_kis_v1` 의 single-ticker (005930) 룰을 universe 380종으로 확장.

## 진입

- `close[-1] < close[-22]` — 21봉 downtrend 필터 (sideways 차단)
- AND `detect_divergence(close, rsi, 14)[-1] == 'bullish'` — 가격 신저점인데 RSI 더 안 떨어짐

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager` 책임:
- `stop_loss_pct = 0.03`, `take_profit_pct = 0.06`, `trailing_stop_pct = null`

## momo_kis_v1 와의 차이

| 구분 | `momo_kis_v1` (legacy) | 본 전략 (universe-wide) |
|---|---|---|
| 종목 | 005930 단일 | KRX 350 + Binance 30 |
| Bar boundary | KRX 15분봉 | 매 tick (1분봉 cache) |
| 청산 | bearish divergence 시 sell signal 자체 발행 | strategy 가 sell 안 함 — `LivePositionRiskManager` 자동 |
| 사이징 | half-Kelly per signal | 고정 5% (포트폴리오 limit 가 게이팅) |
| Downtrend 필터 | 없음 | `close[-1] < close[-22]` 추가 |

## 리스크 연동

```python
orch.register_strategy("live_oversold_with_divergence", LiveOversoldWithDivergence())
orch.register_strategy_returns("live_oversold_with_divergence", daily_returns_series)
```

## 백테스트

- 5y bench 미실시 — #227 S6 단계에서 검증 예정. KRX universe 결과를 `momo_kis_v1` 005930 단일종목 5y bench (있으면) 대비 알파 비교 권장

## 운영 규칙

- LLM 호출 금지 (불변식 #6) — RSI / divergence 모두 결정적 코드
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml` `enabled: true`
- 단위 테스트: `tests/backtest/test_live_oversold_with_divergence.py`

## 관련

- `docs/specs/live-universe-scanner-paradigm.draft.md` — 본 패러다임 spec
- `docs/specs/strategies/momo-kis-v1.md` (legacy single-ticker — universe 화 대상)
- 이슈 #227 (Live Universe Scanner — 진행 중)

## 5y 검증 결과 (2026-05-20)

**REJECTED.** 견고지표(Profit Factor·거래당 기대값) 기준 음의 엣지 확정.

| 지표 | 값 | 게이트 |
|---|---|---|
| Profit Factor | **0.911** | <1 ❌ |
| 기대값/거래 | **-0.203%** | <0 ❌ |
| 승률 | 34.2% | — |
| Payoff | 1.76x | — |
| 거래수 | 44,589 | — |

조건: 5y(2021-05~2026-05) · 30 USDT-perp 심볼 · 라운드트립 비용 10bp.

벤치 하네스의 Sharpe (3.22) 는 `bench_live_scanner._aggregate` 의 일별평균 + `final ** (252/n_days_with_trades)` 투영 집계 산물로, PF<1 과 부호가 모순되어 **신뢰 불가**. 결정 근거는 PF·기대값 (게임 불가능, 합산 기반).

사전등록 가설(naive 진입 + 고정 % 출구) **falsified**. 파라미터 튜닝(stop/TP/trailing %)으로 PF 1 못 넘김 — `scripts/sweep_breakout_atr.py` 의 1y(19조합) + 5y(3조합) sweep 이 이미 증명. 재활성화는 (a) 진입 신호 재설계 + (b) PF>1·exp>0 게이트 통과 후에만.

원자료: `reports/eval_live_scanners_5y.json`.
