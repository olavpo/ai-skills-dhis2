# Assisted manual review — the third track

The official metadata-assessment guidance
(docs.dhis2.org → *Metadata integrity and quality* → "Manual review") lists checks that "cannot be
automated": they need country context, stakeholder decisions, or semantic judgment. This track is how
the skill assists them **without crossing into deciding them**.

## The contract

> The review pack produces **evidence worksheets, never fixes.** Every sheet is a candidate list with
> context and an empty *decision* column. Decisions belong to the metadata owner (or to an interactive
> session with the user). Nothing in this track writes to the instance, and nothing from it may be
> promoted into the fix manifest without an explicit decision recorded in `decisions.md`.

Generate with `scripts/review_pack.py` (read-only API access suffices). Output: a `review-pack/`
directory of CSVs plus an `INDEX.md` explaining each sheet and how to review it.

## Mapping the docs' manual items

| Manual item (docs) | This track does | Ownership |
|---|---|---|
| Duplicate data sources (within a form, across programs, within a program) | **Sheet: `duplicate_data_sources.csv`** — candidate DE pairs/clusters by normalized-name similarity, with each DE's datasets/forms so reviewers see *where* the overlap is. Resolution usually means form redesign → stakeholder work. | assisted here |
| Category checks — do disaggregation totals make sense? | **Sheet: `category_totals.csv`** — per category: its options, plus heuristics that suggest a non-summing dimension (overlapping numeric/age ranges, options named like totals ("All", "Total"), options shared with other categories). | assisted here |
| Dashboard item configuration (relative periods/OUs, sharing) | **Sheet: `dashboard_items.csv`** — dashboard items whose visualization uses fixed periods or fixed OUs (breaks for other users/time), and dashboards with no public/group sharing. | assisted here |
| Program & dataset organisation unit assignment | **Sheet: `orgunit_assignment.csv`** — per dataset/program: OU count, level distribution, admin-level (1–2) assignments, and 0-or-everything anomalies. | assisted here |
| Program & dataset sharing | **Sheet: `sharing.csv`** — per dataset/program: public access string, #user-group accesses; flags "public r/w", "no access configured at all". | assisted here |
| Indicator formulas (right DEs in numerator/denominator, right type, sensible denominator) | **Sheet: `indicator_formula_candidates.csv`** — *flag-only*: name-says-% but factor≠100 (and vice versa), numerator==denominator, denominator literal `1` with rate-like name. **The semantic review itself belongs to the `dhis2-indicators` skill** — author/validate/prove expressions there. Hand the sheet over; do not duplicate expression semantics in this skill. | delegated |
| Naming conventions | Deterministic subset is already this skill's naming track (§7 of the playbook); the acronym-prefix / code-standardization governance exercise remains **out of scope** (see SKILL.md). | covered/excluded |

## How to run a review session with the pack

1. Generate the pack against the instance (or a sandbox restored from a dump). In a full engagement,
   generate it **during triage** so its findings can be promoted through the **same batched decisions
   round** as the integrity judgment calls (SKILL.md step 3) — one decision surface, one `decisions.md`;
   don't run the review as a separate second process with its own asks.
2. Walk one sheet at a time with the user/owner — the sheets are deliberately small enough to read
   (each capped, with the cap noted in INDEX.md so silent truncation can't masquerade as completeness).
3. Record outcomes per row in the decision column (keep / merge / redesign / investigate), then convert
   *decided* items into the normal pipeline: judgment-approved fixes go through `decisions.md` → the fix
   manifest; stakeholder items (form redesign, sharing policy) become owner actions in the change
   proposal's flagged list.
4. In an **interactive advisory session** (user asking "do we have duplicate data sources?"), the pack
   is the evidence base — generate just the relevant sheet and reason over it with the user; the LLM's
   value-add is semantic judgment on the candidates (true duplicate vs deliberate variant), which the
   heuristics deliberately do not attempt.

## Boundaries learned the hard way

- **Name similarity ≠ duplicate.** The indicator dedup evidence (across real engagements, only a small minority — e.g. 3 of 68 —
  of shared-formula groups were true duplicates) applies doubly to name-similar DEs — the sheet is a candidate list, nothing more.

### Cutting false positives in duplicate-candidate lists (proven heuristics)

Apply these structural exclusions *before* any semantic judgment of a candidate pair — they encode real DHIS2 domain knowledge and dramatically shrink the list:

- **Shared-membership exclusion.** Two objects that co-exist in the same parent container are intentionally distinct: dataElements sharing a dataSet (`dataSetElements[dataSet[id]]`), categoryOptions sharing a category (`categories[id]`), options in the same option set, org units under the same parent. Membership links are cheap to fetch and are structural ground truth about intended coexistence.
- **Numeric-variant exclusion.** If two names become identical after masking every digit (`ANC 1st visit` vs `ANC 2nd visit`, `Dose 1` vs `Dose 2`), they are sequence variants, not duplicates — skip the pair.
- **Never compare across domain types.** Only compare dataElements within `domainType:eq:AGGREGATE` (tracker DEs have different duplication semantics).

Two-phase fetching keeps this cheap: candidate discovery needs only `name,id` + membership links; hydrate full context (`formName`, `description`, `categoryCombo[categories[name]]`, `dataSetElements[dataSet[name,periodType]]`) via `filter=id:in:[…]` for the shortlist only — same name with a different category combo or period type is usually *not* a duplicate; different name with the same formName/description often *is*. Scale rule: on small instances (≲2–3k names per type) just feed the whole name+membership list to the model and ask for candidate sets directly; only 10k+ object types justify an embedding/nearest-neighbour pre-filter.
- **"Meaningful totals" is a design question.** The 1,248 `category_option_group_sets_incomplete` issues
  on the Laos dataset were all deliberate overlapping-banding design; the sheet surfaces the same
  patterns as *evidence for a data-model review*, not as defects.
- **Sharing and OU assignment need country policy.** A dataset assigned to every OU may be correct
  (national reporting) or a data-entry hazard — only the owner knows.
