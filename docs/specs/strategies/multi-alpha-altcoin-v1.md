---
type: strategy
id: multi-alpha-altcoin-v1
name: Multi-Alpha Altcoin v1 (Lead-lag + Regime + Cointegration + Outlier)
status: rejected
paradigm: universe-scan
instruments:
- binance-usdt-perp-cointegrated
market: crypto
timeframe: 1h
uses_signals:
- btc-altcoin-cointegration
- btc-altcoin-rolling-corr
- btc-lead-lag-return
- btc-altcoin-zscore
risk_rules:
- max-drawdown-5pct
- cointegration-pvalue-gate
owner: siwoo
created: 2026-06-05
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: 788
backtest_period: "2021-06-05/2026-06-05"
last_updated: 2026-06-05
stop_loss_pct: 0.0075
take_profit_pct: 0.015
trailing_stop_pct: null
profit_factor_bt: 0.747
expectancy_bt: -0.001334
verdict_5y: "REJECTED: PF 0.747 / expectancy -0.133%/trade on 5y · 10 USDT-perp major · 1h · cost 10bp · 788 trades. CLAUDE.md 게이트 (PF>1 AND exp>0) 통과 못 함. 4 layer 중 lead_lag layer 만 trade 발생 — outlier (디커플링) layer 는 0 trade (메이저 alt 의 5y 평균 BTC corr 가 항상 ≥0.3 이라 디커플링 체제 진입 X). statsmodels Python 3.14 미지원이라 cointegration 검증 (Layer 1) 은 더미 pvalue=0.04 로 모두 pass 처리됨 — 학술 정당성 약함. 후속: (a) 소형 alt 추가 또는 (b) statsmodels 환경에서 cointegration 실 검증 + (c) Layer 4 의 regime threshold (corr ≤ 0.3) 완화 검토. production.yaml 미등록."
summary_ko: |
  BTC-알트 4 layer 결합 전략. (1) 60일 코인티그레이션 검증 통과 알트만
  universe (statistical arbitrage 안전성). (2) 30일 rolling correlation 으로
  체제 분류 — 동조(≥0.7)면 lead-lag 추종, 디커플링(≤0.3)이면 z-score 평균
  회귀, 모호 구간(0.3~0.7)은 진입 X. (3) 동조 체제: BTC 직전 1h return
  |r|>1% AND 거래량 spike 면 같은 방향 alt 매수. (4) 디커플링 체제: BTC 대비
  alt spread z-score ±2σ 이탈 시 역방향 진입.
tags:
- pattern:universe-scan
- multi-layer
- lead-lag
- regime-switching
- cointegration
- mean-reversion
- crypto
- binance
---

# Multi-Alpha Altcoin v1

학술 근거 4건 (lead-lag · regime · cointegration · outlier mean reversion) 을 한 전략에 결합. 단일 컨셉의 약점을 다른 컨셉으로 메우는 방어적 설계.

## 4 Layer 결합 흐름

```
매 1h 봉 close 마다 universe 의 각 alt 에 대해:

Layer 1 — Cointegration Filter (universe gate)
  └ BTC ↔ alt 의 60일 rolling Engle-Granger test
  └ p-value < 0.05 인 알트만 trade-eligible
  └ pass 못 하면 본 알트 전 layer skip

Layer 2 — Regime Classification (체제 분류)
  └ 30일 BTC-alt 1h return Pearson corr
  ├─ corr ≥ 0.7   → 동조 체제 → Layer 3 활성
  ├─ corr ≤ 0.3   → 디커플링 체제 → Layer 4 활성
  └─ 그 외 (0.3 < corr < 0.7) → 모호 체제 → 진입 X

Layer 3 — Lead-lag Entry (동조 체제)
  ├ BTC 직전 1h close return |r_btc| > 0.01 (= 1%)
  ├ AND BTC 직전 1h 거래량 > 24h avg × 1.5
  ├ AND alt 직전 1h return 같은 방향 < 0.005 (= 0.5%, 시차 아직 안 끝남)
  └ 충족 시 BTC 와 같은 방향으로 alt 매수
     - r_btc > 0 → alt long
     - r_btc < 0 → alt short

Layer 4 — Outlier Mean Reversion (디커플링 체제)
  ├ 60일 rolling BTC-alt spread = log(alt) - β × log(btc), β = OLS
  ├ z_spread = (spread[-1] - mean) / std
  ├ z > +2.0 → alt 과매수 → short 진입
  └ z < -2.0 → alt 과매도 → long 진입
```

