# Tracker modelling patterns

Each pattern: structure, when it fits, what analytics can and cannot do, entry cost, pitfalls. "Parent" and "member" stand for any one-to-many pair: household–child, mother–baby, index case–contact, school–learner, facility–patient.

Contents
1. Single entity, stages
2. Members as a repeatable stage of the parent (roster as events)
3. Parent and every member as linked tracked entities
4. Roster as events + tracked entity for the members who need follow-up (hybrid)
5. Counts in the parent + tracked entity for the members who need follow-up
6. Members only, with the parent's details copied onto each
7. One person, many programs (shared type, registry pattern)
8. Choosing between stages, programs and enrollments

---

## 1. Single entity, stages

One tracked entity type, one program, stages for the steps of a case (notification, investigation, lab request, result, final classification). Stages may be repeatable (lab requests, follow-up visits).

- **Fits**: case-based surveillance, a patient's episode of care, one person followed through a process. WHO's VPD and generic case-surveillance packages are built this way.
- **Analytics**: everything is inside one enrollment, so enrollment indicators can combine any stage's values with the attributes. The easy case.
- **Pitfalls**: repeatable stages multiply EVENT counts; per-person measures must be ENROLLMENT indicators. A result that arrives in another system (lab) needs an integration, not a stage the lab never opens.

## 2. Members as a repeatable stage of the parent (roster as events)

Parent tracked entity (household) with a repeatable stage, one event per member (child). Follow-up, if any, is edits to the member's event or more events referring to it by line number.

- **Fits**: members are only counted and described, not followed; the person who lists them is the person who acts on them; a paper roster is being digitised.
- **Analytics**: every member row sits in the parent's enrollment, so members can be broken down by parent characteristics (female-headed household, distance, nomadic). Counts, rates and disaggregations are straightforward EVENT indicators with a stage filter.
- **Entry cost**: lowest of the individual-level designs; one form per member.
- **Pitfalls**: no member record to share, transfer, schedule or enrol in a registry; the member cannot be searched on its own; follow-up by editing overwrites the previous state, so there is no visit history and "still attending a term later" rests on a last-check date. Access is all-or-nothing on the parent: sharing a member with a school means sharing the whole household.

## 3. Parent and every member as linked tracked entities

Parent tracked entity plus one tracked entity per member, joined by a relationship type (parent → member). Each member has its own program (identification, follow-up).

- **Fits**: every member is followed individually over time (all children in a cohort; every contact of an index case); members move independently of the parent.
- **Analytics**: member indicators see only the member's own program and attributes. **Parent characteristics are not reachable** from the member's indicators unless copied onto the member. Relationship-based counts are limited; plan on "no joins across the relationship".
- **Entry cost**: highest. In Capture Android each member goes through the relationship "create new" sequence (search across all programs of the type, pick program, org unit); web is one form. Measured in a household-enumeration PoC: this design was the slowest on Android.
- **Pitfalls**: a searchable record for every member, including those who need nothing; if a registry exists, members registered here may duplicate it (see `registries.md`).

## 4. Roster as events + tracked entity for the members who need follow-up (hybrid)

Pattern 2 for everyone, plus a tracked entity in a follow-up program for the subset that needs follow-up (out-of-school children, contacts with symptoms), linked to the parent.

- **Fits**: everyone must be counted and described, but only some are followed. Most enumeration and screening use cases.
- **Analytics**: identification indicators come from the roster (complete, next to the parent characteristics); follow-up indicators from the follow-up program. Nothing is unreachable, except follow-up indicators by parent characteristics (copy a few onto the enrollment at creation if needed).
- **Entry cost**: the followed members are entered twice in Capture (roster row + registration), because program rules cannot create tracked entities. About 45 s per followed member on Android in a household-enumeration PoC. A custom app removes this completely: it creates the tracked entity from the roster row.
- **Why the double record is fine**: the roster event is the dated snapshot ("what was found at the door"), the tracked entity is the living follow-up record. Link them both ways (store the roster event's UID on the tracked entity and vice versa) when a custom app or import writes them, so every followed row can be checked for its record.
- **Recommended** in the worked example (`example-household-enumeration.md`), and the model to keep if a custom app is built later.

## 5. Counts in the parent + tracked entity for the members who need follow-up

The parent's visit records counts (members, and members with property X, by age band and sex); only the followed subset is entered individually.

- **Fits**: individual detail for the non-followed majority has no use and privacy is a concern; the counting categories are fixed and simple.
- **Analytics**: aggregate-style totals only for the counted part: no per-member attributes (birth certificate per child), only the bands on the form, and the counts cannot be checked against the individual records.
- **Entry cost**: fastest; tallying members into cells at the door is error-prone (add rules: "in school ≤ total").
- **Pitfalls**: the bands are frozen in the form; a later need for a different age split cannot be met from past data.

## 6. Members only, with the parent's details copied onto each

No parent record; each followed member is registered with the parent's details as attributes.

- **Fits**: only the followed subset matters and there is no denominator to compute (lists for action, not rates).
- **Analytics**: no denominator, so no rates; parent details are repeated per sibling and can disagree.
- **Pitfalls**: siblings repeat the household; no record of households visited with no one to follow.

## 7. One person, many programs (shared type, registry pattern)

One tracked entity type (Person, Learner, Child) shared by several programs: a registry program plus service or follow-up programs. A person is found once and enrolled in each program that concerns them.

- **Fits**: any setting with a registry or a person followed by more than one service.
- **Analytics**: each program's indicators see only their own program; cross-program facts travel as attributes (see `analytics-reach.md`).
- **Details**: `registries.md`.

## 8. Choosing between stages, programs and enrollments

- **Stage vs program**: the same actors, the same time frame and the same org unit → a stage. Different actors or sharing, a different lifecycle (a follow-up that starts only for some), or a person who is shared with a registry → a separate program on the same type.
- **One enrollment vs re-enrollment**: recurring episodes (a new pregnancy, a new outbreak exposure) are new enrollments in the same program if the program allows more than one. A continuous relationship (a learner through school) is one enrollment.
- **Event vs tracked entity for a member**: follow-up, search, transfer, sharing or a registry link → tracked entity. Counted and described once → event.
- **Codes vs names**: put a project or programme prefix in codes, not names. Names show in Capture lists, headers and recordings; category option names show in charts.
- **Attribute vs data element**: identifying and stable facts (name, date of birth, sex, IDs) are attributes; facts observed on a date (status, reasons, outcome) are data elements in a stage, so their history is kept.
