---
type: spec-architecture
id: dynamic-universe-architecture
name: Dynamic Universe Architecture — 전략별 universe + interval 선언으로 orchestrator 가 동적 fetch
status: in-progress
owner: siwoo
created: 2026-05-28
updated: 2026-05-28
tags:
- universe
- orchestrator
- live-scanner
- architecture
---

# Dynamic Universe Architecture

## 도입 배경

현재 orchestrator (`scripts/live_run.py`) 는 universe 를 한 곳에서 **하드코딩**:

```python
return fetch_universe_klines(_binance_top30(), interval="1d")
```

→ 모든 active live-scanner / universe-scan 전략이 **BINANCE_USDT_TOP30 의 1d 봉만** 받음.

이로 인해:
1. `live-airborne-bb-reversal-kst-hours` 가 1h universe / 1h interval 가정인데 실제로는 1d 봉만 들어와 *무용지물* 상태
2. daemon 의 top-100 dynamic universe 와 mismatch → 30 종목 밖 fire 는 자동매매 안 됨
3. 별도 컨테이너 (`qta-airborne-trader`) 로 우회 — 운영 복잡도 ↑

## 목표

**전략이 자기 universe + interval 선언 → orchestrator 가 union 으로 fetch → 각 전략에 자기 universe 만 dispatch.**

기존 전략 (cs-tsmom 등) 은 explicit TOP30 + 1d 선언으로 **byte-identical 동작 보존**.

## API 디자인

### `LiveScannerMixin` (live-scanner 패러다임 공통)
```python
class LiveScannerMixin:
    # 기본값 — BINANCE_USDT_TOP30, 1d. 회귀 안전.
    @classmethod
    def get_universe(cls) -> list[str]:
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        return list(BINANCE_USDT_TOP30)

    @classmethod
    def get_interval(cls) -> str:
        return "1d"
```

### `live-airborne-bb-reversal-kst-hours` override
```python
class LiveAirborneBbReversalKstHours(...):
    @classmethod
    def get_universe(cls) -> list[str]:
        # daemon 과 같은 동적 top-100 (또는 정적 100 — Phase 2 결정).
        # Phase 1 은 정적 TOP30 유지, Phase 2 에서 dynamic.
        ...

    @classmethod
    def get_interval(cls) -> str:
        return "1h"
```

### `live_run._build_universe_quote_provider`
변경 후:
```python
def _binance_provider():
    # 등록된 모든 전략에서 get_universe() / get_interval() 수집
    union: dict[str, set[str]] = {}  # interval → symbol set
    for strat in orchestrator._strategies.values():
        if hasattr(strat, "get_universe") and hasattr(strat, "get_interval"):
            union.setdefault(strat.get_interval(), set()).update(strat.get_universe())
    # interval 별로 fetch 후 합치기
    ohlcv = {}
    for interval, syms in union.items():
        partial = fetch_universe_klines(sorted(syms), interval=interval)
        # 같은 symbol 다른 interval 일 경우 — symbol-major dispatch 후속 PR.
        # Phase 1 은 first-wins (1d 우선 — cs-tsmom 보존).
        for sym, df in partial.items():
            ohlcv.setdefault(sym, df)
    return ohlcv
```

### Snapshot dispatch
현재 `orchestrator.run_bar` 가 `snapshot["ohlcv_history"]` 의 *모든* symbol 을 각 전략에 dispatch. **전략별 universe filtering** 은 후속 PR (Phase 3).

Phase 1 에서는 universe 가 union 으로 커진 만큼 모든 전략이 더 많은 symbol 받음 — strategy 가 자기 모르는 symbol 받으면 `hold` 반환해야 안전.

## 단계별 PR

### Phase 1 — Interface + cs-tsmom 보존 (이번 PR)
- [x] `LiveScannerMixin.get_universe()` / `get_interval()` 클래스 메서드 추가 (default TOP30 / 1d)
- [x] live-airborne-bb-reversal-kst-hours 가 interval="1h" override
- [x] `live_run._binance_provider` 가 active 전략들의 union universe + per-interval fetch
- [x] cs-tsmom-crypto-daily 의 출력 byte-identical 회귀 검증
- [x] 단위 테스트 — universe union / interval per fetch / unknown symbol graceful hold

### Phase 2 — airborne universe TOP100 으로 확장 (후속 PR)
- airborne 전략의 `get_universe` 가 daemon 의 top-100 동적 동기
- `qta-airborne-trader` 컨테이너 deprecate (또는 별도 entity 유지 결정)

### Phase 3 — Per-strategy universe filtering (후속 PR)
- `orchestrator.run_bar` 가 각 전략에 자기 universe symbol 만 dispatch
- 다른 전략 symbol 받아도 graceful hold 가 아닌 명시적 skip

## 회귀 위험

| 위험 | mitigation |
|---|---|
| cs-tsmom 의 1d 봉 동작 변화 | get_universe/get_interval 명시적 TOP30/1d → byte-identical |
| universe 확장으로 unknown symbol 받음 | 모든 live-scanner 의 `on_bar` 가 history None / 부족 시 hold 반환 — 기존 코드 이미 그렇게 됨 |
| Binance fapi 호출량 ↑ | TOP30 → max 100 = 3배. rate limit 2400 req/min 안에 충분히 들어감 |
| 동일 symbol 다중 interval 충돌 | Phase 1 first-wins (1d 우선). Phase 3 에서 symbol-major 분리. |

## 관련

- [[live-airborne-bb-reversal-kst-hours]]
- [[airborne-trader-daemon]] — 후속 PR 에서 deprecate 검토
- [[live-universe-scanner-paradigm]]
