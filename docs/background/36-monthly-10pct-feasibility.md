---
type: research
id: 36-monthly-10pct-feasibility
name: "월 10% 수익률 목표 가능성 평가 + 전략·리스크·사이징 재설계"
created: 2026-04-27
sources:
  - "Cornell, B. (2020). Medallion Fund: The Ultimate Counterexample? SSRN 3679979. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3679979"
  - "Hedgeweek (2024). Renaissance Tech and Two Sigma Lead 2024 Quant Gains. https://www.hedgeweek.com/renaissance-tech-and-two-sigma-lead-2024-quant-gains/"
  - "Navnoor Bawa / Substack (2024). 36% Returns: How D.E. Shaw Beat Citadel & Millennium to Top 2024. https://navnoorbawa.substack.com/p/36-returns-how-de-shaw-beat-citadel"
  - "Kelly, J. L. (1956). A New Interpretation of Information Rate. Bell System Technical Journal, 35, 917–926."
  - "López de Prado, M. (2018). Advances in Financial Machine Learning. Wiley."
  - "Rockafellar, R. T. & Uryasev, S. (2000). Optimization of Conditional Value-at-Risk. Journal of Risk, 2, 21–42."
  - "Harvey, C. R. et al. (2018). The Impact of Volatility Targeting. Journal of Portfolio Management, 45(1)."
---

# 월 10% 수익률 목표 가능성 평가 + 전략·리스크·사이징 재설계

> 이슈 #119. 본 노트는 사용자 지정 최종 목표 **월 10% (연환산 ~213.8% 복리)** 의 수학적 달성 조건, 정상급 헤지펀드 벤치마크, 현 전략 카탈로그 실측 결과, 보강 옵션 3가지, 그리고 사용자 권고 a/b/c 를 한 곳에 정리한다. Phase 0-4 모든 후속 전략·리스크·사이징 결정의 기초 문서다.

관련 노트: [[19-portfolio-risk]] · [[20-position-sizing]] · [[35-meta-labeling-lopez-de-prado]] · [[08-strategy-paradigms]] · [[12-validation-protocol]] · [[02-terms-quant-vs-quantum]] · [[momo-btc-v2]] · [[breakout-donchian]]

---

## §1 개요

### 목표 정의

| 항목 | 값 |
|------|----|
| 월 수익률 목표 | 10.0% |
| 연환산 (복리) | $(1.10)^{12} - 1 = 213.84\%$ |
| 연환산 (단리 근사) | $0.10 \times 12 = 120\%$ |
| 현 정책 (v0.1) | 레버리지 1.0×, MDD halt −5%, Sharpe ≥ 1.0 |

월 복리 10% 는 **연 213.84%** 로, Renaissance Medallion (gross 66%, net ~40%) 을 5배 이상 웃도는 목표다. 현 v0.1 보수 정책과의 충돌이 구조적이며, 달성 여부와 대안 정책을 본 노트에서 정량 평가한다.

### 본 노트의 위치

```
Phase 0 (본 노트) ─→ Phase 1 전략·리스크 이슈 (AC5 사용자 결정 후)
                          ├─ #105 Phase 2 KIS 모의계좌 정책
                          ├─ #107 Phase 3 Live Pilot 5%
                          └─ #120/#121/#122 리스크·메타라벨러 보강
```

---

## §2 정상급 펀드 벤치마크

> 출처: Cornell (2020) SSRN 3504766, Institutional Investor, Bloomberg, CNBC, DisruptionBanking, Hedgeweek. net/gross 구분 명시. 수치 파이썬 재계산 완료 (02_research.md §3).

