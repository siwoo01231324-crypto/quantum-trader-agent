---
type: work-plan
issue: 119
name: "#119 월 10% 수익률 목표 가능성 평가 — 구현 계획"
created: 2026-04-27
---

# [#119] research: 월 10% 수익률 목표 가능성 평가 + 전략·리스크·사이징 재설계 — 구현 계획

> 작성: 2026-04-27
> 브랜치: `research/000119-monthly-10pct-feasibility`
> 워크폴더: `docs/work/active/000119-monthly-10pct-feasibility/`

---

## 완료 기준 (AC, 이슈 body 기준)

- [ ] AC1. 최상위 펀드(Renaissance Medallion·Two Sigma·Citadel) 수익률·Sharpe 벤치마크 수치 정리
- [ ] AC2. 월 10% 달성 수학 조건 4가지 정량화: 필요 Sharpe / 필요 레버리지 / MDD 허용 / 거래 빈도(LFT vs MFT vs HFT)
- [ ] AC3. 현 카탈로그 5종 + 메타라벨러 ON + 레버리지 3-5x 시나리오 백테스트 (3년)
- [ ] AC4. 미달 시 보강 옵션 3가지 제안 (신규 전략·MFT/HFT·옵션 활용 등)
- [ ] AC5. 사용자 권고: (a) 목표 유지 + 공격 정책 도입, (b) 목표 하향 + 보수 유지, (c) 단계별 차등 중 선택 요청
- [ ] AC6. 결과 노트 `docs/background/36-monthly-10pct-feasibility.md` 작성

---

## 사전 조사 결과 (벨트 pre-search · 2026-04-27)

CLAUDE.md "조사·리서치 규칙" 및 user 메모리 "/plan 전 이슈 상태 일괄 실측" 규칙에 따라 작성 전 일괄 조회.

### 1. 기존 볼트 커버리지 (`grep -ri` + read 4 notes)

| 키워드 | 매칭 노트 | 평가 |
|--------|----------|------|
| `Medallion·Renaissance·Two Sigma·Citadel` | `02-terms-quant-vs-quantum.md`, `08-strategy-paradigms.md` | 사례·진입장벽만 — **수익률·Sharpe 수치 없음** |
| `월 10%·monthly 10·target return` | 본 이슈만 | 신규 영역 |
| `레버리지·leverage` | `20-position-sizing.md`, `31-valuation-analysis.md`, `09-system-components.md` | Kelly·vol-targeting 이론 + KRX 개인 레버리지 제약 — 본 이슈에서 재사용 |

→ **결론**: AC6 산출물 `36-monthly-10pct-feasibility.md` 신규 작성 정당. 단, AC2 의 "필요 레버리지" 수학은 기존 `20-position-sizing.md` §2-3 의 Kelly + vol targeting 식을 재사용해 일관성 유지.

### 2. 의존 이슈 실측 상태 (`gh issue view`)

근거: `gh issue view {N}` 2026-04-27.

| # | 제목(요약) | state | 본 이슈와의 관계 |
|---|-----------|-------|-----------------|
| #79 | 전략 카탈로그 확장 5종 | CLOSED | AC3 백테스트 입력 — `scripts/measure_strategy_catalog.py` 재사용 |
| #80 | 라이브 실행 프레임워크 (PaperBroker + Phase 1) | CLOSED | Shadow Paper 실측 자료 활용 |
| #85 | 메타라벨링 LightGBM | CLOSED | AC3 메타라벨러 ON 시나리오 — `scripts/bench_metalabeler_btc.py` 재사용 |
| #94 | 메타라벨러 프로덕션 활성화 | CLOSED | 동일 |
| #95 | 메타라벨러 주간 자동 재학습 + 드리프트 | CLOSED | AC3 단기 적응성 가정 |
| #105 | Phase 2 KIS 모의계좌 | OPEN | AC3 결과가 Phase 2 정책에 영향 — 후행 |
| #107 | Phase 3 Live Pilot 5% | OPEN | AC5 권고가 직접 반영됨 — 후행 |
| #120 | per_portfolio_risk watchdog | OPEN | AC4 보강 옵션 시 referral |
| #121 | extreme_fear_threshold 가격 프록시 | OPEN | AC4 후보 |
| #122 | 메타라벨러 재학습 윈도우 검증 | OPEN | AC3 메타라벨러 가정 boundary |
| #138 | Whitepaper v0.2 발행 | OPEN | **이슈 body 의 "백서 §1-10" 미래형 표현 — 현재 `docs/whitepaper/` 는 빈 디렉토리** |
| #142 | Whitepaper v0.1.1 fast-forward | OPEN | 위와 동일 |

