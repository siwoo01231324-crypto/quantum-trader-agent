# [#218] universe-scan 패턴 전면 전환 — 작업 계획 (초안)

> ⚠️ 본 문서는 `/si` 가 자동 생성한 **AC 체크리스트 초안**. 구현 시작 전 `/plan` 또는 ralplan 으로 구체화 필요.

## 사전 검증 (이미 완료)

- [x] universe-scan 패턴 spec 작성 (`docs/specs/universe-scan-strategy-pattern.md`)
- [x] cs_tsmom_kr_daily 5y bench (Sharpe 0.871 / Ann 23.0% / MDD -43%)
- [x] cs_tsmom_crypto_daily 5y bench (Sharpe 1.328 / Ann 90.85% / MDD -52%)
- [x] 두 전략 spec 문서 작성 (frontmatter 검증 통과)
- [x] AGENTS.md / CLAUDE.md / src/backtest/strategies/.ai.md 패턴 도입 반영
- [x] check_invariants --strict 통과 (188 노트)
- [x] 이전 worktree (`feat/swing-strategy-portfolio`) 의 산출물 본 worktree 로 이전 완료

## Phase 1 — 코어 모듈 (우선순위 1)

### universe builder
- [ ] `src/universe/krx_top.py` — `top_n_by_marcap(market, n, as_of)`
- [ ] `src/universe/binance_top.py` — `top_n_by_volume(n, exclude_filters)`
- [ ] `src/universe/_filters.py` — 공통 유동성·가격·stable/wrapped 제외 필터
- [ ] 단위 테스트 (`tests/universe/test_krx_top.py`, `test_binance_top.py`)

### Strategy 모듈 (5종 신규 + 2종 wrap)
- [ ] `src/backtest/strategies/cs_rsi_div_kr.py` — momo_kis_v1 + swing_kr_daily.momo_kis_daily 의 universe 변환
- [ ] `src/backtest/strategies/cs_bb_macd_kr.py` — swing_kr_daily.swing_bb_macd 의 universe 변환
- [ ] `src/backtest/strategies/cs_adx_ma_kr.py` — swing_kr_daily.swing_adx_ma 의 universe 변환
- [ ] `src/backtest/strategies/cs_tsmom_kr_daily.py` — bench → AsyncStrategy wrap
- [ ] `src/backtest/strategies/cs_rsi_div_crypto.py` — momo_btc_v2 의 universe 변환
- [ ] `src/backtest/strategies/cs_macd_vol_crypto.py` — momo_vol_filtered 의 universe 변환
- [ ] `src/backtest/strategies/cs_tsmom_crypto_daily.py` — bench → AsyncStrategy wrap
- [ ] orchestrator 등록 + `register_strategy_returns(...)` × 7
- [ ] 단위 테스트 1건 / 전략

## Phase 2 — Broker / Live (우선순위 2)

- [ ] KIS broker 동적 universe quote/order (#212/#213 backoff 활용)
- [ ] Binance broker 동적 universe quote/order
- [ ] paper broker 동적 instrument 시뮬레이션
- [ ] live_run.py / qta.exe orchestrator universe-scan path (매주 금요일 마감 트리거)
- [ ] weights → orders 변환 (단주 반올림, 잔여 현금 처리)

## Phase 3 — 카탈로그 / 대시보드

- [ ] production.yaml 통합 (R4/R6 + universe-scan 7종 단일 카탈로그)
- [ ] R4/R6 shadow_run_swing.py path → orchestrator path 통일
- [ ] 대시보드 `/strategies` 카드 — 통합 카탈로그 + 보유 종목 리스트
- [ ] 토글 ON/OFF 영속화 (`.omc/state/strategy-toggle.json` 등)
- [ ] `/strategies/<id>` 상세 페이지 — 거래내역·equity curve·보유 포지션
- [ ] (#179, #191, #193 흡수)

## Phase 4 — Telegram / Docker / daily_check

- [ ] Telegram 주간 rebal report 템플릿 (LIVE 봇 단일 채널, fallback chain `TELEGRAM_LIVE_* > TELEGRAM_QTA_* > legacy` — #214 와 정합)
- [ ] /portfolio /strategies 봇 명령
- [ ] Docker compose env vars 추가
- [ ] daily_check.ps1 갱신 (universe-scan path)
- [ ] daily_check_kis.ps1 갱신 (universe log keyword)
- [ ] shadow_report.py 출력 포맷 (basket + 종목별)

## Phase 5 — Legacy 정리 + 문서

- [ ] 단일종목 strategy `_legacy/` 이동 + frontmatter `status: deprecated`
- [ ] production.yaml legacy entry 제거 (config_loader graceful skip 검증)
- [ ] spec 문서 5종 (cs-rsi-div-kr, cs-bb-macd-kr, cs-adx-ma-kr, cs-rsi-div-crypto, cs-macd-vol-crypto)
- [ ] `docs/runbooks/universe-scan-runbook.md`
- [ ] `docs/onboarding/getting-started.md` universe-scan 섹션

## Phase 6 — 통합 검증

- [ ] 1주일 paper 시뮬레이션 (전체 카탈로그 + 토글 작동 확인)
- [ ] R4/R6 paper 결과 (#143/#199) 비교
- [ ] check_invariants --strict 통과
- [ ] 단위 테스트 + 회귀 테스트 통과
- [ ] PR 생성·리뷰·머지

## 의존성

- ✅ master 최신화 완료 (db8c5fd, #214 텔레그램 라우팅 머지됨)
- 📌 #143 R4 / #199 R6 paper 30일 종료 결과는 Phase 6 단계에서 비교
- 📌 #133 (Phase 2 KIS 모의) 운영 중 — Phase 4 daily_check 갱신 시 영향 고려

## 다음 단계

본 초안 → `/plan` 또는 `/oh-my-claudecode:ralplan` 으로 구현 계획 구체화 → Phase 1 진입.
