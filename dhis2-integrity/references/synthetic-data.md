# Populating synthetic data so the cleanup is realistic

**Why this matters.** An empty (metadata-only) database makes most of the methodology vacuous:
- the store-layer guards **never fire** — `E1120` ("would make data values inaccessible") and `E4030`
  ("associated with DataValueChangelog/COC/TrackerEvent") only trigger when data exists, so you never
  exercise the merge-vs-SQL decision that is the whole point of a structural fix;
- **output verification has nothing to diff** (§verification) — no analytics, no form values, no
  program-indicator numbers;
- data-dependent checks (`*_no_data`, disjoint-with-data) can't reproduce.

So if the target has no data values, **ask the user whether to populate a synthetic fixture** (fold it
into the batched decisions round when timing allows) — it isn't always wanted (a quick advisory triage,
a metadata subset, time pressure), but proceeding empty must be a *stated* limitation in the deliverable
("fixes not exercised against data"), not a silent one. If populating: **generate after import and
before the "original" snapshot** — the snapshot then becomes the true A/B control. Bundled:
`scripts/gen_synthetic_data.py` (seeded/deterministic, so replays reproduce the same fixture).

## What it generates
- **Aggregate** (`/api/dataValueSets`): every aggregate DE gets a value under **each of its category
  option combos** (crucially incl. disjoint/foreign COCs, so structural COC fixes become real
  data-migration exercises) × a small sample of org-units × periods. Values respect `valueType` and
  `optionSet` (real option codes; empty option sets → skipped).
- **Tracker** (`/api/tracker`): a handful of enrolled TEIs per program with events across all stages,
  filling program-stage data elements + non-unique/non-generated attributes.

## Prerequisites & gotchas (these bite)
1. **`E7617` "Organisation unit not in hierarchy of current user"** rejects ALL data import — even for a
   superuser with `ALL`, because data-entry OU scope is separate from authority, and the broker's
   `local_admin` starts with **0 capture OUs**. Fix before generating:
   ```
   roots = GET /api/organisationUnits?filter=level:eq:1
   PATCH /api/users/{me} (json-patch): add roots to /organisationUnits, /dataViewOrganisationUnits,
                                       /teiSearchOrganisationUnits
   ```
   (Infrastructure fix to the *generating* account — not a change to the case metadata.)
2. **Tracker import runs the program-rule engine**, which ASSIGNs/validates and rejects arbitrary values:
   `E1309`/`E1307` ("must match the calculated value"), `E1300` (validation rules e.g. phone format),
   `E1125` (not a valid option code — incl. **empty option sets**, themselves an integrity issue). The
   generator handles this by: skipping DEs/TEAs that are ASSIGN/validation/mandatory **rule targets**
   (derive from `programRuleActions`), skipping empty-option-set fields, and posting with
   **`validationMode=SKIP&skipSideEffects=true`**.
3. **Scale caveat.** On very large instances (100k+ OUs) the generator's own large metadata fetches
   (a multi-MB `dataSets` export) and bulk `dataValueSets` POSTs can OOM Tomcat on a shared host. Turn the
   knobs DOWN (`--ous 1 --periods 1 --chunk 500`) or accept that a metadata-only run is the realistic
   ceiling there and document it (do the data-dependent methodology on a smaller case instead).

## Usage
```bash
# after import, with local_admin OU scope fixed:
DHIS2_BASE_URL=... DHIS2_AUTH=user:pass python scripts/gen_synthetic_data.py \
    --ous 3 --periods 2 --teis 5 --chunk 8000
# tracker only (e.g. after fixing rule-target skips): --skip-aggregate ; aggregate only: --skip-tracker
# RULE_TARGETS=<path to {"de":[...],"tea":[...]}> lets it skip rule-ASSIGN fields (else derives none)
```
Then take the control snapshot. Confirm it landed with a direct DB count
(`SELECT count(*) FROM datavalue;`) — a `dataValueSets` reply of `ignored:N` (not `imported`) usually
means the E7617 OU-scope prerequisite wasn't met.
