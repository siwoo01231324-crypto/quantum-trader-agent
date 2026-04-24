---
id: 01_plan
type: work-plan
issue: 76
iteration: 3
status: final-approved
supersedes: 01_plan.md @ iter-2
---

# 01_plan.md — #76 Signal interface + #84 patent adoption (iter 3, final-approved with 5 polish items baked)

references: [[76-signal-interface]], [[84-patent-adoption]], [[31-patents-portware]], [[32-patents-kr-reliability]], [[33-patents-axioma]], [[34-patents-llm-delegation]], [[20-position-sizing]]

---

## 1. RALPLAN-DR Summary (iter 3, 5 polish folds baked)

### Principles (5, finalized)
- **P1 Determinism-first** — all new code paths deterministic; NumPy RNG seeded; no LLM on exec paths (invariant #6).
- **P2 Backward compatibility on a single axis** — Signal extension is additive + kw-only; legacy `Signal(action, size, reason)` stays green.
- **P3 Patent-delta via structural differentiation** — every adopted patent idea gets a **structural** (not cosmetic) difference from the claim: Portware→FactorSpec-with-explicit-`bar_interval`; KR reliability→**multiplicative gate** (not additive convex combo); Axioma→abandoned, cite-and-move; LLM→invariant #6 lint.
- **P4 Signal-wins precedence** — when Signal carries `expected_return`/`win_probability`, sizer uses them verbatim; None → 60-bar rolling fallback. Zero is **not** None.
- **P5 Bake-everything** — specs, citations, invariant-lint, tests, .ai.md, regression JSON all land in THIS PR.

### Decision Drivers (unchanged)
- **DR1** Regression = 0 on existing momo-btc-v2 behavior at Signal-default call-sites.
- **DR2** Patent-infringement risk ≡ 0 post-merge (cite + structural-differ + lint).
- **DR3** Auditability — every adopted idea traces to patent note + spec + test + invariant check.

### Finalized Decisions D1–D8 (no longer alternatives)
- **D1** `Signal` grows 3→6 fields: `action, size, reason, expected_return, win_probability, confidence`. `strategy_id` **dropped** → moved to `register_strategy_returns(strategy_id, ...)` call-site param.
- **D2** Signal-wins precedence (sizer prefers Signal-provided values; None → fallback).
- **D3** `FactorSpec` extended with `alpha_horizon_bars: int = 1` **AND** `bar_interval: str = "1d"` (pair-form, mandatory metadata).
  - **Closed vocabulary**: `{"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}`. Unknown `bar_interval` values MUST raise `ValueError` at `@register()` decoration time (not at runtime).
- **D4** Single `neutralize(raw, *exposures, method: Literal["ols","orthogonal"]="ols")` entry point.
- **D5** Reliability = `convex_base × drawdown_gate` (multiplicative gate — substantive structural delta from KR additive claim).
  - **NaN guard (precondition)**: `if T < 20: return 0.0  # avoids Φ(NaN) when t_IR is undefined for tiny samples`
  - `convex_base = 0.4·h(T) + 0.4·Φ(t_IR) + 0.2·(1 − CVaR_breach_rate)`
  - `drawdown_gate = min(1.0, max(0.0, 1.0 − max_dd_pct / 0.20))` (bounded [0, 1]; threshold 20%)
  - Final bounded [0, 1] with explicit NaN guard at T<20 returning 0.0, monotone-decreasing in drawdown, multiplicative (not weighted sum).
- **D6** momo-btc-v2 confidence: `0.5 + 0.3·tanh(2·(rsi_signal−0.5)) + 0.2·trend_alignment` (unchanged).
- **D7** Flat test layout (`tests/test_*.py`, no nested subdirs).
- **D8** 4 `.ai.md` updates + 3 new specs (`signal-interface.md`, `factor-registry.md`, `reliability-score.md`), each with explicit patent-cite block.

### Pre-mortem (4 scenarios)
- **S1 Signal BC silent break** — kw-only Optional extension + `test_signal_backcompat.py` asserting 3-arg ctor still works; CI job greps for legacy `Signal(...)` construction count pre/post.
- **S2 Reliability gaming** — unit test asserts `max_dd_pct = 0.20` → `drawdown_gate = 0` → `reliability = 0` regardless of `convex_base`; prevents high-Sharpe-high-DD exploits.
- **S3 `method="orthogonal"` numerical instability** — designed-collinearity synthetic vector: `exposure_a = [1,1,1,1,…]`, `exposure_b = 2·exposure_a + ε` (ε ~ N(0, 1e-8)); assert `cond(stacked_exposures) > 1e8` triggers `warnings.warn("neutralize: cond=%.2e approaching grey zone for float64")`; `cond > 1e10` triggers deterministic fallback to OLS path. Grey-zone `[1e8, 1e10]` is where orthogonal is still numerically usable but information-degraded. Tolerance `atol=1e-6`.
- **S4 Patent cite rot** — `_check_llm_delegation` lint enforces presence of `"US8433645B1"`, `"KR101139626B1"`, `"US20140081889A1"` + `"differs:"` / `"abandoned"` strings in named files; CI fails if removed.

### Verification plan
- **Unit**: Signal fields, FactorSpec Literal reject, neutralize known-answer (param), reliability components.
- **Integration**: sizer precedence + zero/None sentinel, momo-btc-v2 confidence emission, orchestrator reliability.
- **Regression**: `scripts/compare_momo_btc_v2_signal_interface.py` → `sizing_comparison_with_confidence.json`.
- **Property**: Gram-Schmidt idempotence on `neutralize(method="orthogonal")`.
- **Observability**: structured log on orthogonal→OLS fallback (`logger.warning("neutralize: cond=%.3e, fallback=ols")`).
- **Patent**: citation-string lint + spec-body §patent-distinguish paragraph presence.
- **Invariant #6-as-lint**: `scripts/check_invariants.py::_check_llm_delegation` (keyword tripwire; AST follow-up flagged).

---

## 2. ADR-lite per D1–D8

| D | Decision | Drivers | Alternatives | Why chosen | Consequences | Follow-ups |
|---|---|---|---|---|---|---|
| **D1** | Signal 6 fields, no strategy_id | DR1,DR2 | Keep strategy_id on Signal | strategy_id couples signal-emit to bookkeeping; cleaner at register call-site | `register_strategy_returns` signature change | — |
| **D2** | Signal-wins precedence | DR1 | Always-rolling | Lets strategies override with informed priors | Sizer branches on None | OrderIntent propagation (OOS) |
| **D3** | `alpha_horizon_bars` + `bar_interval` | DR2,DR3 | Single `alpha_horizon_sec` field | Pair-form is explicit, bars-native, avoids implicit conversion; differs from Portware's embedded-minute | Every factor must declare both | TWAP/VWAP routing (OOS) |
| **D4** | Single `neutralize(..., method=)` | DR1 | Two functions (ols_neutralize, gram_schmidt) | One API, one test matrix, one docstring; reduces surface | method validator required | — |
| **D5** | Multiplicative reliability | DR2 | 4-term additive convex | KR claim is additive weighted combination; multiplicative gate is structurally distinct + safer (DD=threshold → 0); hard-zero at DD≥20% is a **discontinuous indicator function** not recoverable by any additive/log-additive decomposition without floor-truncation that contaminates the zero-output guarantee | Non-convex w.r.t. DD; monotone ok; discontinuity at DD=20% is load-bearing for patent defense | ENB downweighting (OOS) |
| **D6** | momo confidence formula | DR1 | Raw RSI | tanh gate + trend term already validated iter-1 | Confidence ∈ [0,1] bounded | — |
| **D7** | Flat test layout | DR3 | Nested subdirs | Consistent with repo; less import juggling | None | — |
| **D8** | 3 new specs + 4 .ai.md | DR3 | Inline docstrings only | Vault is source-of-truth; wikilink discoverability | Line-count ceilings enforced | — |

---

## 3. Implementation steps (35 total)

### Phase 0 — Hard-block gate
1. Check `docs/background/3[1-4]-patents-*.md` exists (31/32/33/34). **If ANY missing → ABORT** with error:
   ```
   Phase 0 blocked: #84 patent research notes must merge before #76 can proceed.
   Missing: {list}. See `gh issue view 84`. Suggest CI label: blocked-by:#84.
   ```
   Named test: `tests/test_phase0_gate.py::test_phase0_aborts_cleanly_without_patent_notes` asserts exact string + exit code 2. Patent notes are NOT created in this PR (scope boundary).
2. Baseline `python scripts/check_invariants.py --strict` green.

### Phase 1 — Docs first (spec-before-code)
3. Create `docs/specs/signal-interface.md` (type: spec-interface, ≤250 lines): 6 fields, Signal-wins precedence, zero-vs-None semantics, invariant #6 cite (US34/LLM-delegation patent note).
4. `check_invariants --strict` green.
5. Extend `docs/specs/signals/rsi-divergence.md` with D6 confidence formula + worked example + LLM-disclaimer ("no LLM on exec path — invariant #6").
6. `check_invariants --strict` green.
7. Create `docs/specs/factor-registry.md` (type: spec-interface, ≤400 lines): FactorSpec fields, `signal_type` Literal vocabulary, **`bar_interval` closed vocabulary**: `{"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}` — unknown values MUST raise `ValueError` at `@register()` decoration time; include `bar_interval` → seconds mapping table for the full closed set (`1m→60, 5m→300, 15m→900, 30m→1800, 1h→3600, 4h→14400, 1d→86400, 1w→604800`). Portware §patent-distinguish ("`US8433645B1` differs: we externalize `bar_interval` metadata; Portware embeds timing in execution engine").
8. `check_invariants --strict` green.
9. Create `docs/specs/reliability-score.md` (type: spec-interface, ≤400 lines): **multiplicative formula verbatim**, worked examples (T∈{20,126,250} × max_dd∈{5%,10%,20%}), §patent-distinguish paragraph: "`KR101139626B1` claims additive weighted combination of reliability components; ours uses `convex_base × drawdown_gate` — multiplicative gate where DD≥20% forces reliability→0 independent of base, which the additive claim cannot express. Structural distinction: the hard-zero at DD≥20% is a **discontinuous indicator function**, not recoverable by any additive or log-additive decomposition (`log(reliability) = log(convex_base) + log(gate)`) without floor-truncation `max(gate, ε)` that contaminates the zero-output guarantee."
10. `check_invariants --strict` green.
11. Light-touch backlinks: `docs/specs/20-position-sizing.md §7.1` add "delivered in #76" marker; append to `31/32/33-patents-*.md` "adopted in #76 with differentiated formula" (if notes present).
12. `check_invariants --strict` green.

### Phase 2 — Protocol + Registry extensions
13. `src/backtest/protocol.py::Signal` extend with kw-only Optional fields (`expected_return: Optional[float] = None`, `win_probability: Optional[float] = None`, `confidence: Optional[float] = None`). **DROP `strategy_id`**. Docstring must contain: `"US20140081889A1"` + `"abandoned"` (Axioma reference), invariant #6 warning ("no LLM on decision path").
14. `src/signals/registry.py::FactorSpec` extend with `alpha_horizon_bars: int = 1` + `bar_interval: str = "1d"`; `signal_type: Literal["momentum","mean_reversion","volatility","trend","breakout"]` (unchanged). Docstring must contain: `"US8433645B1"` + `"differs:"` (Portware cite-and-differ paragraph).

### Phase 3 — Factor migration (7 factors)
15. Migrate 7 factor `register(...)` calls with explicit metadata:
    - `rsi`: `alpha_horizon_bars=5, bar_interval="1d", signal_type="mean_reversion"`
    - `sma`: `alpha_horizon_bars=10, bar_interval="1d", signal_type="trend"`
    - `sma_cross`: `alpha_horizon_bars=10, bar_interval="1d", signal_type="trend"`
    - `atr`: `alpha_horizon_bars=1, bar_interval="1d", signal_type="volatility"`
    - `macd`: `alpha_horizon_bars=10, bar_interval="1d", signal_type="momentum"`
    - `bollinger`: `alpha_horizon_bars=5, bar_interval="1d", signal_type="mean_reversion"`
    - `realized_vol`: `alpha_horizon_bars=20, bar_interval="1d", signal_type="volatility"`

### Phase 4 — New pure modules
16. `src/signals/neutralize.py::neutralize(raw, *exposures, method: Literal["ols","orthogonal"]="ols") -> np.ndarray`. Single entry point. Two-tier conditioning guard: `cond(stacked_exposures) > 1e8` → `warnings.warn("neutralize: cond=%.2e approaching grey zone for float64")`; `cond > 1e10` → deterministic fallback to OLS path (grey-zone `[1e8, 1e10]` is numerically usable but information-degraded). Module docstring must contain: `"US20140081889A1"` + `"abandoned"` (Axioma cite).
17. `src/portfolio/orchestrator.py::StrategyOrchestrator.strategy_reliability_score(strategy_id: str) -> float` with **multiplicative** formula per D5. **NaN guard precondition**: `if T < 20: return 0.0  # avoids Φ(NaN) when t_IR is undefined for tiny samples` must be the first executable line of the method body. `register_strategy_returns(strategy_id: str, returns, t_stat_ir, cvar_breach_rate, max_dd_pct, T)` — strategy_id is **here**, not on Signal (A1 target). Module docstring must contain: `"KR101139626B1"` + `"differs:"` (multiplicative-vs-additive paragraph).

### Phase 5 — Integration
18. `src/risk/sizing.py` (or `src/backtest/engine.py` call-site): Signal-wins precedence. Pseudocode:
    ```python
    er = signal.expected_return if signal.expected_return is not None else rolling_er_60b(...)
    wp = signal.win_probability if signal.win_probability is not None else rolling_wp_60b(...)
    # zero is respected: Signal(expected_return=0.0) → er=0.0 (not fallback)
    ```
19. `src/backtest/strategies/momo_btc_v2.py::_compute_confidence` + Signal emission carries `expected_return` + `confidence` (NO strategy_id on Signal).

### Phase 6 — Regression compare
20. `scripts/compare_momo_btc_v2_signal_interface.py` produces `sizing_comparison_with_confidence.json` (baseline vs Signal-wins paths). Asserts win_rate delta within ±2pp OR records documented reason in JSON `"divergence_reason"` field.

### Phase 7 — Tests
21. `tests/test_signal_interface.py` — field defaults, full ctor, 6-field snapshot (regression-proof).
22. `tests/test_signal_backcompat.py` — `Signal(action="buy", size=0.1, reason="t")` constructs; all new fields None.
23. `tests/test_signal_sizer_integration.py` — Signal-wins precedence + `test_signal_zero_vs_none_sentinel` (A7): `expected_return=0.0` uses 0.0 (not fallback); `=None` uses 60-bar rolling; assert outputs differ.
24. `tests/test_signals_registry.py` extend — `alpha_horizon_bars`/`bar_interval` present, Literal `signal_type` rejects unknown via pydantic/dataclass validator, **`test_register_rejects_unknown_bar_interval`** — `@register(bar_interval="2h")` raises `ValueError` at decoration time (closed vocabulary `{"1m","5m","15m","30m","1h","4h","1d","1w"}`).
25. `tests/test_signals_neutralize.py` —
    ```python
    @pytest.mark.parametrize("method", ["ols", "orthogonal"])
    def test_neutralize_method_known_answer(method):
        # exposure0 = [1,1,1,1,1], exposure1 = [1,2,3,4,5]
        # raw = 1.0*exposure0 + 2.0*exposure1 + rng.normal(0,0.01,5)
        # residual = neutralize(raw, exposure0, exposure1, method=method)
        # assert np.allclose(residual @ exposure0, 0, atol=1e-3)
        # assert np.allclose(residual @ exposure1, 0, atol=1e-3)
    ```
    Plus Gram-Schmidt idempotence (`orthogonal` applied twice == once), `cond > 1e10` fallback test, synthetic RankIC.
26. `tests/test_portfolio_orchestrator.py` extend — reliability at T∈{20,126,250}, `max_dd_pct=0.20 → gate=0 → reliability=0` (multiplicative verification), unknown strategy_id raises, **`test_reliability_nan_guard_at_T_lt_20`** — `len(returns) = 19` → `strategy_reliability_score()` returns `0.0` (not NaN; verifies NaN guard precondition).
27. `tests/test_momo_btc_v2.py` extend — confidence rule regression + pre/post JSON compare.
28. `tests/test_phase0_gate.py` — A6 named test: abort error format + exit code 2 + CI label suggestion string presence.
29. `tests/test_regression_json.py` — `sizing_comparison_with_confidence.json` schema assertions (required keys, types).

### Phase 8 — .ai.md updates (BEFORE test run)
30. Update `src/backtest/.ai.md`, `src/risk/.ai.md`, `src/signals/.ai.md`, `src/portfolio/.ai.md` with: Signal contract (6 fields, no strategy_id), FactorSpec pair-form extension, neutralize single API + fallback behavior, reliability_score multiplicative form.

### Phase 9 — Gates
31. `scripts/check_invariants.py` — add `_check_llm_delegation(root: Path) -> list[str]`:
    - Scan `src/{backtest,risk,signals,portfolio}/**/*.py` for top-level imports matching `_KNOWN_LLM_IMPORTS` (13 entries) → error:
      ```python
      _KNOWN_LLM_IMPORTS = frozenset({
          "anthropic", "openai", "langchain", "langchain_community",
          "httpx.AsyncClient", "litellm",
          # extended 2026-04 (Critic iter-2 fold):
          "cohere", "mistralai", "google.generativeai", "vertexai",
          "replicate", "ollama", "boto3",  # boto3 covers bedrock-runtime path
      })
      ```
    - Enforce citation presence: `src/backtest/protocol.py` contains `"US20140081889A1"` + `"abandoned"`; `src/signals/registry.py` contains `"US8433645B1"` + `"differs:"`; `src/signals/neutralize.py` contains `"US20140081889A1"` + `"abandoned"`; `src/portfolio/orchestrator.py` contains `"KR101139626B1"` + `"differs:"`.
    - **Limitation docstring (A8)**: "Keyword-tripwire over 13-entry `_KNOWN_LLM_IMPORTS` frozenset only; `from my_llm_wrapper import classify` would pass. AST-based semantic check deferred to separate follow-up issue."
32. Targeted pytest: `pytest tests/test_signal_* tests/test_signals_* tests/test_portfolio_orchestrator.py tests/test_momo_btc_v2.py tests/test_risk_sizing.py tests/test_phase0_gate.py tests/test_regression_json.py -q` green.
33. Full `pytest -q` + `python scripts/check_invariants.py --strict` green.
34. `python scripts/compare_momo_btc_v2_signal_interface.py` → JSON produced; `test_regression_json.py` green.
35. Smoke + line-count ceilings + approval:
    - `python -c "from backtest.protocol import Signal; print(Signal(action='buy', size=0.1, reason='t', expected_return=0.02, win_probability=0.55, confidence=0.7))"`
    - `wc -l docs/specs/signal-interface.md` ≤ 250; `factor-registry.md` ≤ 400; `reliability-score.md` ≤ 400.
    - Update `00_issue.md` 작업 내역; request user approval.

---

## 4. AC mapping

| AC (from issue #76) | Steps | Verification bucket |
|---|---|---|
| AC1 Signal carries expected_return/win_probability/confidence | 3, 13, 21, 22 | unit |
| AC2 Sizer consumes Signal-wins | 18, 23 | integration |
| AC3 Zero ≠ None semantics | 18, 23 | integration |
| AC4 FactorSpec alpha_horizon_bars + bar_interval | 7, 14, 15, 24 | unit |
| AC5 neutralize single API, both methods | 9, 16, 25 | unit + property |
| AC6 Reliability multiplicative | 9, 17, 26 | unit |
| AC7 momo-btc-v2 emits confidence | 19, 27 | integration |
| AC8 Regression JSON | 20, 29, 34 | regression |
| AC9 Patent cites present | 13, 14, 16, 17, 31 | patent + lint |
| AC10 Invariant #6 lint | 31 | invariant-as-lint |
| AC11 Phase 0 hard-block | 1, 28 | gate |
| AC12 .ai.md updated | 30 | docs |
| AC13 Specs ≤ line-count ceiling | 3, 7, 9, 35 | docs |
| AC14 No LLM imports in src exec modules | 31 | lint |

---

## 5. Risks & Mitigations

| R | Risk | Mitigation |
|---|---|---|
| **R1** | Patent cite strings removed in future refactor | `_check_llm_delegation` enforces presence; CI fails on removal |
| **R2** | Signal BC silent break | kw-only Optional extension + `test_signal_backcompat.py` + grep count |
| **R3** | `method="orthogonal"` numerical instability | `cond > 1e10` → warn + OLS fallback + S3 designed-collinearity test |
| **R4** | Reliability gaming (high-Sharpe-high-DD) | Multiplicative gate: DD=20% → reliability=0; explicit unit test |
| **R5** (new) | #84 patent notes not merged → blocks #76 | Phase 0 hard-abort with actionable message + CI `blocked-by:#84` label suggestion |
| **R6** (new) | Single neutralize API → one branch rots | `@parametrize("method", ["ols","orthogonal"])` on every behavioral test |
| **R7** (new) | Invariant #6 keyword-tripwire bypassed via wrapper module | Limitation docstring acknowledges; follow-up issue for AST-based semantic check |

---

## 6. Files created / modified

**Created (11)**
- `docs/specs/signal-interface.md`
- `docs/specs/factor-registry.md`
- `docs/specs/reliability-score.md`
- `src/signals/neutralize.py`
- `scripts/compare_momo_btc_v2_signal_interface.py`
- `tests/test_signal_interface.py`
- `tests/test_signal_backcompat.py`
- `tests/test_signal_sizer_integration.py`
- `tests/test_signals_neutralize.py`
- `tests/test_phase0_gate.py`
- `tests/test_regression_json.py`

**Modified**
- `src/backtest/protocol.py` (Signal 3→6 fields, drop strategy_id)
- `src/signals/registry.py` (FactorSpec + 7 factor registrations)
- `src/portfolio/orchestrator.py` (reliability_score + register_strategy_returns signature)
- `src/risk/sizing.py` (Signal-wins precedence)
- `src/backtest/strategies/momo_btc_v2.py` (_compute_confidence, Signal emission)
- `scripts/check_invariants.py` (`_check_llm_delegation`)
- `tests/test_signals_registry.py`, `tests/test_portfolio_orchestrator.py`, `tests/test_momo_btc_v2.py`, `tests/test_risk_sizing.py`
- `src/{backtest,risk,signals,portfolio}/.ai.md`
- `docs/specs/signals/rsi-divergence.md`, `docs/specs/20-position-sizing.md`
- `docs/background/3[1-3]-patents-*.md` (backlink only, if present)

---

## 7. Out-of-scope (minimal, user-approved)
- Execution-layer reading `alpha_horizon_bars` for TWAP/VWAP routing
- Orchestrator ENB downweighting by `reliability_score`
- KOSPI200 live RankIC validation (paper-level)
- OrderIntent Signal propagation on live-path
- AST-based semantic check for invariant #6 (keyword-tripwire only in this PR; follow-up issue to be filed)
