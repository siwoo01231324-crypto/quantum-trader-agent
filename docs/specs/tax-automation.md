---
type: spec-architecture
id: tax-automation
name: "세금·회계 자동화 — 명세 (KR 개인, 2026년 세법 기준)"
owner: siwoo
status: draft
tags: []
---

# 세금·회계 자동화 — 명세 (KR 개인, 2026년 세법 기준)

## 1. 목적
체결·배당 내역으로부터 **양도소득세, 증권거래세, 배당소득세**를 자동 산출하고 신고용 자료(CSV)를 생성한다.

## 2. 2026년 KR 개인 세법 요지
### 2-1. 국내 상장주식 양도소득세
- **일반 투자자**: 국내 상장주식 양도차익 **비과세** (소액주주, 장내거래)
- **대주주** (종목당 보유금액 ≥ 50억 원): 과세
  - 양도차익 ≤ 3억 원: **22%** (지방세 포함)
  - 양도차익 > 3억 원: 3억 초과분 **27.5%** (지방세 포함)
- 연 기본공제: **연 250만 원** (대주주 양도차익에서 차감)

### 2-2. 증권거래세 (2026년 인상)
| 시장 | 거래세 | 농어촌특별세 | 합계 (매도가 기준) |
|---|---|---|---|
| KOSPI | 0.05% | 0.15% | **0.20%** |
| KOSDAQ | 0.20% | 0.00% | **0.20%** |
| K-OTC | 0.20% | 0.00% | **0.20%** |

매도 시에만 부과, 매수 시 0%.

### 2-3. 배당소득세
- 원천징수: **15.4%** (소득세 14% + 지방세 1.4%)
- 연 금융소득 2,000만 원 초과 시 종합과세 (본 모듈은 원천징수만 계산, 종합과세는 사용자 책임)

### 2-4. 손익 통산·이월결손
- 동일 과세기간 내 양도차익·차손 통산
- 이월결손금: 해당 과세연도 차손은 **다음 5년간** 이월공제 (양도소득)

## 3. 데이터 모델
```python
@dataclass
class Trade:
    ts: datetime          # 체결 시각
    symbol: str
    market: Market        # KOSPI | KOSDAQ | K_OTC
    side: Side            # BUY | SELL
    qty: int
    price: float          # 1주당 가격(KRW)
    fee: float = 0.0      # 증권사 수수료(거래세 제외)

@dataclass
class Dividend:
    ts: datetime
    symbol: str
    gross: float          # 세전 금액(KRW)
```

## 4. 산출 항목
1. **증권거래세**: 매도 체결별 = price × qty × rate(market)
2. **양도손익**: FIFO 매칭으로 매수가/매도가/수량 계산
3. **양도세**: 대주주 모드일 때 (양도차익 합계 - 250만 원 기본공제) 에 누진 적용
4. **배당소득세**: gross × 15.4%
5. **연간 신고 CSV**: 종목별 매도 라인 (KR 양도소득세 신고서식 호환 컬럼)

## 5. 한계 / 비범위
- 해외주식, ETF/ELS/펀드, 파생상품은 본 모듈 v1 범위 외
- 양도세 신고 대행은 아님 — 보조 자료 생성

## 6. 출처
- 기획재정부 2025 세제개편안 (2025-07-31 발표) — https://moef.go.kr/
- 국세청 양도소득세 안내 — https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=12274&cntntsId=8800
- 헤럴드경제, "내년부터 증권거래세율 0.05%P 상향" — https://biz.heraldcorp.com/article/10627001
- KPMG Korea Tax Brief 2025-08 — https://assets.kpmg.com/content/dam/kpmgsites/kr/pdf/2025/tkc/tax-brief/korea-tax-brief-202508-kor.pdf
- 신&김 법률 뉴스레터, "2025년 세제개편안 II: 주주과세 분야" — https://shinkim.com/kor/media/newsletter/2925
