# Program indicators — authoring reference

## Contents

- Anatomy and fields
- Analytics type: EVENT vs ENROLLMENT
- Aggregation type
- Expression building blocks
- d2 functions (the useful ones)
- Filter vs expression
- Analytics period boundaries (the part people get wrong)
- Examples
- Disaggregating program indicators (2.42+ category mappings)
- Materializing PIs as aggregate data (tracker-to-aggregate)
- Pitfalls

## Anatomy and fields

A program indicator aggregates a value computed from the events or enrollments of one program. Key fields:

- `program` — the program it runs over.
- `analyticsType` — `EVENT` or `ENROLLMENT`.
- `aggregationType` — how unit values combine: COUNT, SUM, AVERAGE, COUNT (distinct values), MIN, MAX, etc.
- `expression` — computes one value per unit.
- `filter` — boolean deciding which units are included.
- `analyticsPeriodBoundaries` — which date(s) place a unit in a period.
- `decimals`, `displayName`, `shortName`.

Program indicators do **not** use an IndicatorType/factor. The aggregation type and expression do the work. They appear in the `dx` analytics dimension alongside aggregate indicators, so a dashboard can mix them.

## Analytics type: EVENT vs ENROLLMENT

This decides the unit of analysis:

- **EVENT** — one row per event. "Number of ANC visits where weight < 45kg" counts events. If a stage is repeatable, each event counts separately.
- **ENROLLMENT** — one row per enrollment (per tracked entity's enrolment in the program). "Number of clients who ever had a positive test" counts enrollments once, regardless of how many events they have.

The classic bug: counting something per enrollment using EVENT type, which multiplies by the number of events. When the question is about people/cases, use ENROLLMENT; when it's about visits/records, use EVENT. Test a repeatable stage with two events to expose the difference.

In ENROLLMENT type you can still reference event data elements (`#{stage.de}`); analytics resolves them to a value within the enrollment according to the boundaries and, where relevant, the latest/earliest event of that stage. **Verify this empirically before relying on it**: on a 2.43.0.1 demo instance, ENROLLMENT-type filters referencing event DEs (`d2:hasValue(#{stage.de})`, cross-stage `d2:daysBetween`) matched zero enrollments even though event analytics demonstrably held the values — with and without default boundaries. Prove the reference resolves (throwaway-PI probe, `references/testing.md`) before building on it.

**EVENT-type PIs cannot read cross-stage data elements.** Each event-analytics row carries only its own stage's DE columns, so `#{otherStage.de}` inside an EVENT-type expression or filter evaluates to null for events of a different stage — silently (the expression validates fine; the SL demo ships a PI broken this way). Fix pattern: anchor the PI on the stage that owns the DE (`V{program_stage_id} == '<owningStageUid>'`) and use that stage's `V{event_date}`; or switch to ENROLLMENT type (subject to the caveat above).

## Aggregation type

Applied across the selected units:

- **COUNT** — number of units passing the filter (expression often just needs to be a constant or any non-null; the count is of units).
- **SUM** — total of the per-unit expression values.
- **AVERAGE** — mean of the per-unit values.
- **COUNT (distinct)** — distinct values of the expression — the right tool for "how many distinct option codes / categories" questions that aggregate indicators can't do.
- **MIN / MAX** — extremes.

Match the aggregation type to the question. A SUM over a per-unit "1" equals a COUNT; a SUM over a measured value (e.g. amount dispensed) totals it.

**AVERAGE on a count-style expression is a silent breaker.** A PI whose expression is `V{event_count}`, `V{enrollment_count}`, or a constant `1` but whose `aggregationType` is AVERAGE validates fine and returns ~1 (or no rows) instead of the count — the SL demo ships a whole family broken this way. Lint for it when reviewing: count-style expression ⇒ aggregationType must be COUNT or SUM.

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
- `d2:daysBetween(start, end)`, `d2:weeksBetween`, `d2:monthsBetween`, `d2:yearsBetween` — date differences (e.g. `d2:daysBetween(V{enrollment_date}, #{stage.visitDate})`).
- `d2:zing(x)` (negative→0), `d2:oizp(x)` (0 if zero/neg else 1), `d2:zpvc(...)` (count of zero-or-positive values).
- `d2:floor`, `d2:round`, `d2:modulus`, `d2:left`, `d2:right`, `d2:concatenate`, `d2:validatePattern`.

Confirm signatures against `dhis2-docs` for your version — the set grows release to release.

## Filter vs expression

The **expression** computes the per-unit value that gets aggregated. The **filter** is a boolean that decides whether the unit is included at all. Keep selection in the filter:

```
filter:     #{abc.weight} < 45 && d2:hasValue(#{abc.weight})
expression: 1            (with aggregationType COUNT — count the events that pass)
```

Putting the threshold inside the expression (e.g. `d2:condition("#{abc.weight} < 45", 1, 0)` summed) also works but is harder to read and easier to get wrong on nulls. Use the filter for membership; reserve `d2:condition` in the expression for genuinely per-unit computed values.

## Analytics period boundaries (the part people get wrong)

Boundaries decide which date puts a unit into a reporting period. They are the most common silent source of wrong program-indicator numbers.

- For **EVENT** indicators, the default boundary is the **event date** — an event counts in the period containing its `occurredAt` date.
- For **ENROLLMENT** indicators, the default boundaries bracket the **enrollment date**. If your expression uses an event data element but you leave enrollment-date boundaries, the value is taken relative to the enrollment, not the event — often not what you want.

Custom `analyticsPeriodBoundaries` let you say, for example, "include the unit if its **event date** of a particular stage falls in the period" even for an ENROLLMENT indicator. Each boundary has a target (`EVENT_DATE`, `ENROLLMENT_DATE`, `INCIDENT_DATE`, or a specific stage's date) and a type (`AFTER_START_OF_REPORTING_PERIOD`, `BEFORE_END_OF_REPORTING_PERIOD`).

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

**Reuse existing categories and category options** — category options are shared objects, so a new category for disaggregation can be assembled from options that already exist in other categories; don't mint duplicates. Field-level details are version-sensitive — check the program-indicator disaggregation section of the docs (`dhis2-docs` skill) for your version.

## Materializing PIs as aggregate data (tracker-to-aggregate)

The alternative to referencing `I{programIndicatorUID}` live in aggregate indicator expressions: export PI values as a data value set and import them into aggregate DEs. Export `GET /api/analytics/dataValueSet.json?dimension=dx:<PI-uids>&…&outputIdScheme=ATTRIBUTE:<attrUid>` where a TEXT attribute on each PI holds the target DE *code*, then import with `dataElementIdScheme=CODE`. Verified 2.40–2.43. Two gotchas: analytics tables must be generated before the export, and on 2.43+ the target DEs must belong to a data set of the matching period type assigned to the target org units (2.43's stricter data value import — see the dhis2-metadata skill).

## Pitfalls

- **EVENT vs ENROLLMENT double counting** — per-person measures must be ENROLLMENT, or a repeatable stage multiplies the count.
- **Selection logic in the expression** instead of the filter — works but error-prone; verify with units differing only in the filtered field.
- **Null treated as zero** — blank is not zero. Guard with `d2:hasValue`; a `< 45` filter may behave unexpectedly on missing values.
- **Boundaries using the wrong date** — the default may bucket by enrollment date when you meant event date; test on period edges.
- **Aggregation type mismatch** — a SUM where you wanted COUNT (distinct) overcounts; pick the type that matches the question.
- **Forgetting events in analytics** — program-indicator results need the event/enrollment analytics tables; do not skip events when generating analytics for a test.
