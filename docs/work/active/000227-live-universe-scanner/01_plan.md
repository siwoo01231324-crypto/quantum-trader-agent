---
type: work-done
id: 01_plan
name: "01_plan — Live Universe Scanner 패러다임 (#227)"
status: active
issue: 227
last_updated: 2026-05-11
---

# 01_plan — Live Universe Scanner 패러다임

## 0. 핵심 결단 (Architecture Decisions)

| # | Decision | 근거 |
|---|----------|------|
| D1 | **`AsyncStrategy` Protocol 그대로 사용** (`on_bar(ctx)`). 별도 `LiveScanStrategy` Protocol 신설하지 않음 | `momo_kis_v1` 이 이미 `snap["symbol"]` 게이트 패턴으로 per-symbol 동작. 새 Protocol = orchestrator·executor·dashboard 전 layer 의 dual-path. tick driver 가 **각 symbol 마다 ctx 만들어 dispatch** 하면 충분. |
| D2 | **`loop.py` consumer 가 multi-symbol fan-out**: 매 tick 마다 해당 symbol 의 snapshot 만 빌드 → orchestrator.run_bar(ts, snapshot) 호출은 그대로. orchestrator 가 등록된 Live Scanner 전략에 dispatch. | universe 380종 동시 빌드 = 메모리·CPU 부담. 현재 tick-by-tick 모델 유지가 결정성 +. |
| D3 | **Position-level stop/TP 는 orchestrator 가 아니라 별도 `LivePositionRiskManager`** (`src/portfolio/`) 에 둠. `StrategyPositionStore` (#192) consume → 매 tick stop/TP 체크 → 청산 OrderIntent 추가 emit. | 청산 신호는 strategy 책임이 아님 (strategy 는 진입 시 stop_loss_pct / take_profit_pct 만 spec frontmatter 로 선언). 책임 분리 = 테스트성 ↑. |
| D4 | **KIS WS subscribe 우선** (REST polling 350종/분 = rate-limit 즉사). 기존 `src/brokers/kis/async_ws.py` 활용. | #213 (KIS rate-limit) 정합 + 무료 무제한. fallback 으로 stagger REST (Phase 3 옵션). |
| D5 | **5y backtest 검증 = 진입 임계값 모듈 단위로 cs_* 패턴과 공유**. Live Scanner 의 진입 룰 = 기존 `_cs_helpers.py` per-symbol RSI/MACD/BB 헬퍼 재사용 + threshold gate 만 추가. | 코드 중복 회피, signal 일관성. |
| D6 | **env-gated activation**: `LIVE_SCANNER_ENABLED=1` (default OFF) + `production.yaml` 의 strategy entry 별 `live_scanner: true` flag. | Phase 별 머지 후에도 운영 영향 zero. |
| D7 | **이슈 #227 = single-PR 불가능**. 7 phase 를 **7 sub-PR** 로 슬라이스. 각 PR 머지 후 다음 phase 시작. | 8~13일 단일 PR 은 review 불가 + 회귀 risk. 본 plan 의 "Slice 단위" 컬럼 참조. |

## 1. 작업 분량 / 스라이스 계획

| Slice | 산출물 | 일수 | 의존성 |
|-------|--------|------|--------|
| S1 (Phase 1+spike) | `LiveScanStrategy` 마커 + 1 신호 (`live_rsi_oversold_volume_spike`) + per-symbol fan-out POC + smoke test | 1.5d | none |
| S2 (Phase 2) | `LivePositionRiskManager` + spec frontmatter 확장 + 단위 테스트 | 1d | S1 |
| S3 (Phase 4 partial) | `loop.py` multi-symbol fan-out + env-gated wiring | 1d | S1, S2 |
| S4 (Phase 1 잔여) | 4 추가 신호 (`live_macd_bullish_cross_breakout` / `live_bb_lower_bounce` / `live_breakout_with_atr_stop` / `live_oversold_with_divergence`) + 단위 테스트 | 2d | S1 |
| S5 (Phase 3) | KIS WS multi-subscribe + Binance WS 검증 + paper broker 380 instruments smoke | 2d | S2 |
| S6 (Phase 5) | 5y backtest harness + 10 백테스트 실행 + frontmatter sharpe_bt/mdd_bt 갱신 | 2.5d | S4 |
| S7 (Phase 6+7) | 대시보드 카드 + Telegram 진입/청산 알림 + universe-scan 공존 정책 (`production.yaml` 자본 분배) + daily_check 확장 | 2d | S6 |
| **합계** | | **12d** | |

## 2. Phase 1 — Live Scanner 전략 모듈 5종

### 2.1 마커 & 디스패치 (코드 변경 최소)

**file**: `src/backtest/strategies/_live_scanner_helpers.py` (신규)
```python
"""Helpers shared by live_* strategies."""
from typing import ClassVar

# 마커 mixin — orchestrator/dashboard 가 strategy 가 live-scanner 패러다임인지 확인
class LiveScannerMixin:
    """Strategies inheriting this opt-in to per-symbol dispatch + stop/TP management.

    Marker only — no behaviour. Orchestrator iterates ctx.symbols and dispatches
    on_bar(ctx) per-symbol when strategy is `isinstance(s, LiveScannerMixin)`.
    """
    is_live_scanner: ClassVar[bool] = True
    # default stop/TP — strategy may override class attribute
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    trailing_stop_pct: ClassVar[float | None] = None
```

**file**: `src/portfolio/_async_orchestrator.py` (수정)
- `run_bar(ts, snapshot)` 의 strategy dispatch 분기에서 `getattr(s, "is_live_scanner", False)` 가 True 면:
  - snapshot 의 `ohlcv_history: dict[symbol, DataFrame]` (또는 multi-symbol 형태) 에서 각 symbol 마다 `ctx_per_symbol` 만들어 `s.on_bar(ctx_per_symbol)` 호출
  - 결과 Signal 들을 종목별 OrderIntent 로 변환 (기존 `weights_to_orders` 우회 — 종목별 즉시 발주)
- 기존 `cs_*` / `momo_btc_v2` 등 universe-scan / single-ticker 분기는 변경 없음 (회귀 zero)

**테스트**: `tests/portfolio/test_orchestrator_live_scanner_dispatch.py`
- LiveScannerMixin 전략 1개 + 일반 AsyncStrategy 1개 동시 등록 → multi-symbol snapshot 흘리면 LiveScanner 만 per-symbol dispatch 받는지 검증

### 2.2 첫 신호: `live_rsi_oversold_volume_spike`

**file**: `src/backtest/strategies/live_rsi_oversold_volume_spike.py`
- 진입: `rsi_14 < 30` AND `volume_last / volume_ma_20 > 2.0`
- 청산: stop/TP 만 (strategy 자체 청산 없음 — `LivePositionRiskManager` 책임)
- `required_factors: ClassVar[list[str]] = ["rsi"]`
- class attr: `stop_loss_pct=0.03`, `take_profit_pct=0.06`

**spec**: `docs/specs/strategies/live-rsi-oversold-volume-spike.md` (frontmatter `type: strategy`, `paradigm: live-scanner`, `stop_loss_pct: 0.03`, `take_profit_pct: 0.06`)

**테스트**: `tests/backtest/strategies/test_live_rsi_oversold_volume_spike.py`
- synthetic OHLCV: RSI 50→25 + volume 2x → buy signal 발생
- RSI 50→25 + volume 1.5x (미달) → hold

### 2.3 4 추가 신호 (S4)

| strategy_id | 진입 룰 | required_factors |
|-------------|---------|-----------------|
| `live_macd_bullish_cross_breakout` | MACD bullish cross AND `close > rolling_max(20)` (직전 20일 신고가) | macd, donchian |
| `live_bb_lower_bounce` | `close[-2] < bb_lower[-2]` AND `close[-1] > bb_lower[-1]` AND `volume[-1] > volume_ma_20` | bbands |
| `live_breakout_with_atr_stop` | `close > rolling_max(20)` AND ATR-trailing stop (stop = close - 2*ATR) | donchian, atr |
| `live_oversold_with_divergence` | `signals.rsi.detect_divergence` 가 bullish AND price downtrend | rsi |

각 모듈 + spec + unit test 1건.

## 3. Phase 2 — `LivePositionRiskManager`

**file**: `src/portfolio/live_position_risk.py` (신규)
```python
class LivePositionRiskManager:
    """Per-position stop_loss / take_profit / trailing_stop manager.

    Subscribes to PnLAggregator + StrategyPositionStore.
    On each tick, checks all active positions vs entry price + class-attr thresholds.
    Emits SELL OrderIntent (reason='live_stop_loss' / 'live_take_profit' / 'live_trailing_stop').
    """

    def __init__(
        self,
        position_store: StrategyPositionStore,
        pnl_aggregator: PnLAggregator,  # for entry_price lookup
        orchestrator: AsyncStrategyOrchestrator,
    ): ...

    def evaluate(self, symbol: str, last_price: Decimal, ts: datetime) -> list[OrderIntent]:
        """Return SELL intents for all positions in *symbol* that breached stop/TP."""
        ...
```

- `pnl_aggregator` 의 평균매수가 (Decimal) 사용 → entry_price 정확
- trailing stop 은 high-water-mark 추적: `_high_water: dict[(strategy_id, symbol), Decimal]`
- WAL log: `event_type="position_stop_triggered"` payload {strategy_id, symbol, reason, entry_price, last_price, pct_change}

**spec frontmatter 확장**: `docs/schemas/note-schemas.md` 의 `type: strategy` 스키마에 추가
```yaml
paradigm: universe-scan | single-ticker | live-scanner  # NEW
stop_loss_pct: 0.03      # required if paradigm=live-scanner
take_profit_pct: 0.06    # required if paradigm=live-scanner
trailing_stop_pct: 0.02  # optional
```

**테스트**: `tests/portfolio/test_live_position_risk.py`
- 매수가 80,000 → 76,400 (3% stop) → SELL emit 검증
- 매수가 80,000 → 84,800 (6% TP) → SELL emit 검증
- trailing stop: 매수가 80,000 → 90,000 (high-water 90,000) → 88,200 (2% trail) → SELL emit
- stop_loss_pct=0.03 미달 (76,500) → emit 0건

## 4. Phase 3 — KIS broker universe-wide live quote (S5)

### 4.1 KIS WebSocket multi-subscribe

**현황**: `src/brokers/kis/async_ws.py` 이미 존재. 단일 symbol subscribe 패턴. multi-subscribe 는 KIS WS API 의 PINGPONG + 복수 PINGPONG 채널 운용 검증 필요.

**작업**:
- `src/brokers/kis/async_ws.py::AsyncKISWebSocket.subscribe_multi(symbols: list[str])` 메서드 추가
- KIS WS 채널당 동시 구독 한도 (40~50) 검증 → 350종 = ~7~9 채널 worker pool
- 단위 테스트: mock WS 서버에서 50종 동시 subscribe → 모든 종목 tick 수신 확인

**대안 (fallback)**: `src/live/feed_kis.py::KISMarketFeed` 에 stagger-fetch 모드 추가 (350종 ÷ 60s = 종목당 ~10s 간격). 임시 방편 — WS 우선.

### 4.2 Binance WS multi-subscribe

**현황**: `src/brokers/binance/async_ws.py` 이미 multi-symbol stream 지원 (USDT-M Futures aggTrade). 작업 거의 없음 — 30종 시연 + smoke 테스트.

### 4.3 PaperBroker 380 instruments

**현황**: `MockMatchingEngine` 이 단일 symbol 매칭. 380종 동시 시뮬은 별 instance 분리만 필요.

**테스트**: `tests/live/test_paper_broker_universe_scale.py`
- 380종 동시 진입/청산 → WAL 정합 + 메모리 < 500MB

## 5. Phase 4 — `loop.py` multi-symbol fan-out (S3)

**file**: `src/live/loop.py::run_shadow_loop` (수정)

```python
# 현재 (single-symbol per tick):
snapshot = snapshot_builder.build_snapshot(tick)
intents = await orchestrator.run_bar(ts, snapshot)

# 변경 후 (multi-symbol aware):
snapshot = snapshot_builder.build_snapshot(tick)  # 그대로 (per-symbol)
# orchestrator 가 LiveScannerMixin 전략에 대해 universe-wide ohlcv_history 를 보도록
# snapshot_builder 가 누적된 multi-symbol cache 도 함께 노출:
snapshot["universe_ohlcv"] = snapshot_builder.get_universe_cache()  # dict[symbol, DataFrame]
intents = await orchestrator.run_bar(ts, snapshot)

# 추가: position risk manager — 매 tick 실행
risk_intents = position_risk_manager.evaluate(
    tick.symbol, last_price=Decimal(str(tick.price)), ts=ts,
)
if risk_intents:
    await execute_intents(risk_intents, broker=router, ...)
```

**file**: `src/live/snapshot_builder.py` (수정)
- 내부 `_universe_ohlcv: dict[str, pd.DataFrame]` 추가 — 모든 subscribe symbol 의 backfill + 점진 누적
- `get_universe_cache() -> dict[str, pd.DataFrame]` 공개 API

### 5.1 env-gated activation

**file**: `scripts/live_run.py` (수정)
```python
if os.getenv("LIVE_SCANNER_ENABLED") == "1":
    # production.yaml 에서 paradigm=live-scanner 전략 register
    # + LivePositionRiskManager wiring
else:
    logger.info("LIVE_SCANNER_ENABLED!=1 — universe-scan / single-ticker only")
```

**configs/orchestrator/production.yaml** 추가 entry (default disabled):
```yaml
strategies:
  - id: live_rsi_oversold_volume_spike
    class: src.backtest.strategies.live_rsi_oversold_volume_spike.LiveRsiOversoldVolumeSpike
    paradigm: live-scanner
    enabled: false  # default OFF — env LIVE_SCANNER_ENABLED=1 + 명시 enable 필요
```

## 6. Phase 5 — 5y backtest 검증 (S6)

**file**: `scripts/bench_live_scanner.py` (신규)
- 5 신규 전략 × {KRX universe (cs_tsmom_kr_daily 와 동일 universe — KOSPI top-200 + KOSDAQ top-150) , Binance universe (cs_tsmom_crypto_daily 와 동일 — top-30 by 24h volume)} = 10 백테스트
- 각 백테스트 메트릭: Sharpe, MDD, AnnRet, Trades, WinRate, AvgHoldDays, 손익비 적합도 (실제 손익비 vs spec 의 stop/TP 비율)
- 결과를 `docs/specs/strategies/<id>.md` frontmatter 의 `sharpe_bt`, `mdd_bt`, `annual_return_bt`, `trades_bt` 갱신

**Gate**: `Sharpe ≥ 0.5` 통과 전략만 production.yaml 의 `enabled: true` 후보. 미통과 전략은 spec 에 명시 ("백테스트 실패 — production 미등록").

**검증 비용 모델**: KRX 라운드트립 55bp + intraday turnover 가정. 거래 1건 = 55bp 손실 출발 → strategy 가 평균 +0.55% 이상 못 내면 net negative.

## 7. Phase 6 — 대시보드 + Telegram (S7)

### 7.1 대시보드

**file**: `src/dashboard/app.py` (수정)
- `/api/strategies/{id}/positions` 응답에 stop/TP 거리 추가:
```json
{
  "symbol": "005930",
  "qty": 100,
  "entry_price": "80000",
  "last_price": "82500",
  "pnl_pct": 0.031,
  "stop_distance_pct": -0.061,    // 현재가 대비 stop 까지 거리
  "tp_distance_pct": 0.029,       // 현재가 대비 TP 까지 거리
}
```
- `/api/strategies/{id}/watchlist` 신규 — universe 중 임계값 거의 도달한 종목 (예: RSI 32~30 사이) top-10

**frontend**: `src/dashboard/templates/strategy_detail.html` 의 Live Scanner 섹션 추가 (HTMX/Hyperscript). Phase 7 마무리 단계에서 진행.

### 7.2 Telegram 알림

**file**: `services/telegram_bot/notifications.py` (확장)
- `notify_position_entered(strategy_id, symbol, entry_price, signal_reason)` — 진입 즉시
- `notify_position_exited(strategy_id, symbol, exit_price, pnl_pct, exit_reason)` — 청산 즉시 (stop / TP / trailing)
- `notify_daily_digest()` — 일 1회 (정시 cron) — 당일 진입 N건 / 청산 M건 / 일PnL

WAL observer 패턴 활용: `position_stop_triggered` / `signal_emitted` (action=buy) WAL event → telegram fan-out.

### 7.3 daily_check.ps1 / daily_check_kis.ps1

추가 체크:
- Live Scanner 활성 종목수 (`/api/strategies/<live_*>/positions` count)
- 실패 fetch 카운트 (KIS WS reconnect attempts metric)

## 8. Phase 7 — universe-scan 공존 정책 (S7)

### 8.1 자본 분배

**file**: `configs/orchestrator/production.yaml` (수정)
```yaml
capital_allocation:
  universe_scan_pct: 0.70    # cs_*, breakout_donchian
  live_scanner_pct: 0.30     # live_*
  single_ticker_pct: 0.00    # legacy momo_kis_v1 / momo_btc_v2 (비활성)
```

**file**: `src/portfolio/_async_orchestrator.py::_apply_capital_allocation` (신규)
- 각 paradigm 의 size 합산 → allocation 한도 내로 정규화

### 8.2 충돌 방지

같은 종목을 universe-scan + live-scanner 가 동시 보유 시:
- `risk.evaluate` 의 `per_symbol_concentration_limit` rule 이 합산 비중을 평가하도록 확장 (기존 룰 재사용)
- 단위 테스트: 005930 을 universe-scan 5% + live-scanner 4% = 9% 보유 시 합산 한도 (예: 8%) 초과 → 신규 진입 차단

### 8.3 daily_check 양 path 모니터링

기존 daily_check_kis.ps1 + daily_check.ps1 에 두 paradigm 모두 표시:
- universe-scan 활성 전략수 + 직전 리밸 시각
- live-scanner 활성 종목수 + 직전 진입/청산 시각

## 9. 횡단 검증 / 마무리 체크리스트

- [ ] `python scripts/check_invariants.py --strict` 통과 (특히 #6 LLM 위임 금지)
- [ ] `pytest tests/portfolio/ tests/backtest/strategies/ tests/live/ -k "live_scanner"` 전건 green
- [ ] 5y backtest 결과 frontmatter 갱신 완료 + benchmark 대비 알파 명시
- [ ] `LIVE_SCANNER_ENABLED=0` (default) 운영에서 zero-impact 회귀 — 기존 cron / shadow loop 정합
- [ ] `LIVE_SCANNER_ENABLED=1` smoke (paper, 1h) — 진입 1건 + 청산 1건 + 대시보드 표시 + telegram 알림 도착
- [ ] `.ai.md` 갱신: `src/backtest/strategies/.ai.md`, `src/portfolio/.ai.md`, `src/live/.ai.md`, `services/telegram_bot/.ai.md`
- [ ] `docs/specs/live-universe-scanner-paradigm.md` 신규 spec (universe-scan-strategy-pattern 과 대응 — 별 paradigm 명시)

## 10. 위험 / 미해결 질문

| # | 위험 | 완화 |
|---|------|------|
| R1 | KIS WS 동시 구독 채널 한도 미문서화 — 350종 한 번에 안 될 수 있음 | S5 spike 에서 검증. fallback = stagger REST + symbol 우선순위 큐. |
| R2 | intraday turnover 폭증 → 거래비용 net negative | Phase 5 backtest gate (Sharpe ≥ 0.5) 가 stop. 미통과 전략은 production 미등록. |
| R3 | LiveScannerMixin 도입이 기존 strategy 등록 path 의 dispatch 분기를 늘림 → cs_* 회귀 가능성 | dispatch 분기를 `getattr(s, "is_live_scanner", False)` 단순 체크로 한정. cs_* 는 이 attr 없으므로 100% legacy path 유지. 단위 테스트 추가. |
| R4 | `production.yaml` paradigm 필드 — 현 schema 미정의 | S2 단계에서 `docs/schemas/note-schemas.md` 의 strategy 스키마에 paradigm 필드 추가 + invariant check 갱신. |
| R5 | TaskAlloc 합산 검증 — 두 paradigm 간 자본 비율 강제 미구현 시 risk evaluator 가 침묵 | S7 의 충돌 방지 단위 테스트가 회귀 보호. |
| Q1 | `LiveScannerMixin` 의 `is_live_scanner` 가 단일종목에 fixed 인지, watchlist 형태로 multiple 도 가능한지? | **답**: multiple. orchestrator 가 universe symbol 마다 dispatch. strategy 자체는 stateless per-symbol 평가. |
| Q2 | momo_kis_v1 (현 운영 단일종목 prototype) 을 deprecate? retain? | **답 (제안)**: retain — 단일종목 hedge 옵션 유지. 단 spec 에 "Live Scanner 활성화 후 효용 검토" 명시. 운영 보드에서 `enabled: false` 로 OFF 가능. |

## 11. 다음 단계 (구현 시작 전)

1. ☑️ `.ai.md` 읽기 완료 (backtest, live, portfolio, strategies)
2. ☑️ universe-scan-strategy-pattern.md 읽기 완료 (본 plan 의 별 paradigm 정의 근거)
3. ☑️ 핵심 코드 read 완료 (protocol.py, momo_kis_v1, breakout_donchian, loop.py, executor.py)
4. **다음**: 사용자에게 이 plan 검토 요청 + S1 (Phase 1 spike) 시작 승인 받기
5. S1 머지 후 S2 → S7 순차 진행. 각 slice 별 별도 PR.

## 부록 A — 파일 추가/수정 요약

### 신규 파일
- `src/backtest/strategies/_live_scanner_helpers.py`
- `src/backtest/strategies/live_rsi_oversold_volume_spike.py`
- `src/backtest/strategies/live_macd_bullish_cross_breakout.py`
- `src/backtest/strategies/live_bb_lower_bounce.py`
- `src/backtest/strategies/live_breakout_with_atr_stop.py`
- `src/backtest/strategies/live_oversold_with_divergence.py`
- `src/portfolio/live_position_risk.py`
- `scripts/bench_live_scanner.py`
- `docs/specs/strategies/live-rsi-oversold-volume-spike.md` (외 4건)
- `docs/specs/live-universe-scanner-paradigm.md`
- `tests/portfolio/test_orchestrator_live_scanner_dispatch.py`
- `tests/portfolio/test_live_position_risk.py`
- `tests/backtest/strategies/test_live_*.py` (5건)
- `tests/live/test_paper_broker_universe_scale.py`

### 수정 파일
- `src/portfolio/_async_orchestrator.py` (per-symbol dispatch 분기 + 자본 분배)
- `src/live/loop.py` (multi-symbol cache + position_risk_manager wiring)
- `src/live/snapshot_builder.py` (`get_universe_cache` 공개 API)
- `src/brokers/kis/async_ws.py` (`subscribe_multi`)
- `src/dashboard/app.py` (positions endpoint 확장 + watchlist endpoint)
- `services/telegram_bot/notifications.py` (3 신규 함수)
- `scripts/live_run.py` (env gate + LIVE_SCANNER_ENABLED 분기)
- `scripts/check_invariants.py` (paradigm field invariant)
- `configs/orchestrator/production.yaml` (5 strategy entry + capital_allocation)
- `daily_check.ps1`, `daily_check_kis.ps1`
- `docs/schemas/note-schemas.md` (strategy 스키마에 paradigm/stop_loss_pct/take_profit_pct/trailing_stop_pct)
- `src/backtest/strategies/.ai.md`, `src/portfolio/.ai.md`, `src/live/.ai.md`, `services/telegram_bot/.ai.md`
