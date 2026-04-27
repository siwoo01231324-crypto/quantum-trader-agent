# feat: 신규 전략 — 이랑이 VWMA100 단타 기법 카탈로그 (research + vwma-cross-v1)

## 사용자 관점 목표
전업 단타 트레이더 "이랑이" (유튜브 "새로운 부자TV" 인터뷰, 누적 70~80억 수익) 가 공개한 **8가지 기법** 을 프로젝트의 전략·feature·필터로 체계화하고, **사전 등록 factorial 실험** 으로 유의성 검증까지 본 이슈 범위에 포함한다.

## 원본 자료 (착수 전 필수 시청/전사)

- **YouTube**: https://youtu.be/j_0FRRgYYN8?si=DL8JvdJvZp37Gc7s
- **채널**: 새로운 부자TV
- **출연**: 이랑이 (전업 4년차, 단타, 누적 70~80억)
- **길이**: 25:54

**착수 첫 단계**: `/video-summary <위 URL>` 로 전사·요약본을 생성하여 `docs/research/raw/iranyi-vwma-<YYYYMMDD>.md` 에 저장하고 본 이슈의 "8가지 기법" 과 비교 대조한다.

## 배경 — 영상에서 추출한 8가지 기법 전문

### 1. VWMA 100 (거래량 가중 이동평균) — 본 영상의 "제1비법"
- 정의: 종가 아닌 **체결량 가중** 이평선
- 영상 주장: 7/15/50/100/200/400 중 VWMA 100 이 시그널 신뢰도 1위
- 사용: 역배열 구간 캔들이 VWMA100 상향 돌파 시 매수 / VWMA100 저항받을 때 익절
- 기간 100 고정 (50·75 는 단기 EMA 과유사, 200 은 이격 과대)
- 영상 주장 효과: "월 3억 → VWMA 적용 첫 달 13억"
- 참고: López de Prado AFML Ch.2 "information-driven bars" 가 개념적 유사

### 2. 프랙탈 멀티프레임 일치 (Multi-TF Self-Similarity)
- 원문: "1시간봉 파동이 일봉에서 똑같이 반복된다, 멀티버스 같은 느낌"
- 적용: 매수는 작은 프레임(5분/15분), 익절·추세는 큰 프레임(30분/1시간/4시간)
- 자동화 feature: 상위 TF VWMA100 정배열 여부 boolean

### 3. 이평선 자석 이론 (Mean Reversion to MA)
- "이평선이 강력한 자석, 캔들이 이격될수록 강하게 당긴다"
- z-score(거리/σ) > 2 시 카운터트레이드
- #79 Mean Reversion 전략과 중복 가능 — VWMA100 기반 variant 로 차별화

### 4. 이평선 경로 예측 (Forward MA Projection) — 자동화 feature 화
- EMA slope 선형 외삽 → t+N 시점 EMA 값 추정 → 캔들과의 교차 확률
- Features: `ema_slope_k`, `ema_curvature`, `ema_proj_n`, `eta_to_cross`, `price_to_ema_gap_at_n`

### 5. Turning-Point-Only 전략 (하락추세 중 반등만 타격)
- R:R = 기대 7% / 손절 1% ≈ 1:6
- Expectancy > 0 조건: P(win) > 14%
- VWMA100 cross 와 결합 (cross 시점이 turning point)

### 6. 상대강도 필터 (Cross-sectional RS via UBAI)
- 업비트 알트코인 인덱스(UBAI) 대비 상대강도 양수 + 거래대금 상위
- 이론 근거: Jegadeesh & Titman (1993)

### 7. Time-of-Day / Day-of-Week 필터
- 영상 주장:
  - 오전 10:30~11:00 = 펌핑 끝 · 실망 매물 → 매수 금지
  - 주말 = 세력 휴식 · 거래량 사망 → 거래 회피
- 검증: 시간대별 kurtosis·volume 프로파일 실측 (KRX + Binance)

