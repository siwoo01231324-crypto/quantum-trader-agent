# {{PROJECT_NAME}} AGENTS.md

> 이 파일은 레포 전체의 목차다. 백과사전이 아니다.
> 규칙·불변식은 `CLAUDE.md` 참조. 각 디렉토리 상세는 해당 `.ai.md` 참조.

---

## 레포 구조

> ⚠️ 프로젝트 구조에 맞게 직접 수정하세요.

```
{{PROJECT_NAME}}/
├── AGENTS.md               ← 지금 이 파일 (목차)
├── CLAUDE.md               ← 불변식·규칙·작업 흐름
├── README.md               ← 프로젝트 소개
├── setup.sh                ← 템플릿 초기화 스크립트 (1회 실행)
├── .github/
│   └── workflows/          GitHub Actions (project-automation.yml)
├── .claude/
│   ├── agents/             커스텀 에이전트 정의
│   ├── commands/           슬래시 커맨드 (bi, si, fi, ci 등)
│   └── hooks/              Git 훅 스크립트
├── docs/                   ← 프로젝트 문서 (SOT)
│   ├── specs/              기능 명세 + AC
│   ├── onboarding/         환경 설정·기여 가이드
│   └── work/               이슈별 작업 내역
│       ├── active/         진행 중인 작업
│       └── done/           완료된 작업
└── scripts/                ← 유틸리티 스크립트
    ├── check_invariants.py 아키텍처 불변식 검증
    └── check_forbidden_files.py 금지 파일 검사
```

---

## 핵심 문서 링크

- 기능 명세 + AC → `docs/specs/`
- 작업 내역 → `docs/work/active/`
- 온보딩 → `docs/onboarding/getting-started.md`
