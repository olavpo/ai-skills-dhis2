# DHIS2 metadata workflows

Common recipes for the three scripts. Read this when you need a starting point but the inline examples in `SKILL.md` aren't enough.

## 1. Prepare metadata for AI consumption (read-only analysis)

Goal: turn a 50–100 MB metadata.json into something the model can reason about. Anonymize PII, strip translations and sharing, remove non-essential fields, split per type so the model can grep/Read individual files.

```bash
# From a live instance
python scripts/fetch_metadata.py \
    --url http://localhost:9021 \
    --auth user:pass \
    --metadata --schemas \
    --out ./export

# Or, if you already have a metadata.json file, skip the fetch step and
# put schemas.json next to it (or pass --schemas explicitly).

# Transform + split
python scripts/split_metadata.py ./export/metadata.json \
    --schemas ./export/schemas.json \
    --output-dir ./export/split \
    --anonymize --minimize --unshare --delocalize
```

Result: `./export/split/<plural>.json` files (e.g. `dataElements.json`, `programs.json`). The model can Read them individually instead of loading 60MB at once.

If schemas.json is unavailable, `--minimize` falls back to a hardcoded blacklist; results are coarser but still usable. Prefer providing the right-version schema from `references/`.

## 2. Investigate import/export errors

Goal: explain why a metadata import failed.

1. Have the user paste the import report or the metadata file.
2. If it's a metadata file, locate the problem objects — look for objects referencing UIDs that aren't in the file, missing required fields per the schema, or fields that DHIS2 will reject (e.g. category with >50 options, visualization with >255 series items).
3. If it's an import report (response from `/api/metadata` or `/api/dataValueSets`), explain each error code:
   - `E5002`: invalid reference (target UID not present or not yet imported)
   - `E4000`: missing required property
   - `E4007`: collection size out of allowed range
   - `E4061`: dashboard item references object that doesn't exist
   - `E1127`: category exceeds 50-option limit
4. Propose minimal fixes (add the missing object, drop the offending field, change import order).

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

# Sanity-prep before sending
python scripts/split_metadata.py ./slice/metadata.json \
    --schemas <bundled-schemas-for-target-version> \
    --output-dir ./slice/split \
    --unshare --delocalize  # keep names; do NOT minimize if you want full re-import fidelity

# Import to target, exclude users to avoid clobbering admin
python scripts/import_metadata.py \
    --url https://staging.example.org/dhis \
    --auth user:pass \
    --src ./slice/split \
    --exclude users,userGroups \
    --passes 2  # second pass resolves forward refs (program <-> programRule etc.)
```

**Critical safety**: never import users without thinking. The standard DHIS2 admin UID is `M5zQapPyTZI` on virtually every demo/dev instance. Importing an anonymized payload that contains a user with that UID will rename and disable the target's admin and lock you out. Always `--exclude users` unless you have explicit user-import requirements and have verified the UIDs.

## 4. Generate realistic dummy data for a program or dataset

Goal: write a script that pushes plausible test data to `/api/tracker` or `/api/dataValueSets`.

The skill itself doesn't ship a dummy-data script — every program has different value types, option sets, and constraints, so the right shape of script depends on the metadata. The workflow is:

1. Fetch the program (or dataset) and its full transitive metadata: programStages, programStageDataElements, dataElements, optionSets, options, trackedEntityAttributes.

   ```bash
   python scripts/fetch_metadata.py --url ... --auth ... \
       --types programs,programStages,programStageDataElements,dataElements,optionSets,options,trackedEntityAttributes,trackedEntityTypes \
       --out ./prog
   ```

2. Run `split_metadata.py --minimize` so the model can read individual files without spending tokens on irrelevant fields.

3. Read the relevant files and write a Python script that:
   - Iterates the data elements / TEAs in scope.
   - For each, generates values matching its `valueType` (TEXT, INTEGER, BOOLEAN, DATE, NUMBER, etc.) and respects its `optionSet` (pick from the available options) and any value-range constraints.
   - For tracker programs: creates trackedEntities + enrollments + events through `/api/tracker?async=false`.
   - For aggregate datasets: builds a `dataValueSets.json` payload and POSTs to `/api/dataValueSets`.
   - Picks org units from a small allow-list (don't fan out across thousands of facilities — it makes the data unrealistic and the import slow).

4. Always default to a small N (e.g. 50 trackedEntities, 5 events each) for the first run. Confirm with the user before generating thousands.

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
# 1. Gentle export: every metadata type, 200 objects per request, 0.3s between requests
python scripts/fetch_metadata.py --url https://emis.example.org --auth token:$PAT \
    --all-types --page-size 200 --delay 0.3 \
    --exclude users,userGroups \
    --schemas --out ./export

# 2. Anonymize + split. If org unit names or GPS points are themselves sensitive
#    (schools, clinics), add --redact-ou-names / --drop-coordinates.
python scripts/split_metadata.py ./export/metadata.json \
    --output-dir ./export/split \
    --anonymize --unshare --delocalize --drop-coordinates
```

