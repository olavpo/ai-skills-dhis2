# Data formats

Every file passed between phases. All live in the run's `work_dir` unless noted. Names are relative to it; scripts are relative to the skill (`scripts/...`). `--tag T` writes `name-T.json` (`common.tagged`). UIDs below are placeholders.

## Run configuration: `pireview.json`

One file per run (default `./pireview.json`, or `--config` / `$PIREVIEW_CONFIG`). Paths are relative to the config file. Read by every script through `scripts/lib/config.py`. Credentials never go in it: `DHIS2_URL` plus `DHIS2_TOKEN`, or `DHIS2_USER` + `DHIS2_PASSWORD`.

```json
{"title": "Child Programme", "metadata": "export/metadata.json", "program": "<programUid>", "work_dir": "out",
 "test_period": "2026Q1", "org_units": {"root": "TstRoot0001", "facilities": ["TstFacA0001", "TstFacB0001"], "scenario_prefix": "TstScn"},
 "main_stage": null, "field_resolution": {"#{<stage>.<de>}": 0.001}, "dhis2_version": "2.43.2", "exported": "2026-10-06",
 "test_instance": true, "seed": 42, "active_name_pattern": null, "topics_extra": ["topics-extra.json"],
 "fixes_file": "out/fixes-explicit.json", "notes": "notes.json"}
```

| Key | Default | Used by |
|---|---|---|
| `metadata` | required | everything: dependency export of the program (+ `dataSets` for the dictionary) |
| `program` | the only program in the export | everything |
| `test_period` | `<this year>Q1` (must be a quarter) | gen, query, scen, probes |
| `org_units.*` | `TstRoot0001`, `TstFacA0001`/`TstFacB0001`, `TstScn` | import_data, run_e2e, scen |
| `main_stage` | stage most referenced by PIs | gen (noise) |
| `field_resolution` | 1 for INTEGER types, else 0.001 | lint (band gaps/overlaps), gen, scen |
| `test_instance` | `false`: every write refuses | import_data, probe, run_e2e, probes, make_viz |
| `seed` | 42 (e2e witness uses seed + 1000) | gen |
| `active_name_pattern` | lint's built-in regex | lint (`active-name-*` checks) |
| `topics_extra` | `[]`; `--topics-extra` overrides | build_review |
| `fixes_file` | `<work_dir>/fixes-explicit.json`; `--fixes` overrides | build_fixes |
| `notes` | none | build_fix_review (downstream impact per fix group) |

## Phase 1: validate (`scripts/validate/`)

| File | Writer | Readers | Shape |
|---|---|---|---|
| `findings.json` | lint.py | assemble, build_review | `[{"severity": "ERROR\|WARN\|INFO", "check": "<id>", "id": "<PI or mapping uid>", "name": "<PI name> \| mapping: <name>", "detail"}]`; check ids listed in lint.py docstring |
| `syntax.json` | syntax.py | assemble | `[{"id", "name", "where": "expression\|filter\|option <name>", "text", "status": "OK\|ERROR", "message", "description", "ok"}]` |
| `tracker.json` | gen.py | import_data | `POST /api/tracker` payload: `{"trackedEntities": [...]}` (nested enrollments/events), or `{"events": [...]}` for an event program |
| `truth.json` | gen.py | compare, build_review (count only) | records as the evaluator reads them, below |
| `unsat.json` | gen.py | assemble | `[[piUid, reason]]`: filters the generator could not satisfy |
| `import.json` | import_data.py | (human) | `{"orgUnits": {"created", "existing"}, "programOrgUnits": {"added"}, "userOrgUnits": {"changed", "http"}, "dataOutputPeriodTypes": {"set"\|"existing", "http"}, "imports": [{"file", "items", "droppedValues", "stats", "http", "errors"}], "analytics": {"seconds", "message", "level", "lastYears"}}` |
| `analytics.json` | query.py | compare, assemble, build_fix_review (`-before`/`-after`) | `{"<piUid>": {"total": 12.0, "total_err": null, "pe": "2026Q1", "ou": "<uid>", "cells": {"<optA>\|<optB>": 3.0} \| null, "cells_err": null, "cats": ["<categoryUid>"]}}`; cell keys join option uids in `cats` order |
| `compare.json` | compare.py | assemble, build_review, e2e/check | list, below |
| `manual-findings.json` | analyst, by hand | assemble (`--manual`) | below |
| `probes.jsonl` | probe.py | (human) | one line per probe: filter, boundaries, value or error |

