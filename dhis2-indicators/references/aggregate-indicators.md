# Aggregate indicators — authoring reference

## Contents

- Anatomy and fields
- Expression syntax
- Indicator types and the factor
- Common patterns (totals, disaggregation, ratios, percentages, annualized)
- Categorical data: why option sets don't work and what to do
- Pitfalls

## Anatomy and fields

An aggregate indicator computes `factor × (numerator / denominator)`, where the factor comes from the indicator type. Key fields on the object:

- `name`, `shortName` (≤50 chars), `code` (optional), `description`. Follow the agreed naming convention (see the workflow in SKILL.md — if none was given, ask before creating, don't invent one).
- `numerator`, `numeratorDescription`, `denominator`, `denominatorDescription` — the descriptions are shown to users and should state plainly what each side counts.
- `indicatorType` — reference to an IndicatorType, which carries the `factor`.
- `decimals` — displayed precision (null = default).
- `annualized` — scales the value by the number of periods in a year (see below).

The numerator and denominator operate on **aggregated data values**: data already summed into the analytics tables by data element and category option combo. The analytics engine evaluates the arithmetic per org unit and period, aggregating numerator and denominator separately before dividing.

## Expression syntax

Operands inside numerator/denominator:

- `#{dataElementUID.categoryOptionComboUID}` — one specific disaggregation (e.g. a single class × sex cell).
- `#{dataElementUID}` — the sum of **all** that data element's category option combos.
- `#{dataElementUID.categoryOptionComboUID.attributeOptionComboUID}` — pin the attribute combo too (rare).
- `C{constantUID}` — a constant.
- `OUG{orgUnitGroupUID}` — count of org units in a group (useful for "number of facilities" style denominators).
- `D{programUID.dataElementUID}`, `A{programUID.attributeUID}`, `I{programIndicatorUID}` — pull program data or a program indicator into an aggregate indicator.
- Operators: `+ - * /`, parentheses, numeric literals.
- Functions for null/zero safety: `if(condition, x, y)`, `isNull(...)`, `isNotNull(...)`, `firstNonNull(...)`, `greatest(...)`, `least(...)`.

Example null-safe denominator so a missing value yields no result rather than an error:

```
if(isNotNull(#{qoiU4awdpxQ}), #{qoiU4awdpxQ}, 1)
```

Prefer leaving a zero/blank denominator to return no value (DHIS2 omits the row) over forcing a 1 — forcing a 1 silently produces a meaningless number. Use the `if` form only when you specifically want a fallback.

## Indicator types and the factor

The indicator **type** sets the factor that multiplies `numerator/denominator`:

- **Number** — factor 1. Counts and ratios. A pupil-teacher ratio (learners ÷ teachers) is a Number-type indicator with the teacher count as denominator; it is *not* a percentage even if a spec labels it "%".
- **Percentage** — factor 100. Anything that should read 0–100.
- **Per mille / per 10,000 / per 100,000** — factors 1000 / 10,000 / 100,000, for population-rate style measures.
- **Custom** — any factor.

Getting this wrong is the single most common aggregate-indicator error: a percentage built on a Number type is off by 100×, a ratio built on a Percentage type by the same. On a fresh instance, indicator types must exist *before* importing indicators that reference them, and you must confirm the factor — importers move objects but don't check that "Percentage" really has factor 100.

## Common patterns

**Total (sum of a disaggregated element).** Use `#{de}` to sum every COC:

```
numerator:   #{eBTbIITRm3H} + #{tno1y7kW71Z} + …      (sum of the relevant elements)
denominator: 1
type: Number
```

**Sex (or any single category) disaggregation.** There is no "filter by category option" in an aggregate expression. Enumerate the category option combos that carry that option. To get "girls", sum every COC whose category option is Female across the relevant elements:

```
numerator: #{DE1.femaleCOC1} + #{DE1.femaleCOC2} + … + #{DE2.femaleCOC1} + …
```

Build these lists from metadata (group the dataset's category option combos by their category combo, then pick the COCs whose category option set includes the one you want) rather than by hand — it is easy to miss or duplicate a COC, and a wrong COC is invisible unless your test data uses distinct values per cell.

**Ratio (per one).** Learners per teacher, learners per classroom, learners per stream:

```
numerator:   <enrolment total expression>
denominator: #{ERcw4yZuSSd}      (teachers)
type: Number      ← factor 1, NOT Percentage
decimals: 1
```

**Percentage.** Share of a total:

```
numerator:   <girls expression>
denominator: <total expression>
type: Percentage   ← factor 100
decimals: 1
```

**Annualized rates.** `annualized: true` divides by the fraction of the year the period covers, so a quarterly value is scaled to an annual figure. Use it for population-denominator coverage/incidence rates where the denominator is an annual population. Do **not** use it for simple counts or for school-census style indicators where the period already represents the reporting cycle — it will distort the number.

## Categorical data: why option sets don't work

An aggregate indicator does arithmetic on numeric aggregated values. A data element backed by an **option set with valueType TEXT** stores an option code (text) and has aggregationType NONE — it never enters the numeric analytics path, so it cannot appear in a numerator. Even if the option codes are numeric, the data element's valueType decides whether analytics treats them as numbers; and even then, summing or averaging nominal codes is meaningless.

To count by category in aggregate analytics you must move the categorisation into something numeric:

- Model the characteristic as a **category** so each option becomes a category option combo; then `#{de.cocForOptionX}` counts the units in that option.
- Or create one **boolean / TRUE_ONLY** data element per option; each aggregates as a count and drops into an indicator.

Numeric-looking option codes do **not** rescue this, and they still can't give a "count where value = X". For per-record display of a categorical value, show the data element directly (custom report, line listing, data value API) — no indicator. Counting by option *is* native to program indicators (filter on the option), which is one reason to choose that path.

## Pitfalls

- **Factor / indicator type** wrong → off by 100× (or 1000×). Confirm every percentage uses a factor-100 type and every ratio a factor-1 type.
- **`#{de}` vs `#{de.coc}`** — `#{de}` is the sum of all COCs; if you meant one disaggregation, name the COC, and vice versa.
- **Missing COC in a disaggregated sum** — undetectable unless test data uses distinct per-cell values. Generate the COC list from metadata.
- **Forcing a denominator to 1** to dodge divide-by-zero — produces a meaningless number; prefer letting it return no value.
- **Parent aggregation of percentages** — analytics recomputes Σnum/Σden×factor at the parent, which is correct; do not expect the average of child percentages.
- **Time aggregation** — numerator/denominator aggregate across periods by each element's `aggregationType` (SUM, AVERAGE, LAST). An AVERAGE or LAST element behaves differently from SUM when a year is built from months.
- **Data element `aggregationType` set wrong at the source** — an element that should sum but ships with `COUNT`, `AVERAGE`, or `LAST` produces wrong indicator values, and the expression looks fine. This is invisible to expression review; check the DE config directly (see the pre-flight lint in `testing.md`).
- **`zeroIsSignificant` and stored zeros** — a `0` is dropped on import for DEs with `zeroIsSignificant=false`, and analytics omits stored zeros unless `keyIncludeZeroValuesInAnalytics` is on. If a real reported zero must count (e.g. in a denominator or a count), set both.
- **Legacy / wrong COCs** — a `#{de.coc}` whose COC isn't in the DE's current category combo imports and validates cleanly but never carries data, so the term silently contributes nothing. Derive COCs from current metadata.
- **Text/option-set elements** can't be summed — see above.