Notes:
- Tune `--page-size` down (100, 50) and `--delay` up if the server is struggling; the export gets slower but each request stays cheap. Types are fetched with `order=id:asc` so pages stay stable while objects change under you.
- `--since 2026-01-01` turns this into an incremental refresh (objects created **or** changed since that date — `lastUpdated` is set on creation too).
- Keep `--exclude users,userGroups` unless there's an explicit reason to move users; it composes with the import-side safety rule (recipe 3).
- Decide with the user whether org unit names/coordinates count as PII for their context **before** the dump leaves the server. Anonymization here is "safe to share", not deniability — see the safety notes in SKILL.md.
- Transient connection drops are retried automatically with backoff; a type that errors under `--all-types` is skipped with a warning instead of aborting the export.

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
- Batches above `--async-threshold` (default 3000) POST with `async=true` and poll `/api/system/tasks/METADATA_IMPORT/<id>` until done — a synchronous request that big would time out. Smaller payloads stay synchronous so errors come back inline.
- `atomicMode=NONE` (the default) turns a bad row into a per-row error instead of rolling back the whole batch.
- If the run dies partway (network, server restart), re-run with `--resume`: it fetches existing UIDs per type and skips objects already on the server. Caveat: resumed runs will NOT update existing objects, so only use it to finish an interrupted load.

Interpreting failures:
- **A task completes with NO summary** → the server almost certainly crashed or hit OOM mid-import. Check the server log, lower `--chunk-size`, raise `--batch-delay`, then `--resume`.
- **Many E5002 (invalid reference) on the first pass** → forward references between types (program ↔ programRule etc.); the second pass (`--passes 2`) usually clears them. If they persist, the UID genuinely isn't in the export.
- **Circular-reference types**: the per-type ordering handles most cases with `--passes 2`, but if a pair keeps failing (e.g. `dataSets` ↔ `sections`, `programs` ↔ `programStages`), merge the two files into one payload and POST them together — DHIS2 resolves circular references within a single import. Concretely: `jq -s '{dataSets: .[0], sections: .[1]}' dataSets.json sections.json > combo.json`, POST `combo.json` to `/api/metadata`, and exclude both types from the scripted run.

### Known failure classes (verified on 2.42 with a real national database)

These come from a full seed → export → import → re-export → diff round-trip; expect them whenever the source database is older than the target's validation rules.

- **`HTTP 409, status=ERROR, zero error reports` = whole-batch flush crash.** One object whose *required* reference resolves to nothing (e.g. `DataElement.categoryCombo`, `MapView.legendSet`) makes Hibernate throw at commit and the entire payload is lost, even with `atomicMode=NONE` — per-row error reporting only catches references the validator checks. The response `message` (not the typeReports) names the property. Bisect the batch (halve → retry) to isolate poison objects; a single object import gives the exact cause with `importReportMode=FULL`.
- **`E1106` "duplicate translation records"** — old databases often carry duplicate `(property, locale)` translation rows that predate validation; each one rejects its whole object. Repair by deduping `translations` arrays in the split files (keep first per property+locale), then re-import.
- **`E1127` category >50 options** — such categories exist happily in seeded databases but exceed the API's default caps. Fix on the **target's** `dhis.conf` (restart required): `metadata.categories.max_options` (default 50), `metadata.categories.max_per_combo` (5), `metadata.categories.max_combinations` (500) — verified working on 2.42. Only if you can't touch dhis.conf, exclude the whole chain (category → categoryCombos using it → dataElements on those combos → their dataSetElements) or it triggers the flush-crash class above. The `E4007` 255-item collection cap is NOT configurable.
- **`SENDMESSAGE` program rule actions crash if their template is missing** — `templateUid` is a plain string, invisible to reference validation, so a missing `programNotificationTemplate` is a flush crash, not an E5002. Import notification templates (and the userGroups they reference) first.
- **Excluding `userGroups` cascades** — notification templates (`recipientUserGroups`), dashboards, and sharing all reference user groups. Importing userGroups with their `users` membership stripped is safe (the admin-clobbering risk is `users` itself) and unblocks those chains.
- **Map *views* cannot be imported on 2.42** (verified 2.42.5.1). Embedded mapViews crash the payload (their references are never preheated), a standalone `mapViews` payload is silently ignored (HTTP 200, zero objects), and `{id}`-reference views crash on `MapView.layer`. The only working import is maps as **shells with `mapViews` removed** — this keeps dashboard references resolvable but loses all view content. Also note batch size: a large shell-map payload crashed the whole JVM; use small chunks (≤50).
- **Server-normalized properties differ after a round-trip without being real changes**: option `sortOrder` is renumbered, `optionSet`/`dataSet`/`program` `version` counters bump on every import pass, defaults materialize (`false`/`NONE` where the source had null), and everything referencing categoryOptionCombos by UID (section `greyedFields`, predictor `outputCombo`, visualization `dataElementOperand`s) drops or changes because COCs are regenerated with fresh UIDs on the target. Ignore these when diffing source vs. imported copy.
- **`jobConfigurations` are instance-managed** — a fresh instance creates its own defaults, and imported ones carry meaningless scheduling state. Consider `--exclude jobConfigurations` for cross-instance copies.

