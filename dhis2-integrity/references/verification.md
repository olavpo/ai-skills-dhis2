# Verification — proving you didn't break anything

**Re-running the integrity checks proves the DB is internally *consistent*, not that *outputs are
unchanged*.** A category-combo/COC change, an expression fix, or a deleted object can leave every check
green while silently changing an indicator's number, blanking a custom-form field, or altering a
program rule's behaviour. Catch that with **A/B differential testing** against a pre-change baseline.

## The acceptance criterion
> **Every output difference between control and fixed must be explainable by a specific logged change.**

It is *not* "outputs must be identical" — some fixes deliberately turn **broken → correct** (repairing
an invalid indicator expression; re-validating data that sat under disjoint COCs). For those, also
confirm the *new* number is demonstrably right, not merely non-null.

## Prerequisite: a true control
The control must be **the instance exactly as it was before the cleanup** — a restored pre-change
`pg_dump` (or, in dump mode, the original metadata dump on a clean clone). A *clean* standard demo is
**not** a valid control: it won't contain the injected problems you fixed, so every fix shows as a diff.
No snapshot → no true A/B; use the delta fallback below.

## Risk-target the work — classify your changes first
Most edits **cannot** move a number; don't spend verification effort on them.
- **Zero output risk** (skip / spot-check): renames, codes, whitespace, label-only changes, group
  membership, sharing, priorities, trigram config, compulsory flags, geometry removal, deleting
  *unreferenced* objects.
- **Can change outputs** (focus here): category-combo/COC structural changes; data-value moves (COC
  remaps); dataset DE-membership / period-type changes; expression fixes (indicator num/den, program
  indicators, validation rules, predictors); deletions that risk dangling references.

**The rule is proportional, not blanket:** the full layered diff below (including double analytics
regeneration — expensive on large instances) is mandatory exactly when the fix mix contains
"can change outputs" items. An engagement of purely structural fixes needs only the integrity re-run
plus targeted spot checks of the touched objects — say so in the deliverable rather than performing
verification theater.

## What to diff — layered, strongest first
1. **Raw data values** (`datavalue` / `/api/dataValueSets`): the set of `(dataElement, period, orgUnit,
   categoryOptionCombo, attributeOptionCombo, value)` must be identical except deliberate COC remaps.
   Pure DB diff — the bedrock against accidental data loss/movement.
2. **Aggregate analytics** (`/api/analytics`): **regenerate analytics on BOTH** instances with identical
   params, then diff every indicator + the data elements feeding changed structures, at national + a few
   districts over e.g. `LAST_5_YEARS`. Compare **totals AND disaggregations**.
3. **Data-entry form resolvability:** for every dataset form (section + custom HTML) extract all
   `deUID-cocUID(-val)` input references and assert each COC still exists and is valid for that DE's
   combo — the set unchanged except intended additions. A deleted/changed COC is exactly how a custom
   form silently loses a field.
4. **Tracker:** diff event/enrollment counts per program/stage (identical if you touched no event data);
   diff program-indicator values for affected programs; run the program-rule engine on sample
   enrollments and confirm no rule references a deleted object and behaviour is unchanged.
5. **Dangling references:** scan favorites' `dataDimensionItems`, indicator numerator/denominator,
   predictor & validation expressions, min-max, and dataset/section membership for any deleted UID.
6. **User-facing artefacts:** for each dashboard/visualisation that was touched or references changed
   objects, fetch the data it actually renders (`GET /api/visualizations/<uid>/data.json`, or replay its
   analytics query) and confirm it still returns rows. A favorite can pass the dangling-reference scan
   yet render empty — verify the artefact the user looks at, not only the objects you changed.
7. **Dependency closure of every kept dataset** (after deletions): `GET /api/dataSets/<uid>/metadata.json`
   exports the dataset's full dependency closure, so a 200 for all N kept datasets proves nothing a
   dataset needs was deleted. This is a much stronger signal than row counts, and needs neither a control
   instance nor a DB route.

