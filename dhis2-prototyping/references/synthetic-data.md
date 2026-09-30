# Indicators and verification

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

- **Load the same synthetic population into every option.** Then every option's indicators must
  equal the same ground truth, computed in Python from the population. The project code writes the
  truth as a `truth.json` (per option: indicator UID → expected value, plus expected category-option
  cells for disaggregations), and `scripts/verify_indicators.py truth.json` (bundled) queries analytics
  and prints every mismatch; exit code 1 on any. In the PoC it checked 397 values plus the sex × age
  disaggregation, caught verification-script bugs and metadata bugs, and later confirmed nothing broke.
- Build the "not supported" list **in the generator** (`UNSUPPORTED[opt][key] = reason`), not by
  hand, and pass it through `truth.json` (`"unsupported"`): the verifier copies it into its output,
  and the report's indicator matrix and comparison column come straight from that.
- Once scenario entries exist, restrict verification to the synthetic window (`startDate`/`endDate`
  instead of `pe=2026`: `"window"` in `truth.json`) and handle event-dated indicators separately —
  event indicators count by event date, enrollment indicators by enrollment date, and indicators
  using `V{current_date}` by the day analytics ran.
- Run analytics once early (it takes seconds on an empty tracker-only instance) to find analytics
  bugs while there is still time to work around them.

## Synthetic data

The project package generates the population (`scripts/<project>/population.py`, below) and writes
it as tracker payloads; `scripts/load_tracker.py` (bundled) loads them:

- Deterministic (`random.Random(seed)`), plausible distributions (the outcome more likely in some
  age/sex groups and household types; reasons correlated with sex, age and distance).
- Compute every rule-assigned value in the loader (age, derived status flags), because
  the server's rule engine will reject inconsistent imports.
- **Include the seed in the UID key** (`data{SEED}:…`): deleted tracked entities are soft-deleted,
  and their UIDs can't be reused, so a test load with the same keys blocks the real load.
- Batches of 40–100 tracked entities per `POST /api/tracker?async=false` (`atomicMode=OBJECT`, so one
  bad record does not drop its batch); relationships after all tracked entities. 20 000 households
  (221 595 objects) loaded in 5½ minutes.
- Ownership transfers: `PUT /api/tracker/ownership/transfer?trackedEntity=&program=&orgUnit=`
  (`"transfers"` in the loader's input).
- Enrollment coordinates: rejected with E1074 through `/api/tracker` on 2.42.6, so the loader writes
  them as SQL (`--geometry-sql geo.sql`) to run with `psql` against the instance database.
- Generated attribute values: `load_tracker.reserve_values()` wraps `generateAndReserve`; keep the
  synthetic range apart from real entry (new sequences start at 00001 per pattern value).
- Keep synthetic households out of the way of manual entry (separate form-number range, surnames
  not used in the scenario).

### The project package (a worked pattern, not bundled)

A project package, e.g. `scripts/<project>/`, holds everything specific to one PoC, built on
`d2gen.py`: `base.py` (org units, option sets, tracked entity types, shared attributes and data
elements, users), one module per design option (`options.py` or `option_<x>.py`: programs, stages,
rules), `indicators.py` (indicators and program indicators per option, plus `UNSUPPORTED`),
`dashboards.py`, and `population.py` (`generate(n, seed)` returning plain dicts: households, members,
visits, dates, coordinates). A thin `build.py` writes `metadata/*.json` from those modules; one
function per option turns the same population into that option's tracker payloads, and another
computes `truth.json` from it. Every script refers to objects as `g.uid("de:<CODE>")`, so nothing
needs a lookup.

## Scenario data for recorded flows

- **Use different names and phone numbers per platform** for the same scenario household
  (`scenario.for_web()`), otherwise the second platform's search finds the first platform's entry
  and the flow is no longer a first visit.
- **`scripts/cleanup_scenario.py`** (bundled) deletes every scenario entry (by phone and surname) in
  every program: the scenario module writes a match file (program → attribute → values for both
  platforms, plus names used while developing), and the script lists matches until given `--delete`.
  Run it before each final pass, never between flows of the same pass.
- **Read every entry back** with `scripts/check_entry.py --program <uid> --attr <uid> --value <v>`
  (bundled): it prints the tracked entity, enrollments, events and relationships with codes.
- **Sync only after a successful Android flow.** A failed flow leaves a partial household on the
  device; the next sync uploads it. Delete partial entries on the server, then `pm clear` + login
  before re-running.
- Today's date is the only allowed date for most visits: caption the "three months later" visit
  instead of trying to backdate.
