"""Promote a trained MetaLabeler version to 'latest' via pointer.json alias."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MODELS_DIR = Path(__file__).parent.parent / "models"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def promote(strategy: str, version: str) -> Path:
    version_dir = MODELS_DIR / strategy / version
    if not (version_dir / "model.lgbm").exists():
        print(
            f"ERROR: model.lgbm not found in {version_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    latest_dir = MODELS_DIR / strategy / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    pointer = {
        "active": version,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
    }
    pointer_path = latest_dir / "pointer.json"
    pointer_path.write_text(json.dumps(pointer, indent=2))
    print(f"Promoted {strategy}/{version} → {pointer_path}")
    return pointer_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote a MetaLabeler version to latest via pointer.json alias."
    )
    parser.add_argument("--strategy", required=True, help="Strategy ID, e.g. momo-btc-v2")
    parser.add_argument("--version", required=True, help="Version dir name, e.g. 20260424-191615")
    args = parser.parse_args()
    promote(args.strategy, args.version)


if __name__ == "__main__":
    main()
