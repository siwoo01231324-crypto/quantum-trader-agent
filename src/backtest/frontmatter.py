"""Update strategy frontmatter with backtest results."""
from __future__ import annotations

import re
from pathlib import Path

import yaml


def update_strategy_frontmatter(
    strategy_id: str,
    metrics: dict,
    docs_dir: Path = Path("docs"),
) -> Path:
    """Update the sharpe_bt field in the strategy's YAML frontmatter.

    Reads docs/specs/strategies/{strategy_id}.md, updates sharpe_bt,
    writes back. Returns the file path.
    """
    path = Path(docs_dir) / "specs" / "strategies" / f"{strategy_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Strategy file not found: {path}")

    text = path.read_text(encoding="utf-8")

    # Split frontmatter from body
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"No YAML frontmatter found in {path}")

    fm_text = match.group(1)
    body = match.group(2)

    fm = yaml.safe_load(fm_text)
    fm["sharpe_bt"] = round(float(metrics.get("sharpe", 0.0)), 4)

    new_fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    new_text = f"---\n{new_fm_text}---\n{body}"

    path.write_text(new_text, encoding="utf-8")
    return path
