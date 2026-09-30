---
name: dhis2-tracker-design
description: "Design and review DHIS2 tracker data models: tracked entity types, program vs stage vs relationship, repeatable stages, registries shared across programs, registration org unit and ownership, and whether the indicators users need are reachable in analytics. Use whenever someone asks how to model a use case in DHIS2 tracker, compares design options, reviews a tracker configuration, asks whether something should be an event or a tracked entity, plans a household survey, enumeration, case surveillance, contact tracing, follow-up or registry, or weighs Capture against a custom app, PWA or Android SDK app. Trigger even without the word 'design': 'should each child be its own record?', 'can we get X by Y from this program?', 'how do schools/partners see these cases?', 'will this duplicate the registry?' all apply. Not for writing an expression (dhis2-indicators), bulk metadata import/export (dhis2-metadata), running a full prototype (dhis2-prototyping) or building the app (dhis2-android-sdk-app)."
---

# DHIS2 tracker design

Tracker design decisions are expensive to reverse: tracked entities cannot change type, data does not move between programs, and analytics can only reach what the model puts within reach. Tools are replaceable; the data model is not. This skill is for getting the model right first, and for saying clearly what each option costs.

## Principles

1. **Model first, tool second.** Rank designs on analytics, follow-up, access, registry fit and privacy. Treat data-entry time as a property of the tool (Capture Android, Capture web, a custom app), and say explicitly when an option's only weakness is entry cost, because a better tool can remove it.
2. **The unit you follow over time is a tracked entity.** If something needs visits, ownership, transfer, sharing with another actor, or a history, it needs its own record. Things you only count once can be events or counts.
3. **A snapshot and a living record are different things.** Holding a person both as a row in a snapshot (what the enumerator found on a date) and as a tracked entity (the case being followed) is not duplication if they answer different questions. Duplication is when two records claim to be the current truth.
4. **Design backwards from the indicators.** Write down every indicator and breakdown the users need, then check for each candidate model that analytics can reach it (see `references/analytics-reach.md`). Most design regrets are an indicator discovered to be unreachable after go-live.
5. **Hold the least personal data that does the job.** Every searchable tracked entity is a record someone can find. Prefer events or counts for people who need no follow-up.
6. **Think about who works the data.** Registration org unit, ownership, capture and search scope decide who sees a record. Design them with the real actors (enumerator, school, partner, volunteer, supervisor), not the admin user.
7. **Registries are shared, not copied.** If a registry exists or is planned (learners, children for immunisation, patients), use its tracked entity type so one person is one record across programs.

## Workflow

1. **Gather the brief.** Unit(s) of observation, who enters, who follows up, what is counted, what is followed, the indicators with their breakdowns, connectivity, scale, existing registries, and which DHIS2 and Capture versions. If the brief is thin, list your assumptions rather than stalling; ask only about what would change the model.
2. **Sketch two to four candidate models** from `references/patterns.md` (single entity with stages; members as a repeatable stage; every member as a linked tracked entity; roster events + tracked entity for the followed subset; counts + tracked entity for the followed subset; members only; one person in many programs). Always include the simplest model that could work and the "cleanest" one, so the trade-off is visible. Name them (A, B, C…) so the user can refer to them.
3. **Check each candidate** against:
   - analytics reach for every required indicator (`references/analytics-reach.md`);
   - access, ownership and follow-up workflow (`references/access-and-workflow.md`);
   - registry fit and duplication risk (`references/registries.md`);
   - entry cost per tool, and what a custom app would change (`references/entry-and-tools.md`).
4. **Recommend one**, with the reason in one sentence, the cost you accept, and what would change your mind (e.g. "if the registry is built in the same instance and must be seeded for every child, B becomes the contender").
5. **Offer to prove it.** Claims about what Capture or analytics does on a given version are worth a prototype on a disposable instance: generate the metadata, load synthetic data with known totals, and check every indicator against them. `dhis2-prototyping` runs that end to end (generator, synthetic data, scripted entry flows, effort estimates, report); individual expressions go to `dhis2-indicators`. To review an existing configuration, export the program first with `dhis2-metadata`. If the recommendation is a custom Android app, `dhis2-android-sdk-app` builds it.

## Output: a design comparison

Lead with the recommendation, then the comparison. A compact shape that has worked:

```markdown
**Recommendation: C** — <one sentence why>. Cost accepted: <cost>. Would change if: <condition>.

| | A | B | C |
|---|---|---|---|
| Structure | <TETs, programs, stages, relationships in a line> | … | … |
| Indicators not reachable | <list or "none"> | … | … |
| Follow-up, sharing, transfer | … | … | … |
| Registry fit / duplication | … | … | … |
| Child/person records held | <who gets a searchable record> | … | … |
| Entry cost (Capture Android / web) | <relative, or measured> | … | … |
| What a custom app would change | … | … | … |

Open questions: <only those whose answer changes the recommendation>
```

When the design will be shown to others, draw it: one diagram of the recommended structure (tracked entity types → programs → stages, relationships as labelled arrows, which indicators come from which program) and one of the workflow (lanes per actor, time left to right). A side-by-side of the options showing only what differs is more useful than one diagram per option.

## Traps that invalidate a design late

Check these before recommending; each has cost a real project time.

- **Program indicators cannot count or read another program's data.** They can read tracked entity attributes, so a flag set by program X becomes visible to program Y only if the attribute is also assigned to program Y. Relationships do not carry values into analytics; the only thing a program indicator can take from them is a count (`d2:relationshipCount`).
- **Program rules cannot create tracked entities or copy values into another program.** A "roster row becomes a case" step is double entry in Capture; only a custom app or a backend job removes it.
- **A linked record cannot see its parent's attributes in analytics.** Household characteristics are invisible from a child's program indicators. If breakdowns by parent characteristics are needed, copy them onto the child at creation (a dated snapshot) or keep the child as an event in the parent's enrollment.
- **Value types and option sets are fixed once data exists.** DHIS2 (verified on 2.41.10) lets you change a data element's value type or option set while tracker data exists, and the old values then fail validation on the next edit or import (E1125). Settle them before collection starts.
- **Tracked entity type is fixed.** Moving records to another type later is a data migration. Agree the type (and its identifying attributes) with any registry team before data collection starts.
- **A field that is hidden and rule-assigned** loses its value in web Capture and the server then rejects the save (E1309 on 2.42). Show assigned fields read-only.
- **Version matters.** Several behaviours differ between Capture Android, Capture web and DHIS2 versions (render types, working lists, ownership analytics; enrollment coordinates in script-built `/api/tracker` payloads were rejected with E1074 on 2.42.6 while Capture and the Android SDK saved them, so test any custom writer's geometry). `references/entry-and-tools.md` lists what was verified where; verify anything else on the target version before promising it.

## References

| File | Read when |
|---|---|
| `references/patterns.md` | Choosing candidate models; each pattern with when it fits, analytics, entry cost, pitfalls |
| `references/analytics-reach.md` | Checking whether an indicator or breakdown is reachable in a model |
| `references/access-and-workflow.md` | Registration org unit, ownership, search scope, working lists, scheduling, multiple follow-up actors |
| `references/registries.md` | A registry exists, is planned, or is incomplete; linking, seeding, duplicates, IDs |
| `references/entry-and-tools.md` | Entry cost in Capture Android/web, settings that matter, custom app / PWA / SDK choice |
| `references/example-household-enumeration.md` | A worked comparison of six designs with measured results (household enumeration of out-of-school children) |
