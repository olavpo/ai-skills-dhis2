# Data formats specific to aggregate indicators (`type: "indicators"`)

The shared formats (findings.json, syntax.json, compare.json core fields, review.json, fixes.json, fix-review.json) are in `../data-formats.md`. This file lists what the indicators type writes differently. Scripts are in `scripts/types/indicators/`.

## Config keys of the type

```json
{"type": "indicators", "metadata": "export/metadata.json", "work_dir": "out", "test_period": "2026Q1",
 "indicators": ["<uid or code>", "..."],
 "org_units": {"root": "TstRoot0001", "facilities": ["TstFacA0001", "TstFacB0001", "TstFacC0001"], "scenario_prefix": "TstScn"},
 "test_instance": true, "seed": 42}
```

`indicators`: optional scope, a list of uids/codes or one indicator group uid (the export must then hold `indicatorGroups`); default every indicator in the export. Use **three** facilities: gen.py puts the zero / missing denominator cases at the last one and the missing numerator case at the first, and OUG groups get the first half.

## Validate phase

| File | Writer | Shape |
|---|---|---|
| `datavalues.json` (`datavalues-<tag>.json`) | gen.py | `{"dataValues": [{"dataElement", "categoryOptionCombo", "attributeOptionCombo", "orgUnit", "period", "value"}]}`; import_data.py splits it by data set |
| `truth.json` (`truth-<tag>.json`) | gen.py | `[{"de", "coc", "aoc", "ou", "pe", "value": "12" \| null, "case": null \| "blank" \| "zero" \| "zero-den" \| "blank-den" \| "blank-num" \| "scenario:<id>"}]`; `null` = a deliberately blank cell (kept so a `--tag` run leaves it blank) |
| `testsetup.json` | gen.py (no tag) | `{"zeros": "significant" \| "asis", "include_zeros", "zero_significant": [de], "oug_members": {group: [ou]}, "parents": {ou: root}, "facilities", "root", "test_period", "months", "skipped": [[uid, why]], "no_data_set": [de], "stale_cocs": [[de, coc]]}` |
| `import.json` | import_data.py | `{"orgUnits", "userOrgUnits", "dataOutputPeriodTypes", "cocs": {"used", "missing", "imported", "replaced", "errors"}, "dataSets": {"assigned", "closedPeriods": [{"dataSet", "name", "dataInputPeriods", "expiryDays"}], "mode": "strip\|force"}, "orgUnitGroups", "zeroIsSignificant": {"mode", "changed": [de]}, "commentOptionSets": [[de, optionSet, http]], "cleaned", "imports": [{"file", "values", "http", "importCount", "short", "conflicts", "parts"}], "completeness": {"registrations", "http"}, "analytics"}` — every change made to the test copy is recorded here |
| `analytics.json` (`analytics-<tag>.json`) | query.py | `{"<ind>": {"total": 12.5 \| null \| "NaN", "total_err", "pe": "2026Q1", "ou": "<root>", "cells": {"<ou>\|<pe>": value}, "cells_err", "status"}}`; null = no row (no value); cells cover the root and each facility × each month and the quarter |
| `compare.json` | compare.py | core fields (`status`, `expected_total`, `analytics_total`, `total_ok`, `cells_ok`, `cell_diffs`, `n_cell_diffs`, `matches`, `units_in_no_cell` = `units_in_several_cells` = 0) plus `type` ("Per cent ×100"), `agg` ("annualized" or ""), `n_cells`, `expected_cells`; values may be null (no value) or "NaN" |
| `probes.jsonl` | probe.py | one line per probe indicator: `numerator`, `denominator`, `factor`, `annualized`, `decimals`, `skipRounding`, `ous`, `pes`, `values` (`{"<ou>/<pe>": value}` or an error string) |
| `dictionary.json` | build_dictionary.py | `{"title", "scope", "rows": [{"id", "code", "name", "type", "factor", "annualized", "decimals", "numerator": {"text", "terms", "expression"}, "denominator": {…}, "dataElements": [...], "description", "status", "findings"}]}` → `indicator-dictionary.html` |

Status rows the adapter adds in assemble (`status_rows`): `test:nan-values` (WRONG_VALUE: a NaN cell, A4), `test:reads-no-data` and `test:no-value-in-quarter` (REVIEW, only when no lint finding on the indicator or its data elements explains it).

## Fix and e2e phases

| File | Shape |
|---|---|
| `proposed-indicators.json` | `{"indicators": [complete indicator objects as exported, with the fix and the new description]}` → `POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL` |
| `proposed-dataElements.json` | `{"dataElements": [complete data element objects, changed ones only]}` → same call. On the test instance `adapter.import_aux` PATCHes only the changed fields, so the test copy keeps its own zeroIsSignificant / commentOptionSet settings. |
| `scenarios.json` | see `scripts/types/indicators/scen.py` and `scenarios.example.json`: `values` per scenario (`dataElement`, `coc`, `aoc`, `period`, `value`), `expect` (`item`, `before`, `after`, `cellsAfter: {"pe": {"202601": v}}`) |
| `data/scen-values.json`, `data/scen-truth.json`, `data/scen-ous.json` | scen.py: data value set, truth records (`case: "scenario:<id>"`), scenario org units below the root |

`adapter.query(cfg, item, ou, pe, by)` returns `value` null for "no value"; `by="pe"` gives the months of `pe` as cells (keys = period ids), `by="ou"` the children of `ou` (keys = org unit names).
