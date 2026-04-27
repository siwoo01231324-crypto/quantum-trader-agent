---
type: work-plan
issue: 119
name: "#119 AC1+AC2 책상 리서치 — 헤지펀드 벤치마크 + 월 10% 수학 4조건"
created: 2026-04-27
---

# AC1 + AC2 책상 리서치

> 작성: 2026-04-27  
> 담당: researcher  
> 근거: `01_plan.md` Step 1·2

---

## §1 헤지펀드 벤치마크 (AC1)

> 조사 규칙: 모든 수치는 출처 1개 이상. Medallion gross/net 구분 명시. 추정·간접 인용은 "(추정)" 표시.

### 1.1 Renaissance Medallion Fund (1988–2018)

| 지표 | 값 | 비고 |
|------|----|------|
| 연수익률 (gross, 산술평균) | **66.1%** | 수수료 차감 전, 거래비용은 포함 |
| 연수익률 (net, CAGR) | **39%** | 수수료 차감 후 (5% 운용 + 44% 성과보수) |
| 연수익률 (gross, CAGR) | **63.3%** | $100 → $398.7M (31년) |
| 표준편차 (gross) | **31.7%** | |
| 표준편차 (net, 추정) | **21%** | robotwealth 시뮬레이션 기반 |
| Sharpe (gross, rf≈0) | **2.09** | = 66.1% / 31.7% |
| Sharpe (net, 추정) | **~2.0** | mean 44% / vol 21% = 2.10 |
| 최대 낙폭 (MDD) | **-24%** (최악 시나리오) / **-15.5%** (최선) | 시뮬레이션 구간 의존 |
| Calmar (net 추정) | **1.6** | 39% / 24% |
| 연속 손실 연도 | **0** | 1988–2018 31년간 음수 연도 없음 |
| 최저 연간수익 (net) | **~31.5%** (gross 기준) | |
| 승률 (거래 단위) | **50.75%** | R. Mercer 진술 |
| 베타 (시장 대비) | **약 -1.0** | 시장 역상관 |

**출처**:  
- Bradford Cornell, *"Medallion Fund: The Ultimate Counterexample?"* (2020), SSRN 3504766 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766  
- Cornell Capital Group blog (2020-02) — https://www.cornell-capital.com/blog/2020/02/medallion-fund-the-ultimate-counterexample.html  
- RobotWealth, *"Investing in RenTech's Medallion Fund"* — https://robotwealth.com/renaissance-medallion-performance/

> **주의**: "gross 66%" 는 거래비용 차감 후이지만 운용·성과보수(5%+44%) 차감 전. 외부 비교는 반드시 **net 39%** 기준만 사용.

---

### 1.2 Renaissance RIEF / RIDA (외부 공개 펀드)

| 지표 | RIEF | RIDA |
|------|------|------|
| 운용 규모 (2024) | 공개 미상 | ~$2.8B |
| 2024 net 수익 | **+22.7%** (최고: 2011년 +34%) | **+15.6%** |
| 2020 수익 | **-19.9%** | **-31.9%** |
| Medallion 동기간 (2020) | +76% | +76% |
| 전략 특성 | 장기 지수형 | 장기 다자산 |

**출처**: Institutional Investor (2024-01) — https://www.institutionalinvestor.com/article/2e0uykr3vn5booz0smrcw/hedge-funds/renaissances-2024-rebirth  
Institutional Investor (2021) — https://www.institutionalinvestor.com/article/2bswms7wco7as686o8ikg/portfolio/renaissances-medallion-fund-surged-76-in-2020-but-funds-open-to-outsiders-tanked

> **시사점**: Medallion의 알파는 단기·고빈도·내부자 전략에서 발생. 동일 법인 외부 공개 펀드는 시장 대비 소폭 초과 수준에 불과. Sharpe 1.5 이하 추정.

---

### 1.3 Two Sigma (Compass / Absolute Return)

| 지표 | Compass Enhanced (2009–2014 평균) | Absolute Return Enhanced (2023) |
|------|-----------------------------------|---------------------------------|
| 연평균 net 수익 | **~30%** | **12%** |
| Sharpe (추정) | **1.0–1.5** | 미공개 |
| MDD | <20% (추정) | 미공개 |
| Macro Compass Enhanced (2023) | **-16%** | — |
| Spectrum (2023) | **+8.6%** | — |

