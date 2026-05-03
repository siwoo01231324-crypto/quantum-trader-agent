---
type: research
id: 37-orderflow-microstructure-signals
name: "오더플로우·ICT 시그널 카탈로그 평가 (sleeve B 알파 보강)"
created: 2026-04-27
sources:
  - "Kyle, A.S. (1985). Continuous Auctions and Insider Trading. Econometrica, 53(6), 1315–1335. https://doi.org/10.2307/1913210"
  - "Easley, D. & O'Hara, M. (1992). Time and the Process of Security Price Adjustment. Journal of Finance, 47(2), 577–605. https://doi.org/10.1111/j.1540-6261.1992.tb04402.x"
  - "Easley, D., López de Prado, M., O'Hara, M. (2012). Flow Toxicity and Liquidity in a High-Frequency World. Review of Financial Studies, 25(5), 1457–1493. https://doi.org/10.1093/rfs/hhs053"
  - "Roll, R. (1984). A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market. Journal of Finance, 39(4), 1127–1139. https://doi.org/10.1111/j.1540-6261.1984.tb03897.x"
  - "Bouchaud, J.-P., Farmer, J.D., Lillo, F. (2009). How Markets Slowly Digest Changes in Supply and Demand. Handbook of Financial Markets: Dynamics and Evolution, ch.2. https://doi.org/10.1016/B978-012374258-2.50006-3"
  - "Cont, R., Kukanov, A., Stoikov, S. (2014). The Price Impact of Order Book Events. Journal of Financial Econometrics, 12(1), 47–88. https://doi.org/10.1093/jjfinec/nbt003"
  - "Kim, O. & Stoll, H.R. (1993). Trading costs, adverse selection, and the role of the specialist. Journal of Financial Economics, 33(3), 363–394. https://doi.org/10.1016/0304-405X(93)90026-E"
  - "Menkhoff, L. (2010). The Use of Technical Analysis by Fund Managers: International Evidence. Journal of Banking & Finance, 34(11), 2573–2586. https://doi.org/10.1016/j.jbankfin.2010.04.014"
  - "Osler, C.L. (2003). Currency Orders and Exchange Rate Dynamics: An Explanation for the Predictability of Direction Changes. Journal of Finance, 58(5), 1791–1819. https://doi.org/10.1111/1540-6261.00588"
---

# 오더플로우·ICT 시그널 카탈로그 평가 (sleeve B 알파 보강)

> 이슈 #145. 본 노트는 ICT(Inner Circle Trader) 개념 6종을 학술 시장 미시구조 문헌으로 매핑하고, sleeve B(BTC 공격 위성) 알파 보강 후보 3종을 추천한다. 기존 [[43-orderbook-flow-features]](OBI/OFI/Microprice/Hawkes) 와의 중복을 피해 ICT 고유 개념을 중점적으로 다룬다.

관련 노트: [[07-market-microstructure-basics]] · [[13-feature-alpha-catalog]] · [[19-portfolio-risk]] · [[35-meta-labeling-lopez-de-prado]] · [[34-patents-execution-algos]] · [[36-monthly-10pct-feasibility]] · [[43-orderbook-flow-features]]

---

## §1 배경 및 범위

### 1.1 이슈 #145 위치

[[36-monthly-10pct-feasibility]] §7 결정 (d) Sleeve allocation 에서 sleeve B(공격 위성)의 알파 보강이 후속 조치로 명시됐다. 본 이슈는 **ICT(Inner Circle Trader) 개념 6종**을 시장 미시구조 학술 이론으로 매핑하여, retail 커뮤니티에서 출발한 개념 중 실제 학술 근거가 있는 것과 없는 것을 구분한다.

### 1.2 기존 커버리지와의 관계

