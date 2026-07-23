---
name: dhis2-metadata
description: "Use whenever the user is working with DHIS2 metadata files or live-instance metadata in bulk — exporting, splitting, transforming, comparing, validating, importing, or feeding configuration into AI workflows. Triggers on metadata.json, schemas.json, dataElements/indicators/programs/orgUnits/categoryCombos work, import/export error analysis, copying configuration between instances, preparing metadata context for indicator or program-indicator work (authoring the expressions themselves is the dhis2-indicators skill), generating dummy data for a program or dataset, anonymizing or minimizing metadata for sharing, and any case where the user references DHIS2 configuration objects on disk or via the API. Use this even when the user does not explicitly say 'metadata' — if they're investigating why an import failed, looking at a category combo structure, or asking 'what does this program look like', this skill applies."
---

## Overview

This skill helps with DHIS2 metadata in two complementary modes:

1. **Preparing metadata for analysis** — exporting from a live instance (or starting from an existing file), shrinking it down to the fields that matter, splitting it per object type so it can be Read/grepped efficiently. This is the right starting point when the user wants to *understand* a configuration: investigating an issue, defining program indicators, validating structure, generating dummy data scripts, or feeding context to AI.

2. **Transferring metadata between instances** — fetching a slice from one instance, transforming it (strip sharing, strip translations, optionally anonymize), and importing into another instance per-type with dependency-aware ordering. This is the right starting point when the user wants to *move* configuration: copying a program from prod to staging, recreating a setup, importing curated dataset.

Both modes share the same three scripts. Read the rest of this file end-to-end before running anything — the safety notes matter, and the schema-driven minimize behaviour is non-obvious.

---

## Scripts

All scripts live in `scripts/` next to this file and are runnable with plain `python` (only dependency: `requests`). Pass `--help` for the full CLI of each. The two network scripts take `--url`/`--auth`, falling back to `$DHIS2_BASE_URL` and `$DHIS2_AUTH` (or `token:$DHIS2_API_TOKEN`) when omitted — the same variables a `.env` from other DHIS2 skills provides.

| Script | Purpose |
|---|---|
| `fetch_metadata.py` | Pull `metadata.json` and/or `schemas.json` from a live instance. Supports per-type fetches with filters, and paginated/throttled export (`--page-size`/`--delay`) for production servers. |
| `split_metadata.py` | Read a metadata or schema JSON file, optionally apply transformations (`--anonymize`, `--minimize`, `--unshare`, `--delocalize`, `--redact-ou-names`, `--drop-coordinates`), write per-type files to an output directory. |
| `import_metadata.py` | Read a directory of per-type files and POST them to `/api/metadata` in dependency order. Pass `--schemas` to derive the order and circular-reference groups from the schema (single-pass; preferred) instead of the built-in ORDER list. Handles very large files via chunking, async task polling, and `--resume`. |

---

## When to use which transformation

`split_metadata.py` has four independent flags. They compose; you usually want a subset.

- **`--minimize`** — strips properties the schema marks as non-owned/non-persisted (derived/computed/back-reference fields like `displayName`, `access`, `href`, `userAccesses`) plus bookkeeping (`lastUpdated`, `createdBy`, etc.), and reduces nested references to `{id: ...}`. Requires the version-matching `schemas.json` (see below). Use whenever the goal is analysis, not a full round-trip; cuts payload by 30–60%.

- **`--unshare`** — recursively removes `sharing`, `userAccesses`, `userGroupAccesses`, `publicAccess`, `externalAccess`. Use when copying between instances (sharing IDs almost never align), or when the sharing tree is a distraction during analysis.

- **`--anonymize`** — strips PII and credential material (email, phoneNumber, firstName, surname, username, address, twoFactorSecret, previousPasswords, etc.) from every object. For top-level user objects, replaces required fields like firstName/surname/username with `User`/`<id>`/`user_<id>` placeholders so the user remains valid. Reduces `createdBy`/`lastUpdatedBy` to `{id: ...}`. Use when sharing metadata externally or when feeding it to AI services that shouldn't see real names.

  Two companion flags cover data that is sensitive in some contexts but not others, so they are separate opt-ins: **`--redact-ou-names`** replaces organisation unit names with `OrgUnit <id>` (facility/school names can identify people or places), and **`--drop-coordinates`** removes org unit geometry/GPS points. Ask the user whether org unit names and coordinates are sensitive for their dataset before sharing an export externally.

