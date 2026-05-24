# feat: universe-scan 패턴 전면 전환 — 풀 스캔 매매 + 통합 카탈로그 + 대시보드 토글·전략별 페이지 + 데몬·Telegram·Docker·daily_check 일괄 리팩토링

## 사용자 관점 목표

거래 대상을 "고정 종목 1~3개" 에서 "**시총·거래량 top-N 풀 스캔 → 신호 부합하는 상위 N 보유**" 로 전면 전환. 모든 전략을 universe-scan 패턴으로 통일하고, 모든 paper 운영 데몬·대시보드·Telegram·Docker·일일 점검 스크립트가 **누락 없이 한 번에 같이** 따라가도록 일괄 리팩토링한다. 또한 R4/R6/momo_kis_v1 까지 단일 카탈로그로 통합하고, 대시보드에서 토글 ON/OFF + 전략별 거래 내역 페이지를 제공한다.

## 배경

### 검증으로 드러난 사실 (2026-05-06)

1. **단일종목 swing 4종 (`swing_kr_daily.py`) 5y bench 부적합** — 4종 중 3종이 5년간 0~5 trades, 1종 (TSMOM 12-1) 만 부분 성과. 단일종목 패턴 자체의 신호 가뭄 문제.
2. **Universe-scan TSMOM 12-1 검증 우위**:
   - KRX (KOSPI top-200 + KOSDAQ top-150 → top-20): Sharpe **0.871** / Ann **22.99%** / MDD -42.99% (vs KOSPI 0.656 / 11.98% / -35.71%)
   - Crypto (Binance top-30 → top-10): Sharpe **1.328** / Ann **90.85%** / MDD -52.42% (vs BTC 0.989 / 51.61% / -76.63%)
3. **현 카탈로그 5개 전략은 검증 미완** — `docs/specs/strategies/*.md` frontmatter 의 `sharpe_bt`/`mdd_bt`/`annual_return_bt` 거의 모두 `null`. Stub 등록 상태.
4. **paper 운영 path 가 2층** — qta.exe orchestrator (production.yaml 5전략) + Task Scheduler (R4/R6 별도). 통합 카탈로그 없음.
5. 패턴 spec `docs/specs/universe-scan-strategy-pattern.md` 도입 + AGENTS.md/CLAUDE.md 갱신은 `feat/swing-strategy-portfolio` 브랜치에 작성 완료 (미커밋).

### 1:1 변환 매핑

| 기존 (single-ticker) | universe-scan 변환본 | 상태 |
|---|---|---|
| momo_kis_v1 (KRX 005930 RSI 이격) | cs_rsi_div_kr | 신규 작성 |
| swing_kr_daily.momo_kis_daily (KRX RSI 이격 daily) | cs_rsi_div_kr 와 동일 (흡수) | — |
| swing_kr_daily.swing_bb_macd (KRX BB+MACD) | cs_bb_macd_kr | 신규 작성 |
| swing_kr_daily.swing_adx_ma (KRX ADX+EMA) | cs_adx_ma_kr | 신규 작성 |
| swing_kr_daily.swing_tsmom_12_1 (KRX 12-1) | **cs_tsmom_kr_daily** | 완료 (bench 통과) |
| momo_btc_v2 (BTC RSI 이격) | cs_rsi_div_crypto | 신규 작성 |
| momo_vol_filtered (BTC MACD+vol) | cs_macd_vol_crypto | 신규 작성 |
| (BTC 단독 미존재) | **cs_tsmom_crypto_daily** | 완료 (bench 통과) |
| breakout_donchian (KOSPI200→top-10) | (이미 universe-scan) | 그대로 |

### Research 필요 (별도 후속, 본 이슈 외)

- R4/R6 (BTCUSDT regime-switching, S2c↔S4 라우팅) — universe 차원 regime 분배는 학술 research 영역. 본 이슈는 **paper 운영 retain + 카탈로그에 통합**만 다룸. 변환은 별도 research 이슈.
- meanrev_pairs (ETH/BTC pair stat-arb) — universe 차원 cointegration scanner 는 별도 research 이슈.

## 완료 기준 (Acceptance Criteria)

### 1. 코드 — universe-scan 1:1 변환 (5개 신규 + 2개 검증완료)