[[43-orderbook-flow-features]] 는 OBI/OFI/Microprice/Hawkes intensity/Kyle λ 를 이미 다룬다. 본 노트는 그 내용을 중복하지 않고, ICT 고유 6개 개념(liquidity sweep, order block, FVG, breaker, CVD, VPIN) 을 추가로 분석한다. VPIN 은 [[43-orderbook-flow-features]] 8번 항목에서 OFI 의 친척으로 언급만 됐으며, 본 노트에서 상세히 다룬다.

---

## §2 ICT 6종 학술 매핑

### 2.1 Liquidity Sweep (유동성 스윕)

**ICT 정의**: 가격이 이전 고점/저점 너머로 일시 돌출하여 스탑 주문을 체결시킨 뒤 반전하는 패턴. "스탑 사냥(stop hunt)" 이라고도 불림.

**학술 매핑 — Kyle (1985) λ 와 Informed Trader Stop Harvesting**

Kyle (1985) 의 연속 경매 모델에서 내부자(informed trader) 는 비밀 정보를 분할 주문으로 시장에 숨기며 실행한다. Liquidity sweep 의 학술적 설명은 두 갈래다:

1. **대형 기관의 순서 압력(order flow pressure)**: 기관은 유동성이 적은 스탑 클러스터 구간(이전 고점/저점 직후)을 알고 있다. 해당 구간을 시장가로 돌파하면 스탑 주문(지정가 반대방향)이 상대방 유동성이 되어 대형 주문을 낮은 슬리피지로 실행 가능하다. Osler (2003) 는 FX 시장에서 스탑 클러스터가 이전 고/저점 근처에 집중됨을 실증했다 (방향성 변화 예측력 확인, Spearman ρ > 0.4, p < 0.01).

2. **유동성 공급자의 가격 발견**: 스탑 소화 후 주문 흐름이 역전되는 현상은 Bouchaud et al. (2009) 의 "시장이 공급/수요 변화를 천천히 소화" 메커니즘으로도 설명 가능하다.

**학술 근거 수준**: 중간. FX 스탑 클러스터 실증(Osler 2003)은 존재하지만, 암호화폐 시장의 BTC에서 동일 패턴이 통계적으로 유의한지는 별도 검증 필요.

**데이터 요건**: 분봉 OHLCV + 최근 N봉 고점/저점 (Binance aggTrade OK, KIS 분봉 가능).

---

### 2.2 Order Block (오더 블록)

**ICT 정의**: 대형 기관이 포지션을 누적한 것으로 추정되는 캔들(보통 강한 단방향 이동 직전 마지막 반대 방향 캔들). 이후 가격이 해당 구간으로 복귀할 때 지지/저항 역할.

**학술 매핑 — Easley & O'Hara (1992) 정보 기반 거래 + Kim & Stoll (1993) 기관 블록 매매**

Easley & O'Hara (1992) 의 순차 거래 모델(sequential trade model): 내부자가 존재하면 특정 시간대의 거래량·방향이 정보를 내포하며, 시장 조성자는 이를 호가 스프레드로 보상받는다. 연속적으로 동방향 매수/매도가 발생하면 시장 조성자는 호가를 불리하게 이동시킨다 — 이것이 "Order Block" 이후의 강한 이동을 만든다.

Kim & Stoll (1993) 은 블록 매매(institutional block trades) 이후 가격이 반등하는 "가격 압력 가설" 을 실증했다. 블록 이후 반전 포인트가 order block 의 지지/저항 역할과 일치한다.

**학술 근거 수준**: 중간. 기관 누적 지점의 지지/저항 효과는 간접적으로 지지되나, ICT 가 제시하는 정확한 캔들 선택 규칙(마지막 반대방향 캔들)에 대한 직접 실증은 부재.

**데이터 요건**: 분봉 OHLCV + 거래량 (Binance aggTrade OK, KIS 분봉 가능).

---

### 2.3 Fair Value Gap (FVG, 공정가치 갭)

**ICT 정의**: 3개 연속 캔들에서 첫 번째 캔들의 고점(또는 저점)과 세 번째 캔들의 저점(또는 고점) 사이에 빈 구간. "인밸런스(imbalance)" 라고도 불리며, 가격이 이 구간으로 복귀해 "채운다"는 예측.