### 8. POC / VPVR 기반 지지·저항 + 호가창 "힘 빠짐" — 자동화 feature 화
- POC (Point of Control) = 가장 많은 거래량 체결된 가격대
- "호가창 힘 빠짐" 정량화 (#80 tick 데이터 의존):
  - OBI (Order Book Imbalance) = `(bid_vol - ask_vol) / (bid_vol + ask_vol)`
  - OFI (Order Flow Imbalance) = 호가창 레벨별 변화 누적
  - Microprice vs Mid gap
  - Top-of-book depth decay rate
  - Trade arrival intensity (Hawkes/Poisson)

## 영상 기법의 한계 (비판적 관점 — 착수 시 유의사항)

1. **표본 편향**: "월 3억 → 13억" 단일 사례 — 유의수준 없음
2. **Survivor bias**: 성공자 1인 기법, 실패자 N명 미확인
3. **Narrative 의존**: "세력이 주말 클럽" 같은 주장은 검증 불가. 결과(주말 거래량 감소)만 실측
4. **Overfit risk**: 기간 100 은 post-hoc 선택 — PurgedKFold(#85) 재검증 필수
5. **"자동화 불가" 는 오해**: "손으로 그리기", "호가창 힘 빠짐" 모두 feature engineering 으로 자동화 가능. CLAUDE.md §6 불변식은 "LLM이 주문 실행·리스크 결정에 직접 개입 금지" 이지, 결정론적 feature 계산을 금지하지 않음.

## 실험 설계 — 사전 등록 Factorial Experiment

**동기**: "VWMA 단독 테스트 → 실패 → 포기" 는 premature rejection 위험. 반대로 "단독 실패 → 사후적으로 feature 추가 → 통과" 는 p-hacking (HARKing). 해결: **첫 백테스트 실행 전 variant 를 모두 확정**, 같은 CV split 에서 일괄 실행, Deflated Sharpe Ratio 로 multi-testing 보정, **전부 honest 보고**.

### Pre-registered Variant Matrix (착수 전 확정, 사후 추가 금지)

| ID | 구성 | 근거 |
|---|---|---|
| A | VWMA100 cross 단독 | 영상 제1비법 순수 검증 (baseline) |
| B | A + `ema_slope > 0` filter | 영상 "이평선 경로 예측" 의 자동화 최소형 |
| C | A + multi-TF 확인 (상위 TF VWMA100 도 정배열) | 영상 "프랙탈" 자동화 |
| D | A + time-of-day gate (10:30~11:00 · 주말 배제) | 영상 "세력 휴식" 자동화 |
| E | A + cross-sectional RS (UBAI 대비 상위 Q) | 영상 "상대강도" 자동화 |
| F | A + POC 거리 필터 | 영상 "POC 지지/저항" 자동화 |
| G | A + order-book flow (OBI + OFI + microprice-mid gap) | 영상 "호가창 힘 빠짐" 자동화 (#80 tick 의존) |
| H | A + B + C + D + E + F + G (full stack) | 전 기법 결합 — 상호작용 효과 측정 |

### 판정 규칙
- 모두 같은 Purged K-Fold (k=5, embargo=24h) CV split
- 동일 데이터: BTC/ETH 2020-01 ~ 2025-12, Binance 1m OHLCV + L2 tick
- 메트릭: Sharpe, Sortino, MDD, Calmar, avg R:R, turnover
- **Multi-testing 보정**: Deflated Sharpe Ratio (DSR, López de Prado 2014) — N=8 variant 으로 trial-adjusted
- 편입 조건: **DSR > 1.0 && out-of-sample MDD < 25%**
- 기각 시: research 노트에 negative result 증거 보존

## 완료 기준

### Research (선행)
- [x] Research 노트 4건 작성 (`docs/background/` 선검색 완료 — VWMA/이랑이 직접 매칭 0건, gap 정당성 확인)
  - [x] `docs/background/40-vwma-volume-weighted-ma.md` (ID 35 점유로 36~39 시프트, /plan 합의)
  - [x] `docs/background/41-multi-tf-fractal-trading.md`
  - [x] `docs/background/42-cross-sectional-momentum-crypto.md`
  - [x] `docs/background/43-orderbook-flow-features.md`
- [x] 원본 영상 전사 `docs/research/raw/iranyi-vwma-2026-04-27.md` (737 라인 + 8 기법 매핑)
- [x] 5년 BTC/ETH 1m 실데이터 fetch (`lake/ohlcv/freq=1m/year={2020..2025}`, 283 MB, .gitignore)

### Validation 인프라 (Q4 신규)
- [x] `src/ml/validation/deflated_sharpe.py` — PSR + DSR (Bailey & López de Prado 2014)
- [x] `src/ml/validation/cscv.py` — CSCV C(16,8)=12,870 조합
- [x] `src/ml/validation/pbo.py` — PBO convenience wrapper
- [x] `src/ml/validation/.ai.md` 신규
- [x] `src/ml/.ai.md` scope 확장 (AFML 기반 ML + Validation 도구체인)
- [x] PurgedKFold 와 인터페이스 통합 (bench script 에서 합성)
- [x] 단위 테스트 18 건 (PSR closed-form, DSR monotonicity, PBO ∈ [0,1], CSCV n_combinations)

### Feature 모듈 (7건)
- [x] `src/features/vwma.py` — `vwma()`, `vwma_cross()`
- [x] `src/features/ma_projection.py` — `ema_slope`, `ema_curvature`, `ema_projection`
- [x] `src/features/multi_tf.py` — 상위 TF alignment (label='right' 인과)
- [x] `src/features/time_of_day.py` — KST 10:30~11:00 + 주말 gate (Variant D 파라미터 동결)
- [x] `src/features/cross_sectional_rs.py` — RS, quartile, `compute_ubai()` (DI fetcher)
- [x] `src/features/poc.py` — Point of Control + distance + volume_ratio
- [x] `src/features/orderbook_flow.py` — OBI/OFI/microprice gap + 1s→1m aggregation
- [x] `src/features/.ai.md` 신규

### 실험 코드
- [x] `scripts/bench_iranyi_variants.py` — 8 variant 일괄, PurgedKFold + DSR + PBO + sha256 + DATA_UNAVAILABLE 분기
- [x] `tests/test_iranyi_features.py` — 18 테스트 (lookahead guard 재사용)

### 전략 (실험 결과 긍정 시에만 — 게이트 미통과로 미생성)
- [ ] ~~승리 variant `src/backtest/strategies/vwma_cross.py`~~ — **5년 SOP gate FAIL**, 경로 B 확정 (negative result)
- [ ] ~~`docs/specs/strategies/vwma-cross-v1.md`~~ — 동일
- [ ] ~~orchestrator 등록 + 수익률 시계열 공급~~ — 동일

### 판정 리포트 (정식)
- [x] `02_implementation.md` — **5년 BTC@1h 실데이터 SOP run** 메트릭 + DSR/PBO + sha256 무결성 + 후속 이슈 후보
- [x] **정식 negative result 확정**: 8 variant 중 6 평가 (G/H L2 tick 부재로 DATA_UNAVAILABLE), best Sharpe = B(+0.346), 4 게이트 모두 FAIL (DSR=0.0, PBO=0.26, MDD=-0.60, mhr=0.40). 정식 사유 + 후속 이슈 후보 6건 02_implementation.md 에 명시.

## 범위 밖 (후속 이슈)

- 영상에서 파생되지 않은 추가 feature 실험 (여기서 variant 확정 후 사후 추가 금지 — 추가하려면 별도 이슈)
- Forward MA Projection 을 primary signal 로 쓰는 독립 전략 (본 이슈는 filter 용도)

## 의존성

- **하드 선결**: #80 (Paper Broker) — L2 tick / order-book 데이터 경로 확보 필요 (variant G, H)
- **하드 선결**: #85 (PurgedKFold + Deflated Sharpe 인프라)
- **권장**: #79 (전략 카탈로그) Mean Reversion variant 와 설계 합의
- **간접**: #70 (리스크 모듈 — register_strategy_returns)

## 착수 전 결정 필요 (Open Questions)

아래 3가지는 Research 단계 (구현 플랜 3) 에서 사람 승인을 받고 **본 이슈에 댓글로 확정 기록** 후 다음 단계로 진행. 확정 전 feature 구현 착수 금지.

1. **Variant 수 확정**: 현재 8개 (A~H). DSR trial penalty 를 완화하려면 E(cross-sectional RS) / F(POC) 를 후속 이슈로 빼서 6개로 축소할지 결정. 결정 근거: multi-testing 검정력 vs 영상 기법 커버리지 trade-off.
2. **DSR threshold**: 현재 `> 1.0` 가정. #85 의 project-wide 정책과 일치 여부 확인 후, 불일치 시 프로젝트 표준으로 정렬. 없으면 본 이슈에서 프로젝트 표준으로 정책 신규 정의 후 `docs/specs/validation-protocol.md` 또는 #85 spec 에 반영.
3. **Order-book feature 샘플 빈도**: 1m 집계 vs 1s raw tick. 백테스트 비용(스토리지·연산)·정보 손실 trade-off 문서화 후 확정. 기준 데이터: Binance L2 tick 용량 추정 + feature 정보량(mutual information) 간이 측정.

## 구현 플랜

1. `/video-summary` 로 영상 전사 → `docs/research/raw/` 저장
2. 볼트 사전조회 (`docs/background/` · `docs/specs/` grep, CLAUDE.md §조사규칙 준수)
3. Research 노트 4건 초안 작성 → 사람 리뷰 + **위 Open Questions 3건 결정 확정**
4. 7개 feature 모듈 구현 + 단위 테스트
5. `bench_iranyi_variants.py` 로 (확정된 수의) variant 일괄 백테스트, DSR 보정
6. 결과 판정 → 긍정이면 `vwma_cross.py` 구현, 부정이면 negative result 문서화 후 종료

## 개발 체크리스트

- [x] 테스트 코드 포함 (feature 18 + validation 18 = 36 tests passed)
- [x] 해당 디렉토리 .ai.md 최신화 (`src/features/.ai.md` 신규, `src/ml/.ai.md` scope 확장, `src/ml/validation/.ai.md` 신규, `docs/research/.ai.md` + `docs/research/raw/.ai.md` 신규)
- [x] 불변식 위반 없음 (`check_invariants.py --strict` 통과 — 118 노트)
- [x] 영상 주장은 "출처: https://youtu.be/j_0FRRgYYN8" 로 명시 (4 background 노트 + 02_implementation 모두 sources / `## 출처` 섹션)
- [x] Variant matrix 사전 등록 원칙 준수 (sha256 hash 출력 JSON 에 첨부, 사후 추가 금지)


## 작업 내역

- (작업 진행 중 기록)

### 2026-04-27

**현황**: 13/16 완료 (구현 마무리, 정식 판정만 미실행)
**완료된 항목**:
- 영상 전사 + 8 기법 매핑 (`docs/research/raw/iranyi-vwma-2026-04-27.md`, 737 라인)
- Research 노트 4건 (background/36~39, ID 35 점유로 시프트, /plan ralplan iter-2 합의)
- Validation 인프라 (`src/ml/validation/{deflated_sharpe,cscv,pbo}.py` + `.ai.md`, src/ml/.ai.md scope 확장)
- Feature 모듈 7건 (`src/features/{vwma,ma_projection,multi_tf,time_of_day,cross_sectional_rs,poc,orderbook_flow}.py` + `__init__.py` + `.ai.md`)
- Bench 스크립트 (`scripts/bench_iranyi_variants.py` + sha256 + DATA_UNAVAILABLE 분기 + smoke 모드)
- 단위 테스트 36건 통과 (validation 18 + features 18)
- invariants `--strict` 통과 (118 노트, +4 신규)
- 02_implementation.md (smoke run 메트릭 + 정식 판정 보류 사유 명시)
- Open Questions 3건 + Q4 신규 결정 댓글 발행 (#4322461471)
- 01_plan.md status=approved (ralplan Architect+Critic iter-2 합의 완료)

**미완료 항목 (실데이터 의존, 후속 이슈)**:
- 실데이터 fetch (Binance 6년치 1m OHLCV — `scripts/fetch_futures_candles.py` 실행 미수행)
- 정식 8 variant 백테스트 (현재는 synthetic smoke 만)
- L2 tick 데이터 (#80 paper broker 경로 미연결 — variant G/H DATA_UNAVAILABLE)
- UBAI 운영 어댑터 와이어링 (`compute_ubai()` 는 DI fetcher 인터페이스만 구현)
- 전략 코드 (`vwma_cross.py` + spec) — gate 통과 시에만, 현재 smoke 결과로는 보류

**변경 파일** (커밋 대기):
- 신규: 4 background notes, 4 validation modules + 2 tests, 7 feature modules + 1 test, 1 bench script, 1 implementation report, 4 .ai.md, 1 raw transcript + 2 .ai.md
- 수정: 1 .ai.md (src/ml/.ai.md), 00_issue.md / 01_plan.md (work tracking)

**메모**:
- 본 이슈의 산출물은 **재사용 가능 인프라** 로서도 가치 있음 — DSR/PBO/CSCV 는 다른 전략 검증에도 쓰이고, 7 feature 모듈도 promote 가능
- 정식 negative result 확정은 실데이터 6년 + L2 tick 확보 후 후속 이슈에서
- 사전 등록 무결성: VARIANT_REGISTRY sha256 (smoke run: `b8f7c1e8cfe2d941...`) 과 git commit 으로 보존

### 2026-04-28

**현황**: 16/16 완료 (정식 5년 SOP bench 실행 + negative result 확정)
**이전 세션 대비 변경**:
- 5년 BTC + ETH 1m 실데이터 fetch 완료 (`lake/`, 283 MB, .gitignore)
- bench script 에 `--timeframe` (default 5min) 옵션 추가 + sticky-position state machine 으로 backtest 모델 정교화
- 1h 봉 5년 (n_bars=52,585) 정식 SOP run — `bench_output.json` 갱신
- 02_implementation.md → status `negative-result` 로 정식 판정

**5년 정식 결과**:
- best variant B (vwma + ema_slope) Sharpe **+0.346** (baseline A: +0.046, 7배 개선)
- DSR=0.0 / PBO=0.26 / MDD=-0.60 / mhr=0.40 → **4 게이트 모두 FAIL**
- C (multi_tf) 는 A 와 동일 메트릭 (1h 봉에서 효과 없음, 4h/1d 후속 평가 후보)
- E (cross_sectional_rs) Sharpe -1.28 (BTC vs ETH placeholder 한계, 정식 UBAI 어댑터 필요)
- G/H DATA_UNAVAILABLE 유지 (#80 paper broker L2 tick 인프라 부재)

**후속 이슈 후보** (02_implementation §후속 6건):
1. Stop-loss / take-profit backtest 통합 (영상 1% / 7% R:R)
2. L2 tick 인프라 (variant G/H 활성화)
3. 정식 UBAI 어댑터 (Upbit REST)
4. Multi-TF 4h / 1d 상위 frame 재평가
5. 선물 short 변형
6. Multi-asset universe (BTC + ETH + SOL +)

**무결성 증거**:
- `variant_registry_sha256`: `b8f7c1e8cfe2d941382bac3f329804d5...`
- `cv_split_hash`: `3d6d388ed3dab5d4f60f4bc9adf19717...`
- `git_commit`: `7c8e215c0fc9` (실행 시점 master)

**커밋 대기 변경 파일**: 4 background notes + 4 validation modules + 8 features + 3 test files + 1 bench script + 1 work-done report + 5 .ai.md (신규/갱신) + 2 work-tracking 갱신
