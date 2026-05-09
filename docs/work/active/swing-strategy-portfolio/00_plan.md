# Swing Strategy Portfolio + Universe-Scan Pivot — 작업 계획

> 작성: 2026-05-06 — 작업자: siwoo
> 태그: swing · universe-scan · multi-strategy · pattern-pivot

## 작업 개요

여러 전략을 동시에 운용하는 multi-strategy portfolio 를 만들기 위해 KRX 일봉 swing 전략을 추가 검증한 작업. 도중에 **universe-scan 패턴이 신규 default** 가 되어야 한다는 결정이 나옴 (2026-05-06).

## 사용자 요구

1. (1차) "주식왕 단테 등 KR 트레이더 + 학술 모멘텀 논문 참고해서 KRX 일봉 swing 전략 새로 만들고 005930+035720+000660 으로 5y bench"
2. (2차) "지금 종목별 백테스트 말고, 전체 KRX 종목 감시 → 조건 부합 매수 → 백테스트 가능한가?"
3. (3차, **본 결정**) "이 universe-scan 방식을 기획서·specs 에 전면 반영, 앞으로 신규 이슈도 이 방향으로 진행"
4. (4차) "코인 전략도 BTC 만이 아닌 상위 20 종목 cross-sectional 로 동일하게 백테스트"

## 1차 결과 — 단일종목 swing 4종 (도태)

`scripts/bench_swing_kr_daily.py`, 005930+035720+000660, 2020-2025.

| Strategy | Sharpe (avg) | MDD | Ann | Trades | 결론 |
|----------|------------:|-----:|-----:|-------:|------|
| momo_kis_daily (RSI div) | 0.088 | -16.5% | -0.0% | 11 | 신호 부족 |
| swing_bb_macd | -0.101 | -5.8% | -0.5% | 1 | 조건 너무 strict |
| swing_adx_ma | 0.000 | 0% | 0% | **0** | 5y 0 trades |
| swing_tsmom_12_1 | 0.339 | -41.6% | 8.4% | 32 | 종목별 편차 큼 |

→ 단일종목 고정 패턴은 KRX 일봉 swing 에 부적합. universe-scan 으로 전환 결정.

## 2차 결과 — Cross-Sectional TSMOM (universe-scan)

`scripts/bench_cs_tsmom_kr.py`, KOSPI 200 + KOSDAQ 150 = 347 종목, 2020-2025, top-20 동일가중 주간 리밸.

| Variant | Sharpe | MDD | Ann | 결론 |
|---------|-------:|----:|----:|------|
| **v1 baseline** (top-20, no MA filter, dd-15%) | **0.871** | -42.99% | **22.99%** | **winner** |
| v2 +MA200 regime, dd-10% | 0.711 | -43.80% | 14.80% | regime filter 무용 |
| v3 top-10 concentrated | 0.672 | -54.45% | 18.07% | concentration 손해 |
| KOSPI benchmark | 0.656 | -35.71% | 11.98% | (기준선) |

**v1 채택**. KOSPI 대비 Ann +11%p, Sharpe +0.21. MDD 가 KOSPI 보다 깊은 점은 모멘텀의 알려진 약점 — multi-strategy portfolio 차원에서 분산.

## 3차 결정 — Universe-Scan 패턴 = 신규 default

업데이트된 문서 (이번 작업):
- `docs/specs/universe-scan-strategy-pattern.md` — **NEW**, 패턴 정의 + 필수 컴포넌트 + PR 체크리스트 + 자산군별 적용표
- `AGENTS.md` — "전략 패턴" 섹션 추가
- `CLAUDE.md` — "새 전략 추가 시 필수" 패턴 선택 게이트 추가
- `src/backtest/strategies/.ai.md` — 패턴 카탈로그 + 기존 전략 패턴 분류
- `docs/specs/strategies/.ai.md` — frontmatter `pattern:*` 라벨 규칙 추가
- `docs/specs/strategies/cs-tsmom-kr-daily.md` — **NEW**, 본 전략 스펙

후속 영향:
- 신규 KRX·crypto 전략 default = universe-scan
- 단일종목 추가는 spec "운영 규칙" 에 사유 명시 (예외 처리)
- 기존 운영 단일종목 전략 (`momo_kis_v1` 005930) 은 그대로 유지

## 4차 결과 — Crypto Cross-Sectional TSMOM (universe-scan)

