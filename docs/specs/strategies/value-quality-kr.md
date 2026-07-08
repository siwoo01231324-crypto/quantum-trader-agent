---
type: strategy
id: value-quality-kr
name: KRX Value-Quality Screener (BASE)
status: backtest
paradigm: universe-scan
instruments:
- kospi+kosdaq-nonfinancial
market: krx
timeframe: 1d
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-07-08
sharpe_bt: null
sharpe_live: null
mdd_bt: -0.178
annual_return_bt: 0.165
backtest_period: "2019-05-01/2026-05-01"
last_updated: 2026-07-08
summary_ko: |
  KOSPI+KOSDAQ 비금융 종목에서 "싸다 × 재무탄탄 × 실적양호 × 안전마진" 4단계
  게이트를 모두 통과한 종목을 밸류×퀄리티로 랭킹, 상위 10 동일가중 보유.
  더 이상 저평가가 아니게 되면(밸류 정상화) 매도·교체. 재무 펀더멘털 기반.
tags:
- pattern:universe-scan
- value
- quality
- krx
- equity
- fundamental
---

# KRX Value-Quality Screener (BASE)

재무제표 펀더멘털 기반 가치투자 스크리너. 단순 저PER/저PBR 이 아니라 **싸면서 안 망하고 실적 좋은** 종목을 4단계 게이트로 걸러낸 뒤 밸류×퀄리티로 랭킹. Graham(안전마진)·Piotroski(F-Score)·Greenblatt(마법공식) 계열을 종합.

Universe pin-date: **2026-07-08** (현재 상장·유동성 기준 → 생존편향 인정, 단 아래 §생존편향 참조).

## 유니버스

- KOSPI + KOSDAQ 전체 중 **비금융**(매출액 존재 = 은행·보험·증권·금융지주 제외 → 배당 전략 [[dividend-value-kr]] 이 커버).
- 시총 ≥ 100억 KRW **AND** 일 거래대금 ≥ 2억 KRW (잡주·초저유동성 배제).
- 데이터: DART `fnlttSinglAcnt`(재무) + FinanceDataReader(주가·시총) + KRX-DESC(업종).

## 진입 (4단계 게이트 퍼널)

매 리밸 시점(분기, 5/1 기준일)에 FY(Y-1) 사업보고서 재무 + 리밸일 시총으로 팩터 계산:

1. **안전성** (안 망함): 흑자(순익·영업익 > 0) · 부채비율 < max(150%, 업종중앙값) · 유동비율 > 100% · F-Score ≥ 5 (가용 7 컴포넌트).
2. **실적** (좋음): ROE ≥ 8% · 순이익률 > 0 · 매출 역성장 아님.
3. **저평가** (쌈): 0 < PER ≤ max(15, 업종중앙값) · PBR ≤ max(1.5, 업종중앙값).
4. **안전마진** (상승여력): 현재가 < 그레이엄 적정가 √(22.5·EPS·BPS).

- **A: 업종상대 임계** — PER/PBR/부채 게이트는 `max(절대임계, 업종중앙값)`. 조선·건설·유통 등 구조적 고부채/고PBR 업종 우량주를 공정 평가.
- **C: 지주사 NAV 할인** — 지주사(이름 "홀딩스/지주" + 순수지주 셋) 순자산 50% 할인 → PBR·안전마진 뻥튀기 억제.
- 생존자 랭킹: `0.5·value_score + 0.5·quality_score` (백분위) → **top 10**.

look-ahead 방지: FY(Y-1) 재무는 리밸일(5/1) 이전 3~4월 공시 완료. 시총은 리밸일 종가 기준.

## 진입 크기

- Equal-weight 1/N (N=10), 10%씩. 향후 inverse-vol 옵션 (1차 spec 은 동일가중 고정).

## 청산

- **밸류 정상화 exit**: 매월 재스크린 → 보유 종목이 4게이트 통과집합에서 빠지면(재평가로 저평가 아님 OR 펀더 악화) 매도, 그 시점 최상위 미보유 후보로 교체.
- 고정 익절·손절 **없음** (검증 결과 익절·손절·집중·턴어라운드 필터 모두 역효과 — §한계 참조).
- 최대보유 백스톱 ~3년(스태그넌트 강제 회전).

## 훅 소비

