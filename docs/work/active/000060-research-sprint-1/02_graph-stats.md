---
type: work-done
id: 02_graph-stats
name: "#60 볼트 그래프 연결성 측정"
status: active
---

# #60 볼트 그래프 연결성 측정

## 측정 기준

- 대상: `docs/` 하위 `.md` (단, `.ai.md` · `docs/dashboards/` · `docs/schemas/` · `docs/ontology/` · `docs/.obsidian/` 제외)
- 카운팅 단위: **파일 경로** (work-done 은 `00_issue` / `01_plan` 등 동일 id 가 여러 폴더에 존재하므로 파일 단위가 정확)
- 링크 추출: 본문에서 코드블록 제거 후 `[[id]]` 정규식. 대상 id 가 실존하는 경우만 유효 링크로 간주
- 측정 스크립트는 이 문서 말미의 "재현 스크립트" 참조

## 수치

| 시점 | 전체 경로 | outgoing 있음 | incoming 있음 | 완전 고립 |
|------|-----------|---------------|----------------|-----------|
| 2026-04-16 #60 시작 전 | 68 | 4 (5.9%) | 4 (5.9%) | 64 (94.1%) |
| 2026-04-16 #60 종료 | 69 | 53 (76.8%) | 35 (50.7%) | **3 (4.3%)** |

AC 목표 40% 이하 대비 **대폭 달성** (9.3배 초과).

## 타입별 최종 고립 수

| type | 총 | 고립 |
|------|----|------|
| instrument | 1 | 0 |
| onboarding | 7 | 1 |
| research | 18 | 0 |
| risk-rule | 1 | 0 |
| runbook | 1 | 0 |
| signal | 1 | 0 |
| spec-architecture | 6 | 0 |
| strategy | 1 | 0 |
| work-done | 33 | 2 (현재 스프린트 자기 자신) |

## 남은 고립 (의도됨)

- `docs/work/active/000060-research-sprint-1/` 하위 자기 자신 참조 루프는 그래프에서 자연스러운 고립처럼 보일 수 있으나, 본 폴더 내 노트들끼리는 서로 위키링크 연결됨 (`01_plan` ↔ `00_issue` ↔ `02_graph-stats`).

## 재현 스크립트

```python
import re, frontmatter
from pathlib import Path
from collections import defaultdict

docs = Path('docs')
notes = {}
all_paths = []
for p in docs.rglob('*.md'):
    if (p.name == '.ai.md' or '.obsidian' in p.parts
        or 'dashboards' in p.parts or 'schemas' in p.parts
        or 'ontology' in p.parts):
        continue
    post = frontmatter.load(p)
    nid = post.metadata.get('id') or p.stem
    notes[nid] = p
    all_paths.append((p, post.metadata))

links_out = defaultdict(set)
link_pat = re.compile(r'\[\[([^\]\|#]+?)(?:\|[^\]]+)?\]\]')
for p, meta in all_paths:
    nid = meta.get('id') or p.stem
    text = p.read_text(encoding='utf-8')
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]*`', '', text)
    for m in link_pat.finditer(text):
        tgt = m.group(1).strip()
        if tgt in notes and tgt != nid:
            links_out[(p, nid)].add(tgt)

inc = defaultdict(set)
for (p, src), tgts in links_out.items():
    for t in tgts:
        inc[t].add(src)

total = len(all_paths)
iso = sum(1 for (p, meta) in all_paths
         if not links_out[(p, meta.get('id') or p.stem)]
         and not inc[meta.get('id') or p.stem])
print(f'{iso}/{total} isolated ({100*iso/total:.1f}%)')
```

## 관련 노트

- [[00_issue]] — 본 스프린트 이슈 본문
- [[01_plan]] — 본 스프린트 플랜
- [[19-portfolio-risk]] — 본 스프린트 산출물 1
- [[20-position-sizing]] — 본 스프린트 산출물 2
