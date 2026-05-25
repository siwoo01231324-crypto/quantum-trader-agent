"""Patch notes data loader + HTML renderer (2026-05-26 v0.6.0).

Reads ``docs/patch-notes/index.yaml`` and renders a chronological HTML page
served at ``/patch-notes``. Schema is documented at the top of the yaml file.

Failure modes:
    - File missing → renders an empty "준비 중" placeholder, not 500.
    - YAML parse error → renders the error inline so the operator sees it
      without grepping logs.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import yaml

_TAG_PALETTE: dict[str, tuple[str, str]] = {
    # tag → (bg-rgba, fg-color)
    "feature": ("rgba(14,203,129,.16)", "#0ecb81"),
    "fix":     ("rgba(246,70,93,.16)",  "#f6465d"),
    "perf":    ("rgba(24,144,255,.16)", "#4da6ff"),
    "chore":   ("rgba(91,95,100,.18)",  "#b7bdc6"),
    "docs":    ("rgba(153,102,255,.16)","#b07ef4"),
    "safety":  ("rgba(240,165,0,.18)",  "#f0a500"),
}


def _project_root() -> Path:
    # src/dashboard/patch_notes.py → repo root
    return Path(__file__).resolve().parents[2]


def load_patch_notes(path: Path | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Return (versions_list, error_message_or_None).

    On missing file: ([], None). On parse error: ([], str(e)).
    """
    if path is None:
        path = _project_root() / "docs" / "patch-notes" / "index.yaml"
    if not path.exists():
        return [], None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001 — surface to operator UI
        return [], f"{type(exc).__name__}: {exc}"
    raw_versions = data.get("versions") or []
    if not isinstance(raw_versions, list):
        return [], "index.yaml: top-level `versions` must be a list"
    out: list[dict[str, Any]] = []
    for v in raw_versions:
        if not isinstance(v, dict):
            continue
        out.append({
            "version": str(v.get("version", "")),
            "date":    str(v.get("date", "")),
            "title":   str(v.get("title", "")),
            "tags":    [str(t) for t in (v.get("tags") or [])],
            "summary": str(v.get("summary", "")).strip(),
            "items":   [str(i) for i in (v.get("items") or [])],
            "refs":    [str(r) for r in (v.get("refs") or [])],
        })
    return out, None


def _tag_chip(tag: str) -> str:
    bg, fg = _TAG_PALETTE.get(tag, ("rgba(91,95,100,.18)", "#b7bdc6"))
    return (
        f'<span class="pn-tag" style="background:{bg};color:{fg}">'
        f'{html.escape(tag)}</span>'
    )


def _entry_html(v: dict[str, Any]) -> str:
    ver = html.escape(v["version"])
    date = html.escape(v["date"])
    title = html.escape(v["title"])
    tags_html = " ".join(_tag_chip(t) for t in v["tags"])
    summary = html.escape(v["summary"])
    items_html = "".join(
        f'<li>{html.escape(it)}</li>' for it in v["items"]
    )
    refs_html = ""
    if v["refs"]:
        refs_html = (
            '<div class="pn-refs"><span class="pn-refs-label">참조</span>'
            + "".join(
                f'<code class="pn-ref">{html.escape(r)}</code>'
                for r in v["refs"]
            )
            + "</div>"
        )
    return f"""
    <article class="pn-card">
      <header class="pn-head">
        <span class="pn-ver">{ver}</span>
        <span class="pn-date">{date}</span>
        <span class="pn-tags">{tags_html}</span>
      </header>
      <h2 class="pn-title">{title}</h2>
      <p class="pn-summary">{summary}</p>
      <ul class="pn-items">{items_html}</ul>
      {refs_html}
    </article>"""


