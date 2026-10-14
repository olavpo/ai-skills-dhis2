# Fix patterns for aggregate indicators: finding → fix, and when a fix is "clear"

A fix is proposed automatically only when it is **deterministic**: the finding, the metadata and the meeting decision together leave exactly one correct change. Otherwise the item goes on the *not proposed* list with the decision that is needed. The generic patterns are in `scripts/types/indicators/patterns.py` (`GROUPS`, run by the core `fix/build_fixes.py`); instance-specific replacements go in `fixes-explicit.json` with a justification. Behaviour ids (A…) refer to `behaviours.md`.

## Clear patterns

| Group | Kind | Finding (lint check) | Fix | Clear when | Verified by |
|---|---|---|---|---|---|
| `legacyrate` | indicator | `legacy-reporting-rate-operand` (A20) | `#{dataSet.REPORTING_RATE}` → `R{dataSet.REPORTING_RATE}` (also ACTUAL/EXPECTED_REPORTS) | the uid is a data set in the export | SL scenario `fic-adjusted`: 0 → 10.14 as worked out by hand; engine check |
| `cocremap` | indicator | `coc-not-in-de-combo` (A13) | `#{de.<COC of another combo>}` → `#{de.<COC of the current combo>}` | exactly one COC of the data element's combo(s) has the same category options and fits its combo; other terms are left alone (reported as unresolved) | SL scenario `llitn` (with `decount`) |
| `drop100` | indicator | `factor-100-and-times-100` | numerator `(X) * 100` → `X` (Percent type kept) | the factor is 100 and `* 100` applies to the whole numerator (a trailing `* 100` on the last term of a sum is refused) | engine check (the SL demo indicator reads only stale COCs, so it has no value on the test copy, see *Not clear*) |
| `percenttype` | indicator | `percent-name-number-type` | indicator type Number (×1) → the Percent type (×100) | exactly one indicator type has factor 100, the numerator has no `* 100`; **confirm per indicator** (some "rates" are per one) | SL scenario `nvp`: 0.8 → 80 |
| `movedivisor` | indicator | `division-in-numerator-denominator-1` (A4, A5) | numerator `X / Y`, denominator 1 → numerator `X`, denominator `Y` | the numerator is a single top-level division whose divisor reads data | SL scenario `dropout`: same value before and after (A5); engine check: NaN cell → no value |
| `decount` | dataElement | `de-aggregation-count`, `de-aggregation-none` (A6) | aggregation type COUNT / NONE → SUM | value type NUMBER / INTEGER / INTEGER_POSITIVE / INTEGER_ZERO_OR_POSITIVE | SL scenario `llitn` |
| `deaverage` | dataElement | `de-aggregation-average` (A6) | aggregation type AVERAGE → SUM | a numeric count entered monthly or more often, and the name doesn't suggest a stock, population, rate, score, staff, beds or target; **confirm per data element** | SL scenario `ipt-tba`: 13.33 → 40 and 20 → 60 |

Each generator records a change note per edit and raises `FixError` when its test fails; the item then goes on the *not proposed* list with that message. A data element change affects every indicator, pivot table and map that uses it: the review page lists the indicators in scope that read it (`aux_users`), and the approval page shows them; other consumers (visualizations, validation rules, predictors) are outside the export and must be checked by the owners (`notes` in the config).

Explicit data element edits (fixes-explicit.json): `{"group": "<group>", "aux": "<uid or name>", "set": {"aggregationType": "SUM"}, "justification": "..."}`; only `aggregationType`, `zeroIsSignificant` and `valueType` may be set.

## Not clear (put on the "not proposed" list)

| Finding | Why it needs a decision |
|---|---|
| `coc-not-fitting-combo` | The option combos are left over from an earlier version of the combo and the historic data is stored under them; rebuilding the combo and migrating data, or rewriting the indicators against the current categories, is a data-owner decision. |
| `coc-not-in-de-combo` with 0 or 2+ matches | No single current option combo has the same options. |
| `inner-division` | Guarding (`if(b > 0, a / b, 0)`) and moving the divisor give different values where b is 0, and the right one depends on the meaning. |
| `overlapping-operands` | A term is counted twice; the intended term (usually another option combo) can't be inferred. |
| `population-rate-not-annualized`, `annualized-without-population` | Whether a period value or an annual rate is wanted. |
| `rate-name-denominator-1`, `count-name-percent-type`, `name-factor-mismatch`, `numerator-equals-denominator` | Either the name or the definition is wrong. |
| `sum-data-long-period` | A yearly SUM data element has no monthly/quarterly value (A7). AVERAGE_SUM_ORG_UNIT would show the yearly value in every month and quarter, which is a change of meaning for other uses. |
| `de-in-no-data-set`, `de-not-numeric`, `de-domain-tracker` | Which data set should collect the data element, or which data element was meant. |
| `unknown-de`, `unknown-coc`, … | The intended object can't be inferred from a missing uid. |
| `duplicate-definition` | One of the indicators is probably meant differently. |
| `de-aggregation-average` on stocks, populations, scores | AVERAGE_SUM_ORG_UNIT or LAST may be meant. |
| `numerator-zeros-dropped` (INFO) | Setting `zeroIsSignificant` changes storage for every form using the data element; whether a reported 0 must show as 0 is a programme decision (A3). |
