---
name: dhis2-indicators
description: "Author, review, and test DHIS2 aggregate indicators and program indicators. Use whenever the user is defining or fixing an indicator or program indicator — writing a numerator/denominator, choosing an indicator type or factor, building a program-indicator expression or filter, picking EVENT vs ENROLLMENT analytics type, setting analytics period boundaries, using d2: functions, or investigating why an indicator returns the wrong number. Trigger even when the user doesn't say the word 'indicator' explicitly — phrases like 'how do I calculate X in DHIS2', 'this rate looks wrong in analytics', 'count events where…', 'percentage of … by org unit', 'pupil-teacher ratio', or 'validate/verify this calculation against the data' all apply. Covers the full path: define the expression, validate its syntax, prove it on a throwaway instance, document it."
---

# DHIS2 indicators and program indicators

This skill covers writing indicators correctly **and** proving they compute what you intend. Treat testing as part of authoring, not an afterthought — an indicator you haven't validated against known data is a guess. The two halves share the same knowledge (expression syntax, factors, COC references, d2 functions), which is why they live in one skill.

Scope is aggregate indicators and program indicators. Predictors and validation rules use the same expression engine and the same testing method, so the approach transfers, but they aren't documented here.

## Workflow

Follow this order. Each step catches a different class of error.

1. **Decide the kind.** Aggregate indicator or program indicator? See the decision below.
2. **Define the expression** from the metadata, not from memory of field names. Resolve real data element / category-option-combo / attribute UIDs from the dataset or program. For aggregate indicators, choose the indicator **type** deliberately — its factor multiplies the result.
   - **Establish the naming convention first.** Before creating anything, confirm how names should be formed: any prefix or grouping tag, casing, how disaggregation is expressed in the name, the `shortName` rule (≤50 chars), and whether a `code` scheme is used. If the user hasn't given one, **ask** — don't invent it. Consistent names are how these objects stay findable in Maintenance and analytics, and renaming a batch afterwards is painful, so it's worth one question up front.
   - **IDs for new objects** must be valid DHIS2 UIDs: exactly 11 characters, `[A-Za-z0-9]`, starting with a letter. Readable hand-made ids (`AncDrop1t3`, 12-character names) fail the import. Generate them (`GET /api/system/id?limit=N`, or `random.choice(ascii_letters) + ''.join(random.choices(ascii_letters + digits, k=10))` offline) or omit `id` and let the server assign one.
3. **Validate the syntax** with the description endpoints (below) before any data exists. This catches bad references and malformed expressions in seconds.
4. **Lint the underlying metadata.** Syntax validity doesn't mean the data elements are configured right. Check that each referenced DE has the expected `aggregationType` (usually SUM), a numeric `valueType`, and that every `#{de.coc}` COC belongs to the DE's current category combo. A DE silently set to `aggregationType: COUNT` breaks the result while the expression looks perfect — neither verification layer catches it. See the pre-flight lint in `references/testing.md`.
5. **Test on a throwaway instance.** Import the metadata, create org units, enter known data, run analytics, and compare analytics output to independently-computed expected values. See `references/testing.md`.
6. **Document** the definition, its meaning, and the test result so the next person can trust and reproduce it.

## Aggregate indicator or program indicator?

Aggregate indicators run over **aggregated data values** (data element × category option combo, already summed into the analytics tables). Use them for dataset/aggregate data: enrolment totals, facility counts, coverage rates.

Program indicators run over the **events or enrollments** of a tracker or event program — one value computed per event or per enrollment, then aggregated. Use them when the unit of analysis is a case, visit, or registration: count of events meeting a condition, time between two stages, a value taken from one enrollment.

A categorical question ("how many schools are public?") is countable in a program indicator (filter on the option) but **not** in an aggregate indicator unless the category is modelled as a category option combo or a boolean data element — an aggregate indicator does arithmetic, never "count where value = X". See `references/aggregate-indicators.md`.

## Anatomy in one screen

**Aggregate indicator** = `factor × numerator / denominator`.

