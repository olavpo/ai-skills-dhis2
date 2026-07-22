# Review findings: <app name> v<version>

Reviewed: <date> · Scope: <scope> · Reviewer: agent (<model/harness>)
DHIS2 versions tested: <list, or "n/a — code review only">

## Summary

<2–4 sentences: overall state of the app, the most important finding, whether it's safe to use/release.>

## Findings

### HIGH

#### H1. <one-line title>

- **Where**: `src/file.js:123`
- **What**: <the defect and the scenario in which it bites>
- **Fix**: <concrete suggestion>

### MEDIUM

#### M1. <one-line title>

- **Where**: `src/file.js:45`
- **What**: …
- **Fix**: …

### LOW

#### L1. <one-line title> — `src/file.js:7` — <short description and fix>

## Claims investigated and rejected

<Static-review or subagent claims that looked like HIGH findings but were disproved on closer inspection. One entry per rejected claim: where the claim came from, what it predicted, and the evidence that refuted it (e.g. framework layer that changes the behavior, live test result). Keeps the same false alarm from resurfacing next review. Omit this section if there were none.>

- **Claim**: <what was reported>
- **Source**: <static grep / subagent finding / issue #…>
- **Refuted by**: <live test result / framework code path traced end-to-end at `path:line`>

## Architecture assessment

<Only if in scope. Recommendation: stay on <shape> / migrate to <shape>. Costs, benefits, grounded in findings above. Otherwise delete this section.>

## Environment gaps

<Anything that limited the review: missing seeds, blocked hosts, unavailable skills, versions not tested. "None" if none.>
