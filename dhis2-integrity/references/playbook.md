# Cleanup Playbook — triage, per-check fixes, guards, merge, SQL, naming

Table of contents:
1. The data-integrity API — mechanics & gotchas
2. Triage taxonomy
3. Per-check playbook (the fixes that generalise)
4. API business-guards you can't argue with — and how to pass them
5. The merge/dedup endpoints (2.38+, more types per version; prefer these for duplicates)
6. Direct SQL — when, and how to do it safely
7. Naming conventions — safe vs. needs sign-off
8. Self-inflicted-wound watch-list

---

## 1. The data-integrity API (2.38+) — mechanics & gotchas

- `GET /api/dataIntegrity` — catalog of all checks (`name`, `severity`, `code`, `isSlow`,
  `isProgrammatic`, `introduction`, `recommendation`). Severities: `CRITICAL`, `SEVERE`, `WARNING`,
  `INFO`.
- `POST /api/dataIntegrity/summary` and `POST /api/dataIntegrity/details?checks=a,b,c` trigger async
  runs; `GET` the same paths to read results.
- **Async AND cached** — two traps:
  - A `GET` immediately after `POST` returns the **previous** result. Gate your wait-loop on the
    per-check **`finishedTime` changing** (capture it before POST, poll until it moves) — not on the key
    merely being present. `scripts/integrity.py` does this for you.
  - `summary` does **not** populate `details`, and the **slow/programmatic** checks (~17:
    `isSlow`/`isProgrammatic`) are **excluded from the summary run**. Trigger them explicitly via
    `details?checks=...`. If a check is "missing" from the summary, it's a slow one — run it.
- Summary's issue count is the `count` field. Details give `issues[].{id,name,comment,refs}` — the
  `comment`/`refs` usually encode the exact problem (e.g. `"Diagnosis:{Anaemia,AFP}"` = the two
  conflicting groups; `"IDSR Weekly;IDSR Weekly (Start Wednesday)"` = the two conflicting datasets).
- **Always read the check's own `introduction` + `recommendation`** before fixing. They give the
  intended remedy and frequently note "there may be legitimate reasons" — a signal the issue may belong
  in the *accept* bucket rather than being forced.
- **Freshly-(re)started instance → counts are unreliable.** The summary can return partial results for
  the first minute or two after boot (e.g. 34 nonzero checks, then 50 once settled). Re-run the inventory
  until two consecutive runs agree before triaging.
- **On large instances, `details` silently returns empty for heavy checks** (the dedup, group-set, and
  exclusive-violation checks) — the detail job times out or is dropped, so you get `issues: []` while
  `summary.count` says hundreds. **Trust the summary count, and derive the actual issue list yourself**
  with a direct API query (fetch the objects and group/compare in code) or SQL. Never conclude "it's
  fixed" from `details=0` alone — confirm with the summary count and/or a direct query.
  `details?checks=a,b` has also been seen to ignore the filter and serve stale lists (empty while the
  summary said 17) — SQL (`ST_AsText(geometry)='POINT(0 0)'`, `NOT ST_IsValid(geometry)`) was right.
- **A `cacheClear` during a run empties the summary**, and on **2.43.1** it can leave every later
  DATA_INTEGRITY job `SCHEDULED`/`NOT_STARTED` (none `RUNNING`) until **Tomcat restarts**; the summary
  then returns `{}` indefinitely. Fix scripts and merge endpoints clear caches, so this strikes mid-
  engagement. `GET /api/jobConfigurations?filter=jobType:eq:DATA_INTEGRITY&fields=jobStatus,lastExecutedStatus`
  shows it; `integrity.py` checks this and refuses to print a partial run as an inventory.
- **On 2.40 a bare `GET /dataIntegrity/summary` returns `{}`** even after the job completes — results come
  back only for explicitly named `?checks=a,b,c` (`integrity.py` always names them). A second POST while a
  run is in progress returns `409 E1004`.
- **Check names differ between versions** (2.38 has ~39 checks, several named differently from 2.40+:
  `org_units_being_orphaned`, `program_rules_without_action`, …). A precondition or verify naming a check
  the target lacks must fail, not read as 0 — the playbook's `check()` raises on unknown names. Take names
  from the target's own `GET /api/dataIntegrity`, never from another version's playbook.

## 2. Triage taxonomy

Default rule (see SKILL.md §4): **auto-fix the deterministic bucket; ASK for the judgment bucket** —
unless the user has explicitly authorized best-effort autonomy for this engagement. Instance-specific
answers from an earlier engagement never carry over; *policy-level* answers (never delete by
`DELETE_`-name; restore-and-flag broken rules; bucket group-set stragglers into "Other/unknown") may be
offered as **pre-selected defaults with provenance** in the decisions round — confirmed, never silently
applied. Start from each check's own `recommendation`.

- **Deterministic-safe (just do it):** the small whitelist of zero-judgment, zero-semantic-impact fixes —
  whitespace/typo cleanup; `(%)` postfix + age-range naming; quoting a UID in an expression; stripping an
  unsupported `AVG()`; `aggregationType=NONE` on a non-numeric DE; setting a program-rule priority;
  trigram config; and unambiguous cases (e.g. dropping a `DELETE_`-marked deprecated membership). These
  may be applied without asking even in non-autonomous mode.
  *(Note: **merges/dedup, deletions, structural group-set changes, user-access config, expression
  rewrites, period-type and geometry changes are NOT in this bucket** — they're correct fixes but
  destructive/impactful, so they go in "Judgment" below: confirm unless the user granted autonomy.)*
- **Judgment / destructive (ASK unless granted autonomy):** these depend on intent/source-data you can't
  see — don't guess:
  - **Deleting "unused" metadata.** Auto-delete only the strict zero-data + zero-reference set, and even
    that **only when the user is working in isolation / granted autonomy**; otherwise ask. *Broken-but-
    intentional objects are NOT "unused" — repair/restore+flag, never delete* — with one exception:
    **abandonment** (broken AND `lastUpdated` years old AND zero data AND zero references) makes batch
    deletion acceptable with a documented list, no per-object ask (see SKILL.md §4). **Decide PER OBJECT
    TYPE — there is no single blanket rule** (the appropriate handling differs):
    - *indicators not used in any analysis* (`indicator_no_analysis`) — being unused ≠ wrong; deletion is a
      content decision (an indicator may be kept for future dashboards). Default: ask/keep.
    - *aggregate DEs with no analysis* — usually in datasets / feeding other things → keep; don't delete.
    - *DEs without datasets* (`data_elements_without_datasets`) — delete only if also zero-data and unreferenced.
    - *unused option sets / category combos* — deletable if truly unreferenced, but confirm.
    - *empty datasets* — likely a side-effect of removing DEs; confirm before deleting the dataset.
    Surface these as **separate per-type decisions**, not one "delete all unused" toggle.
  - **"Not viewed in 1 year" favorites** (vis/maps/dashboards): **ASK** — significance depends on the
    **age of the database copy** you're reviewing (on a months-old copy "not viewed" means nothing). Not a
    safe auto-accept-or-delete.
  - **Coordinates:** `no_coordinates` → **accept** (never fabricate GPS). `not_contained_by_parent` →
    **ASK** before removing the wrong coordinates, unless autonomy was granted.
  - **Exclusive-group / period-type / niche-dimension conflicts** where two *real* values overlap or the
    canonical choice is unknown → revert/leave + flag (§3 has the specifics).
