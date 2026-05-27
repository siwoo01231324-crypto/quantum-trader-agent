---
type: strategy
id: live-airborne-bb-reversal-kst-morning
name: Live Airborne BB Reversal v1.2 Bidir (KST 06-12 Morning Window)
status: rejected
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
created: 2026-05-26
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: 7003
backtest_period: 2021-05-19/2026-05-19
last_updated: 2026-05-26
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
profit_factor_bt: 0.906
expectancy_bt: -0.002004
verdict_5y: "rejected: PF=0.906<1 best filtered combo (3%/6% R/R), expectancy=-0.200%/trade<0 on 5y · 24 USDT-perp · 1h · cost 10bp. KST 06-12 시간 필터가 trades 만 ~65% 줄였을 뿐 PF 는 baseline 0.898 -> filtered 0.906 (+0.008) 으로 사실상 0 lift. 8 R/R combos 모두 LOSER (PF 0.545~0.906). 4-day daemon 알림 분석 (PF 3.07) 은 over-fitting 으로 확정. reports/eval_live_airborne_kst_morning_5y.json"
verdict_1y: null
summary_ko: |
  Pine v1.2 (close-기반 + 0.1% margin + ATR-적응 body) 의 양방향 (long+short)
  airborne BB-reversal 시그널 + KST 06:00–11:59 진입 게이트. daemon 누적
  4일치 텔레그램 알림 시뮬에서 (1) 06-12 KST 블록이 PF 3.07 / +47.3% 로
  비대칭 알파, (2) SHORT 가 전체 net 의 80% (PF 2.16 vs LONG PF 1.13) 라는
  두 관찰을 모두 반영해 v1.2 bidir + 시간 필터 조합으로 검증한다.
  qta-airborne-daemon (Telegram 알림) 은 본 전략과 독립 24h 발화.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
- airborne
- pine-v1.2
- bidirectional
- time-filter
- kst-morning
- rejected
- pattern:live-scanner
---

# Live Airborne BB Reversal v1.2 Bidir — KST 06–12 Morning Window

## 도입 배경

[[live-airborne-bb-reversal-v11]] 의 Pine v1.2 (close-기반 + ATR-적응 body)
시그널을 양방향 (long+short) 으로 사용. 그 위에 **KST 06–11 시간 게이트** 를
얹는다. 두 가지 관찰이 결합:

### 1. KST 시간대별 비대칭 알파 (2026-05-23 ~ 2026-05-26, 339 FIRE)

| KST 구간 | n | 승률 | 합산 | PF |
|---|---:|---:|---:|---:|
| 00–06 새벽 | 114 | 33.3% | −9.18% | 0.74 |
| **06–12 오전** | **134** | **62.7%** | **+47.30%** | **3.07** |
| 12–18 오후 | 50 | 34.0% | −2.25% | 0.86 |
| 18–24 저녁 | 41 | 41.5% | +3.84% | 1.35 |

오전 한 블록이 net 알파 거의 전부.

### 2. 방향별 비대칭 알파

| 방향 | n | 승률 | 합산 | PF |
|---|---:|---:|---:|---:|
| LONG  | 212 | 41.5% | +7.68% | 1.13 |
| **SHORT** | 127 | **53.5%** | **+32.02%** | **2.16** |

SHORT 가 전체 net 알파의 ~80% — long-only ([[live-airborne-bb-reversal]] v0)
로는 거의 다 놓침. 그래서 v1.2 **bidir** 을 base 로 채택.

### 가설

"Pine v1.2 의 close-기반 ATR-적응 시그널이 평균적으로는 PF≈1 보더라인이지만
*시간대 × 방향* 차원에서 가용 알파가 비대칭이라면, 06–12 KST + bidir 조합으로
5y 평균에서도 PF > 1.0 + expectancy > 0 게이트를 통과할 수 있다."

## 진입 규칙

매 봉 확정 close 시:

