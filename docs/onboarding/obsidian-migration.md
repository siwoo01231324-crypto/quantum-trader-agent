---
type: onboarding
id: obsidian-migration
name: "기존 문서 마이그레이션 가이드"
---

# 기존 문서 마이그레이션 가이드

레포에 이미 존재하는 마크다운 문서를 Obsidian 볼트·온톨로지 규약에 맞게 전환하는 절차.

## 1. 대상 판별
모든 md 파일을 전환할 필요는 없다. **타입화된 엔티티**만 프론트매터를 갖는다:

- 전략·시그널·리스크 규칙·종목 → `docs/specs/` 하위
- 백테스트·장애·회고 → `docs/work/` 하위

그 외 리서치·런북·온보딩 문서는 일반 md 로 남겨둔다 (위키링크만 추가하면 지식그래프에 참여).

## 2. 프론트매터 추가 절차

### Step 1. 타입 결정
7개 타입 중 하나 선택 (`strategy`, `signal`, `risk-rule`, `instrument`, `backtest`, `incident`, `postmortem`).

### Step 2. 파일명 ↔ id 정합
- 파일명 slug 를 id 로 쓴다
- 공백·대문자 제거 (`My Strategy.md` → `my-strategy.md`)

### Step 3. 필수 필드 채우기
`docs/onboarding/frontmatter-guide.md` 체크리스트 준수.

### Step 4. 본문 링크 전환
- 상대 경로 링크 → 위키링크
  - `[RSI](../signals/rsi-divergence.md)` → `[[rsi-divergence]]`
- 외부 URL 은 그대로 둔다.

## 3. ID 규칙 재확인

| 타입 | 규칙 | 예시 |
|------|------|------|
| strategy | `{slug}-{ver}` | `momo-btc-v2` |
| signal | `{slug}` | `rsi-divergence` |
| risk-rule | `{slug}-{threshold}` | `max-drawdown-5pct` |
| instrument | ticker 그대로 | `BTCUSDT`, `005930` |
| backtest | `bt-{YYYY-MM-DD}-{strategy-id}` | `bt-2026-04-10-momo-btc-v2` |
| incident | `inc-{YYYY-MM-DD}-{slug}` | `inc-2026-04-12-slippage` |
| postmortem | `pm-{YYYY-MM-DD}` | `pm-2026-04-12` |

## 4. 마이그레이션 워크플로우

```bash
# 1) 대상 파일에 프론트매터 추가
# 2) 파일명을 id 규칙에 맞게 rename (git mv)
git mv docs/specs/strategies/MyStrat.md docs/specs/strategies/my-strat-v1.md

# 3) 본문 링크 전환
# 4) 검증
python scripts/check_invariants.py

# 5) 온톨로지 재생성
python scripts/ontology_sync.py --write

# 6) Obsidian 에서 그래프뷰·Dataview 확인
```

## 5. 참조 무결성 체크

`check_invariants.py` 가 다음을 경고한다 (v1 warn, v2 fail):
- 필수 필드 누락
- id ↔ 파일명 불일치
- 존재하지 않는 `[[id]]` 링크

## 6. 단계적 전환 권고

1. 신규 노트만 규약 적용 (구 문서 손대지 않음)
2. Phase 별로 구 문서 일괄 전환 (Strategy → Signal → Risk → Instrument 순)
3. 모든 전환 완료 후 `check_invariants.py --strict` 로 CI 강제

## 7. 일괄 마이그레이션 스크립트 (Issue #52)

`scripts/migrate_frontmatter.py` 는 `docs/**/*.md` 를 순회하며 경로 규칙에 따라 프론트매터를 자동 주입한다. 이미 프론트매터가 있는 노트는 건드리지 않아 **idempotent** 하다.

```bash
# 1) 변경 계획만 출력 (파일 수정 없음)
python scripts/migrate_frontmatter.py --dry-run

# 2) 실제 적용
python scripts/migrate_frontmatter.py --apply

# 3) 재실행 — 0 changes 여야 정상
python scripts/migrate_frontmatter.py --dry-run

# 4) strict 검증
python scripts/check_invariants.py --strict
```

### 경로 → type 추론 규칙

| 경로 패턴 | 추론 type |
|---|---|
| `docs/specs/strategies/*.md` | `strategy` |
| `docs/specs/signals/*.md` | `signal` |
| `docs/specs/risk-rules/*.md` | `risk-rule` |
| `docs/specs/instruments/*.md` | `instrument` |
| `docs/specs/*.md` (플랫) | `spec-architecture` |
| `docs/runbooks/*.md` | `runbook` |
| `docs/background/*.md` | `research` |
| `docs/onboarding/*.md` | `onboarding` |
| `docs/whitepaper/*.md` | `whitepaper` |
| `docs/work/done/backtests/*.md` | `backtest` |
| `docs/work/incidents/inc-*.md` | `incident` |
| `docs/work/incidents/pm-*.md` | `postmortem` |
| `docs/work/done/<issue>/*.md` | `work-done` |
| `docs/dashboards/*.md` | **스킵** (Dataview) |
| `docs/schemas/*.md` | **스킵** (스키마 자체 문서) |
| `*.ai.md` | **스킵** (디렉토리 설명) |

### 주의
- 스크립트는 최소 필수 필드만 채운다. 도메인 값(예: strategy 의 `instruments`, `timeframe`)은 수동으로 보강해야 의미 있는 온톨로지가 된다.
- `id` 는 파일명(확장자 제외)에서 kebab-case 로 변환. 대문자·숫자만 있는 티커(예: `BTCUSDT`)는 그대로 보존.
- `name` 은 본문 첫 `#` H1 을 추출. H1 이 없으면 파일명 사용.

## 참조
- 프론트매터 가이드: [[frontmatter-guide]]
- 스키마 스펙: `docs/schemas/note-schemas.md` (스키마 파일 자체는 프론트매터 미적용)
- Obsidian 설정: [[obsidian-setup]]

## 관련 노트

- [[getting-started]] — 전체 온보딩 입구
- [[frontmatter-guide]] — 프론트매터 규약
- [[obsidian-setup]] — Obsidian 앱 설정
- [[ontology-primer]] — RDF 온톨로지 기초
- [[shacl-rules]] — SHACL 검증 규칙
- [[mcp-setup]] — 볼트 MCP 서버 연결
