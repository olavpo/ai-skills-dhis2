# DHIS2 program indicator behaviours (verified)

Each behaviour below was confirmed on **DHIS2 2.43.2** with throwaway probe indicators on a test instance (October 2026). Re-verify on other versions: use the probe recipe at the end. They are built into the independent evaluator (`scripts/lib/piexpr.py`, used by `scripts/validate/compare.py`), and several of them drive fix patterns (`fix-patterns.md`). The same facts, in prose and with authoring advice, are in the `dhis2-indicators` skill (`references/program-indicators.md`); keep the two in step when you verify something new.

## Import and validation

| # | Behaviour | Consequence |
|---|---|---|
| I1 | The metadata import does **not** validate PI filters or expressions. A filter with an unsupported function or a malformed term imports with status OK. | Broken definitions only show up in analytics. Always run the syntax check and an analytics query. |
| I2 | `POST /api/programIndicators/{expression,filter}/description` (body `text/plain`) validates references and functions. `GET` returns 405. | Use it for every expression, filter and mapping filter. |
| I3 | The syntax endpoint passes some filters that still fail as SQL, e.g. `#{x} == '2nd PCR' <= 2`. | Syntax OK ≠ runs. Query analytics too. |
| I4 | `d2:addDays()` is not supported in PI expressions on the server ("Item d2:addDays( not supported for this type of expression"). The Android Capture app does evaluate it on the device. | Indicators can look fine on Android and fail in server analytics. Replace `d2:addDays(D, n) > V{analytics_period_end}` with `d2:daysBetween(V{analytics_period_end}, D) > -n`. |
| I5 | Double-quoted string literals are accepted: `#{x} == "CBD's"`. | Use for option codes containing an apostrophe. |
| I6 | `containsItems()` is supported in filters and mapping filters. | Use for whole-item matches on MULTI_TEXT. |

## Analytics errors

| # | Behaviour | Error |
|---|---|---|
| E1 | `V{event_count}` or `V{event_date}` in an ENROLLMENT PI. | SQL 42703 (HTTP 409) |
| E2 | Aggregation MAX with expression `V{tei_count}`. | SQL 42804 |
| E3 | No aggregation type with `V{tei_count}`. | SQL 42883 |
| E4 | No aggregation type with `V{event_count}` / `1`. | No error, but the value is **1** whatever the count (it averages the per-row 1). |
| E4b | AVERAGE with `V{event_count}` (EVENT PI). | Same: **1** whatever the count (84 matching events gave 1). |
| E4c | AVERAGE with `V{enrollment_count}` in an EVENT PI. | SQL 42883 (HTTP 409). The SL demo ships several PIs like this. |
| E5 | A data element read from a stage it doesn't belong to. | SQL 42703 (the column doesn't exist). |
| E6 | One malformed mapping filter (e.g. `#{a}#{a}`). | Every PI using that mapping fails, including the plain total ("Invalid string token"). |
| E7 | A `null` entry in a PI's `categoryMappingIds`. | HTTP 500 with an empty message. Removing the null fixes it. |
| E8 | A disaggregation category with no mapping on the program. | HTTP 409 "disaggregation category … needs a category mapping in the program". |
| E9 | Event analytics on a fresh instance with empty `dataOutputPeriodTypes`. | "column yearly does not exist"; set `POST /api/configuration/dataOutputPeriodTypes`. |

## Counting semantics

| # | Behaviour | Consequence |
|---|---|---|
C1, C2 and C7 apply only to PIs with **program disaggregation** (`categoryMappingIds` pointing at the program's `categoryMappings`, 2.42+). A PI without mappings counts every unit that passes its filter.

| C1 | **EVENT PIs with program disaggregation count a record only if it matches exactly one option in every mapped category.** Records in a gap, with an unmapped or blank value, or matching two overlapping options are dropped **from the total as well**. | Gaps/overlaps in age bands silently lower totals. Roll-up options in the same category (50+ with 50-54) drop everyone in the overlap. |
| C2 | **ENROLLMENT PIs with program disaggregation keep such records in the total but drop them from the cells.** | Cells don't add up to the total. |
| C3 | Blank text compared with `!=`: EVENT PIs treat it as NULL (excluded); ENROLLMENT PIs coalesce text to `''` (included). | The same filter means different things on the two types. |
| C4 | **ENROLLMENT PIs compare a blank numeric data element as 0** in the filter (not in mapping filters). `d2:daysBetween(…, blank)` stays NULL. | `days >= 0 && days <= 90` counts everyone with no value; `VL < 1000` counts a blank VL as suppressed. Guard with `d2:hasValue()`. |
| C5 | **ENROLLMENT PIs read the latest event of the stage — including events after the reporting period** — unless the PI has an `EVENT_DATE` / `BEFORE_END_OF_REPORTING_PERIOD` boundary, which limits the lookup to events up to the period end. | "Active at end of period" must have the event-date boundary, otherwise a later visit (e.g. death next quarter) removes the client. |
| C6 | An EVENT PI's row has only its own stage's values. Conditions on another stage are NULL. | Cross-stage event PIs are always 0. |
| C7 | A category-mapping filter on a field of another stage never matches for EVENT PIs on a different stage. | Such PIs are always 0 (C1). |
| C8 | `contains()` is a substring match, also on MULTI_TEXT. `containsItems()` matches whole items. | `contains(service,'TB')` also matches `MDRTB`. |
| C9 | `V{tei_count}` in an EVENT PI counts distinct persons with ≥ 1 matching event in the period. | — |

## Period boundaries

| # | Behaviour | Consequence |
|---|---|---|
| P1 | **`offsetPeriods` without `offsetPeriodType` is ignored** (the PI behaves as if there were no offset). | Cohort PIs count the current period. Set `offsetPeriodType: Monthly`. |
| P2 | With `Monthly` offsets of −6/−12/−24 on both boundaries, a quarterly query for Q counts enrollments in the quarter 6/12/24 months earlier. | Query `Q + offset` to see test data generated in Q. |
| P3 | A missing AFTER_START boundary means no lower bound. | "Enrolled on or before the end of the period" = only BEFORE_END on ENROLLMENT_DATE. |

## Probe recipe

```bash
python3 scripts/validate/probe.py --type ENROLLMENT --expr 'V{enrollment_count}' \
  "#{stage.days} >= 0" "d2:hasValue(#{stage.days}) && #{stage.days} >= 0"
# creates ZZZTEMP PIs on the program, queries the test period at the test root, prints the values, deletes the probes
```

For behaviours that depend on data, create 2–4 tracked entities in a dedicated org unit with the exact situation (e.g. active in Q1 and died in Q2), run analytics, and query probe PIs that differ in one detail (e.g. with and without an event-date boundary). Keep probe PIs named `ZZZ…` and delete them afterwards. A scenario in `scenarios.json` (see `data-formats.md`) is the easiest way to load such data.
