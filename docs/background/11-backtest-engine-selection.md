---
type: research
id: 11-backtest-engine-selection
name: "백테스트 엔진 선택·비교 (Zipline-reloaded / Backtrader / LEAN / Nautilus / 자체)"
sources: []
---

# 백테스트 엔진 선택·비교 (Zipline-reloaded / Backtrader / LEAN / Nautilus / 자체)

> 목적: 본 프로젝트 MVP의 baseline 백테스트 엔진을 5개 후보(Zipline-reloaded, Backtrader, QuantConnect LEAN, NautilusTrader, 자체 구현) 중 1개로 확정한다.

## 1. 비교표

| 항목 | Zipline-reloaded | Backtrader | LEAN (로컬) | NautilusTrader | 자체 구현 |
|------|----|----|----|----|----|
| 언어 | Python | Python | C# + Python API | Python + Rust core | Python |
| 아키텍처 | event-driven (일/분 bar) | event-driven (multi-timeframe) | event-driven (HFT급) | event-driven, 실거래 동형 | 선택 가능 (vector or event) |
| 라이브 호환 | 공식 broker 미포함 / StrateQueue로 Alpaca·IB 브릿지 | IB·Oanda·Alpaca 내장 | LEAN CLI + QC Cloud paper/live | 라이브 호환 퍼스트클래스 (Binance, IB 등) | 자체 어댑터 필요 |
| KRX 지원 | bundle 커스텀 (일봉 CSV/Parquet 주입) | Feed 클래스 직접 작성 가능 | KRX 공식 없음, 커스텀 데이터로 가능 | 커스텀 어댑터 필요 | 완전 자유 |
| 유지보수 (2025) | 활발 (2025-07 릴리즈, stefan-jansen) | 사실상 정체 (~2018 이후 PR 최소) | 활발 (QC 본사 주도) | 매우 활발 | N/A |
| 커뮤니티 | 중~대 (120+ contributor) | 대 (레거시) | 대 (QC 포럼) | 중, 빠르게 성장 | 해당 없음 |
| 학습 곡선 | 중간 (pipeline 개념) | 낮음 | 중~높음 (C# 환경 구성) | 높음 | 가장 높음 |
| 성능 | 중간 | 낮음 | 높음 | 매우 높음 | 설계 의존 |
| 한국 개인 적용성 | ◎ 일봉 규칙형에 최적 | ○ | △ (도입 비용) | △ (규칙형엔 과함) | △ (타임 투 마켓) |

## 2. 평가 가중치

- 유지보수 상태 30%, 라이브 브릿지 용이성 20%, KRX 일봉 로딩 편의 20%, 학습곡선 15%, 커뮤니티 10%, 성능 5%

## 3. MVP 베이스 선정: **Zipline-reloaded**

**근거**:
- 2025-07 신규 릴리즈로 활발한 유지보수 확인, Python 3.10~3.13 지원.
- `ingest` 커스텀 bundle로 KRX 일봉을 parquet/CSV에서 자연스럽게 주입 가능 (Phase 2 data-lake(#20)와 정합).
- 저빈도 규칙기반 전략(일/주봉, 리밸런스 월 1~4회)에 이벤트 기반 + Pipeline이 최적.
- StrateQueue 같은 라이브 브릿지가 존재해 장기 전환 경로가 있음.
- Backtrader는 개발 정체, LEAN은 C# 런타임 의존성으로 배포/운영 부담, Nautilus는 저빈도 규칙엔 과도한 엔진, 자체 구현은 TTM/신뢰 리스크.
- 향후 미국 주식 확장 시에도 Zipline 기본 bundle(quandl, sharadar 등)로 이주 용이.

## 4. Hello-world 샘플 (선정 엔진)

`samples/zipline_hello.py`:

```python
"""
Zipline-reloaded hello-world: 단일 종목 SMA-cross 백테스트.
실행: zipline run -f samples/zipline_hello.py \
  --bundle quantopian-quandl --start 2022-01-01 --end 2023-12-31 \
  -o out.pickle
"""
from zipline.api import order_target_percent, record, symbol
import zipline


def initialize(context):
    context.asset = symbol("AAPL")
    context.short = 20
    context.long = 60


def handle_data(context, data):
    hist = data.history(context.asset, "price", context.long, "1d")
    short_ma = hist[-context.short:].mean()
    long_ma = hist.mean()
    if short_ma > long_ma:
        order_target_percent(context.asset, 1.0)
    else:
        order_target_percent(context.asset, 0.0)
    record(short=short_ma, long=long_ma)
```

KRX 적용 시 ingest bundle을 `kr_daily`로 등록하고 `symbol("005930")` 형태로 치환. bundle 등록 코드는 `src/backtest/bundles/kr_daily.py`에서 Phase 2에서 구현 예정.

## 5. 리스크 및 완충

- **Zipline의 분단위 이하 지원 제한**: 저빈도 전략이므로 문제 없음. 추후 분봉·실시간 전환 필요 시 NautilusTrader로 이관.
- **데이터 번들 작성 부담**: data-lake(#20)에서 parquet 표준 스키마를 만들면 bundle writer가 단순화된다.
- **라이브 전환 공백**: MVP 이후 StrateQueue 또는 자체 KIS 어댑터로 연결. 라이브 브릿지 자체는 Phase 3 목표.

## 관련 노트

- [[data-lake-schema]] — bundle writer 가 소비할 parquet 스키마
- [[12-validation-protocol]] — walk-forward·CPCV 프로토콜
- [[13-feature-alpha-catalog]] — 백테스트 입력 피처 카탈로그
- [[10-broker-api-comparison]] — 라이브 브릿지 대상 브로커
- [[19-portfolio-risk]] — 백테스트의 VaR/CVaR 산출
- [[20-position-sizing]] — 백테스트에서의 포지션 사이징 규칙

## 출처

- [Zipline-reloaded PyPI](https://pypi.org/project/zipline-reloaded/)
- [stefan-jansen/zipline-reloaded GitHub](https://github.com/stefan-jansen/zipline-reloaded)
- [Going Live with Zipline-Reloaded in 2025 (Medium)](https://medium.com/@samuel.tinnerholm/from-backtest-to-live-going-live-with-zipline-reloaded-in-2025-step-by-step-guide-40e55ca264f1)
- [Backtrader vs NautilusTrader vs VectorBT vs Zipline-reloaded (autotradelab)](https://autotradelab.com/blog/backtrader-vs-nautilusttrader-vs-vectorbt-vs-zipline-reloaded)
- [Python Backtesting Landscape 2026](https://python.financial/)
- [QuantConnect/Lean GitHub](https://github.com/QuantConnect/Lean)
- [LEAN Engine 문서](https://www.quantconnect.com/docs/v2/lean-engine/getting-started)
- [Top Backtesting Software 2025 (chartswatcher)](https://chartswatcher.com/pages/blog/top-backtesting-software-comparison-for-2025)
