# Testing indicators and program indicators

How to prove an indicator or program indicator computes what you intend, by running it against known data on a disposable instance and comparing analytics output to independently-computed expected values. For the definitions themselves, see `aggregate-indicators.md` and `program-indicators.md`.

## The principle: verify two independent ways

A worthwhile test answers two separate questions, with two independent calculations:

1. **Engine check** — does analytics evaluate the expression as written? Re-evaluate the indicator's own numerator/denominator (or program-indicator expression/filter) over the known input and compare to analytics.
2. **Intent check** — does the expression match what it's supposed to mean? Compute the value from the raw data using the definition in words, classifying data from metadata (sex from a category option, "usable" from a COC name, a status from an option code) rather than from the expression's own token list.

If you only do (1), a wrong data-element or COC reference passes silently — your prediction and analytics share the same mistake. The gap between (1) and (2) is where real definition bugs live. Report disagreements differently: (1)≠analytics is a setup/engine problem; (1)≠(2) is a definition problem.

Second enabling trick: feed **distinct, recorded input values** per data cell, not constants. With all cells equal, a reference to the wrong cell still sums to the right number; distinct values make a wrong reference produce a detectably wrong result.

## Test environment

Always test on a fresh, empty instance — never a real or shared one. The method needs full control over org units, data, and analytics generation: a clean hierarchy, only the metadata under test, known values, analytics run over the whole instance. A populated instance contaminates every result with existing data, org units, sharing, and prior analytics. Spin up a throwaway instance, run the test, discard it.

**Getting a throwaway instance.** If the `dhis2-instances` skill is available (it requires `DHIS2_BROKER_URL`/`DHIS2_BROKER_TOKEN`, typically set inside an agent sandbox), use it: create an instance with a `version` and **no seed** for an empty database, wait for the job to succeed, and use the `devnet_url` it returns as `BASE`. Reset or delete it when done. If that skill is not available, ask the user how to get a disposable instance — do not invent a Docker setup yourself.

Match the version to the instance the indicators will eventually live on. The database must start **empty** — no demo seed, no leftovers from a previous run.

Config block to reuse (`BASE` is the `devnet_url` from `dhis2-instances`, or whatever the user provides):

```
BASE=http://localhost:8080
AUTH='admin:district'
```

Record the version first — a few endpoints differ across releases:

```bash
curl -sg -u "$AUTH" "$BASE/api/system/info.json" | jq '{version,revision,calendar}'
```

Save every request and response under `./test-artifacts/`. Validate expressions via the description endpoints (see SKILL.md) before touching data.

## What metadata to provide

Hand the test a self-contained bundle so every reference resolves on the empty instance. Prefer a **dependency export** over a hand-collected file — it pulls the object plus everything it transitively needs.

**Aggregate indicators.** Provide a dependency export of the dataset(s) whose data elements the expressions reference (`GET /api/dataSets/{id}/metadata.json?download=true`) — this bundles data elements, category metadata, COCs, option sets, legend sets, and the form. Provide the **indicators and their indicator types separately**, since indicators are not dependencies of a dataset.

**Program indicators.** Provide a dependency export of the program (`GET /api/programs/{id}/metadata.json?download=true`) — usually self-sufficient: program stages, tracked entity type, attributes, stage data elements, option sets, program rules, and the program indicators belonging to the program. Provide a tracked entity type or standalone program indicators separately only if they aren't carried by the program export. (Program indicators use no indicator type.)

Dependency exports carry `createdBy`/`user`/`sharing` references to users that don't exist on a fresh instance, so import with `skipSharing=true`; they don't include org-unit assignments (you create your own); and they include category option combos, so run `categoryOptionComboUpdate` after import.

### Tooling: the dhis2-metadata skill

Use the `dhis2-metadata` skill to handle the import cleanly. It is metadata-only — it does not enter data, import tracker data, run analytics, or verify results.

- `fetch_metadata.py --types … --filter … --out ./export` pulls types from a reference instance (or use the dependency-export endpoints above).
- `transform_metadata.py bundle.json --split-dir ./split --unshare --delocalize` writes one file per type, cleaned of sharing/translations. Use `--unshare --delocalize` for a fresh-instance import; **skip `--minimize`** (it can strip a rarely-used owned property and break the round-trip — that flag is for analysis).
- `import_metadata.py --src ./split --url $BASE --auth $AUTH --passes 2` POSTs each type in dependency order. This is what removes the manual ordering gotchas: `indicatorTypes` before `indicators`, `trackedEntityTypes`/`programStages` before `programIndicators`, `categoryOptionCombos` imported with their UIDs, and a second pass resolves forward refs (programs ↔ program rules). Sharing was already stripped by `--unshare`; when importing a raw export instead, add `--skip-sharing`.

