---
type: research
id: 20-position-sizing
name: "포지션 사이징 — Kelly·Fractional Kelly·Vol Targeting·ERC·Risk Parity·HRP"
sources:
  - https://www.princeton.edu/~wbialek/rome/refs/kelly_56.pdf
  - https://www.eecs.harvard.edu/cs286r/courses/fall12/papers/Thorpe_KellyCriterion2007.pdf
  - http://www.thierry-roncalli.com/download/erc-slides.pdf
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678
  - https://www.bis.org/publ/bppdf/bispap113q_rh.pdf
---

# 포지션 사이징 — Kelly·Fractional Kelly·Vol Targeting·ERC·Risk Parity·HRP

> [[01-research-plan]] 의 항목 17 "포지션 사이징·켈리·변동성 타겟팅" 에 대응. [[09-system-components]] 의 `PositionSizer` 모듈 설계, [[risk-rule-dsl]] 의 `per_position.max_weight_pct` 값 결정, [[13-feature-alpha-catalog]] 의 ATR 기반 사이징 근거를 모두 한 곳에 정리한다.

---

## 1. 문제 정의

전략이 매수/매도 방향을 결정했다 가정한 후, **"얼마나 사는가(크기)"** 를 결정하는 문제. 잘못된 사이징은 아무리 좋은 신호도 파괴한다.

세 가지 결정 변수:
1. **단일 포지션 크기** — 계좌의 몇 %를 한 종목에 쓸지
2. **포트폴리오 총 레버리지** — gross 노출 ÷ equity
3. **상대 가중** — 여러 종목 보유 시 서로의 비중 비율

세 가지를 각각 다른 접근이 다룬다.

---

## 2. Kelly Criterion — 기대 성장률 최대화

### 2.1 이론

Kelly (1956) 는 반복 베팅 시 **장기 로그 자산 성장률을 최대화** 하는 베팅 비율을 도출. 단순 이분 결과 ($p$ 확률로 $b:1$ 배당) 에서:

$$
f^* = \frac{bp - (1-p)}{b} = \frac{\text{edge}}{\text{odds}}
$$

예: 승률 55%, 손익비 1:1 → $f^* = 0.55 - 0.45 = 0.10$ (자산의 10%).

### 2.2 연속 수익률 버전

일반화된 Kelly (정규 가정):

$$
f^* = \frac{\mu - r_f}{\sigma^2}
$$

(Sharpe ratio ÷ 변동성). Markowitz 평균-분산 최적해의 단일 자산 한계와 수학적으로 동등.

### 2.3 왜 "그대로 쓰면 안 되는가"

- **파라미터 추정 오차에 취약**: μ 추정이 20% 빗나가면 f* 는 50%+ 빗나감
- **Log-utility 가정**: 실제 사용자의 risk tolerance 와 불일치 (대부분 risk-averse 가 더 강함)
- **Drawdown 극단**: Kelly 최적 비율로 운용 시 이론적으로 최대 50% drawdown 은 일상 (Thorp 1997)

### 2.4 Fractional Kelly (실무 표준)

$f_{\text{use}} = k \cdot f^*, \quad k \in [0.2, 0.5]$

- $k = 0.5$ (Half Kelly) → 장기 성장률은 Full Kelly 의 75% 유지하면서 variance 는 절반 이하 감소
- $k = 0.25$ (Quarter Kelly) → Thorp 의 실제 권고, 변동성 대폭 감소

본 프로젝트 권고값: **Half Kelly (k=0.5) 가 기본, 신호 신뢰도 낮은 구간은 Quarter Kelly**.

---

## 3. Volatility Targeting — 변동성 스케일

### 3.1 개념

포지션 크기를 **예상 변동성의 역수에 비례** 시켜, 포트폴리오 실현 변동성을 타겟값($\sigma_{\text{target}}$, 예: 연 10%) 주위로 유지.

$$
w_i = \frac{\sigma_{\text{target}}}{\sigma_i \cdot \sqrt{252}}
$$

- $\sigma_i$ = 종목 i 의 일간 수익률 표준편차 (ATR 20일 EWMA 등)
- 변동성 급등 시 자동 축소, 안정 시 자동 확대 → drawdown 완화 효과 실증 (Harvey et al. 2018)

### 3.2 Kelly 와의 관계

$\mu_i$ 가 일정하다고 가정하면 Kelly 의 $f^*_i = \mu / \sigma^2$ 이므로, vol targeting $(1/\sigma)$ 은 **"정보비율 가정 하의 축약된 Kelly"** 로 볼 수 있다. 두 접근은 배타적이 아니라 보완적 — Kelly 가 기대 엣지를, vol targeting 이 리스크 스케일을 맡는다.

### 3.3 구현

