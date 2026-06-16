---
type: strategy
id: live-airborne-bb-reversal-kst-hours
name: Live Airborne BB Reversal v1.2 Bidir (KST {1,2,3,6,7,8,23} Hours, v3)
status: active
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
last_updated: 2026-06-16
stop_loss_pct: 0.010
take_profit_pct: 0.020
trailing_stop_pct: null
profit_factor_bt: 0.545
expectancy_bt: -0.0022
verdict_5y: "REJECTED (옛 verdict, 옛 룰 기준). 2026-06-06 — KST gate v3 {1,2,3,6,7,8,23} 도입. 13일 1분봉 실측(logs/airborne_fires/sim_cache_1m.jsonl) hour-of-day 분석에서 새벽~아침 {1,2,3,6,7,8} 이 순손익/PF 최상위(net +68%, PF 2.39), 옛 v2 의 16시(PF 0.15)·22시(PF 0.61)가 손실 누적. 23시 추가(2026-06-06): 숏 PF 2.09, BTC trend filter 로 롱 차단해 숏만 잔존. ⚠️ CAVEAT: 13일 in-sample 선정 — 5y bench 미검증이며 5y hourly 분석은 다른 시각({8,11,16,22})을 선호. hour-of-day 알파가 윈도우마다 불안정 → 과적합 위험. 운영자 직접 판단으로 적용, 5y walk-forward 검증 전까지 모니터링 필요. BTC trend filter 병행 운영."
verdict_1y: null
summary_ko: |
  Pine v1.2 (close-기반 + 0.1% margin + ATR-적응 body) 의 양방향 (long+short)
  airborne BB-reversal 시그널 + KST {1,2,3,6,7,8,23} 시 진입 게이트 (v3, 13일 1m 기반).
  새벽~아침+23시 7시각 선정 — 13일 실측에서 PF 2.39 / net +68%. ⚠️ 5y 미검증,
  과적합 위험. BTC trend filter 병행 (하락추세 LONG 차단). qta-airborne-daemon
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
- active
- pattern:live-scanner
---

# Live Airborne BB Reversal v1.2 Bidir — KST {1,2,3,6,7,8,23} Hours (v3)

## 도입 배경

[[live-airborne-bb-reversal-kst-morning]] 가 KST 06-12 시간 블록을 4 일치
daemon 분석 (PF 3.07) 기반으로 시도했으나 5y 백테스트에서 PF 0.906 → rejected.

v2 는 5y 분석 + 30d sim_cache 기반으로 {7,8,16,20,22} 로 설정.
v3 (2026-06-06) 는 13일 1분봉 실측 분석에서 새벽~아침 {1,2,3,6,7,8} 이
순손익/PF 최상위 (net +68%, PF 2.39) 로 재설계.

## v3 게이트 선정 근거 (13일 1m 실측)

`logs/airborne_fires/sim_cache_1m.jsonl` 의 13일 1분봉 hour-of-day 분석:

| KST | PF | 평가 |
|---:|---:|---|
| 1   | (최상위 군) | ✅ v3 포함 |
| 2   | (최상위 군) | ✅ v3 포함 |
| 3   | (최상위 군) | ✅ v3 포함 |
| 6   | (최상위 군) | ✅ v3 포함 |
| 7   | (최상위 군) | ✅ v3 포함 |
| 8   | (최상위 군) | ✅ v3 포함 |
| 23  | 숏 PF 2.09 | ✅ v3 포함 (숏 PF 2.09, BTC trend filter 로 롱 차단해 숏만 잔존) |
| 16  | 0.15 | ❌ v3 제외 (손실) |
| 22  | 0.61 | ❌ v3 제외 (손실) |

13일 합산 (7 시각): **net +68%, PF 2.39**

⚠️ **CAVEAT**: 13일 in-sample 선정 — 5y bench 미검증. 5y hourly 분석은 다른
시각 ({8,11,16,22}) 을 선호. hour-of-day 알파가 윈도우마다 불안정 → 과적합 위험.
운영자 직접 판단으로 적용, 5y walk-forward 검증 전까지 모니터링 필요.

## 5y hour-of-day breakdown (참고 — v2 기준, v3 미검증)

`reports/airborne_hourly_pf_5y.json` 의 24-bucket 결과 중 v2 통과 시각:

| KST | n | 승률 | PF | long PF | short PF | 강한 방향 |
|---:|---:|---:|---:|---:|---:|---|
|  8  | 783 | 36.7% | 1.049 | **1.120** | 0.979 | long-only |
| 11  | 948 | 38.5% | **1.135** | **1.205** | **1.052** | **bidir** |
| 16  | 642 | 36.8% | 1.054 | 0.851 | **1.318** | short-only |
| 22  | 897 | 37.2% | 1.075 | 0.853 | **1.307** | short-only |
| (나머지 20시각) | — | — | < 1.0 | — | — | LOSER |

v3 에서 선정한 {1,2,3,6,7,8} 의 5y 검증은 미실시. 5y 데이터는 다른 시각을 선호함.

## 진입 규칙

매 봉 확정 close 시:

1. **시간 게이트**: `hour(close_ts_kst) ∈ {1,2,3,6,7,8,23}` (v3) 아니면 hold.
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

- `stop_loss_pct = 0.010` / `take_profit_pct = 0.020` / `trailing = null` (R/R 1:2, **2026-06-16 widening**: 좁은 +1.1%/-0.5% 가 노이즈 손절·수수료 비중 과다(6/16 실거래 fee -4.35 > gross +2.21)를 유발 → +2%/-1% 로 확대. 실측(sim_cache 2068건)상 realistic 비용에서 좁힘 대비 우위. ⚠️ gross PF 는 좁힘이 높음 — 넓힘 이득은 수수료 희석. 손실 1건 2배(-10% ROE@10x). 5y 미검증.)
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

## 과적합 위험성 (v3 강화 경고)

v3 에서 선정한 7 시각 {1,2,3,6,7,8,23} 은 **13일 in-sample** 선정이라 과적합
위험이 v2 보다 훨씬 높다.

- **13일 / 소수 sample** — 통계적 신뢰가 낮음. 5y / 19,924 trade 기반 v2 와
  달리 v3 는 단기 시장 노이즈에 반응한 것일 수 있음.
- **5y hourly 분석과 불일치** — 5y 데이터는 {8,11,16,22} 를 선호. v3 의 새벽
  시각 {1,2,3} 은 5y 분석에서 PF < 1.0 일 가능성.
- **운영자 직접 판단** — 5y walk-forward 검증 전까지 결과를 면밀히 모니터링.
  성과 악화 시 v2 또는 5y 기반 게이트로 즉시 복구 권장.

*진정한 out-of-sample* 검증은 라이브에서만 가능. 모니터링 주기: 1주 단위.

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
