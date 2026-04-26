---
type: guide
id: models-readme
---

# models/ — MetaLabeler Artifact Store

This directory holds trained MetaLabeler artifacts. Binary files are excluded from git (see `.gitignore` policy below).

## Directory Layout

```
models/
  {strategy}/
    {version}/          # e.g. 20260424-191615/
      model.lgbm        # LightGBM booster (binary, git-ignored)
      manifest.json     # metadata: trained_at, git_sha, feature_names, config
    latest/
      pointer.json      # alias: {"active": "{version}", "promoted_at": "...", "git_sha": "..."}
```

## Alias Mechanism

`MetaLabeler.load("models/momo-btc-v2/latest")` resolves the alias automatically:

1. Detects `pointer.json` present and `model.lgbm` absent → alias directory.
2. Reads `pointer["active"]` (e.g. `"20260424-191615"`).
3. Loads from `models/momo-btc-v2/20260424-191615/` directly (1-hop only).

**Constraints:**
- Only **1-hop** alias chains are permitted. `alias → alias` raises `ValueError`.
- A pointer pointing to itself raises `ValueError`.
- A missing `active` key raises `KeyError`.
- A missing target directory raises `FileNotFoundError`.

## `.gitignore` Policy

```
models/**/*.lgbm
models/**/*.pkl
models/**/*.parquet
models/**/*.csv
```

`pointer.json` and `manifest.json` are committed so the alias chain is version-controlled without committing binary weights.

## Promote Procedure

Use `scripts/promote_metalabeler.py` to activate a new version:

```bash
python scripts/promote_metalabeler.py --strategy momo-btc-v2 --version 20260424-191615
```

This writes `models/momo-btc-v2/latest/pointer.json` with:
- `active`: the version directory name
- `promoted_at`: UTC ISO-8601 timestamp
- `git_sha`: current HEAD SHA

Commit `pointer.json` after promoting to record the activation in git history.

## Rollback

To roll back, re-run `promote_metalabeler.py` with the previous version name, or manually edit `pointer.json` and commit.