- **`--delocalize`** — recursively removes `translations` arrays. Use when locale data is not relevant (almost always for analysis; sometimes for transfer).

For **AI consumption**: `--anonymize --minimize --unshare --delocalize` is the right default.
For **instance-to-instance transfer**: usually `--unshare --delocalize`. **Don't** use `--minimize` unless you're sure you don't need to round-trip — a few rarely-used properties may have an `owner=true` flag in the schema even though they're not strictly required.

---

## Bundled schemas

`references/` contains schema dumps for major DHIS2 versions (when present). These are large (~2 MB each) so do not Read them — pass them to `split_metadata.py` via `--schemas`.

```
references/schemas-v40.json
references/schemas-v41.json
references/schemas-v42.json
references/schemas-v43.json
```

If the user has a `metadata.json` but no `schemas.json`, infer the version: open the file briefly and check for fields/types that are version-specific, or ask. If unsure, ask the user — guessing wrong silently filters the wrong properties.

If the user is working against a live instance and wants the freshest schemas, `fetch_metadata.py --schemas` will pull them. Bundled schemas are a fallback for offline work.

---

## Workflow snapshots

### Prep metadata for AI (the most common case)

```bash
# If starting from a live instance
python scripts/fetch_metadata.py --url $DHIS2_URL --auth $DHIS2_AUTH \
    --metadata --schemas --out ./export

python scripts/split_metadata.py ./export/metadata.json \
    --schemas ./export/schemas.json \
    --output-dir ./export/split \
    --anonymize --minimize --unshare --delocalize
```

After this, the model can `Read ./export/split/programs.json` to inspect just programs without loading the rest. Encourage that pattern instead of reading the giant original.

### Export gently from a production instance

A bare `--metadata` export is ONE giant `/api/metadata.json` request — fine on a small or throwaway instance, but on a large production server it can spike memory and hurt live users. When the source is production (or just big), walk every type page-by-page with a delay instead:

```bash
python scripts/fetch_metadata.py --url $DHIS2_URL --auth token:$PAT \
    --all-types --page-size 200 --delay 0.3 \
    --exclude users,userGroups --out ./export
```

