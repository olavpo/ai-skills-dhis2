# Worked example: Sierra Leone demo, malaria program (DHIS2 2.43.2)

A full run of the method on the public Sierra Leone demo database: program "Malaria case diagnosis, treatment and
investigation" (`qDkgAbB5Jlk`, 71 program indicators). The demo has no program disaggregation, so
`inject_disagg.py` adds an age and a sex category mapping with two deliberate defects (an age band gap and an
option name used instead of its code) and one ENROLLMENT PI, to exercise the mapping checks and fixes.

| File | What it is |
|---|---|
| `pireview.json` | run configuration (`export/` and `out/` are created by the run) |
| `inject_disagg.py` | test fixture: adds the defective disaggregation to the export |
| `decisions.json` | the (simulated) meeting decisions, in the document shape the review page exports |
| `fixes-explicit.json` | one explicit fix the meeting asked for (quote a stage UID) |
| `scenarios.json` | the hand-written intent scenarios, with expected values before and after |

## How it was run

```bash
export DHIS2_URL=http://<empty 2.43 test instance> DHIS2_USER=… DHIS2_PASSWORD=…   # never production
# export the program from a Sierra Leone 2.43 demo instance
curl -u … "$SL/api/programs/qDkgAbB5Jlk/metadata.json?download=true" -o export/metadata.json
python3 inject_disagg.py                                   # -> export/metadata-disagg.json
# import it into the test instance with the dhis2-metadata skill (--unshare --delocalize, 2 passes), then
S=<skill>/scripts
python3 $S/types/program-indicators/lint.py && python3 $S/types/program-indicators/syntax.py && python3 $S/types/program-indicators/gen.py
python3 $S/types/program-indicators/import_data.py && python3 $S/types/program-indicators/query.py && python3 $S/types/program-indicators/compare.py && python3 $S/validate/assemble.py
python3 $S/review/build_review.py && python3 $S/types/program-indicators/build_dictionary.py && python3 $S/types/program-indicators/assemble_dict.py
cp decisions.json fixes-explicit.json scenarios.json out/
python3 $S/review/write_decisions.py --decisions out/decisions.json
python3 $S/fix/build_fixes.py --decisions out/decisions.json && python3 $S/fix/prep_desc.py
#   … description sub-agent with scripts/types/program-indicators/description_brief.md → out/desc_out/batch0.json
python3 $S/fix/build_payload.py && python3 $S/fix/syntax_check.py
python3 $S/types/program-indicators/scen.py
for s in witness load revert before apply after; do python3 $S/e2e/run_e2e.py $s; done
python3 $S/e2e/check.py
python3 $S/approve/build_fix_review.py && python3 $S/approve/make_standalone.py
```

## What came out

- **Validation:** 72 PIs: 10 FAILS (9 count expressions with AVERAGE, which on 2.43.2 fail with SQL 42883 when the
  expression is `V{enrollment_count}`, plus a stage UID compared without quotes), 4 WRONG_VALUE (the PIs using the
  defective mappings), 58 OK. The engine check matched analytics on all 64 runnable totals and 4 of 4 breakdowns,
  including the drop rules: the 3 EVENT PIs lost the age-4 and "Female" records from their totals; the ENROLLMENT
  PI kept them in its total but not in its cells.
- **Review:** 9 topics; decisions: 5 agreed, 2 check each item, 1 parked, 1 needs domain input.
- **Fixes:** 9 PIs (AVERAGE → COUNT; quote the UID) and 2 mappings (`<= 3` → `< 5`; `'Female'` → `'FEMALE'`);
  4 not-proposed entries, two of them "expected to be resolved by the mapping fixes".
- **End to end:** import 0 errors, read-back 0 differences; analytics errors among the 13 affected PIs 8 → 0;
  13/13 totals and 4/4 breakdowns equal to the evaluator; 7/7 scenario checks. The quoted-UID PI now runs but stays
  0, because the EVENT PI reads a field of another stage (behaviour C6) — correctly left as a decision.
