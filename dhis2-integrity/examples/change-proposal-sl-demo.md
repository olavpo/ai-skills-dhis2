# Change proposal — sl-demo

Source: `http://dhis2-agent-sl-43:8080` · DHIS2 **2.43.0.1** · control snapshot `data/control.pgdump` · decisions: `docs/decisions.md` · prepared 2026-07-19

Every change below was developed and verified on a copy of this system. Apply via the rendered playbook (stepwise, with preconditions and verification) or — for the importable subset — the metadata import package. Items under *Flagged for owner* are deliberate NON-changes that need a decision from the metadata owner.

## Proposed changes

### S1 — Merge duplicate category combo 'Morbidity Age' into 'Morbidity Cases' via the 2.43 merge endpoint, then dedupe the surviving combo's COCs
- **Integrity check:** `categories_unique_category_combo` (SEVERE)
- **Why:** Both combos use the same single category ('Morbidity Age'); one has no data elements. On 2.43 the categoryCombos/merge endpoint migrates dataset overrides, data values and COCs transactionally. Its own merge report warns duplicate COCs remain in the survivor — the second step clears those with categoryOptionCombos/merge.
- **Output risk:** analytics  ⚠️ can change analytics values
- **Reversible:** Control snapshot; the merge migrates rather than deletes data.
- **How:** 2 step(s): api POST /categoryCombos/merge, code
- **Decision record:** docs/decisions.md#S1

### S2 — Fix the 'Births' combo's wrong category (Gender -> Location PHU/Community) — resolves 12 disjoint COCs, moves NO data
- **Integrity check:** `category_option_combos_disjoint` (SEVERE)
- **Why:** The Births combo is defined as [Births attended by x Gender] but all 12 of its COCs actually use [Births attended by x Location PHU/Community] ('Trained TBA, In Community' etc.) — the combo references the wrong category while the data already sits under the correct COCs. No merge endpoint does a category swap and E1120 blocks the API edit while data exists, so this is the one legitimately-SQL structural fix even on 2.43.
- **Output risk:** none
- **Reversible:** Single-row UPDATE; swap back to the Gender category UID.
- **How:** 2 step(s): sql, api POST /maintenance
- **Decision record:** docs/decisions.md#S2

### S3 — Create an 'Other/unknown' group in each compulsory OU dimension (Facility Ownership, Facility Type, Location Rural/Urban); bucket unclassified OUs and move exclusive conflicts into it
- **Integrity check:** `org_units_not_in_compulsory_group_sets` (SEVERE)
- **Why:** The uncovered OUs are mostly admin-hierarchy levels with no facility classification; conflicted OUs carry contradictory memberships. An explicit Other/unknown bucket keeps each dimension complete and compulsory without fabricating classifications.
- **Output risk:** labels
- **Reversible:** Delete the 3 groups by their fixed UIDs; original memberships are in the control snapshot.
- **How:** 2 step(s): api POST /metadata, code
- **Decision record:** docs/decisions.md#S3

### W1 — Repair validation rules wrapping a single operand in AVG() — strip the unsupported wrapper
- **Integrity check:** `validation_rules_with_invalid_right_side_expression` (WARNING)
- **Why:** Validation-rule expressions do not support aggregation functions; AVG(x) over a single operand is a no-op wrapper, so stripping it is deterministic. Every candidate repair is validated server-side (GET /api/expressions/description) before writing; anything that does not validate is left and flagged.
- **Output risk:** analytics  ⚠️ can change analytics values
- **Reversible:** Re-wrap in AVG() (original expressions in the control snapshot).
- **How:** 1 step(s): code
- **Decision record:** docs/decisions.md#W-exprs

### U1 — Give users data-view access to their capture org units
- **Integrity check:** `users_capture_ou_not_in_data_view_ou` (SEVERE)
- **Why:** Users could enter data at org units they could not see in analytics; standard fix is data-view ⊇ capture.
- **Output risk:** none
- **Reversible:** Original dataViewOrganisationUnits are in the control snapshot.
- **How:** 1 step(s): code
- **Decision record:** docs/decisions.md#S9

## Flagged for owner — decisions needed, intentionally NOT auto-fixed

- **`data_elements_aggregate_with_different_period_types`** — 5 data elements live in datasets of different period types; which period is canonical is a reporting-design decision.
  - Owner action: choose the canonical period type per data element
- **`category_option_group_sets_incomplete`** — 3 group sets flagged 'incomplete' — same-dimension banding overlap (options shared across overlapping categorizations), not fixable per-item without corrupting the dimension.
  - Owner action: data-model review: stop sharing options across overlapping categorizations
- **`category_options_excess_groupset_membership`** — 'World Relief' donor option sits in BOTH the GIZ and USAID donor groups; neither is correct (it is a distinct donor).
  - Owner action: assign the option to the right donor group (or its own)
- **`data_elements_violating_exclusive_group_sets`** — 'Admission Date' is in two diagnosis groups; as a generic field it arguably belongs in neither.
  - Owner action: decide the correct (single or no) diagnosis group
- **`tracked_entity_attributes_invalid_trigram_search_configuration`** — 56 TEAs searchable-but-unindexed; which to index is a per-needs performance decision.
  - Owner action: curate the genuine search-key TEAs (names, IDs, phone) for indexing
- **`users_with_invalid_usernames`** — 1 invalid username; usernames are immutable via the API (E4056) and this is often a superuser.
  - Owner action: confirm the replacement name; rename requires DB access
