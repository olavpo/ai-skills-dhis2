# Registries: linking, seeding and duplicates

A registry (learners, children for immunisation, patients) is a program whose tracked entities other programs should reuse. The design question is always: will this program's records become the registry's records, duplicate them, or stay separate?

## Linking to a registry in the same DHIS2 instance

- **Use the registry's tracked entity type** in the program that finds or follows the person. Then the person is one record: the service (a school, a clinic) opens the existing tracked entity and enrols it in the registry program, with name, date of birth and sex already filled in. Verified in a household-enumeration PoC (mock learner registry, web Capture 2.42.6): 13 clicks and 20 characters, no re-typing; in the design without a person record the school registered the child from scratch (a new, unlinked record).
- **Make registry status visible to the other program's analytics** with an attribute: a rule in the registry program assigns "Enrolled in registry = yes", and the attribute is also a (hidden) program attribute of the follow-up program. Program indicators cannot count enrollments in another program; they can read that attribute. Do not hide the attribute in the registry program itself, where it is assigned (E1309 on web).
- **Search before register** is what prevents duplicates. Starting from the program's own working list and switching program avoids the search altogether; registering from the registry's own screen needs a search by name and date of birth first.

## When the registry is incomplete or does not exist yet

Common: outreach (enumeration, screening) often reaches people before the registry does.

- **Nothing in identification or follow-up should depend on the registry.** Enrolment and retention come from the follow-up visits; the registry step is optional in the workflow.
- **Counts of people already served** (children in school) cannot be taken from an incomplete registry; count them in the outreach (a roster or counts).
- **The outreach record can seed the registry**, if it uses the registry's tracked entity type: when the registry arrives, the service enrols the existing record instead of registering again. This only works in the same instance on the same type, so **agree the type and its identifying attributes with the registry team before data collection starts.** Changing a tracked entity's type later is a migration.
- **Registry roll-out is when duplicates happen**: a school registering all its learners will register outreach-found children again unless it searches first. Give services a working list of "enrolled, not yet in the registry" and start registry registration with a search.
- **Consider issuing the registry ID at identification** (the ID attribute belongs to the type, so any program can generate it), so the person carries it from the first contact. Who issues IDs is the registry owner's decision. If the ID's text pattern includes `ORG_UNIT_CODE(...)`, it takes one character per dot (three dots → the first three characters of the code), and sequences restart per pattern value; the Android SDK reserves values for every coded org unit in the user's capture scope at once (no single-org-unit download), so a wide capture scope reserves many values and org units without a code get none.
- **Registering everyone the outreach meets** (pattern 3) could seed the registry for all, but those records rest on what a parent says at the door, not on the service's own rolls, and hold far more personal data. Prefer letting services seed the registry from their rolls, unless the registry owner explicitly wants outreach seeding.

## When the registry is a separate system

The same-record route does not apply. The link is an exchanged ID and an integration (import job, API). Confirm early which case applies: it changes whether the tracked entity type matters and what the follow-up program must store (the external ID, a sync status).
