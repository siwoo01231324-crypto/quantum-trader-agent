---
type: strategy
id: live-hts-hybrid
name: Live HTS Screener Hybrid (단타+5분대기+스윙)
paradigm: live-scanner
status: paper
instruments:
- krx-kospi-kosdaq
market: krx
timeframe: 1m
uses_signals:
- hts-cond-dts
- hts-cond-wait5m
- hts-cond-swing
risk_rules:
- entry-time-gate-10:30
- live-position-risk-manager
stop_loss_pct: 0.02
take_profit_pct: 0.02
trailing_stop_pct: null
owner: siwoo
created: 2026-05-14
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
backtest_period: "2026-05-14/2026-05-14"
last_updated: 2026-05-14
summary_ko: |
  키움 HTS 조건검색식 3종 (단타·5분대기·스윙) 을 OR 합성한 단일 live-scanner 전략.
  매 1분봉 종목별 평가 → 어느 1개라도 통과 시 즉시 매수.
  KST ≤ 10:30 시간대 게이트. LivePositionRiskManager 가 -2% 손절 / +2% 익절 자동 청산.
tags:
- pattern:live-scanner
- intraday
- krx
- equity
- screener-driven
- hts-cond
---

# Live HTS Screener Hybrid (단타 + 5분대기 + 스윙)

키움 영웅문 HTS 조건검색식 3종 (5분대기 / 단타 / 스윙) 을 OR 합성한 단일 KRX 단타 live-scanner 전략. 어느 1개라도 통과 시 진입.

## 검색식 구성 (사용자 제공 2026-05-14)

### 공통 조건 (A~G, 일간)
| 조건 | DTS·WAIT5M | SWING |
|---|---|---|
| A 종가 범위 | 900 ~ 10,000원 (1봉이내) | 900 ~ 9,000원 (2봉이내) |
| B 등락률 | 2% ~ 30% | 3% ~ 30% |
| C 거래량 | 40,000+ | 50,000+ |
| D 5봉 누적 거래량 | 500,000 ~ 90,000,000,000 | 동일 |
| E 체결강도 | 90% ~ 1,000% | 동일 |
| F 이평선 정배열 | close > MA5 > MA20 > MA60 | 동일 |
| G 등락률 | 5% ~ 30% | 동일 |

### 차별 조건 (H)
- **DTS** (3분봉): 10봉 이내 종가 ≥ 20단순이평 (이격도 100~999% 의 실질 의미)
- **WAIT5M**: 상승방향 정적 VI 근접율 ≤ 3% (VI 발동가 = 전일종가 × 1.10)
- **SWING**: H 없음 (G까지)

### OR 합성
`hybrid_or = DTS or WAIT5M or SWING` — `triggered_by` 로 어느 검색식이 fire 했는지 명시 (디버깅).

## Live-scanner Paradigm 준수

- `LiveScannerMixin` 상속 (`is_live_scanner = True`).
- Per-symbol `on_bar(ctx)` — `ctx["market_snapshot"]["history"]` 의 1분봉 종목별 history 평가.
- Buy signal 만 발행 — sell 은 `LivePositionRiskManager` 가 자동 처리.
- 시간대 게이트 (`max_entry_hour=10.5`): KST > 10:30 시 hold 반환.
- `default_size = 0.05` (포지션당 자본 5%).

## 청산 (LivePositionRiskManager)

| 트리거 | 임계값 | 효과 |
|---|---|---|
| stop_loss_pct | 0.02 (-2%) | 손절 (가장 빈번) |
| take_profit_pct | 0.02 (+2%) | 익절 |
| trailing_stop_pct | null | 미사용 |
| EOD (KRX 15:20) | — | live_loop 의 강제 청산 hook |

## 진입 (룰베이스, LLM 미사용 — 불변식 #6)

매 1분봉 종목별:
1. **시간대 게이트**: KST 시각 ≤ 10:30 (그 외 hold).
2. **일간 스냅샷 로드**: `data/cache/krx_daily/<symbol>.parquet` (오늘 row 제외).
   - prev_close, prev_close_2, MA5/20/60, 5봉 누적 거래량
