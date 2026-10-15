# Worked example: Sierra Leone demo, aggregate indicators (DHIS2 2.43.2)

A full run of the method with `"type": "indicators"` on the public Sierra Leone demo database: 73 aggregate indicators (every indicator of the demo except the four built on program data, `I{…}`/`D{…}`), the 81 data elements they read and the 12 data sets holding them. The demo already has real defects (a category combo changed without rebuilding its option combos, an old reporting-rate operand, a double-counted term, yearly SUM data); `inject_defects.py` adds six more so that every clear fix pattern and the import safeguards are exercised.

| File | What it is |
|---|---|
| `pireview.json` | run configuration (`export/` and `out/` are created by the run) |
| `export_sl.py` | test fixture: export from a Sierra Leone 2.43 instance (data set dependency exports + indicators, types, constants, groups, legend sets) |
| `inject_defects.py` | test fixture: the injected defects D1–D6 (listed in its docstring) |
| `decisions.json` | the (simulated) meeting decisions, in the shape the review page exports |
| `scenarios.json` | the hand-written intent scenarios, with values expected before and after |

## How it was run

```bash
export DHIS2_URL=http://<empty 2.43 test instance> DHIS2_USER=… DHIS2_PASSWORD=…     # never production
SRC_URL=<SL demo> SRC_USER=… SRC_PASSWORD=… python3 export_sl.py   # -> export/metadata-raw.json
python3 inject_defects.py                                          # -> export/metadata.json
# import with the dhis2-metadata skill:
#   transform_metadata.py export/metadata.json --unshare --delocalize --split-dir export/split
#   import_metadata.py --src export/split --url $DHIS2_URL --auth … --passes 2
R="python3 <skill>/scripts/run.py"
$R lint && $R syntax && $R gen && $R import-data && $R query && $R compare && $R assemble
$R topics && $R dictionary
cp decisions.json scenarios.json out/
$R decisions --decisions out/decisions.json && $R fixes --decisions out/decisions.json
$R descriptions      # … description sub-agent with scripts/types/indicators/description_brief.md → out/desc_out/batch0.json
$R payload && $R syntax-check && $R scenarios
for s in witness revert load before apply after; do $R e2e $s; done
$R check && $R approval && $R standalone
```

The import reports these expected warnings: E5002 on category options (their org unit restrictions point at SL org units) and E1130 on the "Births" option combos (below). `import-data` repairs what the test copy needs and records every change in `out/import.json`: it replaced the 12 option combos the fresh instance had generated for "Births" by the exported ones, stripped the injected closed period (D6), set `zeroIsSignificant` on the 61 data elements that get a 0, removed the comment option set from 2 data elements (A11), set `dataOutputPeriodTypes` (A12), assigned the data sets to the facilities and registered 109 complete data sets (for `R{}`).

## What came out

- **Validation** (73 indicators): 1 FAILS, 4 WRONG_VALUE, 15 REVIEW, 53 OK.
  - FAILS: *FIC <1y - Adjusted for reporting* uses `#{dataSet.REPORTING_RATE}`; the server says "not well-formed" and analytics silently returns 0 everywhere (A20).
  - WRONG_VALUE: *ANC LLITN coverage* (D1 option combo of an old combo, D5 COUNT data element), *PHU delivery rate (by ANC1)* (D2 × 100 twice), *NVP during labour rate* (D3 Number type), *Dropout rate Penta 1 - Measles* (D4 rate inside the numerator: NaN where Penta 1 is 0).
  - REVIEW, found in the demo itself: 8 indicators read "Births" option combos (attendant × location) while the combo's categories are now attendant × sex — the data lives under the old combos, but a 2.43 copy can't recreate them (E1130) and new data entry produces the new ones; *ANC visits per clinical professional* adds "MCH Aide, on salary" twice; three facility-assessment indicators read yearly SUM data, so they have no monthly or quarterly value (A7); *Consumption vs population* divides monthly consumption by a yearly population without annualizing; *TrueOnly* reads a data element in no data set. Plus *ANC IPT 2 Coverage* and *IPT doses total*, which read the D5 AVERAGE data element.
- **Engine check**: 2,045 generated values (+123 zeros, 76 zero denominators, 492 deliberate blanks) in 3 facilities and the root; 73 of 73 indicators, **1,168 of 1,168 cells** (4 org units × 3 months + quarter) equal to the independent evaluator, unrounded. Switching single behaviours off shows they matter: without A1 (missing operands) 154 cells differ, without A7 (yearly SUM data) 12, without A20 the reporting-rate indicator can't be evaluated.
- **Review**: 12 topics; decisions: 8 agreed, 1 check each item, 2 parked, 1 needs domain input (the Births combo).
- **Fixes**: 5 indicators (`legacyrate`, `cocremap`, `drop100`, `percenttype`, `movedivisor`) and 2 data elements (`decount` COUNT → SUM, `deaverage` AVERAGE → SUM); 2 not-proposed entries (the double-counted term, the yearly data elements).
- **End to end**: import 0 errors, read-back 0 differences; 7 affected indicators (5 changed + 2 through a data element): 7/7 totals and 112/112 cells equal to the evaluator before and after; **6/6 scenario checks** (LLITN 2 % → 70 %, NVP 0.8 → 80, dropout 16.67 → 16.67, FIC adjusted 0 → 10.14, IPT coverage 13.33 → 40 and IPT doses 20 → 60). The dropout fix changes no value (analytics evaluates the expression on aggregated operands, A5); it replaces a NaN cell by "no value".
- *PHU delivery rate (by ANC1)* reads only the old Births option combos, so on the test copy it has no value before or after the `drop100` fix; the fix is verified by the lint rule and the engine check only.
