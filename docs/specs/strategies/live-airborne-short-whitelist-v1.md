---
type: strategy
id: live-airborne-short-whitelist-v1
name: Live Airborne SHORT-only Whitelist v1
paradigm: live-scanner
owner: siwoo
status: candidate
created: 2026-06-01
last_updated: 2026-06-02
instruments: [BINANCE_USDT_PERP_UNIVERSE]
timeframe: 1h
stop_loss_pct: 0.03
take_profit_pct: 0.06
backtest_period: "2021-01-01/2025-12-31"
mdd_bt: null
sharpe_bt: null
annual_return_bt: null
uses_signals:
- bollinger
- airborne_bb_reversal
risk_rules:
- short-only
- symbol-whitelist
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
- kst-19-hour-entry-gate
summary_ko: >
  Airborne BB-reversal 시그널의 SHORT 방향만 + 21종 동적 whitelist 운영하는
  신규 후보 전략. 147종 per-symbol 분해 + Hard OOS (train 2021-2023 / test
  2024-2025) 로 검증한 Test PF=1.214, sumR +1,395%, 5.45 trades/day. Legacy
  KST {8,11,16,22} 게이트는 알파 92% 손실시켜 train_PF>1 인 19시간 게이트로
  대체. 21종 whitelist 는 weekly cron + 지속성 규칙 + 사람 review 로 drift
  대응. 기존 airborne_trader 코드 0줄 수정 — composition 으로 risk gate 추가.
  status candidate, testnet 4주 paper trading 후 활성화 결정.
tags:
- live-scanner
- airborne
- short-only
- whitelist
- bollinger
- mean-reversion
- pine-v1.2
- pattern:live-scanner
- candidate
---

# Live Airborne SHORT-only Whitelist v1

기존 `airborne-family-overview` 의 모든 변형이 5y PF<1 로 rejected 였으나, **147종 per-symbol 분해 + Hard OOS 검증** 으로 SHORT-only + 21종 whitelist 조합에 양의 엣지 발견. 본 spec 은 그 발견을 **새 전략** 으로 분리·정형화.

## TL;DR

| 항목 | 값 |
|---|---|
| 진입 신호 | v1.2 BB-reversal (`retrace_ratio=0.6`, BB(20, 2.0)) — close 기반 |
| 방향 | **SHORT only** (LONG fire 는 무시) |
| Universe | **21종 whitelist** (`config/airborne_short_whitelist.yaml`) |
| **KST 시간 게이트** | **19시간** {0,1,2,3,5,9,10,11,12,14,15,16,17,18,19,20,21,22,23} — train_PF>1 hours |
| Stop / TP | 3% / 6% (R/R 1:2) |
| 5y in-sample PF | 1.176 (whitelist + hour gate) |
| **2y Hard-OOS PF** | **1.214 (n=3,977, +1,395% sumR)** |
| trades/day | ~5.5 (whitelist + hour gate) |
| 펀딩 효과 | SHORT 수익 +1pp PF |

## 5y 백테스트 게이트 통과 근거 (CLAUDE.md 가드)

| 지표 | 값 | 임계 | 통과? |
|---|---|---|---|
| Profit Factor (Hard OOS) | **1.214** | > 1.0 | ✅ |
| Expectancy (Hard OOS) | **+0.351% / trade** | > 0 | ✅ |
| Sharpe (참고) | 비공식 — test sumR 곡선 양수 일관 | — | — |
| trade count (test 2y) | 3,977 | — | ~5.5/day 운영 가능 밀도 |

→ 5y multi-regime · 현실 비용 10bp · funding 적용 · **KST 19시간 게이트 적용**. PF·expectancy 둘 다 양수.

산출물:
- `reports/airborne_hard_oos_funding.json` (147종 per-symbol metric)
- `reports/airborne_short_whitelist_hour_sweep.json` (시간 게이트 검증)

### Hour gate 변경 근거

기존 `airborne_trader` 의 legacy default `{8, 11, 16, 22}` 는 LONG+SHORT 양방향 + 30종 시절에 선정됨. SHORT-only + 21종 조합에 적용 시:

| 게이트 | te_PF | te_sumR | tr/day |
|---|---|---|---|
| Legacy `{8,11,16,22}` | 1.086 | +120% | 1.12 |
| **train_PF>1 19시간** | **1.214** | **+1,395%** | **5.45** |

→ legacy 사용 시 알파의 **92% 손실**. 본 spec 의 19-hour 게이트가 정답.

## Whitelist (21종)