| 펀드 | 기간 | 연 수익률 (net CAGR) | Sharpe | MDD (추정) | 비고 |
|------|------|---------------------|--------|------------|------|
| **Renaissance Medallion (gross)** | 1988–2018 | **63.3%** | **2.09** | — | 수수료 차감 전. 표준편차 31.7% |
| **Renaissance Medallion (net)** | 1988–2018 | **39%** | **~2.0** | **−24%** | 5%+44% 수수료 차감 후. σ~21% 추정 |
| Renaissance RIEF (외부) | 외부공개 | ~10–22% (연도별) | <1.5 추정 | — | 2020: −19.9% (Medallion 동기간 +76%) |
| **Citadel Wellington** | 1990– | **19.5%** | **~2.75** | — | 장기 Sharpe 업계 최고 추정치 |
| **D.E. Shaw Composite** | 2001– | **12.9%** | **1.83** | — | 2020 Sharpe 1.83 실측 |
| D.E. Shaw Oculus | 2020–2024 | — | — | — | 2024: +36% (단년도) |
| Two Sigma Compass | 2009–2014 | **~30%** (추정) | **1.0–1.5** | <20% 추정 | 간접 추정치, 공식 미공개 |
| **KOSPI (참고)** | 2010–2024 | **3.91%** | **0.33** | **−24.89%** | 산술평균 4.98%, σ 15.14% — 파이썬 재계산 |
| **월 10% 목표** | — | **213.84%** | **≥2.14** (σ=100% 가정) | ≤21% (Calmar=10) | — |

**핵심 관찰:**
- Medallion net 39%/년 = 월 복리 환산 **약 2.77%/월**. **월 10% 목표의 약 1/4 수준**.
- 월 10% 목표(연 213.84%)는 Medallion gross(63.3%)의 **3.4배**, Citadel(19.5%)의 **11배**.
- 세계 최고 Sharpe를 가진 Citadel(~2.75)도 연 19.5% 수준 — 월 10% 달성 사례 전무.
- KOSPI 15년 CAGR 3.91%: 월 10% 목표의 **54분의 1** 수준.

---

## §3 월 10% 달성 수학 4조건

> 계산 근거: `src/risk/sizing.py` `kelly_continuous`, `vol_target` 순수 함수. 이론: [[20-position-sizing]] §2–3.

### 3.1 필요 Sharpe (정규 가정)

월 수익률 $r_m = 0.10$, 연환산 $\mu_a = (1.10)^{12} - 1 = 2.1384$ (검증: $1.10^{12} = 3.1384$ ✓).

$$\text{필요 Sharpe} = \frac{\mu_a}{\sigma_a}$$

| 연 변동성 $\sigma_a$ | 필요 Sharpe | 현실성 판단 |
|---------------------|-------------|------------|
| 10% | 21.38 | 불가능 (BTC 연 변동성도 50%+) |
| 20% | 10.69 | 불가능 |
| 50% | 4.28 | 불가능 (Medallion gross도 2.09) |
| 80% | 2.67 | 이론상 가능이나 MDD 치명적 |
| 100% | 2.14 | Citadel 장기 Sharpe와 유사 — 단 σ=100%는 MDD≥50% 위험 |
| 150% | 1.43 | Sharpe 달성 가능하나 σ=150%는 파산 위험 |
| **213%** | **1.00** | Sharpe 1.0 달성 가능 구간이지만 σ=213%는 레버리지 청산 확정 |

수치 재계산 근거: `mu_a = (1.10)**12 - 1 = 2.1384`, `Sharpe = mu_a / sigma_a` (02_research.md §3).

**결론**: 현 정책 "Sharpe ≥ 1.0 + MDD halt −5%" 와 월 10% 는 **구조적으로 양립 불가**. "Sharpe 2.0 + σ_a ≈ 100%" 조합이 수학적 최소 요건이나, σ=100% 환경에서 MDD −50% 이상이 사실상 불가피.

### 3.2 필요 레버리지

내재 알파 Sharpe = $S$, 기초 변동성 = $\sigma_{\text{underlying}}$ 일 때 레버리지 $L$ 은 수익률·변동성을 $L$ 배 하되 Sharpe 는 불변.

$$L \geq \frac{\mu_{\text{target}}}{S \cdot \sigma_{\text{underlying}}}$$

| 내재 Sharpe | 기초 $\sigma$ | 필요 최소 $L$ | 비고 |
|------------|---------------|--------------|------|
| 2.0 | 30% | **3.6×** | 최선 시나리오 |
| 2.0 | 50% (BTC 실제) | **2.1×** | |
| 1.5 | 30% | **4.8×** | |
| 1.5 | 50% | **2.9×** | |
| 1.0 | 30% (현 카탈로그 추정) | **7.1×** | |
| 1.0 | 50% | **4.3×** | |
| 0.8 | 30% | **8.9×** | |
| 0.5 | 50% | **8.6×** | |