- Numerator/denominator are arithmetic over `#{dataElementUID.categoryOptionComboUID}` (a specific disaggregation) or `#{dataElementUID}` (the sum of all that element's COCs), plus `+ - * /`, parentheses, numbers, `C{constantUID}`, `OUG{orgUnitGroupUID}`, `D{programUID.dataElementUID}`, and `I{programIndicatorUID}`.
- **factor** comes from the indicator **type**: Number ×1, Percentage ×100, "per 1000" ×1000, or custom. A percentage built on a Number type is wrong by 100×. This is the most common authoring error.
- `decimals` controls displayed precision; `annualized: true` scales by period length (use for population-denominator rates, not for school-level counts).

Full patterns, examples, and pitfalls: **`references/aggregate-indicators.md`**.

**Program indicator** has more moving parts, each a place to be wrong:

- **analyticsType**: `EVENT` (each event is a unit) or `ENROLLMENT` (each enrollment is a unit). Determines what gets counted; an enrollment with several events in a repeatable stage is counted once as ENROLLMENT, many times as EVENT.
- **aggregationType**: COUNT, SUM, AVERAGE, COUNT (distinct), etc. — applied across the units.
- **expression**: computed per unit, using `#{programStageUID.dataElementUID}`, `A{attributeUID}`, `V{...}` variables (`event_date`, `enrollment_date`, `value_count`, …), `d2:` functions (`d2:condition`, `d2:hasValue`, `d2:count`, `d2:daysBetween`, …), `C{constantUID}`, and numbers.
- **filter**: a boolean selecting which units count. The expression computes the value; the filter decides membership. Putting selection logic in the expression is a frequent mistake.
- **analyticsPeriodBoundaries**: decide whether an event/enrollment falls in a period by event date or enrollment date. The most common silent error; test it with dates on a period edge.

Full patterns, d2 reference, boundary recipes, examples, and pitfalls: **`references/program-indicators.md`**.

## Validate syntax before data

The description/validation endpoints confirm references resolve and syntax parses, on any instance, before you set up a test. Note the methods differ (verified on 2.43):

- Aggregate numerator/denominator: `GET /api/expressions/description?expression=<url-encoded expr>`
- Program indicator expression: `POST /api/programIndicators/expression/description` with the raw expression as the request body (`Content-Type: text/plain`); GET returns 405
- Program indicator filter: `POST /api/programIndicators/filter/description`, same body convention

A `200` with `"status": "OK"`, `"message": "Valid"`, and a human-readable description echoed back means every UID resolved. An error names the broken reference.

**Importing:** `POST /api/indicators` / `POST /api/programIndicators` take **one object** per request. A file wrapped as `{"indicators":[...]}` or `{"programIndicators":[...]}` goes to `POST /api/metadata` (try `importMode=VALIDATE` first).

## Test it (don't skip)

Syntax validity is not correctness. A numerator can be perfectly valid and still reference the wrong COC. Prove the calculation against known data on a fresh, disposable instance. The method, in brief:

- Compute expected values **two independent ways** — once by evaluating the expression over the input (does analytics match the expression?), once from the definition's meaning by classifying data from metadata (does the expression match the intent?). The gap between them is where real bugs hide.
- Feed **distinct, recorded input values** per cell, never constants, so a wrong reference produces a detectably wrong result.
- Cover edge cases: zero/blank denominator, on-threshold values, period-boundary dates, parent aggregation (percentages recombine as Σnum/Σden, never an average of child percentages).

The full procedure — fresh-instance setup, importing metadata (via the `dhis2-metadata` skill), entering data, running analytics, querying, and comparing — is in **`references/testing.md`**.

## Composition with other skills

- **`dhis2-metadata`** — fetch/split/import the dataset or program dependency export into the test instance, in dependency order. The testing reference assumes you use it.
- **`dhis2-docs`** — look up endpoint behaviour, d2 function signatures, and version-specific details rather than guessing.
- **`dhis2-instances`** (if available) — provision, reset, and discard the disposable test instance; also gives direct PostgreSQL access for root-causing analytics. If it isn't available in the environment, ask the user for a throwaway instance instead.

## Pitfalls (quick list)

- Wrong indicator type → factor off by 100× (percentage on a Number type).
- Referencing `#{de}` (all COCs) when you meant one disaggregation, or vice versa.
- Text/option-set data elements can't feed an aggregate indicator (no numeric value to sum); model as COCs or booleans.
- Program indicator: EVENT vs ENROLLMENT double-counting; selection logic in the expression instead of the filter; null treated as zero; period boundary using the wrong date.
- Concluding a function doesn't exist because the `d2:` form failed validation. Multi-select `containsItems()`/`contains()` are **unprefixed** in indicators and PIs (2.41+); `d2:` is the program-rule form. See `references/program-indicators.md`.
- A division by zero *inside* an expression aborts the whole analytics request (409 E7132), unlike a zero denominator, which just gives no value.
- DE configured wrong at the source — `aggregationType` not SUM, or a `#{de.coc}` pointing at a COC outside the DE's current combo — breaks results while the expression looks fine. Lint the metadata (step 4).
- Program rule variable names are functional identifiers — program rules reference them by *name* (`#{varName}`, `A{varName}`, `d2:hasValue('varName')`), so renaming or translating a variable while tidying metadata silently breaks every rule using it. The failure shows up as rules that stop firing, not as an error.
- Zeros vanish — the importer drops a `0` for DEs with `zeroIsSignificant=false`, and analytics omits stored zeros unless `keyIncludeZeroValuesInAnalytics` is on. Set both when a reported zero must count.
- Testing on a populated instance — always use a fresh, disposable one.
- Reading analytics before the analytics job finished, or after changing data without re-running it.
