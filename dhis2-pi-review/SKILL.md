---
name: dhis2-pi-review
description: "Validate, review with the programme team, fix, test end to end and get approval for the program indicators (and their program disaggregation / category mappings) of a DHIS2 tracker or event program, as one engagement. Use when someone wants to audit or clean up many program indicators at once: 'validate all the PIs in this program', 'which of our indicators are broken', 'do these indicators compute what their names say', 'prepare an indicator review meeting', 'make a page where the team can decide/approve the fixes', 'fix the indicators we agreed on and give me an import file', or when a metadata export with dozens to thousands of program indicators needs a quality pass before a reporting cycle or migration. Bundles the scripts (lint, synthetic data generator, independent evaluator, fix generator, end-to-end test, meeting and approval pages). For writing or debugging a single indicator use dhis2-indicators; for data-integrity check remediation use dhis2-integrity."
---

# DHIS2 program indicator review

Takes a large set of program indicators (PIs) from "nobody knows if these are right" to "every change decided by the programme team, tested, approved and importable". Five phases plus the import, each with an output the next one needs and a point where people decide:

| # | Phase | Proves / decides | Main outputs (in `work_dir`) |
|---|---|---|---|
| 1 | Validate | what is wrong, how sure we are | `pi-status.csv`, `pi-findings.csv`, `PI-VALIDATION.md` |
| 2 | Review | which topics to fix, and how (meeting) | `pi-review.html`, `pi-dictionary.html`, `PI-REVIEW-DECISIONS.md` |
| 3 | Fix | exactly what changes (only clear fixes) | `fixes.json`, `proposed-programIndicators.json`, `proposed-categoryMappings.patch.json` |
| 4 | Test end to end | the payload imports and counts the right thing | `results.json`, `E2E-TEST.md` |
| 5 | Approve | which changes are accepted | `pi-fix-review.html`, standalone page, import files |
| 6 | Import | (done by a DHIS2 administrator) | |

The knowledge underneath (expression semantics, boundaries, program disaggregation) is in the `dhis2-indicators` skill; read its `references/program-indicators.md` when a finding needs explaining. This skill's `references/dhis2-behaviours.md` lists the engine behaviours the scripts encode, with IDs (C1, E7, P1…) used across the references.

## Principles

These are what made the method work; each prevents a specific failure seen in practice.

- **Two independent checks.** The *engine check* compares analytics with an independent evaluator run on the raw test data (`scripts/lib/piexpr.py`); the *intent check* uses lint rules and hand-written scenarios with expected values from the indicator's meaning. When the engine check agrees everywhere, the problems are in the configuration; most real bugs live in the gap between the two.
- **A disposable test instance of the production version, never production.** The data and fix steps write test data, probe PIs and metadata. Scripts refuse to write unless the config says `"test_instance": true`.
- **Verify engine behaviour, don't assume it.** Before a surprising behaviour explains a finding or shapes a fix, prove it with probe PIs (`scripts/validate/probe.py`). Re-verify `dhis2-behaviours.md` on a different DHIS2 version.
- **Group by cause, decide by group, act per item.** A meeting can decide 20–30 topics, not 2,000 indicators. Items that need their own decision are split out ("Check each item"), and that verdict is never read as group approval.
- **Only propose what is clear.** A fix is generated only when the finding, the metadata and the decision leave exactly one correct change (`references/fix-patterns.md`). Everything else goes on the *not proposed* list with the decision needed. When unsure, ask the user rather than guess.
- **Nothing changes silently.** Every rewrite asserts its pattern matched exactly once; every import is read back and diffed; every number in a write-up comes from a script that can be re-run.
- **The programme team owns the decisions.** You propose; they decide, with name and time recorded; only approved items are exported.

## Before you start: ask

Use one `AskUserQuestion` round for what you can't find out yourself:

- **Which program and which export.** A dependency export of the program (`GET /api/programs/<uid>/metadata.json`), plus the data sets the PIs report into if the dictionary should show the aggregate link. Exports from production must be read-only and should be anonymised for sharing (`dhis2-metadata`).
- **The production DHIS2 version** — the test instance must match it.
- **Who decides and how they will see the pages.** Programme team in a meeting (meeting page as an artifact, shared decisions) and reviewers for the approval (artifact for people with accounts, standalone file otherwise).
- **Other consumers of these definitions** (report pipelines, apps that compute the same numbers). They are evidence of intent for ambiguous definitions and need impact notes on the approval page (`notes` in the config).

## Set up a run

