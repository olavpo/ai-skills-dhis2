# What analytics can reach

Use this to check, per candidate model, that every required indicator and breakdown is computable. Behaviour marked (2.42.6) was verified on that version in a household-enumeration PoC; verify anything else on the target version (a disposable instance and a `filter/description` validation call are cheap). Expression syntax itself belongs to `dhis2-indicators`.

## The reach rules

| A program indicator in program P can use | Yes / no |
|---|---|
| Data elements of P's stages | Yes. EVENT indicators: the event's own stage. ENROLLMENT indicators: values from P's events, by default the latest event of each stage in the boundaries |
| Tracked entity attributes | Yes, if the attribute is assigned to P (a program attribute). An attribute set in another program becomes readable in P only when it is also a program attribute of P (it can be hidden in P's form) (2.42.6) |
| Data of another program Q, including Q's enrollments or events | No. Not even "is enrolled in Q". Workaround: Q sets an attribute (rule ASSIGN) that P also carries |
| The parent's attributes across a relationship | No. Copy what is needed onto the member at creation |
| The number of relationships of a type | Yes: `d2:relationshipCount('<relationshipTypeUid>')` (the count only, no values from the other end), e.g. contacts per index case |
| Enrollment and event dates, org units, status | Yes (`V{enrollment_date}`, `V{event_date}`, `V{program_stage_id}`, `V{current_date}` evaluated at query time) |

Consequences to state in a design comparison:
- A **member as an event** in the parent's enrollment can be broken down by parent attributes (pattern 2 and the roster half of pattern 4).
- A **member as its own tracked entity** cannot, unless those attributes are copied onto it (pattern 3, 5, 6 and the follow-up half of 4).
- **Totals spread over two programs** (in-school children in one, out-of-school in another) are possible as an aggregate indicator adding two program indicators, but lose a single disaggregated total (2.42.6).

## Org unit of an indicator

`orgUnitField` decides which org unit a value is counted at: the enrollment org unit (default for ENROLLMENT), the event org unit (default for EVENT), the registration org unit (REGISTRATION), the owner at the start or end of the period (OWNER_AT_START / OWNER_AT_END), or a data element / attribute of value type ORG_UNIT.

- "By school where the child enrolled" needs an ORG_UNIT data element in the follow-up stage, used as `orgUnitField`.
- **OWNER_AT_END did not work on 2.42.6**: after 510 ownership transfers the ownership analytics table stayed empty and everything was counted at the registering org unit. Use an ORG_UNIT data element instead, or verify on the target version before relying on ownership.

## Breakdowns

- **Sex and age bands**: on 2.42+, one program indicator with a disaggregation category combo and `categoryMappings` on the program replaces one indicator per cell (verified on 2.42.6 and 2.43; saved ~60 indicators per design). Before 2.42, plan one program indicator per cell. A PI queried with a category it does not have returns an SQL error (E7145) instead of a message.
- **Multi-select fields** (MULTI_TEXT, e.g. "reasons, up to three"): countable per item with `containsItems(#{stage.de}, 'CODE')` in PIs since 2.41 (no `d2:` prefix; program rules use `d2:contains`). Keep one multi-select for entry; no per-option yes/no fields are needed.
- **"Latest state" measures** (current outcome of a follow-up): ENROLLMENT indicator reading the latest event value. **History measures** ("retained a term later", "visited at least once") need repeatable events, not edits of one event: edits overwrite.
- **Overdue work**: `V{current_date}` against due dates works in a PI, but working lists are usually the better tool for action (see `access-and-workflow.md`).

## Checklist per indicator

For each indicator in the brief, write down for each model: the program, EVENT or ENROLLMENT, the filter, the org unit field, the disaggregation, and "not reachable: <reason>" where it fails. Generate that "not supported" list in the metadata generator rather than by hand if you prototype: it becomes the comparison table's column and cannot drift.
