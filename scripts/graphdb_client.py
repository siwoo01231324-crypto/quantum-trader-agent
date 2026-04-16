"""Shared HTTP helpers for Ontotext GraphDB REST API."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Union

import requests

_TIMEOUT = 5


def wait_for_ready(endpoint: str, timeout: int = 60) -> None:
    """Poll GET /rest/repositories until 200 or timeout."""
    deadline = time.time() + timeout
    while True:
        try:
            r = requests.get(f"{endpoint}/rest/repositories", timeout=_TIMEOUT)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        if time.time() >= deadline:
            raise TimeoutError(f"GraphDB at {endpoint} not ready within {timeout}s")
        time.sleep(2)


def repo_exists(endpoint: str, repo: str) -> bool:
    """Return True if repository exists (200), False if not found (404)."""
    r = requests.get(f"{endpoint}/rest/repositories/{repo}", timeout=_TIMEOUT)
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    r.raise_for_status()
    return False


def create_repo(endpoint: str, repo: str, config_path: Union[str, Path]) -> None:
    """POST /rest/repositories with repo-config.ttl as multipart form data."""
    config_path = Path(config_path)
    with config_path.open("rb") as fh:
        r = requests.post(
            f"{endpoint}/rest/repositories",
            files={"config": (config_path.name, fh, "text/turtle")},
            timeout=60,
        )
    if r.status_code >= 400:
        raise requests.HTTPError(
            f"{r.status_code} Client Error: {r.text.strip()} for url: {r.url}",
            response=r,
        )


def upload_ttl(
    endpoint: str,
    repo: str,
    ttl_bytes_or_path: Union[bytes, str, Path],
    context: Optional[str] = None,
) -> None:
    """POST Turtle data to /repositories/{repo}/statements."""
    if isinstance(ttl_bytes_or_path, (str, Path)):
        data = Path(ttl_bytes_or_path).read_bytes()
    else:
        data = ttl_bytes_or_path

    params = {}
    if context:
        params["context"] = context

    r = requests.post(
        f"{endpoint}/repositories/{repo}/statements",
        data=data,
        params=params,
        headers={"Content-Type": "text/turtle"},
        timeout=30,
    )
    r.raise_for_status()


def sparql_update(endpoint: str, repo: str, update_query: str) -> None:
    """POST a SPARQL Update to /repositories/{repo}/statements."""
    r = requests.post(
        f"{endpoint}/repositories/{repo}/statements",
        data=update_query.encode(),
        headers={"Content-Type": "application/sparql-update"},
        timeout=30,
    )
    r.raise_for_status()