```
1000LUNCUSDT, 1000SHIBUSDT, AAVEUSDT, APTUSDT, ARBUSDT, ARUSDT, ATOMUSDT,
AXSUSDT, BCHUSDT, BNBUSDT, BTCUSDT, DASHUSDT, ETHUSDT, FETUSDT, IDUSDT,
LTCUSDT, RIFUSDT, UNIUSDT, XLMUSDT, XRPUSDT, ZECUSDT
```

선별 기준 (`scripts/airborne_hard_oos_funding.py` 의 `train_funded` 게이트):
- 2021-2023 (train) 의 SHORT-only 3%/6% funded PF >= 1.0
- 2021-2023 의 SHORT-only fire 수 >= 50

이 중 **2024-2025 (test) 에서도 PF>1.05 인 코어 15종**: FETUSDT, APTUSDT, ATOMUSDT, AXSUSDT, DASHUSDT, UNIUSDT, ARBUSDT, RIFUSDT, ZECUSDT, XLMUSDT, 1000SHIBUSDT, LTCUSDT, IDUSDT, AAVEUSDT, XRPUSDT.

## 동적 Whitelist — Drift 대응 (3-레이어)

### 레이어 1: 정기 재평가 (weekly)

`scripts/refresh_airborne_short_whitelist.py` — 매주 토요일 KST 02:00 cron:
- 직전 6개월 (rolling) 데이터로 per-symbol SHORT 3%/6% PF 재계산
- 결과를 `config/airborne_short_whitelist.yaml` 에 후보 list 로 출력
- diff (added / removed / kept) 동봉

### 레이어 2: 지속성 규칙 (anti-churn)

`config/airborne_short_whitelist.yaml` 의 `state` 필드로 종목 단위 상태 머신:

```yaml
state:
  ARBUSDT:
    status: active        # candidate | active | warning | removed
    consecutive_pass: 12  # 연속 PF>1 주 수
    consecutive_fail: 0
  NEWALTUSDT:
    status: candidate     # 신규 — 진입 대기
    consecutive_pass: 2
    consecutive_fail: 0
```

전이 규칙:
- `candidate → active`: 3주 연속 rolling PF > 1.0 + n_trades >= 30
- `active → warning`: 1주 rolling PF < 0.95
- `warning → removed`: 추가 1주 PF < 0.95 (즉 2주 연속) **또는** 1주 PF < 0.85
- `warning → active`: 1주 PF >= 1.0 회복
- 매주 refresh 후 orchestrator 다음 cycle 부터 자동 반영 (`get_universe()` 가 매 dispatch 마다 yaml 재로드). daemon 재시작 불필요.

### 레이어 3: 운영 안전망

- 신규 `active` 종목 = **shadow mode 4주** (testnet 만 발주, mainnet 보류)
- whitelist 변경 시 dashboard `/airborne` 페이지에 알림 배너
- 변경 PR 의무화 (자동 commit 금지) — refresh 스크립트는 yaml 만 생성, 사람 review 후 merge

## 진입 / 청산 로직

### 운영 아키텍처 (orchestrator dispatch)

본 전략은 **별도 daemon 프로세스 없이** `AsyncStrategyOrchestrator` 가 매 봉마다 `on_bar(ctx)` 를 호출하는 표준 live-scanner 패턴을 따른다. 대시보드 "거래 시작" 버튼만 누르면 자동 가동.

```
qta.exe (또는 dashboard)
   └─ AsyncStrategyOrchestrator.run_bar
        └─ for sym in strategy.get_universe():   # ← whitelist active 종목만
             └─ on_bar(ctx={market_snapshot: history, ...})
                  ├─ KST 19시간 게이트 → 미통과 hold
                  ├─ evaluate_short_fire_v11 (retrace_ratio=0.6, atr_body_mult=0.3)
                  └─ 발화 시 Signal(action="sell", reason="airborne_short_wl_fire:...")
        └─ LivePositionRiskManager 가 stop=3% / TP=6% 청산 자동 처리
```

`scripts/airborne_alert_daemon.py` (Telegram 알림) 은 본 전략과 무관 — 24시간 그대로 발화.

### 게이트 평가 순서

1. **Universe 필터** (`get_universe()` 단계): `config/airborne_short_whitelist.yaml` 의 `status == "active"` 인 종목만 orchestrator dispatch 대상. 나머지는 호출 자체 안 됨.
2. **Warmup** (BB_WINDOW + ATR_PERIOD 미충족): hold
3. **KST 19시간 게이트**: `hour ∉ {0,1,2,3,5,9,10,11,12,14,15,16,17,18,19,20,21,22,23}` → hold
4. **SHORT setup 평가**: `evaluate_short_fire_v11` (LONG 평가 자체 안 함 — beneficial side effect of code path)
5. **Fire**: `Signal(action="sell", size=0.05, ...)` → orchestrator → `OrderIntent(reduce_only=False, ...)` ← `shorts_allowed=True`
6. **청산**: `LivePositionRiskManager` 가 mark price 기반 stop/TP 자동 청산