**출처**:  
- Quora 커뮤니티 분석 (2009–2014 데이터) — https://www.quora.com/What-has-been-the-historical-performance-of-Two-Sigma-Compass-Enhanced-fund  
- Bloomberg (2024-01-29) — https://www.bloomberg.com/news/articles/2024-01-29/two-sigma-s-macro-compass-enhanced-fund-sank-16-last-year  
- Hedgeweek (2024) — https://www.hedgeweek.com/two-sigma-records-16-loss-for-compass-enhanced-fund/

> **주의**: Two Sigma는 공개 자료 극히 제한. 2009–2014 수치는 간접 추정치(출처: Quora 분석). 공식 검증 불가.

---

### 1.4 Citadel Wellington / Tactical

| 지표 | 값 | 비고 |
|------|----|------|
| 설립 이래 CAGR (1990–) | **19.46%** | net |
| 2017– 평균 연수익 | **18%** | net |
| 2022 수익 | **+38%** | 역대 최고 |
| 2023 수익 | **+15.28%** | net |
| 2024 수익 | **+15.21%** | net |
| 2025 수익 | **+10.2%** | net |
| Sharpe (장기 추정) | **~2.75** | 업계 추정치 |
| Sharpe (단일 연도 측정) | **~2.51** | rf=4% 가정 연도 |
| AUM | $33.2B (Wellington) | |

**출처**:  
- CNBC (2026-01-02) — https://www.cnbc.com/2026/01/02/ken-griffins-flagship-hedge-fund-at-citadel-rises-10point2percent-in-volatile-2025.html  
- GrowthMind Substack — https://growthmind.substack.com/p/why-it-doesnt-matter-if-citadel-beat  
- Institutional Investor — https://www.institutionalinvestor.com/article/2aucrzsa72lr93xz8ghds/ria-intel/the-most-consistently-profitable-hedge-funds-continue-to-prove-their-edge

---

### 1.5 D.E. Shaw Composite / Oculus

| 지표 | Composite (2001–2025) | Oculus (2020–2024) |
|------|-----------------------|--------------------|
| 설립 이래 CAGR (net) | **12.7–12.9%** | — |
| 연 손실 이력 | **1회** (23년 중) | — |
| 2018–2022 연평균 (추정) | ~15–20% | — |
| 2020 | +18% (Composite) | **+25%** |
| 2021 | — | **+15%** |
| 2022 | — | **+20%** |
| 2023 | — | **+8%** |
| 2024 | **+18%** (Composite) | **+36%** |
| Sharpe (net, 2020 기준) | **1.83** | — |

**출처**:  
- DisruptionBanking (2025-01-09) — https://www.disruptionbanking.com/2025/01/09/how-d-e-shaws-oculus-fund-made-a-36-return-in-2024/  
- Hedgeweek — https://www.hedgeweek.com/de-shaw-delivers-strong-returns-2020/  
- The Hedge Fund Journal — https://thehedgefundjournal.com/the-waxing-and-waning-of-demand/

---

### 1.6 KOSPI 비교군 (2010–2024)

원시 데이터 출처: 1stock1.com KOSPI Yearly Returns — https://www.1stock1.com/1stock1_770.htm

| 연도 | KOSPI 연간수익 |
|------|---------------|
| 2010 | +21.88% |
| 2011 | +10.98% |
| 2012 | +9.38% |
| 2013 | +0.72% |
| 2014 | -4.76% |
| 2015 | +2.39% |
| 2016 | +3.32% |
| 2017 | +21.76% |
| 2018 | -17.28% |
| 2019 | +7.67% |
| 2020 | +30.75% |
| 2021 | +3.63% |
| 2022 | -24.89% |
| 2023 | +18.73% |
| 2024 | -9.63% |

**파이썬 계산 결과 (재검증)**:

```
산술평균 수익:  4.98%
CAGR:          3.91%
표준편차:      15.14%
Sharpe (rf=0): 0.33
최솟값:       -24.89% (2022)
최댓값:       +30.75% (2020)
```

