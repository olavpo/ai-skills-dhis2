---
name: dhis2-bug-report
description: Reproduce a suspected DHIS2 bug on the play instances, check Jira for an existing issue, isolate it, capture screenshots, record versions, and write the Jira ticket content (Steps to Reproduce / Actual Result / Expected Result). Use this whenever the user reports something in DHIS2 behaving wrongly and wants it tested, confirmed, narrowed down, or written up — including phrasings like "make a Jira ticket", "file a bug", "help me reproduce this", "has this been reported already", "is this a known issue", "does this also happen in 2.42", "is this a regression", "check this on play", "capture screenshots of this", or just a description of broken behaviour with a play URL attached. Also use it when the user asks to check which version of an app or of DHIS2 core an instance is running, or whether an installed app is the latest on the App Hub.
---

# DHIS2 bug reproduction and reporting

Turn a suspected bug into a ticket a developer can act on without asking follow-up questions. That means: an exact recipe on a public instance, the observed numbers, the correct numbers, one isolating test, and the versions.

The default failure mode is a ticket that is long, hedged, and vague. The second failure mode is a ticket that reports the user's description back to them without testing whether it is actually true. Both waste the developer's time.

## Workflow

### 1. Restate the bug as a testable claim

Before touching an instance, write down in one line what should happen and what the user says happens. If the user's description is ambiguous about which cells, which items, or which layout, pick the most likely reading and say which one you picked. Test that.

Keep the user's original wording somewhere. At the end, compare: anything in their description you could not reproduce is a finding worth reporting, not something to quietly drop.

### 2. Search Jira for an existing issue

Do this before spending effort on reproduction. Someone may have filed it already, or filed it and had it fixed in a release newer than the one the user is on, in which case the useful answer is a key and a version rather than a new ticket.

Search again at the end, once you know the real trigger and have better keywords than the user's original wording gave you.

See `references/jira-search.md` for the tracker layout, search strategy, and what to do with a hit.

### 3. Test the backend before the UI

Hit the API first with the same data items and periods. This is cheap and it splits the problem in half: if the API returns correct values, the bug is in the app; if not, it is in core. Say which one in the ticket — it decides who picks up the issue.

See `references/instance-testing.md` for the analytics calls that matter, including how to read aggregation types and numerator/denominator fields out of a response.

### 4. Build the exact configuration, don't click it together

For anything with a saved-object model (visualizations, event reports, maps, dashboards), create the configuration through the API as a temporary favourite rather than driving the UI. Reasons: the config is exact, it is identical across the versions you test, and you can open the same object on three instances without repeating fifteen clicks.

Name test objects with a prefix like `ZZTEST` so they are easy to find and delete. Delete them at the end of the session, in the same turn where you report results, so cleanup is not left to a later message that may never come. Play instances reset regularly, but leaving debris in a shared demo is rude.

The steps you write in the ticket are the manual UI equivalent, because a QA person will follow those, not your API calls.

`references/instance-testing.md` has the payload shapes and the silent-failure traps.

### 5. Isolate with one variant

A ticket that says "totals are NaN" is weak. A ticket that says "in the same table, the indicator subtotal computes and the completeness subtotal is NaN" points at the code path.

Change exactly one thing and re-run. Good variants: swap the suspect item for a comparable item of a different type; remove one dimension; use a single item instead of three; try the same config with the option turned off. One or two variants is usually enough. Stop when you can name what distinguishes working from broken.

### 6. Record versions

Get the core version, the app version, and whether the app version is current. All three matter: a bug in the latest app version needs fixing, a bug in an old one may already be fixed.

`references/instance-testing.md` covers where each version lives and how to query the App Hub.

Test at least two versions when the user gives you more than one instance, or when "is this a regression" is a live question. Identical behaviour across versions is itself a useful finding.

### 7. Screenshot

One screenshot per distinct claim. Three or four is plenty; ten is noise. Crop to the relevant table or panel and keep the layout controls visible if they explain the configuration.

`references/screenshots.md` has the setup and the two scripts, `scripts/shoot.py` and `scripts/crop.py`.

### 8. Write the ticket

Use the template and the length limits in `references/ticket-template.md`. Write it to a markdown file so the user can paste it into Jira.

### 9. Report back briefly

The ticket file is the deliverable. In chat, add only what is not in the file: what surprised you, what you could not reproduce, and any question you need answered. Do not summarise the ticket back to the user — they can read it.

## Length discipline

This is the part most likely to go wrong, so treat it as a constraint rather than a preference.

The ticket earns its length from information a developer needs. Version table, numbered steps, observed numbers, expected numbers, one isolating comparison, screenshots. Everything else is subtraction.

Specifically:

- One numbered step per action, imperative, under twenty words, no explanation mixed in.
- Actual Result: what you saw, with the numbers. Two to four sentences before you get to the variants.
- Expected Result: the correct values, computed. Not a description of correctness.
- Isolating variants: one short paragraph each, no headings.
- A cause hypothesis is welcome if it is two or three sentences and labelled as a hypothesis. Do not speculate at length about code you have not read.
- Findings and hypotheses stay visibly separate. "The API returns X" and "this is probably because Y" are different kinds of statement.
- Do not repeat a finding in the ticket, the chat message, and a summary table. Say it once.

If the draft feels thorough, it is probably too long. Cut the parts that make you look diligent and keep the parts that let someone else fix the bug.

## Scope

Written for DHIS2 against the play instances, but the workflow holds for any web application with an API: verify the backend, build config programmatically, isolate with a variant, record versions, screenshot, write short. When testing a user's own instance rather than a public demo, ask before creating or deleting anything.

## Reference files

- `references/jira-search.md` — the DHIS2 tracker, how to search it for duplicates, what to do with a hit
- `references/instance-testing.md` — play instances, credentials, version and App Hub lookups, analytics calls, saved-object payload shapes, silent-failure traps
- `references/ticket-template.md` — the Jira template, per-section length limits, a worked example
- `references/screenshots.md` — headless browser setup, authenticated screenshots, cropping

## Scripts

- `scripts/shoot.py` — log in to an instance and screenshot an app path or saved visualization
- `scripts/crop.py` — trim background and upscale a screenshot for attachment
