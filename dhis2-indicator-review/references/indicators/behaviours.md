# DHIS2 aggregate indicator behaviours (verified)

Each behaviour below was confirmed on **DHIS2 2.43.2** (October 2026) with throwaway `ZZZTEMP` probe indicators (`scripts/types/indicators/probe.py`) over a small hand-made data set in four probe org units, or with direct API calls on the same test instance. Re-verify on other versions with the probe recipe at the end. They are built into the independent evaluator (`scripts/types/indicators/indexpr.py`, used by `compare.py`); its docstring lists the same ids. On the Sierra Leone run the evaluator reproduced 509 of 509 probe cells and 1,168 of 1,168 analytics cells (73 indicators × 4 org units × 4 periods).

The same facts, in prose and with authoring advice, belong in the `dhis2-indicators` skill (`references/aggregate-indicators.md`, `references/testing.md` Part A). **Several claims there do not hold on 2.43.2** (A3, A4, A13): keep the two in step.

Probe data used below (all in 2026): org units A, B, C, D under one parent. ANC 1st visit (`a`, SUM, Fixed/Outreach): A Jan 10+1, Feb 20+2, Mar 30+3; C Jan 0, Feb 4; D Jan 6. ANC 2nd visit (`b`): A Jan 5; B Jan 8; C Jan 0. Population <1 (yearly, AVERAGE_SUM_ORG_UNIT): A 1000, B 500, C 300. Doctors (six-monthly, AVERAGE_SUM_ORG_UNIT): A 4+2, B 3. A data element with AVERAGE: A Jan 10, Feb 20; B Jan 40. One with COUNT: A Jan 7, Feb 9 (Fixed) + 11 (Outreach); B Mar 0. A yearly TRUE_ONLY (SUM): A, B true. Org unit group with A and B.

## Missing values, zeros and division

| # | Behaviour | Probe evidence |
|---|---|---|
| A1 | **Missing operands.** An operand with no data reads 0 when at least one other operand visited on the same side has data. When every visited operand of a side is missing, that side is null and the indicator has **no value**. A constant does not rescue a side: `#{a} + 0` is null where `a` has no data. "Visited" follows lazy evaluation: `if()` evaluates the condition and the branch taken only. Operands inside `isNull`, `isNotNull` and `firstNonNull` don't count (so `if(isNull(#{b}), -1, #{b})` gives −1 without any data). `OUG{}`, `C{}` and `[days]` are not operands for this rule; `R{}` is. | `#{a}+#{b} \| 1`: B Jan 8 (a missing → 0), D Jan 6; B Feb no value. `#{a}+0 \| #{b}` at B Jan: no value. `if(#{b}>0, 7, #{a})` at A Feb (b missing, a = 22): 22; `if(#{b}>0, #{a}/#{b}, 7)` at D Jan (b missing): no value (only `b` was visited). `firstNonNull(#{b})` with no data: 0. |
| A2 | **Denominator 0 or null → no value** (the analytics row is omitted). | `#{a} \| #{b}`: C Jan (b = 0) no value; D Jan (b missing) no value. |
| A3 | **Zeros reach analytics only when the data element has `zeroIsSignificant=true` AND the system setting `keyIncludeZeroValuesInAnalytics` is on.** 2.43.2 *stores* a 0 for a data element with `zeroIsSignificant=false` (the dataValueSets import keeps it), but analytics drops it. With the setting off, even zeros of `zeroIsSignificant=true` elements are dropped. A dropped zero is a missing value (A1), so a facility reporting 0 shows no value instead of 0 %. | Import of 0 for a zIS=false element: `updated: 1`, value readable via `/api/dataValueSets`; analytics row absent. Key on: zIS=true zero → 0; key off: absent. |
| A4 | **Division by zero inside a numerator or denominator gives NaN** in the analytics response (row value `"NaN"`) for that org unit and period; no E7132 on 2.43.2, rounding on or off. A missing divisor counts as 0 when the dividend has data (A1). `log(0)` gives `-Infinity`. | `#{a}/#{b} \| 1`: A Feb (b missing, a = 22) NaN; C Jan (0/0) NaN; B Jan (a missing, b = 8) 0. |
| A5 | **Parent org units and longer periods: every operand is aggregated first (over org units and months, by its aggregation type), then the whole expression is evaluated once on those totals.** So the parent value is Σnum ÷ Σden, and a ratio written inside the numerator is the ratio of the totals, not a sum of facility ratios. An org unit with only numerator data still adds to Σnum. | `#{a} \| #{b}` at the parent, Jan: (11+0+6)/(5+8+0) = 1.3077; `#{a}/#{b} \| 1` at the parent, Jan: also 1.3077 (not 2.2 + 0 + NaN). SL run: “Dropout rate Penta 1 - Measles” as ((P1−M)/P1) ÷ 1 and as (P1−M) ÷ P1 gave the same root value, −17.79. |
| A20 | **A side the server can't parse reads 0, silently.** E.g. the old reporting-rate form `#{dataSet.REPORTING_RATE}` ("Expression is not well-formed" from `/api/expressions/description`): a numerator gives 0 at every org unit and period, a denominator gives no value. The metadata import accepts the text. | `#{a}*(#{<ds>.REPORTING_RATE}/100) \| 1`: 0 everywhere; `#{a} \| #{b} +* 2`: no value. |
| — | An unknown data element uid is a missing operand (A1); the expression check still says "Valid". | `#{Abcdefghijk}+#{a} \| 1` = a. |

