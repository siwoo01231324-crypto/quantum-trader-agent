"""anthropic SDK stub. `--with-llm` 플래그로 활성화 시 호출된다.

SDK 가 설치되지 않았거나 API 키가 없으면 프롬프트 템플릿 그대로를 반환해
호출자가 안전하게 폴백할 수 있게 한다.
"""
from __future__ import annotations

import os
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def call_llm(prompt_name: str, context: dict) -> str:
    """LLM 호출 (옵션). SDK/API 키 부재 시 템플릿 문자열 반환."""
    template = load_prompt(prompt_name)
    rendered = template
    for k, v in context.items():
        rendered = rendered.replace(f"{{{{{k}}}}}", str(v))

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return rendered
    try:
        import anthropic  # type: ignore
    except ImportError:
        return rendered
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
            max_tokens=512,
            messages=[{"role": "user", "content": rendered}],
        )
        # 최신 SDK: resp.content is list of blocks
        blocks = getattr(resp, "content", [])
        texts = [getattr(b, "text", "") for b in blocks]
        return "\n".join(t for t in texts if t) or rendered
    except Exception:  # noqa: BLE001
        return rendered