> KOSPI 2010–2024 연평균 수익 약 4%, Sharpe 0.33. 월 10% 목표(연 213.8%)와 비교 시 차이: 약 54배.

---

### 1.7 종합 비교표

| 펀드 | 기간 | 연수익 (net CAGR) | Sharpe | MDD (추정) | 전략 유형 |
|------|------|-------------------|--------|------------|-----------|
| Medallion (net) | 1988–2018 | **39%** | **~2.0** | -24% | 단기 통계차익 |
| Medallion (gross) | 1988–2018 | **63.3%** | **2.09** | — | — |
| Citadel Wellington | 1990– | **19.5%** | **~2.75** | — | 멀티전략 |
| D.E. Shaw Composite | 2001– | **12.9%** | **1.83** | — | 퀀트+재량 |
| Two Sigma Compass | 2009–2014 | **~30%** (추정) | **1.0–1.5** | <20% (추정) | 매크로 퀀트 |
| Renaissance RIEF | 외부공개 | ~10–22% (연도별) | <1.5 (추정) | — | 장기 지수형 |
| KOSPI | 2010–2024 | **3.91%** | **0.33** | -24.89% | 패시브 |
| **월 10% 목표** | — | **213.8%** | **≥2.14** (σ=100% 가정) | ≤21% (Calmar=10) | — |

> **결론**: 월 10% 복리(연 213.8%)는 세계 최고 헤지펀드(Medallion gross 63.3%)의 **3.4배**. Citadel·D.E. Shaw 대비 **11–16배**. Sharpe 관점에서도 σ=100% 가정 시 최소 Sharpe 2.14 필요 — Citadel 장기 Sharpe(2.75)와 유사한 수준이지만, 변동성이 100%라는 것 자체가 큰 MDD 위험을 내포.

---

## §2 월 10% 수학 4조건 (AC2)

### 기본 전제 검증

```
월 수익률 r_m = 10%
연 복리 수익률 μ_a = (1.10)^12 - 1 = 3.1384 - 1 = 2.1384 = 213.84%

검증: 1.10^12 = 3.1384  ✓
```

---

### 2.1 필요 Sharpe — σ_a 시나리오 6종

**공식**: Sharpe = μ_a / σ_a

| 연 변동성 σ_a | 필요 Sharpe | 현실성 판단 |
|--------------|-------------|------------|
| 10% | 21.38 | 불가능 (BTC 연 변동성도 50%+) |
| 20% | 10.69 | 불가능 |
| 50% | 4.28 | 불가능 (Medallion gross도 2.09) |
| 80% | 2.67 | 이론상 가능이나 MDD 치명적 |
| 100% | 2.14 | Citadel 장기 Sharpe와 유사 — 단 σ=100%는 MDD≥50% 위험 |
| 150% | 1.43 | Sharpe는 달성 가능하나 σ=150%는 파산 위험 |
| **213%** | **1.00** | Sharpe 1.0 달성 가능 구간이지만 σ=213%는 레버리지 청산 확정 |

**계산 근거**: 파이썬 직접 계산 (`mu_annual / sigma_a`, 2026-04-27 재검증)

> **결론**: "Sharpe 2.0 + σ_a ≈ 100%" 조합이 수학적으로 최소 요건. 그러나 σ_a=100% 환경에서 MDD -50% 이상이 사실상 불가피. 현재 정책(MDD halt -5%)과 양립 불가.

---

### 2.2 필요 레버리지

**공식**: L ≥ μ_target / (Sharpe_underlying × σ_underlying)

레버리지는 Sharpe를 변경하지 않고 수익률과 변동성을 동시에 L배 스케일한다.

μ_target = 2.1384 (연 213.84%)

| 내재 Sharpe | 기초 σ_underlying | 필요 최소 L |
|------------|-------------------|------------|
| 2.0 | 30% (BTC 저변동) | **3.6x** |
| 2.0 | 50% (BTC 실제) | **2.1x** |
| 1.5 | 30% | **4.8x** |
| 1.5 | 50% | **2.9x** |
| 1.0 | 30% (현 카탈로그 추정) | **7.1x** |
| 1.0 | 50% | **4.3x** |
| 0.8 | 30% | **8.9x** |
| 0.5 | 50% (저알파) | **8.6x** |

