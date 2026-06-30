---
type: strategy
id: live-macross-regime-v1
name: Live MA-Cross Regime v1 (SMA25/200 cross + BTC SMA200 regime gate, bidir)
status: candidate
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1h
uses_signals:
- sma-cross
risk_rules:
- per-symbol-stop-loss-2pct
- per-symbol-take-profit-12pct
owner: siwoo
created: 2026-06-18
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: null
backtest_period: 2024-06/2026-06
last_updated: 2026-06-19
stop_loss_pct: 0.02
take_profit_pct: 0.12
trailing_stop_pct: null
profit_factor_bt: 1.61
expectancy_bt: 0.0048
verdict_5y: "CANDIDATE (비활성, 활성화 선결 2건 남음). **데드크로스 숏 confluence 스택이 5y 게이트 통과** (2026-07-01 재검증, 30종목 data/cache/binance_1m). 채택 스택 = R(BTC레짐 하락) + ADX≥20/25 + 자기SMA200하회 + 과확장회피(10%) + **자체도출 시간게이트 {2,3,4,5,6,7,12,13,14,19,22}**(에어본차용 {1,2,3,5,6,7,8,23} 폐기 — 8h·23h 는 MA크로스선 PF<0.8 손실, 12·13·14h 좋은시각 누락이었음). **5y PF 1.61·기대값 +0.48% / 2y PF 1.42, split-half OOS 전반 PF1.43·후반 PF1.81 둘 다 통과**(시간게이트 없이 R+DdPX 만 해도 전반1.13·후반1.28 통과 — 게이트는 거래 1127→515 줄이며 PF 샤프닝). 롱(골든)은 raw 5y −144% 구조적 손실이라 **숏 전용**(allow_long=False). 과거 평결(레짐만 PF1.22·5y BTC/ETH 본전)은 raw+레짐 기준이었고, confluence 스택은 그보다 명확히 우위. ⚠️ 활성화 선결: ① cross-strategy 종목중복 차단(swing 3전략 — 투매반등/터틀 롱 + macross 숏 같은종목 동시보유 시 Bitget one-way 네팅사고, 현 가드는 live-airborne prefix 한정) ② 전체 명목 합산 상한(롱2+숏1 증거금 경합, 현 max_concurrent 는 전략별만) ③ 생존편향(폐지코인 부재 5y)·라이브 슬리피지 소액 검증."
verdict_1y: null
summary_ko: |
  1h 종가 SMA(25)/SMA(200) 골든·데드 크로스 + BTC SMA200 레짐 게이트 (양방향).
  골든→롱은 BTC 상승장(close≥SMA200), 데드→숏은 BTC 하락장(close<SMA200) 에서만
  진입 — 추세 정렬된 방향만 통과 (역행/BTC데이터부재 시 hold). 청산 1:6
  (stop −2% / TP +12%, LivePositionRiskManager). 2년 broad PF 1.22·기대값
  +0.39% (전 반기 양수) 이나 5y BTC/ETH 본전(PF 1.01) → candidate, 비활성.
tags:
- live-scanner
- sma-cross
- trend-following
- regime-filter
- intraday
- bidirectional
- btc-regime
- candidate
- pattern:live-scanner
---

# Live MA-Cross Regime v1 — SMA25/200 Cross + BTC SMA200 Regime Gate

## 도입 배경

`scripts/ma_cross_alert_daemon.py` 의 Bitget MA-cross 텔레그램 알림 데몬은
1h 종가 SMA(25)/SMA(200) 골든·데드 크로스를 24h 발화한다. 이 시그널을
orchestrator 안에서 직접 평가하되, **BTC SMA200 레짐 게이트** 를 얹어 시장
추세와 정렬된 방향만 진입시키는 것이 본 전략 (`live-scanner` 패러다임).

[[live-airborne-bb-reversal-kst-hours]] 의 venue-routing universe / BTC 레짐
판정(`universe_ohlcv["BTCUSDT"]`) 패턴을 그대로 미러하되, airborne 의 단방향
LONG 차단 대신 **양방향 레짐 정렬** 로 일반화한다 (상승=롱 허용, 하락=숏 허용).

## 진입 규칙

매 *마지막 확정봉(closed bar)* 에서 순서대로 평가:

