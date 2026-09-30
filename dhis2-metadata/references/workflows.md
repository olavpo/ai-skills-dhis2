# DHIS2 metadata workflows

Recipes and verified traps for the three scripts. Read this when the inline examples in `SKILL.md` aren't enough.

**Contributor rule:** this file records only what a frontier model gets wrong or cannot know — behaviour verified on a live instance, tagged with the DHIS2 version it was seen on (`verified 2.42.5`). Do not add generic API explanations, error-code glossaries, or how-to steps the model already produces unprompted; a baseline probe (2026-09) showed it knows field-filter syntax, the standard PII fields, E5002/E4000/E4007 and the `createdBy` shape without help. Untested claims go nowhere.

## 1. Prepare metadata for AI consumption (read-only analysis)

Goal: turn a 50–100 MB metadata.json into something the model can reason about. Anonymize PII, strip translations and sharing, remove non-essential fields, split per type so the model can grep/Read individual files.

```bash
# From a live instance: transforms are applied server-side (fields=:owner,!sharing,...)
# and locally, and --split writes the per-type files in one go
python scripts/fetch_metadata.py \
    --url http://localhost:9021 --auth user:pass \
    --metadata --schemas --split --out ./export \
    --anonymize --minimize --unshare --delocalize

# Metadata already on disk: same flags, local pass only
python scripts/transform_metadata.py ./export/metadata.json \
    --schemas ./export/schemas.json \
    --anonymize --minimize --unshare --delocalize \
    --split-dir ./export/split
```

Result: `./export/split/<plural>.json` files (e.g. `dataElements.json`, `programs.json`). The model can Read them individually instead of loading 60MB at once. `./export/metadata.json` is the same transformed content in one file.

`--minimize` refuses to run without a schemas file — pass the live one or the right version from `references/`; a wrong version silently keeps or strips the wrong properties.

## 2. Investigate import/export errors

The model knows the common codes (`E5002` invalid reference, `E4000` missing required property, `E4007` collection size, `E4061` dashboard item reference). What it does not know:

- `E1127` category >50 options and `E1130` COC count mismatch (2.43+) are **target-side caps/validation**, not payload bugs — see §7 known failure classes for the `dhis.conf` keys and the fix.
- `HTTP 409, status=ERROR, zero error reports` is a whole-batch Hibernate flush crash, not "no errors" — §7.
- `E5002` on the **first** of several passes is usually a deferred forward reference; judge by the final pass.
- Pre-import scan (§5) catches the late-failing dashboard/visualization references before an import cycle is spent.

## 3. Copy a slice of metadata between instances

Goal: take specific objects from instance A and import them into instance B.

```bash
# Fetch only what you need, with full owned fields (re-import shape)
python scripts/fetch_metadata.py \
    --url https://prod.example.org/dhis \
    --auth token:$PROD_PAT \
    --types dataElements,indicators,programs \
    --filter "name:like:Malaria" \
    --out ./slice

# Sanity-prep before sending (or pass --unshare --delocalize --split to the fetch above)
python scripts/transform_metadata.py ./slice/metadata.json \
    --unshare --delocalize \
    --split-dir ./slice/split
# keep names; do NOT --minimize if you want full re-import fidelity

# Import to target, exclude users to avoid clobbering admin
python scripts/import_metadata.py \
    --url https://staging.example.org/dhis \
    --auth user:pass \
    --src ./slice/split \
    --exclude users,userGroups \
    --passes 2  # second pass resolves forward refs (program <-> programRule etc.)
```

Sharing travels unless you removed it: `--unshare` at export, or `--skip-sharing` on import (sends `skipSharing=true`). A raw export whose sharing references users/groups the target lacks needs one of the two.

**Critical safety**: never import users without thinking. A user object in the payload overwrites any target user with the same UID or username (renaming, re-roling or disabling it), which can lock people out. Don't assume anything about admin accounts or their UIDs (production systems usually have the built-in admin disabled); if users must move, list the target's existing users first and check for UID/username collisions. Always `--exclude users` unless you have explicit user-import requirements and have verified the UIDs.

## 4. Generate realistic dummy data for a program or dataset