The `-fix` variants (`tracker-fix.json`, `truth-fix.json`, `unsat-fix.json`) are the e2e witness data for the proposed filters (`run_e2e.py witness` runs gen.py with `--tag fix`).

**`truth.json`** record (one per person; event programs: one per event, `te` = `enrollment` = event uid, `attrs` empty):
```json
{"te": "<uid>", "enrollment": "<uid>", "ou": "<uid>", "enrolledAt": "2026-01-14", "attrs": {"<teaUid>": "Female"},
 "events": [{"event": "<uid>", "stage": "<stageUid>", "date": "2026-02-10", "values": {"<deUid>": "value"}}],
 "witness_for": "<piUid> | null (noise) | control:<piUid> | scenario:<id>"}
```

**`compare.json`** (one record per PI in `analytics.json`):
```json
{"id", "name", "code", "type", "agg", "pe", "status": "compared|parse|unsupported|analytics-error", "detail": "(when not compared)",
 "expected_total": 4, "expected_total_ignoring_disagg": 5, "expected_total_in_cells": 4, "expected_total_any_match": 5,
 "analytics_total": 4, "total_ok": true, "units": 5,
 "cells_ok": true, "cell_diffs": {"<key>": [expected, analytics]}, "n_cell_diffs": 0, "units_in_no_cell": 1,
 "units_in_several_cells": 0, "sum_cells_analytics": 4, "cells_sum_equals_total": true, "cells_err": "(instead of the cell fields)"}
```
`expected_total` now applies the drop rules: EVENT PIs drop records outside exactly one option per mapped category (C1); ENROLLMENT PIs keep them (C2). `expected_total_ignoring_disagg` = every record passing the filter; `expected_total_in_cells` = only records in exactly one option of every category. Cell fields only for PIs with categories; `cell_diffs` keeps the first 6. Older readers (build_review's `total-mismatch` selector, e2e/check.py) still pick `expected_total_ignoring_disagg` for ENROLLMENT PIs; with the new `expected_total` that is the same number.

**`manual-findings.json`**:
```json
{"findings": [{"id": "<pi>", "status": "FAILS|ALWAYS_ZERO|WRONG_VALUE|REVIEW", "category": "reversed-period-window", "detail": "why"}],
 "suppress": [{"id": "<pi>", "category": "<category> | *", "why": "read by hand: ..."}]}
```

**assemble.py outputs**:
- `pi-status.csv`: `id, code, name, analyticsType, aggregationType, status, issue_categories, analytics_total_in_test, engine_check` (`match|mismatch|unsupported|parse|analytics-error|''`). Status worst first: FAILS > ALWAYS_ZERO > WRONG_VALUE > REVIEW > OK.
- `pi-findings.csv`: `id, code, name, severity, category, detail`, sorted by severity (severity = status level). Categories: lint check ids, `uses-<check>` (PI uses a flagged mapping), `server-rejects-<where>`, `analytics-error[-disaggregated]`, `test:<observation>`, manual categories.
- `summary.json`: `{"status": {"<pi>": "WRONG_VALUE"}, "counts": {"FAILS": 3, …, "OK": 900}, "categories": {"<cat>": n}, "mappings": {"total", "withErrors": [uid], "unused": [uid]}}`.
- `PI-VALIDATION.md`: skeleton with counts and `TODO` markers; written only when absent or with `--overwrite-md`.

## Phase 2: review (`scripts/review/`)

**`topics.json`** (catalogue, next to the script) and the **topics-extra** file (config `topics_extra` or `--topics-extra`, same shape): `{"topics": [{"key", "severity", "kind": "pi|mapping", "checks": [category], "match"/"notMatch": "regex on detail", "compare": ["total-mismatch|cells-mismatch|units-in-no-cell|units-in-several-cells"], "nameMismatch": true, "checkEach": true, "onlyUnexplained": true, "title", "confidence", "confidenceWhy", "what", "evidence", "fix"}]}`. Extra-file only: `"drop": true`, `"after": "<key>"`, `"splitFrom": ["<key>"]`, `"items": [{"id": "<uid or code>", "details": [...]}]`, `"count"`.

**`review.json`** (build_review.py; read by the meeting page, write_decisions, build_fixes, build_dictionary, build_fix_review):
```json
{"cfg": {"title", "program", "programId", "dhis2Version", "exported", "piCount", "mappingCount", "persons", "engineCheck", "built", "dataVersion"},
 "names": {"<uid>": "name"}, "total": 2100, "mappings": 80, "counts": {"OK": 1500, "WRONG_VALUE": 300},
 "index": [{"id", "code", "name", "label", "status", "topics": ["<key>"]}],
 "topics": [{"key", "severity", "kind", "checks", "title", "confidence", "confidenceWhy", "what", "evidence", "fix", "checkEach": false, "count": 75,
   "items": [{"id", "code", "name", "label", "type", "agg", "expr", "filter", "details": ["..."], "test": {"analytics": 0, "filterMatches": 3} | null}]}]}
```
Mapping topic items: `{"id", "name", "category", "piCount", "pis": [code], "options": [{"name", "filter"}], "issues": [...]}`. Uncovered findings land in topics `other` / `other-mapping` (`checkEach: true`). `dataVersion` = hash of topic keys and item ids.

**Meeting decisions**: artifact db collection `decisions`, one document per topic key:
```json
{"verdict": "agree|each|domain|park|disagree", "note": "free text", "by": "<user id> | null", "byName": "typed name (offline) | null",
 "at": "ISO time", "excluded": ["<item uid>"], "items": {"<item uid>": "fix|ok|rename|domain"}}
```
`excluded` = items marked "not affected" (only offered when the verdict is not `each`); `items` = per-item marks under "Check each item".

Accepted decisions file shapes:
- write_decisions.py (`--decisions`, repeatable, default `decisions.json`; newest `at` wins per topic): the page's export `{"format": "pireview-meeting-decisions", "dataVersion", "program", "reviewer", "exportedAt", "decisions": {key: doc}}`; a plain `{key: doc}`; a db dump `[{"id"|"doc_id"|"key": key, "data": doc}]`; compact `{key: [verdict, note, at]}`.
- build_fixes.py (`--decisions`, default `decisions.json`, else `decisions-snapshot.json`): `{key: doc}` or `{"decisions": {...}}`, compact `{key: [verdict, note]}`, or a list of docs carrying `key`/`id` (an artifact db dump `[{"id", "data": doc}]` is unwrapped). Verdicts `agree|agreed|fix` → agree; `each|check|check-each|individual` → check each. Items to change in a check-each topic or a confirm group: `confirmed: [uid]` plus the items marked `fix` in `items`; items marked `ok`, `rename` or `domain` are treated as excluded.

**`PI-REVIEW-DECISIONS.md`** (write_decisions.py): summary, decision table, follow-ups, per-topic detail, per-item checklists for "Check each item" topics; `--link`, `--meeting`, `--clarifications` add header links, meeting line and a hand-written section.

**`dictionary.json`** (build_dictionary.py; read by assemble_dict.py and prep_desc.py):
```json
{"title", "program", "exported", "version", "hasStatus", "cats": {"<catUid>": {"name", "options": [...]}}, "names": {"<uid>": "name"},
 "datasets": [{"id", "name", "period", "count"}],
 "rows": [{"id", "code", "name", "label", "type", "agg", "headline", "period", "conditions": ["..."], "parseError",
   "disagg": [{"category", "mapping", "options": [...]}], "boundaryTargets", "expr", "filter", "description", "nameMismatch": null,
   "target": {"id", "code", "name", "combo", "match": "exact|case-insensitive"} | null, "piCombo", "attrCombo", "comboMatch", "attrMatch",
   "datasets": [{"id", "name", "period"}],
   "targets": [{"ds", "dsName", "deCombo", "effCombo", "overridden", "dsAttr", "piCats", "hmisCats", "piAttr", "hmisAttr", "ccMatch", "acMatch"}],
   "status", "issues": ["<topic key>"]}]}
```
Dictionary descriptions: `--desc-batches N` writes `dict-desc/in/batchK.json` (`{"instructions", "pis": [{id, code, name, type, aggregation, headline, period, conditions, breakdown, filter, expression, currentDescription}]}`); sub-agents write `dict-desc/out/batchK.json` `{"<uid>": {"description", "nameMismatch": null | "..."}}`. assemble_dict.py merges them (and dedupes mappings into `"mappings": {"m0": {name, options}}`) into `pi-dictionary.html`. The same out files can feed `build_review.py --descriptions` (name-vs-definition topic).

## Phase 3: fix (`scripts/fix/`)

**`fixes-explicit.json`** (by hand; config `fixes_file`). Item refs are UIDs or PI codes; mapping refs UIDs or mapping names.
```json
{"topics":  {"<group>": ["<topic key>"]},
 "groups":  {"<group>": {"enabled": true, "items": ["<ref>"], "scan": false, "title", "change", "why", "topic": "<key>",
                         "after": "<group>", "verified": "...", "periodType": "Monthly", "options": ["option name"]}},
 "explicit": [{"group", "items": ["<ref>"], "field": "filter", "find": "old text | null", "replace": "new", "justification": "..."},
              {"group", "mapping": "<ref>", "option": "<option name or id>", "find": "old | null", "replace": "new", "justification": "..."}],
 "hold": {"<ref>": "reason left out of every group"}, "skip_mappings": {"<ref>": "reason"},
 "not_proposed": [{"topic", "items": ["<ref>"], "reason", "decision"}]}
```
`topics` replaces a group's default topic links. `enabled: true` runs a group without an agreed topic, `false` disables it. `scan` takes every PI/mapping as a candidate (not for confirm groups). Confirm groups (`contradiction`, `periodend`, `numnull`, `mapprefix`) only change items listed under `items` or a decision's `confirmed`. An explicit entry with an unknown group defines a new group. `find: null` replaces the whole field; every `find` must match exactly once (whitespace-insensitive). Extra group keys (`periodType`, `options`) reach the generator as `ctx['opts']`.

**`fixes.json`** (build_fixes.py; updated in place by build_payload.py):
```json
{"program": "<uid>",
 "groups": [{"key", "kind": "pi|mapping", "topic", "topicNum", "title", "change", "why", "identical", "verified", "count"}],
 "pis": [{"id", "code", "name", "type", "groups": ["<group>"], "changes": [{"group", "field", "note"}],
          "before": {exported PI}, "after": {complete PI}, "concern": null,
          "open": [{"topic", "num", "title"}], "descBefore", "descAfter"}],
 "mappings": [{"id", "name", "category", "groups", "changes": [{"group", "option", "before", "after", "note"}],
               "before": {mapping}, "after": {mapping}, "pis": ["<code>"]}],
 "excluded": [{"topic", "group": "<group> | null", "items": [uid], "codes": [...], "reason", "decision", "resolvedBy": ["<mapping name>"]}],
 "errors": [["<item>", "<group>", "message"]], "skippedAgeChanges": [["<mapping>", "<option>", "before", "would-be", "reason"]]}
```
`excluded` = not proposed: pattern did not fit, unconfirmed items of confirm groups, agreed topics with no clear fix, symptoms expected to be `resolvedBy` the mapping fixes, `not_proposed` and `hold` entries. `descBefore`/`descAfter`/`concern` and the description inside `after` are set by build_payload.py.

**Descriptions**: prep_desc.py writes `desc_in/batchN.json` = `[{"id", "code", "name", "program", "analyticsType", "aggregationType", "stage", "expression", "filter", "boundaries", "disaggregatedBy", "oldExpression", "oldFilter", "oldBoundaries", "oldDescription", "changesMade"}]` (UIDs replaced by names). Sub-agents (brief: `scripts/fix/description_brief.md`) write `desc_out/batchN.json` = `{"<piUid>": {"description": "≤450 chars", "concern": null | "..."}}`. Hand overrides, same shape (or a bare string), applied last: `desc_out/zz_*.json`, then `desc_overrides.json`.

**Proposed payloads** (build_payload.py; read by syntax_check, run_e2e, the validate `--metadata` overlay):
- `proposed-programIndicators.json`: `{"programIndicators": [complete PI objects with the new description]}` → `POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL`
- `proposed-categoryMappings.patch.json` (only when a mapping changed): `[{"op": "replace", "path": "/categoryMappings", "value": [all mappings of the program]}]` → `PATCH /api/programs/<uid>`, `Content-Type: application/json-patch+json`

**`syntax-check.json`** (syntax_check.py): `{"pis": {"<code or uid>": {"expression"|"filter": {"status": "OK|ERROR", "message", "description"}}}, "mappings": {"<mapping> / <option>": {"filter", "status", "message", "description"}}}`.

## Phase 4: end to end (`scripts/e2e/`)

**`scenarios.json`** (by hand; format and a fictional example in `scripts/e2e/scenarios.example.json`):
```json
{"scenarios": [{"id": "late-visit", "group": "window+agebands", "title", "note", "pe": "2026Q1", "choose": {"<category>": "<option>"},
  "clients": [{"label": "A", "enrolled": "2025-11-02", "incident": "2025-11-02", "attributes": {"<attribute>": "value"},
               "events": [{"stage": "<stage>", "date": "2026-02-10", "values": {"<data element>": "value"}}]}],
  "expect": [{"pi": "<uid or code>", "before": "error" | 0, "after": 1, "cellsAfter": {"<category>": {"<option>": 1}},
              "probe": {"before": "<uid or probe key>", "after": "..."}}]}]}
```
Stages, fields, categories and options by uid, code or name; option values by code or name. Expected values come from the indicator's meaning, never from the evaluator.

| File | Writer | Readers | Shape |
|---|---|---|---|
| `scenario-checks.json` | scen.py | run_e2e, check, make_viz | `[{"key": "<id>[:<code>]", "scenario", "group", "pi", "code", "name", "title", "note", "ou", "pe", "persons", "expectBefore", "expectAfter", "expectCellsAfter", "probe"}]` |
| `data/scen-tracker.json` | scen.py | run_e2e load / reload-scenarios | `POST /api/tracker` payload |
| `data/scen-truth.json` | scen.py | check | truth format, `witness_for: "scenario:<id>"` |
| `data/scen-ous.json` | scen.py | run_e2e load | `{"organisationUnits": [...]}`, one per scenario under `org_units.root` |
| `probes.json` | by hand | probes.py | `{"probes": [{"key", "analyticsType", "aggregationType", "expression", "filter", "boundaries": [["ENROLLMENT_DATE", "BEFORE_END_OF_REPORTING_PERIOD"], ["EVENT_DATE", "...", -6, "Monthly"]], "copyOf": "<uid or code>"}]}` |
| `probe-ids.json` | probes.py create | run_e2e, make_viz | `{"<key>": "<uid>"}` |
| `affected-ids.json` | e2elib | gen/query/compare `--pis` | `["<uid>"]`: changed PIs + every PI using a changed mapping |
| `metadata-after.json` | e2elib | check, make_viz | the export with the proposed PIs and mapping patch applied |
| `scenario-before.json` / `-after.json` | run_e2e before/after | check | `{"<check key>": {"pe", "pi", "status": 200, "total": 1.0, "error", "cells": {"<category>": {"<option name>": 1.0} \| {"error"}}}}` |
| `truth-e2e.json` | check.py | compare (via check) | truth + truth-fix + scen-truth + `--truth` files |
| `compare-before/after.json`, `analytics-before/after.json` | compare / query via run_e2e, check | check, build_fix_review | as phase 1 |
| `results.json` | check.py | evidence, build_fix_review | below |
| `api-log.jsonl` | lib/d2.py (every write call of every phase; most GETs unlogged) | evidence, build_fix_review | `{"t", "method", "path", "status", "request" (JSON cut to 600 chars), "response" (cut to 800)}` per line |
| `e2e-steps.jsonl` | run_e2e (`log_step`) | evidence, build_fix_review | `{"step": "import programIndicators", ...summary}` per line; `read-back` carries `pis_read`, `pi_diffs` [{pi, field, sent, got}], `mapping_diffs` |
| `viz-ids.json` | make_viz.py | shots.py | `{"scenarios": "<uid>", "breakdown:<categoryUid>": "<uid>"}` |
| `shots/NN-<name>.png`, `shots/captions.json` | shots.py | evidence, build_fix_review | `{"<name>": "caption"}` |
| `E2E-TEST.md` | evidence.py (via check) | (human) | skeleton with TODO markers; not overwritten without `--force` once edited |

**`results.json`**:
```json
{"truthPersons": 1450, "truthSources": {"truth.json": 1200, "truth-fix.json": 230, "data/scen-truth.json": 20},
 "engine": {"before|after": {"queried", "errors", "errorByPi": {"<code>": "msg"}, "compared", "totalMismatch": [code], "cellSets",
            "cellMismatch": [code], "perPi": {"<uid>": {"expected", "analytics", "totalOk", "cellsOk"}}}},
 "scenarios": [{"key", "scenario", "group", "pi", "code", "title", "note", "ou", "pe", "persons", "probe",
                "expectBefore", "gotBefore", "beforeError", "expectAfter", "gotAfter", "afterError", "expectCells", "gotCells", "pass"}]}
```

## Phase 5: approve (`scripts/approve/`)

**Notes file** (config `notes`): `{"_consumer": "Downstream report pipeline", "<group key>": {"impact": "same|none|changes|action", "text"} | ["impact", "text"] | "text"}`.

**`fix-review.json`** (build_fix_review.py; embedded as DATA in both approval pages; read by export_approved, make_standalone, manual_shots):
```json
{"title", "programId", "exported", "generated", "fileStem": "child-programme", "notesLabel", "version": "<12-hex hash of pis+mappings>",
 "groups": [{"key", "title", "short", "change", "why", "identical", "topicNum", "topicTitle", "isNew", "kind", "items": [uid], "scenarios": [key], "note": {"impact", "text"}}],
 "pis": [{"id", "code", "name", "groups", "changes", "before": {slim}, "after": {slim}, "full": {complete proposed PI}, "concern", "open", "failCause",
          "test": {"before": {"value", "pe"} | {"error", "pe"}, "after": {...}, "engineOk", "cellsOk"}}],
 "mappings": [{"id", "name", "category", "groups", "changes", "pis", "afterFull", "test": {"checked", "ok"}}],
 "excluded": [...as fixes.json], "allMappings": [program mappings as exported], "names": {"<uid>": "name"},
 "test": {"instance", "period", "truthPersons", "scenarios", "engine": {"before|after": {...}}, "stillFailing": [{"cause", "codes"}],
          "steps", "apiCalls", "readBack": {"pis", "mappings"}, "syntax": {"texts", "failures"}},
 "shots": {"<name>": "data:image/png;base64,..."}, "shotCaptions": {"<name>": "caption"}}
```
`slim` = expression, filter, aggregationType, analyticsType, description, categoryMappingIds, `boundaries: [[target, type, offset, periodType]]`.

**Artifact store** (approval page, artifact mode):
- collection `approvals`, one document per fix group: `{"states": {"<itemId>": "approved|rejected"}, "meta": {"<itemId>": {"by", "at"}}, "by", "at"}` (`by` = user id; a missing state = pending)
- collection `descriptions`, one document per PI: `{"text", "by", "at"}`

**Decisions file** (standalone page "Save decisions file", `pi-fix-decisions-<name>-<date>.json`):
```json
{"format": "pireview-fix-decisions/1", "dataVersion": "<fix-review version>", "title", "reviewer": "Reviewer A", "savedAt": "ISO",
 "approvals": {"<group>": {"states": {"<itemId>": "approved|rejected"}, "meta": {"<itemId>": {"by": "Reviewer A", "at": "ISO"}}}},
 "descriptions": {"<piId>": {"text", "by", "at"}}}
```
Merge (page and export_approved.py): per (group, item) the newest `meta.at` wins; two reviewers with different non-pending states → disagreement; a different `dataVersion` loads with a warning.

**Export files** (page downloads `<fileStem>-…-<date>`; `export_approved.py --dump DIR | --decisions F…` writes the same names without the date to `export/`). `--dump` reads `DIR/approvals/<group>.json` and `DIR/descriptions/<piId>.json` (docs may be wrapped as `{"data": …}`). An item is exported only when every fix on it is approved; edited descriptions replace the proposed ones.
- `<stem>-program-indicators-approved.json`: `{"programIndicators": [...]}` approved PIs whose analytics works after the fix
- `<stem>-program-indicators-approved-still-failing.json`: approved, but `test.after.error` is set
- `<stem>-categoryMappings.patch.json`: JSON Patch with all program mappings, approved ones replaced
- `<stem>-fix-review-summary.csv` (UTF-8 with BOM, CRLF, all fields quoted): `type, code, name, overall, fix, decision, by, at, still fails after fix, description edited`; one row per item and fix.

Reviewer guide (manual_shots.py): `manual/*.png`, `manual/guide.html`, sample decisions files `manual/dec-*.json`, `PI-Fix-Approvals-reviewer-guide.pdf`.