1. **SMA 크로스** (`detect_cross` — daemon 과 동일 규약):
   - golden: 직전 `fast<=slow` 이고 현재 `fast>slow` → 롱 후보 (buy)
   - death : 직전 `fast>=slow` 이고 현재 `fast<slow` → 숏 후보 (sell)
   - SMA(25)/SMA(200), 최소 slow+2=202 봉 필요.
2. **SMA200 기울기 필터** (ranging 감지 1차, v0.6.x 추가):
   - `SMA200[-1] > SMA200[-6]` 이면 slope="up", 반대면 "down", 같으면 "flat"
   - 골든크로스 → slope="up" 이어야 통과 (flat/down → hold `slope_gate`)
   - 데드크로스 → slope="down" 이어야 통과 (flat/up → hold `slope_gate`)
   - 근거: flat SMA200 위 크로스는 range 내 표류 → whipsaw 원인 (StockCharts ADX/MA 문서)
3. **ADX(14) ≥ 20 필터** (ranging 감지 2차, v0.6.x 추가):
   - Wilder 기준 ADX < 20 → 추세 없는 ranging 환경 → hold (`adx_gate`)
   - ADX 데이터 부족(warmup 29봉 미만) → 보수적 skip (hold)
   - 구현: `_indicators.adx()` (numpy 전용, 외부 라이브러리 없음)
   - 근거: Wilder(1978) — ADX 20~25 gray zone, 25+ 강세추세. 1h intraday 기준 20 적용.
4. **BTC SMA200 레짐 게이트** (엣지의 핵심):
   - golden→롱은 **BTC close ≥ BTC SMA200 (상승장)** 일 때만 통과.
   - death →숏은 **BTC close <  BTC SMA200 (하락장)** 일 때만 통과.
   - 역행 (골든+하락장 / 데드+상승장) → **hold** (진입 안 함).
   - BTC ohlcv 부재 / warmup → 보수적으로 진입 **skip (hold)**.
   - BTC ohlcv 는 orchestrator 가 `market_snapshot["universe_ohlcv"]["BTCUSDT"]`
     로 박아준다 (airborne 와 동일 경로).
5. **리서치 confluence 필터 (opt-in, 기본 OFF, 2026-06-19 추가)** — 아래
   "리서치 confluence" 섹션의 5y/2y/split-half OOS 검증으로 채택. 생성자 인자:
   - `allow_long` / `allow_short` (기본 True) — **숏-집중 시 `allow_long=False`**.
     롱 raw 는 5y −144%·2y −161% 구조적 손실 → 숏만 남기면 엣지 정제.
   - `kst_hour_gate` (기본 False) + `kst_hours` (기본 **`{2,3,4,5,6,7,12,13,14,19,22}`**) —
     진입봉 KST 시(時)가 게이트 밖이면 hold (`kst_gate`). **2026-07-01: 기본값을
     MA크로스 데드숏 *자체 데이터* 도출로 교체** (옛 airborne kst-hours 차용 폐기).
     도출법: R+DdPX 데드숏을 시각별 분해 → 5y AND 2y 둘 다 PF>1 & n≥8 인 시각만 채택.
     에어본차용 대비 5y PF 1.31→1.61·거래 361→515 (8h·23h 손실시각 제거, 12~14h 호시각 추가).
   - `self_sma200_filter` (기본 False) — 롱은 가격이 자기 SMA200 위, 숏은 아래일
     때만 통과 (`self_sma200`). 장기추세 정렬 (BTC 레짐과 별개의 종목 자체 추세).
   - `overextension_max_pct` (기본 None) — 진입가가 자기 SMA200 에서 이 비율 초과
     이탈했으면 hold (`overextended`). 이미 급등/급락한 데 추격 금지 (권장 0.10).

## 청산

- `stop_loss_pct = 0.02` (−2% 가격) / `take_profit_pct = 0.12` (+12% 가격)
  / `trailing = null` → **손익비 1:6**.
- `LivePositionRiskManager` (live-scanner 공통) 가 24h 어느 시각이든 stop/TP
  도달 시 즉시 청산. 전략은 sell 청산 시그널을 직접 내지 않는다 (death-cross
  의 sell 은 *숏 진입*, 롱 청산이 아님 — bidir).

## universe

top-100 venue-routing — airborne `get_universe` 미러. `QTA_BROKER_VENUE=bitget`
이면 Bitget 거래량 기준 top-100, 그 외(기본/binance)는 Binance top-100
(`get_top_n_symbols(100)`). interval = `1h`.

## 백테스트 결과 (정직 기록)

내가 이미 돌린 결과:

