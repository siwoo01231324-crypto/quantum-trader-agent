---
type: strategy
id: live-turtle-trend-daily
name: Live Turtle Trend Daily (Donchian 20/10 breakout + 200MA filter + 2xATR stop, long-only)
status: candidate
paradigm: universe-scan
instruments:
- binance-usdt-spot-top30
market: crypto
timeframe: 1d
uses_signals:
- donchian
- atr
risk_rules:
- atr-hard-stop-2x
- donchian-trailing-exit-10
- max-concurrent-6
owner: siwoo
created: 2026-06-24
sharpe_bt: 0.68
sharpe_live: null
mdd_bt: 22.7
annual_return_bt: 24.8
trades_bt: 295
backtest_period: 2021-05/2026-05
last_updated: 2026-06-24
pin_date: 2026-06-24
top_n: 6
risk_per_trade: 0.01
---

# Live Turtle Trend Daily

리처드 데니스 **터틀 트레이더 시스템**(영상 슈퍼트레이더 `sTSvaQ9336M`)을 Binance
top-N 크립토 **일봉**에 이식한 추세추종 전략. **롱 전용**. 2026-06-24 대규모 신호
리서치(`project_research_signal_screen_summary`)에서 5y·random-vs-signal·생존편향·
포트폴리오 4관문을 **모두 통과한 유일한 전략** — 첫 유효 candidate.

> 에어본 BB역추세(좁은 TP/SL)와 정반대 구조. 낮은 승률(31%) + 큰 익절(트레일링)으로
> 한 번의 큰 추세가 다수의 작은 손실을 덮는다. 좁은 고정 TP/SL이 신호 edge를 죽이던
> 문제 없음(추세 트레일링 청산).

## 패러다임 — universe-scan (일봉)

- **유니버스**: Binance USDT 현물 top-30 (델리스팅 포함, 생존편향 통제).
- **리밸 경계**: UTC 00:00 일봉 (`_is_my_bar_boundary`).
- **보유**: top_n=6 슬롯 동시보유, 동등가중.
- **pin-date**: 2026-06-24 (universe-scan 룰 — 백테스트 유니버스 고정 기준일).

## 진입 / 청산 룰

**진입** (롱만, AND 조건):
1. `close > rolling_max(high, 20).shift(1)` — 20봉 신고가 종가 돌파 (Donchian entry).
2. `close > SMA(200)` — 200MA 추세필터 (상승추세 확인).
3. `ATR(20) > 0` — 변동성 유효.
- 돌파강도 `(close − upper) / ATR` 내림차순으로 랭킹 → 빈 슬롯 채움.

**청산** (OR 조건):
1. `close < rolling_min(low, 10).shift(1)` — 10봉 신저가 종가 이탈 (Donchian trailing).
2. `low <= entry − 2.0 × ATR(20)_at_entry` — 2×ATR 하드스톱 (저가 터치, tail 통제).

## 리스크 연동

- **거래당 위험**: `risk_per_trade = 0.01` (자본의 1%). 포지션 규모 = `risk_capital / (2×ATR/entry)`.
  즉 스톱폭(2×ATR)이 클수록 명목 축소 — 변동성 정규화 사이징.
- **하드스톱**: 진입가 − 2×ATR(20)을 진입 시점에 고정 기록(`self._positions[code]["stop"]`),
  매 봉 저가가 터치하면 즉시 청산. worst-case tail −20% 캡(생존편향 검증에서 확인).
- **동시보유 한도**: top_n=6 (포트폴리오 시뮬상 6~10 적정; 3은 슬롯 부족, 위험 집중).
- **사이징 출력**: `on_bar` 의 `size = clip01(len(positions)/top_n)` — orchestrator 가
  명목 스케일. 슬롯 점유율에 비례한 보수적 노출.
- **숏 제외**: 크립토 숏은 5y 구조적 손실(상방 drift). 롱 전용
  (`project_research_signal_screen_summary` 원리 2).

## 5y 백테스트 결과 (게이트)

`scripts/bench_turtle_trend_daily_5y.py` → `reports/eval_turtle_trend_daily_5y.json`.
데이터: Binance top-24 생존 코인, 2021-05~2026-05, 비용 0.16%/거래.

| 지표 | 값 | 게이트 |
|---|---|---|
| Profit Factor | **2.35** | > 1.0 ✅ |
| 거래당 기대값 | **+8.74%** | > 0 ✅ |
| 거래 수 | 295 | — |
| 승률 | 31.5% | (낮음=추세추종 정상) |
| 포트폴리오 CAGR | +24.8% | — |
| 포트폴리오 MDD | 22.7% | — |
| 포트폴리오 Sharpe | 0.68 | ≥ 0.5 ✅ |

- **연도별**: 매년 PF>1 (2022 베어 포함 robust). 순수 베어(2022 단독) CAGR −14.8%(통제됨, 파산 아님).
- **random-vs-signal**: 동일 exit 룰 랜덤 진입 기대값 +0.68% → 터틀 롱 +8.66% = **랜덤 13배**.
- **생존편향**: 죽은코인 주입 breakeven 사망률 **16.6%**(폭락바운스 2.3%의 7.2배 강건).
  200MA위+신고가 진입조건이 죽는 코인을 구조적 배제.
- **타임프레임 핵심**: 1h 터틀은 PF 0.89(랜덤급, 거짓돌파 난자). 일봉(평균보유 22일)이라야 작동.

## PR 체크리스트

- [x] spec `docs/specs/strategies/live-turtle-trend-daily.md` (type: strategy, paradigm: universe-scan, pin-date)
- [x] 코드 `src/backtest/strategies/live_turtle_trend_daily.py` (Donchian 20/10 + 200MA + 2×ATR)
- [x] 단위 테스트 `tests/backtest/test_live_turtle_trend_daily.py` (5건: 진입/warmup/경계/롱전용/스톱청산)
- [x] 5y backtest 결과 `reports/eval_turtle_trend_daily_5y.json` (PF 2.35 / 기대값 +8.74% / 게이트 PASS)
- [x] production.yaml — candidate(주석, 비활성) 등록
- [ ] orchestrator 활성화 — **보류**. 실거래 모니터링 후 별도 PR 로 활성화 판단.
- [x] patch-notes index.yaml entry

## 상태

`status: candidate` — production.yaml **비활성**(주석). 5y·생존편향·포트폴리오 전부 통과했으나,
universe-scan broker 통합(동적 universe OHLCV 공급) 선결 + 실거래 페이퍼 모니터링 후
활성화. 관련: `project_turtle_daily_candidate` · `project_research_signal_screen_summary`.