- 변동성 추정: ATR(14) ([[13-feature-alpha-catalog]] §1.2) 또는 EWMA σ(λ=0.94, RiskMetrics 표준)
- 리밸런스 주기: 일 1회 (비용 고려 시 주 1회 허용)
- 타겟 변동성: KR 중대형주 기준 **연 10% 이하** 권장 (KOSPI 연 변동성 약 18% 대비 보수적)

---

## 4. Risk Parity — 리스크 기여 균등화

### 4.1 모티베이션

60/40 포트폴리오는 **자본 배분상** 분산되지만 **리스크 기여상** 은 주식이 ~90% 를 점유 (Maillard et al. 2010). Risk parity 는 각 자산의 포트폴리오 변동성 기여를 같게 만든다.

### 4.2 수식 (ERC — Equal Risk Contribution)

자산 $i$ 의 한계 리스크 기여:

$$
\text{MRC}_i = \frac{\partial \sigma_P}{\partial w_i} = \frac{(\Sigma w)_i}{\sigma_P}
$$

총 기여 $\text{RC}_i = w_i \cdot \text{MRC}_i$. ERC 조건:

$$
w_i \cdot (\Sigma w)_i = w_j \cdot (\Sigma w)_j, \quad \forall i, j
$$

해는 수치적 최적화로 구함 (cvxpy · PyPortfolioOpt 구현 제공).

### 4.3 장단점

| 구분 | 내용 |
|------|------|
| 장점 | 평균 수익률 추정 불필요 (Σ만 있으면 됨), out-of-sample Sharpe 가 최소분산·등가중 대비 우수 (Maillard 2010) |
| 단점 | 레버리지 의존 (목표 수익을 맞추려면 leverage 필요), 상관 구조 붕괴 시 취약 |

### 4.4 본 프로젝트 적합성

**KRX 개인 계좌 = 레버리지 제한적** → ERC 그대로 쓰기 어려움. 대안: **unlevered risk parity** — $w \propto 1/\sigma_i$ 에 섹터 제약만 얹는 단순화 버전.

---

## 5. Hierarchical Risk Parity (HRP)

### 5.1 동기

López de Prado (2016) 는 샘플 공분산의 불안정성이 Markowitz·Risk Parity 해를 파괴하는 것을 지적. HRP 는 **Σ 역행렬을 피하고 계층 클러스터링으로 대체**.

### 5.2 3단계 알고리즘

1. **Tree Clustering**: 상관 거리 $d_{ij} = \sqrt{0.5(1 - \rho_{ij})}$ 로 계층 클러스터
2. **Quasi-Diagonalization**: 리프 순서 재배열 → 공분산 행렬이 블록 대각에 가까워짐
3. **Recursive Bisection**: 트리를 재귀적으로 두 그룹으로 나누며 역변동성 비율로 가중

### 5.3 장점

- Σ 역행렬 불필요 → 수치적 안정성 극상
- out-of-sample Sharpe 가 평균-분산·ERC 대비 +10~30% (저자 실험)
- 클러스터 단위 해석 가능

Python 구현: `pypfopt.HRPOpt`, `riskfolio-lib` 공식 제공.

---

## 6. 비교표

| 방법 | 입력 | 강점 | 약점 | 본 프로젝트 적합성 |
|------|------|------|------|-------------------|
| **Full Kelly** | μ, σ² | 장기 성장률 최적 | Drawdown 극단 | 금지 |
| **Half/Quarter Kelly** | μ, σ² | 실무 균형 | μ 추정 필요 | 신호당 f 계산 시 표준 |
| **Vol Targeting** | σ | 단순, 견고 | 상관 무시 | 단일 전략 기본값 |
| **ERC** | Σ | μ 불필요 | 레버리지 필요 | 멀티전략 combiner |
| **HRP** | ρ | 안정, 해석가능 | 학술적 신상 (2016) | 20종목+ 포트폴리오 |

---

## 7. 본 프로젝트 SOP (제안)

계층적으로 4단계 사이징을 적용한다.

```
신호 → Half Kelly 로 종목별 희망비율 f_i 계산
      → f_i 를 σ_i 역수로 normalize (vol targeting)
      → HRP 로 종목 간 상대 가중 보정 (20+ 종목일 때)
      → [[risk-rule-dsl]] 의 per_position·sector_limits·drawdown 으로 상한 clamp
      → 최종 주문 크기
```

제약 순서는 **리스크 하한에서 상한으로** 흐른다 — 수학적 최적해가 정책 한도를 초과하면 정책이 이긴다.

### 7.1 PositionSizer 모듈 인터페이스 (제안)