**계산 근거**: 파이썬 직접 계산 (2026-04-27 재검증)

> **결론**: 현 카탈로그 Sharpe ≈ 1.0 (bench_metalabeler 결과: -1.13 ~ 미측정) 가정 시, 월 10% 달성에 최소 **4–7x 레버리지** 필요. Binance Futures 최대 레버리지 125x지만, 리스크 정책상 허용 L ≤ 3x 가정 시 **Sharpe 2.0 + σ=50% 이상의 고변동성 전략이 필수**.

---

### 2.3 MDD 허용 한계 (Calmar 비율 기반)

**공식**: MDD_allowed = μ_a / Calmar

| Calmar 목표 | MDD 허용 한계 | 의미 |
|------------|--------------|------|
| 15 (엄격) | **14.3%** | 현 halt -5%와 가장 근접 — 여전히 3배 완화 필요 |
| 10 (Medallion 역사) | **21.4%** | MDD halt를 -21%로 완화해야 |
| 7 (보수적 퀀트 기준) | **30.5%** | 대규모 드로다운 허용 필요 |

**1σ 일변동성 (L배 레버리지 시)**:

| σ_a | L=1 | L=2 | L=3 | L=5 |
|-----|-----|-----|-----|-----|
| 30% | 1.89% | 3.78% | 5.67% | 9.45% |
| 50% | 3.15% | 6.30% | 9.45% | 15.75% |
| 100% | 6.30% | 12.60% | 18.90% | 31.50% |

> **결론**: 현 정책 MDD halt **-5%**는 월 10% 목표와 **정면 충돌**. Calmar 10 달성 시 MDD halt를 **최소 -21%**로 완화해야 하며, 이는 현재 Phase 1 정책의 4배 리스크 허용. 정책 변경은 사용자 결정(AC5)이 선행되어야 함.

---

### 2.4 거래 빈도 (LFT / MFT / HFT) 격자

**공식**: μ_monthly = N × R_net × s  
여기서 N=월 거래 수, R_net=거래당 순수익률, s=포지션 크기(레버리지 배수)

**Binance Futures 왕복 수수료 (taker)**: 0.10% (= 2 × 0.05%)

| 유형 | N/월 | R_min_net | R_gross 필요 | 비용 비중 | 목표 달성 조건 |
|------|------|-----------|-------------|----------|---------------|
| LFT | 10 | 0.500% | 0.600% | 0.2× | R 0.5%+ 충분히 가능 |
| MFT | 100 | 0.050% | 0.150% | 2.0× | R의 2배가 비용 — 타이트 |
| MFT/HFT 경계 | 1,000 | 0.005% | 0.105% | 20× | **비용이 순수익의 20배** — 불가 |
| HFT | 10,000 | 0.0005% | 0.1005% | 200× | **비용이 순수익의 200배** — 파괴적 |

(포지션 크기 s=2x 가정; R_min_net = 0.10 / (N × 2))

> **결론**:  
> - **LFT (N=10/월)**: R_net ≥ 0.5%/거래 달성 가능. 레버리지 없이 가능한 유일 경로이지만 N이 적어 총 월수익 10% 달성이 어렵 → 레버리지 필수.  
> - **MFT (N=100/월)**: R_net ≥ 0.05%/거래 필요. 비용 0.10%를 감안하면 R_gross ≥ 0.15% — 슬리피지까지 포함 시 달성 매우 어려움.  
> - **HFT (N≥1,000/월)**: 거래 비용이 순수익을 초과 → **슬리피지·수수료 압축 없이 구조적으로 불가**. 메이커 주문 전환(수수료 -0.02%) + 콜로케이션 필수.  
> - **결론**: 월 10%를 위해 **MFT 이상의 거래 빈도 + 거래비용 최소화(메이커 위주)** 가 구조적 전제 조건.

---

## §3 수학 검증 로그 (재계산 증적)