- [ ] `src/universe/krx_top.py` — `top_n_by_marcap(market, n, as_of)` (KOSPI/KOSDAQ 시총 top-N)
- [ ] `src/universe/binance_top.py` — `top_n_by_volume(n, exclude_filters)` (24h 거래량 top-N + stable/wrapped/leveraged 제외)
- [ ] `src/universe/_filters.py` — 공통 유동성·가격 필터
- [ ] (선택, 후속) PIT (point-in-time) 시총 스냅샷 모듈 — survivorship bias 제거. 본 이슈는 current Marcap 기반으로 출발, PIT 는 별도 이슈로 분리 가능.
- [ ] `src/backtest/strategies/cs_rsi_div_kr.py` — momo_kis_v1 + swing_kr_daily.momo_kis_daily 의 universe-scan 변환본
- [ ] `src/backtest/strategies/cs_bb_macd_kr.py` — swing_kr_daily.swing_bb_macd 의 universe-scan 변환본
- [ ] `src/backtest/strategies/cs_adx_ma_kr.py` — swing_kr_daily.swing_adx_ma 의 universe-scan 변환본
- [x] `cs_tsmom_kr_daily` — bench 검증 완료 (`feat/swing-strategy-portfolio` 브랜치). AsyncStrategy wrap + orchestrator 등록 후속.
- [ ] `src/backtest/strategies/cs_rsi_div_crypto.py` — momo_btc_v2 의 universe-scan 변환본
- [ ] `src/backtest/strategies/cs_macd_vol_crypto.py` — momo_vol_filtered 의 universe-scan 변환본
- [x] `cs_tsmom_crypto_daily` — bench 검증 완료 (`feat/swing-strategy-portfolio` 브랜치). AsyncStrategy wrap + orchestrator 등록 후속.
- [ ] AsyncStrategy wrap (7종) — `src/backtest/protocol.py` `AsyncStrategy` 준수, `on_bar(ctx)` 매주 금요일 마감 트리거
- [ ] orchestrator 등록 + `register_strategy_returns(...)` 호출 — universe-scan 7종 모두
- [ ] 단위 테스트 1건 / 전략 (CLAUDE.md "새 전략 추가 시 필수")
- [ ] 5y bench 결과 frontmatter 기록 (`update_strategy_frontmatter` 호출) — Sharpe/MDD/Ann/period

### 2. Broker — 동적 universe quote/order 확장