## Aggregation over time and org units

| # | Behaviour | Probe evidence |
|---|---|---|
| A6 | Each operand follows its data element's **aggregationType**: **SUM** adds; **COUNT** counts stored values (a stored 0 counts, 3 reports of 7, 9 and 11 give 3); **AVERAGE** is the mean of every value in the period and org units (no weighting by days, missing months don't count); **AVERAGE_SUM_ORG_UNIT** per org unit is Σ(value × days of the data period) ÷ days of the query period, then summed over org units — data in a **longer** period than the query (yearly population, six-monthly staff for a month or quarter) is used as it is. | AVERAGE: parent Jan (10+40)/2 = 25, parent Q1 (10+20+40)/3 = 23.33, A Q1 15. COUNT: parent Q1 4. Doctors (S1 = 181 days) for 2026: A 6 × 181/365 = 2.975; for Jan or Q1: 6. Population for Jan, Q1 and 2026: 1000. |
| A7 | **SUM data in a period longer than the query period is not used**: a yearly SUM data element has no value for any month or quarter. | Yearly TRUE_ONLY (SUM): Jan, Q1 no value; 2026: 2 at the parent. |
| A18 | `#{de}` is every category option combo and every attribute option combo of the data element. | `#{a}` = Fixed + Outreach. |

## Factor, annualization, rounding

| # | Behaviour | Probe evidence |
|---|---|---|
| A8 | **annualized: × 365 ÷ days in the query period** (Jan 365/31, Feb 365/28, Q1 365/90, year 1). | `#{a} \| #{pop}` ×100 annualized, A: Jan 1.1 × 11.774 = 12.95; Q1 6.6 × 4.0556 = 26.77; 2026 6.6. |
| A9 | Values are rounded to the indicator's **decimals**; unset → 2 decimals. | decimals unset 130.77; 0 → 131; 1 → 130.8; 3 → 130.769. |

## Operands and functions

