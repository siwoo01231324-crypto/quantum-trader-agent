"""Obsidian MCP tool 단위테스트.

MCP SDK 없이 `services.obsidian_mcp.tools` 의 순수 함수만 호출해서
read / list / search / write(dry-run) / sparql / graph_neighbors / append 를 검증한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.obsidian_mcp import tools as t
from services.obsidian_mcp import server as srv

FIXTURE_VAULT = Path(__file__).parent.parent / "fixtures" / "obsidian_vault"


@pytest.fixture
def ctx():
    os.environ.pop("OBSIDIAN_MCP_ALLOW_WRITE", None)
    return t.VaultContext(
        vault_root=FIXTURE_VAULT.resolve(),
        allowed_paths=["specs/strategies/", "specs/signals/", "specs/risk-rules/"],
        write_mode="dry-run",
    )


def test_read_note(ctx):
    r = t.read_note(ctx, "momo-btc-v2")
    assert r["ok"] is True
    assert r["id"] == "momo-btc-v2"
    assert r["frontmatter"]["type"] == "strategy"
    assert r["frontmatter"]["status"] == "paper"
    assert "rsi-divergence" in r["body"]


def test_read_note_missing(ctx):
    r = t.read_note(ctx, "does-not-exist")
    assert r["ok"] is False


def test_list_notes_by_type(ctx):
    r = t.list_notes(ctx, type="strategy")
    assert r["ok"] is True
    assert r["count"] == 1
    assert r["items"][0]["id"] == "momo-btc-v2"

    r2 = t.list_notes(ctx)
    assert r2["count"] == 4  # 전체 샘플 노트 (strategy/signal/risk-rule/instrument)


def test_list_notes_by_tag_and_prefix(ctx):
    r = t.list_notes(ctx, tag="technical")
    assert r["count"] == 1 and r["items"][0]["id"] == "rsi-divergence"

    r2 = t.list_notes(ctx, path_prefix="specs/risk-rules/")
    assert r2["count"] == 1 and r2["items"][0]["id"] == "max-drawdown-5pct"


def test_search_fulltext_and_wikilink(ctx):
    r = t.search(ctx, "momentum")
    assert r["ok"] is True and r["count"] >= 1

    r2 = t.search(ctx, "rsi-divergence")
    ids = {i["id"] for i in r2["items"]}
    assert "rsi-divergence" in ids
    # 위키링크를 가진 momo-btc-v2 도 매칭되어야 함
    assert "momo-btc-v2" in ids
    assert any(i["matched_wikilink"] for i in r2["items"] if i["id"] == "momo-btc-v2")


def test_write_note_dry_run_existing(ctx):
    r = t.write_note(
        ctx,
        "momo-btc-v2",
        {"type": "strategy", "id": "momo-btc-v2", "name": "BTC Momentum v2", "status": "live",
         "instruments": ["BTCUSDT"], "timeframe": "15m", "owner": "siwoo", "created": "2026-04-14"},
        "변경된 본문",
    )
    assert r["ok"] is True
    assert r["dry_run"] is True
    # 파일이 실제로는 변경되지 않았어야 함
    after = t.read_note(ctx, "momo-btc-v2")
    assert after["frontmatter"]["status"] == "paper"


def test_write_note_respects_allowed_paths(ctx):
    ctx.allowed_paths = ["specs/signals/"]  # strategies 경로는 금지
    r = t.write_note(
        ctx,
        "momo-btc-v2",
        {"type": "strategy", "id": "momo-btc-v2"},
        "body",
    )
    assert r["ok"] is False
    assert "allowed_paths" in r["error"] or "allowed_paths" in r


def test_write_note_create_if_missing_dry_run(ctx):
    r = t.write_note(
        ctx,
        "new-strategy-xyz",
        {"type": "strategy", "name": "New", "status": "draft",
         "instruments": ["BTCUSDT"], "timeframe": "1h", "owner": "siwoo",
         "created": "2026-04-14"},
        "draft body",
        create_if_missing=True,
    )
    assert r["ok"] is True and r["dry_run"] is True
    assert "specs/strategies" in r["path"]
    # 실제 파일 생성 안 됐는지 확인
    assert not (FIXTURE_VAULT / "specs" / "strategies" / "new-strategy-xyz.md").exists()


def test_append_section_dry_run(ctx):
    r = t.append_section(ctx, "rsi-divergence", "Notes", "추가 메모")
    assert r["ok"] is True and r["dry_run"] is True
    # 원본 파일에 "Notes" 제목이 추가되지 않았는지
    body = (FIXTURE_VAULT / "specs" / "signals" / "rsi-divergence.md").read_text(encoding="utf-8")
    assert "## Notes" not in body


def test_sparql_query(ctx):
    pytest.importorskip("rdflib")
    r = t.sparql(
        ctx,
        """
        PREFIX qta: <https://siwoo.dev/qta/ontology#>
        SELECT ?s WHERE { ?s a qta:Strategy . }
        """,
    )
    assert r["ok"] is True
    assert r["count"] == 1
    assert "momo-btc-v2" in r["rows"][0]["s"]


def test_graph_neighbors(ctx):
    r = t.graph_neighbors(ctx, "momo-btc-v2", depth=1)
    assert r["ok"] is True
    assert set(r["outlinks"]) >= {"rsi-divergence", "max-drawdown-5pct"}
    # 역방향: rsi-divergence 의 백링크에 momo-btc-v2 포함
    r2 = t.graph_neighbors(ctx, "rsi-divergence", depth=1)
    assert "momo-btc-v2" in r2["backlinks"]


def test_server_dispatch(ctx):
    """server.dispatch 가 tools 를 올바르게 라우팅하는지."""
    r = srv.dispatch(ctx, "list_notes", {"type": "signal"})
    assert r["ok"] is True and r["count"] == 1

    r2 = srv.dispatch(ctx, "read_note", {"id": "max-drawdown-5pct"})
    assert r2["ok"] is True and r2["frontmatter"]["severity"] == "critical"

    r3 = srv.dispatch(ctx, "unknown_tool", {})
    assert r3["ok"] is False


def test_writes_enabled_env_flag(ctx, tmp_path, monkeypatch):
    """OBSIDIAN_MCP_ALLOW_WRITE=1 이면 실쓰기. tmp 볼트로 격리."""
    # 격리된 tmp 볼트로 복사
    vault = tmp_path / "vault"
    (vault / "specs" / "signals").mkdir(parents=True)
    sig = FIXTURE_VAULT / "specs" / "signals" / "rsi-divergence.md"
    (vault / "specs" / "signals" / "rsi-divergence.md").write_text(
        sig.read_text(encoding="utf-8"), encoding="utf-8"
    )
    local_ctx = t.VaultContext(
        vault_root=vault.resolve(),
        allowed_paths=["specs/"],
        write_mode="dry-run",
    )
    monkeypatch.setenv("OBSIDIAN_MCP_ALLOW_WRITE", "1")
    assert local_ctx.writes_enabled is True

    r = t.append_section(local_ctx, "rsi-divergence", "ExtraNotes", "hello")
    assert r["ok"] is True and r["dry_run"] is False
    after = (vault / "specs" / "signals" / "rsi-divergence.md").read_text(encoding="utf-8")
    assert "## ExtraNotes" in after