- [ ] KIS broker (`src/brokers/kis/`) 동적 universe quote — top-20 호가/일봉/잔고 fetch (#212/#213 rate-limit 후속, EGW00201 backoff 활용)
- [ ] KIS broker 동적 universe order — weights → orders 변환 (단주 반올림, 잔여 현금 처리, 주문가능 종목 사전 필터)
- [ ] Binance broker 동적 universe quote/order — top-10 (현물 spot 우선, futures 후속)
- [ ] paper broker (`src/execution/paper_broker.py`) 동적 instrument 시뮬레이션 — 호가창 깊이 보수 추정

### 3. Live loop / 데몬 / Docker

- [ ] `src/live/loop.py` universe-scan path — 매 주 금요일 마감 트리거 + universe builder 호출 + weights 산출 + orders 발주
- [ ] qta.exe / production.yaml 갱신 — 카탈로그 7종 (5 신규 + 2 검증완료) 등록, legacy 단일종목 entry 정리
- [ ] Docker compose env — `UNIVERSE_SIZE_KOSPI=200`, `UNIVERSE_SIZE_KOSDAQ=150`, `TOP_N_KR=20`, `UNIVERSE_SIZE_CRYPTO=30`, `TOP_N_CRYPTO=10`, `REBAL_DAY=Friday`
- [ ] KIS 1m cron (#152) — universe top-N 분봉 수집 path 추가 (또는 EOD 일봉 별도 cron, 분봉 부담 시)
- [ ] R4/R6 Task Scheduler (shadow_run_swing.py) — 통합 후 단일 path 로 일원화 또는 명시적 retain (현재 paper 운영 중이므로 중단 없이 통합)

### 4. 단일 카탈로그 통합 + 대시보드 토글

- [ ] R4/R6 + momo_kis_v1 + universe-scan 변환본 7종을 **단일 production.yaml 카탈로그** 에 통합 — 현재 R4/R6 가 shadow_run_swing.py 별도 path 인 것을 orchestrator path 로 통일
- [ ] 대시보드 `/strategies` 카드 — 통합 카탈로그 전체 표시 (R4/R6 포함). frontmatter 검증 수치 채움.
- [ ] 대시보드 토글 ON/OFF (#180 확장) — 모든 전략에 작동, OFF 시 orchestrator runtime 에서 paper 발주 안 함
- [ ] universe-scan 카드는 보유 종목 리스트 + 가중치 표시
- [ ] 토글 상태 영속화 — `.omc/state/strategy-toggle.json` 등 파일 기반, 재시작 시 복원

### 5. 전략별 상세 페이지 (신규)

- [ ] `/strategies/<id>` 라우트 — 전략별 상세 페이지
- [ ] 거래 내역 테이블 — entry/exit timestamp, symbol, price, qty, PnL
- [ ] 일PnL 시계열 + 누적 equity curve 차트
- [ ] 현 보유 포지션 리스트 (universe-scan 은 다종목)
- [ ] 데이터 소스: position_provider (#192) + pnl_aggregator (#194/#210) 재사용 — 새 데이터 수집 없이 기존 레이어 활용

### 6. Telegram 알림

- [ ] 주간 rebal report 템플릿 — "주간 리밸 (cs_tsmom_kr_daily): 매수 X종 / 매도 Y종 / 유지 Z종 / 일PnL ..." 1건 알림
- [ ] 종목별 entry/exit 알림 → 주간 합산 알림으로 변경 (단, 즉시 청산·crash guard 발동 시 별도 alert)
- [ ] `/portfolio` 명령 — 봇이 현재 보유 top-N + 가중치 + 일PnL 응답
- [ ] `/strategies` 명령 — 봇이 카탈로그 + 토글 상태 응답

### 7. 일일 점검 스크립트 갱신 (놓치기 쉬운 부분)

- [ ] `daily_check.ps1` 갱신 — universe-scan 변환본의 새 strategy ID + WAL 경로 + Task Scheduler 이름 반영. 점검 결과에 universe 단위 (basket) + 종목별 둘 다 출력.
- [ ] `daily_check_kis.ps1` 갱신 — Daemon log grep 패턴 (warmup_loaded 의 universe 모드 keyword), WAL/리포트 경로 다종목 대응, 주간 rebal 카운트 항목 추가
- [ ] `scripts/shadow_report.py` 출력 포맷 — universe-scan 의 종목별 PnL + basket 단위 PnL 둘 다 표현
- [ ] 신규 데몬 추가 시 Task Scheduler XML / Docker compose service 정의 포함

### 8. Legacy 정리

- [ ] 단일종목 strategy 파일 (momo_kis_v1, momo_btc_v2, momo_vol_filtered, swing_kr_daily 4종) — `src/backtest/strategies/_legacy/` 로 이동 또는 deprecate 주석 + 이슈 링크
- [ ] production.yaml 의 단일종목 entry 제거 (legacy 디렉토리는 import 경로만 잔존)
- [ ] 단일종목 단위 테스트 (`tests/test_momo_btc_v2_*.py` 등) retain (회귀 보호) — 단 frontmatter `status: deprecated` 표기

### 9. 문서

- [x] `docs/specs/universe-scan-strategy-pattern.md` (이미 작성, `feat/swing-strategy-portfolio`)
- [x] `docs/specs/strategies/cs-tsmom-kr-daily.md` (이미 작성)
- [x] `docs/specs/strategies/cs-tsmom-crypto-daily.md` (이미 작성)
- [ ] `docs/specs/strategies/cs-rsi-div-kr.md` (신규)
- [ ] `docs/specs/strategies/cs-bb-macd-kr.md` (신규)
- [ ] `docs/specs/strategies/cs-adx-ma-kr.md` (신규)
- [ ] `docs/specs/strategies/cs-rsi-div-crypto.md` (신규)
- [ ] `docs/specs/strategies/cs-macd-vol-crypto.md` (신규)
- [ ] `docs/runbooks/universe-scan-runbook.md` — 운영 런북 (rebal 실패·universe stale·rate-limit 대응)
- [ ] `docs/onboarding/getting-started.md` universe-scan 패턴 onboarding 섹션
- [ ] 본 이슈 작업 폴더 `docs/work/active/<NNN>-universe-scan-pivot/` 에 `00_issue.md` / `01_plan.md` / `02_implementation.md`

## 구현 플랜

### Phase 1 — 코어 모듈 (1-2일)
1. `src/universe/{krx_top,binance_top,_filters}.py` 신규 작성
2. AsyncStrategy wrap 7종 (cs_tsmom_kr_daily, cs_tsmom_crypto_daily 먼저 → 단순 변환 5종)
3. `register_strategy_returns` 호출 + 각 단위 테스트 1건

### Phase 2 — Broker / Live (1-2일)
4. KIS broker 동적 universe quote/order 확장 + rate-limit 검증
5. Binance broker 동일
6. paper broker 동적 instrument 시뮬레이션
7. live_run.py / qta.exe orchestrator universe-scan path

### Phase 3 — 카탈로그 / 대시보드 (1-2일)
8. production.yaml 통합 (R4/R6 카탈로그 path 통일 포함)
9. 대시보드 `/strategies` 카드 카탈로그 전체 + 토글 ON/OFF 영속화
10. `/strategies/<id>` 상세 페이지 + 거래내역·equity curve

### Phase 4 — Telegram / Docker / daily_check (1일)
11. Telegram rebal report 템플릿 + `/portfolio` `/strategies` 봇 명령
12. Docker compose env vars 추가
13. `daily_check.ps1` / `daily_check_kis.ps1` / `shadow_report.py` 갱신

### Phase 5 — Legacy 정리 + 문서 (반일)
14. 단일종목 strategy 파일 `_legacy/` 이동 + spec frontmatter `status: deprecated`
15. 신규 spec 문서 5종 작성
16. universe-scan-runbook.md 작성
17. work 폴더 산출물 (00_issue/01_plan/02_implementation)

### Phase 6 — 통합 검증 (1일)
18. 1주일 paper 시뮬레이션 (모든 전략 통합 카탈로그 + 대시보드 토글)
19. R4/R6 paper 결과 (#143/#199 30일) 와 비교
20. PR 리뷰 / 머지

## 의존성 / 차단

- **시작 시점**: 다른 동시 작업 PR (#214 등) main 머지 완료 후. 픽업 시 첫 단계 = `feat/swing-strategy-portfolio` rebase onto master + invariants 재검증.
- **연관 이슈**:
  - #133 (Phase 2 KIS 모의 운영) — momo_kis_v1 운영 중, 본 이슈 머지 시 cs_rsi_div_kr 로 마이그레이션
  - #143 (R4 4h paper) / #199 (R6 1h paper) — paper 결과 종료 후 카탈로그 통합 + 평가
  - #178 (대시보드 카드) / #180 (전략 토글) / #194 (PnL aggregator) / #210 (KST reset) / #192 (position_provider) — 본 이슈에서 활용
  - #212 / #213 (KIS rate-limit) — 본 이슈 broker 확장에서 활용
- **차단 외부 데이터**:
  - PIT 시총 스냅샷은 KRX 정식 데이터 또는 자체 수집 필요 — 본 이슈 1차는 current Marcap 기반 (survivorship bias 인정), PIT 는 별도 follow-up 가능

## 리스크 / 위험

- **거래 빈도 약 20배 증가** — 주당 KRX 5~6 + crypto 3 = 8~9 round-trip. KIS API rate-limit (#212) 매주 마감 직후 spike, backoff 정책 유지 필요. Telegram noise 제어 (주간 합산 알림 의무).
- **R4/R6 통합 시 paper 운영 중단 위험** — 30일 결과 수집 중. 통합은 (a) R4/R6 path 유지하면서 카탈로그 view 만 통합, (b) 결과 종료 후 마이그레이션 둘 중 선택. 권장: (a) 후 (b) 단계적.
- **legacy 단일종목 strategy 가 production.yaml 에서 즉시 제거되면** qta.exe 가 등록되지 않은 strategy 를 reference 해 오류 가능 — config_loader graceful skip 검증 필수.
- **survivorship + listing bias** — 백테스트 수치 (Sharpe 0.871/1.328) 가 실거래보다 낙관. 보수적 기대치 설정.

## 관련 노트

- `docs/specs/universe-scan-strategy-pattern.md` (이미 작성)
- `docs/specs/strategies/cs-tsmom-kr-daily.md` (이미 작성)
- `docs/specs/strategies/cs-tsmom-crypto-daily.md` (이미 작성)
- `docs/work/active/swing-strategy-portfolio/00_plan.md` (이미 작성, 본 이슈의 사전 검증 결과)

## 흡수되는 기존 open issues

본 이슈는 다음 open issue 들의 스코프를 포함한다 — 본 이슈 머지 후 중복으로 close 또는 본 이슈에 link 후 정리:

- #179 — feat: 전략 상세 페이지 (마크다운 렌더링 + 신호·리스크룰 인라인)
- #191 — feat: 전략 상세 페이지 (/strategies/{id}) — 종목·실시간 가격·summary·토글
- #193 — feat: 전략별 체결 이력 필터 — REST + 상세 페이지 라이브 타임라인

→ 본 이슈의 §5 "전략별 상세 페이지" 가 이 셋의 합집합. 본 이슈 시작 시점에 위 3개를 "blocked by 본 이슈" 로 코멘트.

## 개발 체크리스트

- [ ] 테스트 코드 포함 (전략 7종 단위 테스트 + 통합 시나리오 1건)
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음 (`scripts/check_invariants.py --strict` 통과)