| 구분 | 결과 |
|---|---|
| **2년 broad (top-65, 2024-06~2026-06)** | 레짐필터+1:6 → **PF 1.22, 거래당 기대값 +0.39%, 전 반기 양수** (펀딩·수수료 후). 거래/주 ~45, 보유 중앙값 14h, 승률 19% (저승률 추세추종) |
| 포트폴리오 sim ($1000, 5x, 명목 50%) | MDD 88% (과도) |
| **권장 사이즈** | **명목 5~10% + 동시보유캡 5종 → CAGR ~10-12%, MDD ~15-20%** |
| **5년 BTC/ETH** | **PF 1.01 (본전), 2022·2025 손실 연도** → 5y 다중자산 미충족 (데이터 한계: 알트 5y perp history 없음) |

### verdict

**candidate (비활성)**. 2년 broad 는 양(+) 이나 5y 본전 + breadth 의존 +
저승률 변동성 → production 활성화 전 추가 검증 필요. **활성화 게이트
(5y PF>1 AND 거래당 기대값>0) 미충족이라 비활성.** production.yaml 미등록,
orchestrator register/returns export 미실시.

## 리서치 confluence (숏-집중 스택, 2026-06-19)

골크/데크 승률을 **과적합 없이** 올리는 방법을 리서치 — 정석 추세확인 지표를
*사전지정 임계값*(스윕 금지)으로 검증, **5y AND 2y 둘 다 개선 + n≥100** 만 채택,
최종 combo 는 **전반기/후반기 split-half OOS** 재검증. 데이터: `data/cache/binance_1m`
30종목 1h, 비용 10bp, R/R 1:6. (실험: `scripts/_macross_*` — research scratch.)

**채택 (robust):**
- **ADX(14)≥20** — MA-크로스 whipsaw 의 교과서적 해법(Wilder 1978). 단독 5y PF
  1.20→1.29 / 2y 1.35→1.53. 본 전략에 이미 반영(진입규칙 3).
- **숏-집중** — 롱 raw 5y −144%·2y −161% 구조적 손실, 엣지는 숏. `allow_long=False`.
- **KST 시간게이트** — 숏 PF 1.10→1.20(5y). airborne kst-hours 와 동일 시간대.
- **자기 SMA200 하회 + 과확장 회피** — 약하지만 5y·2y 일관된 보조 (+).

**기각 (비robust/역효과):** SMA200 기울기 단독(lookback24 중립 — 단 lookback5 는
숏+ADX+H 와 묶으면 borderline +라 유지), 4h 정렬·거래량·RSI(과매도 추격금지는
크립토 숏에서 역효과 PF 0.97), QPP존·EMA배열·다이버전스(15m 스캘핑용 — 1h 추세에 역효과).

**검증 수치 (숏 전용):**

| 구성 | 5y PF / 기대값 / 승률 / n | 2y PF / 기대값 / 승률 / n |
|---|---|---|
| 현 v1 (롱+숏+레짐, 필터 전) | 1.02 / +0.03% / 16% / 5490 | 1.00 / −0.01% / 2533 |
| 숏+레짐+H | 1.20 / +0.36% / 18% / 575 | 1.35 / +0.61% / 244 |
| **숏+레짐+H+ADX+자기200+과확장 (권장)** | **1.33 / +0.59% / 20% / 452** | **1.59 / +1.00% / 23% / 184** |
| split-half OOS (전반 / 후반) | 전반 1.27 / 후반 1.36 | (양쪽 독립 개선 — 과적합 아님) |

→ **권장 숏-집중 구성** (활성화 시): `allow_long=False, kst_hour_gate=True,
self_sma200_filter=True, overextension_max_pct=0.10`. 이 구성이 5y 게이트
(PF>1 AND 기대값>0)를 **통과**(PF 1.33, 기대값 +0.59%) — 현 v1(본전)과 결정적 차이.
단 여전히 30종목 1h 한정 검증이라 라이브 OOS 전까지 candidate 유지.

## 리스크 연동

본 전략은 `status: candidate` (비활성) 이라 현재 orchestrator 등록 / 일수익률
export 를 하지 않는다. **production 활성화로 승격 시** 아래를 필수로 수행한다
(live-scanner 공통 계약 — `src/backtest/strategies/.ai.md` "리스크 연동"):