1. **시간 게이트**: `hour(close_ts_kst) ∈ {6,7,8,9,10,11}` 아니면 hold.
2. **v1.2 long signal** ([[signals/airborne_bb_reversal#evaluate_long_fire_v11]]):
   - close[i] < bb_lower[i] × (1 − 0.001) — 0.1% margin
   - close[i−1] ≥ bb_lower[i−1] × (1 − 0.001) — 직전 봉 미돌파
   - |close[i] − open[i]| ≥ 0.6 × ATR(14)[i] — ATR-적응 body 게이트
   - 그 후 close[-1] ≥ extreme + 0.4 × (base − extreme) → BUY 발화
3. **v1.2 short signal** (mirror):
   - close[i] > bb_upper[i] × (1 + 0.001)
   - close[i−1] ≤ bb_upper[i−1] × (1 + 0.001)
   - body ≥ 0.6 × ATR(14)
   - close[-1] ≤ extreme − 0.4 × (extreme − base) → SELL 발화
4. **우선순위**: v1.2 state machine 상 한 setup 만 active. 동시 발화는 발생할
   수 없으나 안전 fallback 으로 long 우선.

## 청산

- `stop_loss_pct = 0.03` / `take_profit_pct = 0.06` / `trailing = null`
- LivePositionRiskManager (live-scanner 공통) 가 24h 어느 시각이든 stop/TP
  도달 시 즉시 청산.
- 시간 게이트는 **진입만** 막음 — 13 시 KST 에 stop 닿아도 그대로 청산.

## 데몬과의 분리 (사용자 명시 요청 2026-05-26)

`qta-airborne-daemon` (`scripts/airborne_alert_daemon.py`) 의 Telegram FIRE
알림은 본 전략과 *완전 독립* 으로 24h 모든 시각에 그대로 발화. 본 전략은
같은 시그널 모듈 (`src/signals/airborne_bb_reversal.py` 의 v11 helper) 을
orchestrator 안에서 직접 호출하므로 daemon 코드/설정 일체 무수정.

## 리스크 연동

```python
from src.backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
)

orch.register_strategy(
    "live_airborne_bb_reversal_kst_morning",
    LiveAirborneBbReversalKstMorning(),
)
orch.register_strategy_returns(
    "live_airborne_bb_reversal_kst_morning",
    daily_returns_series,
)
orch.refresh_portfolio_risk()
```

## 5y 검증 결과 (2026-05-26) — REJECTED

조건: 5y (2021-05-19 ~ 2026-05-19) · BINANCE USDT-perp top-30 중 24 sym (캐시) ·
1h · 라운드트립 cost=10bp · `scripts/bench_live_airborne_kst_morning_5y.py --months 60 --top-n 30 --sweep-rr`.

### 결과 표 (filtered = KST 06-12 게이트 적용, baseline = 24h)

| R/R | filt trades | filt PF | filt exp/trade | base PF | base exp/trade | verdict |
|---|---:|---:|---:|---:|---:|---|
| 3.0/6.0 (1:2) ← spec default | 7003 | **0.906** | **−0.200%** | 0.898 | −0.218% | LOSER |
| 2.0/6.0 (1:3) | 7579 | 0.899 | −0.166% | 0.874 | −0.208% | LOSER |
| 2.0/4.0 (1:2) | 8054 | 0.857 | −0.211% | 0.841 | −0.235% | LOSER |
| 1.5/3.0 (1:2) | 8522 | 0.829 | −0.193% | 0.808 | −0.219% | LOSER |
| 1.0/3.0 (1:3) | 8789 | 0.806 | −0.173% | 0.775 | −0.203% | LOSER |
| 1.0/2.0 (1:2) | 8925 | 0.743 | −0.206% | 0.712 | −0.234% | LOSER |
| 0.5/2.0 (1:4) | 9078 | 0.718 | −0.154% | 0.633 | −0.206% | LOSER |
| 0.5/1.0 (1:2) | 9142 | 0.545 | −0.216% | 0.496 | −0.246% | LOSER |

### 판정 근거

- **8 R/R combos 모두 PF<1**. 최고 PF=0.906 (3%/6%) — 게이트 미통과.
- **KST 필터의 lift 가 사실상 0**. baseline 3%/6% PF=0.898 → filtered PF=0.906
  (+0.008). trades 만 ~65% 줄였을 뿐 (19924 → 7003) 알파는 같음.
- **방향별 breakdown** (3%/6% filtered): long PF 0.94 / short PF 0.87 — 4-day
  daemon 분석의 SHORT PF 2.16 예측이 완전히 반증됨.
- **4-day daemon 의 KST 06-12 PF 3.07 은 over-fitting**. 5y 평균에서는 06-12
  구간이 다른 구간과 사실상 동일한 (음의) 엣지.

### 가족 base rate 일관성

| 전략 | 5y/1y PF | 게이트 |
|---|---|---|
| live-bb-lower-bounce | 0.922 (5y) | naive reclaim + volume MA |
| live-airborne-bb-reversal v0 | 0.912 (1y) | high/low + 40% retrace |
| live-airborne-bb-reversal v1.1 | <0.82 (1y, 모든 cost/R/R) | close + margin + 절대 body |
| **본 전략 (v1.2 bidir + KST)** | **0.906 (5y, filtered)** | **+ ATR body + KST 06-12** |

BB 평균회귀 가족 전체가 음의 엣지 — 시간 필터든 ATR body 든 모두 PF~0.9 천장.

### 재활성화 조건

이 spec 은 잠긴다. 재활성화는:

1. **시간 필터 + 다른 컨텍스트 게이트 조합** — 시간 단독은 부족함이 증명됨.
   예: 변동성 레짐, 펀딩비, 멀티 TF 추세 정렬과의 조합.
2. **5y 게이트 통과** — 본 bench 스크립트 동등 조건에서 PF > 1.0 AND exp > 0.
3. 후속 작업에서 spec 새로 작성 (본 spec 은 historical record).

### 원자료

`reports/eval_live_airborne_kst_morning_5y.json` — full sweep + 방향별 PF.

## PR 체크리스트

- [x] `src/backtest/strategies/live_airborne_bb_reversal_kst_morning.py`
      (v1.2 bidir + KST gate)
- [x] `docs/specs/strategies/live-airborne-bb-reversal-kst-morning.md` (본 파일)
- [x] `tests/backtest/test_live_airborne_bb_reversal_kst_morning.py` (21 passed)
- [x] **5y bench gate FAIL** — 결과 위에 기록, `status: rejected`.
- [x] `configs/orchestrator/production.yaml` — **미등록** (gate FAIL).
- [x] `docs/patch-notes/index.yaml` — **미추가** (rejected 전략은 불변식 #8
      적용 대상 아님 — 활성화 변경이 없으므로).

## production 등록 (영구 lock)

❌ `production.yaml` 미등록. 재활성화 조건 충족 전까지 lock.

## 코드 위치

| 파일 | 역할 |
|---|---|
| `src/backtest/strategies/live_airborne_bb_reversal_kst_morning.py` | Strategy (v1.2 bidir + KST 게이트) |
| `src/signals/airborne_bb_reversal.py` | v1.2 helper (`evaluate_long_fire_v11`, `evaluate_short_fire_v11`) — 이미 존재 |
| `tests/backtest/test_live_airborne_bb_reversal_kst_morning.py` | 단위 테스트 |
| `scripts/bench_live_airborne_kst_morning_5y.py` | 5y bench (KST filter vs no filter) |

## 관련

- [[live-airborne-bb-reversal-v11]] — Pine v1.2 long-only 변종 (rejected, 시각용)
- [[live-airborne-bb-reversal]] — v0 (rejected, high/low + long-only)
- [[38-airborne-indicator-reverse-engineering]] — 시그널 수식 도출 근거
- [[live-universe-scanner-paradigm]] — paradigm spec
- `src/backtest/strategies/_live_scanner_helpers.py` — `LiveScannerMixin`
