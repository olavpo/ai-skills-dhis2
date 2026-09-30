---
name: dhis2-metadata
description: "Use whenever the user works with DHIS2 metadata in bulk, on disk or from a live instance — exporting or downloading configuration (especially from a production server), anonymizing, redacting or minimizing metadata for sharing or for AI/LLM context, splitting or transforming metadata.json, comparing or validating configuration, importing or copying metadata between instances, or analysing import/export errors. Triggers on metadata.json, schemas.json, dataElements/indicators/programs/orgUnits/categoryCombos, PII in metadata, sharing/translations stripping, import reports (E5002, E4000, E1127), dummy-data generation for a program or dataset (a full comparative PoC is dhis2-prototyping), and program documentation. Use it even without the word 'metadata': 'what does this program look like', 'why did the import fail', 'get me the config from prod', 'make this safe to share'. Authoring indicator expressions themselves is dhis2-indicators."
---

## Overview

Two modes, same three scripts:

1. **Prepare metadata for analysis** — export (or start from a file), strip what is noise or sensitive, split per type so the model can `Read` one type at a time. For investigating configuration, program-indicator work, dummy data, documentation, feeding AI.
2. **Transfer metadata between instances** — fetch a slice, transform, import per type in dependency order.

Read this file fully before running anything; the safety notes matter.

## Scripts

`scripts/` next to this file; plain `python`, only dependency `requests` (`pip install requests`). Network scripts take `--url`/`--auth` or fall back to `$DHIS2_BASE_URL` and `$DHIS2_AUTH` (or `token:$DHIS2_API_TOKEN`). `--help` has the full CLI.

| Script | Purpose |
|---|---|
| `fetch_metadata.py` | Export `metadata.json`/`schemas.json` from a live instance: one bulk request, or per type with `--types`/`--all-types`, filters, pagination and throttling for production. Accepts the transform flags below and applies them server-side first (`fields=:owner,!sharing,...`), then locally; `--split` writes per-type files too. |
| `transform_metadata.py` | Same transforms on a metadata file already on disk (`--split-dir` per type, or `--out` single file). Importable module shared with fetch. |
| `import_metadata.py` | POST a directory of per-type files to `/api/metadata` in dependency order; `--schemas` derives order and circular groups (preferred); chunking, async polling, `--resume` for huge loads. |

## Before exporting: decide with the user

Nothing sensitive is removed silently, so ask which of these apply — most users have not thought about it and will want some of them:

| Decision | Flag | Why it matters |
|---|---|---|
| Real names, emails, phones, credentials | `--anonymize` | Users, org unit contacts, and every `createdBy`/`lastUpdatedBy` carry them |
| Sharing settings | `--unshare` | Sharing IDs rarely align across instances; noise for analysis |
| Translations | `--delocalize` | Bulk of the bytes on multilingual instances; needed only for locale work |
| Org unit names | `--redact-ou-names` | Facility/school names can identify people or places |
| Org unit coordinates | `--drop-coordinates` | GPS points are location data |
| Include `users` at all? | `--exclude users` | Default to excluding for anything cross-instance (see safety) |

`createdBy`/`lastUpdatedBy` are excluded by default in fetch: they are re-stamped on import and are the main PII leak (creator's name and username on every object). Pass explicit `--fields` to keep them.

## Transform flags

All flags compose. In `fetch_metadata.py` each also becomes server-side `!field` exclusions, so removed data never leaves the server; the local pass still runs because field filtering does not reach embedded objects (legends inside legendSets, members inside userGroups).

- **`--anonymize`** — strips PII/credential fields everywhere, reduces user references to `{id}`, gives top-level users placeholder `firstName`/`surname`/`username` so they stay importable. Cannot see PII stored in custom `attributeValues` — check those with the user.
- **`--unshare`**, **`--delocalize`** — remove sharing/access fields, remove `translations`.
- **`--minimize`** — keep only schema `owner && persisted` properties minus bookkeeping, reduce references to `{id}`. Needs the version-matching `schemas.json`. Analysis only: on a `:owner` export it saves ~10–15% (embedded objects carry the noise), and it can strip a rarely-used owned property that a round-trip needs.
- **`--redact-ou-names`**, **`--drop-coordinates`** — org unit placeholders / geometry removal.

Defaults: AI consumption `--anonymize --minimize --unshare --delocalize`; instance transfer `--unshare --delocalize` (never `--minimize`).

## Workflow snapshots

```bash
# Prep for AI, from a live instance (bulk request: fine on small/throwaway instances)
python scripts/fetch_metadata.py --metadata --schemas --split --out ./export \
    --anonymize --minimize --unshare --delocalize
# → Read ./export/split/programs.json etc., never the whole metadata.json

# Same from production: per type, paginated, throttled, no users
python scripts/fetch_metadata.py --all-types --page-size 200 --delay 0.3 --schemas \
    --exclude users --anonymize --unshare --delocalize --split --out ./export

# Metadata already on disk
python scripts/transform_metadata.py ./metadata.json --schemas references/schemas-v42.json \
    --anonymize --minimize --unshare --delocalize --split-dir ./split

# Copy a slice between instances
python scripts/fetch_metadata.py --types dataElements,indicators --filter "name:like:Malaria" \
    --unshare --delocalize --split --out ./slice
python scripts/import_metadata.py --src ./slice/split --schemas ./slice/schemas.json \
    --url $TARGET --auth $TARGET_AUTH --exclude users,userGroups
```

Without `--schemas` on import, add `--passes 2` for forward references; judge by the final pass's errors. Import does not strip sharing unless `--skip-sharing` is passed — decide at export time with `--unshare`.

Recipes and verified traps in `references/workflows.md`: import-error traps (§2), dummy data (§4), pre-import validation (§5), huge imports (§7), program documentation (§8), translating metadata (§9), troubleshooting (§10).

## Bundled schemas

`references/schemas-v40..v43.json` (~2 MB each — never `Read` them, pass via `--schemas`). Prefer the live instance's schemas (`fetch_metadata.py --schemas`); a mismatched version silently keeps or strips the wrong properties. If the version of a `metadata.json` is unknown, ask.

## Critical safety notes

1. **Never import users without explicit thought.** The standard admin UID `M5zQapPyTZI` is identical across most demo/dev instances; an anonymized payload containing it renames and disables the target's admin. Default to `--exclude users` on cross-instance imports and confirm before importing `users.json`. Excluding `userGroups` cascades into notification templates and dashboards; importing userGroups with membership stripped is safe.
2. **Imports modify shared systems.** Confirm target URL and auth first, especially if not localhost or a sandbox. `--dry-run` only previews ordering; validate on a throwaway instance (`dhis2-instances` skill) when it matters.
3. **`--minimize` requires a matching schema**, or the server rejects the payload with E4000.
4. **Anonymize is "safe to share", not deniability.** User objects stay (with placeholders), UIDs stay, custom attribute values are untouched.