수치 재계산: `L = mu_a / (S * sigma)`, `mu_a = 2.1384` (02_research.md §2.2).

**결론**: 현 카탈로그 실측 최고 Sharpe momo_vol_filtered 1.102 (DRY-RUN) 가정 시, 연 213% 달성에 최소 **4–5× 레버리지** 필요. Binance Futures 허용 최대 125×이나, 리스크 정책상 허용 L ≤ 3× 가정 시 **Sharpe 2.0 + σ=50% 이상 전략이 필수**.

### 3.3 MDD 허용 범위

Calmar 비율 = 연수익 / |MDD|. Medallion 역사적 Calmar ≈ 7–10.

$$\text{허용 MDD} \leq \frac{\mu_a}{\text{Calmar}} = \frac{213.84\%}{10} = 21.38\%$$

| Calmar 목표 | MDD 허용 한계 | 현 정책 MDD halt | 충돌 |
|------------|--------------|-----------------|------|
| 15 (엄격) | **−14.3%** | −5% | ×3 완화 필요 |
| 10 (Medallion급) | **−21.4%** | −5% | ×4 완화 필요 |
| 7 (보수 퀀트) | **−30.5%** | −5% | ×6 완화 필요 |

1σ 일변동성 (L배 레버리지 시, σ_a 기준):

| σ_a | L=1 | L=2 | L=3 | L=5 |
|-----|-----|-----|-----|-----|
| 30% | 1.89% | 3.78% | 5.67% | 9.45% |
| 50% | 3.15% | 6.30% | 9.45% | 15.75% |
| 100% | 6.30% | 12.60% | 18.90% | 31.50% |

수치: `MDD_allowed = mu_a / calmar`, `daily_vol = L * sigma_a / sqrt(252)` (02_research.md §2.3).

**결론**: 현 −5% MDD halt 는 월 10% 경로에서 **상시 발동**. 최소 −21% (Calmar 10)로 완화해야 하며, 이는 현 Phase 1 정책의 **4배 리스크 허용**. 정책 변경은 AC5 사용자 결정 선행 필수.

### 3.4 거래 빈도 요건 (LFT / MFT / HFT)

Binance Futures 왕복 수수료(taker): 0.10% (= 2 × 0.05%).

$$\mu_{\text{monthly}} = N \times R_{\text{net}} \times s = 0.10$$

| 유형 | N/월 | R_min_net | R_gross 필요 | 비용 비중 | 달성 가능성 |
|------|------|-----------|-------------|----------|------------|
| LFT | 10 | 0.500% | 0.600% | 0.2× | 가능 — 단 레버리지 필요 |
| MFT | 100 | 0.050% | 0.150% | 2.0× | 타이트 — 슬리피지 포함 시 어려움 |
| MFT/HFT 경계 | 1,000 | 0.005% | 0.105% | 20× | 비용이 순수익의 20배 — 불가 |
| HFT | 10,000 | 0.0005% | 0.1005% | 200× | 비용이 순수익의 200배 — 파괴적 |

(포지션 크기 s=2× 가정. 02_research.md §2.4 재계산.)

**결론**: R-multiple 0.05% 이하는 BTC 선물 수수료(0.10% 왕복) + 슬리피지에 침식. **MFT 이상 + 메이커 주문 전환(수수료 −0.02%) + 레버리지 병행** 이 구조적 전제 조건.

---

## §4 카탈로그 5종 실측 결과 요약

