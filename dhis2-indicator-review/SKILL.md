---
name: dhis2-indicator-review
description: "Validate, review with the programme team, fix, test end to end and get approval for many DHIS2 indicators at once: program indicators (with their program disaggregation / category mappings) or aggregate indicators (with the data elements they read). Use when someone wants to audit or clean up a whole set of indicators: 'validate all the PIs in this program', 'which of our indicators are broken', 'do these indicators compute what their names say', 'check all the HMIS indicators before the reporting cycle', 'prepare an indicator review meeting', 'make a page where the team can decide/approve the fixes', 'fix the indicators we agreed on and give me an import file', or when a metadata export with dozens to thousands of indicators needs a quality pass before a migration or upgrade. Bundles the scripts (lint, synthetic data, independent evaluator, fix generator, end-to-end test, meeting and approval pages). For writing or debugging a single indicator use dhis2-indicators; for data-integrity check remediation use dhis2-integrity."
---

# DHIS2 indicator review

Takes a large set of indicators from "nobody knows if these are right" to "every change decided by the programme team, tested, approved and importable". One process for every supported type; each type has an adapter with its own checks, test data and evaluator.

| Type (`"type"` in the config) | Items | Aux objects fixes may change | Engine knowledge |
|---|---|---|---|
| `program-indicators` (default) | program indicators of one program | the program's category mappings | `references/program-indicators/behaviours.md` |
| `indicators` | aggregate indicators (all, or a scope) | the data elements they read | `references/indicators/behaviours.md` |

Validation rules and predictors use the same expression engine and would fit as further types; they aren't built.

| # | Phase | Proves / decides | Main outputs (in `work_dir`; `<p>` = `pi` or `indicator`) |
|---|---|---|---|
| 1 | Validate | what is wrong, how sure we are | `<p>-status.csv`, `<p>-findings.csv`, `<P>-VALIDATION.md` |
| 2 | Review | which topics to fix, and how (meeting) | `<p>-review.html`, `<p>-dictionary.html`, `<P>-REVIEW-DECISIONS.md` |
| 3 | Fix | exactly what changes (only clear fixes) | `fixes.json`, proposed payload + aux file |
| 4 | Test end to end | the payload imports and counts the right thing | `results.json`, `E2E-TEST.md` |
| 5 | Approve | which changes are accepted | `<p>-fix-review.html`, standalone page, import files |
| 6 | Import | (done by a DHIS2 administrator) | |

The semantics behind every finding are in the `dhis2-indicators` skill (`references/program-indicators.md`, `references/aggregate-indicators.md`); this skill's `references/<type>/behaviours.md` lists the engine behaviours the scripts encode, with IDs (C1, E7, A5…) used across the references.

## Principles

Each one prevents a failure seen in practice.

- **Two independent checks.** The *engine check* compares analytics with an independent evaluator run on the raw test data; the *intent check* uses lint rules and hand-written scenarios with expected values from the indicator's meaning. When the engine check agrees everywhere, the problems are in the configuration; most real bugs live in the gap between the two.
- **A disposable test instance of the production version, never production.** The data and fix steps write test data, probe objects and metadata. Scripts refuse to write unless the config says `"test_instance": true`.
- **Verify engine behaviour, don't assume it.** Before a surprising behaviour explains a finding or shapes a fix, prove it with probe objects (`run.py probe`). Behaviours differ between versions (an inner division by zero is E7132 on earlier versions and `NaN` on 2.43.2), so re-verify the behaviours file on another version.
- **Group by cause, decide by group, act per item.** A meeting can decide 20–30 topics, not 2,000 indicators. Items that need their own decision are split out ("Check each item"), and that verdict is never read as group approval.
- **Only propose what is clear.** A fix is generated only when the finding, the metadata and the decision leave exactly one correct change (`references/<type>/fix-patterns.md`). Everything else goes on the *not proposed* list with the decision needed. When unsure, ask the user rather than guess.
- **Nothing changes silently.** Every rewrite asserts its pattern matched exactly once; every import is read back and diffed; every number in a write-up comes from a script that can be re-run.
- **The programme team owns the decisions.** You propose; they decide, with name and time recorded; only approved items are exported.

## Before you start: ask

Use one `AskUserQuestion` round for what you can't find out yourself:

- **What is in scope and which export.**
  - Program indicators: a dependency export of the program (`GET /api/programs/<uid>/metadata.json`), plus the data sets the PIs report into if the dictionary should show that link.
  - Aggregate indicators: the indicators, indicator types, constants and org-unit groups, plus a dependency export of every data set holding the data elements they read (`examples/sl-aggregate/export_sl.py` shows one way). Limit the scope with `"indicators"` in the config when the set is large.
  - Exports from production are read-only; anonymise them before sharing (`dhis2-metadata`).
