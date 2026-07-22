# Review pack — http://dhis2-agent-hmis-42:8080 (DHIS2 2.42.5.1)

Evidence worksheets for the docs' MANUAL metadata-review items. **Candidates, not defects —
and worksheets, not fixes.** Fill the `decision` column with the owner; decided fixes then
flow through decisions.md into the fix manifest. See references/manual-review.md.

- **duplicate_data_sources.csv** (200 rows — **CAPPED at 200 of 704**): Candidate duplicate data elements (name similarity ≥0.8, blocked by rarest token). Similarity ≠ duplicate — judge semantics; resolution often = form redesign with stakeholders.
- **category_totals.csv** (60 rows): Categories whose disaggregation may not sum to a meaningful total (overlapping ranges, total-like options, fully-shared option sets, single-option).
- **dashboard_items.csv** (200 rows — **CAPPED at 200 of 221**): Dashboard items using fixed periods/org units (stale or wrong for other users) and dashboards nobody else can see.
- **orgunit_assignment.csv** (26 rows): Dataset/program org-unit assignment anomalies: none, admin-level, or ~all org units. Country policy decides what is correct.
- **sharing.csv** (1 rows): Dataset/program sharing anomalies: public read-write, or no access configured.
- **indicator_formula_candidates.csv** (151 rows): FLAG-ONLY handoff: candidates for semantic indicator-formula review. Do the actual review with the dhis2-indicators skill (authoring/validation is its domain).
