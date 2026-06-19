---
type: strategy
id: live-ppp-scalping-v1
name: Live PPP Scalping v1 (EMA 60/120/240 배열+지지 + QPP StochRSI 크로스, bidir)
status: candidate
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 15m
uses_signals:
- ema
- stoch-rsi
- rsi-divergence
risk_rules:
- per-symbol-stop-loss-1.5pct
- per-symbol-take-profit-3pct
owner: siwoo
created: 2026-06-18
sharpe_bt: null
sharpe_live: null
mdd_bt: -1.0
annual_return_bt: null
trades_bt: 34626
backtest_period: 2020-01/2025-12
last_updated: 2026-06-18
stop_loss_pct: 0.015
take_profit_pct: 0.03
trailing_stop_pct: null
profit_factor_bt: 0.871
expectancy_bt: -0.00143
verdict_5y: "CANDIDATE — 검증 미완(1차 bench 는 강의에 비충실). 1차 bench(15m · stop1.5%/tp3% 등 큰 청산폭 · 1분봉 미세진입 미반영)에서 4조합 PF 0.70~0.87 로 FAIL 했으나, 이는 강의의 실제 방식(1분봉 진입 + 매우 짧은 구조기반 익절: 다음이평/볼린저/1분 반대크로스, ROI 30%≈가격 0.6%@50x)과 청산폭·타임프레임이 어긋난 **비충실 파라미터** 결과라 판정 보류. 충실한 재검증(1m 봉 + HTF 레짐 + 소폭 구조청산 + 소폭 % 그리드) 후 확정 예정. profit_factor_bt/expectancy_bt 는 1차(비충실) 수치."
verdict_1y: null
summary_ko: |
  외부 유튜브 강의 "PPP 스캘핑 매매법"(1P/2P/3P)을 공개 표준 기술적지표로 독립
  구현한 live-scanner 전략. 1P=EMA(120)>EMA(240) 정/역배열로 방향, 2P=가격이
  60/120/240 EMA 중 하나를 지지(롱)/저항(숏) 터치, 3P=QPP(Stochastic RSI)
  본선×시그널 골든/데드 크로스 트리거. 세 레이어 정렬 시 진입(양방향). 청산은
  LivePositionRiskManager 가 stop 1.5%/tp 3%(1:2)로 처리. QPP 는 PPP 체험판
  지표(보호 상품)의 소스 복제 없이 출력 거동으로 Stochastic-RSI 계열임을 확인해
  재현 — TradingView 우리 지표 QPP Oscillator 와 동일 규약. status candidate,
  5y bench 미실시·미활성.
tags:
- live-scanner
- ema
- stoch-rsi
- mean-reversion-pullback
- intraday
- scalping
- bidirectional
- candidate
- pattern:live-scanner
---

# Live PPP Scalping v1 — EMA 60/120/240 + QPP(StochRSI) 크로스

## 도입 배경

외부 유튜브 무료특강 "직장 다니면서 손절 없이 60연승 기록한 PPP 스캘핑 매매법"
(채널 블록캠퍼스, 카페 이진트) 의 1P/2P/3P 매매 체계를 **공개 표준 기술적지표로
독립 구현** 한 live-scanner 전략. 강의의 자작 지표 "PPP" 는 유료 보호 상품이라
소스가 비공개이며, 그 출력 거동(0~100 본선/시그널 크로스 + 과매수/과매도)을
관찰해 **Stochastic RSI 계열** 임을 확인하고 `QPP` (Stochastic RSI) 로 재현했다.
같은 규약을 TradingView 우리 지표 `QPP Oscillator`
(`D:\ppp_transcribe\qpp_oscillator.pine`) 와 공유한다.

[[live-macross-regime-v1]] 의 venue-routing universe + LiveScannerMixin +
ClassVar stop/TP 골격을 그대로 미러한다.

## 진입 규칙 (매 마지막 확정봉)

1. **1P 방향** — 장기 이평 배열: `EMA(120) > EMA(240)` 정배열 → 롱장,
   `EMA(120) < EMA(240)` 역배열 → 숏장.
2. **2P 셋업** — 직전 `touch_lookback`(기본 3) 봉 안에서 가격이 60/120/240 EMA 중
   하나를 **지지**(롱: `low ≤ EMA×(1+tol)` 이고 `close > EMA`) / **저항**(숏:
   `high ≥ EMA×(1−tol)` 이고 `close < EMA`) 한 적이 있는가. `tol` 기본 0.15%.
3. **3P 트리거** — QPP(Stochastic RSI 14/14/3/3) 본선×시그널 크로스:
   golden(상향)→롱, death(하향)→숏.
