# Access, ownership and workflow

Who can see, search and act on a record is decided by the model as much as by user setup. Design it with the real actors and their scopes, and test it with a user per role, never with the superuser.

## Registration, enrollment and owner org units

Three org units are easy to conflate:

- **Registration org unit**: where the tracked entity was first created. Stored on the tracked entity; never changes.
- **Enrollment org unit**: where the enrollment in a given program was made. The default org unit of ENROLLMENT program indicators.
- **Owner org unit**: per (tracked entity, program), initially the enrollment org unit; ownership transfer changes it. It decides whose capture scope the record falls in for that program.

At first registration all three are the same, so the choice below is really "where do records start?"

| Option | Consequence |
|---|---|
| The nearest service point (school, health facility) | The service point's staff get a working list of their catchment; ownership can be transferred when the person moves to another service point. Needs a way to pick the nearest point at registration (an org unit picker, or a rule-free list) |
| An administrative area (district, sub-district) | Simple for enumerators; service points see nothing without a wider search scope; the actual service point becomes an ORG_UNIT data element |

Communities and enumeration areas are rarely org units; put them in attributes, not in the hierarchy.

## Scopes

- **Capture scope** (data capture org units): where a user can register and edit.
- **Search scope** (tracked entity search org units): where a user can find records, e.g. a partner officer who searches the whole region but captures in one area.
- **Data view scope**: what the user's analytics show.

Write a table of roles × the three scopes as part of the design; it is also the test plan.

## Ownership and transfer

- Each (tracked entity, program) has an owner org unit, initially the registering one. Transfer moves the record into another org unit's scope (`PUT /api/tracker/ownership/transfer?trackedEntity=&program=&orgUnit=`, or the Capture UI), e.g. when a child enrols at a school outside the catchment.
- Program access level decides what users outside the owner's capture scope can do with a record found in their search scope:
  - **OPEN**: access within search scope, no justification asked.
  - **AUDITED**: the same, but the access is logged.
  - **PROTECTED**: the user must "break the glass" (give a reason, logged) to get temporary access.
  - **CLOSED**: no access outside the owner's capture scope; transfer is the only route.
- Analytics by owner was unreliable on 2.42.6 (see `analytics-reach.md`); if "counted where the person is now" matters, record the current service point in a data element as well.

## Several actors following up the same people

Schools, implementing partners and volunteers visiting the same children worked best as **one follow-up stage** with a "visited by" data element, not a program or stage per actor: indicators then count visits and outcomes whoever made them, and a child can pass between actors. Split into separate stages or programs only when actors need different forms or must not see each other's data.

## Working lists and scheduling

- **Program stage working lists** (`programStageWorkingLists`, e.g. follow-up visits OVERDUE / SCHEDULE) work in web Capture 2.42 and Android 3.4.2. Tracked-entity working lists with an event-status criterion were ignored by web Capture 2.42.6 (it listed every active tracked entity).
- A follow-up stage can be **auto-generated with a minimum days offset** (first visit due 30 days after identification) and **offer the next event on completion** with a standard interval (e.g. 90 days). On Android, "schedule next event" schedules without opening it; on web the new event opens on the Schedule tab.
- A retention check ("still attending a term later") is just another scheduled follow-up event whose outcome is recorded; model history as events, not as edits of a "last check" field.

## Time zones

Server-side "future date" checks use the server's time zone. A server on UTC rejects records entered on phones in UTC+1/+2 just after local midnight ("Enrollment date … cannot be a future date") until the server's date catches up. Set the server's time zone to the country's before field use and before any device testing.