**학술 매핑 — Roll (1984) 비드-애스크 바운스 + 시장 조성 불균형**

Roll (1984) 의 암묵적 스프레드 모델은 가격이 비드와 애스크 사이를 튀는(bounce) 현상을 설명한다. FVG 는 이 개념의 확장으로 해석 가능하다: 급격한 단방향 주문 압력이 정상 시장 조성 메커니즘을 일시 교란할 때, 일부 가격 구간에서 양방향 체결이 이루어지지 않은 "거래 공백" 이 생긴다. Cont et al. (2014) 의 OFI 틀에서 이 구간은 OFI 가 극단적으로 한쪽으로 치우친 순간과 대응한다.

단, "가격이 반드시 FVG 를 채운다"는 ICT 주장의 학술 근거는 미약하다. 단기 평균 회귀(mean reversion) 문헌([[30-market-regime-detection]] §2 HMM 참조) 에서 급격한 이탈 후 복귀 경향이 관찰되나, FVG 수준까지의 정확한 복귀는 검증되지 않음.

**학술 근거 수준**: 낮음~중간. 시장 조성 불균형 개념은 지지되나, FVG 의 예측력에 대한 독립 실증 연구 부재.

**데이터 요건**: 분봉 OHLCV (Binance aggTrade OK, KIS 분봉 가능). L2 호가창 불필요.

---

### 2.4 Breaker (브레이커)

**ICT 정의**: 이전 Order Block 이 반전에 실패(지지/저항으로 작동하지 못하고 돌파됨) 하면 "브레이커"로 전환되어 반대 방향의 지지/저항이 된다.

**학술 매핑 — Bouchaud et al. (2009) 주문 흐름 불균형 + 지지/저항 실패 메커니즘**

Bouchaud et al. (2009) 의 시장 충격 모델에서 한방향 주문 흐름이 기존 유동성 공급을 소진하면, 가격이 새로운 균형 수준으로 영구적으로 이동할 수 있다. Breaker 의 메커니즘은 이와 일치한다: 이전 기관 지지선에서 추가 매수 주문이 소진되면, 해당 가격대가 이제 오버헤드 저항으로 작동한다 (Osler 2003 FX 스탑 클러스터의 역전).

기술적 분석 문헌에서 "지지선 이탈 시 저항선 전환" 은 Menkhoff (2010) 의 설문(전 세계 펀드매니저 70%+ 가 TA 사용)에서 실무적으로 광범위하게 활용되는 것으로 확인됐다. 그러나 학술 검증은 낮다.

**학술 근거 수준**: 낮음. 개념적 설명은 가능하나 Breaker 특유의 예측력을 독립 검증한 논문 부재.

**데이터 요건**: Order Block 탐지와 동일 — 분봉 OHLCV + 거래량.

---

### 2.5 CVD (Cumulative Volume Delta, 누적 볼륨 델타)

**ICT 정의**: 매수 체결량(aggressive buy = ask hit) 에서 매도 체결량(aggressive sell = bid hit) 을 뺀 값의 누적합. 가격 변화와 CVD 방향이 일치하는지(확인) 또는 반대인지(다이버전스) 로 추세 강도를 판단.

**학술 매핑 — Tape Reading 의 정량화 / OFI 1차원 버전**

CVD 는 시장 미시구조 문헌의 **넷 오더 플로우(net order flow)** 의 직접 측정치다:

$$\text{CVD}_t = \sum_{\tau=0}^{t} \bigl( V^{\text{buy}}_\tau - V^{\text{sell}}_\tau \bigr)$$

여기서 $V^{\text{buy}}$, $V^{\text{sell}}$ 은 각각 aggTrade 에서 taker side 가 BUY/SELL 인 체결량.