def render_patch_notes_page(
    versions: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> str:
    if versions is None:
        versions, error = load_patch_notes()
    if error:
        body = (
            '<div class="pn-empty pn-err">⚠️ '
            f'patch-notes/index.yaml 파싱 실패: <code>{html.escape(error)}</code>'
            "</div>"
        )
    elif not versions:
        body = (
            '<div class="pn-empty">'
            "패치노트가 아직 없습니다. "
            "<code>docs/patch-notes/index.yaml</code> 의 <code>versions:</code> "
            "리스트에 entry 를 추가하세요."
            "</div>"
        )
    else:
        body = "".join(_entry_html(v) for v in versions)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>QTA — 패치노트</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0b0e11;--surface:#161a1e;--surface2:#1e2329;--border:#2b3139;
  --text:#eaecef;--text2:#b7bdc6;--text3:#848e9c;--accent:#f0b90b;
  --mono:'IBM Plex Mono','Consolas',monospace;
  --sans:'IBM Plex Sans KR','Segoe UI',sans-serif;
}}
body{{font-family:var(--sans);background:var(--bg);color:var(--text);
  padding:14px;font-size:13px}}
h1{{font-size:1.1rem;margin-bottom:10px;font-weight:600}}
.nav{{margin-bottom:18px}}
.nav a{{color:var(--text2);text-decoration:none;margin-right:8px;font-size:.8rem;
  background:var(--surface);padding:6px 12px;border-radius:4px;
  border:1px solid var(--border)}}
.nav a:hover{{color:var(--text);background:var(--surface2)}}
.pn-empty{{padding:30px;text-align:center;color:var(--text3);
  background:var(--surface);border-radius:6px;border:1px solid var(--border)}}
.pn-empty code{{background:var(--bg);padding:2px 6px;border-radius:3px;
  color:var(--accent);font-family:var(--mono)}}
.pn-err{{border-color:#f6465d;color:#f6465d}}
.pn-card{{
  background:var(--surface);border:1px solid var(--border);border-radius:6px;
  padding:18px 20px;margin-bottom:14px;
}}
.pn-head{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  margin-bottom:6px;font-size:.78rem;color:var(--text3)}}
.pn-ver{{font-family:var(--mono);color:var(--accent);font-weight:700;
  font-size:.95rem;letter-spacing:.5px}}
.pn-date{{font-family:var(--mono)}}
.pn-tags{{display:flex;gap:5px;margin-left:auto;flex-wrap:wrap}}
.pn-tag{{font-family:var(--mono);font-size:.65rem;padding:2px 8px;
  border-radius:3px;font-weight:600;letter-spacing:.4px;text-transform:uppercase}}
.pn-title{{font-size:1rem;font-weight:600;margin-bottom:8px;line-height:1.4}}
.pn-summary{{color:var(--text2);line-height:1.6;margin-bottom:12px;
  padding-left:0;font-size:.85rem}}
.pn-items{{list-style:disc;margin-left:22px;color:var(--text);font-size:.82rem}}
.pn-items li{{margin-bottom:5px;line-height:1.55}}
.pn-refs{{margin-top:12px;padding-top:10px;border-top:1px dashed var(--border);
  display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.pn-refs-label{{font-size:.7rem;color:var(--text3);letter-spacing:.5px;
  text-transform:uppercase}}
.pn-ref{{font-family:var(--mono);font-size:.7rem;background:var(--bg);
  padding:2px 7px;border-radius:3px;color:var(--text2);
  border:1px solid var(--border)}}
.pn-footer{{margin-top:20px;font-size:.7rem;color:var(--text3);text-align:center;
  font-family:var(--mono)}}
.pn-footer code{{color:var(--accent)}}
</style>
</head>
<body>
<h1>📋 QTA 패치노트</h1>
<div class="nav">
  <a href="/">← 대시보드</a>
  <a href="/strategies">전략 카탈로그</a>
  <a href="/signals">신호 목록</a>
  <a href="/shadow_runs">Shadow Runs</a>
</div>
{body}
<div class="pn-footer">
  엔트리 추가: <code>docs/patch-notes/index.yaml</code> 의
  <code>versions:</code> 리스트 맨 앞에 append
</div>
</body>
</html>"""