- **The production DHIS2 version.** The test instance must match it.
- **Who decides, and how they will see the pages.** The programme team decides in a meeting (meeting page as an artifact with shared decisions). Reviewers approve on the approval page: the artifact for people with accounts, the standalone file otherwise.
- **Other consumers of these definitions** (report pipelines, apps that compute the same numbers). They are evidence of intent for ambiguous definitions and need impact notes on the approval page (`notes` in the config).

## Set up a run

1. Get an empty disposable instance of the production version (`dhis2-instances` skill if available; otherwise ask the user). "Reset to empty" means delete and re-create.
2. Write `pireview.json` next to the export (copy `scripts/pireview.example.json`; keys in `references/data-formats.md`). Set `"type"`. Credentials go only in the environment: `DHIS2_URL`, plus `DHIS2_TOKEN` or `DHIS2_USER` and `DHIS2_PASSWORD`.
3. Import the metadata into the test instance with the `dhis2-metadata` skill (`transform_metadata.py --unshare --delocalize`, then `import_metadata.py --passes 2`). Org-unit references fail with E5002; that's expected, and the scripts create their own test org units. For program disaggregation run `POST /api/maintenance?categoryOptionComboUpdate=true`.

Every step runs through one command: `R="python3 <skill>/scripts/run.py --config pireview.json"`, then `$R <step>`. `$R steps` lists every step and the script it runs for the configured type. All scripts read from and write to `work_dir`; every script has a docstring with its inputs, outputs and options. A step that a type doesn't support exits with status 2.

## Phase 1: validate

```bash
$R lint && $R syntax          # static checks (ids in the type's lint.py docstring); server check of every text
$R gen && $R import-data      # synthetic data (witnesses, noise, controls, edge values); test org units, import, analytics
$R query && $R compare        # analytics per item; engine check against the independent evaluator
$R assemble                   # status per item, findings, <P>-VALIDATION.md skeleton
```

Then do the parts a script can't:

- **Read by hand** every item the generator couldn't exercise (`unsat.json` for PIs) and every item that stays at zero or without a value despite its test data. Add what you find as `manual-findings.json` and re-run `assemble`.
- **Explain every engine mismatch.** It is either a DHIS2 behaviour the evaluator doesn't know or a real engine problem. For the first, probe it, then add it to the evaluator and the behaviours file. Don't leave a mismatch unexplained.
- **Read `import.json`** (aggregate): what `import-data` changed on the test copy to make data land. This includes zeroIsSignificant, closed periods, comment option sets and option combos. Each change is a test-copy repair, not a fix for production; mention the relevant ones in the write-up.
- **Write the narrative** into the TODOs of `<P>-VALIDATION.md`. Give each kind of finding a confidence (high, medium or low) and its reason: a reproduced error or a data test is high; a rule that depends on intent is medium or low.

## Phase 2: review with the programme

```bash
$R topics        # topics from the type's topics.json (+ topics_extra) -> review.json, <p>-review.html
$R dictionary    # plain-language rendering per item -> <p>-dictionary.html
```

