---
type: research
id: 31-uprich-patent-analysis
name: "업리치 AI 자동매매 특허 (KR 10-2024-0114873) 분석 — 차용·회피 매트릭스"
sources:
  - https://kpat.kipris.or.kr/kpat/biblioa.do?method=biblioFrame&applno=1020240114873
  - https://patents.google.com/patent/KR20260030316A/ko
---

# 업리치 AI 자동매매 특허 (KR 10-2024-0114873) 분석 — 차용·회피 매트릭스

> ⚠️ **법적 고지**: 본 노트는 학술·회피설계 목적 조사이며 변리사 리뷰가 아님.
> 상용 서비스 전 반드시 법무 검토 필수.
> 관련 노트: [[30-market-regime-detection]], [[20-position-sizing]], [[12-validation-protocol]], [[26-point-in-time-data]] — 우리 시스템 강화 및 침해 리스크 제거 목적.
>
> **서지 일부 미확정**: KIPRIS applno URL (https://kpat.kipris.or.kr/kpat/biblioa.do?method=biblioFrame&applno=1020240114873) 접근 불가 (ECONNREFUSED). 아래 서지 정보는 Google Patents (KR20260030316A) 확인 기준이며, 청구항 원문 전문은 미확인 상태임.

---

## 1. 조사 범위

- **대상 특허**: KR 10-2024-0114873 (공개번호 KR20260030316A)
- **조사 목적**: 청구항 1·8 핵심 구성요소 파악 → (a) 우리 시스템 강화 제안, (b) 회피 설계 근거 확보
- **데이터 출처**: Google Patents 공개공보 (2026-03-06 공개). KIPRIS 원문 미확인 — 청구항 발췌는 Google Patents 기반 요약이며 원문과 표현 차이 있을 수 있음.
- **선행 중복조회**: `grep -ri "업리치\|uprich\|10-2024-0114873" docs/background/ docs/specs/` 결과 0건 → 신규 노트 필요 확인.

---

## 2. 특허 서지

| 항목 | 내용 |
|------|------|
| 출원번호 | KR 10-2024-0114873 |
| 공개번호 | KR20260030316A |
| 출원인 | 이보람 (Lee Bo-ram) |
| 출원일 | 2024-08-27 |
| 공개일 | 2026-03-06 |
| 법적 상태 | 심사 중 (Pending) — 등록 미확정 |
| 기술 분야 | AI 기반 암호화폐 자동매매 시스템 |
| 주요 기술 키워드 | BTC dominance, 사용자 리스크 프로필, 알트코인 안정성 등급, 양방향 헤징 |

---

## 3. 청구항 핵심 요약

> 원문 과다복제 금지 원칙에 따라 구성요소 카탈로그 + 기능 요약으로 기술.

### 3.1 청구항 1 — 독립 시스템 청구항 (6개 구성요소)

| 구성요소 | 기능 요약 |
|---------|----------|
| (a) 사용자 리스크 수용지수 산출기 (R1) | 매매 성향, 포트폴리오 구성, 부채 현황, 상환 비율을 입력으로 사용자 개인 리스크 허용치 스칼라 R1 계산 |
| (b) 가상자산 리스크 지수 산출기 (R2) | 웹크롤링 소셜 감성 + 가격 지표 + 거시경제 지표를 합산한 시장 리스크 지수 R2 계산 |
| (c) BTC dominance 추세 지표 산출기 (C) | 수식 `C = a × Bdominance × T` (a=조정계수, Bdominance=비트코인 시가총액 비중, T=추세 팩터) |
| (d) 알트코인 안정성 등급 분석기 | 시가총액·거래량·온체인 개발 활성도 기반 알트코인 A~F 등급 산출 |
| (e) 상관관계 분석 모듈 | 알트코인-비트코인 간 가격 상관계수 산출 (방법론 미특정) |
| (f) 자동매매 전략 제공기 | 지표 I = (R1 - R2) × C 기반 ML 모델로 양방향 헤징 전략 실행 |

**핵심 공식**: 자동매매 리스크 지수 `I = (R1 - R2) × C`

### 3.2 청구항 8 — 전략 실행 종속항

두 지표(I와 추가 지표)가 모두 0을 초과할 때, 비트코인 현물 매수 + 낮은 등급 알트코인 선물 공매도를 동시 실행하여 지표 크기에 비례한 포지션 조정 (통계적 차익거래 최적화 목적).

---

## 4. 💎 강화 제안 (Strengthening Proposals)

> **이 섹션이 본 조사의 핵심 산출물**. 각 인사이트별로 우리 코드에 즉시 적용 가능한 제안을 기술한다.

---

### 💎 강화 제안 #1 — 사용자 리스크 프로필(R1) → `vol_target()` 타겟 변동성 파라미터화

**적용 대상**: `src/risk/sizing.py::vol_target(target_annual: float)`

**접목 방법**:
현재 `vol_target()` 은 `target_annual=0.10` 을 고정 기본값으로 사용한다. 업리치 특허의 R1(사용자 리스크 수용지수) 개념을 차용해, 호출자가 사용자 프로필 점수(`risk_score ∈ [0.0, 1.0]`)를 전달하면 `target_annual` 을 동적으로 결정하는 팩토리 함수를 `sizing.py` 에 추가한다:

```python
def user_risk_vol_target(
    risk_score: float,           # 0.0 = 보수적, 1.0 = 공격적
    vol_floor: float = 0.05,
    vol_ceil: float = 0.20,
) -> float:
    """risk_score 를 annualized vol target 으로 선형 매핑."""
    return vol_floor + (vol_ceil - vol_floor) * risk_score
```

`StrategyOrchestrator.evaluate_order()` 호출 전에 이 값을 `Snapshot` 에 주입하면, 동일 전략 신호에서도 사용자별로 서로 다른 포지션 크기가 산출된다. R1 의 입력변수(부채비율, 상환능력)는 우리 시스템이 다루는 영역이 아니므로 **`risk_score` 는 외부에서 주입** 하는 단순 스칼라로 충분하다 — 특허 청구항의 복잡한 R1 산출 로직 전체를 구현하지 않으므로 침해 구성요소 (a) 를 회피한다.

**기대 효과**: 단일 vol target 에서 사용자별 맞춤 리스크 버킷(보수·중립·공격)으로 전환. 멀티 계좌 운용 시 계좌별 drawdown 분산 개선.

**저비용 검증 경로**: `tests/test_sizing.py` 에 `risk_score=[0.0, 0.5, 1.0]` 에 대한 파라메트릭 테스트 3건 추가 → `vol_target()` 반환값이 `[0.05, 0.125, 0.20]` 범위에 드는지 assert.

---

### 💎 강화 제안 #2 — BTC dominance 레짐 스위치 → `RegimeClassifier` 입력 확장

**적용 대상**: `src/` (신규 모듈 `src/factors/btc_dominance.py`) + [[30-market-regime-detection]] Phase 1 Rule-based 분류기

**접목 방법**:
[[30-market-regime-detection]] §5.1 의 `RegimeClassifier.classify()` 는 현재 `ewma_sigma_20`, `adx_14`, `vol_percentile_252d` 만 입력으로 받는다. 업리치 특허의 `C = a × Bdominance × T` 개념을 변형해 BTC dominance 를 **레짐 입력 축** 으로 추가한다.

구체적으로 `src/factors/btc_dominance.py` 에 다음을 구현한다:

```python
def btc_dominance_signal(
    btc_mcap: float,
    total_mcap: float,
    dominance_ma20: float,
) -> float:
    """BTC dominance 추세 방향 [-1, 1].
    dominance > ma20: BTC 강세 (+), < ma20: 알트 강세 (-)
    """
    dominance = btc_mcap / total_mcap if total_mcap > 0 else 0.5
    return float(np.sign(dominance - dominance_ma20))
```

이 신호를 `RegimeClassifier` 의 `classify()` 메서드에 `btc_dominance_trend` 필드로 추가해, BTC dominance 상승 국면에서는 모멘텀 전략 가중을 높이고 하락 국면(알트 시즌)에서는 알트코인 비중을 제한하는 의사결정 매트릭스를 확장한다. 특허의 `C = a × Bdominance × T` 수식 그대로가 아니라 **이진 부호 신호**로 단순화하므로 구성요소 (c) 를 침해하지 않는다.

**기대 효과**: 알트 시즌 vs BTC 지배 국면에서 포트폴리오 내 자산 배분 자동 조정. [[30-market-regime-detection]] §4 의사결정 매트릭스에 dominance 축 추가로 체제 분류 세분화.

**저비용 검증 경로**: BTC dominance 과거 데이터(CoinGecko public API) 60일 다운로드 → dominance 상승/하락 구간에서 BTCUSDT 모멘텀 전략의 Sharpe 비교 스크래치 노트북 1개.

---

### 💎 강화 제안 #3 — 알트코인 안정성 등급 → 유니버스 필터 모듈

**적용 대상**: 신규 모듈 `src/universe/stability_grade.py` (후속 이슈 후보 #1)

**접목 방법**:
현재 우리 시스템에는 알트코인 거래 유니버스를 필터링하는 전용 모듈이 없다. 업리치 특허의 A~F 안정성 등급 개념을 차용해, 시가총액·30일 평균 거래량·GitHub 커밋 활성도(온체인 대리지표) 세 가지를 입력으로 받는 단순 등급 분류기를 별도 모듈로 구현한다.

```python
class StabilityGrade:
    """A(최우수)~F(투기) 6단계 등급. 각 기준 독립 배점 합산."""

    WEIGHTS = {"mcap_score": 0.4, "volume_score": 0.4, "dev_score": 0.2}

    def grade(self, mcap_usd: float, vol_30d_usd: float, gh_commits_90d: int) -> str:
        score = (
            self._mcap_score(mcap_usd) * self.WEIGHTS["mcap_score"]
            + self._vol_score(vol_30d_usd) * self.WEIGHTS["volume_score"]
            + self._dev_score(gh_commits_90d) * self.WEIGHTS["dev_score"]
        )
        return "ABCDEF"[min(5, int((1 - score) * 6))]
```

이 등급을 `StrategyOrchestrator` 의 유니버스 필터 단계에 연결해, D등급 이하 알트코인은 매수 진입 차단 or 포지션 크기를 `fractional_kelly(k=0.25)` 로 강제 축소한다. 특허 청구항 (d) 의 세부 입력변수(온체인 개발 활성도 정의)를 다르게 구성하고 A~F 등급 정의도 자체 기준으로 재정의하므로 침해를 회피한다.

**기대 효과**: 유동성 낮은 알트코인 포지션 진입 방지 → 슬리피지·유동성 리스크 감소. 데이터 품질 기반 자동 유니버스 축소.

**저비용 검증 경로**: CoinGecko `/coins/markets` API 로 상위 200개 알트코인 등급 산출 → A-C 등급 비율 체크 (기대: 상위 30% 이내). 단위 테스트 3건 (경계값: 초대형 코인=A, 마이크로캡=F, 중간=C).

---

### 💎 강화 제안 #4 — 복합 리스크 지수(R2) → `PortfolioRiskReport` 확장 필드

**적용 대상**: `src/risk/portfolio.py::PortfolioRiskReport` + `src/portfolio/orchestrator.py::StrategyOrchestrator.refresh_portfolio_risk()`

**접목 방법**:
업리치의 R2(소셜 감성 + 가격 + 거시경제 합산 지수) 개념에서 **가격 지표 컴포넌트만** 차용한다. `PortfolioRiskReport` 에 `fear_greed_proxy: float` 필드를 추가하고, `refresh_portfolio_risk()` 에서 이를 산출한다:

```python
# PortfolioRiskReport 에 추가
fear_greed_proxy: float = Field(0.5, ge=0.0, le=1.0,
    description="0=극단 공포, 1=극단 탐욕. 가격 기반 단순 추정.")
```

산출 방법: `(현재가격 / 52주_최고가)` 를 정규화한 단순 지수 — 소셜 크롤링 없이 가격 데이터만 사용하므로 특허 구성요소 (b) 의 웹크롤링 소셜 감성 요소를 의도적으로 제외. 이 값이 임계(0.2 이하=극단 공포)일 때 `evaluate_order()` 에서 신규 매수 차단 플래그를 반환하게 `dsl.py` 를 확장한다.

**기대 효과**: 시장 극단 공포 구간에서 자동 매수 보류 → 급락장 진입 리스크 완화. 기존 CVaR·ENB 외에 시장 심리 차원 리스크 신호 추가.

**저비용 검증 경로**: 2020-03 코로나 급락, 2022-11 FTX 붕괴 구간에서 `fear_greed_proxy` 값이 0.2 이하로 내려가는지 히스토리컬 백테스트 단일 플롯으로 확인.

---

### 💎 강화 제안 #5 — 양방향 헤징 신호 → `fractional_kelly` 에 방향성 스케일 추가

**적용 대상**: `src/risk/sizing.py::fractional_kelly()` + `src/backtest/strategies/momo_btc_v2.py`

**접목 방법**:
청구항 8의 "두 지표 모두 양수일 때 BTC 현물 롱 + 알트 선물 숏" 페어 전략 개념에서 **신호 방향 일치도에 따른 kelly 배율 조정** 아이디어를 차용한다. 현재 `fractional_kelly(full_kelly, k=0.5)` 는 단방향 신호에 고정 k를 적용하지만, 복수 지표가 같은 방향을 가리킬 때 k를 상향하는 `consensus_kelly()` 함수를 추가한다:

```python
def consensus_kelly(
    full_kelly: float,
    signal_agreement: float,  # 0.0~1.0: 지표 간 방향 일치도
    k_base: float = 0.5,
    k_max: float = 0.75,
) -> float:
    """지표 합의도가 높을수록 kelly 배율을 k_base~k_max 로 선형 상향."""
    k = k_base + (k_max - k_base) * signal_agreement
    return fractional_kelly(full_kelly, k)
```

`momo_btc_v2.py` 의 신호 생성 단계에서 momentum + vol regime + btc_dominance 세 신호의 방향 일치도를 `signal_agreement` 로 전달한다.

**기대 효과**: 지표 합의 구간에서 포지션 확대, 불일치 구간에서 자동 축소 → 신호 품질 기반 동적 사이징. 기존 고정 Half Kelly 대비 합의 구간 수익 개선 가능.

**저비용 검증 경로**: `momo_btc_v2` 백테스트에서 `k=0.5` 고정 vs `consensus_kelly` 비교 → Sharpe, max drawdown, 평균 포지션 크기 비교 리포트 1개.

---

## 5. 회피 필요 영역

### 5.1 청구항 1 구성요소 카탈로그 및 대체 설계

| 구성요소 | 특허 정의 | 우리 대체 설계 | 회피 근거 |
|---------|----------|--------------|---------|
| (a) R1 산출기 | 매매성향·포트폴리오·부채·상환비율 → 스칼라 | 외부 주입 `risk_score` 스칼라 (계산 로직 없음) | 입력변수·산출로직 완전 다름 |
| (b) R2 산출기 | 소셜크롤링 + 가격 + 거시 합산 | 가격 기반 `fear_greed_proxy` 단일 지표 (소셜 크롤링 제외) | 구성요소 의도적 생략 |
| (c) BTC dominance C | `C = a × Bdominance × T` 수식 | 이진 부호 신호 `sign(dominance - ma20)` | 수식·파라미터 완전 다름 |
| (d) 알트 안정성 등급 | 시총·거래량·온체인 A~F | 자체 가중 배점 + 자체 등급 임계값 | 등급 정의·배점 기준 다름 |
| (e) 상관관계 모듈 | 알트-BTC 상관 계산 | 기존 `shrinkage_covariance()` (LW 수축) 사용 | 방법론 다름 |
| (f) 전략 제공기 | `I = (R1-R2)×C` ML 실행 | `I` 수식 미사용; Kelly+vol target 독립 신호 | 핵심 공식 불사용 |

**핵심 회피 원칙**: 청구항 1의 6개 구성요소를 **하나의 통합 시스템으로** 구현하지 않는다. 각 개념을 독립 모듈로 분리하고, 특허의 결합 공식 `I = (R1-R2)×C` 를 사용하지 않는다.

### 5.2 청구항 8 회피

"두 지표 모두 양수 → BTC 롱 + 알트 숏 동시 실행" 조건을 그대로 구현하지 않는다. 우리 `consensus_kelly()` 는 포지션 **크기** 를 조정할 뿐, 특허의 **페어 트레이딩 조건 (BTC 현물 + 알트 선물 동시)** 을 조건부로 트리거하지 않는다.

---

## 6. 우리 코드 연결고리

| 특허 개념 | 우리 코드 | 현재 상태 |
|----------|----------|---------|
| R1 사용자 리스크 프로필 | `src/risk/sizing.py::vol_target()` | 구현 완료 (#69), `target_annual` 고정값 |
| R2 복합 리스크 지수 | `src/risk/portfolio.py::PortfolioRiskReport` | CVaR·ENB 있음, 감성 지표 없음 |
| BTC dominance 레짐 | [[30-market-regime-detection]] Phase 1 | Rule-based 미구현 (Phase 1 예정) |
| 알트코인 안정성 등급 | 미존재 | 후속 이슈 후보 #1 |
| 상관관계 분석 | `src/risk/portfolio.py::shrinkage_covariance()` | 구현 완료 (#70) |
| 양방향 헤징 전략 | `src/backtest/strategies/momo_btc_v2.py` | 단방향 모멘텀 전략 |
| 포트폴리오 오케스트레이터 | `src/portfolio/orchestrator.py::StrategyOrchestrator` | 스텁 구현 완료 (#70) |

---

## 7. 우리 레포 강화 로드맵

| # | 강화 제안 | 적용 대상 파일/함수 | 우선순위 | 예상 난이도 | 의존 이슈 |
|---|---------|-------------------|---------|-----------|---------|
| P1 | 사용자 리스크 프로필 vol target 파라미터화 | `src/risk/sizing.py::vol_target()` 확장 | High | Low (1~2일) | #69 완료 |
| P2 | BTC dominance 레짐 입력 추가 | `src/factors/btc_dominance.py` 신규 | High | Medium (3~5일) | [[30-market-regime-detection]] Phase 1 |
| P3 | 알트코인 안정성 등급 필터 | `src/universe/stability_grade.py` 신규 | Medium | Medium (3~5일) | 없음 (독립) |
| P4 | 복합 리스크 지수 R2 대안 필드 | `src/risk/portfolio.py::PortfolioRiskReport` 확장 | Medium | Low (1일) | #70 완료 |
| P5 | 합의 기반 동적 Kelly 배율 | `src/risk/sizing.py::consensus_kelly()` 신규 | Low | Low (1일) | #69 완료 |

---

## 8. 우리 차별점 (업리치 특허 대비)

업리치 특허에 **없는** 우리 고유 요소:

| 우리 기능 | 관련 노트 | 설명 |
|---------|---------|-----|
| Walk-forward 검증 프로토콜 | [[12-validation-protocol]] | 사전 등록·CPCV·WFV 엄격 분리 |
| Point-in-Time 데이터 무결성 | [[26-point-in-time-data]] | 룩어헤드 바이어스 방지 파이프라인 |
| Kill-switch + 자동 롤백 | [[12-validation-protocol]] §4 | 6개월 Sharpe 괴리 시 자동 중지 |
| 한국 거래세 자동화 | 별도 모듈 | KRX 거래세 0.20% 실시간 반영 |
| RDF 온톨로지 동기화 | `docs/ontology/trading.ttl` | 지식볼트 연결 — 업리치 없음 |

---

## 9. 후속 이슈 후보

### 후속 이슈 #1 — 알트코인 안정성 등급 분류기 구현

- **제목**: `feat: 알트코인 안정성 등급 필터 (StabilityGrade A~F)` 
- **요약**: `src/universe/stability_grade.py` 신규 모듈. 시가총액·30일 거래량·개발 활성도 기반 6단계 등급 산출. D 이하 종목 유니버스 자동 제외 또는 포지션 사이즈 Half Kelly 강제 적용.
- **연결 노트**: 본 노트 (31-uprich-patent-analysis)
- **근거 청구항**: 특허 청구항 1-(d) 알트코인 안정성 등급 분석기 개념 차용, 입력변수·등급기준 자체 재정의

### 후속 이슈 #2 — BTC dominance 레짐 신호 팩터 구현

- **제목**: `feat: BTC dominance 레짐 팩터 (src/factors/btc_dominance.py)`
- **요약**: [[30-market-regime-detection]] Phase 1 Rule-based 분류기에 BTC dominance 추세 축 추가. `btc_dominance_signal()` 함수 구현 + `RegimeClassifier.classify()` 확장.
- **연결 노트**: [[30-market-regime-detection]] Phase 1, 본 노트
- **근거 청구항**: 특허 청구항 1-(c) BTC dominance 추세 지표 개념 차용, 수식은 완전 재정의 (이진 신호로 단순화)

---

## 10. 종합 매트릭스

| 특허 구성요소 | 강화 제안 | 회피 포인트 | 연결 코드 경로 |
|-------------|---------|-----------|-------------|
| (a) R1 사용자 리스크 | 💎 P1: vol_target 파라미터화 | 산출 로직 미구현, 외부 주입 스칼라 | `src/risk/sizing.py::vol_target()` |
| (b) R2 복합 지수 | 💎 P4: fear_greed_proxy 필드 | 소셜 크롤링 제외, 가격 지표만 | `src/risk/portfolio.py::PortfolioRiskReport` |
| (c) BTC dominance C | 💎 P2: dominance 레짐 축 추가 | 수식 `C=a×Bdominance×T` 미사용 | `src/factors/btc_dominance.py` (신규) |
| (d) 알트 안정성 등급 | 💎 P3: StabilityGrade 모듈 | 등급 정의·배점 자체 기준 | `src/universe/stability_grade.py` (신규) |
| (e) 상관관계 분석 | (기존 충족) | LW shrinkage 별도 방법론 | `src/risk/portfolio.py::shrinkage_covariance()` |
| (f) 전략 실행 I=(R1-R2)×C | 💎 P5: consensus_kelly() | I 수식 미사용 | `src/risk/sizing.py::consensus_kelly()` (신규) |
| 청구항 8 페어 전략 | 방향성 kelly 배율 | 페어 트리거 조건 미구현 | `src/backtest/strategies/momo_btc_v2.py` |

---

## 출처

- KIPRIS (접근 불가 — ECONNREFUSED): https://kpat.kipris.or.kr/kpat/biblioa.do?method=biblioFrame&applno=1020240114873
- Google Patents (확인 완료): https://patents.google.com/patent/KR20260030316A/ko
- 출원번호: KR 10-2024-0114873
- 공개번호: KR20260030316A
- 출원인: 이보람 (Lee Bo-ram)
- 공개일: 2026-03-06
- 관련 노트: [[30-market-regime-detection]], [[20-position-sizing]], [[12-validation-protocol]], [[26-point-in-time-data]]