이것은 [[43-orderbook-flow-features]] §3 OFI 와 밀접히 관련되나, OFI 는 호가창 이벤트(bid/ask level 변화)를 추적하는 반면 CVD 는 실제 체결량의 방향성을 측정한다. Kyle (1985) 의 $Q$ (net signed order flow) 와 정확히 대응한다.

**CVD 다이버전스의 학술 근거**: Cont et al. (2014) 에서 mid-price 변화가 OFI 의 선형 함수임이 보였다. CVD 와 가격의 다이버전스는 "정보 없는 대형 일방향 주문" 이 가격을 밀어 올렸으나 그것을 받아주는 informed 매수세가 없는 상황으로 해석 가능하며, 반전의 선행 지표가 된다.

**학술 근거 수준**: 높음. Kyle λ · OFI 문헌의 직접 구현체. aggTrade 데이터로 계산 가능.

**데이터 요건**:
- Binance: `aggTrade` 스트림 (`m` 필드: true=seller-initiated, false=buyer-initiated). **현재 인프라로 가능** (Binance Vision 히스토리 데이터 존재).
- KIS: 분봉 체결량만 제공 (매수/매도 분리 불가) → **KIS 환경에서 CVD 계산 불가**.

---

### 2.6 VPIN (Volume-Synchronized Probability of Informed Trading)

**ICT 맥락**: VPIN 은 ICT 자체 개념은 아니나, 오더플로우 독성(toxic flow) 지표로 ICT 커뮤니티에서 함께 언급된다.

**학술 매핑 — Easley, López de Prado, O'Hara (2012) 학술 표준**

VPIN 은 학술적으로 완전히 정의된 지표다:

$$\text{VPIN} = \frac{\sum_{i=1}^{n} |V^B_i - V^S_i|}{\sum_{i=1}^{n} V_i}$$

- $V^B_i$, $V^S_i$: 볼륨 버킷 $i$ 의 추정 매수/매도량 (Bulk Volume Classification 사용).
- $n$: 버킷 수 (통상 50 버킷).

Easley et al. (2012) 의 핵심 발견:
- VPIN 이 높을수록 informed trading 비율이 높아 **market maker 가 손실** (adverse selection 위험).
- 2010년 Flash Crash 직전 VPIN 이 비정상적으로 상승함 — 유동성 공급자 철수 선행 신호.
- VPIN > 0.7 (상위 10% 분위) 시 다음 버킷의 시장 변동성이 유의하게 상승.

[[35-meta-labeling-lopez-de-prado]] 의 저자 Marcos López de Prado 가 공동 개발자이며, 메타라벨링 2단계 구조와 결합 가능성이 높다.

**학술 근거 수준**: 매우 높음. 동료 심사 저널 (Review of Financial Studies) 게재, 인용 수 1000+.

**데이터 요건**:
- Binance: `aggTrade` 스트림 + 볼륨 버킷 분류. **현재 인프라로 가능** (Binance Vision).
- KIS: 분봉 체결량으로 Bulk Volume Classification 근사 가능하나 정확도 낮음. **제한적 적용**.

---

## §3 데이터 요건 표

| ICT 시그널 | 필요 데이터 | Binance L2/aggTrade | KIS 분봉 | Fetcher 신규 필요 |
|------------|------------|---------------------|-----------|------------------|
| Liquidity Sweep | 분봉 OHLCV + N봉 고/저점 | aggTrade OK | 가능 | 없음 (기존 OHLCV) |
| Order Block | 분봉 OHLCV + 거래량 | aggTrade OK | 가능 | 없음 |
| FVG | 분봉 OHLCV | aggTrade OK | 가능 | 없음 |
| Breaker | Order Block 탐지 결과 | aggTrade OK | 가능 (제한적) | 없음 |
| CVD | aggTrade taker-side 체결량 | **aggTrade 필수** | **불가** | Binance aggTrade 스트리머 신규 |
| VPIN | aggTrade + 볼륨 버킷 분류 | **aggTrade 필수** | 근사 가능 (낮은 정밀도) | Binance aggTrade + BVC 계산기 신규 |

