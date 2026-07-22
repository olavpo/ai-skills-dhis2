# Dump-based cleanup & restoring onto a disposable sandbox

The safest way to clean a real instance is to **not clean the real instance**. Work on a copy: import a
dump onto a throwaway sandbox, fix it there, verify, and hand back either the fixed instance or a
re-exported dump that someone can review and apply to production. This also gives you a free **control
baseline** (the original dump) for output verification.

## Which kind of "dump"?

| Artifact | Contains | Restores onto empty instance? | Best for |
|----------|----------|-------------------------------|----------|
| **Metadata `.json`** (`/api/metadata.json`) | all metadata (DEs, indicators, combos, programs, OUs, users, sharing…) — **no data values** | Yes, via `POST /api/metadata` (with the caveats below) | metadata-structure cleanup; sharing a sanitized config; the deliverable to review |
| **`pg_dump`** | everything incl. data values, audit, analytics | Yes, restore into an empty Postgres then point a DHIS2 container at it | a true clone for data-dependent checks (`no_data`, disjoint-with-data) and A/B **output** verification |

Rule of thumb: clean **metadata** from a metadata dump; if you must verify **outputs** (analytics,
forms, tracker numbers) or fix data-dependent checks, you need a `pg_dump`-level clone.

## Fingerprint the dump's DHIS2 version BEFORE creating the sandbox
The sandbox version must match the dump, or the import rejects objects or silently drops
added/removed properties. If you don't know it, derive it by diffing the dump's property names against
the bundled `dhis2-metadata/references/schemas-v4x.json`:
- `attributeValues` present on most object types ⇒ **≥2.42**.
- `programIndicators.categoryCombo` / `programs.categoryMappings` / `dataSets.displayOptions` ⇒ **2.42**.
- none of the 2.42 markers, and users still carry the top-level `userCredentials`-free 2.41 structure ⇒ **2.41**.
Quick check: load one `programs.json` / `dataElements.json` object and compare its keys to each schema's
property set; the version whose schema has the fewest "unknown" keys is the match. Guessing wrong is
expensive — verify.

## Exporting a complete metadata dump

```bash
python scripts/metadata_dump.py export --out original.metadata.json
```
This calls `GET /api/metadata.json?...` for all metadata types. Notes:
- Big instances: the export can be large; it's still a single JSON document. Keep `original.*.json` as
  the immutable control.
- Include sharing/users only if you intend to restore them (`--skip-sharing` / `--skip-users` flags).
  Importing users brings password hashes and org-unit assignments — usually fine for a sandbox clone,
  but drop them if you only care about structural metadata.

## Spinning up the sandbox and importing

1. Create an **empty** instance with the **dhis2-instances** skill (d2-broker — create empty, matching
   the source DHIS2 **version**; mismatched versions cause import failures).
2. Import:
   ```bash
   python scripts/metadata_dump.py import --in original.metadata.json \
       --base http://dhis2-<sandbox>:8080 --user admin --pass district
   ```
   The script posts to `/api/metadata` with `importMode=COMMIT`, `atomicMode=NONE` (so a few rejects
   don't abort the whole import), `identifier=UID`, and `async=true` for large payloads (then polls the
   job). Read the import summary — `typeReports[].objectReports[].errorReports` list what didn't import.

### Import gotchas (these bite everyone)

