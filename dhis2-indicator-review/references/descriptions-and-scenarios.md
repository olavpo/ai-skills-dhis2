# Writing descriptions and scenarios

## Descriptions of the corrected items (sub-agents)

`run.py descriptions` (`scripts/fix/prep_desc.py`) writes the changed items in batches (`desc_in/batchN.json`, ~45 items each) with UIDs replaced by names. Give each batch to a sub-agent with the type's brief, verbatim, with `{INPUT}` and `{OUTPUT}` filled in:

- program indicators: `scripts/types/program-indicators/description_brief.md`
- aggregate indicators: `scripts/types/indicators/description_brief.md`

What every brief asks for: a plain-language description of what the CORRECTED item counts, 1–3 sentences, ≤ 450 characters, no history of the fix, faithful to the definition even where it looks odd, and a `concern` (one sentence) when the corrected definition still doesn't match the name. Add program-specific phrasing at the end of a brief when the set has recurring constructs (for example how to say "active at the end of the period" when it is defined as a status plus a next-appointment window).

Then show every concern to the reviewers (don't fix it silently), and hand-adjust descriptions in `desc_overrides.json` where needed. `run.py payload` merges them.

## Scenarios (intent tests)

A scenario proves that a fix makes an indicator count what it should. It is written by hand in `work_dir/scenarios.json`; formats in the type's `scen.py` docstring and `scenarios.example.json`.

Rules for every type:

1. **One fix (or one interaction) per scenario**, on a real indicator of the group.
2. **Its own org unit** (the builder creates one per scenario), so other scenarios and the bulk test data can't change the value.
3. **A near miss next to every case that counts**: something that must *not* be counted or must not change the value.
4. **Expected values before and after, worked out by hand** from the indicator's meaning — never from the evaluator — with a one-line note explaining them. `"error"` is a valid "before"; `null` means "no value".
5. Where the real indicator can't show the effect (still broken by another open topic), test a **probe copy** and say so, or record that the fix is verified by lint and the engine check only.

### Program indicators

- **2–4 clients** per scenario (e.g. appointment 26 vs 39 days before the period end; registration date in vs before the period; a later visit after the period).
- **Disaggregation-safe:** every counted record must match exactly one option in every mapped category, or event PIs drop it and the test proves nothing. The builder fills fields the scenario doesn't set and refuses records that would match 0 or 2 options.
- **Cells** where the fix is about breakdowns (e.g. an age of 4 lands in "0-4 years").
- Generate **new UIDs** for every regeneration (deleted tracker objects keep their UIDs).

### Aggregate indicators

- **Data values per month** (`dataElement`, `coc`, `period`, `value`) in the scenario's org unit; yearly data (population) as a yearly period.
- Choose values that make the fix **visible as a different number**: a COUNT → SUM fix needs values other than 1 and more than one month; an option-combo fix needs data on both the old and the current combo; a factor fix needs a ratio that isn't already a round number.
- Expected values are what analytics shows: the indicator's factor, `annualized` (× 365 / days in the period) and rounding to its decimals (2 when unset). Expect "no value" when every operand on one side is missing.
- Use `cellsAfter` with `"pe"` (per month) when the fix is about how values aggregate over time.
- Option combos that a fresh instance refuses (E1130/E8024) can't hold scenario data; verify such fixes by lint and the engine check, and say so.

`run.py check` queries each scenario's indicator at its org unit for the scenario period, before and after, and writes pass/fail with both values to `results.json` and `E2E-TEST.md`.