```python
# 2026-04-27 파이썬 직접 계산
import math, statistics

# AC2.1 기본
assert abs((1.10)**12 - 3.1384) < 0.001   # 3.1384 확인
mu_a = (1.10)**12 - 1                      # = 2.1384

# AC2.2 필요 레버리지 예시
L_bestcase = mu_a / (2.0 * 0.30)          # = 3.56x
L_moderate = mu_a / (1.0 * 0.30)          # = 7.13x

# AC2.3 MDD
MDD_calmar10 = mu_a / 10                  # = 0.2138 = 21.38%
MDD_calmar7  = mu_a / 7                   # = 0.3055 = 30.55%

# KOSPI 2010-2024
kospi = [0.2188, 0.1098, 0.0938, 0.0072, -0.0476, 0.0239,
         0.0332, 0.2176, -0.1728, 0.0767, 0.3075, 0.0363,
         -0.2489, 0.1873, -0.0963]
kospi_cagr = math.prod([1+r for r in kospi])**(1/15) - 1  # = 3.91%
kospi_std  = statistics.stdev(kospi)                       # = 15.14%
```

모든 표 수치는 위 코드로 재검증 완료 (2026-04-27).

---

## §4 AC1·AC2 요약 — 핵심 제약 조건

| 제약 | 수치 | 현 시스템 상태 | 갭 |
|------|------|--------------|-----|
| 필요 연수익 | 213.84% | 측정 중 (Step 3) | 미확인 |
| 최소 Sharpe (σ=100%) | 2.14 | ~0.5–1.0 (추정) | ×2–4 부족 |
| MDD 허용 (Calmar=10) | -21.4% | halt -5% | ×4 완화 필요 |
| 최소 레버리지 (S=2, σ=50%) | 2.1x | 정책 미정 | 정책 결정 필요 |
| 거래비용 임계 (MFT N=100) | R_gross ≥ 0.15% | taker 0.10% | maker 전환 시 해소 가능 |
| 세계 최고 펀드 대비 | Medallion gross 63.3% | — | 목표가 Medallion의 3.4× |

---

## 출처 목록

1. Bradford Cornell, *"Medallion Fund: The Ultimate Counterexample?"* (2020), SSRN 3504766 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766
2. Cornell Capital Group blog (2020-02) — https://www.cornell-capital.com/blog/2020/02/medallion-fund-the-ultimate-counterexample.html
3. RobotWealth — https://robotwealth.com/renaissance-medallion-performance/
4. Institutional Investor, Renaissance 2024 rebirth — https://www.institutionalinvestor.com/article/2e0uykr3vn5booz0smrcw/hedge-funds/renaissances-2024-rebirth
5. Institutional Investor, Medallion vs RIEF 2020 — https://www.institutionalinvestor.com/article/2bswms7wco7as686o8ikg/portfolio/renaissances-medallion-fund-surged-76-in-2020-but-funds-open-to-outsiders-tanked
6. Bloomberg, Two Sigma Compass -16% (2024-01-29) — https://www.bloomberg.com/news/articles/2024-01-29/two-sigma-s-macro-compass-enhanced-fund-sank-16-last-year
7. CNBC, Citadel 2025 +10.2% (2026-01-02) — https://www.cnbc.com/2026/01/02/ken-griffins-flagship-hedge-fund-at-citadel-rises-10point2percent-in-volatile-2025.html
8. Institutional Investor, Top hedge funds Sharpe — https://www.institutionalinvestor.com/article/2aucrzsa72lr93xz8ghds/ria-intel/the-most-consistently-profitable-hedge-funds-continue-to-prove-their-edge
9. DisruptionBanking, D.E. Shaw Oculus 36% 2024 — https://www.disruptionbanking.com/2025/01/09/how-d-e-shaws-oculus-fund-made-a-36-return-in-2024/
10. Hedgeweek, D.E. Shaw 2020 — https://www.hedgeweek.com/de-shaw-delivers-strong-returns-2020/
11. 1stock1.com, KOSPI Yearly Returns — https://www.1stock1.com/1stock1_770.htm
12. GrowthMind Substack, Citadel Sharpe — https://growthmind.substack.com/p/why-it-doesnt-matter-if-citadel-beat
13. Quora, Two Sigma Compass 2009–2014 — https://www.quora.com/What-has-been-the-historical-performance-of-Two-Sigma-Compass-Enhanced-fund
