# Program indicators — authoring reference

## Contents

- Anatomy and fields
- Analytics type: EVENT vs ENROLLMENT
- Aggregation type
- Expression building blocks
- d2 functions (the useful ones)
- Multi-select (MULTI_TEXT) values
- Filter vs expression
- Analytics period boundaries (the part people get wrong)
- Examples
- Disaggregating program indicators (2.42+ category mappings), and how they count
- Materializing PIs as aggregate data (tracker-to-aggregate)
- Pitfalls

## Anatomy and fields

A program indicator aggregates a value computed from the events or enrollments of one program. Key fields:

- `program` — the program it runs over.
- `analyticsType` — `EVENT` or `ENROLLMENT`.
- `aggregationType` — how unit values combine: COUNT, SUM, AVERAGE, COUNT (distinct values), MIN, MAX, etc.
- `expression` — computes one value per unit.
- `filter` — boolean deciding which units are included.
- The `expression` must evaluate to a **number**. A boolean such as `d2:hasValue(V{event_date})` parses but is invalid as an expression; put conditions in the filter, or turn them into a number with `d2:condition("…", 1, 0)`. For a plain event count the expression is `1` or `V{event_count}`.
- `analyticsPeriodBoundaries` — which date(s) place a unit in a period.
- `decimals`, `displayName`, `shortName`.

Program indicators do **not** use an IndicatorType/factor. The aggregation type and expression do the work. They appear in the `dx` analytics dimension alongside aggregate indicators, so a dashboard can mix them.

## Analytics type: EVENT vs ENROLLMENT

This decides the unit of analysis:

