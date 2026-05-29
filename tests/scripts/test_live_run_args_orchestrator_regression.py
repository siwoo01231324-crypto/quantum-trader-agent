"""Regression guard — PR #336/#337 `args._orchestrator = orch` NameError.

PR #336/#337 (Dynamic Universe Architecture) introduced three callsites that
reference an `args` name that doesn't exist in the enclosing scope:

  - scripts/live_run.py `_serve()` (dashboard pre-build)
  - scripts/live_run.py `_run_pipeline_attached._on_orchestrator_ready`
  - scripts/live_run.py `_run_pipeline._on_orchestrator_ready`

`_serve()` wrapped the call in try/except so it silently logged
"[qta] dashboard orch pre-build skipped: NameError(...)". The two
`_on_orchestrator_ready` callbacks fire from the live loop and would
NameError every startup — leaving `args._orchestrator = None` forever, which
made `_build_universe_quote_provider._collect_strategy_universes` return an
empty dict, falling back to TOP30 × 1d. airborne (1h, 100 symbols) became
inert: 14h of `python scripts/live_run.py` produced 0 trades on 2026-05-28
while the daemon emitted 139 fires.

These tests pin the fix: `_run_pipeline_attached` and `_run_pipeline` must
accept an `args` kwarg, and the callback must populate `args._orchestrator`
on a passed-in Namespace.
"""
from __future__ import annotations

import inspect
from argparse import Namespace
from unittest.mock import MagicMock

import scripts.live_run as live_run


def test_run_pipeline_attached_accepts_args_kwarg():
    """dashboard '거래 시작' 경로 — _factory 가 자기 Namespace 를 명시 전달해야
    _on_orchestrator_ready 가 NameError 없이 args._orchestrator 박는다."""
    sig = inspect.signature(live_run._run_pipeline_attached)
    assert "args" in sig.parameters, (
        "_run_pipeline_attached must accept an 'args' kwarg so the nested "
        "_on_orchestrator_ready can attach args._orchestrator for the "
        "universe quote provider. Without it the closure raised NameError "
        "on every live-loop startup → dynamic universe (100종목/1h) silently "
        "fell back to TOP30/1d. PR #336/#337 회귀 가드."
    )


def test_run_pipeline_accepts_args_kwarg():
    """CLI 경로 — main() 이 args 를 명시 전달해야 _on_orchestrator_ready 가 작동."""
    sig = inspect.signature(live_run._run_pipeline)
    assert "args" in sig.parameters, (
        "_run_pipeline must accept an 'args' kwarg — same regression as "
        "_run_pipeline_attached but on the CLI path (qta.exe --symbols ...)."
    )


def test_serve_no_args_reference():
    """standalone dashboard pre-build 코드에서 args 참조가 사라졌는지.

    과거 _serve() 안의 `args._orchestrator = state.orchestrator` 라인은
    enclosing scope 에 args 가 없어서 매번 NameError → try/except 가
    'dashboard orch pre-build skipped' 메시지로 silent swallow. 데드코드였다.
    """
    src = inspect.getsource(live_run._run_dashboard_only_mode)
    assert "args._orchestrator" not in src, (
        "_run_dashboard_only_mode() (containing _serve) must not reference "
        "args._orchestrator — that line never executed (NameError swallowed) "
        "and gave the false impression that standalone pre-build attached "
        "the orchestrator to the universe provider. Removed in this fix."
    )


def test_on_orchestrator_ready_writes_args_when_passed():
    """행동 검증 — args 가 주입됐을 때 _on_orchestrator_ready 가 정확히
    args._orchestrator = orch 를 박는다. closure 가 args 를 잃지 않는지 확인."""
    # _on_orchestrator_ready 는 nested 함수라 직접 추출이 어렵다.
    # 대신 src 에 'if args is not None:\n            args._orchestrator = orch'
    # 패턴이 두 번 (attached + pipeline) 들어있는지 검사.
    src = inspect.getsource(live_run)
    occurrences = src.count("args._orchestrator = orch")
    assert occurrences == 2, (
        f"expected 2 assignments of args._orchestrator = orch (one per "
        f"_on_orchestrator_ready in attached + pipeline paths), got {occurrences}."
    )
    # 각 할당이 `if args is not None:` 가드 아래에 있는지.
    assert src.count("if args is not None:\n            args._orchestrator = orch") == 2, (
        "Each `args._orchestrator = orch` must be gated by `if args is not None:`. "
        "Without the guard a stray CLI invocation (args 미전달) would re-introduce "
        "the original NameError silent failure."
    )


def test_universe_provider_uses_args_orchestrator():
    """`_build_universe_quote_provider` 가 args._orchestrator 로 strategies 를
    실제로 lookup 하는지 — 행동 보존 검증 (PR #336 의 본래 의도)."""
    # args._orchestrator 에 가짜 orch 박고 provider 생성 → _collect 가 빈 dict
    # 가 아니라 strategies 의 universe 를 반환해야 한다.
    args = Namespace()
    fake_strat = MagicMock()
    fake_strat.get_universe = MagicMock(return_value=["FAKEUSDT", "DEMOUSDT"])
    fake_strat.get_interval = MagicMock(return_value="1h")
    fake_orch = MagicMock()
    fake_orch._strategies = {"fake-strat": fake_strat}
    args._orchestrator = fake_orch

    # provider 는 fetch_universe_klines 호출. mock 으로 어떤 symbols / interval
    # 가 들어가는지 캡쳐.
    import src.brokers.binance.universe_quote as uq_mod
    seen: dict = {}

    def _fake_fetch(syms, interval="1d"):
        seen["syms"] = list(syms)
        seen["interval"] = interval
        return {s: object() for s in syms}

    orig = uq_mod.fetch_universe_klines
    uq_mod.fetch_universe_klines = _fake_fetch
    try:
        provider = live_run._build_universe_quote_provider(
            "binance-testnet-shadow", None, args,
        )
        result = provider()
    finally:
        uq_mod.fetch_universe_klines = orig

    # active 전략의 universe + interval 이 fetch 에 전달돼야 한다.
    assert seen.get("interval") == "1h", (
        f"interval should come from strategy.get_interval(), got {seen.get('interval')!r}. "
        f"NameError-fallback 시 1d 가 들어가던 회귀 사고 가드."
    )
    assert set(seen.get("syms") or []) == {"FAKEUSDT", "DEMOUSDT"}, (
        f"universe should come from strategy.get_universe(), got {seen.get('syms')!r}."
    )
    assert set(result.keys()) == {"FAKEUSDT", "DEMOUSDT"}