`scripts/verify_outputs.py` automates layers 1, 2, 3 and 5 between two instances.

## Diff-harness gotchas
- Regenerate analytics fresh on both with identical params (`skipRounding=true`, same date) — stale
  analytics tables produce false diffs.
- **Diff by UID, not name** — renames would otherwise swamp the signal.
- Exclude `lastUpdated`/`created`/audit fields.
- Pre-load the **whitelist of intended diffs** from the change log (broken→correct corrections, COC
  remaps, deleted-dataset completeness) so the harness flags only the *unexplained* ones.
- COC remaps to identical disaggregation labels show **same totals, same per-band values, new UIDs** —
  assert on values, tolerate UID changes.

## Fallback when there's no control snapshot — delta-verification (run against the live fixed DB)
Verify each logged change produced exactly its intended effect and nothing else:
- **Conservation check:** for every combo whose COCs you touched, the **sum across COCs** for each
  `(DE, period, orgUnit)` is unchanged before vs after (use backups), and grand totals are preserved.
- Every changed expression **validates** (`/api/expressions/description`) and returns sane numbers.
- Every dataset form field **resolves** to an existing, valid COC.
- **No dangling references** to any deleted UID.
- Re-run **all** integrity checks (catches structural regressions).
Cheap and immediate, but weaker against *unknown* side effects than a true A/B — hence "snapshot first".

### A global `datavalue` count is NOT a conservation test on a live-ish instance
Background processes mutate data independently of your cleanup: on the HMIS 2.42 case ~16k datavalues were
created by a `system-process` (predictor/scheduled job) between the control snapshot and the final check,
and an analytics-table generation ran (bloating the fixed `pg_dump` 167MB→823MB — analytics tables, not
data). So a raw `SELECT count(*) FROM datavalue` before/after can rise or fall for reasons unrelated to
your fixes. **Verify conservation PER-MIGRATION instead** (source COCs empty, 0 orphan COCs, summed totals
match, deliberately-deleted counts match) — and check `storedby`/`created` to attribute any global delta
before attributing it to the cleanup.

### Concrete delta-SQL recipe (worked; use when the API inventory is unreliable or a control isn't restored)
On large instances the API inventory under-reports (slow checks time out → empty), so **verify fixes with
direct SQL** — authoritative and fast:
- **Datavalue conservation:** `SELECT count(*) FROM datavalue` must equal `baseline − (values you
  deliberately deleted)`; and rows you migrated must be present under the survivor COC. (Real check: the
  post-fix count equalled the baseline minus exactly the deliberately-removed values.)
- **No orphan COCs:** `SELECT count(*) FROM categoryoptioncombo coc LEFT JOIN categorycombos_optioncombos
  m ON coc.categoryoptioncomboid=m.categoryoptioncomboid WHERE m.categorycomboid IS NULL AND coc.name<>'default'` = 0.
- **No self-inflicted dangling expression refs:** scan every indicator operand `#{de.coc}`; flag any COC
  absent from the live instance **where the DE is still live** (= a ref you broke). If the DE is *also*
  absent from the source dump, it's pre-existing corruption, not your fault (see next).
- **Exclusive-group fix:** the "OU in ≥2 groups of one set" query returns 0 (group by
  `(organisationunitid, orgunitgroupsetid)` HAVING count(distinct group) > 1).
- **Duplicate-combo merge:** group `categorycombo` by sorted `categorycombos_categories.categoryid`;
  HAVING count > 1 returns 0.
- **A "new" post-fix check is not automatically self-inflicted.** After a fix + `categoryOptionComboUpdate`,
  `indicators_with_invalid_denominator=3` became `invalid_numerator=5` — looked like a regression, but all
  5 referenced DEs/COCs/indicators **never present in the source dump** (grep the dump for the UIDs). The
  check just re-evaluated. Confirm the missing object isn't one you deleted, then flag as pre-existing.