No shipped generator: fetch the program/dataset with `--types programs,programStages,programStageDataElements,dataElements,optionSets,options,trackedEntityAttributes,trackedEntityTypes --schemas --minimize --split`, read the split files, write a one-off script. The model knows how (respect `valueType`/`optionSet`, `/api/tracker?async=false`, `/api/dataValueSets`, small org-unit allow-list, small N first). Traps it does not know:

- **2.43+ `POST /api/dataValueSets` imports values per data set** (verified 2.43). Without a top-level `"dataSet"`, the server detects one per data element, and the whole payload fails with `409 Data set detection failed` when the payload's data elements belong to **no** data set (E8003) or, taken together, to **more than one** (E8002 "found multiple sets: […]"). Period type plays no part in detection (2.43.1 `DefaultDataEntryService.autoTargetDataSet`). The data set must also be assigned to the target org units ("Data set X not usable with org unit(s)…"). 2.40–2.42 accepted the same payloads, so a generator that worked there fails wholesale on 2.43. Fix: post one payload per data set with `"dataSet"` set, only for org units the data set is assigned to (as `dhis2-integrity/scripts/gen_synthetic_data.py` does). Also send each value's `categoryOptionCombo`: an omitted one is stored under the default COC (verified in 2.43.1 source), which is wrong for disaggregated data elements such as ANC visits by Fixed/Outreach. Timeliness also applies per group: for users without `F_EDIT_EXPIRED` (superusers have it), a single period past the data set's expiry days or beyond its open future periods rejects the whole group with E8030 (2.43.1 `DefaultDataEntryService.validateEntryTimeliness`). Generate only complete past periods, or run as a superuser.
- On a fresh instance the importing user has no org units, so tracker payloads fail on ownership/search scope until the root is assigned (§10, JSON Patch on 2.42+).

## 5. Validate metadata for issues before importing

Quick checks the model can do over the split files:

- **Dangling references**: collect all UIDs declared in any file, then scan every `{id: ...}` reference in every file; flag references whose target isn't declared. The classic late-failing case is visualizations/eventVisualizations and dashboards — data-dimension items (`dataDimensionItems`, `columns`/`rows`/`filters`) pointing at DEs/indicators/PIs missing from the slice only surface as `E4061`/`E5002` at import time, so scanning them up front saves a whole import cycle.
- **Required-field violations**: read the right-version schema from `references/`. For each object type, list properties with `required: true` and check every object in `<plural>.json` has them set (and non-empty).
- **DHIS2-specific limits**:
   - Category: max 50 options.
   - Visualization: collection properties capped at 255 (e.g. `series`, `seriesItems`).
   - UID format: 11 chars, must match `^[a-zA-Z][a-zA-Z0-9]{10}$`.
- **Duplicate codes**: many types require globally unique `code` values within their type.

## 6. Full export + anonymize from a large production instance

Goal: take a complete, PII-free copy of a production configuration off-site (for analysis, for a sandbox restore, for sharing with partners) **without hurting the live server**. A single `/api/metadata.json` request on a big instance can spike server memory and slow real users; instead walk each type page-by-page with a delay.

```bash
# Every metadata type, 200 objects per request, 0.3s between requests. PII, sharing and
# translations are excluded server-side (fields=:owner,!email,!sharing,...) so they never
# leave the server; the local pass catches embedded objects; --split writes per-type files.
# If org unit names or GPS points are themselves sensitive (schools, clinics), add
# --redact-ou-names / --drop-coordinates.
python scripts/fetch_metadata.py --url https://emis.example.org --auth token:$PAT \
    --all-types --page-size 200 --delay 0.3 --schemas \
    --exclude users \
    --anonymize --unshare --delocalize --drop-coordinates \
    --split --out ./export
```