- **Accepted-unfixable (document, don't force):** "no coordinates" without GPS; `indicator_no_analysis` /
  `data_elements_aggregate_no_analysis` — **these only mean "not in a chart/map/pivot"; they do NOT count
  use in another indicator's expression (`I{}`) or in a dataset** (verified: dozens of "no-analysis"
  indicators were referenced in other indicators/datasets). So "no analysis" ≠ unused/deletable — default
  **keep + document**; never bulk-delete (you'd break parent indicators / dataset feedback). "no data /
  abandoned" DEs still referenced or holding data; dimensions not yet in a visualization; a group that's
  *legitimately* small (e.g. a "Country" group with one country); at-scale `*_group_sets_incomplete`
  age-band overlap (§3). Forcing these means deletion or fake data — worse than the warning.
- **Blocked-by-privilege:** needs `ALL` authority or DB access (§4, §6).

## 3. Per-check playbook (the fixes that generalise)

- **`*_scarce` (group/group-set < 2 members):** delete the **empty** (0-member) groups (if not otherwise
  referenced) — that's the safe, deterministic part. **Do NOT auto-populate 1-member groups** (you'd be
  guessing which member belongs), and **never delete a scarce group-SET** (it's an analytical dimension) —
  **flag both for the owner**. (Earlier guidance said "populate 1-member"; in practice that's a judgment
  call, so flag it unless autonomy is granted.)
- **`*_not_grouped` / `*_without_groups` / `*_no_groups`:** map members to existing thematic groups by
  keyword; for bulk data elements, **group by their dataset** (one DE group per dataset) — meaningful
  and fast. Catch-all groups only as a last resort.
- **`*_violating_exclusive_group_sets` / `*_excess_groupset_membership`:** object is in ≥2 groups of one
  exclusive set → it must end up in exactly one. Three cases:
  - one group is a clear **generic/catch-all** (e.g. "Public facilities", a `DELETE_…` deprecated group)
    → drop the catch-all, keep the specific. *Deterministic.*
  - the conflicting groups are a *structurally foreign* dimension mixed into the set (e.g. HIV/TB/VCT
    program-lists inside a facility-LEVEL set) → remove those whole groups **from the set** (don't edit
    each member). *Deterministic.*
  - two **equally-valid** values overlap and you can't tell which is true (e.g. Private vs Public, Strata
    1 vs 2a) → **move the member to an "Unknown" group, or revert + flag** — do NOT guess. *Ask / autonomy.*
- **`*_group_sets_incomplete`:** usually a member option leaked in from *another* category/dimension,
  dragging that whole dimension into scope. Find and remove the **foreign** option from the group —
  don't add the "missing" options, they don't belong.
  **At scale (hundreds–thousands) it's almost always the overlapping-categorization design pattern:**
  category options are reused across multiple overlapping categorizations of the **same dimension** —
  age (`0-4/5-14/15+` vs 5-year bands vs COVID bands), sex (`Male/Female` vs `…/Pregnant/Other/Unknown`),
  attendant, etc. — so each group set is flagged for "missing" options it logically shouldn't contain
  (a `0-4/5-14/15+` set "missing" `25-34`; a `Male/Female` set "missing" `Female - Pregnant`).
  **Do the smarter triage before deciding:** group the details `issues` by group-set, extract each
  missing option, and classify it — **same-dimension variant** (another age/sex/… band → design overlap,
  *not* fixable, document) vs **foreign unrelated-dimension option** (e.g. a stock-reason inside a funding
  group → a genuine leak you *can* remove). If, as is usual at scale, they're all same-dimension variants,
  there's **no safe per-item fix** — "completing" a set with incompatible options corrupts the dimension.
  **Accept and document as a data-model review item** (stop sharing options across overlapping
  categorizations / consolidate them); auto-leave only under granted autonomy, else surface to the owner.
- **`org_units_not_in_compulsory_group_sets`:** the **recommended default** (validated across four
  engagements — present it pre-selected in the decisions round, the remaining judgment call being only
  the membership of specific objects) is to add an **"Unknown" / "Other" group** (fixed pre-generated
  UID) to the group set and assign the unclassified org units to it — **especially when the group set
  is `compulsory` OR `dataDimension=true`** (an analytical dimension). Check the `dataDimension` flag.
  Reason: an org unit in *no* group of a dimensional group set silently **drops out of any pivot/map/
  data-set that splits by that dimension** — so "make it non-compulsory" or "leave the gap" hides data.
  An explicit "Unknown" bucket keeps the dimension **complete** (every OU classified) and lets the set
  stay compulsory and usable as a dimension. Never fabricate a *real* classification (don't guess Public
  vs Private) — that's what "Unknown" is for.
  **BUT only when the dimension is *universal*** — i.e. every OU plausibly has a value (Ownership: every
  facility has an ownership). For a **niche/subset** dimension that only applies to some units (e.g.
  "REHAB - Administrative levels" applies only to rehab facilities), forcing 900+ unrelated facilities
  into a "REHAB - Unknown" bucket is wrong — **don't** auto-complete it; flag for the owner (it may
  simply not belong as compulsory). Judge universal-vs-niche from the set's name/scope; if unsure, ask.
  Apply the same "Unknown/Other group" principle to **any** group set used as an analytical dimension
  (org-unit, category-option, data-element group sets), compulsory or not, so dimension splits never
  silently drop members.
