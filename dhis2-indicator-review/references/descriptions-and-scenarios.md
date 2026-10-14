# Writing descriptions and scenarios

## Per-indicator descriptions (sub-agent prompt)

`scripts/fix/prep_desc.py` writes the batches (~45 PIs each). Give each batch to a sub-agent with the brief in `scripts/types/program-indicators/description_brief.md`. In short:

> You are writing the DHIS2 `description` field for corrected program indicators. Read the input file (a JSON list). Each item has the CORRECTED definition (analyticsType, aggregationType, expression, filter with UIDs replaced by "Stage.Field" names, boundaries, disaggregatedBy) plus, for context only, the old definition, a description of the old behaviour, and the list of changes.
>
> For every item write a description of what the CORRECTED indicator counts, for people who read it in DHIS2:
> - Plain English, 1–3 sentences, at most 450 characters. No markdown.
> - Describe the corrected definition only. Never mention fixes, bugs, history or the review.
> - Unit: EVENT + V{event_count} → number of <stage> events in the period; EVENT + V{tei_count} → number of clients with at least one matching event; ENROLLMENT → number of clients (enrollments); SUM → total of the field.
> - Period logic from the boundaries in words (event date in the period; enrolled on or before the period end using the latest visit up to the period end; cohort enrolled N months earlier; date field in the period).
> - Field names in words, option values in double quotes; summarise long option lists.
> - `contains` = "includes"; `containsItems` = "includes exactly".
> - End with "Disaggregated by …" (short category names).
> - Describe faithfully even if a condition looks odd. Don't invent conditions.
> Set `concern` to one sentence if the corrected definition still doesn't match the indicator name; otherwise null. Be conservative.
> Write `{"<id>": {"description": "...", "concern": ...}}`, validate (all ids, ≤ 450 chars), reply with the count and doubtful items.

Add program-specific phrasing at the end of the brief when the program has recurring constructs, e.g. how to say "active at the end of the period" when it is defined as status plus a next-appointment window (`status == 'Active' && d2:daysBetween(V{analytics_period_end}, next appointment) > -28`). The brief itself is in `scripts/types/program-indicators/description_brief.md`.

Then: read the concerns (show them to reviewers, don't fix silently), and hand-adjust descriptions whose meaning changed afterwards (overrides file).

## Scenarios (intent tests)

A scenario proves that a fix makes the indicator count what it should. Rules:

1. **One fix (or one interaction) per scenario**, on a real indicator of the group.
2. **Its own org unit**, so other scenarios and the bulk test data can't change the value.
3. **2–4 clients**, at least one that must count and one near-miss that must not (e.g. appointment 26 vs 39 days before period end; registration date in vs before the period; a later visit after the period).
4. **Expected values before and after, worked out by hand** from the indicator's meaning and written into the scenario with a one-line note explaining them. "error" is a valid "before".
5. **Disaggregation-safe:** fill every category so each client matches exactly one option (the builder checks it); otherwise event PIs drop the client and the test proves nothing.
6. **Cells** where the fix is about breakdowns (e.g. age 14.5 lands in 0-14).
7. Where the real indicator can't show the effect (still broken by another topic), test a **probe copy** of the filter without the breakdown and say so.
8. Generate **new UIDs** for every regeneration (deleted tracker objects keep their UIDs).

Query each scenario's PI at its org unit for the scenario period, before and after, and record pass/fail with both values in the evidence document.