Notes:
- Go through the "decide with the user" table in SKILL.md **before** the dump leaves the server: sharing, translations, users, org unit names and coordinates are each a deliberate choice, not a default.
- Tune `--page-size` down (100, 50) and `--delay` up if the server is struggling; the export gets slower but each request stays cheap. Types are fetched with `order=id:asc` so pages stay stable while objects change under you.
- `--since 2026-01-01` turns this into an incremental refresh (objects created **or** changed since that date — `lastUpdated` is set on creation too).
- Keep `--exclude users` unless there's an explicit reason to move users; it composes with the import-side safety rule (recipe 3). `userGroups` can stay: with `--anonymize` their member list is reduced to UIDs, and excluding them cascades into notification templates and dashboards (§7).
- `--anonymize` cannot recognise PII stored in custom attributes (`attributeValues` on org units, users, …). Ask what the instance's attributes hold; drop them with a one-off pass if needed.
- Transient connection drops are retried automatically with backoff; a type that errors under `--all-types` is skipped with a warning instead of aborting the export.
- Verify before handing over: grep the output for a known real name/username from the source; it should not appear anywhere (that check is how the `userGroups.users[]` leak was found).

## 7. Import a very large metadata file (tens of thousands of org units and up)

Goal: load a huge configuration (e.g. a national school registry with 100k+ org units) into an instance without timeouts, out-of-memory crashes, or a broken hierarchy.

```bash
python scripts/import_metadata.py --url http://localhost:9021 --auth user:pass \
    --src ./export/split \
    --exclude users,userGroups \
    --chunk-size 5000 --batch-delay 1 \
    --passes 2
```

Add `--schemas ./export/schemas.json` to derive the import order from the schema: circular clusters (program↔programStage etc.) import as one payload and a single pass suffices — no `--passes 2`. Verified caveats are encoded in the script: bookkeeping user refs are ignored, analytical types get explicit dependencies (`DataDimensionItem` has no schema entry), and options+optionSets import split-with-repeat because the combined payload mass-ignores options on 2.42.

What the flags do:
- `--chunk-size 5000` splits any type bigger than that into batches. Org units get special treatment: they are imported **shallow-first** (hierarchy level 1 → deepest, derived from `level`/`path`/parent links), so a child's parent always exists when the child arrives.
- Batches above `--async-threshold` (default 300) POST with `async=true` and poll `/api/system/tasks/METADATA_IMPORT/<id>` until done. Synchronous requests of that size can time out or leave a dead socket: on 2.38 a synchronous 1,500-visualization payload hung for an hour with no import task and nothing written. Smaller payloads stay synchronous so errors come back inline.
- **A 5xx is not proof of failure.** 2.38 has been seen committing a payload and then returning `500 could not execute statement`. After any 5xx the script re-reads the payload's UIDs and reports how many persisted, and a type that is fully on the server is not counted as failed. Do the same by hand before retrying outside the script, or the retry hits unique-key violations. After such 500s the list endpoints can lag the database (Hibernate cache) until a restart or `POST /api/maintenance?cacheClear=true`.
- `atomicMode=NONE` (the default) turns a bad row into a per-row error instead of rolling back the whole batch.
- If the run dies partway (network, server restart), re-run with `--resume`: it fetches existing UIDs per type and skips objects already on the server. Caveat: resumed runs will NOT update existing objects, so only use it to finish an interrupted load.

Interpreting failures:
- **A task completes with NO summary** → the server almost certainly crashed or hit OOM mid-import. Check the server log, lower `--chunk-size`, raise `--batch-delay`, then `--resume`.
- **Many E5002 (invalid reference) on the first pass** → forward references between types (program ↔ programRule etc.); the second pass (`--passes 2`) usually clears them. If they persist, the UID genuinely isn't in the export.
- **Circular-reference types**: the per-type ordering handles most cases with `--passes 2`, but if a pair keeps failing (e.g. `dataSets` ↔ `sections`, `programs` ↔ `programStages`), merge the two files into one payload and POST them together — DHIS2 resolves circular references within a single import. Concretely: `jq -s '{dataSets: .[0], sections: .[1]}' dataSets.json sections.json > combo.json`, POST `combo.json` to `/api/metadata`, and exclude both types from the scripted run.

### Known failure classes (verified on 2.42 with a real national database)

These come from a full seed → export → import → re-export → diff round-trip; expect them whenever the source database is older than the target's validation rules.

