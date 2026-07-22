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

In ENROLLMENT type you can still reference event data elements (`#{stage.de}`); analytics resolves them to a value within the enrollment according to the boundaries and, where relevant, the latest/earliest event of that stage.

## Aggregation type

Applied across the selected units:

- **COUNT** — number of units passing the filter (expression often just needs to be a constant or any non-null; the count is of units).
- **SUM** — total of the per-unit expression values.
- **AVERAGE** — mean of the per-unit values.
- **COUNT (distinct)** — distinct values of the expression — the right tool for "how many distinct option codes / categories" questions that aggregate indicators can't do.
- **MIN / MAX** — extremes.

Match the aggregation type to the question. A SUM over a per-unit "1" equals a COUNT; a SUM over a measured value (e.g. amount dispensed) totals it.

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

## Pitfalls

- **EVENT vs ENROLLMENT double counting** — per-person measures must be ENROLLMENT, or a repeatable stage multiplies the count.
- **Selection logic in the expression** instead of the filter — works but error-prone; verify with units differing only in the filtered field.
- **Null treated as zero** — blank is not zero. Guard with `d2:hasValue`; a `< 45` filter may behave unexpectedly on missing values.
- **Boundaries using the wrong date** — the default may bucket by enrollment date when you meant event date; test on period edges.
- **Aggregation type mismatch** — a SUM where you wanted COUNT (distinct) overcounts; pick the type that matches the question.
- **Forgetting events in analytics** — program-indicator results need the event/enrollment analytics tables; do not skip events when generating analytics for a test.