4. **4P 중첩 근거(다이버전스)** — `signals.rsi.detect_divergence` (regular RSI
   다이버전스, 강의의 "Divergence for Many Indicators v4" 와 동일 개념). 진입
   방향과 정렬되는 다이버전스(롱→bullish / 숏→bearish)면 **가산점**: `confidence`
   0.5→0.7. 방향 불일치 다이버전스는 자동 제외(강의의 "PPP 와 같은 방향이면 신뢰,
   반대면 페이크" 필터와 일치). `require_divergence=true`(기본 false)면 4P 정렬을
   진입 **필수** 조건으로 강제 — 강의: "3P 로도 진입 가능, 4P 더하면 더 좋은 타점".

5. **OB/OS 구간 가중** — 강의 "과매도에서 골크 / 과매수에서 데크일 때 신뢰도↑".
   QPP 본선이 과매도(<`os_level` 25)에서 골든 / 과매수(>`ob_level` 75)에서 데드면
   가산점. `require_zone=true`(기본 false)면 진입 **필수**. 진입 `confidence` =
   0.5 + (4P 정렬 0.15) + (OB/OS 정렬 0.15) → 0.5 / 0.65 / 0.8.

1P~3P 정렬 시 진입(4P·OB/OS 는 가산점/옵션 필수). (선택) `btc_regime_gate=true` 면
macross 처럼 BTC SMA200 레짐과 방향 정렬(`universe_ohlcv["BTCUSDT"]`)도 추가 요구
— 기본 off.

### 강의 대비 단순화 (정직 기록)
- **2P 갭/이격 역추세 모드** 미구현 — 강의가 당일 상세히 가르친 "장기이평 지지/저항"
  모드만 구현. 갭(이격) 회귀 역추세 모드는 후속.
- **미세타점 1분봉** 멀티-TF 진입 정밀화 미구현 — 단일 15m interval. 강의는 15m
  셋업→1m 미세진입.
- **반대 QPP 크로스 청산** 미구현 — live-scanner 는 보유 중 1포지션 고정 + 청산을
  `LivePositionRiskManager`(stop/tp/trailing) 전담이라 신호기반 청산·플립 불가
  (orchestrator 개조 필요). Pine 버전엔 구현됨.

## 청산

- `stop_loss_pct = 0.015` (−1.5%) / `take_profit_pct = 0.03` (+3%) = **손익비 1:2**.
- `LivePositionRiskManager` (live-scanner 공통) 가 stop/TP 도달 시 즉시 청산 —
  전략은 진입만 담당 (death-cross 의 sell 은 *숏 진입*, 롱 청산이 아님 — bidir).
- **동적 per-entry 청산 모드 (오케스트레이터 수정 0 — `Signal` override 경유).**
  진입 시점에 가격 레벨을 계산해 `stop_loss_pct_override`/`take_profit_pct_override`
  로 전달, `LivePositionRiskManager` 가 (sid,symbol)별 소비. 계산 불가/0.2% 미만/음수면
  정적 pct 로 자동 폴백.
  - **`sl_mode`** = `fixed`(정적 1.5%) | `ema`(지지(롱)/저항(숏) 이평 이탈, `sl_buffer_pct` 버퍼)
  - **`tp_mode`** = `fixed`(정적 3%) | `next_ema`(다음 이평 목표, 예 240지지→120) |
    `bb_mid`(볼린저 중앙선) | `bb_upper`(볼린저 상단(롱)/하단(숏)) — `bb_period`/`bb_std`
  - = 강의 익절 3로직 중 ①다음 목표(next_ema) ②보이는 저항(bb_mid/bb_upper) 반영.
- **반대 QPP 크로스 청산**(이벤트 기반)은 TP/SL(가격 레벨)로 표현 불가 → 미구현.
  반전 청산은 `trailing_stop_pct`(가격기반 % 반전 청산)로 근사 가능.
- 스캘핑이라 짧은 보유 지향 → global 1h time-stop 과 충돌 없음 → `max_hold_sec`
  미선언(global 유지). (macross 와 달리 면제 불필요.)

## universe

top-100 venue-routing — airborne/macross `get_universe` 미러.
`QTA_BROKER_VENUE=bitget` → Bitget 거래량 top-100, 그 외(기본/binance) → Binance
top-100 (`get_top_n_symbols(100)`). interval = `15m`.

## 백테스트 결과 (정직 기록)

**5y(2020-01~2025-12) · 15m · 10 메이저 · 왕복 20bp** (`scripts/bench_ppp_scalping_5y.py`,
`reports/bench_ppp_scalping_5y.json`). 진입(1P/2P/3P)은 청산 무관 1회 산출, 청산
4조합만 재시뮬. 지표·청산은 전략 실제 함수 재사용(백테스트=라이브 동일).

| 청산 조합 | 거래 | PF | 거래당 기대값 | 승률 | MDD | 평균보유 |
|---|---|---|---|---|---|---|
| fixed 1:2 (stop1.5%/tp3%) | 34,626 | **0.871** | **−0.143%** | 34.1% | −100% | 31봉 |
| ema 손절 + next_ema 익절 | 58,814 | 0.742 | −0.169% | 32.1% | −100% | 14봉 |
| ema 손절 + bb_upper 익절 | 61,483 | 0.698 | −0.192% | 35.0% | −100% | 12봉 |
| ema 손절 + bb_mid 익절 | 61,747 | 0.722 | −0.175% | 34.9% | −100% | 13봉 |

- **4개 조합 전부 PF<1 AND 기대값<0** → 활성화 게이트(PF>1 AND 기대값>0) 명확 미충족.
- 동적 익절(다음 이평/볼린저)은 거래수 폭증(35k→59~62k)으로 왕복 비용 잠식이 심해져
  fixed 보다 **악화** (PF 0.87→0.70대). 줄먹 빈번매매가 비용 앞에서 역효과.
- 강의 원 가족([[live-airborne-bb-reversal-kst-hours]]·live_mg_bb_reversal)의 5y
  reject 패턴과 동일.

### ⚠️ 위 1차 bench 는 강의에 **비충실** — 판정 보류

위 결과는 **stop 1.5%/tp 3% 같은 큰 청산폭 + 15m 단일봉**으로 돌린 것인데, 강의의
실제 방식과 다음이 어긋난다:
1. **청산폭**: 강의는 "줄 때 먹어라" 매우 짧은 익절(다음 이평/볼린저/1분 반대크로스,
   ROI 30%≈가격 0.6%@50x). 1.5%/3% 가격 청산은 5~10배 과대 → 15m 노이즈에 노출.
2. **타임프레임**: 강의는 15m 셋업 → **1분봉 미세진입+청산**. 1차 bench 는 15m 단일.
3. fixed 가 동적보다 덜 나빴던 이유도 "거래수↓→비용↓" 일 뿐, 엣지 측정이 아님.

→ **충실 재검증 필요**(1m 봉 + HTF 레짐 + 소폭 구조청산 + 소폭 % 그리드). 그 전까지
**candidate(판정 보류)**, 라이브 미활성. 위 PF 0.70~0.87 은 *비충실 파라미터* 수치임.

## 리스크 연동

본 전략은 `status: candidate` (비활성) 이라 현재 orchestrator 등록 / 일수익률
export 를 하지 않는다. **production 활성화로 승격 시** 아래를 필수로 수행한다
(live-scanner 공통 계약 — `src/backtest/strategies/.ai.md` "리스크 연동"):

```python
from src.backtest.strategies.live_ppp_scalping_v1 import LivePppScalping

orch.register_strategy("live_ppp_scalping_v1", LivePppScalping())
orch.register_strategy_returns(
    "live_ppp_scalping_v1",
    daily_returns_series,   # index=날짜, 값=그날 실현 수익률
)
orch.refresh_portfolio_risk()
```

- 청산(stop −1.5% / TP +3%)은 `LivePositionRiskManager` 가 ClassVar
  `stop_loss_pct` / `take_profit_pct` 를 소비해 평가 — 전략은 진입만 담당.
- 일수익률 미공급 시 `portfolio_risk is None` → 리스크 평가기가 항상 ALLOW →
  리스크 관리 무력화(불변식). 활성화 전 반드시 연동.

## 과적합 / 한계

- **스캘핑 비용 민감** — 작은 TP(3%)는 슬립+수수료에 잠식되기 쉬움. 5y 현실비용
  bench 전엔 라이브 금지.
- **3중 정렬(1P+2P+3P)** 은 신호 빈도를 줄여 표본 부족 위험 — bench 시 거래수 확인.
- **외부 강의 출처** — "손절 없이 60연승·월 380%" 등 원 강의 주장은 검증 불가
  마케팅 수치. 본 구현은 그 *방법론*(공개 TA 조합)만 차용, 주장 성과를 보증 안 함.

## PR 체크리스트

- [x] `src/backtest/strategies/live_ppp_scalping_v1.py` (LiveScannerMixin, on_bar 1P/2P/3P)
- [x] `docs/specs/strategies/live-ppp-scalping-v1.md` (본 파일)
- [x] `tests/backtest/test_live_ppp_scalping_v1.py` (19 케이스, 컴포넌트+on_bar)
- [x] `configs/orchestrator/production.yaml` commented candidate entry
- [x] `docs/patch-notes/index.yaml` entry (candidate 추가, 비활성)
- [x] **5y bench 실시** — `scripts/bench_ppp_scalping_5y.py` (10 메이저·15m·왕복20bp).
  **게이트 FAIL** (4조합 전부 PF<1·기대값<0, 최선 PF 0.871) → status: rejected.
- [ ] orchestrator register/returns export — **안 함 (rejected)**

## 관련

- [[live-macross-regime-v1]] — 미러 템플릿 (venue-routing universe, LiveScannerMixin,
  ClassVar stop/TP, bidir).
- TradingView 지표: `QPP Oscillator` (Stochastic RSI 본선/시그널, PPP 체험판 동작
  재현) — `D:\ppp_transcribe\qpp_oscillator.pine`.
