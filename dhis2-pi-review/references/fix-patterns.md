# Fix patterns: finding → fix, and when a fix is "clear"

A fix is proposed automatically only when it is **deterministic**: the finding, the metadata and the meeting decision together leave exactly one correct change. Otherwise the item goes on the *not proposed* list with the decision that is needed. Each row has the test used to decide whether it is clear. The generic patterns are implemented in `scripts/fix/build_fixes.py`; program-specific replacements go in `fixes-explicit.json` with a justification.

## Clear patterns (generic ones implemented in `scripts/fix/build_fixes.py`)

| Pattern | Finding (lint check) | Fix | Clear when | Verified by |
|---|---|---|---|---|
| Unsupported function | `d2:addDays` (I4) | `d2:addDays(D,n) > V{analytics_period_end}` → `d2:daysBetween(V{analytics_period_end}, D) > -n` | always (same meaning) | probe: same count on test data |
| Count aggregation | `count-expression-wrong-aggregation`, `no-aggregation-type` (E2–E4c) | `aggregationType` AVERAGE / MAX / MIN / none → COUNT | the expression is exactly `V{event_count}`, `V{enrollment_count}` or `1` (`V{tei_count}` and value expressions need a decision) | scenario |
| Event variable in enrollment PI | `event-var-in-enrollment-pi` (E1) | `V{event_count}` → `V{enrollment_count}` | expression is exactly `V{event_count}`; filter has no other event variable | scenario |
| Reversed period test | `V{analytics_period_start} >= X && V{analytics_period_end} <= X` | `X >= V{analytics_period_start} && X <= V{analytics_period_end}` | regex matches the full pattern on one field | scenario |
| Contradiction | same field `== a && == b`; `!= v && == v` | `(== a || == b)`, or drop the clause the name contradicts | the indicator name says which reading is meant | scenario per PI |
| Option-code literal | `literal-not-an-option-code` | replace with the single matching code | exactly one code matches after normalising case/space/punctuation, or a documented 1:1 synonym (e.g. a transposed code whose option name makes the intended code certain) | scenario |
| Precedence | `mixed-and-or-without-parens` | wrap each run of `||`-joined terms in brackets | every bracketed run tests **one field** against alternative values (checked in code); duplicates removed | scenario |
| Offset type | `offset-without-period-type` (P1) | `offsetPeriodType: Monthly` | offsets are month counts (6/12/24) | probe + scenario |
| Null mapping entry | `null` in `categoryMappingIds` (E7) | remove nulls | the remaining mappings still cover every category of the PI's combos | probe + scenario |
| Blank number as 0 | numeric comparison satisfied by 0 in an ENROLLMENT PI without `d2:hasValue` (C4) | add `d2:hasValue(field) &&` | the comparison is meant as "a value in this range" (checked by name) | scenario |
| Enrollment window for "active at end of period" | "active/current" name with enrollment AFTER_START boundary | keep only ENROLLMENT_DATE BEFORE_END; add EVENT_DATE BEFORE_END | name says "active", "on treatment", "currently" at period end | probe (C5) + scenario |
| Malformed mapping filter | `#{a}#{a}` | write the band from the option name | option name is a plain band | scenario |
| Age bands from option names | `mapping-range-gap`, `mapping-range-vs-name` | `"a-bYrs"` → `>= a && < b+1`; `"a+Yrs"` → `>= a`; `"<aYr"` → `< a`; months likewise | the option names of neighbouring options are consistent (`0-6Mths` next to `6-23Mths` is not); the mapping doesn't mix two overlapping schemes | scenario + engine check of every PI using the mapping |
| Mapping option without filter | `mapping-option-without-filter` | filter on the single matching code | exactly one unused code matches the option name | scenario |
| Prefix-coded options | literal list missing codes / non-codes | `contains(field, 'GMC')` per prefix | all codes of the option set start with the group prefix; confirmed by another implementation | scenario |

Every pattern asserts its match exactly once per item (whitespace-insensitive) and records a change note.

## Not clear (put on the "not proposed" list)

| Pattern | Why it needs a decision |
|---|---|
| Event PI reading several stages | Enrollment type changes the unit counted (people vs events/tests) and which date the period applies to; DHIS2 reads only the latest event per stage. |
| Mapping on another stage than the PI | Needs new mappings (e.g. age from date of birth) or a type change. |
| Field not in any stage | Add it to the stage (program change; no historic data) or drop the condition. |
| MAX "return a date" | A PI returns a number; encoding (YYYYMM?) and source are a design choice. |
| Clinical windows (`== 42` days) | `>= 42` vs a window is a clinical definition. |
| Unmapped values | Add an "Unknown/Other" option (changes the category model) or accept dropping. |
| Overlapping roll-up options | Splitting categories changes the data set mapping. |
| Literal on the wrong field | The intended field is a guess. |
| `contains` → `containsItems` where substrings may be intended | e.g. "TB" vs "MDR-TB": a programme definition. |
| Definition doesn't match the name | Rename or redefine: per indicator. |

## Interaction rules

- Fixes on one item are applied in a fixed group order to the same object; the item is approved/exported as a whole.
- A fix can be correct and the item still broken (another open topic). Show it ("still fails after the fix: reason") instead of holding the fix back; export such items separately.
- Fixes found during testing that weren't agreed are proposed as separate, labelled groups (here "NEW: found in testing").
