---
type: work-done
id: 01_plan
name: "Issue #143 — 구현 계획"
status: active
---

# 구현 계획 — #143 Phase 1 Shadow Paper 데몬 실가동

> 작성: 2026-05-05

## 완료 기준

- [ ] `scripts/shadow_run.py --symbols BTCUSDT,005930 --duration 24h` 자동 재기동 운영 시작 → **갱신: shadow_run_swing.py + R4 + BTCUSDT 단일**
- [ ] WAL 파일 (`logs/shadow/{run_id}/wal.jsonl`) 일자별 누적 30 거래일
- [ ] 자동 재시작 시스템 (윈도우 작업 스케줄러 또는 systemd-equivalent)
- [ ] `scripts/shadow_report.py --verify-exit` 30일 누적 후 Phase 1 exit criteria 4 조건 검증 통과
- [ ] 비교 4조건 강제: 동일 데이터 소스 / 동일 슬리피지 / 동일 수수료 / 동일 사이징 (29-paper-to-live-protocol §7.1)
- [ ] Phase 1 가동 시작일·누적일 백서 §11-5 갱신
- [ ] 메타라벨러 ON Sharpe vs OFF Sharpe 차이 실측 (목표: 차이 ≤ 0.3) → **갱신: R4 vs R0(bare S2c) Sharpe 비교**

## 사실 확인 (2026-05-05 git log + 실측)

| 항목 | 사실 |
|---|---|
| #80 PR #115 머지 | ✅ — `PaperBroker`, `WAL`, `KillSwitch`, `shadow_run.py` 존재 |
| #175 PR #189 머지 | ✅ — `paper_adapter.py`, `shadow_run_swing.py` (s2c-voltarget / s4-funding 만 등록) |
| #173 PR #187 머지 | ✅ — `regime_switching.py` route_r0~r5 전체 구현, 5년 bench BTCUSDT@4h 결과 R4 BEST |
| `logs/shadow/` 디렉토리 | ❌ 부재 — 데몬 미가동 확인 |
| `shadow_run_swing.py` choices | `["s2c-voltarget", "s4-funding"]` 만 — **R4 등록 안됨** |

## R4 본질 (#173 bench 실측)

```
R0 (bare S2c)        Sharpe 0.825  MDD -18.7%  trades 600
R4 (threshold-switch) Sharpe 1.218  MDD  -9.7%  trades 458  ← BEST
R2 (HMM-2state)      Sharpe 0.597  MDD -20.6%
R3 (HMM-3state)      Sharpe -1.84  MDD -61.6%  ← worst
R5 (ensemble)        Sharpe 0.143
```

**R4 = threshold-based** (NOT HMM): `return_180bar > 0 → S2c`, `funding < 0 → S4`. HMM 시도는 모두 실패. 단순 threshold 가 5년 OOS 실측에서 우월.

## 구현 계획

### Phase 1 — PaperAdapter R4 지원 (45분)

**파일**: `src/backtest/swing/paper_adapter.py`

```python
StrategyId = Literal["s2c-voltarget", "s4-funding", "r4-switch"]

@dataclass
class AdapterConfig:
    ...
    # R4 params
    return_lookback: int = 180
```

`_compute_signal` 분기 추가:

```python
elif self._cfg.strategy == "r4-switch":
    from src.backtest.swing.regime_switching import route_r4
    sig_series, size_series = route_r4(
        df,
        return_lookback=self._cfg.return_lookback,
        s2c_params={"entry_lookback": ..., ...},
    )
    signal = int(sig_series.iloc[-1])
    pos_size = float(size_series.iloc[-1]) if size_series is not None else 1.0
```

**엣지 케이스**:
- df 에 `_funding_rate` 컬럼이 있어야 R4 정상동작 (없으면 ThresholdRegime 이 funding=None 받아 처리)
- df 길이 < return_lookback 일 때 → route_r4 가 적절히 처리 (확인 필요)

### Phase 2 — shadow_run_swing.py CLI 확장 (15분)