## 청산 룰

R/R 1:2 유지, dashboard `/airborne` 시뮬과 같은 짧은 호흡:

- `stop_loss_pct = 0.0075` (-0.75% price)
- `take_profit_pct = 0.015` (+1.5% price)
- timeout: 4h (= 1h 봉 4개) — `LiveScannerMixin.timeout_bars` 또는 자체 hold counter
- cooldown: stop 후 30분 같은 (sid, symbol) 재진입 차단

## Universe

- BTC-cointegrated alt top 20 (24h 거래량 기준 weighted)
- 매일 KST 00:00 universe rebal (cointegration test 비용 분산)
- pin-date: 2026-06-05 (live-scanner 패러다임 한해 dynamic refresh)

## Risk 연동

- `stop_loss_pct` / `take_profit_pct` 는 `LivePositionRiskManager` 가 stop/TP 라인 등록
- bidir 전략이라 `shorts_allowed: ClassVar[bool] = True` 선언 (PR #342)
- `LiveAirborneBbReversalKstMorning` 자식 아님 — 별도 `LiveScannerMixin` 직접 상속
- max concurrent positions: 5 (`production.yaml` 의 운영 한도)

## 자본 분배 (capital-allocation-v1 호환)

- `default_size: 0.04` (4% per trade, capital-allocation-v1 표준)
- 동시 보유 max 5 → 최대 노출 20%
- 다른 활성 전략 (cs-tsmom 40% + airborne kst-hours 20% + short-whitelist 20%) 과 합산 100% 도달 → **본 전략 활성화는 cs-tsmom 비중 조정 필요** (예: 0.40 → 0.20)

## 5y backtest gate

CLAUDE.md "PF·기대값 우선" 규칙:
- **PF > 1.0 AND expectancy > 0** (5y, 24 USDT-perp top, 비용 10bp, 1h)
- 통과 → `production.yaml` 등록 검토
- 미통과 → `status: rejected`, 코드는 보존 (rule sweep / Layer 분리 재설계 검토 자료)

**예상 caveat (학술 리서치 기반)**:
- Lead-lag 시차가 2024-2025 사이 60일 → 20일 미만으로 압축 (ETF 자금 흐름 변화)
- Cointegration 관계 불안정 (DIVA portal 페이퍼)
- 5y PF<1 가능성 인지하고 진행

## 학술 근거

- **Lead-lag** — Guo, Sang, Tu, Wang. "Cross-Cryptocurrency Return Predictability" (SSRN 3974583)
- **Regime switching** — arXiv 2112.15321, Springer Digital Finance (2025)
- **Cointegration / pairs** — arXiv 2109.10662 ("Dynamic Cointegration-Based Pairs Trading")
- **Outlier mean reversion** — Quantified Strategies (BTC mean reversion vs momentum, low volume regimes)

## PR 체크리스트

- [ ] spec md 작성 (본 문서)
- [ ] signal 모듈 (`src/signals/btc_altcoin_lead_lag.py` 등 4건 또는 통합)
- [ ] strategy 모듈 (`src/backtest/strategies/multi_alpha_altcoin_v1.py`)
- [ ] 단위 테스트 (synthetic OHLCV 로 4 layer 각 path)
- [ ] 5y backtest 스크립트 (`scripts/bench_multi_alpha_altcoin_v1_5y.py`)
- [ ] 5y bench 결과 (`reports/eval_multi_alpha_altcoin_v1_5y.json`)
- [ ] spec frontmatter PF/exp/verdict_5y 갱신
- [ ] (PF>1 시) production.yaml 등록 + capital_allocation 조정
- [ ] patch-note v0.6.32
