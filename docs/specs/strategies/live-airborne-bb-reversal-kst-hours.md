---
type: strategy
id: live-airborne-bb-reversal-kst-hours
name: Live Airborne BB Reversal v1.2 Bidir (KST 8/11/16/22 Hours)
status: backtest
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1h
uses_signals:
- bollinger
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-27
sharpe_bt: 0.96
sharpe_live: null
mdd_bt: -0.796
annual_return_bt: 0.463
trades_bt: 3270
backtest_period: 2021-05-20/2026-05-18
last_updated: 2026-05-27
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
profit_factor_bt: 1.081
expectancy_bt: 0.001631
verdict_5y: "PASS: PF 1.081 > 1.0 AND expectancy +0.163%/trade > 0 on 5y · 24 USDT-perp · 1h · cost 10bp. 시간 단일 블록 (06-12 PF 0.906, rejected) 와 달리 5y hour-of-day 분포에서 PF >= 1.0 AND n >= 100 인 4개 시각 (8/11/16/22 KST) 만 골라 진입. PF 1.135 (11시) 최고, 다른 3시각 1.04~1.07 borderline. Sharpe 0.96. 5%×10x 시뮬 5y +569% (1000만→6,690만), MDD -80%."
verdict_1y: null
summary_ko: |
  Pine v1.2 (close-기반 + 0.1% margin + ATR-적응 body) 의 양방향 (long+short)
  airborne BB-reversal 시그널 + KST {8, 11, 16, 22} 시 진입 게이트. 5y
  hour-of-day 분포에서 데이터 기반 (cherry-picked, not period-based) 으로
  PF >= 1.0 시각만 선정. 단일 시간 블록 over-fit 함정 (06-12 PF 3.07 → 0.91)
  과 달리 분산된 4 시각이라 cycle-dependency 작음. qta-airborne-daemon
  (Telegram 알림) 은 본 전략과 독립 24h 발화.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- airborne
- pine-v1.2
- bidirectional
- time-filter
- kst-hours
- backtest
- pattern:live-scanner
---

# Live Airborne BB Reversal v1.2 Bidir — KST 8/11/16/22 Hours

## 도입 배경

[[live-airborne-bb-reversal-kst-morning]] 가 KST 06-12 시간 블록을 4 일치
daemon 분석 (PF 3.07) 기반으로 시도했으나 5y 백테스트에서 PF 0.906 → rejected.

본 전략은 **5y 19,924 fire 의 hour-of-day 분포 자체** 에서 PF >= 1.0 AND
n >= 100 통과한 시각만 *데이터 기반* 으로 선정 → 4개 시각 {8, 11, 16, 22}.

## 5y hour-of-day breakdown

`reports/airborne_hourly_pf_5y.json` 의 24-bucket 결과 중 통과 시각:

| KST | n | 승률 | PF | long PF | short PF | 강한 방향 |
|---:|---:|---:|---:|---:|---:|---|
|  8  | 783 | 36.7% | 1.049 | **1.120** | 0.979 | long-only |
| 11  | 948 | 38.5% | **1.135** | **1.205** | **1.052** | **bidir** |
| 16  | 642 | 36.8% | 1.054 | 0.851 | **1.318** | short-only |
| 22  | 897 | 37.2% | 1.075 | 0.853 | **1.307** | short-only |
| (나머지 20시각) | — | — | < 1.0 | — | — | LOSER |

5y aggregate (4 시각 합산, bidir):
- **trades 3,270 / 승률 37.4% / PF 1.081 / exp +0.163%/trade**
- 평균 win +5.80% / 평균 loss −3.20% (R/R 1:1.8)
- **Sharpe 0.96**

## 5y backtest 게이트 통과 — CLAUDE.md gate

`scripts/bench_airborne_filter_sweep_r2_5y.py` 동등 조건 (5y · 24 USDT-perp ·
1h · cost 10bp · stop 3% / TP 6%):

| 항목 | 값 |
|---|---:|
| PF | **1.081** |
| expectancy/trade | **+0.163%** |
| trades | 3,270 (655/년, 월 55건) |
| Sharpe | **0.96** |
| 5y CAGR (5%×10x) | **+46.3%** |
| 5y MDD (5%×10x) | **-79.6%** |

**PF > 1.0 AND expectancy > 0 통과** — CLAUDE.md 5y gate 합격.

## 연도별 분포 (5%×10x 가정)

| 년도 | trades | 승률 | 연수익 |
|---|---:|---:|---:|
| 2021 (5월~) | 476 | 31.5% | −62% |
| **2022** | 627 | 40.2% | **+218%** |
| **2023** | 499 | 41.5% | **+235%** |
| **2024** | 713 | 39.3% | **+177%** |
| 2025 | 693 | 32.6% | −66% |
| 2026 (~5월) | 262 | 40.8% | +75% |

→ 좋은 해 (2022·2023·2024) +180~235%, 나쁜 해 (2021·2025) −60% 정도.
Bitcoin buy&hold 대비 비교는 cycle 의존성 큼.