LONG fire 는 `on_bar` 가 evaluate_long_fire_v11 을 호출조차 안 해서 자연 차단. SHORT-only 보장.

## 운영 파라미터 (production.yaml)

```yaml
- id: live-airborne-short-whitelist-v1
  class: backtest.strategies.live_airborne_short_whitelist_v1.LiveAirborneShortWhitelistV1
  kwargs:
    default_size: 0.05         # kst-hours 와 동일 비중 (200$ @ 4000$ seed)
    stop_loss_pct: 0.03        # -3% price
    take_profit_pct: 0.06      # +6% (R/R 1:2)
    retrace_ratio: 0.6         # Hard OOS 검증 (부모 default 0.4)
    atr_body_mult: 0.3         # Hard OOS 검증 (부모 default 0.6)
    cooldown_after_stop_sec: 900  # stop 후 동일 (sid, symbol) 15분 재진입 차단
```

## 리스크 연동

- **Daily loss kill switch**: 일 −200 USDT 도달 시 자동 정지 (기존 메커니즘)
- **Max concurrent positions**: 8 (기존 10 보다 낮춤 — short 사이드 다양성 보장)
- **Cooldown after stop**: 15분 (기존)
- **Per-symbol leverage cap**: 10× (기존)
- **Whitelist 위반 시**: 단순 reject — 별도 처벌 없음
- **펀딩 비용 모니터링**: 펀딩 rate 가 −0.1%/8h 이하 (즉 SHORT 가 지급) 인 종목은 자동 임시 제외 (운영 시 fallback)

## PR 체크리스트

### 완료 (PR #341 + #343 머지)
- [x] `docs/specs/strategies/live-airborne-short-whitelist-v1.md`
- [x] `config/airborne_short_whitelist.yaml` 초기 21종 + state + kst_entry_hours
- [x] `src/live/airborne_short_whitelist/whitelist_loader.py` (yaml + validation)
- [x] `scripts/refresh_airborne_short_whitelist.py` (weekly cron)
- [x] `tests/live/airborne_short_whitelist/test_whitelist_loader.py`
- [x] `scripts/airborne_short_whitelist_hour_sweep.py` + 결과 JSON
- [x] `docs/patch-notes/index.yaml` v0.6.17

### 본 PR (orchestrator dispatch 전환)
- [x] `src/signals/airborne_bb_reversal.py` — `retrace_ratio` kwarg 추가 (backward-compat)
- [x] `src/backtest/strategies/live_airborne_short_whitelist_v1.py` — 실 live-scanner (parent: `LiveAirborneBbReversalKstHours`)
- [x] `tests/backtest/test_live_airborne_short_whitelist_v1.py` — 24 tests
- [x] `configs/orchestrator/production.yaml` — entry 등록 (testnet 활성)
- [x] `scripts/check_strategy_completeness.py` 통과

### Deprecated (다음 PR 에 정리)
- `src/live/airborne_short_whitelist/risk.py` (daemon-only risk gate, orchestrator 패턴으로 대체됨)
- `scripts/airborne_short_whitelist_daemon.py` (daemon entry, orchestrator 가 대신)
- `tests/live/airborne_short_whitelist/test_risk.py` (risk gate test)
→ 본 PR 에서는 *유지* (refresh 스크립트의 의존). 별도 cleanup PR.

## 외부 참조

- [[live-airborne-bb-reversal-v11]] — 진입 신호 원본 (v1.2 close-based)
- [[airborne-family-overview]] — 가족 전체 정리 (모두 rejected, 본 spec 만 candidate)
- [[live-universe-scanner-paradigm]] — paradigm 정의
- `reports/airborne_hard_oos_funding.json` — Hard OOS 산출 (24h baseline)
- `reports/airborne_short_whitelist_hour_sweep.json` — KST 19-hour 게이트 검증
- `reports/airborne_100sym_per_symbol_pf.json` — 147종 per-symbol 분해
- `reports/airborne_5y_signal_dev.json` — 60 entry-param sweep

## 윤리 / 면책

- 본 전략은 *백테스트 통과 candidate* 상태. 실거래 결정 사용자 본인 책임.
- 5y 통과했어도 *미래* 일관성 보장 X. drift 발생 시 weekly refresh + persistence 로직이 잡지 못할 수 있음.
- 알트 폭락기 (특히 2022) 의존성이 클 가능성. 알트 강세장 진입 시 PF 무너질 위험.