### 3.1 Binance aggTrade fetcher 현황

현재 `src/data_lake/fetcher.py` 는 OHLCV Parquet 수집기다. CVD/VPIN 계산을 위한 `aggTrade` 스트림 fetcher 는 별도 구현 필요:
- REST: `GET /api/v3/aggTrades` (최대 1000건/요청)
- Binance Vision: `aggTrades` 월별 zip (히스토리 백테스트용)
- 필드: `a` (aggTradeId), `p` (price), `q` (qty), `m` (isBuyerMaker: true=seller-initiated)

### 3.2 KIS 제약 요약

KIS Open API 분봉 데이터는 OHLCV + 거래량만 제공한다. 매수/매도 체결 구분 없음 → CVD 계산 불가, VPIN 은 Bulk Volume Classification 근사(정확도 ~60%) 만 가능. Sleeve B 의 주 자산이 BTC(Binance) 이므로 본 이슈에서는 Binance 우선 적용.

---

## §4 Sleeve B 알파 보강 후보 3종 추천

[[36-monthly-10pct-feasibility]] §5 옵션 B(MFT/HFT 인프라 + 실행 비용 절감) 및 옵션 A(통계차익) 의 맥락에서, sleeve B(BTC 공격 위성) 에 즉시 추가 가능한 오더플로우 시그널 3종을 추천한다.

### 후보 1: CVD 다이버전스 신호 (최우선, 학술 근거 高)

**선택 이유**:
- Kyle λ 및 OFI 문헌의 직접 구현체 — 학술 근거가 ICT 6종 중 가장 강함.
- Binance aggTrade 로 계산 가능 — 인프라 추가 비용 최소.
- [[43-orderbook-flow-features]] §3 OFI 의 보완재(OFI = 호가창 변화, CVD = 실체결 방향).

**시그널 정의**:
- `cvd_divergence`: 가격이 N봉 신고점을 갱신했으나 CVD 가 감소하면 하락 다이버전스 → 매도 신호.
- `cvd_confirmation`: 가격 상승 + CVD 상승 → 추세 확인 → 매수 신호 강도 증가.

**구현 경로**: `src/signals/cvd.py::cumulative_volume_delta(aggTrade_df)` 신규. `docs/specs/signals/cvd-divergence.md` 스펙 노트.

**예상 알파**: 단기(15m~1h) 모멘텀 반전·확인 필터. [[35-meta-labeling-lopez-de-prado]] 2단계 필터로 결합 시 false positive 제거 효과 기대.

---

### 후보 2: VPIN 독성 게이트 (차순위, 학술 근거 최高)

**선택 이유**:
- Easley et al. (2012) Review of Financial Studies 게재, 인용 1000+. 6종 중 학술 근거 최강.
- Flash Crash 선행 신호로 검증됨 — 꼬리 위험 방어 도구.
- [[35-meta-labeling-lopez-de-prado]] 저자(López de Prado) 가 개발 — 메타라벨링과 개념적 정합성.

**시그널 정의**:
- `vpin_gate`: VPIN > 임계값(θ = 0.7, 상위 10% 분위) 시 신규 진입 금지 게이트.
- 해석: 독성 주문 흐름이 높은 구간 = 시장 조성자 철수 = 슬리피지 급증 위험.

**구현 경로**: `src/signals/vpin.py::bulk_volume_classify(aggTrade_df) → vpin(buckets, n=50)` 신규.

**예상 알파**: 직접 수익 창출보다 **꼬리 손실 방어** 역할. 메타라벨러와 유사한 포지션 — 나쁜 타이밍에 진입하지 않는 필터.

---

### 후보 3: Liquidity Sweep 역방향 진입 (3순위, 학술 근거 中)