3. **3분봉 리샘플**: 1분봉 history → 3분봉 close 시계열.
4. **Hybrid OR 평가**: `evaluate_hybrid_or(daily_inputs, bars_3m, current_price)`.
5. fire 시 `Signal(action="buy", size=0.05, reason="hts_hybrid:<triggered_by>")` 발행.

## 데이터 의존

- 1m history: `snapshot_builder` 가 universe 종목 1m bar 누적 (live_loop 가 매 tick KIS REST/WS 로 수집).
- 일봉: `data/cache/krx_daily/<symbol>.parquet` — `scripts/cron_fetch_screener_universe.py` 가 매일 갱신 (Task Scheduler 평일 16:30 KST).

## 알려진 한계

- **E (체결강도) placeholder** = 100.0 (>=90 통과 처리). KIS `tday_rltv` 가 당일 누적 스냅샷만 제공 → 분봉 시점별 정확한 누적값 재현 불가. 분봉 tick 누적 재구성은 별도 이슈.
- **단타 H "지지"** 단일 봉 1회 기준 — 키움 내부 정확한 정의 단정 불가 (qa12.htm 추가 확인 권장).
- **5y backtest gate 미통과 위험**: KIS 1m API 가 당일·30일 한정 → 5y backtest 데이터 부족. CLAUDE.md 의 live-scanner Sharpe ≥ 1.0 gate 충족 어려움 → `status: paper` 유지, `production.yaml` 의 entry 는 `enabled: false` 로 두고 paper 운영만.

## 활성화 절차 (CLAUDE.md "새 전략 추가 시 필수" 준수)

1. `production.yaml` 의 `live-hts-hybrid` entry uncomment (env-gated 옆).
2. `LIVE_SCANNER_ENABLED=1` 환경변수 설정.
3. 5거래일 paper 누적 (5/15~5/21) + walk-forward 검증 — 본 운영 조건.
4. 5y backtest 데이터 부족 → 대안 검증 (paper 1개월) 필요.

## 백테스트 결과 (1일 pilot, 2026-05-14)

| Metric | hybrid_or | DTS only | WAIT5M only | SWING only |
|--------|---------:|---------:|------------:|-----------:|
| trades | 20 | 15 | 16 | 20 |
| win rate | 65.0% | 66.7% | 62.5% | 65.0% |
| avg PnL | +0.520% | +0.586% | +0.420% | +0.520% |
| expectancy | +0.600% | +0.667% | +0.500% | +0.600% |

⚠️ 1일 표본, 통계 신뢰 매우 낮음. 5거래일 누적 후 본검증 (예상 2026-05-22) 에서 채택 임계값 확인.

채택 임계값: win_rate ≥ 50% AND avg_pnl ≥ +0.3% AND trades ≥ 30.

bench: `scripts/grid_hts_cond.py` (≤10:30 + hybrid_or 행 참조).

## 리스크 연동

```python
orchestrator.register_strategy("live_hts_hybrid", strategy)
orchestrator.register_strategy_returns("live_hts_hybrid", daily_return_series)
```

- `daily_return_series`: index=KRX 거래일, 값=일수익률 (비용 차감 후).
- LiveScannerMixin paradigm 의 orchestrator dispatch 가 per-symbol fan-out 으로 매 tick 호출.
- ENB/CVaR/상관 체크는 orchestrator 에 등록된 모든 strategy 와 함께 평가.

## 관련 노트

- [[live-universe-scanner-paradigm]] — 본 전략이 따르는 paradigm
- [[live-rsi-oversold-volume-spike]] — 동일 paradigm 의 다른 사례
- `hts-cond-dts` / `hts-cond-wait5m` / `hts-cond-swing` — 구성 검색식 (signals 등록 후속, 정식 노트 미존재)

## 출처

- 검색식 캡처 3장: 사용자 제공 (2026-05-14, 이슈 #230)
- 키움 이격도 가이드: https://www.kiwoom.com/wm/fnd/fs010/fndTechIndiGuidePop
- 키움 이평선 지지 도움말: https://download.kiwoom.com/hero4_help_new/qa12.htm
- KIS API (FHKST03010200 분봉, FHKST01010100 현재가): https://apiportal.koreainvestment.com/
- 본 레포: `docs/specs/live-universe-scanner-paradigm.md`, #230 이슈, #228 머지
