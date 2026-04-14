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

## 참조
- 프론트매터 가이드: `docs/onboarding/frontmatter-guide.md`
- 스키마 스펙: `docs/schemas/note-schemas.md`
- Obsidian 설정: `docs/onboarding/obsidian-setup.md`