```python
class PositionSizer(Protocol):
    def size(
        self,
        signals: dict[str, SignalStrength],  # symbol -> p, expected_return, sigma
        account: AccountState,               # equity, existing_positions
        cov: np.ndarray,                     # LW shrinkage Σ from [[19-portfolio-risk]]
        policy: Policy,                      # [[risk-rule-dsl]] Policy
    ) -> dict[str, float]:                   # symbol -> target_weight
```

[[09-system-components]] 의 `PositionSizer` 박스는 이 인터페이스의 구체화다.

---

## 8. 한국 시장 주의사항

1. **공매도 제약** — 개인 공매도는 제한적. Long-only 로 $w_i \geq 0$ 제약 필수
2. **레버리지** — 증권사별 신용·미수 한도 다름. 리스크 parity 의 "risk scaling via leverage" 는 보수적으로
3. **최소 거래단위** — 1주 단위 (우선주·일부 ETN 제외) → 소액 계좌에서 비중 이산화 오차 발생
4. **거래세** — KOSPI 0.20%, KOSDAQ 0.20% ([[tax-automation]]) → 고회전 리밸런스는 비용으로 엣지 소실
5. **가격제한폭 ±30%** ([[07-market-microstructure-basics]]) — 일 단위 ruin 확률 유한, Kelly 의 "무한 베팅 수렴" 가정 일부 깨짐

---

## 9. 체크리스트 (운영)

- [ ] Full Kelly 사용 금지 — Half Kelly 상한
- [ ] 단일 전략 σ 타겟팅: 연 10% 이하
- [ ] 20종목+ 포트폴리오: HRP or ERC 적용
- [ ] Long-only 가정 명시 (개인 공매도 제약)
- [ ] [[risk-rule-dsl]] 의 `per_position.max_weight_pct` 가 최종 clamp
- [ ] 리밸런스 주기: 일/주 단위, 거래세 영향 시뮬레이션 후 결정

---

## 참고 노트

- [[01-research-plan]] — 본 노트가 항목 17 "신규 이슈 제안" 대응
- [[09-system-components]] — `PositionSizer` 박스 구현 근거
- [[13-feature-alpha-catalog]] — ATR·EWMA σ 계산 (vol targeting 입력)
- [[19-portfolio-risk]] — 공분산 Σ 추정 (본 노트의 ERC/HRP 입력)
- [[12-validation-protocol]] — 사이징 방법별 backtest 검증
- [[risk-rule-dsl]] — per_position·sector_limits 로 최종 clamp
- [[07-market-microstructure-basics]] — KRX 거래세·가격제한폭 맥락
- [[tax-automation]] — 거래세 0.20% 가 리밸런스 주기 결정에 영향
- [[max-drawdown-5pct]] — 사이징 결과의 drawdown 상한

---

## 출처

- Kelly, J. L. (1956). *A New Interpretation of Information Rate*. Bell System Technical Journal, 35, 917–926. <https://www.princeton.edu/~wbialek/rome/refs/kelly_56.pdf>
- Thorp, E. O. (1997). *The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market*. <https://www.eecs.harvard.edu/cs286r/courses/fall12/papers/Thorpe_KellyCriterion2007.pdf>
- MacLean, L. C., Thorp, E. O., & Ziemba, W. T. (eds.) (2010). *The Kelly Capital Growth Investment Criterion*. World Scientific.
- Maillard, S., Roncalli, T., & Teiletche, J. (2010). *The Properties of Equally Weighted Risk Contribution Portfolios*. Journal of Portfolio Management, 36(4), 60–70. <http://www.thierry-roncalli.com/download/erc-slides.pdf>
- Qian, E. (2005). *Risk Parity Portfolios: Efficient Portfolios Through True Diversification*. PanAgora Asset Management. <https://www.panagora.com/wp-content/uploads/Risk-Parity-Portfolios-Efficient-Portfolios-Through-True-Diversification-PanAgora.pdf>
- López de Prado, M. (2016). *Building Diversified Portfolios that Outperform Out of Sample*. Journal of Portfolio Management, 42(4), 59–69. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678>
- Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., & Van Hemert, O. (2018). *The Impact of Volatility Targeting*. Journal of Portfolio Management, 45(1). <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538>
- Bailey, D. H. & López de Prado, M. (2012). *The Sharpe Ratio Efficient Frontier*. Journal of Risk, 15(2), 3–44.
- PyPortfolioOpt HRP — <https://pyportfolioopt.readthedocs.io/en/latest/OtherOptimizers.html#hierarchical-risk-parity-hrp>
- riskfolio-lib — <https://riskfolio-lib.readthedocs.io/>
- RiskMetrics Technical Document (1996) — EWMA λ=0.94 근거. <https://www.msci.com/documents/10199/5915b101-4206-4ba0-aee2-3449d5c7e95a>