1. Get an empty disposable instance of the production version (`dhis2-instances` skill if available; otherwise ask the user). "Reset to empty" means delete and re-create.
2. Write `pireview.json` next to the export (copy `scripts/pireview.example.json`; every key is documented in `scripts/lib/config.py`). Credentials only in the environment: `DHIS2_URL` plus `DHIS2_TOKEN`, or `DHIS2_USER` and `DHIS2_PASSWORD`. Export `PIREVIEW_CONFIG=<path>` or pass `--config` to every script.
3. Import the program's metadata into the test instance with the `dhis2-metadata` skill (`transform_metadata.py --unshare --delocalize`, then `import_metadata.py --passes 2`). Org-unit references fail (E5002) — expected; the scripts create their own test org units. Program stages without a program in the export are ignored (E4053). Run `POST /api/maintenance?categoryOptionComboUpdate=true` if the program has disaggregation.

All scripts read from and write to `work_dir`; `S=<skill>/scripts` below. Every script has a docstring with its inputs, outputs and options.

## Phase 1: validate

```bash
python3 $S/validate/lint.py          # ~60 checks against the metadata -> findings.json (check ids in the docstring)
python3 $S/validate/syntax.py        # server check of every expression/filter/mapping filter -> syntax.json
python3 $S/validate/gen.py           # synthetic data: one witness per PI, noise, out-of-period controls -> tracker/truth/unsat.json
python3 $S/validate/import_data.py   # test org units, program assignment, tracker import, analytics
python3 $S/validate/query.py         # total + breakdown per PI -> analytics.json
python3 $S/validate/compare.py       # engine check -> compare.json
python3 $S/validate/assemble.py      # -> pi-status.csv, pi-findings.csv, summary.json, PI-VALIDATION.md skeleton
```

Then do the parts a script can't:

