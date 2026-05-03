---
type: runbook
id: test-coverage-sop
name: "테스트 커버리지 SOP — 3계층 전략"
---

# 테스트 커버리지 SOP — 3계층 전략

> 이슈 #132. 월 10% 공격 운용에서 코드 결함 = 즉시 손실. 커버리지는 안전망이다.

---

## 3계층 정의

| 계층 | 마커 | 위치 | 특성 | CI 게이트 |
|------|------|------|------|-----------|
| Layer 1 — 단위 | `@pytest.mark.unit` | `tests/` (루트 레벨) | 빠름, I/O 없음, mock 허용 | 전체 ≥ 70%, 핵심 모듈 ≥ 90% |
| Layer 2 — 통합 | `@pytest.mark.integration` | `tests/integration/` | 네트워크·시크릿 필요, 기본 스킵 | informational (CI non-blocking) |
| Layer 3 — 백테스트 | `@pytest.mark.backtest` | `tests/backtest/` | 결정론적 시뮬레이션, 느릴 수 있음 | backtest 모듈 ≥ 85% |

---

## 임계값 근거

| 임계값 | 대상 | 근거 |
|--------|------|------|
| 전체 ≥ 70% | `src/**` | 현실적 기준선. 레거시 fetcher·CLI 제외 후 달성 가능 |
| 핵심 모듈 ≥ 90% | `src/risk`, `src/portfolio`, `src/brokers`, `src/backtest` | 자금 손실 직결 경로 — 백서 부록 A-3 VC T4 요건 |
| backtest ≥ 85% | `src/backtest` | 전략 검증 로직 — 잘못된 시뮬레이션은 전략 선택 오류로 직결 |
| 회귀 -2pp | PR 단위 | 점진적 하락 조기 탐지 |
| 신규/수정 파일 ≥ 80% | PR diff | 새 코드는 더 높은 기준 적용 |

---

## 마커 사용법

```python
import pytest

@pytest.mark.unit
def test_risk_limit_breach():
    ...

@pytest.mark.backtest
def test_momo_strategy_sharpe():
    ...

# integration 마커는 기존 방식 유지
@pytest.mark.integration
def test_kis_paper_order():
    ...
```

마커 미부착 테스트는 Layer 1(단위) 으로 간주된다. 명시적 부착을 권장하지만 강제하지 않는다.

---

## 로컬 실행

```bash
# 전체 커버리지 측정 (통합·slow 제외)
pytest tests/ -m "not integration and not slow and not e2e_kis_paper" \
  --cov=src --cov-report=term-missing --cov-report=html

# 핵심 모듈만
pytest tests/ -m "not integration and not slow" \
  --cov=src/risk --cov=src/portfolio --cov=src/brokers --cov=src/backtest \
  --cov-fail-under=90

# 백테스트 계층만
pytest tests/backtest/ --cov=src/backtest --cov-report=term-missing

# HTML 리포트 열기
open htmlcov/index.html  # macOS
start htmlcov/index.html  # Windows
```

---

## CI 워크플로우

`.github/workflows/coverage.yml` 가 3개 job 을 실행한다.

1. **unit-coverage** — 단위 계층 + 전체 게이트 + 핵심 모듈 게이트 + PR 코멘트
2. **integration** — 통합 계층 (non-blocking, `continue-on-error: true`)
3. **backtest** — 백테스트 계층 ≥ 85% 게이트
4. **regression-guard** — PR 에서만 실행, base 브랜치 대비 -2pp 초과 하락 시 fail

---

## omit 정책

`pyproject.toml [tool.coverage.run]` 의 `omit` 목록에 추가하는 경우:

- 외부 API fetcher (네트워크 없이 테스트 불가) — 이유를 인라인 주석으로 명시
- CLI `__main__.py` — 실행 진입점
- 자동 생성 마이그레이션 코드

omit 추가 시 PR 설명에 사유를 반드시 기록한다.

---

## 관련 문서

- [[execution-algorithms]] — 실행 알고리즘 명세
- [[kill-switch-runbook]] — 킬스위치 운영 런북
- 백서 부록 A-3 (VC T4·D3), A-7