- **`tracker_geometry_invalid_srid`** (2.42+): tracker/enrollment/**event** geometries with SRID≠4326
  (WGS84). Often SRID 0 at scale (e.g. 27k event geoms). The check ships its own remediation SQL —
  `UPDATE event SET geometry=ST_SetSRID(geometry,4326) WHERE geometry IS NOT NULL AND ST_SRID(geometry)!=4326`
  (same for `enrollment`, `trackedentity`) — which only **labels** the SRID (no coordinate transform) →
  deterministic and data-safe; apply it. `cacheClear` after.
- **`orgunits_not_contained_by_parent` / `orgunits_invalid_geometry`:** the coordinates are wrong →
  remove the geometry (`PATCH [{"op":"remove","path":"/geometry"}]`), folding into the accepted
  "no coordinates" set. **ASK before removing** (the coords may be fixable from source) unless the user
  granted autonomy. `invalid_geometry` (self-intersection etc.) similarly: fix or remove + flag.
- **`data_elements_aggregate_with_different_period_types`:** a DE must live in datasets of one period
  type. **Which period type is canonical is the owner's call — ASK; do not auto-pick** (e.g. "keep
  Monthly, drop Yearly" silently removes the DE from possibly-intended annual reporting forms). With
  autonomy granted, a reasonable default is to keep the dataset that actually holds the DE's data; if
  none/both do, flag. Removing a DE from a dataset is reversible (re-add), but it changes data-entry forms.
- **invalid expressions** (validation rules / program indicators / indicators):
  - validation-rule expressions don't support `AVG()`/aggregation functions — strip the wrapper;
  - reporting rate is `R{dataSetUID.REPORTING_RATE}`, **not** `#{...REPORTING_RATE}`;
  - unquoted UIDs in program-indicator filters need quotes (`== 'uid'`);
  - a COUNT/EVENT program indicator that wants "1 per matching event" should just be `1`
    (`d2:hasValue(V{event_date})` is invalid — `V{}` program vars aren't valid for `d2:hasValue`).
  - **Validate every new expression first** with `GET /api/expressions/description?expression=...`
    (it's GET; note `%` breaks psycopg2 params — see §6) before writing it.
- **`program_rules_no_action` / `program_rules_message_no_template` / `program_stages_no_programs`:**
  **broken ≠ unwanted — repair or restore-and-flag, do NOT reflexively delete.** A rule whose name/condition
  shows clear intent ("Hide pregnant if male", "Send birth notification to CRVS") is *broken* (lost its
  action / template), not useless — restore/keep it and flag the owner to re-add the action or template.
  Only **delete** when the object is genuinely orphaned/empty/test/duplicate with no discernible intent.
  Note: a knowingly-broken action (a template-less `SENDMESSAGE`) **can't be round-tripped via the API**
  (the importer rejects it) — restore the rule shell and flag for reconfiguration.
  - **`program_stages_no_programs`: INSPECT before deleting — a "no program" stage is often a REAL stage
    that lost its program link, not junk.** Check its `programStageDataElements` count and, crucially,
    whether any **`HIDEPROGRAMSTAGE` program-rule action or program rule references it** — the referencing
    rule's `program` is the stage's correct home. (Real case on SL 2.43: "Nearby household investigation"
    looked like an empty orphan but had 11 DEs + a section and was targeted by a HIDEPROGRAMSTAGE action in
    a rule belonging to the Malaria program → the fix is **RESTORE** (set `program` to that program), not
    delete.) Only delete a stage with no DEs, no rule references, and no discernible home.
  - Deletion of a *genuine* orphan can throw a **500 "transaction silently rolled back"** when the object
    has a `null` parent (NPE in a deletion handler) — temporarily **attach it to a valid parent** then
    delete cleanly (and remember to revert the attach if you then decide NOT to delete). Delete dangling
    rule actions / section + PSDE children first (a populated stage's API delete may still 409 on a
    `programruleaction` FK — remove that action, or restore instead).
- **trigram TEA config** (`tracked_entity_attributes_invalid_trigram_search_configuration`): **NOT
  deterministic — the check's own recommendation says "depending on your needs."** Read it. At scale the
  flagged TEAs are typically *all* `trigramIndexable=false` with `LIKE`/`EW` *not* blocked (searchable but
  unindexed → slow). Two resolutions: **index** (`trigramIndexable=true` + `minCharactersToSearch>=3` +
  keep LIKE/EW unblocked) or **block** (`blockedSearchOperators` += `LIKE`,`EW`). Which one is per-needs.
  - **Don't blanket-index every text TEA** (each index has write cost). **Curate by name** the genuine
    TEI search keys — person names (first/last/given/family/middle/surname), national/unique IDs
    (national ID, passport, NHIS, registration no., any `unique=true`), phone/contact numbers, email —
    and propose *that* subset for owner approval (best-effort if autonomous). Leave non-key text
    (status/type/gender/address/occupation/comment) **un-indexed** (it stays flagged but functional —
    document the residual; the owner can index on demand).
  - **Non-text TEAs** (DATE/NUMBER/ORG_UNIT/BOOLEAN/IMAGE/COORDINATE) can't be trigram-searched → block
    `LIKE`/`EW` on them (deterministic-safe). - **Drop TEAs used in 0 programs** from the index set — no
    search benefit. (Heads-up: high flagged counts usually come from **per-program attribute
    proliferation** — each module defines its own `name`/`ID`/`phone` TEA, hence prefixes like `GEN -`,
    `MPOX CS:`; indexing is per-attribute/global, so one index serves every program that reuses it.)
  - Persist via **json-patch PATCH** of the three fields; a full `:owner` PUT can `409` on unique-name
    validation. Then run `TRACKER_TRIGRAM_INDEX_MAINTENANCE` (create jobConfiguration, `POST .../execute`,
    delete after) to clear `*_trigram_index_out_of_sync`.
- **`users_capture_ou_not_in_data_view_ou`:** set the user's data-view OUs to include the capture OUs
  (simplest: data-view = capture OUs). The sibling **`users_capture_ou_not_in_tei_search_ou`** is about
  the *optional* TEI-search scope — if empty it defaults and won't fire, so a flagged user has a partial
  search scope; aligning capture-OUs into it (or clearing it) fixes it, but it touches access scope →
  **confirm/flag** unless autonomous.
- **`user_roles_no_authorities`:** if the role has users, grant a sensible minimal set (e.g. Capture +
  Dashboard + Visualizer app authorities) rather than deleting; if unused, delete — **but only if no user
  has it as their *only* role** (deleting it would leave that user with no role). Check first; else flag.
- **`data_elements_in_data_set_not_in_form`:** SECTION form → add the DE to a section; CUSTOM form →
  the DE isn't in the HTML — parse the form for `deUID-cocUID` inputs to find the *truly* missing ones
  (often just a few, not all), then append valid input fields (PATCH `dataEntryForms/{id}` htmlCode).
- **`programs_custom_data_entry_forms_empty`:** the empty custom form is usually on a **program stage**
  (`formType=CUSTOM`, `dataEntryForm.htmlCode` blank), not the program itself — query `programStages`.
  Fix = set the stage `formType=DEFAULT` and delete the empty `dataEntryForm` so it falls back to the
  auto-generated form. (A blank custom form renders an empty data-entry screen.)
- **`program_rules_no_priority`:** arguably **not an error** — priority only defines execution order
  among rules on the same field. Setting a **uniform default** (`priority=1`) is behaviour-neutral and
  clears the warning. Bulk-set via the API; only resort to a single SQL `UPDATE programrule SET
  priority=1 WHERE priority IS NULL` if you must (it's a plain nullable field, no guard).
- **`data_elements_can_aggregate_with_none_operator`:** numeric DE with `aggregationType=NONE`. **Often
  intentional** — counts that must not sum up the hierarchy ("Number of districts that notified…"),
  modelled estimates (Spectrum PLHIV), or a year value ("Year produced"). Don't auto-set SUM — **list the
  DEs and let the owner decide** per DE (or leave + document; NONE is frequently correct).
- **`users_with_invalid_usernames`:** username is **immutable via API** (`E4056`) → SQL `UPDATE userinfo
  SET username=…`. These are often **`ALL`-authority super-users** → intrusive; **confirm** the new names
  (≥4 chars) rather than auto-renaming.
- **`datasets_not_assigned_to_org_units`:** **common and usually benign** — datasets mid-configuration or
  deliberately **archived**. Don't auto-assign (you'd guess the OU scope). **Flag/accept** for the owner.
- **Structurally broken COCs (disjoint, wrong cardinality, foreign options): scan references BEFORE
  deleting any.** A COC can be broken as structure yet still be referenced as text or by FK — deleting
  366 broken COCs in one engagement orphaned 235 indicator operands (66 indicators) and 2 custom forms,
  and only one was remappable. Before the delete, search `indicator.numerator`/`denominator`,
  `dataentryform.htmlcode`, `dataelementoperand`, `minmaxdataelement`, and section greyed fields for each
  COC UID; referenced ones go to **keep/flag**, not delete. If you have to restore one, restore its
  `datavalue` rows as well as the COC row: restoring the metadata alone left 38 sum changes that could
  not be explained until the data was copied too.
- **Deletion landmines on long-lived instances:**
  - Orphan category options: the delete returns 500 "Transaction silently rolled back" (the same NPE
    class as program stages).
  - Empty option groups that `SHOWOPTIONGROUP` rule actions still reference.
  - Test datasets referenced by `datadimensionitem` reporting-rate items.
  - On 2.43.1, some programs imported from 2.38 fail `DELETE /api/programs/{id}` with `409 current
    transaction is aborted`, even with no stages. The §6 FK-graph cascade removes them.
- **Bulk deletion of unreferenced objects: use `POST /api/metadata?importStrategy=DELETE` in chunks of
  ~200** rather than one `DELETE` per object. 7,676 favourites took ~2 min this way, against ~70 min at
  ~110 objects/min one at a time.
- **Clearing an org unit's data without a DB route:** `POST /api/maintenance/dataPruning/organisationUnits/{uid}`.
  It removes data values **and their audit rows**, completeness and events, and needs `ALL`. A
  `dataValueSets?importStrategy=DELETE` only soft-deletes, and the `deleted=true` rows plus
  `datavalueaudit` still veto the org-unit delete. Audit history cannot be archived through the API:
  `/api/audits/dataValue` lists it, but nothing imports it back, so the change proposal must say the
  history is lost. Archive before any destructive org-unit step:
  - Export the units and their ancestors, dataset and program assignments, user memberships and
    favourite references.
  - Export all data values across every dataset.
  - Prove archive → delete → restore to identical counts on the sandbox.
  - Traps: exporting data for units outside your data-view hierarchy fails with `409 E2012`; grant the
    scope temporarily, then revert. `dataValueSets` returns `400` with an empty body for datasets with
    zero data elements.
- **2.38 specifics:**
  - There is no `cascadeSharing` (`404 Property cascadeSharing does not exist on Dashboard`); cascade
    group access one item at a time via `/api/sharing`.
  - The dashboards list endpoint hides non-public dashboards even from superusers; enumerate with
    `/dashboards/gist` or by UID.
- **duplicates (combos / COCs / indicators / DEs / org units / categories):** use the **merge endpoints**
  (§5). NB: if a check-refresh (`fresh()`) times out and returns an **empty** issue list, you'll merge
  *nothing* and wrongly conclude "no duplicates" — always confirm the list is non-empty (re-run with a
  longer wait) before reporting zero.

## 4. API business-guards you can't argue with — and how to pass them

These fire **regardless of authority** (even with `ALL`) — they're store-layer business rules:

| Guard | Means | Pass it by |
|-------|-------|-----------|
| `E1120` "would make existing data values inaccessible" | Can't change a category combo's categories while it has live data | Remove/relocate the data first, **or** SQL-edit `categorycombos_categories` directly (§6) |
| `E4030` "associated with … DataValueChangelog/COC/TrackerEvent" | Object can't be deleted while referenced | Migrate/clear the references first (data **and** `datavalueaudit`), then delete — or use a merge endpoint which does this |
| `E4056` "username can not be changed" | Usernames are immutable via API | SQL `UPDATE userinfo SET username=…` + cache clear |
| `E8031` "untimely data entry" | Period closed/future, or **OU not assigned to the dataset** | `force=true`+`F_EDIT_EXPIRED` handles expiry, but **not** unassigned-OU; relocate via SQL or merge instead |

Privilege/escalation realities:
- A "Superuser" role often has many explicit authorities but **not `ALL`** — check `/api/me`. Some ops
  need `ALL` (`/api/maintenance/dataPruning/...`) or a specific authority (`F_*_MERGE`).
- You **cannot self-escalate to `ALL`** (`E3032`), and **cannot modify a user that holds `ALL`** if you
  don't (`E3041`/`E3003`). So you can't "borrow" a superuser account — but you *can* add non-`ALL`
  authorities to your own role.
- **When genuinely blocked, document precisely** (root cause + exact remediation) and move on; don't
  burn the engagement on one check. Revisit if elevated access is granted.

## 5. The merge/dedup endpoints (2.38+, expanding per version) — prefer these; SQL is last resort

**The merge framework keeps EXPANDING across versions.** `POST /api/<type>/merge` exists for (checked in
dhis2-core source at 2.40.12, 2.41.10, 2.42.6, 2.43.1):

| Type | 2.40 | 2.41 | 2.42 | 2.43 |
|---|---|---|---|---|
| `organisationUnits` (since 2.38) | ✔ | ✔ | ✔ | ✔ |
| `indicators`, `indicatorTypes` | – | ✔ | ✔ | ✔ |
| `dataElements`, `categoryOptions`, `categoryOptionCombos` | – | – | ✔ | ✔ |
| `categories`, `categoryCombos` | – | – | – | ✔ |

There is no `dataSets` or `programIndicators` merge. Tracker duplicates use
`POST /api/potentialDuplicates/{uid}/merge` (all versions). Still **PROBE the live instance** before
relying on it. Quick probe: `POST /api/<type>/merge`
with an empty body `{}` — **`404`/`405` = no endpoint; `409`/`400` = endpoint exists** (it's just
rejecting the empty payload). On 2.43 the confirmed POST-merge types are the eight in the table.

> **⚠️ Merge availability is VERSION-DEPENDENT — verified, not memorized.** On **DHIS2 2.42.5.1** a probe
> returns **405 "method not supported"** for `categories/merge` AND `categoryCombos/merge` — those two do
> NOT exist before 2.43. Present on 2.42: `categoryOptions`, `categoryOptionCombos`, `dataElements`,
> `indicators`, `indicatorTypes`, `organisationUnits`. **Consequence:** on ≤2.42, consolidating duplicate categories/combos *that have
> data* has no endpoint, so the `E1120` guard leaves **SQL as legitimate first-line** for those two types
> (or upgrade the target to 2.43+ first). This is the opposite of the 2.43 rule below — so PROBE the actual
> target version every engagement; the §6b "always use the endpoint, never SQL" lesson is 2.43-specific.
> COC-level dedup (`categoryOptionCombos/merge`) is available on 2.42 and remains the right tool there.

> **Hard lesson (do not repeat):** `category` (the dimension itself) **has a merge endpoint** in 2.43.
> Consolidating two categories that share an identical option set by hand-editing
> `categorycombos_categories` in SQL is the WRONG path — the raw combo edit is blocked by `E1120`
> ("would make data values inaccessible"), which tempts you toward SQL. **The merge endpoint is
> *designed* to perform exactly that data-safe migration** and sidesteps E1120. Always probe + use
> `/categories/merge` first; only fall to SQL if the probe returns 404.

`POST /api/<type>/merge` body:
```json
{"sources": ["uid", ...], "target": "uid", "deleteSources": true, "dataMergeStrategy": "LAST_UPDATED"}
```
(`dataMergeStrategy`: `LAST_UPDATED` or `DISCARD`.) It validates source/target existence (`E1533`),
migrates **all references + data values + audit** into the target, and optionally deletes the sources —
in one transaction. Requires the per-type authority (e.g. `F_CATEGORY_OPTION_COMBO_MERGE`) or `ALL`.

**Pick the right level:**
- Duplicate **category combos** (the `categories_unique_category_combo` check) → `categoryCombos/merge`.
  This handles dataset-element overrides, data values, and COCs for you. *Don't* hand-roll it in SQL.
- True duplicate **COCs within the same combo** (`category_option_combos_have_duplicates`) →
  `categoryOptionCombos/merge`. **It only merges COCs that share the same category combo + options +
  differ only by UID** (`E1540` otherwise) — it is **not** for cross-combo consolidation.
- Duplicate **indicators / data elements / org units** → the corresponding `…/merge` (migrates
  group/favorite/expression references to the keeper). More robust than delete-the-unreferenced-one,
  though a plain delete is equivalent when the duplicate has *zero* references (verify first via
  indicatorGroups/visualizations/datasets).
- Duplicate **indicator types** (`indicator_types_duplicated`) → `indicatorTypes/merge`. The check
  compares only the `factor` — and that is **correct**: verified in current core (2.4x master), the
  indicator value computation uses `indicatorType.getFactor()` only; the `number` property is **vestigial**
  (stored/serialized but unused in analytics, not exposed in recent Maintenance apps; the old docs text
  about denominator-less "number" indicators describes legacy behavior). So same-factor types are true
  duplicates — merge to one (still confirm-unless-autonomous, since the surviving type's *name* is
  user-visible on every indicator using it). *(Corrects earlier guidance that said to keep types whose
  `number` flag differs — that distinction has no runtime effect on current versions.)*
- Redundant **categories that share an identical option set** (`categories_same_category_options`) →
  `categories/merge`. Note this check is **advisory like the indicator ones**: same options ≠ same
  dimension. `Sex`/`Sex (m/f)` (both M/F) are truly redundant, but `Malaria IPT Dose`/`OPV Dose` (both
  "Dose 0-4") may be deliberately distinct dimensions — **confirm with the owner which to merge**, and
  give the survivor a **generic, program-neutral name** (e.g. `Dose (0-4)`). The merge handles the
  E1120 data migration; do **not** SQL-edit `categorycombos_categories` for this.

  **⚠️ "Same formula/expression" is NOT "safe to merge".** The `indicators_exact_duplicates`,
  `indicators_duplicated_terms`, and `indicators_with_identical_formulas` checks flag objects that
  *share an expression* — but many are **semantically distinct** (e.g. `BEmONC` / `CEmONC` / `IEmONC`
  "facilities with all basic infrastructure" share a numerator/denominator yet mean different things).
  Merging them destroys real indicators. These checks are **advisory, not a merge directive**. Merge
  **only true duplicates** — shared formula AND near-identical names (same name modulo
  whitespace/case/trivial wording) — and **document the semantically-distinct same-formula groups as
  accepted/flag-for-review** instead of merging. This is a judgement call: when unsure, keep and document.

**Rule of thumb:** *dedup/consolidation → merge endpoint (probe first); SQL only when the probe returns
404 AND no endpoint can do it.* An `E1120`/`E4030` block is **not** a signal to jump to SQL — it's
usually a signal you're hand-editing something a merge endpoint should do. The genuinely SQL-only cases
are narrow: e.g. swapping a single combo's **wrong** category where there is **no dedup target** to merge
into (a true one-combo metadata correction), or relocating data under a closed period.

## 6. Direct SQL — when, and how to do it safely

Only for fixes an API guard blocks (§4) **and** that no merge endpoint can do — **probe `/<type>/merge`
first** (§5); an `E1120`/`E4030` block alone does not justify SQL. Find the DB: for **broker / dev-net
instances, prefer the container-internal DB host** (e.g. `dhis2-<name>-db:5432` on dev-net) over the
host-published port — it's immune to host-port churn (a broker restart can reassign the published port, or
a sibling can grab it, breaking a `PGHOST=gateway PGPORT=54xx` config mid-run). Non-broker instances
often **publish PostgreSQL to the host gateway** — `ip route | awk '/default/{print $3}'` for the
gateway IP, then scan candidate ports (broker instances and siblings sit on adjacent ports). Typical
test creds `dbname=dhis2 user=dhis password=dhis`. No `psql`? `pip install psycopg2-binary`. Confirm
you're on the right DB by cross-checking a count against the API (e.g. `dataelement` count).

**Real deployments: assume NO direct DB route.** Direct SQL from your environment is realistic on
sandboxes/local copies (triage, developing the fix), but on online dev/production instances SQL is
almost always run **by a DBA on the server itself** (psql, or a reviewed `.sql` file). Design for that:
- author every fix's SQL as **dedicated `sql` manifest steps, UID-based** (subqueries or `resolve_id`,
  never numeric ids) — `manifest.py render` then emits them into a standalone **`sql-fixes-*.sql`**
  (one BEGIN/COMMIT block per statement, ordering warnings, cache-clear reminders) that the DBA can run
  block-by-block or with `psql -v ON_ERROR_STOP=1 -f`;
- the rendered playbook auto-detects the situation (`SQL_MODE`): with PG env vars its SQL cells execute
  directly; without, each SQL cell prints its statement + block reference and pauses for the DBA — the
  notebook keeps driving the ORDER (SQL interleaves with API steps within a fix) and the verify cells
  prove the statement was actually applied;
- avoid burying `sql()` calls inside `code` steps when a dedicated sql step can express the fix — code-
  embedded SQL cannot be exported to the `.sql` file (validate() warns about it);
- **every exported statement must be SELF-GUARDING (idempotent)** — the `.sql` file runs without the
  playbook's precondition guards, so write state-transitions whose WHERE matches the **pre-fix state**
  (`UPDATE … SET new WHERE old` → a re-run matches 0 rows; `WHERE priority IS NULL`; DELETE by explicit
  keys), use `INSERT … ON CONFLICT DO NOTHING`, and never leave a bare accumulating update
  (`SET x = x + …`) — if a fix must sum/migrate, consume the source rows in the same transaction so a
  second run finds nothing to add. The exported header states this contract ("a re-run reports 0 rows;
  more rows than the proposal describes → stop and investigate"), and manifest `validate()` flags the
  common re-run-unsafe shapes;
- **guards are TIERED: destructive steps get an aborting guard on top.** Self-guarding is the base for
  every statement; a **destructive** sql step (DELETE, `INSERT … SELECT` data migration) must also carry
  a `guard` field — a boolean SQL expression asserting the expected pre-fix state (e.g.
  `EXISTS (SELECT 1 FROM categorycombo WHERE uid='…' AND name='…')`). The renderers emit it as a
  `DO $$ … RAISE EXCEPTION … $$` block **inside the same transaction**, in both the `.sql` export and
  the playbook cell, so on a drifted database the whole fix rolls back instead of half-applying. A
  "GUARD FAILED" error is the guard working — verify the target before retrying. `validate()` warns
  when a destructive step has no guard.

Schema notes (2.4x): tables are **singular** — `categorycombo`, `categoryoptioncombo`, `category`,
`datasetelement`, `userinfo`; joins `categorycombos_categories`, `categorycombos_optioncombos`,
`categoryoptioncombos_categoryoptions`, `categories_categoryoptions`. **The "changelog" the API vetoes
on is the `datavalueaudit` table.**

**Version landmines** (each broke otherwise-correct SQL on a real run):
- **Event tables renamed twice**: `programstageinstance` → `event` in 2.41; 2.43 split `event` into
  `trackerevent` + `singleevent`. Version-portable event SQL loops over all generations:
  `FOREACH t IN ARRAY['programstageinstance','event','trackerevent','singleevent'] … CONTINUE WHEN to_regclass(t) IS NULL`.
- **2.43 added NOT NULL `period.iso`** (the ISO period name; weekly is unpadded ISO week-year, e.g.
  `2027W1`) — any `INSERT INTO period` must populate it.
- **2.42 made `userinfo.twofactortype` a NOT NULL enum** (2FA off = `'NOT_ENABLED'`, not NULL);
  ≤2.41 uses the nullable `userinfo.secret` instead.

**Catalog-driven SQL beats hand-maintained table lists** — two techniques that survive version drift
because they enumerate the live schema instead of hardcoding it:
- *FK-graph cascade delete*: recurse over `pg_constraint` — for NOT NULL referencing columns, delete the
  referencing rows recursively; for nullable ones, `SET NULL`; skip constraints with
  `confdeltype IN ('c','n')` (the DB handles those itself). DHIS2 FKs are NO ACTION checked at end of
  statement, so whole self-referencing subtrees delete in one call; `relationship`↔`relationshipitem`
  are ON DELETE CASCADE both ways.
- *FK-driven repointing* for merges: enumerate referencing tables/columns from the catalog and
  `UPDATE … SET col=<keep> WHERE col=<remove>`, special-casing only the natural-key tables
  (`datavalue`, `completedatasetregistration`, `minmaxdataelement`) where repointing can collide.

For pre-2.43 category/combo/COC merges, ask whether the user already has a vetted SQL merge toolkit
(duplicate diagnostics, merge functions with LAST_UPDATED conflict resolution, precondition checks,
savepoint atomicity, and the COC data migration the `E1120` guard protects). If one exists, prefer it
over hand-writing the consolidation SQL below.

Proven surgical patterns:
- **Swap a combo's category** (fixes disjoint COCs without moving any data — the data already sits under
  the right COCs, only the combo metadata is wrong):
  `UPDATE categorycombos_categories SET categoryid=<new> WHERE categorycomboid=<combo> AND categoryid=<old>`.
- **Consolidate duplicate combos / merge categories** (the ONLY path when `categoryCombos/merge` /
  `categories/merge` are unavailable — i.e. ≤2.42). Pick a survivor per `(dataDimensionType, sorted
  category-ids)`; map each source COC → survivor COC by identical option-set; then repoint **every**
  reference and delete the emptied source:
  - combo-level FKs: `dataelement`, `dataset`, `datasetelement`, `program` `.categorycomboid` → survivor.
  - COC-level FKs: `dataelementoperand`, `minmaxdataelement` `.categoryoptioncomboid`; `event`,
    `datavalue`, `datavalueaudit` `.attributeoptioncomboid`/`.categoryoptioncomboid` → survivor COC
    (delete colliding rows first where a unique index would trip).
  - **COC-level TEXT refs** (the ones name-based deletion silently breaks): `indicator.numerator`/
    `denominator` and `dataentryform.htmlcode` — `replace(<sourceCOCuid>, <survivorCOCuid>)`.
  - if data exists, migrate `datavalue`+`datavalueaudit` by matching COC (see conflict check below).
  - Then delete the source combo via **API** (E4030 veto is the backstop) → cascades its COCs;
    `cacheClear`+`categoryOptionComboUpdate`.
- **A category used as a favorite/analytics dimension blocks its own deletion** with `E4030 "…associated
  with CategoryDimension"`. Repoint the dimension before deleting the category:
  `UPDATE categorydimension SET categoryid=<survivor> WHERE categorydimensionid=<the row>` (the
  dimension's `categorydimension_items` stay valid if survivor shares the options).
- **Rename a username:** `UPDATE userinfo SET username=…`.
- **Bulk-load a huge join table** the API OOMs on (e.g. `orgunitgroupmembers`, 1M+ rows): resolve
  uid→id maps, delete-then-`execute_values`-insert in 50k batches directly. Then `cacheClear`.
- **`sort_order` renumbering where `sort_order` is part of the PK** (e.g. `visualization_organisationunits`):
  a single-pass renumber can collide transiently. Two-phase update: park changed rows on unique negative
  values (`SET sort_order = -(new)-1`), then flip (`SET sort_order = -(sort_order)-1 WHERE sort_order < 0`).
- **A COC migration on a huge `datavalue` table (10M+ rows) needs a temp index.** `datavalue` is indexed
  only on its composite PK (+deleted/lastupdated), so `UPDATE datavalue ... WHERE categoryoptioncomboid=X`
  full-scans the whole table per COC (a combo-merge on a 27.8M-row table timed out at 6+ min). Before the
  migration: `CREATE INDEX tmp_dv_coc ON datavalue(categoryoptioncomboid)` and one on
  `attributeoptioncomboid` (~20s to build), run the UPDATEs (now instant), then **DROP** them.
- **The SQL combo-merge must repoint `dataElementOperand.categoryoptioncomboid` (and `minmaxdataelement`)
  too**, not just datavalue/audit/indicator-expr/form — a single lingering operand ref makes the source
  combo's API delete NPE (500) / FK-block. Map source COC→survivor COC by option-set and repoint operands
  before deleting the source.

**PostgreSQL at scale — bulk cleanups on 10M+ row tables:**
- **Commit, then vacuum, then continue.** Never delete parents in the same transaction as a mass child
  delete. With 22M dead `datavalue` rows, each FK check (`SELECT 1 FROM datavalue WHERE dataelementid=$1
  FOR KEY SHARE`) walks the dead index entries, and 2,792 parent deletes ran for more than 5 min.
  COMMIT the child delete, `VACUUM` the child table (it cannot run inside a transaction), then delete the
  parents in a new transaction.
- **Stock DHIS2 lacks many FK indexes.** On 2.40 there is no index on `datadimensionitem`'s data-element
  FK columns, nor on `datavalue.{periodid,sourceid,categoryoptioncomboid}`, so any cascading metadata
  delete seq-scans. Creating the ~197 missing FK indexes took 4 s for all tables under 50 MB and cut a
  multi-minute stall to 7 s. Do it before a bulk metadata delete, and drop the indexes afterwards if the
  target is production.
- **Batch whole-table UPDATEs by ctid block range:** `WHERE ctid >= '(lo,0)'::tid AND ctid < '(hi,0)'::tid`
  plus a predicate that matches only rows not yet done, so the batch is idempotent and resumable.
  Batching by business key turns it into random I/O (~4× slower), and `LIMIT n` loops are O(n²).
- **"No space left on device" from VACUUM or a parallel query is usually `/dev/shm`, not disk.** It
  appears as `could not resize shared memory segment`, from Docker's 64 MB default. Run
  `PGOPTIONS="-c max_parallel_maintenance_workers=0" psql -c "vacuum analyze …"`, or `VACUUM (PARALLEL 0)`.
  For queries, set `max_parallel_workers_per_gather=0`. Don't put `SET …; VACUUM …` in one `psql -c`:
  it runs as a transaction, and VACUUM refuses.
- **Legacy tables:** a dump from a long-lived instance can carry ~47 pre-2.40 tables (`patient*`,
  `importdatavalue`, `validationcriteria`, …) that a stock Flyway init of the same version never creates.
  Diff `pg_tables` against a freshly initialised instance of that version to find them.

SQL safety checklist:
1. **Find ALL foreign-key references first** — query `information_schema` for FKs pointing at the table,
   then count rows per referencing column for your target ids. Don't delete until the only remaining
   references are migrated or cascade.
2. **Check for UPDATE conflicts** before moving data — a `(dataelementid, periodid, sourceid,
   attributeoptioncomboid, categoryoptioncomboid)` collision violates the unique index. Verify with a
   self-join (usually none if source/target serve different DEs).
3. **After any SQL metadata change, `POST /api/maintenance?cacheClear=true`** — DHIS2 caches metadata
   in memory; the API/checks won't see your change otherwise. Run `categoryOptionComboUpdate` too if you
   changed combo/COC structure.
4. Prefer the **API for deletions** (it cascades join tables + runs deletion handlers) — but only after
   you've migrated the data/audit away so the delete isn't vetoed.
5. psycopg2 treats `%` as a placeholder — pass `LIKE` patterns as **parameters**, or escape `%%`.

## 7. Naming conventions — deterministic fixes ONLY (own track)

Reference: dhis2-docs `metadata-naming-conventions.md`. This process applies **only the deterministic,
objectively-correct naming fixes**, and keeps them as a **separate track/playbook** from the integrity
fixes (they change end-user-visible labels, so an admin may apply/review them on a different cadence).
Object **UIDs don't change on rename**, so forms/visualizations/rules keep working — renames are
reference-safe.

**In scope (auto-applyable):**
- Trim/collapse whitespace in `name`/`shortName`.
- Fix obvious typos (e.g. `"Accute"`→`"Acute"`).
- Age-range notation: `<5 yrs`→`0-4 yrs`, `<15y`→`0-14y`, `>49y`→`50+y`, `=>4`→`4+`.
- Append ` (%)` to percentage-type indicators (`indicatorType.factor==100`) whose name lacks it.

**Do NOT blindly convert** (the `%`/`<` is part of the meaning, not an age/percentage band):
time thresholds (`<24 hrs`), standard infancy/EPI denominators (`<1y` coverage), clinical %-thresholds
(`<70%` weight-for-height).

**OUT OF SCOPE for this process — do not attempt, do not produce a proposal table:** the convention's
acronym-prefix rule (`TB_`/`MAL_`/`HIV_`/`EPI_`/`GEN_` on every DE/indicator) and `ACRONYM_` code
standardization. That's a per-object domain-classification + governance exercise handled separately; this
skill deliberately leaves it alone.

**Gotcha:** a `PATCH` rename can fail (`409`) on an **unrelated dangling custom-attribute value** (an
attribute not assigned to that object type, `E6012`). Fall back to a full-object **PUT** with the bad
`attributeValue` stripped — and note that dangling attribute as its own integrity fix. This is an
instance of a general rule: **JSON-Patch and full-object writes re-validate the whole object**, so on
the messy metadata this skill targets they 409 over pre-existing issues unrelated to the change.
Dedicated endpoints skip that revalidation — e.g. sharing changes should go through
`PUT /api/sharing?type=<singular>&id=<uid>` (recipe in the `dhis2-docs` skill), never a patch of
`/sharing` (which also silently ignores `public`/`external` keys).

## 8. Self-inflicted-wound watch-list

- **Removing an object's only action/member can create a *new* violation** (deleting a rule's last
  action leaves the rule action-less; adding a 2nd group to a group-set can introduce an exclusive-set
  violation if the two groups share a member). **Re-run the broad summary after each batch**, not just
  the check you targeted.
- **Prefix-based grouping self-inflicts `*_groups_scarce`.** Grouping indicators/DEs by name-prefix to
  clear `*_not_grouped` can create many single-member groups (real engagements produced 14–17 singletons each — worse when
  prefixes are mostly unique). Better: route would-be singletons into one "… Other (misc)" catch-all **from the
  start**, not per-prefix. Re-check the scarce checks after; the pre-existing 1-member groups stay (flag,
  don't auto-populate).
- **Deleting a POPULATED indicator/data-element group can 409** — the delete handler cascades toward the
  member object and trips an FK on a *different* reference (e.g. `fk_dataset_indicatorid` on
  `datasetindicators`: the member indicator is in a dataset). **Empty the group first** (remove members
  via `DELETE /<groupType>/{gid}/<members>/{id}`), then delete the now-empty group. (This is why a naive
  "delete scarce group" loop silently fails on referenced members.)
- **Bulk metadata POSTs need `atomicMode=NONE`, or ONE bad object rolls back the WHOLE batch.** A
  DE-by-dataset grouping POST of 140 groups returned "created 0, ignored 140" because 3 errored under the
  default atomic mode; re-posting with `atomicMode=NONE` landed 136 and ignored only the 3 bad ones. Set it
  on every bulk `/api/metadata` POST (the bundled import script already defaults to NONE).
- **A `categoryCombos/merge` leaves duplicate COCs in the survivor — the merge report says so.** Always
  follow it with a `categoryOptionCombos/merge` dedup pass (group the survivor's COCs by option-set, merge
  each dup group into one) + `cacheClear`&`categoryOptionComboUpdate`. Validated on SL 2.43: combo merge →
  4 dup COCs → COC merge cleared them; both endpoints exist on 2.43 (405 on 2.42 → SQL, see §5).
- **Every merge, not just the SQL path, needs two follow-ups:**
  - **Find the duplicate COCs from SQL-derived option sets.** After merges the API listing served
    pre-merge option sets from cache: it found 0 groups where SQL found 198.
  - **Text-repoint `dataentryform.htmlcode`** from source to target. `categoryOptionCombos/merge` (2.43.1)
    moves data values and operands but not form HTML: 860 `deUID-cocUID-val` cells still named deleted
    COCs.
  - `categories/merge` strips foreign options from COCs that already had the wrong cardinality, which
    creates duplicates even when no combo merge ran.
  - `categoryCombos/merge` returns `409 Duplicate CategoryOptionCombo` when the **target** already holds
    duplicates. Run the COC dedup pass **before** the combo merge as well as after.
- **A "new" check appearing after fixes is NOT automatically self-inflicted — verify vs the source.**
  one engagement's `indicators_with_invalid_denominator=3` became `indicators_with_invalid_numerator=5` after a
  `categoryOptionComboUpdate`; it looked like a regression but all 5 referenced DEs/COCs/indicators that
  were **never in the source dump** (pre-existing dangling refs; the check merely re-evaluated num-vs-den).
  Before blaming the cleanup: grep the source metadata for the referenced UIDs and confirm the missing
  object isn't one *you* deleted (scan your delete log). Pre-existing → flag for owner, don't fabricate.
- **"Unused"-object deletion must scan EXPRESSION-operand references, not just `dataElement.categoryCombo`.**
  A category combo unused by any DE can still be referenced as a **dataset attribute combo** (seen in
  practice: a combo flagged "unused" by a DE-only scan was the attribute combo of every dataset —
  deleting it would have dangled them all), a **program-indicator AOC**, or via its **COCs
  in indicator operands** (`#{de.coc}`), section greyedFields, or predictor outputCombo. Scan all of these
  before deleting; the API `E4030`/`E4061` veto is the final backstop (let it catch what you missed).
- `categoryOptionComboUpdate` **regenerates** COCs you deleted if the combo's categories still imply
  them — fix the combo's categories first, then delete the orphaned COCs.
- Deleting unused metadata can cascade group memberships (usually fine). A deletion **vetoed by hidden
  data** (e.g. a "no_data" DE that actually has tracker events) is a useful safety net — let the veto
  catch it rather than pre-checking everything.
- Renames can theoretically create duplicate names — after a naming pass, check `datasets_same_name`
  and equivalents didn't appear.
- **Creating an object with a bare `POST` (no `id`) is NOT idempotent** — the server assigns a fresh UID
  each run, so re-running the same fix **duplicates** the object (two "Unknown" groups, two "Additional
  data elements" sections, …). Always **pre-generate a fixed UID** (`/api/system/id` or reuse the
  dedicated instance's) and create-by-UID (exists → skip); guard non-UID creates (e.g. dataset sections)
  with a name-on-parent existence check at minimum. This also keeps the id stable across instances and
  makes the create reversible.
- **NEVER dedupe duplicate category COMBOS by name — even `...DELETE`-named ones are referenced.** On
  one 2.41 engagement, dozens of DELETE-named duplicate combos' COCs were referenced by **over a
  thousand indicator expressions and hundreds of custom-form fields** (plus data elements,
  dataset-element overrides and dataElementOperands). Deleting a combo because its name says "DELETE"
  would silently break all of those. The safe path is a
  **reference-repointing merge**: pick a survivor per (dataDimensionType, sorted category-ids) group; map
  each source COC → survivor COC by identical option-set; repoint every combo-level ref
  (`dataelement`/`dataset`/`datasetelement`/`program`.categorycomboid) AND every COC-level ref
  (dataElementOperand FK, `indicator.numerator/denominator` text, `dataentryform.htmlcode` text,
  minmax, section greyedfields) to the survivor; then delete the emptied source (E4030 backstop). On
  2.42/2.41 there is no `categoryCombos/merge`, so this is SQL. **Expected side-effect:** consolidating
  duplicate combos' COCs makes formerly-distinct indicators share identical formula strings
  (`indicators_with_identical_formulas` rises) — advisory, keep + document.
- **On very large instances (100k+ OUs) the integrity inventory silently UNDER-reports.** The slow checks
  (coordinates, no-analysis, group-set, dedup) time out server-side and return empty, so a suspiciously
  low result (e.g. "2 nonzero checks" while a six-figure `orgunits_no_coordinates` count has vanished) is a partial run,
  not success. **Verify every fixed check with a direct SQL query** (authoritative + fast); don't trust a
  low inventory on a big instance.
- **Merging categories that share identical options creates duplicate category-COMBOS.** Any two combos
  that become identical after the category swap (e.g. a `Sex` combo and a `Sex (m/f)` combo both reduce to
  `[Sex]`) now violate `categories_unique_category_combo` (a SEVERE). **Always follow a category merge with
  a `categoryCombos/merge` cleanup pass.** Note: this check's **`details` endpoint silently returns 0**
  while `summary` shows the real count — derive the duplicate groups by a **direct scan** (fetch all
  combos, group by `(dataDimensionType, sorted categoryIds)`), pick the survivor with the most COCs, merge
  the rest. The merge POST can exceed the default HTTP read timeout on a loaded instance — **raise the
  client timeout (e.g. 300s); the server usually finishes even if the client times out** (re-scan to
  confirm rather than re-issuing blindly). **And the combo merge in turn leaves duplicate COCs inside the
  survivor combo** (two `Male` COCs in `Sex`, etc.) — so finish with a `categoryOptionCombos/merge` dedup
  pass (again by **direct scan**: group COCs by `(comboId, sorted optionIds)`; the summary under-reports
  these — it showed `1` when a direct scan found `10`). Order: categories → category-combos → COCs.
- **Deleting favorites (visualizations/maps) inflates the `*_no_analysis` counts** — fewer charts
  reference each indicator/DE, so more fall into "not in any analysis." Expected; don't mistake it for a
  regression. (And deleting not-viewed *dashboards first* cascades: favorites that lived only on them
  become orphan, so the deletable set balloons well past the dashboards-kept estimate — report the
  post-cascade magnitude before/while deleting.)
- **DELETE is slow on large instances** (~20-60s each, server-side cascade). Pre-filter deletability in
  the DB (count rows in every blocking FK table) so you only attempt deletes on genuinely-orphan objects,
  rather than firing hundreds of deletes and eating a ~20s `E4030` rejection on each referenced one.
