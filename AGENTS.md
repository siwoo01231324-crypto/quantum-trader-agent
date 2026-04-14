# quantum-trader-agent — AGENTS.md

> 이 파일은 레포 전체의 목차다. 백과사전이 아니다.
> 규칙·불변식은 `CLAUDE.md` 참조. 각 디렉토리 상세는 해당 `.ai.md` 참조.

---

## 레포 구조

```
quantum-trader-agent/
├── AGENTS.md                        ← 지금 이 파일 (목차)
├── CLAUDE.md                        ← 불변식·규칙·작업 흐름
├── README.md                        ← 프로젝트 소개
├── setup.sh                         ← 템플릿 초기화 스크립트 (1회 실행)
├── docker-compose.yml               ← Prometheus + Grafana + Loki 관측성 스택
│
├── .github/
│   ├── workflows/                   GitHub Actions (project-automation.yml · ci.yml)
│   └── ISSUE_TEMPLATE/              이슈 템플릿 (feat · chore · bug)
│
├── .claude/
│   ├── agents/                      커스텀 에이전트 정의
│   ├── commands/                    슬래시 커맨드 (bi · si · plan · fi · ci · drop · ri · update-changelog)
│   └── hooks/                       Git 훅 스크립트 (secret-filter 등)
│
├── docs/
│   ├── specs/                       기능 명세 + AC (이슈와 1:1 매핑)
│   │   ├── data-lake-schema.md                  (#20)
│   │   ├── risk-rule-dsl.md                     (#24)
│   │   ├── execution-algorithms.md              (#25)
│   │   ├── observability.md                     (#26)
│   │   ├── kill-switch-dr.md                    (#27)
│   │   └── tax-automation.md                    (#28)
│   ├── background/                  Phase 0-1 리서치 + 외부 참고 자료
│   │   ├── 01-research-plan.md                  (#1 메타)
│   │   ├── 02-terms-quant-vs-quantum.md         (#2)
│   │   ├── 03-what-is-quantum-trading.md        (#3)
│   │   ├── 04-what-is-algo-trading.md           (#4)
│   │   ├── 05-positioning.md                    (#5 정체성 확정)
│   │   ├── 06-why-quantum-now.md                (#6)
│   │   ├── 07-market-microstructure-basics.md   (#7)
│   │   ├── 08-strategy-paradigms.md             (#8)
│   │   ├── 09-system-components.md              (#9)
│   │   ├── 10-broker-api-comparison.md          (#19 KIS 1차 / LS 2차)
│   │   ├── 11-backtest-engine-selection.md      (#21 Zipline-reloaded 선정)
│   │   ├── 12-validation-protocol.md            (#22 walk-forward / purged K-fold)
│   │   ├── 13-feature-alpha-catalog.md          (#23)
│   │   ├── 14-quantum-poc-design.md             (#29 Phase 4 옵션)
│   │   ├── 15-llm-agent-layer.md                (#30)
│   │   └── ref-snowflake-alt-data-trading-demo.md  (#31 외부 참고)
│   ├── onboarding/                  환경 설정·기여 가이드
│   ├── runbooks/                    운영 런북
│   │   └── kill-switch-runbook.md               (#27)
│   └── work/                        이슈별 작업 내역
│       ├── active/                  진행 중
│       └── done/                    완료
│
├── src/                             ← 애플리케이션 소스
│   ├── data_lake/                   Parquet 스키마·저장소 (#20)
│   ├── risk/                        리스크 룰 DSL 파서·평가기 (#24)
│   ├── execution/                   주문 실행 알고리즘 (#25)
│   │   ├── base.py                  ExecutionAlgorithm 프로토콜
│   │   ├── twap.py · vwap.py
│   │   ├── limit.py · market.py
│   │   └── krx_handler.py           KRX 단일가 매매 구간 핸들러
│   ├── observability/               Prometheus 메트릭 (#26)
│   ├── ops/                         Kill Switch · DR · 트리거 (#27)
│   │   ├── kill_switch.py · triggers.py · cli.py
│   └── tax/                         KR 개인 세법 자동화 (#28)
│       ├── calculator.py · reporter.py
│
├── tests/                           pytest 스위트
│
├── policies/                        리스크 정책 YAML (#24)
│   ├── conservative.yaml
│   ├── neutral.yaml
│   └── aggressive.yaml
│
├── grafana/                         Grafana 대시보드 JSON (#26)
│   └── dashboards/                  system · strategy · execution
├── prometheus/                      Prometheus 설정 (#26)
├── loki/                            Loki 라벨 규약 (#26)
│
└── scripts/                         유틸리티 스크립트
    ├── check_invariants.py          아키텍처 불변식 검증
    └── check_forbidden_files.py     금지 파일 검사
```

---

## 핵심 문서 링크

- 기능 명세 + AC → `docs/specs/`
- 배경·리서치·외부 참고 → `docs/background/`
- 작업 내역 → `docs/work/active/` · `docs/work/done/`
- 운영 런북 → `docs/runbooks/`
- 온보딩 → `docs/onboarding/getting-started.md`

## 프로젝트 정체성

본 프로젝트는 한국 개인투자자가 국내 증권사 Open API로 운용하는 **저빈도(LFT) 규칙기반·퀀트 팩터 자동매매 에이전트**이며, 프로젝트명의 "quantum"은 브랜딩 표기일 뿐 양자컴퓨팅 기술과는 무관하다. (이슈 #5 결론)

---

## 지식볼트 · 온톨로지 (#47)

`docs/` 는 Obsidian 볼트로 열린다. 프론트매터 기반 노트가 RDF 온톨로지로 동기화된다.

### 볼트 구조
- `docs/.obsidian/` — Obsidian 설정 (app / core-plugins / community-plugins / graph)
- `docs/schemas/note-schemas.md` — 공식 프론트매터 규약 (7개 타입)
- `docs/specs/{strategies,signals,risk-rules,instruments}/` — 타입별 인스턴스 노트
- `docs/ontology/trading.ttl` — OWL 온톨로지 (T-Box)
- `docs/ontology/instances.ttl` — `ontology_sync.py --write` 로 생성 (A-Box)
- `docs/ontology/queries/*.rq` — SPARQL 쿼리 프리셋
- `docs/dashboards/*.md` — Dataview 대시보드

### 필수 플러그인
- Dataview, Graph Analysis (+ 선택: Templater)

### 핵심 스크립트
- `scripts/check_invariants.py` — 프론트매터 스키마 · 위키링크 · TTL 파싱 검증 (v1 warn)
- `scripts/ontology_sync.py` — 프론트매터 → `instances.ttl` 동기화 (`--check` / `--write`)

### 온보딩 문서
- `docs/onboarding/obsidian-setup.md` — 볼트 오픈·플러그인 설치
- `docs/onboarding/frontmatter-guide.md` — 7개 타입 작성법
- `docs/onboarding/ontology-primer.md` — Turtle · SPARQL 기초
- `docs/onboarding/obsidian-migration.md` — 기존 문서 이관