- **`HTTP 409, status=ERROR, zero error reports` = whole-batch flush crash.** One object whose *required* reference resolves to nothing (e.g. `DataElement.categoryCombo`, `MapView.legendSet`) makes Hibernate throw at commit and the entire payload is lost, even with `atomicMode=NONE` — per-row error reporting only catches references the validator checks. The response `message` (not the typeReports) names the property. Bisect the batch (halve → retry) to isolate poison objects; a single object import gives the exact cause with `importReportMode=FULL`.
- **`E1130` "Importing N CategoryOptionCombos does not match the expected amount of M for CategoryCombo X"** (2.43+; verified 2.43.1 with the Sierra Leone demo seed, absent on 2.40–2.42 with the same data). 2.43 validates that the COCs imported for a category combo equal its option-combination count; old databases carry stale/duplicate COCs that fail this per combo. Not caused by any transform — the raw export fails identically. Fix the source (drop the stale COCs; the dhis2-integrity skill covers COC deduplication) or exclude the affected COCs and run `categoryOptionComboUpdate` afterwards.
- **`E1106` "duplicate translation records"** — old databases often carry duplicate `(property, locale)` translation rows that predate validation; each one rejects its whole object. Repair by deduping `translations` arrays in the split files (keep first per property+locale), then re-import.
- **`E1127` category >50 options** — such categories exist happily in seeded databases but exceed the API's default caps. Fix on the **target's** `dhis.conf` (restart required): `metadata.categories.max_options` (default 50), `metadata.categories.max_per_combo` (5), `metadata.categories.max_combinations` (500) — verified working on 2.42. Only if you can't touch dhis.conf, exclude the whole chain (category → categoryCombos using it → dataElements on those combos → their dataSetElements) or it triggers the flush-crash class above. The `E4007` 255-item collection cap is NOT configurable.
- **`SENDMESSAGE` program rule actions crash if their template is missing** — `templateUid` is a plain string, invisible to reference validation, so a missing `programNotificationTemplate` is a flush crash, not an E5002. Import notification templates (and the userGroups they reference) first.
- **Excluding `userGroups` cascades** — notification templates (`recipientUserGroups`), dashboards, and sharing all reference user groups. Importing userGroups with their `users` membership stripped is safe (the admin-clobbering risk is `users` itself) and unblocks those chains.
- **Maps and map views: import maps with embedded views, never standalone `mapViews`, and check the version** (the importer skips `mapViews` by default). Verified on SL 2.38.7, 2.40.12, 2.41.10, 2.42.6, 2.43.1:

  | | 2.38 | 2.40 | 2.41 | 2.42 | 2.43 |
  |---|---|---|---|---|---|
  | `maps` with embedded views | fails in batch on some operands; works per map | **409 `TransientObjectException … LegendSet`** (or `…Indicator`) for every thematic view; `preheatMode=ALL` doesn't help | works, fixed periods too | works (but 2.42.5.1 crashed on unpreheated references) | works, except views with a **fixed** period: 409 `TransientObjectException … Period` |
  | standalone `mapViews` | creates orphan rows | ignored (200, total 0; mapView isn't a metadata schema) | created, then every embedding `maps` import fails on `mapview_uid_key` | same as 2.41 | same as 2.41 |
  | `mapViews:[{id}]` references | – | – | E4000 `Missing required property 'layer'` | same | same |
  | shell maps (`mapViews: []`) | works | works | works | works | works |

  Fallbacks: on 2.40, `/api/metadata` only imports shells; `POST /api/maps` one map at a time keeps the views but drops fixed periods. On 2.43, rewrite fixed-period views to relative periods or import those maps as shells. On 2.38, import `maps.json` in its own run with `--chunk-size 1`. Shells keep dashboard references resolvable but lose the views. Orphaned views can't be removed with `DELETE /api/mapViews/<uid>` (405 on 2.40–2.43); on 2.41+ use `POST /api/metadata?importStrategy=DELETE` with `{"mapViews":[{"id":…}]}` (a no-op on 2.40, where deleting the map removes its views). A large shell-map payload crashed the whole JVM; use small chunks (≤50).
- **Server-normalized properties differ after a round-trip without being real changes**: option `sortOrder` is renumbered, `optionSet`/`dataSet`/`program` `version` counters bump on every import pass, defaults materialize (`false`/`NONE` where the source had null), and everything referencing categoryOptionCombos by UID (section `greyedFields`, predictor `outputCombo`, visualization `dataElementOperand`s) drops or changes because COCs are regenerated with fresh UIDs on the target. Ignore these when diffing source vs. imported copy.
- **`jobConfigurations` are instance-managed** — a fresh instance creates its own defaults, and imported ones carry meaningless scheduling state. Consider `--exclude jobConfigurations` for cross-instance copies.
- **2.38 targets: maps.** Maps with a `DATA_ELEMENT_OPERAND` on a non-default combo failed in batch (`could not initialize proxy CategoryCombo#N - no Session`) but committed one per request (see the maps table above).
- **2.38 users already use the flattened shape** (`username`/`userRoles` on the user, no `userCredentials`), so a 2.38 export imports into 2.38 unchanged.
- **SQL views are only parsed at execution, never at import** — a metadata package can therefore ship version-specific SQL-view variants side by side (e.g. tracker queries for 2.40 / 2.41–42 / 2.43+) and import cleanly everywhere; users run the variant matching their version.

## 8. Document a program as human-readable Markdown

Same approach as §4: prep, then a one-off script over the split files. Structure (per program: TET + attribute table; stages → sections → data-element tables; option-set appendix with "used by") is what the model produces anyway. Traps:

- **Display order lives on the join objects** — `programTrackedEntityAttributes` (program level) and `programStageDataElements` (stage level) carry `sortOrder`, not the DE/TEA itself. Sections and stages carry their own `sortOrder`.
- **Stage data elements that appear in no section are silently dropped** unless you add a "Not in sections" table per stage.
- Option sets can have thousands of options — cap the appendix (~50 rows + "N skipped").
- Program-indicator variant: resolve `#{stageUid.deUid}` and `A{teaUid}` tokens to names. Authoring/reviewing the expressions is the dhis2-indicators skill.

## 9. Translate metadata to another locale

The model does the translating; the record format is known to it (`translations: [{locale, property, value}]`, `property` in SCREAMING_SNAKE: `shortName` → `SHORT_NAME`). Traps:

- **Never translate `programRuleVariables` names.** Program rules reference variables *by name* (`#{varName}`, `A{varName}`, `d2:hasValue('varName')`); translating the name silently breaks every rule using it and the damage only shows as rules that stop firing. Exclude the type entirely.
- **Duplicate `(property, locale)` per object rejects the whole object with `E1106`** (verified 2.42, common in old databases) — dedupe before importing.
- Length limits apply to translations too (`SHORT_NAME` ≤ 50).
- Which fields are translatable is per-type schema data (`translatable: true` in `schemas.json`), not a fixed list.
- Confirm the target: *add* a locale (primary properties untouched) vs *switch primary language* (translated values become the properties, originals stored as `translations` for the source locale).

## 10. Troubleshooting

- **Don't trust `response.uid` on a metadata POST.** A failed create (e.g. a name conflict on `/api/attributes`) can still return a generated `response.uid` for an object that was never persisted — a later GET on it 404s. Check `status`/`httpStatus`; uid-presence alone means nothing.
- **`--minimize` keeps a property the user expected stripped, or strips one they expected kept.** Schema property names are the source of truth. The script uses `name` (not `fieldName`) for non-collection properties because that's what the JSON serialization uses — an easy bug class. Verify by inspecting the schema entry: if `owner=true && persisted=true` and the name isn't in `BOOKKEEPING_FIELDS`, it should be kept.
- **A field filter with brackets returns nothing from curl.** `fields=legends[id,name]` is eaten by curl's URL globbing; pass `-g`. (Nested exclusions like `legends[:owner,!lastUpdatedBy]` do work server-side, but the transform scripts handle embedded objects locally instead of generating per-type nested filters.)
- **`import_metadata.py` returns 500 on a type.** Check the server log — the response body often loses the cause. Common cause: a required reference was stripped (often by an over-eager `--minimize`) so an `IdentifiableObject.getUid()` call sees null. Re-run without `--minimize`, or add the missing field manually.
- **An async import task completes with NO summary.** The server almost certainly crashed or ran out of memory mid-import. Shrink the payload (`--chunk-size`), give the server breathing room (`--batch-delay 1`), and re-run with `--resume` (§7).
- **Many reference errors after import.** `--passes 2` lets a second pass pick up forward refs. If errors persist they're usually source data quality — the import report names the missing UID.
- **409 `PropertyValueException: not-null property references a null or transient value` when POSTing a whole bundle to an empty instance** (e.g. `DataSet.periodType` even though every dataset has one). The named property is a red herring: Hibernate flushed an object before a dependency it hadn't persisted yet. Import per-type in dependency order (`import_metadata.py`'s default).
- **Metadata and data imported fine, but an app or analytics shows an empty state.** On a fresh instance the admin has no org units assigned. Assign the hierarchy root to the user's `organisationUnits`, `dataViewOrganisationUnits` and `teiSearchOrganisationUnits` — on 2.42+ via JSON Patch (`Content-Type: application/json-patch+json`; plain-JSON `PATCH /api/users/<id>` is rejected).
- **categoryOptionCombos: migrate them, don't regenerate.** The importer imports COCs by default so their UIDs survive — section `greyedFields`, predictor `outputCombo`, and visualization `dataElementOperand`s reference COCs by UID. Only skip them consciously (`--exclude categoryOptionCombos` + `POST /api/maintenance/categoryOptionComboUpdate` afterwards). Run the maintenance endpoint after import either way to fill gaps.
- **Default category objects may not match between instances.** Databases first created on 2.22 or later share fixed default UIDs (category option `xYerKDKCefk`, category `GLevLNI9wkl`, category combo `bjDvmb4bfuf`, COC `HllvX50cXC0`); databases created earlier keep random ones through every upgrade, and many long-running production systems are that old. Before copying between instances, read both sides' four `default` objects (`?filter=name:eq:default`). If they differ, the import creates a second set of defaults: map the source's default UIDs to the target's in the payload first.
- **Import limits blocking valid-looking config (E1127).** Category caps are configurable in the **target's** `dhis.conf` (restart required): `metadata.categories.max_options` (50), `max_per_combo` (5), `max_combinations` (500). The 255-item collection cap behind `E4007` is not configurable.
- **UID-uniqueness conflicts** (`duplicate key value violates unique constraint`) on a type owned by a parent object: add it to `EMBEDDED_OWNED` in the import script.