- **Default objects collide.** Every DHIS2 instance ships a `default` category, categoryCombo,
  categoryOption, and categoryOptionCombo — but with **different UIDs** per instance. A dump carries the
  *source's* default UIDs; importing into a fresh instance that already has *different* default UIDs
  creates duplicate "default" objects and can trip `categories_one_default_*` checks. Options: import
  into an instance seeded from the **same base** as the source, or post-import reconcile the defaults
  (map the source default UIDs to the target's, or delete the surplus). Flag this early.
- **Dependency order / forward references.** The metadata importer resolves most ordering itself, but a
  single big import with `atomicMode=NONE` may leave a few objects rejected on the first pass — **import
  twice** (the second pass resolves references created by the first), then read the summary.
- **Sharing & users.** If you imported users, logins/passwords come across; if you skipped them,
  ownership/sharing references may dangle — usually harmless for a structural-cleanup sandbox.
- **`generateMetadataDependencies`/`download=true`** on the export side keeps a self-contained set; the
  bundled script already requests a complete export.

## ALWAYS reconcile object counts after import — a dump can be dirtier than any live instance
The importer **silently refuses objects a live instance cannot hold**, so a rebuilt-from-dump instance is
often *cleaner* than the source — which **hides real integrity problems** (the original live instance
would fail checks the rebuild never can). After import, reconcile per type:
`GET /api/<type>?fields=id&pageSize=1&totalPages=true` → `pager.total`, vs the dump's array length.
Explain every delta and record the negatives as findings:
- **Legit positive deltas:** broker-added `local_admin` user + Superuser role; DHIS2 ships built-in
  **system jobConfigurations** on every instance.
- **Negative deltas = pre-existing corruption that couldn't round-trip** — real cases seen: **orphan
  program stages** (no `program` parent — DHIS2 can't create a stage without one; one had 308 DEs),
  **event visualizations missing `program`** (E4000), **job configs with params on a non-configurable job
  type** (E7003), **program-rule actions whose DE isn't on any stage** (E4047). These are genuine
  integrity issues the rebuild masked → flag them for the owner (don't silently ignore the discrepancy).

## Large / heavy dumps — when the single-payload import OOMs Tomcat
On big instances (100k+ org units, 100k+ users, or OU groups with huge memberships) a single
`/api/metadata` payload OOMs Tomcat (silent `RemoteDisconnected` / `api 000`). Strategy that worked on a dump with
hundreds of thousands of org units and users and over a million org-unit-group memberships:
1. **Main metadata:** `dhis2-metadata/scripts/import_metadata.py --resume --chunk-size 2000`, **excluding
   the two OOM culprits** `--exclude apiToken,users,userGroups,organisationUnitGroups`. `--resume` skips
   already-present UIDs so you can grind through mid-run crashes; import order is dependency-based, so
   org units may load before the category types (which backfill after).
2. **OU-group memberships:** the API collection `additions` endpoint re-serializes the whole (growing)
   group per call and OOMs on the 100k+-member groups → **bulk-INSERT directly into `orgunitgroupmembers`
   via SQL** (delete-then-insert per group, `execute_values`, 50k batches). Bypasses Tomcat entirely.
3. **Users:** chunk `/api/metadata` at ~1000 users/call, resumable (skip present UIDs); survive OOM blips
   by continuing + a final resume pass. Or SQL if even that fails (`userinfo` + `usermembership` +
   `userdatavieworgunits` + `userrolemembers`).
4. **`cacheClear` after any SQL write** so the API/checks see it, and re-link anything whose refs failed
   during the excluded-import (e.g. re-PUT the org-unit **group sets** once their groups exist).
5. **Serialize instances.** Two DHIS2 instances booting or one heavy import while another serves will
   starve each other on a shared host — finish and STOP one before starting the next; give the live one
   more heap (`POST /instances/<n>/memory {"memory":"8g"}`).

## The recommended dump-mode workflow

1. `export` the source → `original.metadata.json` (the **control**; never edit it).
2. Create empty sandbox (same version) → `import` the dump (twice; check the summary is clean).
3. Run the normal cleanup workflow on the sandbox (inventory → triage → decisions → fix → verify).
4. `export` the sandbox → `fixed.metadata.json` — the reviewable deliverable.
5. **Round-trip check:** import `fixed.metadata.json` into one more empty instance; a clean import
   summary proves the fixed metadata is internally consistent and portable.
6. Hand back `fixed.metadata.json` + the change docs. To apply to production, the owner imports the
   fixed dump (ideally after the same import into their own staging copy first).

## Diffing the dumps

A metadata-level diff between `original` and `fixed` is a fast, reviewable record of exactly what
changed: normalize both (sort by `id`, drop volatile fields like `lastUpdated`/`created`/`href`), then
diff per object type. This complements the per-category change CSVs and is great for a pull-request-style
review before anyone touches production.
