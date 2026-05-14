#!/usr/bin/env python3
"""전략 완전성 검증 — 새 전략이 운영까지 가는 8개 레이어 모두 등록됐는지 정적 검증.

빠뜨림 방지 목적. `check_invariants.py` 는 문서·온톨로지 무결성만 본다.
본 스크립트는 그 다음 단계 — "추가했는데 안 보임" 증상을 PR 단계에서 막는다.

검증 레이어:
  1. spec frontmatter (docs/specs/strategies/<id>.md) — 필수 필드 + paradigm
  2. 전략 코드 모듈 (src/backtest/strategies/<module>.py) 존재
  3. 단위 테스트 (tests/backtest/test_*.py 중 하나가 모듈/심볼을 참조)
  4. production.yaml 등록 (configs/orchestrator/production.yaml) — active 또는 commented
  5. live-scanner: stop_loss_pct / take_profit_pct 클래스 속성
  6. universe-scan: production.yaml `module` kwarg + spec 본문 pin-date
  7. 5y backtest 결과 frontmatter 기록 (sharpe_bt, mdd_bt, annual_return_bt, backtest_period)
  8. orphan: production.yaml entry 인데 spec 없는 경우

사용법:
  python scripts/check_strategy_completeness.py            # warn 모드 (exit 0)
  python scripts/check_strategy_completeness.py --strict   # error 있으면 exit 1
  python scripts/check_strategy_completeness.py --id <id>  # 특정 전략만
  python scripts/check_strategy_completeness.py --quiet    # warn 숨김, error 만

권장 운영:
  - PR 단계에서는 `--strict` 없이 실행하여 visibility 확보 (CI: continue-on-error: true).
  - 기존 전략의 누락이 정리된 뒤 `--strict` 로 승격하여 머지 차단.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "docs" / "specs" / "strategies"
PRODUCTION_YAML = REPO_ROOT / "configs" / "orchestrator" / "production.yaml"
STRATEGY_CODE_DIR = REPO_ROOT / "src" / "backtest" / "strategies"
TESTS_ROOT = REPO_ROOT / "tests"
TESTS_PREFERRED_DIR = TESTS_ROOT / "backtest"

# frontmatter 필수 필드 (check_invariants.py REQUIRED_FIELDS["strategy"] 와 일치)
COMMON_REQUIRED = ["type", "id", "name", "status", "instruments", "timeframe", "owner", "created"]

# 5y backtest 결과 — null 이면 warn (status≥backtest 일 때만)
BACKTEST_RESULT_FIELDS = ["sharpe_bt", "mdd_bt", "annual_return_bt", "backtest_period"]

# live-scanner 추가 frontmatter
LIVE_SCANNER_FIELDS = ["stop_loss_pct", "take_profit_pct"]

PARADIGMS = {"single-ticker", "universe-scan", "live-scanner"}

# 패러다임 도입 이전(2026-05-06 universe-scan / 2026-05-11 live-scanner) 의
# 5 baseline 전략은 `paradigm:` 필드 미명시를 허용 (CLAUDE.md 참조).
LEGACY_NO_PARADIGM = {
    "momo-btc-v2", "momo-vol-filtered", "meanrev-pairs",
    "breakout-donchian", "momo-kis-v1",
}

# production.yaml 등록·5y 결과 검증을 적용할 status 값.
ACTIVE_STATUSES = {"backtest", "live"}


@dataclass
class StrategyEntry:
    id: str
    cls: str          # production.yaml class 경로
    kwargs: dict
    commented: bool


@dataclass
class Finding:
    level: str        # "error" | "warn"
    strategy_id: str
    layer: str
    message: str


def _id_to_snake(strategy_id: str) -> str:
    return strategy_id.replace("-", "_")


def _load_frontmatter():
    try:
        import frontmatter  # type: ignore
        return frontmatter
    except ImportError:
        return None


def _parse_production_yaml() -> tuple[list[StrategyEntry], list[str]]:
    """active(yaml load) + commented(라인 정규식) 엔트리를 합쳐 반환."""
    import yaml  # type: ignore

    if not PRODUCTION_YAML.exists():
        return [], [f"production.yaml 부재: {PRODUCTION_YAML}"]

    text = PRODUCTION_YAML.read_text(encoding="utf-8")
    entries: list[StrategyEntry] = []
    errors: list[str] = []

    try:
        data = yaml.safe_load(text) or {}
        for entry in (data.get("strategies") or []):
            entries.append(StrategyEntry(
                id=str(entry.get("id", "")),
                cls=str(entry.get("class", "")),
                kwargs=dict(entry.get("kwargs") or {}),
                commented=False,
            ))
    except Exception as exc:
        errors.append(f"production.yaml 파싱 실패: {exc}")

    # 주석 처리된 entry: "# - id: foo" 다음 라인에서 "# class: bar.Bar" 탐색.
    lines = text.splitlines()
    id_re = re.compile(r"^\s*#\s*-\s*id:\s*([\w\-]+)\s*$")
    class_re = re.compile(r"^\s*#\s*class:\s*([\w\.]+)\s*$")
    for i, line in enumerate(lines):
        m = id_re.match(line)
        if not m:
            continue
        sid = m.group(1)
        cls = ""
        # 다음 몇 줄에서 class 탐색 (kwargs 등 사이에 있을 수 있어 최대 5줄).
        for j in range(i + 1, min(i + 6, len(lines))):
            cm = class_re.match(lines[j])
            if cm:
                cls = cm.group(1)
                break
        entries.append(StrategyEntry(id=sid, cls=cls, kwargs={}, commented=True))

    return entries, errors


def _class_path_to_file(class_path: str) -> Path | None:
    """`backtest.strategies.live_x.LiveX` → `src/backtest/strategies/live_x.py`."""
    if not class_path:
        return None
    parts = class_path.split(".")
    if len(parts) < 2:
        return None
    module_parts = parts[:-1]
    rel = Path("src", *module_parts).with_suffix(".py")
    return REPO_ROOT / rel


def _detect_paradigm(fm: dict, strategy_id: str) -> str:
    explicit = fm.get("paradigm")
    if explicit in PARADIGMS:
        return str(explicit)
    tags = [str(t) for t in (fm.get("tags") or [])]
    if "live-scanner" in tags or any(t.startswith("live-scanner") for t in tags):
        return "live-scanner"
    if "pattern:universe-scan" in tags:
        return "universe-scan"
    if strategy_id.startswith("live-"):
        return "live-scanner"
    if strategy_id.startswith("cs-"):
        return "universe-scan"
    return "single-ticker"


def _find_test_file(strategy_id: str, class_path: str | None) -> Path | None:
    """test_{id_snake}.py (tests/** 전체) 또는 모듈/클래스를 참조하는 test_*.py."""
    if not TESTS_ROOT.exists():
        return None
    id_snake = _id_to_snake(strategy_id)
    # 우선 정확한 파일명 매칭 — tests/backtest/ 가 표준 위치, 나머지는 fallback.
    candidates = [TESTS_PREFERRED_DIR / f"test_{id_snake}.py"]
    candidates += list(TESTS_ROOT.rglob(f"test_{id_snake}.py"))
    for c in candidates:
        if c.exists():
            return c
    # fallback: 모듈명 마지막 토큰 또는 클래스명을 포함하는 test 파일 찾기.
    needles: list[str] = []
    if class_path:
        module = class_path.rsplit(".", 1)[0]
        needles.append(module.rsplit(".", 1)[-1])  # e.g., cs_tsmom_kr_daily
        needles.append(class_path.rsplit(".", 1)[-1])  # e.g., CrossSectionalAsyncStrategy
    needles.append(id_snake)
    for tf in sorted(TESTS_ROOT.rglob("test_*.py")):
        try:
            txt = tf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if any(n and n in txt for n in needles):
            return tf
    return None


def _file_contains_all(path: Path, *patterns: str) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return all(p in text for p in patterns)


def _check_spec(
    path: Path,
    fm: dict,
    body: str,
    prod_index: dict[str, StrategyEntry],
) -> list[Finding]:
    findings: list[Finding] = []
    sid = str(fm.get("id") or path.stem)
    status = str(fm.get("status") or "").lower()

    # ── (1) frontmatter 필수 필드 ──────────────────────────────
    for f in COMMON_REQUIRED:
        if f not in fm or fm.get(f) in (None, ""):
            findings.append(Finding("error", sid, "spec",
                f"frontmatter 필수 필드 누락 '{f}'"))

    paradigm = _detect_paradigm(fm, sid)
    if "paradigm" not in fm and sid not in LEGACY_NO_PARADIGM:
        findings.append(Finding("warn", sid, "spec",
            f"frontmatter `paradigm:` 미명시 (추론 결과: {paradigm}). 명시 권장"))

    # ── (2) 전략 코드 모듈 ────────────────────────────────────
    entry = prod_index.get(sid)
    code_path: Path | None = None
    if entry and entry.cls:
        code_path = _class_path_to_file(entry.cls)
    if code_path is None:
        code_path = STRATEGY_CODE_DIR / f"{_id_to_snake(sid)}.py"
    if not code_path.exists():
        findings.append(Finding("error", sid, "code",
            f"전략 코드 미발견: {code_path.relative_to(REPO_ROOT)}"))

    # ── (3) 단위 테스트 ──────────────────────────────────────
    class_path = entry.cls if entry else None
    test_file = _find_test_file(sid, class_path)
    if test_file is None:
        findings.append(Finding("error", sid, "test",
            f"단위 테스트 미발견 (tests/backtest/test_{_id_to_snake(sid)}.py 또는 모듈 참조)"))

    # ── (4) production.yaml 등록 ─────────────────────────────
    if status in ACTIVE_STATUSES:
        if entry is None:
            level = "error" if status == "live" else "warn"
            findings.append(Finding(level, sid, "production-yaml",
                f"production.yaml 미등록 (status={status})"))
        elif status == "live" and entry.commented:
            findings.append(Finding("error", sid, "production-yaml",
                "status=live 인데 production.yaml entry 가 주석 처리됨"))

    # ── (5) live-scanner: 클래스 속성 + frontmatter 필드 ─────
    if paradigm == "live-scanner":
        if code_path and code_path.exists():
            if not _file_contains_all(code_path, "stop_loss_pct", "take_profit_pct"):
                findings.append(Finding("error", sid, "live-scanner",
                    f"live-scanner 코드에 stop_loss_pct/take_profit_pct 속성 부재 ({code_path.name})"))
        for f in LIVE_SCANNER_FIELDS:
            if fm.get(f) in (None, ""):
                findings.append(Finding("warn", sid, "live-scanner",
                    f"frontmatter `{f}` 미설정 (코드값과 일치 여부 사람 확인 필요)"))

    # ── (6) universe-scan: module kwarg + 본문 pin-date ──────
    if paradigm == "universe-scan":
        if entry and not entry.commented and "module" not in (entry.kwargs or {}):
            findings.append(Finding("error", sid, "universe-scan",
                "production.yaml kwargs.module 누락 — importlib resolve 실패 위험"))
        body_lower = body.lower()
        if "pin-date" not in body_lower and "pin_date" not in body_lower:
            findings.append(Finding("warn", sid, "universe-scan",
                "본문에 'pin-date' 명시 부재 (생존편향 disclosure 권장)"))

    # ── (7) 5y backtest 결과 ─────────────────────────────────
    if status in ACTIVE_STATUSES:
        for f in BACKTEST_RESULT_FIELDS:
            if fm.get(f) in (None, ""):
                findings.append(Finding("warn", sid, "backtest-results",
                    f"frontmatter `{f}` null — 5y 백테스트 결과 미기재"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="전략 완전성 검증 (8 레이어)")
    parser.add_argument("--strict", action="store_true",
        help="error 1건이라도 있으면 exit 1")
    parser.add_argument("--id", help="특정 strategy id 만 검사")
    parser.add_argument("--quiet", action="store_true",
        help="warn 숨김, error 만 출력")
    args = parser.parse_args()

    fm_mod = _load_frontmatter()
    if fm_mod is None:
        print("[deps] python-frontmatter 미설치. `pip install python-frontmatter`", file=sys.stderr)
        return 2
    try:
        import yaml  # type: ignore  # noqa: F401
    except ImportError:
        print("[deps] PyYAML 미설치. `pip install pyyaml`", file=sys.stderr)
        return 2

    entries, yaml_errs = _parse_production_yaml()
    for err in yaml_errs:
        print(f"[production-yaml] {err}", file=sys.stderr)
    prod_index = {e.id: e for e in entries if e.id}

    findings: list[Finding] = []
    spec_ids: set[str] = set()

    if not SPECS_DIR.exists():
        print(f"[error] specs 디렉토리 부재: {SPECS_DIR}", file=sys.stderr)
        return 2

    for spec_path in sorted(SPECS_DIR.glob("*.md")):
        if spec_path.name == ".ai.md":
            continue
        try:
            post = fm_mod.load(spec_path)
        except Exception as exc:
            findings.append(Finding("error", spec_path.stem, "spec",
                f"frontmatter 파싱 실패: {exc}"))
            continue
        fm = dict(post.metadata or {})
        if fm.get("type") != "strategy":
            continue
        sid = str(fm.get("id") or spec_path.stem)
        if args.id and sid != args.id:
            continue
        spec_ids.add(sid)
        findings += _check_spec(spec_path, fm, post.content, prod_index)

    # ── (8) orphan: production.yaml 인데 spec 없음 ───────────
    # 주석 처리된 entry 는 "documented opt-in 절차" (예: momo-btc-v2-meta) — orphan 검사 제외.
    if not args.id:
        for entry in entries:
            if entry.commented:
                continue
            if entry.id and entry.id not in spec_ids:
                findings.append(Finding("error", entry.id, "spec",
                    f"production.yaml entry 에 대응 spec 없음 (docs/specs/strategies/{entry.id}.md)"))

    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]

    if not findings:
        print(f"[check_strategy_completeness] 통과 ({len(spec_ids)} 전략)")
        return 0

    print(f"[check_strategy_completeness] 전략 {len(spec_ids)} · error {len(errors)} · warn {len(warns)}")
    by_strat: dict[str, list[Finding]] = {}
    for f in findings:
        by_strat.setdefault(f.strategy_id, []).append(f)

    for sid in sorted(by_strat):
        items = by_strat[sid]
        if args.quiet and not any(x.level == "error" for x in items):
            continue
        print(f"\n  {sid}")
        for f in items:
            if args.quiet and f.level == "warn":
                continue
            tag = "ERROR" if f.level == "error" else "warn "
            print(f"    [{tag}] {f.layer:<18s} {f.message}")

    if args.strict and errors:
        print(f"\n[check_strategy_completeness] --strict: FAIL ({len(errors)} errors)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
