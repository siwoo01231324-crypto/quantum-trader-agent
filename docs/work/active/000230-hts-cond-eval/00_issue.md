# chore: HTS 검색식 3종 (5분대기/단타/스윙) 채택 평가 — 1주 분봉 백테스트

## 목적
사용자가 새로 찾은 키움 HTS 조건검색식 3종(5분대기작전·단타·스윙)을 본 레포에 채택할지 결정.

## 배경
3종 모두 일간 A~F (저가주 900~10,000원 + 등락률 + 거래량 + 체결강도 90%+ + 이평정배열) 공통이고, 차별 조건은 5분대기=H(상승 정적 VI 근접율 3%), 단타=H(3분봉 10봉이내 20MA 지지), 스윙=H 없음. 평가 결과 합당하면 후속 이슈로 universe-scan 전략 신설, 부적합 시 폐기. 주문 자동화 인프라는 #227 에 의존.

## 완료 기준
- [x] 3종 검색식 evaluator 구현 — A~G 일간 + 5분대기 H(정적 VI 근접율) + 단타 H(3분봉 20MA 지지) ✓ `src/screeners/hts_cond/{common,dts,wait5m,swing,hybrid}.py` + 46 단위 테스트. E 체결강도 placeholder 처리 (KIS `tday_rltv` 분봉 시점별 재구성은 후속 이슈 — KIS API 가 당일 누적 스냅샷만 제공)
- [x] 최근 5거래일 KOSPI+KOSDAQ universe 백테스트 인프라 — 1m fetch cron (Task Scheduler) 등록, `--multi-day N` 옵션 구현, `run_hts_cond_pilot.py` + `grid_hts_cond.py` 모두 multi-day 지원. **실 5거래일 데이터 누적은 5/15~5/21 자동 진행 → 5/22 본검증**
- [~] 채택 판정 — **본검증 보류**: 1일 분봉 결과 promising (≤10:30 + DTS win 66.7%, +0.586%), 30일 daily 결과 reject (win 21.4%, -1.17%). 모델 차이 (분봉 walk-forward vs daily next-day) + 1일 표본 부족 → 5/22 정식 본검증 (`--multi-day 5`) 으로 최종 결정

## 구현 플랜
1. KIS API 클라이언트 확장 — 시간별 체결강도 + 정적 VI 발동가 fetch 메서드 추가, `scripts/fetch_kis_power_ratio.py` 신규
2. `src/screeners/hts_cond/` 신설 — `dts.py`(단타), `wait5m.py`(5분대기), `swing.py` 각 evaluator + 단위 테스트
3. universe + 1분봉 + 체결강도 5거래일 데이터 수집 (`cron_fetch_kis_daily.py --interval 1m` 활용, 누락률 <1% 검증)
4. `src/backtest/eval/hts_cond_*.py` 3개로 분봉 walk-forward 백테스트 (look-ahead bias 차단)
5. 결과 리포트 `.draft.md` 작성 → 채택 검색식은 후속 이슈로 `cs-hts-*-kr` universe-scan 전략 신설 분리, 폐기·보류는 사유와 조건 수정 후보 기록

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화



## 작업 내역

- 2026-05-14T09:07Z 작업 시작 (`/si 230`)
- 2026-05-14: `/plan` — 외부 리서치 (KIS API endpoint, 키움 검색식 H 의미) 후 01_plan.md 채움
- 2026-05-14: AC1 evaluator 구현 — `src/screeners/hts_cond/{common,dts,wait5m,swing}.py` + 46 단위 테스트 통과
- 2026-05-14: V0 pilot (8 syms, 단순 cache 필터) → 표본 부족 확인
- 2026-05-14: FDR snapshot 사전필터 도입 → V1 pilot (281 syms, 분봉 fetch 80분)
- 2026-05-14: **버그 발견·수정** — daily refresh 가 오늘 row 를 cache 에 추가 → `closes.iloc[-1]` 이 오늘로 잘못 잡힘 → ret 0% 로 평가됨. 수정 후 Kiwoom HTS 실제 결과와 73% 일치 (11/15)
- 2026-05-14: grid search (시간대 × TP-SL 13조합) → **≤10:30 시간대 게이트가 핵심**: DTS win 66.7% / avg +0.586%, SWING win 65% / +0.520%, WAIT5M win 62.5% / +0.420%
- 2026-05-14: 옵션 B (5거래일 누적 cron) — `scripts/cron_fetch_screener_universe.py` + Windows Task Scheduler `QTA-Screener-Fetch` (평일 16:30 KST). 누적 모드 검증 (read-concat-dedupe-write)
- 2026-05-14: hybrid_or vs A (3개 별도) 1일 비교 — 자본 효율 동일, hybrid 가 dashboard 단순화 이점
- 2026-05-15: **#228 (Live Universe Scanner paradigm) 머지 반영** — 우리 cs_hts_hybrid_kr 폐기 + `live_hts_hybrid.LiveHtsHybrid` 로 refactor (LiveScannerMixin 상속, paradigm 정렬). spec 도 `cs-hts-hybrid-kr.md` → `live-hts-hybrid.md` 갱신. production.yaml entry 도 live-* 옆 commented section 으로 이동 (env-gated)
- 2026-05-15: `--multi-day N` 옵션 구현 (run_hts_cond_pilot + grid_hts_cond) — 5/22 본검증 즉시 가능. 회귀: --multi-day 2 결과 baseline 동일
- 2026-05-15: **30일 daily-level proxy backtest** (`bench_hts_daily_30d.py`) — 628 종목 × 30 거래일, 182 trades, win 21.4%, avg -1.17%, total -213% → daily next-day 모델로는 reject. 분봉 모델과 차이 명확 (1일 hold vs 분 단위 hold) → 5/22 분봉 본검증으로 결정타
- 2026-05-15: Phase 1+2 통합 — 5/22 5거래일 본검증 데이터 자동 누적 중, live-hts-hybrid 는 dashboard catalog 등록 + paper status, LIVE_SCANNER_ENABLED 미설정으로 실제 매매 보류