## 8. Document a program as human-readable Markdown

Goal: readable configuration documentation for implementers and reviewers — a different deliverable from the AI prep in §1. Like dummy data (§4), don't reach for a shipped generator: run the §1 prep, then write a one-off script over the split files producing this structure (proven on real tracker programs):

- One `#` section per program: the tracked entity type, then a table of its tracked entity attributes (ID, name, value type, option set — link option-set names to the appendix).
- `## Programme structure`: stages in order, each with `repeatable`, then its sections in order, each with a data-element table (same columns as the TEA table). Finish each stage with a "Not in sections" table for stage DEs that appear in no section — silently dropping them is the common mistake.
- **Ordering gotcha**: display order lives on the *join* objects — `programTrackedEntityAttributes` (program level) and `programStageDataElements` (stage level) carry `sortOrder`, not the DE/TEA itself. Sections and stages carry their own `sortOrder`.
- `# Appendix — OptionSets`: one entry per option set *actually used*, with ID, `valueType`, an options table (ID, code, name; sort by option `sortOrder`; cap at ~50 rows with an "N options skipped" note — option sets can have thousands) and a "Used by" table of the DEs/TEAs referencing it.
- Program-indicator variant: per PI a table of name/ID/aggregationType/expression/filter, plus the stages, DEs and TEAs the expressions reference — parse `#{stageUid.deUid}` and `A{teaUid}` tokens and resolve UIDs to names. (Authoring or reviewing the expressions themselves is the dhis2-indicators skill.)

## 9. Translate metadata to another locale

Goal: produce translations (the opposite of `--delocalize`). The model does the translating itself; the reusable knowledge is the format and the traps.

- **Which fields are translatable** is per-type schema data: properties with `translatable: true` in `schemas.json` (`name`, `shortName`, `description`, form names, …).
- **Two distinct targets — confirm which the user wants**: (a) *add* translations for a new locale, leaving the primary properties alone; (b) *switch the primary language* — set translated values as the main properties and store the originals as `translations` records for the source locale.
- **Record format**: entries in the object's `translations` array look like `{"locale": "fr", "property": "SHORT_NAME", "value": "…"}` — `property` is the field name in SCREAMING_SNAKE (`shortName` → `SHORT_NAME`, `formName` → `FORM_NAME`).
- **Length limits apply to translations too**: a translated `SHORT_NAME` must still fit 50 chars.
- **Dedupe `(property, locale)` per object before importing** — duplicates reject the whole object with `E1106` (failure classes, §7).
- **Never translate `programRuleVariables` names.** Program rules reference variables *by name* — `#{varName}`, `A{varName}`, `d2:hasValue('varName')` — so translating the name silently breaks every rule using it, and the damage only shows up as rules that stop firing. Exclude the type entirely.
- **Keep terminology consistent** across objects (the same domain term translated the same way everywhere): translate with the related objects in context, or build up a glossary as you go, rather than translating each object in isolation.

## DHIS2 schema concepts (what the script's --minimize is doing)

Every property in a schema has flags that explain how it's stored:

| Flag | Meaning |
|---|---|
| `owner=true` | The property "belongs" to this object (vs. being a back-reference from another object) |
| `persisted=true` | Stored in this object's row(s); needed for round-tripping |
| `required=true` | Server rejects the object if the property is missing |
| `propertyType=REFERENCE` | Single object reference; only the `id` is needed for re-import |
| `propertyType=COLLECTION` + `embeddedObject=true` | Owned sub-objects (e.g. `dashboardItems` inside a dashboard) |
| `propertyType=COLLECTION` + `embeddedObject=false` | Plain references (e.g. a program's `organisationUnits`) |

The minimize step keeps `owner=true && persisted=true` properties (minus bookkeeping like `lastUpdated`/`createdBy`) and reduces references to `{id: ...}`. That's enough to recreate the object on import while removing 30–60% of the bytes and most of the noise.

## Adapting the import script across versions

If you're importing into 2.40 or 2.43+ and the dependency order or type list differs:

- **New types**: edit `ORDER` in `scripts/import_metadata.py`. Unknown types are appended at the end, which is OK if they have no dependents but problematic if they do.
- **Renamed types**: the splitter writes whatever plural name appears in the source. If 2.43 renames `eventVisualizations` to something else, the file will be named differently — adjust `ORDER` accordingly.
- **Removed types**: just leave them out of the source dir; the importer skips missing files.
- **EMBEDDED_OWNED set**: this list (currently `mapViews`) holds types that are owned by a parent and shouldn't be imported standalone (UID collision). If you find another such type causing conflicts, add it.

When in doubt: run `import_metadata.py --dry-run` first to see the order, then run with `--passes 2` to resolve any forward-reference cycles.