**선택 이유**:
- Osler (2003) FX 스탑 클러스터 실증으로 간접 지지.
- OHLCV 분봉만으로 구현 가능 — 추가 데이터 비용 없음.
- 단기 반전(mean reversion) 성격 — 현재 카탈로그의 모멘텀 전략과 상관이 낮아 ENB 증가 기여.

**시그널 정의**:
- `liq_sweep_reversal`: 가격이 직전 N봉(20봉 기본) 고점/저점을 0.1% 이상 돌파 후 당봉 내 원복 시 역방향 진입.
- 조건: 거래량 스파이크(당봉 거래량 > 20봉 평균 × 1.5) + OBI/CVD 역방향 확인.

**구현 경로**: `src/signals/liq_sweep.py::liquidity_sweep_reversal(ohlcv_df, n=20)` 신규.

**예상 알파**: 단기 평균 회귀. False positive 많으므로 CVD 확인 + 메타라벨러 필터 필수.

---

### 추천 비교 요약

| 후보 | 학술 근거 | 추가 데이터 비용 | 기대 역할 | 구현 난이도 |
|------|----------|----------------|-----------|------------|
| **CVD 다이버전스** | 高 (Kyle λ, OFI) | 중 (aggTrade fetcher 신규) | 추세 확인/반전 필터 | 낮음 |
| **VPIN 게이트** | 최高 (Easley 2012) | 중 (aggTrade + BVC) | 꼬리 손실 방어 | 중간 |
| **Liquidity Sweep 역진입** | 中 (Osler 2003) | 없음 (OHLCV만) | 저상관 반전 알파 | 낮음 |

---

## §5 ICT 비판론

ICT 는 Michael Huddleston 이 소셜미디어에서 개발·전파한 트레이딩 방법론으로, 학술 커뮤니티에서 다음과 같은 비판을 받는다.

### 5.1 학술 검증 부족

6종 개념 중 CVD(Kyle λ 대응)와 VPIN(Easley 2012) 을 제외하면 독립적인 동료 심사(peer-reviewed) 실증 연구가 없다. Order Block, FVG, Breaker 는 학술 저널 검색(Google Scholar, SSRN)에서 직접 검증 논문이 발견되지 않는다.

### 5.2 Retail 발 컨셉의 구조적 문제

ICT 는 학술 기관이 아닌 retail 트레이더 커뮤니티에서 발전했다. 개념 정의가 문헌마다 다르고, 핵심 파라미터(Order Block 의 "마지막 반대방향 캔들" 정의, FVG 의 최소 크기 조건 등) 가 표준화되어 있지 않다. 이는 재현 가능한 백테스트를 어렵게 한다.

### 5.3 백테스트 회의론

- **Hindsight bias(사후확신편향)**: Order Block, FVG 등은 차트를 사후에 볼 때 명확히 보이지만, 실시간 확인이 어렵다. 자동화 규칙이 없는 경우 backtest 는 사실상 curve-fitting.
- **Overfitting 위험**: ICT 규칙이 많고 상호 의존적이어서 in-sample 성과가 out-of-sample 로 이전되지 않는 경향이 실무에서 보고된다.
- **데이터 스누핑**: ICT 커뮤니티의 "성공 사례" 는 선택적 공개(publication bias) 로 실패 사례는 보고되지 않는다. [[12-validation-protocol]] §3 DSR(Deflated Sharpe Ratio) 보정 없이는 신뢰 불가.

### 5.4 본 프로젝트 적용 시 리스크

ICT 개념을 전략화할 경우 다음 절차 필수:
1. [[12-validation-protocol]] §3 Purged K-Fold + DSR 보정
2. OOS(Out-of-Sample) 기간 최소 6개월 이상
3. 학술 근거 낮은 개념(FVG, Breaker)은 단독 신호 금지, CVD/VPIN 확인 필터 결합 필수
4. [[35-meta-labeling-lopez-de-prado]] 2단계 구조로 false positive 방어

---

## §6 기존 노트와의 연결

### 6.1 [[43-orderbook-flow-features]] 와의 관계