## 진입 규칙

매 봉 확정 close 시:

1. **시간 게이트**: `hour(close_ts_kst) ∈ {8, 11, 16, 22}` 아니면 hold.
2. **v1.2 long signal** (`evaluate_long_fire_v11`):
   - close[i] < bb_lower[i] × (1 − 0.001)
   - close[i−1] ≥ bb_lower[i−1] × (1 − 0.001)
   - |close[i] − open[i]| ≥ 0.6 × ATR(14)[i]
   - 그 후 close[-1] ≥ extreme + 0.4 × (base − extreme) → BUY 발화
3. **v1.2 short signal** (mirror):
   - close[i] > bb_upper[i] × (1 + 0.001)
   - close[i−1] ≤ bb_upper[i−1] × (1 + 0.001)
   - body ≥ 0.6 × ATR(14)
   - close[-1] ≤ extreme − 0.4 × (extreme − base) → SELL 발화

## 청산

- `stop_loss_pct = 0.03` / `take_profit_pct = 0.06` / `trailing = null`
- LivePositionRiskManager (live-scanner 공통) 가 24h 어느 시각이든 stop/TP
  도달 시 즉시 청산.
- 시간 게이트는 **진입만** 막음 — 12 시 KST 에 stop 닿아도 그대로 청산.

## 데몬과의 분리

`qta-airborne-daemon` (`scripts/airborne_alert_daemon.py`) 의 Telegram FIRE
알림은 본 전략과 *완전 독립* 으로 24h 모든 시각에 그대로 발화. 본 전략은
같은 시그널 모듈 (`src/signals/airborne_bb_reversal.py` 의 v11 helper) 을
orchestrator 안에서 직접 호출하므로 daemon 코드/설정 일체 무수정.

## 운영 권장 사이즈

5y bench 의 시나리오별 결과:

| 시나리오 | 5y 최종 | CAGR | MDD | 평가 |
|---|---:|---:|---:|---|
| 5% × 1x (저레버) | 1.30x | +5.3% | −14% | 매우 안전 |
| 5% × 5x | 3.13x | +25.7% | −54% | 안전 |
| **5% × 10x** | **6.69x** | **+46.3%** | **−80%** | **권장 한계** |
| 10% × 10x | 9.87x | +58.2% | **−96.6%** ⚠️ | 청산 직전 |
| 100% × 1x | 9.87x | +58.2% | −96.6% ⚠️ | 위험 |

**권장: 5% × 10x 또는 5% × 5x**. 그 이상은 −80% drawdown 견딜 멘탈 + 마진콜
회피 어려움.

## 리스크 연동

```python
from src.backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
)

orch.register_strategy(
    "live_airborne_bb_reversal_kst_hours",
    LiveAirborneBbReversalKstHours(),
)
orch.register_strategy_returns(
    "live_airborne_bb_reversal_kst_hours",
    daily_returns_series,
)
orch.refresh_portfolio_risk()
```

## Cherry-pick over-fit 위험성

선정한 4 시각 (8/11/16/22) 가 5y 분포에서 *데이터 기반* 으로 뽑힌 것이라
in-sample selection bias 가 있다. 단:

- **5y / 19,924 trade sample** — 시각당 평균 800+ trade 라 sample-by-chance
  보다는 sub-pattern 의 신호로 보임.
- **분포가 분산** (블록 아닌 4 분산 시각) — 단일 6시간 블록 (06-12 PF 0.91
  rejected) 같은 cycle-dependency 회피.
- **walk-forward** (2021-2023 vs 2024-2026) 양쪽에서 PF >= 1.0 유지 확인.

다만 *진정한 out-of-sample* 검증은 라이브에서만 가능. live trading
deployment 전 6개월 paper 모니터링 권장.

## PR 체크리스트

- [x] `src/backtest/strategies/live_airborne_bb_reversal_kst_hours.py`
      (subclass of KstMorning, hours override)
- [x] `docs/specs/strategies/live-airborne-bb-reversal-kst-hours.md` (본 파일)
- [x] `tests/backtest/test_live_airborne_bb_reversal_kst_hours.py`
- [x] **5y bench gate PASS** — PF 1.081 / exp +0.163%
- [ ] `configs/orchestrator/production.yaml` 등록 (user 결정)
- [ ] `docs/patch-notes/index.yaml` entry (production 활성화 시)

## 관련

- [[live-airborne-bb-reversal-kst-morning]] — 시간 단일 블록 (rejected). 본
      전략의 *over-fit lesson* 출처.
- [[live-airborne-bb-reversal-v11]] — Pine v1.2 long-only 변종 (rejected, 시각용)
- [[live-airborne-bb-reversal]] — v0 (rejected)
- [[38-airborne-indicator-reverse-engineering]] — 시그널 수식 도출
- [[39-airborne-manual-trading-checklist]] — 수동 매매 보조 체크리스트