- **EVENT** — one row per event. "Number of ANC visits where weight < 45kg" counts events. If a stage is repeatable, each event counts separately.
- **ENROLLMENT** — one row per enrollment (per tracked entity's enrolment in the program). "Number of clients who ever had a positive test" counts enrollments once, regardless of how many events they have.

The classic bug: counting something per enrollment using EVENT type, which multiplies by the number of events. When the question is about people/cases, use ENROLLMENT; when it's about visits/records, use EVENT. Test a repeatable stage with two events to expose the difference.

In ENROLLMENT type you can still reference event data elements (`#{stage.de}`); analytics uses the value from the **latest event of that stage in the enrollment that has a non-null value**, among events inside the PI's event-date boundaries (`.stageOffset(n)` picks an earlier one). **Without an `EVENT_DATE` boundary, "latest" includes events after the reporting period** (verified 2.43.2): a client active in Q1 who died in Q2 reads as dead when you query Q1. Indicators meaning "status at the end of the period" (active, on treatment, currently) need an `EVENT_DATE` / `BEFORE_END_OF_REPORTING_PERIOD` boundary, which limits the lookup to events up to the period end, besides their enrollment-date boundary.

**2.43 changed the SQL engine**: the system setting `experimentalAnalyticsSqlEngineEnabled` defaults to true on 2.43 (false on 2.42). With it on, all boundaries apply to the event rows (enrollment- and incident-date boundaries too), and in ENROLLMENT PIs a missing event value is coalesced instead of read as null. Verified on 2.43.2 with probe PIs, the coalescing is narrower than "everything blank is 0":

- **ENROLLMENT filter, numeric DE: blank compares as 0.** `#{s.days} >= 0 && #{s.days} <= 90` counts every enrollment with no value; `#{s.vl} < 1000` counts a missing viral load as suppressed. Guard with `d2:hasValue(#{s.days}) && …`. The guard works on 2.43.2 (3 of 3 blank clients counted without it, 0 with it).
- **ENROLLMENT filter, text DE: blank becomes `''`**, so `#{s.status} != 'Dead'` *includes* enrollments with no status.
- **EVENT PIs don't coalesce**: blank stays NULL, `!= 'Dead'` excludes it. The same filter text means different things on the two analytics types.
- **Not coalesced:** category-mapping filters (blank stays NULL there), and `d2:daysBetween(…, blank)`, which stays NULL.

One older observation conflicts in part: on a 2.43.0.1 instance, ENROLLMENT filters with `d2:hasValue(#{stage.de})` combined with a cross-stage `d2:daysBetween` matched zero enrollments. It isn't confirmed whether that is a patch-level difference or specific to the cross-stage date. Prove the reference resolves (throwaway-PI probe, `references/testing.md`) before building on it; to tell engine differences apart, compare with the setting turned off on a throwaway instance.

**EVENT-type PIs cannot read cross-stage data elements.** Each event-analytics row carries only its own stage's DE columns, so `#{otherStage.de}` inside an EVENT-type expression or filter evaluates to null for events of a different stage — silently (the expression validates fine; the SL demo ships a PI broken this way). Fix pattern: anchor the PI on the stage that owns the DE (`V{program_stage_id} == '<owningStageUid>'`) and use that stage's `V{event_date}`; or switch to ENROLLMENT type (subject to the caveat above). With ENROLLMENT type, remember the lookup rule above: `EVENT_DATE` boundaries also restrict *which events the value is read from*, so a value recorded on another stage in an earlier period (birth weight at birth, read in the month of a later postnatal visit) drops out. Choose boundaries that keep the owning stage's event in scope (e.g. enrollment-date boundaries, or a `PS_EVENTDATE:<stageUid>` boundary on the counted stage) and prove the result with a cross-period case on a throwaway instance.

## Aggregation type

Applied across the selected units:

- **COUNT** — number of units passing the filter (expression often just needs to be a constant or any non-null; the count is of units).
- **SUM** — total of the per-unit expression values.
- **AVERAGE** — mean of the per-unit values.
- **COUNT (distinct)** — distinct values of the expression — the right tool for "how many distinct option codes / categories" questions that aggregate indicators can't do.
- **MIN / MAX** — extremes.

Match the aggregation type to the question. A SUM over a per-unit "1" equals a COUNT; a SUM over a measured value (e.g. amount dispensed) totals it.

**AVERAGE on a count-style expression is a silent breaker.** A PI whose expression is `V{event_count}`, `V{enrollment_count}`, or a constant `1` but whose `aggregationType` is AVERAGE validates fine and returns ~1 (or no rows) instead of the count — the SL demo ships a whole family broken this way. A **missing** aggregation type behaves the same with `V{event_count}` or `1` (value 1 whatever the count). Verified on 2.43.2 with the SL demo malaria program: `V{event_count}` + AVERAGE returned 1 with 84 matching events, while `V{enrollment_count}` + AVERAGE in an EVENT PI failed with SQL 42883. Lint for it when reviewing: count-style expression ⇒ aggregationType must be COUNT or SUM.

Other combinations fail loudly in analytics instead (verified 2.43.2): `V{tei_count}` with MAX (SQL 42804) or with no aggregation type (SQL 42883). In an EVENT PI, `V{tei_count}` counts distinct persons with at least one matching event in the period.

## Expression building blocks

Operands available in the expression (and filter):

- `#{programStageUID.dataElementUID}` — a data element value from a stage.
- `A{trackedEntityAttributeUID}` — a tracked entity attribute (enrollment-level).
- `V{...}` — built-in variables: `event_date`, `enrollment_date`, `incident_date`, `due_date`, `completed_date`, `value_count`, `zero_pos_value_count`, `event_count`, `enrollment_count`, `program_stage_name`, `current_date`, `org_unit_count`, `tei_count`, and others (check `dhis2-docs` for the full list on your version).
- `C{constantUID}` — a constant.
- `d2:` functions — see below.
- Operators `+ - * /`, parentheses, comparison `< <= > >= == !=`, logical `&& || !`, numeric/string literals.

## d2 functions (the useful ones)

- `d2:condition("expr", trueVal, falseVal)` — inline if; the condition is a boolean expression in quotes.
- `d2:hasValue(#{stage.de})` — true if the value is present (distinguish blank from zero).
- `d2:count(#{stage.de})` — number of events with a value for that element.
- `d2:countIfValue(#{stage.de}, value)` / `d2:countIfCondition(#{stage.de}, "expr")` — conditional counts across events.
- The `d2:count*` functions count events of the stage in the enrollment **within the PI's `EVENT_DATE` boundaries only** (2.42.6 `ProgramCountFunction`: `getStart/EndEventBoundary` match `boundaryTarget == EVENT_DATE`). With only enrollment-date or `PS_EVENTDATE:<stage>` boundaries they count across the whole enrollment, whatever the reporting period.
- `d2:daysBetween(start, end)`, `d2:weeksBetween`, `d2:monthsBetween`, `d2:yearsBetween` — date differences (e.g. `d2:daysBetween(V{enrollment_date}, #{stage.visitDate})`).
- `d2:zing(x)` (negative→0), `d2:oizp(x)` (0 if zero/neg else 1), `d2:zpvc(...)` (count of zero-or-positive values).

The full set accepted in program indicator expressions and filters on 2.40–2.43 (dhis2-core source): `d2:condition`, `d2:count`, `d2:countIfCondition`, `d2:countIfValue`, `d2:daysBetween`, `d2:hasValue`, `d2:maxValue`, `d2:minValue`, `d2:minutesBetween`, `d2:monthsBetween`, `d2:oizp`, `d2:relationshipCount`, `d2:weeksBetween`, `d2:yearsBetween`, `d2:zing`, `d2:zpvc`; the unprefixed `firstNonNull`, `greatest`, `least`, `if`, `is`, `isNull`, `isNotNull`, `log`, `log10`, `removeZeros` (plus `contains`/`containsItems` from 2.41); and `.stageOffset()`. `d2:floor`, `d2:round`, `d2:modulus`, `d2:left`, `d2:right`, `d2:concatenate`, `d2:validatePattern` and `d2:addDays` are **program-rule functions**: the parser knows them, but a PI using them fails with `Item d2:… not supported for this type of expression`.

`d2:addDays` deserves a note because it hides well: the Android Capture app evaluates program indicators on the device and accepts it, so an indicator can look right on Android and fail in server analytics. Same-meaning rewrite (verified 2.43.2): `d2:addDays(D, n) > V{analytics_period_end}` → `d2:daysBetween(V{analytics_period_end}, D) > -n`.

String literals may use double quotes, which is how to write an option code containing an apostrophe: `#{s.model} == "CBD's"`.

Confirm signatures against `dhis2-docs` for your version — the set grows release to release. **Validate before concluding a function is missing**: `POST /api/programIndicators/filter/description` (or `/expression/description`) with the candidate, and try the unprefixed form too (next section).

## Multi-select (MULTI_TEXT) values

2.41+ has two functions for multi-select values, and **in program indicators they have no `d2:` prefix**:

- `containsItems(#{stage.de}, 'R01')`: true if the comma-separated value holds the item `R01` as a whole item. Use this for counting per option of a multi-select field.
- `contains(#{stage.de}, 'R01')`: a substring match, on MULTI_TEXT too. Only safe when no code is part of another (`R1` matches `R10`; `contains(service, 'ART')` matches `PreART`). Lint every `contains()` literal against the option set: it should match exactly the codes intended. The opposite case exists too: when all codes of a group share a prefix by design (`GMC01`, `GMC02`, …), `contains(field, 'GMC')` is the deliberate way to say "any of the group".

Program *rules* use the prefixed forms (`d2:contains`, `d2:validatePattern`), while indicators, program indicators, predictors and validation rules use the unprefixed forms. Validating `d2:containsItems(...)` as a PI filter returns "Expression is not valid" / "Invalid string token 'd'", which reads as "PIs cannot do this". A session built 204 rule-assigned yes/no flag data elements to work around a function that was available all along. Verified on 2.42.6. When arguing against such flags, don't claim rule-assigned values are missing for API data: `/api/tracker` imports run the server rule engine, and an ASSIGN action fills an omitted data value (warning E1308) or rejects a different one (E1307, unless `ruleEngineAssignOverwrite`) — verified in 2.42.6 `AssignDataValueExecutor`. The real costs are duplicated metadata, existing events needing a backfill, and flags drifting if rules change.

Related facts from the same work (2.42.6):
- `V{current_date}` is allowed in PI filters and is evaluated at query time ("overdue as of today"). Test against the real date, not a fixed one.
- After changing a PI, analytics keeps serving the old definition until `POST /api/maintenance/cacheClear`.

## Filter vs expression

The **expression** computes the per-unit value that gets aggregated. The **filter** is a boolean that decides whether the unit is included at all. Keep selection in the filter:

```
filter:     #{abc.weight} < 45 && d2:hasValue(#{abc.weight})
expression: 1            (with aggregationType COUNT — count the events that pass)
```

Putting the threshold inside the expression (e.g. `d2:condition("#{abc.weight} < 45", 1, 0)` summed) also works but is harder to read and easier to get wrong on nulls. Use the filter for membership; reserve `d2:condition` in the expression for genuinely per-unit computed values.

**Bracket every `||`.** `&&` binds tighter than `||`, so `#{s.sex} == 'F' && #{s.test} == 'A' || #{s.test} == 'B'` counts every B test whatever the sex. Hand-built filters mix them often; when reviewing, wrap each run of `||`-joined alternatives on one field in brackets: `#{s.sex} == 'F' && (#{s.test} == 'A' || #{s.test} == 'B')`.

Other filter mistakes that are worth linting for, all seen in real configurations:

- **Literal is not an option code.** Filters compare the stored **code**, not the option name; `== 'Positive'` matches nothing when the code is `POS`. Check every literal against the option set's codes (watch case, stray spaces and punctuation).
- **Contradictions.** `#{s.test} == 'A' && #{s.test} == 'B'` on one field, or `!= v && == v`, is always false. Usually `||` was meant; the indicator's name tells which.
- **Reversed period tests.** `V{analytics_period_start} >= X && V{analytics_period_end} <= X` is only true when the period is a single day; the intended form is `X >= V{analytics_period_start} && X <= V{analytics_period_end}`.
- **Event variables in an ENROLLMENT PI.** `V{event_count}` or `V{event_date}` there fails in analytics with SQL 42703; for a count use `V{enrollment_count}`.
- **A data element read from a stage it doesn't belong to** (`#{wrongStage.de}`) also fails with SQL 42703: that column doesn't exist in the stage's table.

## Analytics period boundaries (the part people get wrong)

Boundaries decide which date puts a unit into a reporting period. They are the most common silent source of wrong program-indicator numbers.

- For **EVENT** indicators, the default boundary is the **event date** — an event counts in the period containing its `occurredAt` date.
- For **ENROLLMENT** indicators, the default boundaries bracket the **enrollment date**. If your expression uses an event data element but you leave enrollment-date boundaries, the value is taken relative to the enrollment, not the event — often not what you want.

Custom `analyticsPeriodBoundaries` let you say, for example, "include the unit if its **event date** of a particular stage falls in the period" even for an ENROLLMENT indicator. Each boundary has a target (`EVENT_DATE`, `ENROLLMENT_DATE`, `INCIDENT_DATE`, or a specific stage's date) and a type (`AFTER_START_OF_REPORTING_PERIOD`, `BEFORE_END_OF_REPORTING_PERIOD`).

A boundary can be shifted with `offsetPeriods` (cohort indicators: "enrolled 12 months before the reporting period"). **`offsetPeriods` without `offsetPeriodType` is ignored**: the PI silently counts the current period (verified 2.43.2). Set `offsetPeriodType` (usually `Monthly`) whenever there is an offset, and give the start and end boundaries the same offset unless the window is meant to stretch. With Monthly offsets of −12 on both boundaries, a quarterly query for Q counts enrollments in the quarter 12 months earlier, so test data for a cohort PI goes in Q−12 months, or you query Q+12.

A missing `AFTER_START_OF_REPORTING_PERIOD` boundary means no lower bound: "enrolled on or before the end of the period" is a single `BEFORE_END_OF_REPORTING_PERIOD` boundary on `ENROLLMENT_DATE`. That is the right shape for "currently active" counts (with the event-date boundary from the ENROLLMENT section above); an AFTER_START boundary there wrongly limits the count to clients enrolled *in* the period.

**Omission trap when creating PIs via the API:** the Maintenance UI silently adds the two default boundaries, but a raw `POST /api/programIndicators` with no `analyticsPeriodBoundaries` creates an **unbounded** PI — it returns the same all-time value for every period, with no error anywhere. Always include the boundaries explicitly in API-created PIs.

Always test boundaries by placing events/enrollments on dates on both sides of a period edge (e.g. 31 Dec vs 1 Jan) and confirming each lands in the period you expect.

## Examples

**Count events meeting a condition (EVENT).**
```
analyticsType: EVENT
aggregationType: COUNT
filter: #{ANCstage.weight} < 45 && d2:hasValue(#{ANCstage.weight})
expression: 1
```

**Average value across events (EVENT).**
```
analyticsType: EVENT
aggregationType: AVERAGE
filter: d2:hasValue(#{ANCstage.weight})
expression: #{ANCstage.weight}
```

**Clients with at least one positive result (ENROLLMENT).**
```
analyticsType: ENROLLMENT
aggregationType: COUNT
filter: d2:countIfValue(#{testStage.result}, "Positive") > 0
expression: 1
```

**Time from enrollment to first visit, in days (ENROLLMENT, averaged).**
```
analyticsType: ENROLLMENT
aggregationType: AVERAGE
filter: d2:hasValue(#{visitStage.visitDate})
expression: d2:daysBetween(V{enrollment_date}, #{visitStage.visitDate})
```

## Disaggregating program indicators (2.42+ category mappings)

The modern replacement for "one PI per age/sex combination": a single PI produces disaggregated analytics cells via category mappings. The conversion path, verified on 2.43:

1. Define `categoryMappings` on the **program** — per category, map each category option to a filter expression over the program's data (e.g. an age range over an attribute, a sex option over a DE).
2. On the **PI**, set `categoryMappingIds` (referencing the program's mappings) and a disaggregation `categoryCombo` built from those categories.
3. Run `POST /api/maintenance?categoryOptionComboUpdate=true` so the combo's COCs exist.
4. Verify: the disaggregated cells must sum to the undisaggregated PI total (and, when migrating, match the legacy one-PI-per-cell values).

Confirmed on 2.42.6 as well: one mapped PI replaced about 60 per-option PIs. Two write-side traps: `categoryMappingIds` is not validated (a bogus UID is stored with 200 and no error reports), and `optionMappings[].optionId` is checked for existence but not for membership in that category. Re-read the program after writing mappings.

### How a disaggregated PI counts (verified 2.43.2)

Everything in this subsection applies **only to PIs with `categoryMappingIds`** (program disaggregation); a PI without mappings counts every unit that passes its filter.

- **EVENT PIs count a record only if it matches exactly one option in every mapped category, and a record that fails this drops out of the total too**, not just from the cells. A value in a gap between bands, a blank or unmapped value, or a value matching two overlapping options (a "50+" roll-up option next to "50-54") all lower the total silently.
- **ENROLLMENT PIs keep such records in the total but drop them from the cells**, so the cells don't add up to the total.
- **A mapping filter on a field of another stage never matches for an EVENT PI** (the row only has its own stage's values), so with the rule above the PI is always 0.
- **Blank values are not coalesced in mapping filters** (unlike ENROLLMENT PI filters): a blank age matches no band.

So the mappings need the same care as the filter: every option mapped once, no gaps or overlaps between numeric bands **at the real resolution of the field** (an age computed by a program rule with `d2:round(…, 3)` can be 14.5, which falls in the gap between a "10-14" band written `<= 14` and a "15-19" band written `>= 15`), bands that agree with the option names, and every possible value landing in exactly one option. The safe band form is `>= a && < b` with the next band starting at `b`.

Failures caused by mappings:

- One malformed mapping filter (e.g. `#{a}#{a}`) makes **every PI using that mapping fail**, including the plain total ("Invalid string token").
- A `null` entry in a PI's `categoryMappingIds` gives HTTP 500 with an empty message; removing the null fixes it.
- A disaggregation category with no mapping on the program gives HTTP 409 "disaggregation category … needs a category mapping in the program".

**Reuse existing categories and category options** — category options are shared objects, so a new category for disaggregation can be assembled from options that already exist in other categories; don't mint duplicates. Field-level details are version-sensitive — check the program-indicator disaggregation section of the docs (`dhis2-docs` skill) for your version.

## Materializing PIs as aggregate data (tracker-to-aggregate)

The alternative to referencing `I{programIndicatorUID}` live in aggregate indicator expressions: export PI values as a data value set and import them into aggregate DEs. Export `GET /api/analytics/dataValueSet.json?dimension=dx:<PI-uids>&…&outputIdScheme=ATTRIBUTE:<attrUid>` where a TEXT attribute on each PI holds the target DE *code*, then import with `dataElementIdScheme=CODE`. Verified 2.40–2.43. Two gotchas: analytics tables must be generated before the export, and on 2.43+ the target DEs must belong to a data set of the matching period type assigned to the target org units (2.43's stricter data value import — see the dhis2-metadata skill).

## Pitfalls

- **EVENT vs ENROLLMENT double counting** — per-person measures must be ENROLLMENT, or a repeatable stage multiplies the count.
- **Selection logic in the expression** instead of the filter — works but error-prone; verify with units differing only in the filtered field.
- **Null treated as zero** — blank is not zero, but ENROLLMENT filters on 2.43 compare a blank number as 0 and blank text as `''` (see the ENROLLMENT section). Guard every numeric range with `d2:hasValue`.
- **Status at period end read from a later event** — ENROLLMENT PIs without an `EVENT_DATE` boundary read events after the period.
- **Offset without `offsetPeriodType`** — silently ignored; the cohort PI counts the current period.
- **Unbracketed `&&`/`||`**, option names instead of codes, `contains()` over-matching.
- **Broken definitions import fine** — the metadata import does not validate PI filters or expressions; a filter with an unsupported function imports with status OK and only fails in analytics. And the description endpoints pass some filters that still fail as SQL (`#{x} == '2nd PCR' <= 2`). Validate *and* run an analytics query.
- **Mapping gaps and overlaps** drop records from disaggregated EVENT PIs' totals (see the disaggregation section).
- **Boundaries using the wrong date** — the default may bucket by enrollment date when you meant event date; test on period edges.
- **Aggregation type mismatch** — a SUM where you wanted COUNT (distinct) overcounts; pick the type that matches the question.
- **Forgetting events in analytics** — program-indicator results need the event/enrollment analytics tables; do not skip events when generating analytics for a test.