본 노트와 `43-orderbook-flow-features` 는 상호 보완적이다:

| 측면 | 43-orderbook-flow-features | 본 노트(37) |
|------|---------------------------|------------|
| 초점 | L2 호가창 이벤트 (OBI/OFI/Microprice/Hawkes) | 실체결 방향 + ICT 패턴 (CVD/VPIN/Sweep) |
| 데이터 | 실시간 L2 tick (고비용) | aggTrade + OHLCV (저비용) |
| 시간 단위 | 1s~1m | 1m~1h |
| Variant 연결 | Variant G/H (이슈 #99) | Sleeve B 보강 (이슈 #145) |

### 6.2 [[35-meta-labeling-lopez-de-prado]] 와의 결합

CVD 다이버전스 + VPIN 게이트는 메타라벨러의 2단계 입력으로 자연스럽게 결합된다:
- 1단계 모델: momo-vol-filtered (기존 카탈로그 최고 Sharpe 1.102)
- 2단계 메타라벨러 추가 입력: `cvd_divergence`, `vpin_percentile`

---

## §7 구현 로드맵 (후속 이슈)

| 이슈 후보 | 내용 | 의존 |
|-----------|------|------|
| `feat: CVD 시그널 + aggTrade fetcher` | `src/signals/cvd.py` + Binance aggTrade REST/Vision | 본 이슈 완료 후 |
| `feat: VPIN 게이트 + BVC 분류기` | `src/signals/vpin.py` + BVC 구현 | aggTrade fetcher 선행 |
| `feat: Liquidity Sweep 역진입 신호` | `src/signals/liq_sweep.py` | OHLCV 기존 인프라만 필요 |
| `feat: 메타라벨러 CVD/VPIN 입력 확장` | 기존 메타라벨러에 CVD/VPIN 피처 추가 | CVD/VPIN 신호 완료 후 |

---

## §8 출처

1. **Kyle, A.S.** (1985). *Continuous Auctions and Insider Trading*. Econometrica, 53(6), 1315–1335. https://doi.org/10.2307/1913210
2. **Easley, D. & O'Hara, M.** (1992). *Time and the Process of Security Price Adjustment*. Journal of Finance, 47(2), 577–605. https://doi.org/10.1111/j.1540-6261.1992.tb04402.x
3. **Easley, D., López de Prado, M., O'Hara, M.** (2012). *Flow Toxicity and Liquidity in a High-Frequency World*. Review of Financial Studies, 25(5), 1457–1493. https://doi.org/10.1093/rfs/hhs053
4. **Roll, R.** (1984). *A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market*. Journal of Finance, 39(4), 1127–1139. https://doi.org/10.1111/j.1540-6261.1984.tb03897.x
5. **Bouchaud, J.-P., Farmer, J.D., Lillo, F.** (2009). *How Markets Slowly Digest Changes in Supply and Demand*. Handbook of Financial Markets: Dynamics and Evolution, ch.2. https://doi.org/10.1016/B978-012374258-2.50006-3
6. **Cont, R., Kukanov, A., Stoikov, S.** (2014). *The Price Impact of Order Book Events*. Journal of Financial Econometrics, 12(1), 47–88. https://doi.org/10.1093/jjfinec/nbt003
7. **Kim, O. & Stoll, H.R.** (1993). *Trading costs, adverse selection, and the role of the specialist*. Journal of Financial Economics, 33(3), 363–394. https://doi.org/10.1016/0304-405X(93)90026-E
8. **Menkhoff, L.** (2010). *The Use of Technical Analysis by Fund Managers: International Evidence*. Journal of Banking & Finance, 34(11), 2573–2586. https://doi.org/10.1016/j.jbankfin.2010.04.014
9. **Osler, C.L.** (2003). *Currency Orders and Exchange Rate Dynamics: An Explanation for the Predictability of Direction Changes*. Journal of Finance, 58(5), 1791–1819. https://doi.org/10.1111/1540-6261.00588
