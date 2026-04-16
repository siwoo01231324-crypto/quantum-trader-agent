"""Bootstrap GraphDB: create qta repo and load T-Box TTL."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from scripts.graphdb_client import (
        create_repo,
        repo_exists,
        upload_ttl,
        wait_for_ready,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from graphdb_client import (  # type: ignore[no-redef]  # noqa: E402
        create_repo,
        repo_exists,
        upload_ttl,
        wait_for_ready,
    )

_REPO_CONFIG = Path(__file__).parent.parent / "infra" / "graphdb" / "repo-config.ttl"


def bootstrap(
    endpoint: str,
    repo: str,
    tbox: Path,
    timeout: int = 90,
) -> int:
    """Run bootstrap sequence. Returns 0 on success, 1 on failure."""
    try:
        wait_for_ready(endpoint, timeout=timeout)

        if not repo_exists(endpoint, repo):
            create_repo(endpoint, repo, _REPO_CONFIG)

        upload_ttl(endpoint, repo, tbox)

        import requests as _req
        r = _req.get(f"{endpoint}/repositories/{repo}/size", timeout=5)
        r.raise_for_status()
        size = r.text.strip()
        print(f"Repository '{repo}' ready. Triple count: {size}")
        return 0
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap GraphDB repository")
    parser.add_argument("--endpoint", default="http://localhost:7200")
    parser.add_argument("--repo", default="qta")
    parser.add_argument("--tbox", default="docs/ontology/trading.ttl")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    tbox = Path(args.tbox)
    if not tbox.is_absolute():
        tbox = Path(__file__).parent.parent / tbox

    sys.exit(bootstrap(args.endpoint, args.repo, tbox, args.timeout))


if __name__ == "__main__":
    main()
