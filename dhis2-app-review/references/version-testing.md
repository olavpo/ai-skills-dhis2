# Testing across DHIS2 versions

Most real bugs in DHIS2 apps come from version drift: an endpoint that worked in 2.40 changes shape in 2.42, a UI auth flow changes, a system setting key gets renamed (`keyCorsWhitelist` → `corsWhitelist` was a real example on 2.42.4).

## Setup

Create one broker instance per target version (`"version": "40"`, `"41"`, `"42"`, …) — prefer the same seed on each so results are comparable. Run the same Playwright suite against each and diff:

```
DHIS2_INSTANCES = [
    {"url": "http://dhis2-agent-review-240:8080", "user": "admin", "pass": "district", "label": "2.40"},
    {"url": "http://dhis2-agent-review-241:8080", "user": "admin", "pass": "district", "label": "2.41"},
    {"url": "http://dhis2-agent-review-242:8080", "user": "admin", "pass": "district", "label": "2.42"},
]

for inst in DHIS2_INSTANCES:
    # 1. Repoint d2auth.json (or env vars) at this instance
    # 2. Restart dev server
    # 3. Run the Playwright suite, tagging each result with inst["label"]
    # 4. Persist screenshots to /tmp/<label>-step-N.png
```

Run versions sequentially by default: stop (or delete) one instance before starting the next — see the "at most 2, ideally 1" cap in `SKILL.md`. Per-version suite runs are good candidates for cheap subagents.

## What to actively diff across versions

- `GET /api/system/info` → `version`. Confirm the app's version-dependent code paths (e.g. a `versionInfo.minor < 42` branch) trigger correctly on each.
- API field shapes. For each endpoint the app calls, hit it manually on each instance (conventions in the `dhis2-docs` skill) and confirm the response shape matches what the app expects.
- Auth differences: token vs session cookie endpoints, CORS allowlist API path, login form structure. Cookie name, login JSON shape, and CSRF requirements have all changed across the 2.39 → 2.42 line.
- UI shell differences: legacy header bar vs `@dhis2/header-bar` vs the modern app shell. **2.42 is the hard boundary**: from 2.42 the platform serves installed apps inside a global-shell iframe, while 2.41 and earlier serve `/api/apps/<key>/index.html` at top level. Write suites frame-aware from the start (scan `page.frames` for a known app selector) so one suite covers both.
- System-setting keys. Many were renamed; if the app reads or writes `systemSettings`, run `GET /api/systemSettings.json` on each version and verify the keys it expects exist.
- App install and write status codes drift too (`POST /api/apps` → `201` on 2.42+ vs `204` on ≤2.41; plain-JSON partial `PATCH` → `204` on ≤2.41 vs `415` on ≥2.42, where JSON Patch is required; silent no-op writes; refused operations). See `references/server-quirks.md` — accept any 2xx and verify after write rather than trusting a single status code. Any tool doing single-field metadata updates needs a run on both sides of the 2.42 boundary — a single-version test ships a tool broken on half the fleet.

## Results format

Keep a single results table, one column per instance, one row per test step:

| Step | 2.40 | 2.41 | 2.42 |
|---|---|---|---|
| App loads | PASS | PASS | PASS |
| Programs dropdown populated | PASS (28) | PASS (28) | PASS (28) |
| Validate single program | PASS | FAIL: 4xx on `/api/programRules/condition/description` | PASS |

Failures that occur on only one version are far more interesting than uniform passes — flag them prominently in the report.
