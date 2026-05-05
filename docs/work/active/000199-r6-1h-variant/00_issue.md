---
type: work-done
id: 00_issue
name: "Issue #199 — R6 (R4 의 1시간봉 변형)"
status: active
---

# feat: R6 (R4 의 1시간봉 변형) — backtest + paper 30일 병렬 운영 (#143 후속)

## 사용자 관점 목표
#143 R4 (4h 봉) 운영 표본 부족 (~7건/30일) 해결. R4 와 동일 로직을 1h 봉에 맞게 재튜닝한 R6 추가 → 30일 운영 시 ~9건 기대 (예상 28보다 적음 — regime threshold 가 빡빡) → R4 와 동등 비교 가능.

## 배경
R4 (4h, Sharpe 1.218) 30일 paper 시 거래 약 7건 → Sharpe SE 약 ±1.5 → "임계 0.609 통과/미달" 판정 노이즈로 좌우. 1h 봉 + 4배 봉 개수 + 같은 시간 horizon 유지하는 R6 추가하면 거래 빈도 증가 + 다른 봉 검증 가능.

## 완료 기준
- [x] `src/backtest/swing/regime_switching.py` 에 `route_r6` 추가
- [x] `VARIANT_REGISTRY` 에 R6 entry 추가
- [x] `src/backtest/swing/paper_adapter.py` `StrategyId` 에 `r6-switch` 추가
- [x] `scripts/shadow_run_swing.py` `--strategy choices` 에 `r6-switch` + `--interval {1h,4h}` flag
- [x] R6 1h 5년 backtest Sharpe 1.201 ≥ 0.6 임계 통과 ✅
- [x] 단위 테스트 (test_paper_adapter.py R6 케이스 3건) 통과
- [ ] 두 번째 Task Scheduler XML 등록 (사용자 PC, 머지 후)
- [ ] daily_check.ps1 R6 추가 (사용자 PC, 머지 후)
- [ ] 30일 운영 후 R4 vs R6 비교 결과

## 작업 내역

### 5년 OOS 백테스트 결과 (1h BTCUSDT, lake/ohlcv/freq=1m → 1h resample)

```
R0 (1h, S2c always)             Sharpe -0.570  MDD -71.6%  trades 2486   ← 망함
R4 (1h, default 4h params)      Sharpe -0.302  MDD -52.0%  trades 1990   ← 재튜닝 안하면 망함
R6 (1h, retuned)                Sharpe +1.201  MDD -17.4%  trades 554    ← ✅ 진행 결정
R2/R3/R5 (HMM)                  ModuleNotFoundError (hmmlearn Python 3.14 빌드 실패)
```

**핵심 발견**:
- R6 Sharpe 1.201 ≈ R4 (4h) 1.218 — 두 봉 모두 같은 logic 작동
- R6 MDD -17.4% > R4 -9.7% — 1h 변동성 ↑ (1.8배)
- R6 trades 554 ≈ R4 458 — 4배 아니라 1.2배만 (regime threshold 가 강세장 진입만 허용)
- 30일 환산: R4 ~7건 → R6 ~9건. 표본 효과는 약간만 ↑

### R6 파라미터 (1h-tuned, route_r6 default)

| Param | R4 (4h) | **R6 (1h)** | Time horizon |
|---|---|---|---|
| return_lookback | 180 | **720** | 30 days |
| entry_lookback | 20 | **80** | 3.3 days |
| exit_lookback | 10 | **40** | 1.7 days |
| vol_lookback | 60 | **240** | 10 days |
| vol_target | 0.15 | 0.15 | (unchanged) |

### 코드 변경

- `src/backtest/swing/regime_switching.py`: `route_r6` 추가 + `VARIANT_REGISTRY["R6"]` entry. R4 와 동일 로직 (ThresholdRegime → S2c/S4 라우팅) 이지만 1h-tuned default s2c_params 내장.
- `src/backtest/swing/paper_adapter.py`: `StrategyId` Literal 에 `r6-switch` 추가. `_compute_signal` 에 r6-switch 분기 (route_r6 호출).
- `scripts/shadow_run_swing.py`:
  - `--strategy` choices 에 `r6-switch` 추가
  - `--interval {1h,4h}` 플래그 신설 (None → 자동: r6-switch=1h, 그 외=4h)
  - r6-switch 선택 시 entry/exit/vol/return lookback 자동으로 4배 (사용자가 명시 override 안 했을 때만)
  - `_fetch_candles(interval=...)` Binance API interval 파라미터 통과
- `tests/test_paper_adapter.py`: R6 시나리오 3건 (entry / no-signal / round-trip) 추가 → 16/16 green

### 운영 계획 (머지 후 사용자 행동)

새 Task Scheduler 등록 — `QuantumTrader\ShadowSwing143-r6` (기존 `ShadowSwing143` 은 R4 4h 그대로 유지):

```xml
<!-- 1h 간격, 720시간 (30일), StartBoundary 다음 정시 -->
<Repetition><Interval>PT1H</Interval><Duration>P30D</Duration></Repetition>
```

명령:
```powershell
python scripts\shadow_run_swing.py --strategy r6-switch --symbol BTCUSDT --max-bars 1
```

WAL 디렉토리: `logs/shadow/phase1-r6-switch-BTCUSDT/wal.jsonl` (R4 와 분리)

### 30일 후 비교 분석
- R4 paper Sharpe vs R6 paper Sharpe — 어느 봉이 backtest 와 더 가까운지
- 거래수 / 수수료 영향 비교
- MDD 비교 (R6 backtest MDD -17.4% 가 paper 에서도 그대로?)
- Phase 3 (#107) 진입 시 어느 봉으로 갈지 결정 근거

## 의존성
- 선행: #143 (R4 운영 시작) ✅, #173 (regime_switching 구현) ✅, #195 (R4 paper 통합) ✅
- 후행: #138 (백서 v0.2), #107 (Phase 3 실자금)

## 범위 밖
- HMM 변형 (R2/R3/R5) 의 1h 적용 — 본 이슈는 R6 (R4 의 1h) 만
- 5m / 15m 봉 — 수수료 폭증 우려 (R0 5m 시뮬레이션 이미 망함 추정)