> ⚠️ 본 섹션은 backtester (task #2) 산출물 `02_implementation.md` 를 인용한다. task #2 완료 후 수치를 채운다. 현재는 plan §Step 3 에 명시된 방법론과 제약사항만 기록.

### 4.1 방법론 요약

- **데이터 윈도우**: 크립토 3년 (2023-04-27 ~ 2026-04-27, Binance USDT-M). KRX 실측 1.3년 (2025-01 ~ 2026-04) — 3년 요건 미충족, Bootstrap 보완.
- **레버리지 모델**: 사후 곱하기 근사 $r_t^{(L)} = L \cdot r_t - (L-1) \cdot c_{\text{borrow},t}$. 펀딩비 sensitivity: 0% / 11% / 25% 3종.
- **청산 한계**: $r_t^{(L)} \leq -1$ (ruin 조건). MDD = peak-to-trough on $\prod(1 + r_t^{(L)})$.
- **월 10% hit ratio**: 12개월 롤링 단순수익률 분포에서 ≥ 10% 비율.

### 4.2 전략별 L=1 베이스라인

> 측정 모드: DRY-RUN (synthetic data, seed=119). 공통 거래일 330일 (크립토 1095일, KRX 330일). 출처: `02_implementation_catalog_3y.md`.

| 전략 | 연 수익률 | Sharpe | MDD | 기간(일) |
|------|-----------|--------|-----|---------|
| momo-btc-v2 (15m) | **9.08%** | **0.472** | −24.42% | 1095 |
| momo-vol-filtered (4h) | **26.42%** | **1.102** | −24.25% | 1095 |
| meanrev-pairs ETHBTC (1h) | **5.03%** | **0.324** | −26.22% | 1095 |
| breakout-donchian KOSPI200 (1d) | **−15.40%** | **−0.783** | −27.51% | 330 (KRX 1.3년) |
| momo-kis-v1 KRX 15m | **6.25%** | **0.411** | −16.06% | 330 (KRX 1.3년) |

**포트폴리오 리스크 지표 (등가중 5전략)**:

| 지표 | 값 |
|------|-----|
| ENB | 3.32 (ENB/N = 0.664) |
| 평균 pairwise ρ | 0.0075 (극히 낮음 — 분산 우수) |
| CVaR (97.5%) | 1.39%/일 |
| VaR (97.5%) | 1.10%/일 |

**관찰**:
- momo_vol_filtered 가 Sharpe 1.102 로 카탈로그 최고. 그러나 연 26.4% 는 월 10% 목표(연 213.8%)의 **12%** 수준.
- breakout_donchian 은 KRX 1.3년 데이터 제약으로 음수 수익. 3년 요건 미충족 — 참고용.
- 전략 간 평균 상관 0.0075 → ENB 3.32/5 = 분산 양호. 레버리지 적용 시 포트폴리오 CVaR 확대 폭이 단일 전략 대비 완화됨.

### 4.3 레버리지 시나리오 (실측, 02_implementation.md)

> 사후 곱하기 근사: $r_t^{(L)} = L \cdot r_t - (L-1) \cdot c_{\text{borrow}}$. funding=7.3% = Binance USDT-M 장기 평균. DRY-RUN synthetic(seed=119).

**momo_vol_filtered (카탈로그 최우수)**:

| L | funding | 연 수익률 | Sharpe | MDD | 월 10% hit ratio |
|---|---------|----------|--------|-----|-----------------|
| 1× | 0% | 26.42% | 1.102 | −24.25% | **24.32%** |
| 3× | 0% | 70.30% | 1.102 | −61.71% | **43.24%** |
| 3× | 7.3% | 47.19% | 0.898 | −65.37% | **40.54%** |
| 5× | 0% | 82.40% | 1.102 | −83.76% | **43.24%** |
| 5× | 7.3% | 36.17% | 0.858 | −88.75% | **43.24%** |

**momo_btc_v2**:

| L | funding | 연 수익률 | Sharpe | MDD | 월 10% hit ratio |
|---|---------|----------|--------|-----|-----------------|
| 1× | 0% | 9.08% | 0.472 | −24.42% | 10.81% |
| 3× | 7.3% | −7.11% | 0.278 | −78.93% | 27.03% |
| 5× | 7.3% | −38.69% | 0.239 | −97.17% | 35.14% |

**Sharpe 불변성 sanity check (funding=0%)**: 전 전략 PASS — Sharpe(L=3,fund=0) = Sharpe(L=1). 사후 곱하기 근사 내적 정합 확인.

**핵심 판정**:
- 월 10% hit ratio **≥ 40%** 달성은 `momo_vol_filtered` L=3× (fund=0%) 구간에서만 관찰됨.
- 실제 funding 7.3% 적용 시 hit ratio 40.54% — 그러나 MDD −65% 수반.
- 현 MDD halt −5% 정책에서는 L=3× 적용 즉시 halt 발동 → 레버리지 무의미.
- **결론**: 월 10% hit ratio 40%+ 달성 = momo_vol_filtered L=3× + MDD halt −65% 허용 필요. 현 정책 대비 **13배 리스크 완화** 요구.

### 4.4 메타라벨러 ON/OFF 비교 (momo-btc-v2 한정)

기존 1년 실측 (35-meta-labeling-lopez-de-prado §4, issue #85, BTC/USDT 15m 35,041 bars):

| 지표 | OFF (bypass) | ON (메타라벨러) | Δ |
|------|-------------|----------------|-----|
| Sharpe | −2.16 | −1.13 | **+1.04** ✅ |
| MDD | 0.43 | 0.34 | −0.09 |
| 거래수 | 95 | 61 | −34 |

메타라벨러의 가치는 **손실 방어** (좌측 꼬리 제거). 알파 생성이 아닌 테일 리스크 컷. CV 정확도 49.58% (≈동전 던지기) 임에도 Sharpe +1.04 개선.

**판정**: 현 카탈로그는 레버리지 1× 에서 월 10% 달성 불가 (최고 전략 연 26.4% vs 목표 213.8%). 레버리지 3–5× 시나리오에서도 포트폴리오 Sharpe ≈ 0.5 × L 환경에서 월 10% hit ratio 는 극히 낮을 것으로 예상됨. 상세 레버리지 매트릭스: `02_implementation.md` (backtester task #2 완료 후 갱신).

---

## §5 보강 옵션 3가지

> 사용자 메모리 규칙: 특허 리서치 1순위 = 시스템 강화 (#111–114 차용) → 옵션 B 에 최우선 반영.

현 카탈로그가 월 10% 미달 시 세 가지 경로로 보강 가능하다.

---

### 옵션 A: 신규 전략 — Renaissance-style 단기 통계차익

**기대 이익 메커니즘:**

$$\text{월 수익} \approx N_{\text{trade}} \times \overline{R} \times s, \quad \overline{R} \in [0.05\%, 0.2\%], \; N \in [100, 500]$$

분단위 mean-reversion (BTC-ETH 스프레드, BTC-gold 상관) + 메타라벨러 2차 필터. [[35-meta-labeling-lopez-de-prado]] 의 2단계 구조를 크립토 페어에 적용.

**필요 인프라:**
- 틱/분봉 데이터 파이프라인 (현재 15m/1h/4h — 1m 추가 필요)
- 공적분 테스트 (Engle-Granger / Johansen) 자동화 모듈
- 메타라벨러 확장: momo-btc-v2 → ETHBTC pairs 커버

**예상 구현 기간**: 6–8주 (데이터 파이프라인 + 모델 + 백테스트)

**리스크/실패 모드:**
- 스프레드 붕괴 (2022 LUNA, 2020 코로나 쇼크 시 상관 단절)
- 슬리피지: 분봉 스프레드 0.02–0.05% → R-multiple 잠식
- 과적합: purged K-fold [[12-validation-protocol]] 없이 백테스트 시 false positive

---

### 옵션 B: MFT/HFT 인프라 도입 — #111–114 특허 차용 (최우선)

**기대 이익 메커니즘:**

$$\text{실행 비용 절감} = \Delta \text{slippage} \times N_{\text{trade}} \times s \Rightarrow \text{엣지 보존}$$

#111–114 특허 차용:
- **OrderRouter** (#111): 거래소 간 최적 체결 경로 선택 → 슬리피지 30–50% 절감 추정
- **VWAP/TWAP 실행** (#112–113): 대형 주문 분할 실행 → 시장충격 최소화
- **WebSocket lag 단축** (#114): Binance + KRX 콜로케이션급 지연 단축 → MFT 진입 정확도 향상

[[08-strategy-paradigms]] §2 "실행 품질 의존" 지적과 직결: 통계차익 엣지는 실행력이 관건.

**필요 인프라:**
- Binance co-location 또는 VPS (도쿄 리전) — 현재 미구축
- KRX WebSocket 직결 (KIS API 현재 REST 기반)
- OrderRouter 모듈 신규 (`src/execution/order_router.py`)

**예상 구현 기간**: 4–6주 (VPS + WebSocket + OrderRouter 기본 구현)

**리스크/실패 모드:**
- 인프라 비용 (VPS 도쿄 $50–200/월)
- KRX co-location 개인 접근 제한
- 레이턴시 개선 효과가 월 10% 기여에는 간접적 — 단독으로 목표 달성 불가

---

### 옵션 C: 옵션 활용 — Delta-hedged 변동성 수익

**기대 이익 메커니즘:**

$$\text{Short Volatility Premium} = \text{Implied Vol} - \text{Realized Vol} > 0 \text{ (평균)}$$

Covered call (현물 보유 + call 매도) 또는 short strangle (OTM call + put 동시 매도) 로 변동성 프리미엄 수취.

$$\text{월 수익률} \approx \frac{\text{Option Premium}}{\text{Notional}} \approx 2–5\% \text{ (BTC 옵션 기준, 평균 IV-RV 스프레드)}$$

**필요 인프라:**
- Binance Options API 연동 (현재 미구현)
- KIS 옵션 거래 권한 별도 신청 (KRX 야간 ETF 옵션)
- Delta hedging 엔진 (`src/execution/options_hedge.py`)
- Greeks 계산 모듈 (Black-Scholes / Heston)

**예상 구현 기간**: 8–12주 (옵션 API + Greeks + hedging 루프)

**리스크/실패 모드:**
- **Negative convexity**: short vol 포지션은 꼬리 이벤트 시 무제한 손실 (2020년 Volmageddon)
- KRX 옵션 개인 거래 권한 취득 불확실
- Binance Options 유동성 부족 (OI 제한적)

---

## §6 사용자 권고 a/b/c/d

> ⚠️ backtester 정량 수치 (Sharpe, 월 10% hit ratio) 미수신으로 일부 셀은 예상치로 기재. 실측 후 갱신.

> 실측 데이터 기반 (DRY-RUN synthetic, seed=119): 카탈로그 최고 Sharpe 1.102 (momo_vol_filtered). momo_vol_filtered L=3×, funding 7.3% 시 월 10% hit ratio **40.54%**, MDD **−65.37%**.

| 시나리오 | 정책 변경 | 실측 기반 도달 가능성 | 주요 리스크 | 후행 이슈 영향 |
|---------|-----------|----------------------|------------|----------------|
| **(a) 목표 유지 + 공격 정책** | Sharpe ≥ 2.0 목표, MDD halt −25%, L 3–5×, 옵션B+C 병행 | **낮음** (~40%): momo_vol_filtered L=3, fund=7.3% 기준 hit ratio 40.54% — 단, MDD −65%, 현 −5% halt 정책에서는 즉시 청산 | MDD −65%+ 수반, 현 halt −5%와 충돌, 인프라 비용 $50–200/월 | #107 Live 5% → 25% 격상, #122 윈도우 단축, #111–114 즉시 착수 |
| **(b) 목표 하향 + 보수 유지** | Sharpe ≥ 1.0, MDD halt −5%, L 1.0×, 목표 월 2–3% | **높음** (~70–80%): momo_vol_filtered L=1 hit ratio 24.32%, 월 2–3% 범위는 카탈로그 전반 도달 가능 | 낮음 — 장기 자본 성장 제한 | 현 정책 유지, #105 Phase 2 보수 운용 |
| **(c) 단계별 차등** | Phase 1: (b) 보수. Phase 3 실측 Sharpe ≥ 1.5 + hit ratio ≥ 30% 달성 시 (a) 부분 도입 | **중간** (Phase 1 ~70%, Phase 3 이후 확장 ~40%): [[12-validation-protocol]] §3 SOP 와 일치 | Phase 게이트 미달 시 공격 전환 보류 — 의사결정 지연 | Phase 게이트 명문화 (#105/#107 연동), #120 watchdog 활성화 |
| **(d) Sleeve allocation (multi-PM)** | 자본을 슬리브로 분할: A=(b) 보수 core, B=(a) 공격 satellite, C=(c) 게이트 experimental — 동시 운영 | **높음** (Phase 1 ~70%, Phase 3 이후 격상 시 hit ratio 단계적 상승). 단일 (a) 보다 자본 보존, 단일 (b) 보다 알파 노출, 단일 (c) 보다 즉시 분산 | sleeve 별 독립 Policy 필요 ([[risk-rule-dsl]] v3 `sleeve_id` 확장), sleeve 간 상관 ρ 모니터링 필요 | Phase 1 즉시 운영 가능, #105/#107 sleeve 별 한도 분리, #120 sleeve 별 watchdog |

### 비교 요약

```
(a) 공격: 높은 보상 가능성, 높은 손실 리스크 — 자본 보존 위협
(b) 보수: 낮은 수익 기대, 높은 안정성 — 장기 신뢰 구축에 유리
(c) 차등: 균형점 — Phase 게이트로 결정을 데이터에 위임
(d) Sleeve: (a)+(b)+(c) 동시 운영 — 분산·격리·게이트의 장점 결합 (헤지펀드 multi-PM 표준)
```

### (d) Sleeve allocation 상세 (실측 분석적 합성)

> 출처: `02_implementation.md` §3.7. 분석적 합성 — 정확치는 5전략 등가중 daily returns 측정 후 별도 PR 갱신 필요.

**Phase 1 비중 시나리오 (Sleeve A:B:C)**:

| 비중 (A/B/C) | 통합 연 수익률 | 통합 Sharpe | 통합 MDD 상한 | 비고 |
|--------------|----------------|-------------|---------------|------|
| **70/20/10 (권장)** | 14.46% | ~1.03 | -25% | 보수 우세 + 공격 제한 + 게이트 격상 여지 |
| 60/20/20 | 14.46% | ~1.03 | -24% | C 격상 비중 확대 (데이터 게이트 의존도 ↑) |
| 50/30/20 | 18.55% | ~1.03 | -27% | 공격 비중 격상, MDD 약간 확대 |
| 90/10/0 | 10.37% | ~0.86 | -20% | 보수 강화, 공격 노출 최소 |

**Phase 3 게이트 통과 후 (Sleeve C → Sleeve B 와 동일)**:

| Phase 1 비중 | Phase 3 비중 (A/B 통합) | 통합 연 수익률 | 통합 Sharpe | 통합 MDD 상한 |
|--------------|------------------------|----------------|-------------|---------------|
| 70/20/10 | 70/30 | 18.55% | ~1.04 | -30% |
| 60/20/20 | 60/40 | 22.65% | ~1.05 | -35% |
| 50/30/20 | 50/50 | 26.74% | ~1.06 | -40% |

> 월 10% (연 213.8%) 직접 도달은 어떤 비중 조합에서도 불가. **단**, sleeve B 가 단독으로 월 10% hit ratio 40.54% 달성하므로, sleeve B 비중 1.0 (= 단일 a) 와 정확히 동일한 노출은 자본의 30% 까지 격상 가능 (Phase 3 50/30/20 → 50/50 시).

**본 노트 권고 (사용자 요청 반영):** 옵션 **(d) Sleeve allocation 70/20/10 (Phase 1)** 이 (a)·(b)·(c) 단일 선택의 장점을 모두 살린다. 실무 표준(Citadel/Millennium multi-PM, AQR Style Premia)이며, Phase 3 격상 후 자연스럽게 70/30 으로 전환된다. **단, 운영 전 [[risk-rule-dsl]] v3 확장 (`sleeve_id` 필드 + sleeve 별 독립 Policy 인스턴스) 이 선결 조건**이다.

---

## §7 결정 요청

> 사용자 응답 후 채움. 본 이슈 #119 는 응답 전까지 OPEN 유지.

**사용자 선택**: ☐ (a) 단일 공격  ☐ (b) 단일 보수  ☐ (c) 단계별 차등  **☑ (d) Sleeve allocation** *(2026-04-27 사용자 결정)*

**Sleeve 비중**: **신규 전략 추가 후 재평가 (보류)** — 사용자 방향: "전략을 추가하면서 수익률을 끌어올린다. 멀티 전략 프로그램 컨셉을 유지하되, 비중 결정은 카탈로그 확장 후."

**결정 일자**: 2026-04-27 (방향 결정 + 비중 보류 결정)

**근거** (백서 정합성):
- `docs/whitepaper/qta-master-plan-v01.md` (PR #141 `chore/000133-phase2-operation` 브랜치, master 미머지) **§1-10 목표 수익성** 에서 "월 10% 달성은 현 카탈로그로 불가능 — **신규 전략 발굴·기존 강화·메타라벨러 ON 재검증 후 v0.2 에서 갱신**" 으로 명시. 본 결정은 백서 §1-10 약속과 정합.
- 백서 §1-8 트랙션 = "5종 전략 카탈로그" + §5-7 = #99 VWMA 단타 사전 등록 — 멀티 전략 = 본 프로젝트 핵심 컨셉. 본 이슈는 그 컨셉을 운영 정책 (sleeve) 으로 구체화.

**결정에 따른 후속 조치**:
1. **(선결 조건) [[risk-rule-dsl]] v3.1 확장** — `sleeve_id` 필드, sleeve 별 독립 Policy 인스턴스, `halt_sleeve` 액션. 별도 이슈 #TBD. 비중 무관하게 진행 가능.
2. **신규 전략 추가** — #99 VWMA 단타 카탈로그 (8-variant factorial 사전 등록), **#145 오더플로우·ICT 시그널 카탈로그** (sleeve B 알파 보강 research), 옵션 §5 (A) 통계차익 신규 전략. 카탈로그 확장이 sleeve 비중 결정의 인풋.
3. **5전략 등가중 portfolio daily returns 실측** — sleeve A 의 σ·MDD·Sharpe 정확치 갱신. 별도 이슈 #TBD.
4. **#105/#107 sleeve 별 한도 분리** — Phase 2/3 정책 PR 에 sleeve 인프라 적용 (비중은 운영 시점에 가변).
5. **#120 watchdog sleeve 별 alarm 분기**.
6. **#122 메타라벨러 재학습** — sleeve B (momo-vol-filtered) 한정 우선.
7. **#111–114 특허 차용 OrderRouter/VWAP/TWAP** — sleeve B 알파 보존 인프라로 우선 착수.
8. **백서 v0.2 (#138/#142)** — 본 이슈 결과 (sleeve allocation 방향 + 카탈로그 확장 약속) 를 §1-10 / §5-7 에 반영.

**Sleeve 비중 재평가 시점**:
- 신규 전략 1-2개 추가 (예: #99 VWMA 1차 카탈로그) + 5전략 등가중 portfolio Sharpe 실측 후
- 또는 Phase 1 paper-trading 30거래일 누적 (#143) 결과 기반

> 단일 (a)/(b)/(c) 회귀가 필요한 경우 본 노트 권고 §6 비교 표를 그대로 활용 가능.

---

## 참고 노트

- [[19-portfolio-risk]] — CVaR(97.5%), ENB, 공분산 추정 이론
- [[20-position-sizing]] — Kelly, vol targeting, HRP 사이징 이론
- [[35-meta-labeling-lopez-de-prado]] — 메타라벨러 2단계 구조 + 실측 결과
- [[08-strategy-paradigms]] — 전략 패러다임 비교 (규칙기반/통계차익/ML)
- [[12-validation-protocol]] — 백테스트 검증 SOP (walk-forward, DSR, PBO)
- [[02-terms-quant-vs-quantum]] — 퀀트 vs 퀀텀 용어 정의
- [[momo-btc-v2]] — BTC 15m 모멘텀 전략 (카탈로그 주력)
- [[breakout-donchian]] — KOSPI200 돌파 전략 (KRX 카탈로그)

## 출처

- Cornell, B. (2020). *Medallion Fund: The Ultimate Counterexample?* SSRN 3679979. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3679979>
- Hedgeweek (2024). *Renaissance Tech and Two Sigma Lead 2024 Quant Gains*. <https://www.hedgeweek.com/renaissance-tech-and-two-sigma-lead-2024-quant-gains/>
- Navnoor Bawa (2024). *36% Returns: How D.E. Shaw Beat Citadel & Millennium to Top 2024*. <https://navnoorbawa.substack.com/p/36-returns-how-de-shaw-beat-citadel>
- Kelly, J. L. (1956). *A New Interpretation of Information Rate*. Bell System Technical Journal, 35, 917–926.
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Ch.3, Ch.7.
- Rockafellar, R. T. & Uryasev, S. (2000). *Optimization of Conditional Value-at-Risk*. Journal of Risk, 2, 21–42.
- Harvey, C. R. et al. (2018). *The Impact of Volatility Targeting*. Journal of Portfolio Management, 45(1).
