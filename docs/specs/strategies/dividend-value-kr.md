---
type: strategy
id: dividend-value-kr
name: KRX Dividend-Value Screener
status: backtest
paradigm: universe-scan
instruments:
- kospi+kosdaq-dividend
market: krx
timeframe: 1d
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-07-08
sharpe_bt: null
sharpe_live: null
mdd_bt: -0.222
annual_return_bt: 0.171
backtest_period: "2019-05-01/2026-05-01"
last_updated: 2026-07-08
summary_ko: |
  BASE([[value-quality-kr]])가 매출액 없어 제외하는 금융·배당주를 커버하는 자매 전략.
  저PBR × 고배당수익률 × 배당연속 × 흑자 종목을 이익창출력·지속성·주주환원 3축으로
  점수화(퀄리티 틸트), 상위 보유. 은행·보험·금융지주 포함. 배당가치 투자 방식.
tags:
- pattern:universe-scan
- value
- dividend
- financial
- krx
- equity
---

# KRX Dividend-Value Screener

[[value-quality-kr]] 이 매출액 기준으로 제외하는 **금융·배당주**(은행·보험·금융지주 등)를 커버하는 자매 전략. 저PBR·고배당·배당연속 우량주를 이익창출력·이익지속성·주주환원 3축으로 점수화(장기 배당가치 투자 방식). 두 전략은 유니버스가 겹치지 않아 상호보완 2슬리브를 구성.

Universe pin-date: **2026-07-08** (현재 상장 기준 → 생존편향 인정, 단 §생존편향 참조).

## 유니버스

- KOSPI + KOSDAQ 전체 (금융 **포함** — BASE 와 반대). 시총 ≥ 100억 KRW.
- 금융주는 매출액 없어 PSR/EV 생략, **PBR/PER/ROE + 배당**만 사용.
- 데이터: DART `fnlttSinglAcnt`(순익·자본) + DART `alotMatter`(배당수익률·DPS·배당성향) + FinanceDataReader(주가).

## 진입 (3축 점수표 + 게이트)

매 리밸 시점(분기, 5/1 기준일)에 FY(Y-1) 재무·배당으로:

**게이트 (통과 필수)**:
- 3년 연속 흑자 (이익 지속성)
- 배당 실시 (DPS > 0), 배당수익률 = 최근 DPS / 현재가 ≥ 2%
- PBR ≤ 1.5, ROE ≥ 5%, 배당성향 ≤ 80% (과배당·역성장 배제)
- 배당수익률 > 20% 는 파싱 글리치로 배제

**3축 점수 랭킹 (B 퀄리티 틸트, 검증 채택)**:
- ① 이익 창출력: ROE(30%) + 저PER(15%)
- ② 이익 지속성: 배당 연속성 · 3년 흑자
- ③ 주주환원: 배당수익률(35%) + 저PBR(20%)
- → top N 보유.

look-ahead 방지: FY(Y-1) 재무·배당은 리밸일(5/1) 이전 공시 완료.

## 진입 크기

- Equal-weight 1/N. 향후 배당수익률 가중 옵션.

## 청산

- **밸류 정상화 exit** (BASE 와 동일): 더 이상 저평가·고배당이 아니게 되면(재평가) 매도, 다른 저평가 배당주로 교체. 오일전문가 인터뷰(장기 배당투자, 12년 연 24%)의 "매수 기준의 반대로 매도" 원칙.
- 지주사 NAV 할인(C) **미적용** — 저PBR 고배당 지주사는 진짜 가치(백테스트 확인).

## 훅 소비

- 배당 데이터: `scripts/_dividend_value.py`(`fetch_dividend`·`div_history`, DART alotMatter 캐시 `data/cache/dart/div/`).
- Factor/gate: `scripts/_dividend_value.py`(`build_rows`·`gate_fail`·`score`).
- Bar boundary: 분기 리밸. 일봉.

## 비용

- KRX 라운드트립 평균 55bp. 저회전(분기·밸류정상화) + 배당 재투자로 비용 영향 작음.

## 리스크 연동

```python
orchestrator.register_strategy("dividend_value_kr", strategy)
orchestrator.register_strategy_returns("dividend_value_kr", daily_return_series)
```

- `daily_return_series`: index=KRX거래일, 값=바스켓 일수익률(배당 포함, 비용 차감 후).
- BASE(비금융)와 유니버스 비중첩 → 합산 시 분산. crypto/momentum 과도 낮은 상관 기대.

## 백테스트 결과 (2026-07-08, 생존편향 보정·금융 포함)

| Metric | B 퀄리티(채택) | A 딥밸류(기각) | KOSPI |
|--------|---------:|---------:|------:|
| Ann.Return | **17.1%** | 14.6% | 17.0% |
| MDD | -22.2% | -24.8% | — |
| 종목 승률 | 60% (42/70) | 59% | — |
| 최대 수익 | +212% | +163% | — |

- bench: `scripts/_dividend_value_backtest.py` (틸트 A vs B). 결과 `reports/dividend_value_backtest.json`.
- **B(퀄리티·배당) > A(딥밸류)**: 초저PBR 마이크로캡(A)은 부실종목 부도로 상방 갉아먹힘, 퀄리티+배당(B)이 재평가 승자를 더 잘 골라 대박도 더 큼. 딥리서치 "밸류+퀄리티 > 밸류 단독" 일치.
- KOSPI 초과(유일) — 위험조정 압승(MDD 더 낮음, 2025 광기장 포함에도).

## 운영 규칙

- **backtest-only (현 단계)**. 라이브 주문은 KIS/토스(`TOSS_CLIENT_ID`) 배선 후속.
- Universe pin-date 2026-07-08. 분기 재집계.

## 한계 및 후속 작업

- 생존편향: 배당 전략은 오히려 더 강건 — 배당 지급 기업은 부도율 낮음(3년 흑자+배당 게이트가 부실 배제).
- **오늘 은행은 최고 바겐 아님** — 밸류업 랠리로 이미 상승(배당수익률 6%→3%, 삼성화재 PBR 1.92 탈락). 스크린이 정직하게 반영. 저PBR 지주사(삼양홀딩스·KPX홀딩스·GS)가 현 상위.
- 후속: 자사주 매입/소각 신호 추가(DART 별도 공시), 정식 코드/테스트, 라이브 배선.

## 관련 노트

- [[value-quality-kr]] — 자매 전략(비금융 밸류)
- [[universe-scan-strategy-pattern]] — 패턴 spec
- [[31-valuation-analysis]] — 배당 지표(배당수익률·배당성향·배당귀족) 정의
- [[20-position-sizing]] — 사이징
- [[19-portfolio-risk]] — 다전략 리스크 통합

## 출처

- 오일전문가 인터뷰(YouTube SskCOZ0yi9g, 2026) — 장기 배당가치 투자, 3축(이익창출력·지속성·주주환원) 점수표, 12년 연 24%.
- 본 레포: `docs/background/31-valuation-analysis.md` §4(배당 지표), `docs/specs/universe-scan-strategy-pattern.md`.
