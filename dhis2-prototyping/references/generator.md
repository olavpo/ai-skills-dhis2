# Generating the metadata

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

### Write a generator, never hand-edit JSON

A project package, e.g. `scripts/<project>/` with `base`, per-option, `indicators` and `population`
modules built on `scripts/d2gen.py` (bundled), builds everything in Python and writes
`metadata/*.json` (the package layout is described in `synthetic-data.md`):

- **Deterministic UIDs** from a key (`uid("de:C_STATUS")`) so re-imports update in place and scripts
  can refer to any object without lookups.
- A small `Program`/`Stage` builder that adds program rule variables automatically for every data
  element and attribute it places (named after the code, `useCodeForOptionSet: true`), so rule
  expressions read like `#{C_STATUS} == '3'`.
- Shared base (org units, option sets, TETs, attributes, data elements, users) plus one module per
  design option. Adding an option took minutes.

Import with `POST /api/metadata?importStrategy=CREATE_AND_UPDATE&atomicMode=ALL&identifier=UID`
and print the error reports on failure: `scripts/import_package.py metadata/*.json` (bundled) does
this file by file, stops at the first failure, and retries a file without its rules when it hits
E4047 (below).

**Metadata import never deletes.** When the generator stops producing an object (a rule, a working
list type), delete it through the API yourself, or the old object keeps acting (it did: an old
hide rule broke enrolment in a registry program).

### Validate expressions against the server, in bulk

| What | Endpoint (POST, body = expression, `Content-Type: text/plain`) |
|---|---|
| Rule condition | `/api/programRules/condition/description?programId=<uid>` |
| Rule action data | `/api/programRuleActions/data/expression/description?programId=<uid>` |
| PI expression / filter | `/api/programIndicators/expression/description`, `/filter/description` |
| Indicator formula | `/api/indicators/expression/description` |

`scripts/validate_package.py` (bundled) runs every expression in the package; run it after each
import.

### Naming

- Put the prefix in **codes** (`DEMO_…`), not in names: names and form names show up in Capture lists,
  headers and videos ("DEMO Household form number"). Category option names show in charts too
  ("Male (DEMO)") — keep them clean as well.
- Data element and attribute names still need to be unique on a real server; plan that with the
  owner's naming scheme.

### Things the platform does that you will not guess (2.42.6, Capture 3.4.2)

| Topic | Behaviour | What to do |
|---|---|---|
| Text pattern | `ORG_UNIT_CODE(...)` = first **three** characters | One dot per character: `ORG_UNIT_CODE(..........)` |
| Generated values | New sequences start at `00001` per pattern value | Put synthetic data in a separate range (`-9xxxx`) |
| MULTI_TEXT in indicators | PIs have `contains()` and `containsItems()` since 2.41, **without** the `d2:` prefix (validating `d2:contains` fails, which misled the first build) | `containsItems(#{stage.de}, 'R07')` for an exact item match (below) |
| Hidden + assigned | Web drops a hidden field's value; the server's rule engine then rejects the save (E1309) | Never hide a field that a rule assigns; show it read-only |
| PI disaggregation | Works in 2.42 (`categoryCombo` + `categoryMappingIds`, mappings on the program) | Use it; it replaced ~60 PIs per option. Querying a PI by a category it lacks gives a raw SQL error (E7145). |
| PI changes | Analytics keeps the old definition | `POST /api/maintenance/cacheClear` after changing PIs |
| `orgUnitField: OWNER_AT_END` | Ownership analytics table stayed empty despite transfers | Use an org-unit data element as the PI's org unit field |
| Rule-assigned attribute left out of a tracker import | E1019 "Only ProgramAttributes are allowed for Enrollment" (whole enrollment dropped), although it is a program attribute; a different value gives E1309 | Every API writer sends the value the rule would assign |
| New stage + its HIDEFIELD/SETMANDATORYFIELD rules in one metadata update | E4047 "DataElement … is not linked to any ProgramStageDataElement": the check uses the stored stage fields, not the payload's | Import once without `programRules`/`programRuleActions`, then in full (`import_package.py` does this) |
| Enrollment geometry via tracker import | Rejected with E1074 even with featureType POINT | Import without it; set `enrollment.geometry` by SQL |
| Server rules on import | `/api/tracker` runs program rules; mandatory/error actions reject imports | Useful check; make synthetic data satisfy every rule, compute assigned values yourself |
| `V{current_date}` in PIs | Allowed; evaluated at query time | Good for "overdue as of today"; verification must use the real date |
| Visualizations via `/api/metadata` | `columns`/`rows`/`filters` are ignored on this path, so relative periods given there as items (`{"id":"THIS_YEAR"}`) are lost: "end date not specified" | Use the explicit fields: `relativePeriods`, `dataDimensionItems`, `categoryDimensions`, `organisationUnitLevels`, `userOrganisationUnit`. The single-object `POST /api/visualizations` is the reverse: it takes `columns`/`rows`/`filters` items and drops the export-shape fields with a 201 (verified 2.42.6, 2.43.1) |
| Dashboard text items | Markdown-lite: `*bold*`, not `**bold**` | |
| Working lists | Tracked-entity filters with an event status: web ignores the status and lists every active TE | Use **program stage working lists** (`programStageWorkingLists`) — web and Android both honour them |
| Tracker API `eventStatus` | Must come with `eventOccurredAfter/Before` | Add a date range when querying |

### Counting multi-select reasons

Keep one MULTI_TEXT field for entry (fast on both platforms) and count each reason with
`containsItems(#{stage.C_REASONS}, 'R07')` in the PI filter. `containsItems` parses the
comma-separated value and matches whole items; `contains` is a substring match, so it needs codes
where no code is part of another. Both exist in indicators, program indicators, predictors and
validation rules from 2.41; in program rules the function is `d2:contains` (or
`d2:validatePattern` with word boundaries). Always validate an expression with
`POST /api/programIndicators/filter/description` before concluding a function is missing.

The first build missed this, and used 17 hidden, rule-assigned yes-only data elements per option
(204 rules across six programs). They worked, but were pure overhead. They were removed, and
the indicators then matched the ground truth unchanged.

### Entry settings: what they actually do

| Setting | Android 3.4.2 | Web Capture 2.42 |
|---|---|---|
| Radio render type, data elements | Honoured; **except long option sets (11 options) → bottom-sheet list** | Honoured, including 11 options |
| Radio render type, attributes | Honoured | **Ignored: always a drop-down** |
| "Default" render, data elements | Tiles (one tap, like radio) in some stages, drop-downs in others | Drop-down (one extra click) |
| Yes/no fields | Always radio | Always radio, whatever the render type |
| AGE value type | "DATE OF BIRTH or AGE": 8 digits or years | Date input + years/months/days |
| Use first stage during registration | n/a | Works (one page), then opens the event in edit mode: +1 click |
| Auto-generate + open after enrollment | Opens the event with an **empty date**: +3 taps (calendar, Today, OK) | Date empty, must be typed |
| Ask to create new event on completion | "Schedule next event?" (schedules, doesn't open): +2 taps | New event opens on the **Schedule** tab; switching to Report asks to discard: +2 clicks |
| Sections | Collapsed accordions; section "Next" sits under the save FAB | Flat |
| Relationship "create new" | Opens a search across **all** programs of the TET; program not preselected; forced search; org unit picker | One form, program and org unit preselected |
| Registration | Forced search first (if searchable attributes); search values carry into the form | Search optional; values carry over |

Measure these once in a variant program (AV here) instead of guessing.