After import, still run `POST /api/maintenance/categoryOptionComboUpdate`, and still confirm indicator-type **factors** — the skill moves objects, it doesn't check that Percentage really is ×100. A single-bundle metadata POST is a fine alternative for small, self-contained exports.

## Metadata pre-flight lint (before any data)

A whole class of defect is invisible to expression-level checks because both verification layers read the *expression*, not the data element's configuration. The worst example: a data element shipped with `aggregationType: COUNT` instead of `SUM`. The expression looks correct, Layer A and Layer B both assume the element sums, and the bug only surfaces as wildly wrong analytics. Catch it with a quick lint before entering data. For every data element referenced by the indicators under test, assert:

- `aggregationType` matches the indicator's intent (almost always `SUM` for counts/enrolment; an element silently set to `COUNT`, `AVERAGE`, or `LAST` breaks the result).
- `valueType` is numeric and `domainType == AGGREGATE` (a text/option-set element can't feed an aggregate indicator at all).
- `zeroIsSignificant == true` wherever a reported 0 must be distinguishable from "not reported" (see zero handling below).
- every explicit `#{de.coc}` token's COC belongs to that DE's **current** `categoryCombo` — a COC from a legacy or different combo (often named `… DELETE`) is accepted by the importer but holds no data, so the term silently contributes nothing.

This is a ten-line script against `GET /api/dataElements/<uid>.json?fields=aggregationType,valueType,domainType,zeroIsSignificant,categoryCombo[categoryOptionCombos]`. Run it first; it is the cheapest bug you will ever catch.

---

# Part A — Aggregate indicators

### 1. Metadata and setup

1. Import the metadata (dataset dependency export + indicators and their `indicatorTypes`) with `import_metadata.py` so types load before the objects that reference them. Run `categoryOptionComboUpdate`. Confirm indicator-type factors (Number 1, Percentage 100).
2. Create the **org unit hierarchy** with at least two siblings under one parent (to test upward aggregation). Assign the root to the importing user's org units.
3. Assign the **dataset to the leaf org units** (data is entered at leaves) and confirm its `periodType`.
4. Fetch the **default** category option combo UID — used for default-combo elements and as `attributeOptionCombo`.

### 2. Generate and import data

Build the cell map from metadata: for each referenced data element, read its `categoryCombo` and list that combo's COCs. Populate every referenced (dataElement, COC) cell with a **distinct** integer. Include deliberate zeros, a blank cell, and one case where a denominator element is entirely blank (to test divide-by-zero). Save the payload — it is the single source of truth for expected values.

Before importing, clear two traps:

- **Closed periods.** If the dataset ships with `dataInputPeriods` or `expiryDays`, the period may be closed and *every* value is rejected with `E7643 Period is not open for this data set`. Either add `force=true` to the import (superuser only) or strip `dataInputPeriods`/`expiryDays` from the dataset first.
- **Zeros.** Two independent mechanisms eat zeros, which makes the "numerator 0 → 0.0 in analytics" edge case untestable as shipped: the importer **silently drops** a `0` for any DE with `zeroIsSignificant=false` (not stored, not counted in `importCount`), and analytics excludes even stored zeros unless the system setting `keyIncludeZeroValuesInAnalytics` is on. If you need zeros to appear, do both *before* generating data: set `zeroIsSignificant=true` on the relevant DEs and `POST /api/systemSettings/keyIncludeZeroValuesInAnalytics?value=true`.

```bash
curl -sg -u "$AUTH" -H 'Content-Type: application/json' -X POST \
  "$BASE/api/dataValueSets?importStrategy=CREATE_AND_UPDATE&force=true" \
  --data-binary @datavalues.json | jq '.status,.importCount,.conflicts'
```

Do not rely on `ignored == 0` and `conflicts == empty` as the import check — silently-dropped zeros appear in neither. The correct assertion is **`imported + updated == number of dataValues in the payload`**. A short count is a dropped value, and a dropped value invalidates the comparison.

Two more import facts that bite:

- **The importer accepts a data value whose COC does not belong to the DE's category combo.** Wrong-COC values import cleanly and are picked up by whatever indicator references that COC. This is exactly why the cell map must come from server metadata, never from the indicator expressions or a hardcoded list.
- **UID-less metadata imports duplicate on re-run.** A new-indicators file with no `id` fields matches nothing on a second import and creates a fresh duplicate set. Assign UIDs first, or treat that import as single-shot.

### 3. Run analytics

```bash
curl -sg -u "$AUTH" -X POST "$BASE/api/resourceTables/analytics?lastYears=5&skipResourceTables=false"
curl -sg -u "$AUTH" "$BASE/api/system/tasks/ANALYTICS_TABLE.json"   # poll until completed:true
```

`lastYears` must reach back to your test period. Re-run analytics after every data change.

### 4. Query and compare

```bash
curl -sg -u "$AUTH" \
 "$BASE/api/analytics.json?dimension=dx:<IND_UIDS>&dimension=ou:<leaves>;<parents>&dimension=pe:<period>&outputIdScheme=UID"
```

Build the comparison table: indicator × org unit × period, with both expected columns (engine, intent) and the analytics value. At a parent, a count = sum of children; a percentage = Σnumerator / Σdenominator × factor, **never** the average of child percentages — test this explicitly.

Separate the **value** comparison from the **rounding** comparison, or you will generate false disagreements when an indicator's stored `decimals` differ from the spec. Layer A (engine) should round with the indicator's **stored** decimals — that is what analytics does; Layer B (intent) rounds with the **spec** decimals; compare A vs B **unrounded**, and report any rounding-config mismatch as its own finding rather than as a calculation failure.

---

# Part B — Program indicators

### 1. Metadata and setup

Import the program dependency export with `import_metadata.py` (dependency order handles `trackedEntityTypes`/`programStages` before `programIndicators`; `--passes 2` resolves the program ↔ program-rule cycle; `--skip-sharing` unless the export was run through `--unshare`). Create the org unit hierarchy and assign the program to org units and the importing user. Validate expression and filter via the description endpoints first.

### 2. Generate test data

Create tracker data with the modern endpoint (async by default; force sync to read results immediately). Registration program: `trackedEntities → enrollments → events → dataValues`, with attributes at the TEI/enrollment level. Event program: just events.

```bash
curl -sg -u "$AUTH" -H 'Content-Type: application/json' -X POST \
  "$BASE/api/tracker?async=false&importStrategy=CREATE_AND_UPDATE" \
  --data-binary @tracker.json | jq '.status,.stats,.validationReport.errorReports'
```

Design units deliberately: some that should pass the filter and some that shouldn't (value above/below a threshold, status equal to a specific option, element present vs absent); distinct values so a COUNT/SUM/AVERAGE mistake shows; `occurredAt`/`enrolledAt` dates on both sides of a period boundary; a unit with the relevant element blank (null handling). Record which units you expect counted, in which period, at which org unit — that is your intent layer.

### 3. Run analytics (include events)

Program-indicator results live in the event/enrollment analytics tables, so do **not** skip events:

```bash
curl -sg -u "$AUTH" -X POST "$BASE/api/resourceTables/analytics?lastYears=5"
curl -sg -u "$AUTH" "$BASE/api/system/tasks/ANALYTICS_TABLE.json"
```

### 4. Query and compare

Aggregated program-indicator values appear in the `dx` dimension:

```bash
curl -sg -u "$AUTH" "$BASE/api/analytics.json?dimension=dx:<PI_UID>&dimension=ou:<ous>&dimension=pe:<periods>"
```

Drop to line level to check the units behind a number:

```bash
curl -sg -u "$AUTH" "$BASE/api/analytics/events/query/<PROGRAM>.json?dimension=pe:<period>&dimension=ou:<ou>&stage=<stage>"
# or aggregated: /api/analytics/enrollments/aggregate/<PROGRAM>.json?...
```

Compare the aggregated value, your engine-layer recomputation of expression+filter over the events, and your intent-layer count of expected units. Disagreement at line level usually points at the filter or the period boundary.

---

# Cross-cutting techniques

- **Validate expressions before data** via the description endpoints — catches bad references and syntax in seconds.
- **Distinct, recorded inputs** — constants hide wrong-reference bugs.
- **Edge cases every time** — zero numerator, zero/blank denominator (→ no value, not an error), all-blank unit, value on a threshold, dates on a period boundary.
- **Aggregate up the hierarchy** — counts sum; percentages recombine as Σnum/Σden; test both.
- **Aggregate across time** — respect each element's aggregation type; query a built-up period, not only one.
- **Re-run analytics after every change** and confirm the job completed before reading. Stale analytics is the most common false failure.
- **Bust the server-side analytics cache when verifying a metadata change.** `/api/analytics` responses are cached by identical URL for as long as the system setting `keyCacheStrategy` says (default `CACHE_1_MINUTE`; the Sierra Leone demo seeds use `CACHE_TWO_WEEKS`, served as `Cache-Control: public, max-age=1209600`), so after PATCHing an indicator/PI, re-querying the same URL returns the stale pre-change result — which looks exactly like "my fix didn't work". Append a fresh `_cb=<random>` parameter to every verification query, or verify via a fresh throwaway PI.
- **Throwaway-PI probing isolates the failure class cheaply.** To separate "reference doesn't resolve" from "filter logic wrong" from "no matching data", create obviously-named temp PIs (`ZZZTEMP …`) that A/B-test one candidate at a time — e.g. a bare `d2:hasValue(#{stage.de})` filter to prove the reference resolves at all, then a loosened threshold. Minutes per probe; delete the temps when done.
- **Seeded demos: check data recency before using relative periods.** On a *seeded* instance (as opposed to this method's self-entered data), check `max(occurreddate)` / the newest period first — demo seeds are snapshots that age at different rates (the SL v40 seed's tracker data ends Aug 2024 while the v43 seed is current), and `LAST_12_MONTHS` silently returns nothing on a stale seed.
- **Independent expected values, scripted** — read the saved input payload and metadata; produce the engine layer (evaluate the stored expression) and the intent layer (classify from metadata) separately; diff both against analytics.
- **Reconcile against raw data** — `GET /api/dataValueSets` (aggregate) or the events/tracker API (program) confirms what landed, independent of analytics.
- **Drop to the database when analytics is mysterious.** If a value is inexplicable, querying the `analytics_*` tables and `datavalue` directly is the fastest way to root-cause it (e.g. confirming whether a zero was stored at all). Have read access to the test database, not just the API — instances created via the `dhis2-instances` skill (if available) expose their PostgreSQL directly (`devnet_db`, database `dhis2`, user/password `dhis`/`dhis`).
- **Remember the test mutates the instance.** Beyond imported data, you may change DE `aggregationType`/`zeroIsSignificant` and system settings during setup. That is another reason the instance must be fresh and disposable — never carry these changes into a second run or a real system.

## API notes (version-sensitive — confirm against dhis2-docs)

- Metadata import stats for a single-type import live under `.response.stats`, not `.stats`; a top-level `jq '.stats'` prints null.
- Poll a *specific* analytics job via `/api/system/tasks/ANALYTICS_TABLE/<jobId>` (the `id` from the trigger response) rather than slicing the whole task list.
- `GET /api/system/id.json?limit=N` returns valid server-generated UIDs for the org units (and anything else) you create.
- Changing a single property like `aggregationType` via JSON-patch (`PATCH /api/dataElements/<uid>`) needs `Content-Type: application/json-patch+json`; plain `application/json` returns 400.

# Checklist

- [ ] Fresh, empty, disposable instance — never a real or shared one.
- [ ] Metadata supplied as dependency exports; transformed and imported in dependency order with sharing stripped (dhis2-metadata skill, `--unshare --delocalize`, no `--minimize`; or `--skip-sharing` on import).
- [ ] Version recorded; expressions validated via description endpoints.
- [ ] Indicator types created with correct factors before importing indicators (aggregate only).
- [ ] Org unit hierarchy with ≥2 siblings under a shared parent; dataset/program assigned to leaves and to the importing user.
- [ ] Default COC fetched and used where needed.
- [ ] Pre-flight lint passed on every referenced DE (aggregationType, valueType/domainType, zeroIsSignificant, COCs belong to the current combo).
- [ ] If testing zeros: `zeroIsSignificant=true` on those DEs and `keyIncludeZeroValuesInAnalytics=true` set before generating data.
- [ ] Closed periods handled (`force=true` or stripped `dataInputPeriods`/`expiryDays`).
- [ ] Distinct per-cell/per-unit input values, saved; zeros, blanks, threshold and boundary cases included.
- [ ] Data import verified by `imported + updated == payload size` (not just conflicts/ignored).
- [ ] UID-less metadata imported only once (no duplicate set created).
- [ ] Analytics run (events included for program indicators) and completed.
- [ ] Results queried at leaf and parent, single and multi-period.
- [ ] Expected computed two ways (engine, intent); both diffed against analytics.
- [ ] Failures classified: setup/engine vs definition; definition fixes fed back into the metadata.
- [ ] Every payload, response, and curl command saved for replication.
