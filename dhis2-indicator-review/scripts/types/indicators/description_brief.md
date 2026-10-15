You are writing the DHIS2 `description` field for corrected aggregate indicators.

Input: `{INPUT}` (a JSON list). Each item has the CORRECTED definition (`numerator`, `denominator` with UIDs replaced by "Data element.Option combo" names, `indicatorType` with its factor, `annualized`, `decimals`) plus, for context only, the old definition (`oldNumerator`, `oldDenominator`, `oldIndicatorType`), the old descriptions (`oldDescription`, `oldNumeratorDescription`, `oldDenominatorDescription`) and the list of changes (`changesMade`).

For every item, write a description of what the CORRECTED indicator shows, for people who read it in DHIS2:

- Plain English, 1–3 sentences, at most 450 characters. No markdown.
- Describe the corrected definition only. Never mention fixes, bugs, history or the review.
- Say what is divided by what, in words: "Live births at the PHU as a percentage of ANC 1st visits (fixed and outreach)". Use the factor: ×100 → "percentage of", ×1000 → "per 1,000", ×1 with a denominator → "per" / "ratio of", ×1 with denominator 1 → "number of".
- Name the data elements and the option combos that matter in words ("fixed and outreach", "under 1 year"); summarise long lists ("live births attended by any skilled cadre at the PHU (6 cadres)"). Don't list UIDs.
- `#{X}` without an option combo = all disaggregations of X. `R{dataset.REPORTING_RATE}` = the reporting rate of that data set, in percent. `OUG{group}` = number of org units in that group. `C{x}` = the constant x.
- `annualized: true` → add "annualized" (the value is scaled to a full year). Mention a yearly population denominator as such ("estimated population under 1 year").
- Describe faithfully even if a term looks odd. Don't invent terms, and don't correct the definition in words.

Set `concern` to one sentence if the corrected definition still doesn't match the indicator name (for example the name says "coverage" but the denominator is not a population, or the name says "rate" but the denominator is 1); otherwise `null`. Be conservative: a concern is shown to the reviewers, so only flag real mismatches.

Write `{OUTPUT}` as one JSON object:

```json
{"<id>": {"description": "...", "concern": null}}
```

Then check it: every input id present, every description ≤ 450 characters, valid JSON. Reply with the number of descriptions written and the ids you were unsure about (one line each, with the reason).