```python
from src.backtest.strategies.live_macross_regime_v1 import LiveMacrossRegime

orch.register_strategy(
    "live_macross_regime_v1",
    LiveMacrossRegime(),
)
orch.register_strategy_returns(
    "live_macross_regime_v1",
    daily_returns_series,   # index=날짜, 값=그날 실현 수익률
)
orch.refresh_portfolio_risk()
```

- 청산(stop −2% / TP +12%)은 `LivePositionRiskManager` 가 ClassVar
  `stop_loss_pct` / `take_profit_pct` 를 소비해 평가 — 전략은 진입만 담당.
- 일수익률을 공급하지 않으면 `portfolio_risk is None` → 리스크 평가기가 항상
  ALLOW → 리스크 관리 무력화 (불변식). 활성화 전 반드시 연동.

### ⚠️ 활성화 전 필수 — 시간기반 청산(time-stop) 면제

**현재 `LivePositionRiskManager.max_hold_sec` 는 global**(전 전략 공통,
`scripts/live_run.py` 가 env `AIRBORNE_MAX_HOLD_SEC` 기본 **3600초=1시간**으로
주입). airborne(역추세·단기, 상승장 숏 무한보유 방지 v0.6.72/#440)용 방어장치다.

**MA크로스는 이 1h time-stop 과 정면 충돌한다.** 추세추종 1:6 이라 TP(+12%)
도달에 **중앙값 2.4일·최대 30일** 걸린다(백테스트 보유분포). global 1h 아래서
돌면 **전 포지션이 진입 1시간 만에 `time_exit` 강제청산** → 손익비 1:6 이 ~0 으로
붕괴, 엣지 전멸. (airborne 의 보수적 방지턱이 추세전략엔 독.)

→ **조치 = 1번(per-strategy 오버라이드) 구현 완료 (2026-06-18)**:
- `LivePositionRiskManager.set_strategy_max_hold(sid, max_hold_sec)` + `_max_hold_by_sid`
  맵 + `_effective_max_hold(sid)` 추가. 맵 미등록 전략은 global 사용(**airborne
  byte-identical, 영향 0**) — 회귀테스트 `tests/portfolio/test_live_position_timeout.py`
  (`test_per_strategy_no_override_is_global_byte_identical` 등) 박제.
- 본 전략은 `max_hold_sec: ClassVar = None`(time-stop 면제) 선언. `scripts/live_run.py::
  _register_exit_policies` 가 ClassVar 선언 전략만 sentinel 로 읽어 오버라이드 등록
  → **활성화 시 자동 면제**. airborne 은 ClassVar 미선언 → 맵 미등록 → global 1h 유지.

(trailing 미사용 → #258 warm-guard N/A. synthetic SL/TP 백업은 본 전략 0.02/0.12
그대로 써 정상.)

## 과적합 / 한계

- **breadth 의존** — 2년 broad(top-65) 양수가 5y BTC/ETH 본전과 갈리는 핵심:
  알파가 폭넓은 알트 유니버스에 분산돼 있음. 5y 알트 perp history 부재로
  broad 5y 검증 불가 — 본전이 최선 추정.
- **저승률(19%) 추세추종** — TP/SL 1:6 으로 소수 큰 추세에 의존. 무손절 연속
  손실 구간이 길어 멘탈/마진 리스크 큼 → 권장 사이즈 보수적(명목 5~10%).
- 진정한 out-of-sample 검증은 라이브에서만 가능.

## PR 체크리스트

- [x] `src/backtest/strategies/live_macross_regime_v1.py` (LiveScannerMixin, on_bar — SMA200 slope + ADX + opt-in confluence 필터)
- [x] `docs/specs/strategies/live-macross-regime-v1.md` (본 파일 — 진입 규칙 5단계 + 리서치 confluence 섹션)
- [x] `tests/backtest/test_live_macross_regime_v1.py` (37 케이스 — slope/ADX + confluence 숏-집중)
- [x] `docs/patch-notes/index.yaml` entry (candidate 추가, 비활성)
- [ ] **5y bench gate PASS** — 미충족 (5y BTC/ETH PF 1.01 본전). candidate.
- [ ] `configs/orchestrator/production.yaml` 등록 — **안 함 (candidate)**
- [ ] orchestrator register/returns export — **안 함 (candidate)**

## 관련

- [[live-airborne-bb-reversal-kst-hours]] — 미러 템플릿 (venue-routing universe,
  BTC 레짐 판정, LiveScannerMixin, ClassVar stop/TP). 본 전략은 그 BTC 필터를
  양방향으로 일반화.
