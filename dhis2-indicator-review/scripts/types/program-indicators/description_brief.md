You are writing the DHIS2 `description` field for corrected program indicators.

Input: `{INPUT}` (a JSON list). Each item has the CORRECTED definition (`analyticsType`, `aggregationType`, `stage`, `expression`, `filter` with UIDs replaced by "Stage.Field" and attribute names, `boundaries`, `disaggregatedBy`) plus, for context only, the old definition (`oldExpression`, `oldFilter`, `oldBoundaries`), the old description (`oldDescription`) and the list of changes (`changesMade`).

For every item, write a description of what the CORRECTED indicator counts, for people who read it in DHIS2:

- Plain English, 1–3 sentences, at most 450 characters. No markdown.
- Describe the corrected definition only. Never mention fixes, bugs, history or the review.
- Unit: EVENT + `V{event_count}` (or `1`, or `V{enrollment_count}` with COUNT) → number of <stage> events in the period; EVENT + `V{tei_count}` → number of clients with at least one matching event; ENROLLMENT → number of clients (enrollments); SUM/AVERAGE of a field → the total/average of that field.
- Which events: `stage` names the stage when the indicator is tied to one. When it is null, take it from a `V{program_stage_id} == '…'` condition in the filter (write the stage name, which the input gives in place of the UID), else from the stage the filter's fields belong to, else say "events of the <program>" using the program name in the input. Don't invent a disease or service the input doesn't name.
- Fields of another stage: in an EVENT indicator, a condition on a field of a different stage than the counted event can never be met (the event row only carries its own stage's values). Describe the condition as written, and set `concern` saying it can't match.
- One-sided date windows: `d2:daysBetween(A, B) <= n` with no lower limit also matches B before A; describe it as written ("no more than n days after"), and raise a `concern` only when the name promises a two-sided window.
- Period logic from the boundaries, in words: event date in the period; enrolled on or before the period end, using the latest event up to the period end (ENROLLMENT_DATE before end + EVENT_DATE before end); cohort enrolled N months earlier (offsets); a date field within the period.
- Field names in words, option values in double quotes; summarise long option lists ("any of 12 codes starting with …").
- `contains` = "includes"; `containsItems` = "includes exactly"; `d2:hasValue(x)` = "with a recorded x"; `d2:daysBetween(a, b) > -n` = "b no more than n days before a".
- End with "Disaggregated by …" (short category names) when `disaggregatedBy` is not empty.
- Describe faithfully even if a condition looks odd. Don't invent conditions, and don't correct the definition in words.

Set `concern` to one sentence if the corrected definition still doesn't match the indicator name (for example the name says "positive" but the filter has no result condition, or the name says "in the last 6 months" but there is no such window); otherwise `null`. Be conservative: a concern is shown to the reviewers, so only flag real mismatches.

Write `{OUTPUT}` as one JSON object:

```json
{"<id>": {"description": "...", "concern": null}}
```

Then check it: every input id present, every description ≤ 450 characters, valid JSON. Reply with the number of descriptions written and the ids you were unsure about (one line each, with the reason).