- Universe builder: `src/universe/` KOSPI+KOSDAQ 유동성 필터 (신규 정식화 필요, 현 리서치는 `scripts/_value_screener.py`).
- Factor/gate 계산: `scripts/_value_screener.py`(`build_factor_row`·`run_funnel`·`rank_survivors`).
- Bar boundary: 분기 리밸 (3·6·9·12월 말 or 5/1 기준). 일봉 데이터.

## 비용

- KRX 라운드트립 평균 55bp (commission 15 + slippage 20 + 거래세 25/2). 저회전(분기·밸류정상화)이라 비용 영향 작음.
- `apply_cost(returns, positions, "krx")` 활용 예정.

## 리스크 연동

```python
orchestrator.register_strategy("value_quality_kr", strategy)
orchestrator.register_strategy_returns("value_quality_kr", daily_return_series)
```

- `daily_return_series`: index=KRX거래일, 값=바스켓 일수익률(비용 차감 후).
- `intersect_trading_days` 로 crypto/single-ticker 전략과 정렬 후 ENB/CVaR 평가.
- 가치 전략은 정상장·하락장 초과, 광기장 열위 → 크립토·모멘텀 전략과 낮은 상관 기대(분산 효과).

## 백테스트 결과 (2026-07-08, 생존편향 보정)

| Metric | Strategy | KOSPI |
|--------|---------:|------:|
| Ann.Return | **16.5%** | 17.0% |
| MDD | -17.8% | — |
| 종목 승률 | 53% (37/70) | — |
| 종목 평균수익 | +20.7% | — |

- bench: `scripts/_value_screener_backtest.py`(고정 1년) · `_value_screener_backtest_sv.py`(생존편향 보정) · `_value_screener_dynamic_backtest.py`(밸류정상화 exit). 결과 `reports/value_screener_*.json`.
- 밸류정상화 exit ≈ 고정 1년 리밸 (동률) → exit 방식은 세금·회전율 선호로 선택. 밸류정상화 채택(저회전).
- KOSPI 미달은 순전히 2025 광기장(+158%) 탓 — 2025 제외 시 2021 +27%p·2024 +37%p 초과.

## 운영 규칙

- **backtest-only (현 단계)**. 라이브 주문은 KIS/토스(`TOSS_CLIENT_ID`) 배선 후속.
- Universe pin-date 2026-07-08. 분기 재집계로 rotation 후속.
- 주간 스크린 자동화: `scripts/run_screener_weekly.py` + `docker-compose.screener.yml`(매주 월 08:00 + 텔레그램 LIVE 채널 요약).

## 한계 및 후속 작업

- **생존편향은 이 전략에서 미미** — KRX-DELISTING 상폐 277종목 point-in-time 포함 검증 결과, 부도종목 139개 중 스크리너가 매수한 건 1개뿐. 퀄리티 게이트(흑자·부채·F-Score)가 부도 기업을 사전 배제하기 때문(Piotroski 원 목적). 보정 전후 CAGR 격차 미미.
- **역효과 확인된 것들(재시도 금지)**: 익절 로테이션(+35%)은 승자를 잘라 CAGR 6.8%로 폭락. top-5 집중·턴어라운드 필터·손절은 승률 개선 없이 CAGR·MDD 악화. 단순함이 최적.
- 후속: 정식 universe builder, KIS/토스 라이브 배선, PF·기대값 게이트 산출(현 지표는 CAGR/승률).

## 관련 노트

- [[universe-scan-strategy-pattern]] — 본 전략이 따르는 패턴 spec
- [[dividend-value-kr]] — 자매 전략(금융·배당 커버, 유니버스 비중첩)
- [[31-valuation-analysis]] — 가치·배당 지표 정의
- [[26-point-in-time-data]] — look-ahead / 생존편향
- [[20-position-sizing]] — 사이징 이론
- [[19-portfolio-risk]] — 다전략 리스크 통합

## 출처

- Piotroski (2000) — *Value Investing: The Use of Historical Financial Statement Information*, JAR.
- Greenblatt (2006) — *The Little Book That Beats the Market* (마법공식).
- Graham (1949) — *The Intelligent Investor* (안전마진·그레이엄 넘버).
- 본 레포: `docs/background/31-valuation-analysis.md`, `docs/specs/universe-scan-strategy-pattern.md`.