- `topics` puts every finding in exactly one topic and prints anything that landed in a catch-all. Read the topics as the meeting will: one cause each, plain words, honest confidence. Program-specific topics, splits and wording go in a `topics_extra` file (format in the `build_review.py` docstring).
- Optional: per-item plain-language definitions by sub-agents make the dictionary much more useful in the meeting (program indicators: `--desc-batches N`; brief in the type's `description_brief.md`).
- **Publish the meeting page and dictionary as artifacts.** Load `artifact-design` and `artifact-capabilities` first. The meeting page declares `db` with collection `decisions`, and `user`. Offline, the same file keeps decisions in the browser and exports them.
- After the meeting, read the stored decisions into `work_dir/decisions.json`: an `ArtifactData` list of collection `decisions`, or the page's export file. Then run `$R decisions`. Check the result with the user: a topic agreed "in principle, but check each" must be "Check each item", not "Agreed".

## Phase 3: propose fixes

```bash
$R fixes          # decisions + review.json + fixes file -> fixes.json (fixed group order, asserted rewrites)
$R descriptions   # changed items -> desc_in/batchN.json; one sub-agent per batch with the type's description_brief.md
$R payload        # -> proposed payload (+ aux file: mapping patch or data elements)
$R syntax-check   # server check of every proposed text
```

- The type's generic clear patterns run for agreed topics (`scripts/types/<type>/patterns.py`; table in `references/<type>/fix-patterns.md`). Patterns whose meaning depends on the item run only for items confirmed one by one, either in the meeting's "Check each item" marks or in the fixes file.
- Program-specific changes go in `fixes-explicit.json` (`fixes_file` in the config; schema in the `fix/build_fixes.py` docstring). It holds explicit find/replace entries with a justification, group switches, items to hold, and extra not-proposed entries. Give explicit groups a readable `title`, because that is what reviewers see.
- **Read the not-proposed list** with the user. Each entry needs its reason and the decision needed. Entries "expected to be resolved by the aux fixes" are confirmed in phase 4.
- Show reviewers every description `concern` (the corrected definition still doesn't match the name). Don't fix it silently.

## Phase 4: test end to end

Write `work_dir/scenarios.json` by hand. The format is in the type's `scen.py` docstring and `scenarios.example.json`; the rules are in `references/descriptions-and-scenarios.md`.

- One scenario per fix or interaction, in its own org unit.
- Program indicators: 2–4 clients, at least one that must count and one near miss.
- Aggregate indicators: data values per month that make the effect of the fix visible.
- Work out the expected values before *and* after from the indicator's meaning, never from the evaluator.

```bash
$R scenarios                                    # builds scenario data; refuses data that would prove nothing
for s in witness revert load before apply after; do $R e2e $s; done
$R check                                        # engine check before/after + scenarios -> results.json, E2E-TEST.md skeleton
$R viz && $R shots                              # optional (program indicators): pivot tables and screenshots
```

`apply` imports exactly the payload reviewers will approve (`atomicMode=ALL`), applies the aux file and reads every changed field back.

**Expect the test to change the fixes.** In the first program-indicator run it found four things no review would have:

- the latest-event lookup reading past the period end
- offsets ignored without a type
- a `null` mapping entry
- blank numbers compared as 0

Loop back to phase 3, re-run, and complete the TODOs in `E2E-TEST.md`.

## Phase 5: approve and export

```bash
$R approval      # -> fix-review.json, <p>-fix-review.html (artifact: db approvals/descriptions, user, downloads)
$R standalone    # -> <p>-fix-review-standalone.html (offline; decisions files merged by a coordinator)
$R guide         # optional: reviewer guide PDF
$R export [--dump DIR] [--decisions dec-*.json]   # import files without the page (both sources merge)
```

An item is exported only when **every** fix on it is approved; the page shows "partly approved" and offers "Approve all N fixes" per item. Exports are split into four files:

- working items
- approved items that still fail because of another open topic
- the aux file
- a summary CSV

Publish the approval page as an artifact for reviewers with accounts, and send the standalone file to the others. When both are used, build the import files from both in one call: dump the artifact's store and run `$R export --dump DIR --decisions dec-*.json`, so the newest decision per item wins and disagreements are listed. Settle each disagreement before handing over: a later click overrides an earlier rejection.

## Phase 6: import (hand over)

The import is the DHIS2 administrator's call; give them the files and these steps:

1. Re-export the affected objects from production and diff them against the `before` objects in `fixes.json`. The payload carries **complete objects** from the export date, so anything edited since would be overwritten. If anything changed, regenerate.
2. Import into a copy first; then import into production:
   - the payload: `POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL`
   - the aux file: the import call shown on the approval page. For program indicators that is `PATCH /api/programs/<uid>` with `application/json-patch+json`; for aggregate indicators it is the data elements via `/api/metadata`.
   - then run analytics.
3. Tell downstream consumers which numbers change (listed per group on the approval page). The not-proposed list and the "check each" checklists are the next round's input.

## Gotchas met in real runs

- **Data won't land on a test copy without repairs.** For program indicators, importing a program object resets its org-unit assignment; `import-data` re-assigns the test org units. For aggregate indicators, `import-data` also has to:
  - open closed periods
  - set zeroIsSignificant where zeros matter
  - drop comment option sets (they make value imports fail with HTTP 500 on 2.43.2)
  - configure `dataOutputPeriodTypes`
  - assign data sets

  Run `import-data` after any metadata import.
- **Regenerated test data needs new UIDs.** Tracker DELETE is a soft delete, so re-importing a UID fails. `e2e witness` uses its own seed for this reason. To start over cleanly, re-create the instance.
- **Only analytics proves a definition runs.**
  - The metadata import doesn't validate PI filters.
  - The syntax endpoints pass some texts that fail as SQL.
  - An unquoted UID literal fails with "Invalid string token".
  - An old `#{dataSet.REPORTING_RATE}` operand makes an aggregate numerator read 0 everywhere without any error.
- **An aggregate category combo changed without rebuilding its option combos** leaves indicators reading option combos that a fresh 2.43 copy refuses to create (E1130). Treat these as a decision for the programme, not a fix.

## References

- `references/data-formats.md`: every file passed between phases, the config, the fixes file and the adapter interface
- `references/review-pages.md`: the four pages, with purpose, design decisions, capabilities and how to publish
- `references/descriptions-and-scenarios.md`: description brief summary and scenario rules
- `references/<type>/behaviours.md`: verified engine behaviours and the probe recipe
- `references/<type>/fix-patterns.md`: finding → fix, what is clear and what needs a decision
- `examples/sl-malaria/` (program indicators) and `examples/sl-aggregate/` (aggregate indicators): complete runs on the Sierra Leone demo (2.43.2), with their inputs and results

Related skills:

- `dhis2-indicators`: single indicator authoring, and the semantics behind every finding
- `dhis2-metadata`: export, anonymise, import
- `dhis2-instances`: test instances
- `dhis2-integrity`: data-integrity remediation, with the same "deterministic vs ask" decision model
- `artifact-design` / `artifact-capabilities`: the pages