- **Read by hand** every PI in `unsat.json` (the generator couldn't satisfy the filter: often impossible, or syntax the server rejects) and every PI that stays at zero despite its witness. Add what you find as `manual-findings.json` (format in `references/data-formats.md`) and re-run `assemble.py`.
- **Explain every engine mismatch.** Either a DHIS2 behaviour the evaluator doesn't know (probe it, then add it to `piexpr.py` and `dhis2-behaviours.md`) or a real engine problem. Don't leave one unexplained.
- **Write the narrative** into the TODOs of `PI-VALIDATION.md`, with a confidence (high/medium/low) and its reason for each kind of finding: a reproduced error or a data test is high; a rule that depends on intent is medium or low.

## Phase 2: review with the programme

```bash
python3 $S/review/build_review.py      # topics from scripts/review/topics.json -> review.json, pi-review.html
python3 $S/review/build_dictionary.py  # plain-language rendering per PI, aggregate link by code -> dictionary.json
python3 $S/review/assemble_dict.py     # -> pi-dictionary.html
```

- `build_review.py` puts every finding in exactly one topic and prints anything that landed in a catch-all. Read the topics as the meeting will: one cause each, plain words, honest confidence. Program-specific topics, splits and wording go in a `topics_extra` file (format in the `build_review.py` docstring), not in `topics.json`.
- Optional: per-PI plain-language definitions by sub-agents (`build_dictionary.py --desc-batches N`, brief in `scripts/fix/description_brief.md`) make the dictionary much more useful in the meeting.
- **Publish the meeting page and dictionary as artifacts** (load `artifact-design` and `artifact-capabilities` first; the meeting page declares `db` with collection `decisions` and `user`). Offline, the same file keeps decisions in the browser and exports them.
- After the meeting, read the stored decisions (`ArtifactData` list of collection `decisions`, or the page's export file) into `work_dir/decisions.json`, then `python3 $S/review/write_decisions.py`. Check the result with the user: a topic agreed "in principle, but check each" must be "Check each item", not "Agreed".

## Phase 3: propose fixes

```bash
python3 $S/fix/build_fixes.py     # decisions + review.json + fixes file -> fixes.json (fixed group order, asserted rewrites)
python3 $S/fix/prep_desc.py       # changed PIs -> desc_in/batchN.json for description sub-agents
#   one sub-agent per batch with scripts/fix/description_brief.md ({INPUT}, {OUTPUT}) -> desc_out/batchN.json
python3 $S/fix/build_payload.py   # -> proposed-programIndicators.json, proposed-categoryMappings.patch.json
python3 $S/fix/syntax_check.py    # server check of every proposed text
```

- Generic clear patterns run for agreed topics (`scripts/fix/patterns.py`; table in `references/fix-patterns.md`). Patterns whose meaning depends on the item (contradictions, "status at period end" windows, blank-as-0 guards, prefix groups) run only for items confirmed per item, in the meeting's "Check each item" marks or in the fixes file.
- Program-specific changes go in `fixes-explicit.json` (`fixes_file` in the config; schema in the `build_fixes.py` docstring): explicit find/replace with a justification, group switches, items to hold, extra not-proposed entries. Give explicit groups a readable `title` — it is what reviewers see.
- **Read the not-proposed list** with the user. Each entry needs its reason and the decision needed; entries "expected to be resolved by the mapping fixes" are confirmed in phase 4.
- Surface every description `concern` (corrected definition still doesn't match the name) to reviewers; don't fix it silently.

## Phase 4: test end to end

Write `work_dir/scenarios.json` by hand (format and rules: `scripts/e2e/scen.py` docstring, `scripts/e2e/scenarios.example.json`, `references/descriptions-and-scenarios.md`). One scenario per fix or interaction, 2–4 clients in its own org unit, at least one that must count and one near miss, expected values before *and* after worked out from the indicator's meaning — never from the evaluator.

```bash
python3 $S/e2e/scen.py                        # builds scenario data; refuses clients that would match 0 or 2 options of a category
for s in witness load revert before apply after; do python3 $S/e2e/run_e2e.py $s; done
python3 $S/e2e/check.py                       # engine check before/after + scenarios -> results.json, E2E-TEST.md skeleton
python3 $S/e2e/make_viz.py && python3 $S/e2e/shots.py   # optional: pivot tables and screenshots for the evidence
```

`apply` imports exactly the payload reviewers will approve (`atomicMode=ALL`), patches the mappings and reads every changed field back. **Expect the test to change the fixes**: in the first application of this method it found four things no review would have (latest-event lookup after the period, offsets ignored without a type, a `null` mapping entry, blank numbers compared as 0). Loop back to phase 3, re-run, and complete the TODOs in `E2E-TEST.md`.

## Phase 5: approve and export

```bash
python3 $S/approve/build_fix_review.py   # -> fix-review.json, pi-fix-review.html (artifact: db approvals/descriptions, user, downloads)
python3 $S/approve/make_standalone.py    # -> pi-fix-review-standalone.html (offline; decisions files merged by a coordinator)
python3 $S/approve/manual_shots.py       # optional: reviewer guide PDF
python3 $S/approve/export_approved.py --dump DIR | --decisions dec-*.json   # import files without the page
```

An item is exported only when **every** fix on it is approved; the page shows "partly approved" and offers "Approve all N fixes" per item. Exports are split: working PIs, approved PIs that still fail because of another open topic, the mapping patch, and a summary CSV. Publish the approval page as an artifact for reviewers with accounts; send the standalone file to the others.

## Phase 6: import (hand over)

The import is the DHIS2 administrator's call; give them the files and these steps:

1. Re-export the affected objects from production and diff them against the `before` objects in `fixes.json`: the payload carries **complete objects** from the export date, so anything edited since would be overwritten. Regenerate if anything changed.
2. Import into a copy first; then production: `POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL`, and `PATCH /api/programs/<uid>` with the mapping patch (`Content-Type: application/json-patch+json`). Run analytics.
3. Tell downstream consumers which numbers change (listed per group on the approval page). The not-proposed list and the "check each" checklists are the next round's input.

## Gotchas met in real runs

- Importing a program object resets its org-unit assignment; `import_data.py` re-assigns the test org units, so run it after any program import.
- Regenerated test data needs new UIDs (tracker DELETE is a soft delete; re-importing a UID fails). `run_e2e.py witness` uses its own seed for this reason. To start over cleanly, re-create the instance.
- The metadata import doesn't validate PI filters; the syntax endpoint passes some filters that fail as SQL; an unquoted UID literal (`V{program_stage_id} == abcdefghijk`) fails in analytics with "Invalid string token". Only analytics proves a definition runs.
- PIs without program disaggregation never drop records (C1/C2 apply only with `categoryMappingIds`), but every mapping-based check is skipped for them — that's expected, not a gap.

## References

- `references/dhis2-behaviours.md` — verified engine behaviours (IDs used everywhere) and the probe recipe
- `references/fix-patterns.md` — finding → fix, what is clear and what needs a decision
- `references/data-formats.md` — every file passed between phases, including the config and the fixes file
- `references/review-pages.md` — the four pages: purpose, design decisions, capabilities, how to publish
- `references/descriptions-and-scenarios.md` — description brief summary and scenario rules
- `examples/sl-malaria/` — a complete run on the Sierra Leone demo (2.43.2), with its inputs and results

Related skills: `dhis2-indicators` (single indicator authoring and the semantics behind every finding), `dhis2-metadata` (export, anonymise, import), `dhis2-instances` (test instances), `dhis2-integrity` (data-integrity remediation, same "deterministic vs ask" decision model), `artifact-design` / `artifact-capabilities` (the pages).