**파일**: `scripts/shadow_run_swing.py`

```python
parser.add_argument(
    "--strategy",
    required=True,
    choices=["s2c-voltarget", "s4-funding", "r4-switch"],
    ...
)
parser.add_argument("--return-lookback", type=int, default=180)
```

`AdapterConfig(...)` 인자에 `return_lookback=args.return_lookback` 추가.

### Phase 3 — 단위 테스트 (30분)

**파일**: `tests/test_paper_adapter.py`

신규 케이스 3종:
1. `test_r4_switch_signal_computation` — synthetic df (200 bars, with funding) → r4 strategy → 신호 0/1 확인
2. `test_r4_switch_round_trip` — entry → exit 한 사이클 OrderAck FILLED 검증
3. `test_r4_unknown_strategy_raises` — choices 위반 시 ValueError

### Phase 4 — Smoke 테스트 (10분)

```powershell
$env:PYTHONUTF8=1
python scripts\shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT --max-bars 5 --log-level INFO
```

기대: `logs/shadow/{run_id}/wal.jsonl` 생성, 무결성 검증 통과.

### Phase 5 — 이슈 #143 AC 갱신 (5분)

`gh issue edit 143 --body` 로 AC 갱신:
- `shadow_run.py` → `shadow_run_swing.py`
- `--symbols BTCUSDT,005930` → `--symbol BTCUSDT --strategy r4-switch` (KRX 005930 은 별도 이슈로 분리)
- 메타라벨러 ON/OFF → R4 vs R0 비교

### Phase 6 — 02_implementation.md (30분)

운영 매뉴얼:
- Task Scheduler XML (4h 주기, 30일 duration, R4 strategy)
- 일일 점검 체크리스트 (WAL 무결성 / 신호 빈도 / 데몬 alive / daily report)
- 30일 후 채택 SOP (R4 paper Sharpe ≥ 0.6 = backtest 1.218 의 50% 보존)
- Halt trigger R1-R5 (MDD>15% R4 baseline 의 1.5x 등)

### Phase 7 — 사용자 행동 항목 (사람만 가능)

본 PR 머지 후 사용자가 해야 할 것:
1. Task Scheduler 등록 (02_impl Phase 6 의 XML 사용)
2. 매일 1회 점검 (5분)
3. 30일 후 verify-exit 실행 + 백서 §11-5 갱신

## 변경 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `src/backtest/swing/paper_adapter.py` | StrategyId literal 확장 + r4-switch 분기 + return_lookback 파라미터 |
| `scripts/shadow_run_swing.py` | choices 확장 + return_lookback CLI flag |
| `tests/test_paper_adapter.py` | R4 테스트 3종 추가 |
| `docs/work/active/000143-phase1-shadow-daemon/02_implementation.md` | 운영 매뉴얼 신규 |
| (issue body) | gh issue edit 으로 AC 갱신 |

## 의존성·주의사항

- `regime_switching.py` 의 `route_r4` 가 `_funding_rate` 컬럼을 `df.get("_funding_rate")` 로 None-safe 하게 처리하는지 재검증 (Phase 1 구현 시 확인)
- Binance public REST `klines` 응답에는 funding rate 없음 → `_fetch_candles` 가 placeholder 0.0 채우고 있음 (현재 구현). R4 정상동작을 위해 **funding rate 별도 fetch 필요할 수 있음** — `services/funding_fetcher` (#174 partial 머지) 활용 검토.
- 30일 운영은 사람만 할 수 있음 — 본 PR 의 산출물은 "데몬 가동 가능한 도구 + SOP" 까지

## 위험·롤백 계획

- R4 통합 후 회귀: 기존 `s2c-voltarget` 9 케이스 모두 그린 유지
- Smoke 실패 시: choices 만 추가하고 r4-switch 분기는 미완료 → 수동 분기로 임시 복구
- funding rate fetch 미해결 시: R4 placeholder 0.0 으로 운영 (R4 의 funding 분기는 비활성, return-only 분기만 동작) — 차선책으로 합리적