## DHIS2 schema concepts (what --minimize is doing)

Every property in a schema has flags that explain how it's stored:

| Flag | Meaning |
|---|---|
| `owner=true` | The property "belongs" to this object (vs. being a back-reference from another object) |
| `persisted=true` | Stored in this object's row(s); needed for round-tripping |
| `required=true` | Server rejects the object if the property is missing |
| `propertyType=REFERENCE` | Single object reference; only the `id` is needed for re-import |
| `propertyType=COLLECTION` + `embeddedObject=true` | Owned sub-objects (e.g. `dashboardItems` inside a dashboard) |
| `propertyType=COLLECTION` + `embeddedObject=false` | Plain references (e.g. a program's `organisationUnits`) |

The minimize step keeps `owner=true && persisted=true` properties (minus bookkeeping like `lastUpdated`/`createdBy`) and reduces references to `{id: ...}`, recursing into embedded objects with their own schema. On a `fields=*` export that removes 30–60% of the bytes; on the `:owner` export the fetch script produces it is ~10–15%, mostly from embedded objects (which `:owner` renders with `access`, `displayName`, `sharing` etc.) and bookkeeping.

## Adapting the import script across versions

If you're importing into 2.40 or 2.43+ and the dependency order or type list differs:

- **New types**: edit `ORDER` in `scripts/import_metadata.py`. Unknown types are appended at the end, which is OK if they have no dependents but problematic if they do.
- **Renamed types**: the splitter writes whatever plural name appears in the source. If 2.43 renames `eventVisualizations` to something else, the file will be named differently — adjust `ORDER` accordingly.
- **Removed types**: just leave them out of the source dir; the importer skips missing files.
- **EMBEDDED_OWNED set**: types owned by a parent that must not be imported standalone (UID collision). Currently `{"mapViews"}`: maps carry their views embedded (see *Known failure classes*); `--include-embedded-owned` overrides it. If a type causes `duplicate key value violates unique constraint`, add it.

When in doubt: run `import_metadata.py --dry-run` first to see the order, then run with `--passes 2` to resolve any forward-reference cycles. On multi-pass runs, judge success by the **final pass's** error count (the script prints it per pass) — pass-1 `E5002`s that a later pass resolves are deferred forward references, not real failures.