| # | Behaviour | Probe evidence |
|---|---|---|
| A14 | `OUG{group}` = number of org units of the group at or below the queried org unit (1 or 0 at a facility), whatever the data. | parent 2, A 1, C 0. |
| A15 | `C{constant}` = its value; `[days]` = days of the query period. | 3.14; Jan 31, Feb 28, Q1 90. |
| A16 | `#{de.*.aoc}` and `#{de.coc.aoc}` select one attribute option combo. | equal to `#{de}` / `#{de.coc}` with the default AOC. |
| A17 | Functions: `if`, `isNull`, `isNotNull`, `firstNonNull` (0 when every argument is null), `greatest`, `least` (missing arguments read 0 unless all are missing), `log`, `log10`, `^`, `%`. A bare boolean as a whole side (e.g. numerator `isNotNull(#{b})`) reads **0**. | `greatest(#{b},#{a})` B Feb (both missing) no value; `least(#{b},#{a})` B Jan 0; `isNotNull(#{b}) \| 1` = 0 everywhere; inside `if()` it works. |
| A21 | `R{ds.REPORTING_RATE}` = 100 × complete registrations ÷ (assigned org units × periods of the data set in the query period), at or below the queried org unit; `ACTUAL_REPORTS`, `EXPECTED_REPORTS` the two counts. The rate is rounded to 2 decimals before it enters the expression, except when the query has `skipRounding=true` (SL run: 577.73 rounded vs 577.748 unrounded at the root). An assigned parent org unit counts as an expected report. Without any complete registration on the instance, analytics fails for these operands with **E7144** ("a referenced table does not exist"). A rate of 0 is a value for A1. | Child Health assigned to the root and 3 facilities, all facilities complete: parent 75 %; after removing the root: 100 %. |

## Import and test-instance behaviours

| # | Behaviour | Consequence |
|---|---|---|
| A10 | `POST /api/dataValueSets` takes **one data set per request**: a payload spanning several fails with "Data set detection failed, found multiple sets" (and values of a data element in no data set fail with **E8003**). 2.43.2 reports new values as `updated`, not `imported`. | `import_data.py` splits by data set and checks `imported + updated == values sent`. |
| A11 | A data element with a **`commentOptionSet`** makes every value import fail with **HTTP 500** "Cannot invoke DataEntryGroup.values() because valid is null" (single values and data value sets). Removing the comment option set fixes it. | `import_data.py` removes it on the test copy and records it. |
| A12 | On a fresh 2.43 instance with empty **`dataOutputPeriodTypes`**, aggregate analytics returns **no rows at all** (no error). | `import_data.py` sets it. |
| A13 | The importer **refuses** a value whose option combo is not in the data element's (data set) combo: **E8024**, and the whole data set request is rejected (HTTP 409, nothing imported). A 2.43 instance also refuses to create option combos whose options don't match their combo's categories (**E1130**, e.g. COCs left from an earlier version of a combo). A `#{de.coc}` with a COC of another combo therefore never gets new data. | Lint `coc-not-in-de-combo` (ERROR) and `coc-not-fitting-combo` (WARN); `gen.py` fills neither; such terms read nothing on a test copy. The claim "the importer accepts a COC not in the combo" (older versions) does not hold on 2.43.2. |
| A19 | `INTEGER_POSITIVE` refuses 0 (E8122); a category option outside its start/end dates makes values with its COC/AOC invalid. | `gen.py` writes no 0 for INTEGER_POSITIVE and skips invalid option combos. |

## Probe recipe

1. A dedicated probe config (its own root and 3–4 org units, never the run's root: probe data would change the run's root totals), `"test_instance": true`.
2. Write a small data value set with one situation per org unit or month (numerator only, denominator only, a zero, a zero denominator, a value in a longer period), load it with `import_data.py --file probe-values.json` (creates the org units, assigns the data sets, runs analytics).
3. Query probe pairs that differ in ONE detail, all at once (each pair is one indicator): `probe.py '#{a} | #{b}' '#{a}+0 | #{b}' --ou <parent> <A> <B> <C> --pe 202601 202602 2026Q1 --skip-rounding`.
4. Record version, data and values here; check the evaluator against `probes.jsonl` (every probe with `skipRounding`).