`scripts/bench_cs_tsmom_crypto.py`, Binance USDT spot top-30 (stablecoin/wrapped/leveraged 제외, 29 fetched), 2020-2025, top-10 동일가중 주간 리밸.

| Metric | Strategy | BTC |
|--------|---------:|----:|
| Sharpe | **1.328** | 0.989 |
| MDD | -52.42% | -76.63% |
| Ann.Return | **90.85%** | 51.61% |
| Calmar | 1.733 | — |
| Final Equity (5y) | 48.5× | ~7.7× |
| Avg Holdings | 8.1 | — |
| Annual Turnover | 20.3× | — |
| Exposure | 72.4% | 100% |

→ BTC 단일 보유 대비 Sharpe +0.34, MDD 24%p 개선, Ann +39%p. **survivorship + listing bias 강함** (신생 코인이 listing 후 급등하여 자동 합류) — 실거래 기대치는 보수적으로 -10~20%p 차감.

신규 spec: [[cs-tsmom-crypto-daily]]

## 통합 비교 — universe-scan 두 자산군

| 자산 | Sharpe | MDD | Ann | Turnover | Holdings |
|------|-------:|----:|----:|--------:|--------:|
| KRX cs_tsmom_kr_daily | 0.871 | -43% | 23% | 14.5× | 20 |
| Crypto cs_tsmom | 1.328 | -52% | 91% | 20.3× | 8.1 |
| KOSPI bench | 0.656 | -36% | 12% | — | — |
| BTC bench | 0.989 | -77% | 52% | — | — |

두 전략 동시 운용 시 (correlation 추정 ρ ≈ 0.2~0.3) ENB ~1.7 → 단일 vs 50/50 portfolio 비교 후속 검증.

## 산출물

- 코드: `src/backtest/strategies/swing_kr_daily.py` (단일종목 4종, archive 후보), `scripts/bench_swing_kr_daily.py`, `scripts/bench_cs_tsmom_kr.py`
- 캐시: `data/cache/krx_daily/*.parquet` (.gitignore 필요)
- 백테스트 출력: `cs_tsmom_v{1,2,3}_*.{json,md}`
- 신규 spec: `docs/specs/universe-scan-strategy-pattern.md`, `docs/specs/strategies/cs-tsmom-kr-daily.md`

## 현재 상태 (2026-05-06)

**브랜치**: `feat/swing-strategy-portfolio` (worktree). master 미커밋·미머지 — 다른 에이전트 작업이 main 에 머지된 후 한 번에 리팩토링 예정 (사용자 결정).

**완료**:
- [x] universe-scan 패턴 spec + 전 레포 doc 반영 (AGENTS.md, CLAUDE.md, 두 .ai.md)
- [x] `cs_tsmom_kr_daily` 5y bench (Sharpe 0.871 / Ann 23.0%)
- [x] `cs_tsmom_crypto_daily` 5y bench (Sharpe 1.328 / Ann 90.85%)
- [x] 두 전략 spec 문서 생성 (frontmatter 포함, invariants 통과)
- [x] `check_invariants --strict` PASS (184 노트 검증)

**보류 (main 머지 후 재개)**:
- [ ] `src/universe/krx_top.py` + `binance_top.py` 모듈 (universe builder 정식 위치)
- [ ] `cs_tsmom_*_daily.py` AsyncStrategy wrapping → orchestrator 등록
- [ ] 단위 테스트 1건 per 전략 (CLAUDE.md "새 전략 추가 시 필수")
- [ ] PIT universe 로 survivorship + listing bias 제거 (정밀 검증)
- [ ] KIS broker 동적 universe quote/order 확장 (#212/#213 후속)
- [ ] Telegram "rebal report" 템플릿
- [ ] live deploy: weights → orders 변환 (단주 반올림, 잔여 현금)

**왜 대기**: 동시 진행 중인 다른 PR 들이 (대시보드, 컨테이너 리빌드) 자주 main 에 들어오는 중 → 본 브랜치 머지 시점 충돌·누락 위험. 다른 작업 정리 후 일괄 리팩토링이 안전.

**픽업 시 첫 단계**: 본 브랜치 `feat/swing-strategy-portfolio` rebase onto master → invariants 재검증 → 보류 항목 우선순위 (universe builder 모듈부터) 부터 이슈로 분리.

## 관련 노트

- [[universe-scan-strategy-pattern]]
- [[cs-tsmom-kr-daily]]
- [[breakout-donchian]]
- [[42-cross-sectional-momentum-crypto]]
- [[44-time-series-momentum-crypto]]
