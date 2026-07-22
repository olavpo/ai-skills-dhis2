# UI test results: <app name> v<version>

Tested: <date> · Dev server: <URL/port> · Test data: <seed name / "synthetic metadata imported" / instance description>

## Instances

| Label | URL | DHIS2 version | Source |
|---|---|---|---|
| 2.42 | http://dhis2-agent-review-242:8080 | 2.42.x | broker, seed `<seed>` |

## Results

| Step | 2.40 | 2.41 | 2.42 | Notes |
|---|---|---|---|---|
| App loads | PASS | PASS | PASS | |
| <flow> | | | | |

<PASS/FAIL per cell; include counts where meaningful, e.g. "PASS (28 programs)". For FAIL, put the error in Notes or link a finding ID.>

## Version-specific failures

<Anything that failed on only some versions — the most important section. "None" if none.>

## Console/network hygiene

<Errors logged during otherwise-passing tests: console errors, 4xx/5xx responses, failed requests. A passing flow with 12 console errors gets listed here.>

## Screenshots

<Inline screenshots per step/version: `![2.42 step 3](/tmp/2.42-step-3.png)`>