→ **이슈 body 의 "백서 §1-10 갱신본 참조" 는 작성 시점 미래형 표현**. 실제 백서는 미작성. 본 이슈는 백서 산출 **선행** (백서 §5/6/7 의 입력이 됨). `docs/work/active/000119-*` 안에 자급자족.

### 3. 코드 자산 실측

- `scripts/measure_strategy_catalog.py` — 5전략 일수익률 + ENB/CVaR 자동 계산 ✅ 그대로 활용
- `scripts/bench_metalabeler_btc.py` — BTC on/off Sharpe·MDD 비교 ✅
- `src/risk/sizing.py` — `kelly_continuous`, `fractional_kelly`, `vol_target` 순수 함수 ✅ AC2 수학에 재사용
- `src/risk/portfolio.py` — `compute_portfolio_risk_from_df` (CVaR/ENB) ✅
- `data_lake/fetcher.py` (#106 closed) — Binance Futures USDT-M 적재기 ✅ 3년치 데이터 가능
- KIS paper 데이터 — 1년 정도 (2025-01 ~ 2026-04). 3년 보강 불가능 → AC3 KRX 부분은 1.3년 한계 명시

---

## 본 이슈 성격 (재서술)

- **Type: research + decision**. 코드 변경 최소(<200줄), 산출물은 1개 background 노트 + 1개 implementation 노트 + 사용자 권고서.
- **결정 트리거**: AC5 의 사용자 권고 (a/b/c) 가 Phase 2/3 (#105/#107) 정책 노브를 결정. 따라서 **사용자 응답까지 본 이슈를 닫지 않는다** (수동 게이트).
- **위험**: 이슈 body 의 "레버리지 3-5x 시나리오 백테스트" 는 백테스트 엔진이 레버리지를 명시적으로 모델링하지 않음 → **사후 곱하기 기반 근사**(returns × L)로 처리 + MDD/CVaR 도 동시 스케일 보고. 이 가정의 한계 명시.

---

## 구현 계획 (Task Flow)

### Step 0. 실행 환경 준비 (≤30분)

- 본 워크폴더에 `02_research.md` 빈 헤더 생성 (조사 노트 누적용).
- `data_lake/binance_futures.parquet` 3년치 (BTCUSDT 15m·1h·4h, ETHBTC 1h, BTCUSDT 4h) 캐시 확보 — `python scripts/fetch_futures_candles.py` 또는 `fetch_candles.py` 사용. 캐시 hit 시 skip.
- KIS 환경변수 미설정 시 KRX 부분은 dry-run synthetic 으로 fallback (이미 `measure_strategy_catalog.py` 에 구현됨) — 실측 1.3년 자료가 있으면 실측 우선.

**검증**: `ls -la data_lake/*.parquet` 로 3년치 (>= 1095일) 확인.

---

### Step 1. AC1 — 헤지펀드 벤치마크 수치 수집 (1-2h, 책상 리서치)

조사 대상 (각각 정량 수치 + 1-2 출처):

| 펀드 | 필요 수치 | 1차 출처 후보 |
|------|----------|--------------|
| Renaissance Medallion | 1988-2018 연평균 net/gross, Sharpe (Cornell 강의록·Bradford Cornell 2020 논문) | Cornell *The Medallion Fund* (SSRN 3679979), Wall Street Journal 인터뷰, Simons 전기 |
| Renaissance RIEF/RIDA (외부 펀드) | net 수익률 (메달리온 vs 외부 격차 검증용) | Hedgeweek 2024 boundary |
| Two Sigma (Compass·Spectrum·Absolute Return) | 2010-2024 연수익·Sharpe (보고된 한도) | Hedge Fund Research, Institutional Investor |
| Citadel Wellington/Tactical | 연수익·Sharpe | Bloomberg, FT |
| D.E. Shaw Composite | 2024 36% 사례 비교 (이미 `08-strategy-paradigms.md` 출처에 링크됨) | 동일 출처 재인용 |
| KRX 비교군 | KOSPI 연환산 수익·변동성 (참고) | KRX 통계 |

**출력**: `02_research.md` "§1 벤치마크" 표 + `36-monthly-10pct-feasibility.md` "§2 정상급 펀드 벤치마크" 섹션.

**검증**: 수치마다 출처 URL/논문 1개 이상. 추정·간접 인용은 **명시**.

⚠️ Medallion 36% 류 통계는 *gross* (수수료 차감 전) 와 *net* (차감 후) 가 크게 다르다 — 둘 다 표시하고 외부 비교는 net 기준만 사용.

---

### Step 2. AC2 — 월 10% 수학 조건 4가지 정량화 (3-4h)

월 복리 10% = 연 213.84% 라는 조건에서:

#### 2.1 필요 Sharpe (정규 가정)

월 수익률 $r_m = 0.10$, 연 환산 $\mu_a = (1.10)^{12} - 1 = 2.1384$.
변동성 시나리오별 필요 Sharpe = $\mu_a / \sigma_a$:

| 연 변동성 σ_a | 필요 Sharpe |
|--------------|-------------|
| 10% | 21.4 (비현실적) |
| 20% | 10.7 |
| 50% | 4.28 |
| 80% | 2.67 |
| 100% | 2.14 |
| 150% | 1.43 |

→ "Sharpe 1.0 정책" 과 양립하려면 σ_a ≈ 213% 필요 → MDD 제어 불가능.
→ "Sharpe 2.0 + σ_a ≈ 100%" 조합이 현실적 minimum.

#### 2.2 필요 레버리지

내재 알파 Sharpe = S, 변동성 = σ_underlying. 레버리지 L 적용 시 Sharpe 는 불변, 수익률·변동성 모두 L 배.

$$L \geq \frac{\mu_{target}}{S \cdot \sigma_{underlying}}$$

본 카탈로그 measured Sharpe 입력 (Step 3 결과) 시 필요 L 도출.

#### 2.3 MDD 허용

월 10% 복리 + 레버리지 L 시 1σ 일변동성 = $L \cdot \sigma_a / \sqrt{252}$.
Calmar = annual_return / |MDD|. Medallion historical Calmar ≈ 7-10 → 동일 Calmar 가정 시 MDD ≤ 21% (target 213% / 10).

→ **현 정책 MDD halt 5% 와 정면 충돌**. 월 10% 추구 시 MDD halt 임계는 최소 -20% (Calmar 10 가정), 보수적으로는 -30% (Calmar 7).

#### 2.4 거래 빈도 (LFT/MFT/HFT)

월 N 거래·평균 R = 거래당 기대 R-multiple 일 때:
$$\mu_{monthly} = N \cdot R \cdot avg\_size$$
0.10 = N × R × s 의 (N, R, s) 3차원 격자 점검 — LFT (N=10/월, R=0.5%, s=2x), MFT (N=100, R=0.05%, s=2x), HFT (N=10000, R=0.005%, s=1x) 등.

→ R-multiple 0.05% 이하는 슬리피지·수수료에 침식됨. **MFT 이상 강제 + 거래비용 압축이 필수 조건**.

**출력**: `02_research.md` "§2 수학" 표 4개 + `36-monthly-10pct-feasibility.md` "§3 월 10% 수학" 4개 표.

**검증**: 표 셀 값을 `python -c` 또는 Jupyter 셀에서 재계산해 정합성 확인 (예: $1.10^{12} = 3.1384$, 따라서 연 $\mu_a = 2.1384$).

---

### Step 3. AC3 — 카탈로그 5종 + 메타라벨러 ON + 레버리지 3-5x 백테스트 (4-6h)

#### 3.1 데이터 윈도우

- 크립토 (BTCUSDT, ETHBTC) — Binance USDT-M 3년치 (2023-04-27 ~ 2026-04-27).
- KRX (momo-kis-v1 005930, breakout-donchian KOSPI200) — 실측 1.3년 + 직전 합성 보강 금지 (룩어헤드 위험) → **3년 요구 미충족 명시 + 1.3년 결과 + Bootstrap 재표본 보완**.

#### 3.2 베이스라인 측정

`scripts/measure_strategy_catalog.py` 재실행 — 단, START_DATE/END_DATE 를 `2023-04-27 ~ 2026-04-27` 로 확장한 사본 `scripts/measure_strategy_catalog_3y.py` 를 만들고 출력 경로를 `docs/work/active/000119-monthly-10pct-feasibility/02_implementation_catalog_3y.md` 로 변경.

산출:
- 5전략 일수익률 시계열 → CVaR(97.5%), ENB, 평균 ρ, 연 Sharpe, 연 수익률, MDD.
- 합산 포트폴리오 (equal-weight 5전략 + ENB 가중) Sharpe·MDD.

#### 3.3 메타라벨러 ON 시나리오

- `scripts/bench_metalabeler_btc.py` 결과 (Sharpe -1.13, 단일 BTC 1년) 를 3년 윈도우로 확장 + on/off 두 케이스 도출.
- 다른 4전략은 메타라벨러 미적용 (#85 는 momo-btc-v2 한정). → **본 이슈 한정 단일 자산 효과만 보고**, 5전략 전반 메타라벨러는 **AC4 후보**.

#### 3.4 레버리지 3-5x 시나리오 (사후 곱하기 근사)

각 전략 일수익률 $r_t$ 에 대해 $r_t^{(L)} = L \cdot r_t - (L-1) \cdot c_{borrow,t}$ ($c_{borrow}$ = 일 펀딩비 0.02%/일 가정 = 연 7.3%, Binance USDT-M 8h funding 평균치).

- L ∈ {1.0, 2.0, 3.0, 4.0, 5.0} 5종.
- MDD = peak-to-trough on $\prod (1 + r_t^{(L)})$ — 레버리지 청산 시뮬레이션 별도(보존: $r_t^{(L)} < -1$ 한계 도달 시 ruin).
- CVaR(97.5%), 연 수익률, Sharpe 동시 표출.

→ **월 10% 도달 여부 판정**: 12개월 롤링 단순수익률 분포에서 ≥ 10% 비율(success rate) + 평균/메디안.

#### 3.5 로직 추가 코드

| 파일 | 변경 |
|------|------|
| `scripts/measure_strategy_catalog_3y.py` | 신규 (기존 스크립트의 윈도우 확장 사본). |
| `scripts/leverage_scenario.py` | 신규 (단일 일수익률 시리즈 → L배 곱셈 + funding cost + MDD/Sharpe/CVaR/월 10% rate 표). |
| `tests/test_leverage_scenario.py` | 신규 1건 (synthetic 입력 → 알려진 L=2 결과 회귀 테스트). |

#### 3.6 출력

`02_implementation.md` 에:
- 5전략 × L=1·3·5 매트릭스 (연 수익률, Sharpe, MDD, CVaR, 월 10% hit ratio).
- 포트폴리오 합산 (등가중 + HRP) × L 매트릭스.
- 메타라벨러 ON/OFF 비교 (BTC 한정).

**검증**:
- `python -m pytest tests/test_leverage_scenario.py -q` 통과.
- 산출물의 **Sharpe(L=k) ≈ Sharpe(L=1)** 정합성 (사후 곱하기 가정의 첫째 sanity check).
- L=5 인데 MDD < L=1 이면 모순 → 즉시 재계산.

---

### Step 4. AC4 — 미달 시 보강 옵션 3가지 (1-2h)

Step 3 결과로 월 10% 미달 (예상됨, Sharpe ≤ 0 환경) 가정 시 후보:

1. **신규 전략 — Renaissance-style 단기 통계차익**: 분단위 mean-reversion + 메타라벨러 (1차) → 백서 §6 신규 항. 후행 이슈 후보.
2. **MFT/HFT 인프라 도입**: 콜로케이션·KRX/Binance WebSocket lag 단축 → #112-114 (특허 차용 OrderRouter·VWAP·TWAP) 우선순위 격상으로 묶음.
3. **옵션 활용 (delta-hedged 변동성 수익)**: 선물 + 옵션 spread (covered call·short strangle). 단 KIS 옵션 거래 권한 별도. KRX 야간 ETF 또는 Binance Options 검토.

각 옵션마다:
- 기대 이익 메커니즘 (수식 1줄)
- 필요 인프라 (코드·외부 연동)
- 예상 구현 기간
- 리스크/실패 모드

**출력**: `36-monthly-10pct-feasibility.md` "§5 보강 옵션 3가지" 섹션.

---

### Step 5. AC5 — 사용자 권고 트리오 (a/b/c) (1h)

3가지 시나리오를 표 1개로 정리:

| 시나리오 | 정책 변경 (Sharpe·MDD halt·레버리지) | Step 3 측정 기반 도달 가능성 | 리스크 | 후행 이슈 영향 |
|---------|-----------------------------------|---------------------------|------|--------------|
| (a) 목표 유지 + 공격 정책 | Sharpe ≥ 2.0, MDD halt -25%, L 3-5x | 30-40% (메타라벨러 ON 가정) | 일간 ruin 확률 X% | #107 Live 5% → 25% 격상, #122 윈도우 단축 |
| (b) 목표 하향 + 보수 유지 | Sharpe ≥ 1.0, MDD halt -5%, L 1.0 | 80% (월 2-3% 목표) | 낮음 | 현 정책 유지 |
| (c) 단계별 차등 | Phase 1 (b), Phase 3 결과 따라 (a) 부분 도입 | 결과 의존 | 중간 | Phase 게이트 명문화 |

**출력**: `36-monthly-10pct-feasibility.md` "§6 사용자 권고" + "§7 결정 요청" (사용자 응답 빈칸).

---

### Step 6. AC6 — 결과 노트 작성 (1h)

`docs/background/36-monthly-10pct-feasibility.md` 신규.
프론트매터: `type: research`, `id: 36-monthly-10pct-feasibility`, `sources: [...]`.
구성:
1. 개요 (월 10% 정의 + 본 노트 위치)
2. 정상급 펀드 벤치마크 (Step 1)
3. 월 10% 수학 4조건 (Step 2)
4. 카탈로그 5종 실측 결과 (Step 3 요약 + 본문은 02_implementation.md 링크)
5. 보강 옵션 3가지 (Step 4)
6. 사용자 권고 a/b/c (Step 5)
7. 결정 요청 (사용자 응답 후 채움)

위키링크: `[[19-portfolio-risk]]`, `[[20-position-sizing]]`, `[[35-meta-labeling-lopez-de-prado]]`, `[[08-strategy-paradigms]]`, `[[12-validation-protocol]]`, 5개 전략 노트.

**검증**:
- `python scripts/check_invariants.py --strict` 통과 (프론트매터 + 위키링크 + ttl 파싱).
- `python scripts/ontology_sync.py --dry-run` 으로 RDF 동기화 가능 확인.

---

### Step 7. 00_issue.md 작업 내역 + AC 체크 (수동, 30분)

- AC1-6 각각 완료 시 `[ ]` → `[x]`.
- 작업 내역 섹션에 2026-04-27 항목 추가.

---

## 변경/생성 대상 파일

| 경로 | 작업 | 종류 |
|------|------|------|
| `docs/work/active/000119-monthly-10pct-feasibility/01_plan.md` | 신규 (본 파일) | doc |
| `docs/work/active/000119-monthly-10pct-feasibility/02_research.md` | 신규 — Step 1·2 책상 리서치 누적 | doc |
| `docs/work/active/000119-monthly-10pct-feasibility/02_implementation.md` | 신규 — Step 3 백테스트 결과 표 | doc |
| `docs/work/active/000119-monthly-10pct-feasibility/00_issue.md` | 업데이트 — AC 체크 + 작업 내역 | doc |
| `docs/background/36-monthly-10pct-feasibility.md` | 신규 (AC6) | doc |
| `scripts/measure_strategy_catalog_3y.py` | 신규 — Step 3.5 | code (파생 스크립트) |
| `scripts/leverage_scenario.py` | 신규 — Step 3.5 | code |
| `tests/test_leverage_scenario.py` | 신규 1건 | test |

**Out of scope (이번 이슈 안 건드림)**:
- `src/risk/sizing.py`, `src/risk/portfolio.py` — 리스크 모듈 변경 금지.
- `configs/orchestrator/*.yaml` — 정책 변경은 AC5 사용자 응답 후 별도 PR (Phase 2/3 이슈).
- `docs/whitepaper/` — 백서는 #138 별도.

---

## 단계별 실행 순서

```
Step 0 (env)
  └→ Step 1 (벤치마크 리서치, 책상 작업)
       └→ Step 2 (수학 4조건, 책상 + 계산 검증)
            └→ Step 3.1-3.4 (3년 백테스트 + 레버리지 + 메타라벨러)
                 └→ Step 3.5 (코드 추가) ⇄ Step 3.6 (산출물)
                      └→ Step 4 (보강 옵션) ─┐
                      └→ Step 5 (a/b/c)     ├→ Step 6 (background 노트)
                                            └→ Step 7 (AC 체크 + 작업 내역)
```

병렬 가능: Step 1 ↔ Step 2 (모두 책상 작업), Step 3.5 코드 작성은 Step 1·2 와 병렬.
**막힘 시 사용자 confirm 필요**: Step 3.4 레버리지 펀딩비 가정 (0.02%/일) 정합성, Step 5 권고 a/b/c 의 정량 임계값.

---

## Guardrails

### Must Have (불변식)

- ✅ 본 이슈는 **research + decision** — 코드 변경은 측정 스크립트 + 1개 테스트로 한정. 리스크/사이저 핵심 모듈 미변경.
- ✅ AC6 노트는 `docs/schemas/note-schemas.md` 의 **research** 타입 스키마 준수 (프론트매터 `type/id/name/sources`).
- ✅ 모든 수치는 출처 명시 (CLAUDE.md "조사·리서치 규칙"). Medallion 류는 net/gross 구분.
- ✅ 위키링크 5종 + `[[19-portfolio-risk]]` `[[20-position-sizing]]` `[[35-meta-labeling-lopez-de-prado]]` 모두 존재 확인.
- ✅ `python scripts/check_invariants.py --strict` 통과.
- ✅ 레버리지 3-5x 결과는 **사후 곱하기 근사** 임을 §4 첫 문단에 명시. 청산 메커니즘은 `r_t^{(L)} <= -1` 한계로 단순화함을 표기.
- ✅ AC5 결정 요청은 **사용자 응답 빈칸** 으로 두고 본 이슈는 응답 전까지 OPEN.

### Must NOT Have (금지)

- ❌ 백테스트 엔진 본체 (`src/backtest/`) 변경 금지 — 본 이슈는 측정만.
- ❌ `src/risk/sizing.py`·`portfolio.py` 변경 금지.
- ❌ orchestrator 정책 (`configs/orchestrator/*.yaml`) 변경 금지 — Phase 2/3 별도.
- ❌ 메타라벨러를 momo-btc-v2 외 전략에 자동 적용 금지 (#85 의 단일 자산 검증 범위 한정).
- ❌ KRX 1.3년 데이터를 3년처럼 표시 금지 — 별도 라벨링.
- ❌ 자동 커밋 금지 (CLAUDE.md "행동 규칙": git commit/push 전 사용자 확인).
- ❌ 이슈 body 의 "백서 §1-10 갱신본" 미래형 표현 그대로 인용 금지 — 실측(현재 비어있음) 으로 재서술.

### 주의사항

- **데이터 다운로드 시간**: Binance Futures USDT-M 3년치 (BTC 15m 약 105k bars + ETHBTC 1h 26k + BTC 4h 6.5k) 첫 fetch 5-10분 예상. `run_in_background` 사용 권장.
- **KIS paper API rate limit**: KOSPI200 200종목 일봉 fetch 시 ~10분. 환경변수 미설정 시 synthetic fallback (이미 구현).
- **펀딩비 가정 보수성**: Binance USDT-M 펀딩 8h 0.01% (연 ~11%) 평균이 더 보수적. 결과 표에 funding=0% / 11% / 25% 3종 sensitivity 함께 표출.
- **사용자 메모리 규칙 준수**: 1) 특허 리서치 1순위 = 시스템 강화 (보강 옵션 §4 에 #111-114 차용 강조). 2) /plan 전 의존 이슈 일괄 실측 (위 §사전 조사 §2 실측 표).

---

## 검증 체크리스트 (Step 6 직후)

- [ ] AC1-6 각 항목별 산출물 링크 1개 이상 확인.
- [ ] `36-monthly-10pct-feasibility.md` 프론트매터 `type/id/name` 일치.
- [ ] 위키링크 7개 모두 실제 노트 존재.
- [ ] `python scripts/check_invariants.py --strict` exit 0.
- [ ] `python scripts/ontology_sync.py --dry-run` 파싱 성공.
- [ ] `pytest tests/test_leverage_scenario.py -q` 통과.
- [ ] AC5 권고서에 정량 수치 (Sharpe, L, MDD, 월 10% hit ratio) 포함.
- [ ] 모든 수치에 출처 또는 계산 근거 1개 이상.

---

## 다음 단계

1. 본 플랜 사용자 승인 → Step 0 환경 준비.
2. Step 1·2 병렬 시작 (책상 리서치).
3. Step 3 백테스트는 Binance Futures 캐시 빌드와 동시 진행.
4. Step 5 권고서 초안 후 사용자 결정 요청 → 응답 시 본 이슈 close + Phase 2/3 이슈에 반영.