`--all-types` discovers the type list from `/api/schemas`; each type is fetched in pages of `--page-size` (ordered `id:asc` so pages don't shift), sleeping `--delay` seconds between requests. `--since 2026-01-01` limits to objects created/changed since a date (useful for incremental refreshes). Full recipe including the anonymize step: `references/workflows.md`.

### Copy a slice between instances

See `references/workflows.md` for the recipe — it covers fetching specific types with `--filter`, transforming, and importing with `--passes 2 --exclude users,userGroups`.

### Investigate an import failure

Read the per-type files and the import report side by side. Decode the error code (E5002 = invalid reference, E4000 = missing required property, E4061 = dashboard item ref not found, E4007 = collection size out of range, E1127 = category >50 options). For details, see `references/workflows.md`.

### Generate dummy data

The skill does not ship a dummy-data generator — it depends too much on the specific program or dataset. Instead, use the prep workflow above to give the model the relevant program metadata, then write a one-off script that respects each data element's `valueType` and `optionSet`. Recipe in `references/workflows.md`.

### Document a program for humans

A different deliverable from the AI prep: readable Markdown documentation of a program's configuration — attributes, stages/sections in display order, option-set appendix with usage cross-references. Same approach as dummy data (prep, then a one-off script); the output structure and its `sortOrder`-on-join-objects gotcha are in `references/workflows.md` §8.

### Translate metadata to another locale

The flags above only *strip* translations (`--delocalize`); producing them is a recipe: schema-driven translatable fields, the `translations` record format, and the trap that translating `programRuleVariables` names silently breaks program rules. See `references/workflows.md` §9.

---

## Critical safety notes

1. **Never import users without explicit thought.** The standard DHIS2 admin UID `M5zQapPyTZI` is identical across virtually all demo/dev/training instances. An anonymized payload containing that UID will rename and disable the target's admin user, locking everyone out. Default to `--exclude users,userGroups` for any cross-instance import. Confirm with the user before running an import that includes `users.json`.

2. **Imports modify shared systems.** Treat `/api/metadata` like any other write to a shared service: confirm the target URL and auth with the user before running, especially if the URL doesn't look like localhost or a personal sandbox. A `--dry-run` is available and previews ordering only — for true validation, post to a throwaway instance first (the `dhis2-instances` skill can provision one, if it is available in the environment).

3. **`--minimize` requires a matching schema.** The schema's `owner`/`persisted`/`fieldName`/`name` metadata is what lets the script know which fields are essential. A v42 schema applied to a v43 export may strip a brand-new required property and produce a payload the server rejects with E4000. When in doubt, fetch the live schema instead.

4. **Anonymize replaces values; it does not remove user objects.** If the goal is "remove all evidence that user X existed", `--anonymize` is not enough — you also need to drop the user from `users.json` and audit every `createdBy`/`lastUpdatedBy` reference. The flag is intended for "make this safe to share", not "deniability".

---

## When something goes wrong

- **`split_metadata.py` keeps a property the user expected stripped, or strips one they expected kept.** Schema property names are the source of truth. The script uses `name` (not `fieldName`) for non-collection properties because that's what the JSON serialization uses — but that's an easy bug class. Verify by inspecting the schema entry for the property: if `owner=true && persisted=true` and the name isn't in the bookkeeping blacklist, it should be kept.

- **`import_metadata.py` returns 500 on a type.** Check the server log (`d2-logtail <container>` if available) — the body of the response often loses the cause, but the server log usually has a stack trace. Common cause: a required reference was stripped (often by an over-eager `--minimize`) so an `IdentifiableObject.getUid()` call sees null. Re-run without `--minimize`, or add the missing field manually.

- **An async import task completes with NO summary.** The server almost certainly crashed or ran out of memory mid-import (the importer prints this diagnosis). Shrink the payload (`--chunk-size`), give the server breathing room (`--batch-delay 1`), and re-run with `--resume` to skip what already landed. The large-import recipe in `references/workflows.md` covers this end-to-end.

- **The import is huge (tens of thousands of org units or more).** Don't POST it as one payload per type. Use `--chunk-size 5000` — org units are then imported shallow-first (parents before children) in async batches with task polling — plus `--resume` after any crash. See `references/workflows.md`.

- **Many reference errors after import.** Run `import_metadata.py --passes 2` so a second pass can pick up forward refs (e.g. programs <-> programRules). If errors persist, they're usually source data quality (UID points to nothing in the export) — the import report will tell you which UID is missing.

- **categoryOptionCombos: migrate them, don't regenerate.** The importer imports COCs by default so their UIDs survive — section `greyedFields`, predictor `outputCombo`, and visualization `dataElementOperand`s reference COCs by UID and silently break if the target regenerates them with fresh UIDs. Only skip them consciously (`--exclude categoryOptionCombos` + `POST /api/maintenance/categoryOptionComboUpdate` afterwards) when COC-level references don't matter. Run the maintenance endpoint after import either way to fill any gaps.

- **Import limits blocking valid-looking config (E1127).** Objects that exist in an old database can exceed the API's import caps. The category caps are configurable in `dhis.conf` on the **target** (restart required): `metadata.categories.max_options` (default 50), `metadata.categories.max_per_combo` (5), `metadata.categories.max_combinations` (500). Raising them is usually better than mutilating the source. The 255-item collection cap behind `E4007` (visualization series etc.) is *not* configurable.

- **UID-uniqueness conflicts** (`duplicate key value violates unique constraint`) on a type that's owned by a parent object: add it to `EMBEDDED_OWNED` in the script so it's skipped as a standalone payload.

---

## Further reading

`references/workflows.md` has expanded recipes for each of the workflows above, plus a section on adapting the import script across DHIS2 versions and a quick-reference table of DHIS2 schema concepts (`owner`, `persisted`, `embeddedObject`, etc.).
