---
name: dhis2-app-review
description: >
  Standardised recipe for reviewing and testing DHIS2 web apps and tools: static code
  review, functional/UI testing with Playwright against live DHIS2 instances,
  multi-version compatibility checks, architecture assessment, and severity-ranked
  reporting. Use whenever the user asks to review, test, validate, QA, or assess a
  DHIS2 app or tool — phrases like "review this app", "test this tool on 2.42",
  "does this still work on the new version", "check this before release", or
  "should this be migrated to the App Platform" all apply, even if the word
  "review" is never used. Do NOT use for building new apps (create-dhis2-app /
  dhis2-apps) or for reviewing non-DHIS2 projects.
---

# Reviewing and testing a DHIS2 tool/app

A standardised workflow for reviewing DHIS2 web apps and tools: static code review, functional/UI testing against live DHIS2 instances, multi-version checks, and reporting.

## Capability check — run this first

Establish what the environment provides before planning anything:

```bash
echo "broker:     ${DHIS2_BROKER_URL:+available at }${DHIS2_BROKER_URL:-unavailable}"
echo "host port:  ${SANDBOX_HOST_PORT:-unavailable}"
which playwright || python3 -c "import playwright" 2>/dev/null || echo "playwright: missing"
ls ~/.claude/skills/ 2>/dev/null | grep ^dhis2- || echo "dhis2 skills: none found"
```

Branch on the results:

- **Broker vars set** (`DHIS2_BROKER_URL` + `DHIS2_BROKER_TOKEN`) → self-provision disposable test instances via the `dhis2-instances` skill (see [Provisioning](#provisioning-test-instances)).
- **Broker vars unset** → ask the user for one or more DHIS2 instance URLs with credentials and which of them (if any) are expendable. Skip provisioning. Treat every user-supplied instance as production-like: the [mutation safety rule](#mutation-safety) applies in full.
- **Playwright missing** and functional testing is in scope → install it (`pip install playwright && playwright install chromium`); if that fails, report the gap and fall back to API-level testing.
- **Sibling `dhis2-*` skills missing** → proceed with the generic guidance in this skill, and note in the report which skills were unavailable.

If a required capability is absent, say so and adapt — never probe for undeclared infrastructure or hunt for workarounds (e.g. around egress firewalls or read-only git tokens).

## Inputs

- `PROJECT_DIR` — path to the project under review.
- **Scope** — one of: `code review only`, `code review + functional test`, `functional test only`, `regression check on <feature>`, or `architecture assessment`. If unspecified, assume `code review + functional test` and say so in the report.
- **Target DHIS2 versions** — if unspecified, test the versions listed in the app's `README.md`/compatibility docs, or the latest stable if none are listed.

## Sibling skills to use

Invoke the skill rather than improvising:

| Capability | Skill | Use for |
|---|---|---|
| Disposable DHIS2 instances | `dhis2-instances` | Creating/resetting/deleting `agent-*` test instances, per version |
| DHIS2 docs lookup + ad-hoc API calls | `dhis2-docs` | Endpoint parameters, version-specific behavior, deprecations, verifying endpoint shapes |
| Bulk metadata work | `dhis2-metadata` | Seeding test metadata, diffing configs, import-error analysis |
| App Platform conventions | `dhis2-apps` | Reference standard for React/`@dhis2/app-runtime` apps |
| Vanilla tool-template conventions | `create-dhis2-app` | Reference standard for vanilla-JS tools (dhis2/tool-template) |
| Browser automation | `playwright-cli` / `webapp-testing` | UI testing, screenshots, console/network capture |
| Android testing | `dhis2-android-testing` | Only if scope includes the Android Capture app |

### Delegating to subagents

Use cheaper/faster subagents (a smaller model and/or lower reasoning effort, whatever the agent harness offers) for the mechanical, well-specified parts — they're also a way to parallelise: file/endpoint cataloguing, re-running the same Playwright suite per version, response-shape diffs across instances, broker job polling, cleanup verification. Keep judgment-heavy work in the main agent: deciding what is actually a finding and its severity, architecture assessment, anything that mutates data, and writing the final reports. Give subagents a precise task and output format, and verify — don't forward — their conclusions.

**Verify HIGH claims live before filing.** A static-review claim that predicts a feature *cannot work at all* while functional tests show it working is a contradiction to resolve in the framework/library source before it reaches the report — not a finding to forward. Reproduce the failure live, or trace the *complete* code path (framework layers included) end-to-end. Log the outcome of every such claim in the "Claims investigated and rejected" section of `REVIEW-FINDINGS.md` so the same false alarm doesn't resurface next review.

## Mental model: app shapes

DHIS2 web apps come in two shapes. Each has its own skill defining what "good" looks like — **judge an app against the conventions of its own shape**, not the other's. A vanilla tool is not wrong for lacking React; an App Platform app is not wrong for not being "simpler".

- **App-Platform apps** (React + `@dhis2/app-runtime`) — marker: `d2.config.js`. Served via `d2 app:scripts start`, which proxies and authenticates. Reference: `dhis2-apps`.
- **Vanilla / tool-template apps** — marker: `webpack.config.js` + `manifest.webapp`, no `d2.config.js`. Hand-rolled fetch wrapper, `webpack-dev-server` proxying to a configured instance. Current-template apps configure via `.env` and proxy all API calls same-origin (no CORS setup); older scaffolds use `d2auth.json` and need the instance's `corsWhitelist` extended. Reference: `create-dhis2-app`.

```bash
ls "$PROJECT_DIR/d2.config.js" 2>/dev/null && echo "App Platform" || echo "Vanilla/tool-template"
```

Older apps may predate both conventions — note divergence as findings, referenced against the nearest shape. Don't hard-code ports from a previous review: read the dev-server port from `webpack.config.js` (or `d2.config.js`) each time.

## Standard workflow

1. **Capability check** — as above.
2. **Read context** — `README.md`, `MANUAL.md`, `package.json`, `manifest.webapp`/`d2.config.js`. Identify the app's purpose, what it reads, what it mutates. List every mutating endpoint now — this drives instance choice and cleanup planning.
3. **Static review** — read every file under `src/`. Note the API surface; cross-check questionable endpoints against `dhis2-docs`. Run the project's own `lint`/`tsc`/test scripts and record the baseline before changing anything. Beyond bugs, always check for:
   - **Dead/unused code** — unexported functions never called, unreferenced files, commented-out blocks, dependencies in `package.json` nothing imports.
   - **Duplicated or near-duplicated logic** that could be harmonized into shared code or one level of abstraction — but *only* when the shared version is simpler than the copies. Simplicity is the goal: two loosely similar blocks don't justify a forced abstraction; three near-identical ones usually do. Frame it as a finding with the concrete shared shape, not a blanket "DRY this up".
   - **App icon** — the app declares an icon (`d2.config.js` or `manifest.webapp`) and the referenced file exists in the repo. A missing icon shows as a broken/generic tile in the app menu.
   - **Tests that assert request shape instead of server effect** — mocked unit tests that only check what was sent (URL, method, payload) pass even when the server ignores or rejects the write, and even when the tool misreads a legitimate success code (e.g. treats a `204` as failure). Flag suites where mutation tests never assert the resulting state; the fix is to re-GET and assert the effect (see `references/server-quirks.md`).
4. **Provision instances** — one per target version (or use the user-supplied URLs).
5. **Set up dev mode** — see [Setup checklist](#setup-checklist). Verify the dev server actually returns data before writing any test.
6. **Functional/UI tests** — read `references/playwright-patterns.md` first; run `scripts/probe.py` before writing real tests. Test documented happy paths first, then edge cases the code review suggested. If you need to seed test data or assert on the app's mutations, read `references/server-quirks.md` first — several DHIS2 write behaviours make a naive test wrong.
7. **Multi-version pass** — read `references/version-testing.md`; run the same suite against each instance and diff.
8. **Cleanup** — see [Cleanup discipline](#cleanup-discipline). Includes broker instances.
9. **Report** — see [Report structure](#report-structure); start from the skeletons in `assets/templates/`.

Check the project root for an optional `REVIEW-NOTES.md` — project-specific quirks, must-test flows, and version constraints the maintainer has recorded. It supplements (never overrides) the safety rules here.

## Provisioning test instances

With broker access, create instances via the `dhis2-instances` skill:

- Name them `agent-<purpose>` (e.g. `agent-review-whitespace-240`). One instance per DHIS2 version under test.
- Check `GET /seeds` first. **Prefer a seeded instance** (demo database) — most tools need existing metadata/data to exercise. If no suitable seed exists, create an empty instance and import test metadata with `dhis2-metadata`; note in the report that testing ran against synthetic rather than demo data.
- Instances are reachable on dev-net at `http://dhis2-<name>:8080` (usually `admin`/`district`).
- **At most 2 concurrent `agent-*` instances, and ideally only 1** — resource limits on the host make more than that unreliable. For multi-version testing, run one version at a time: finish (or at least stop) the current instance before starting the next. If you must have two running, don't *boot* them concurrently — two DHIS2 instances starting up at the same time starve each other and can take 20+ minutes with nothing on `/api`. Wait until the first answers on `/api/system/info` before creating the second. Delete instances when done, and list anything left running in the report.
- **Verify credentials before building on an instance.** Some seeds ship with `admin` disabled or a non-default password — a `GET /api/me` (Basic auth) up front catches this in seconds. DHIS2 caches user details, so a failed login *sticks* until the instance restarts: don't discover this in the middle of a test run. The reliable path is the **always-present `local_admin` / `district` superuser** the broker injects into every instance — prefer it over repairing a seed's `admin`. Fix mechanics (DB reset of the account) belong to the `dhis2-instances` skill.
- **Use `local_admin` for anything that grants roles or authorities.** A seed's `admin` can authenticate yet not be a true superuser: on the Sierra Leone demo, `admin` has ~248 authorities *without* `ALL`, so creating a user with a new role fails with `409 User 'admin' is not allowed to grant users access to user role '…'`. A successful login proves nothing about authority — check `GET /api/me?fields=authorities` for `ALL`, or just use `local_admin` for user/role fixtures from the start.

## Mutation safety

If the tool under review mutates data or metadata (most admin tools do — check for POST/PUT/PATCH/DELETE in the code *before* running anything), test destructive paths **only against disposable instances you created**, never against instances the user handed you, unless they explicitly say the instance is expendable.

## Setup checklist

Two shapes to choose from; pick by app type and testing goal.

### Vanilla / tool-template — dev-server proxy

Check the config style first: current-template apps have `.env.template`; older scaffolds have `d2auth.json`.

**Current template (`.env`-based):** all API calls go same-origin through the webpack proxy — no CORS setup needed, credentials never reach the browser.

```bash
# 0. Save any existing .env so you can restore it during cleanup
cp "$PROJECT_DIR/.env" /tmp/env.orig 2>/dev/null || true

# 1. Point at the target instance (DHIS2_DEV_PORT optional; default 8081)
cat > "$PROJECT_DIR/.env" <<EOF
DHIS2_BASE_URL=$DHIS2_URL
DHIS2_USERNAME=$DHIS2_USER
DHIS2_PASSWORD=$DHIS2_PASS
EOF

# 2. Start dev server in the background; wait for "compiled successfully" in the log
yarn start &> /tmp/dev.log &

# 3. Smoke check — does the app actually load data?  (scripts/probe.py)
```

**Legacy scaffolds (`d2auth.json`):** same flow, but write the instance URL + credentials to `d2auth.json` (save the original first), and the browser calls the API cross-origin — add the dev-server origin to the instance's CORS allowlist:

```bash
# POST replaces the whole list — read-modify-write, never POST just your origin
curl -sg -u "$DHIS2_USER:$DHIS2_PASS" "$DHIS2_URL/api/configuration/corsWhitelist" \
  | jq '. + ["http://localhost:<devServerPort>"] | unique' \
  | curl -sg -u "$DHIS2_USER:$DHIS2_PASS" -X POST -H "Content-Type: application/json" \
      -d @- "$DHIS2_URL/api/configuration/corsWhitelist"
# (the endpoint accepts POST; PUT returns 405)
```

If the dev server proxy is misconfigured, the app loads HTML but no API data. Always assert that *some* known data appears (program count, org-unit count, etc.) in your first probe before committing to a full test suite.

### App Platform — dev server (default), install the zip to verify

Run the app the way the user does: `d2 app:scripts start` (dev server with hot reload) is the default serving mechanism, for both development and manual testing — it proxies and authenticates, so no CORS setup. Installing the built zip is a **verification step**, not the serving mechanism: do it for reviews/releases to confirm the production bundle builds, installs, and loads — including the generated manifest and global-shell integration on 2.42+, which the dev server bypasses.

**Exception — multi-version test matrices**: when running the same suite against several instances, prefer installing the zip on each instance and driving the installed app. Zero per-version proxy/CORS setup, identical URLs across versions, and it exercises what users actually get. Keep the dev server for the iterative review-and-fix loop on one instance.

```bash
# Dev server (default serving mechanism)
yarn start   # or: d2-app-scripts start
# The internal port is fine for your own Playwright tests.

# For a server the *user* opens in their browser, bind BOTH the app and the
# API proxy to host-published ports (the shell's login form then needs the
# proxy URL as the server):
d2-app-scripts start --host 0.0.0.0 --port $SANDBOX_HOST_PORT \
    --proxy http://dhis2-agent-x:8080 --proxyPort $SANDBOX_HOST_PORT_2
# → tell the user: open http://localhost:$SANDBOX_HOST_PORT and enter
#   http://localhost:$SANDBOX_HOST_PORT_2 as the server URL.

# --- Verification pass (reviews/releases): does the production bundle work? ---
# 1. Build the production bundle
yarn build      # or: d2-app-scripts build       — produces build/bundle/<app>.zip

# 2. Install on the target instance — accept any 2xx (201 on 2.42+, 204 on ≤2.41)
curl -sg -u "$DHIS2_USER:$DHIS2_PASS" -F "file=@build/bundle/<app>.zip" \
  "$DHIS2_URL/api/apps"

# 3. Drive the installed app directly
#    URL: $DHIS2_URL/api/apps/<app-key>/index.html
#    On 2.42+ the instance wraps the app in a global-shell iframe even at
#    that URL — locate the app's frame via page.frames rather than assuming
#    the top document. See references/playwright-patterns.md.

# 4. Uninstall during cleanup (expect 204)
curl -sg -u "$DHIS2_USER:$DHIS2_PASS" -X DELETE "$DHIS2_URL/api/apps/<app-key>"
```

For Playwright against the dev server, don't inject cookies — script the dev shell's own "Please sign in" form with `--proxy` pointing at the instance (see `references/playwright-patterns.md`). Cookie injection only works when app and API origins are same-site; that trap and the forwarder workaround (still needed for legacy vanilla apps) are covered in the same reference.

## Architecture assessment and migration

When the scope includes `architecture assessment`, additionally answer: **is this app on the right architecture?** Load *both* app skills and evaluate:

- **Vanilla tool-template fits** when the app is a lightweight admin/maintenance utility: mostly ad-hoc metadata operations, a handful of screens, maintained by one or two people, no need for `@dhis2/ui` consistency, translations, or App Hub distribution.
- **App Platform fits** when the app needs the DHIS2 look-and-feel (`@dhis2/ui`), i18n, offline support, App Hub publication, long-term multi-developer maintenance, or non-trivial state — and when its API usage would benefit from `useDataQuery`/`useDataMutation` handling of auth, versioning, and errors.

Output: a short recommendation in `REVIEW-FINDINGS.md` — stay or migrate — with concrete costs (what must be rewritten, roughly how much code) and benefits, grounded in what the review actually found (e.g. recurring bugs in a hand-rolled fetch layer are an argument for `app-runtime`).

**Migration is a separate task, not part of the review.** If asked to migrate, treat it as a fresh implementation: scaffold with the target shape's skill, port features incrementally, and use the review's UI test suite as acceptance tests — same flows must pass on the same instances before and after.

## Cleanup discipline

Any test data you create must be deleted before reporting. Track every mutation:

```python
created_uids = []  # append every uid you POST
# ... at end of test, regardless of pass/fail:
for uid in created_uids:
    requests.delete(f"{base}/api/programRules/{uid}", auth=...)
```

Also:

- Revert system-setting changes (CORS allowlist edits, feature flags). If you can't safely revert, surface it in the report. (Skippable on broker instances you're about to delete.)
- If you installed an app via `POST /api/apps`, confirm uninstall returned 204.
- Restore config files you switched for testing (`d2auth.json`, `.env`) from the copy saved in setup step 0, or call out the change clearly in the report.
- **Broker instances**: delete the `agent-*` instances you created, or state explicitly which you left (name, version, why). Never leave an instance running silently.

## Repository layout for review artefacts

Write everything you produce into predictable folders so successive reviews don't sprawl:

```
PROJECT_DIR/
├── docs/
│   └── review-<YYYY-MM-DD>[-<slug>]/   ← this review's REVIEW-FINDINGS.md, UI-TEST-RESULTS.md, FIXES.md, screenshots
├── e2e/                                 ← Playwright suites, probes, fixtures, seed scripts
```

- **Reports**: create a dated folder per review (`docs/review-2026-07-09-whitespace/`). Screenshots and probes referenced from the report go alongside it, not in `/tmp`. Previous reviews stay put — dated folders let them coexist.
- **Tests**: everything browser/API-driving lives in `e2e/` — **not** `tests/` or `test/e2e/`, which get confused with the unit-test dir (`test/`, vitest/jest) that platform scaffolds already have. **This rule is for suites you create.** If the repo already has a working e2e suite elsewhere (e.g. `tests/e2e/` from an earlier review), extend it where it is — don't relocate it silently. If relocation is worth it, propose it as a LOW housekeeping finding, and include updating every README/docs reference to the old path.
- **The moment you create `e2e/`, extend `.gitignore`**: `__pycache__/`, the results/screenshot output dir, and any auth/state files. A `py_compile` run leaves `.pyc` files that a later broad `git add` sweeps into a commit unnoticed.
- **Keep worthwhile e2e suites in-repo**: when a suite is worth re-running (e.g. the acceptance suite for a migration — "same flows must pass before and after"), parameterize the instance URL (env var like `DHIS2_URL`, no hard-coded hosts) and offer to commit the suite. Ask the user before committing.
- **Publishable vs session-internal — decide per document, at creation time.** Findings with root-cause analysis, version-compatibility results, and user documentation are repo history: commit them. State-change/ops logs (`STATE-CHANGES.md` — broker instances, SQL run, package installs, host ports), skill-improvement notes, and most screenshots are session-internal: they document internal test infrastructure that doesn't belong in a public product repo, even when nothing in them is secret. Write session-internal docs *outside* the repo or under a gitignored path **from the start**, so a later broad `git add` can't publish them by accident — deliver them to the user separately.
- **Existing sprawl**: if the repo already has ad-hoc test scripts at the root, old `REVIEW-*.md` files, or screenshots strewn about, propose consolidating them into this layout as a small housekeeping finding (LOW severity) — don't silently rearrange the repo as part of the review itself.
- **Session-internal docs an earlier review already committed** (a `STATE-CHANGES.md` with broker instance names, test data, job ids, and similar ops logs): these shouldn't be in the repo, but removing committed history is the user's call, not yours. Flag the file as a LOW finding recommending removal; delete it only with the user's consent.

## Write for the scanner (SonarCloud)

dhis2-org repos run SonarCloud automatic analysis on every PR — **including any test tooling the review itself adds**, and the AI-code security rules are strict. Write generated code to pass from the first draft:

- **Don't write a script for a one-command task.** `pythonsecurity:S8703`/`S8707` flag *any* script that passes `sys.argv` values into `urllib`/`open()` as a MAJOR vulnerability. Installing an app zip is one documented curl — put the command in a README instead of writing `install_app.py`.
- **Hoist repeated literals to module constants** (`python:S1192` fires on any literal repeated ≥3 times — with Playwright that's always the `[data-test=…]` selectors).
- **Per-step functions, not one linear `main()`** (`python:S3776`, cognitive complexity ≤15 — a linear test script blows past it around 6–8 steps; per-step functions also localize failures better).
- If the repo legitimately keeps scripts that take URLs/paths from argv, add `.sonarcloud.properties` with `sonar.tests=e2e` so the security rules treat them as test code — or mark findings Accepted in the SonarCloud UI with a one-line justification.

To pull a PR's findings directly (no auth needed for public projects):

```
GET https://sonarcloud.io/api/issues/search?componentKeys=<projectKey>&pullRequest=<n>&issueStatuses=OPEN,CONFIRMED&ps=100
GET https://sonarcloud.io/api/issues/search?issues=<issueKey>&componentKeys=<projectKey>&pullRequest=<n>
```

## Report structure

Up to four documents per review, written to `docs/review-<YYYY-MM-DD>[-<slug>]/` (or wherever the user asks) — start from `assets/templates/`. Produce only the ones the scope calls for: `code review only` needs just `REVIEW-FINDINGS.md`; skip `UI-TEST-RESULTS.md` when no functional tests ran, `FIXES.md` unless the review was followed by fix work, and `STATE-CHANGES.md` when nothing persistent was touched (state that explicitly in the findings doc instead).

1. **`REVIEW-FINDINGS.md`** — severity-ranked code/UX/architecture issues, each with `file_path:line_number` references and a concrete fix suggestion.
2. **`UI-TEST-RESULTS.md`** — one row per flow tested, status per DHIS2 version if multi-instance. Embed screenshots. State which seed/data the tests ran against.
3. **`FIXES.md`** — when the user asks to fix findings: one entry per finding — the fix, where, and how it was verified.
4. **`STATE-CHANGES.md`** — every persistent change made (auth files, system settings, test data, broker instances created/deleted). Mark which were reverted and which remain. **Session-internal — keep it out of the repo** (see Repository layout).

Severity tiers:

- **HIGH**: bug, security issue, or architectural mistake that affects correctness, can lose data, or makes the app unusable in some configuration.
- **MEDIUM**: UX inconsistency, performance issue, missing cancel/abort path, code that works but is fragile.
- **LOW**: cosmetic, dead code, typos, missing `.gitignore` entries.

Don't pad the list. A 5-finding report with concrete fixes is more useful than a 30-finding report mixing real bugs with style nits.

## Scope discipline

- A bug fix is *not* an excuse to refactor the surrounding code. If asked to "review", do not also implement.
- If the user asks for testing only, write tests and report. Do not edit the app.
- If the user asks for a fix to a specific finding, fix only that one and ask before bundling other findings into the same change.
- Git tokens may be read-only: commit and branch locally, but expect `git push` to be rejected — the user pushes. Never work around this.

## What to do when blocked

- Empty dropdown / no data after dev-server start → check the browser console for CORS errors first, then `d2auth.json`, then whether the dev-server proxy is actually reachable.
- Instance provisioned but tool has nothing to operate on → the instance is empty (no seed). Import representative test metadata via `dhis2-metadata`, or report that a demo seed is needed.
- Login form fill not authenticating → skip the form: use Basic-auth `GET /api/me` and inject the returned session cookie (see `references/playwright-patterns.md`). Do **not** use `POST /api/auth/login` — it 302s to the legacy login page on 2.40 and silently yields an anonymous cookie.
- Cookie injected but app still shows the login screen → the app origin and the API origin aren't **same-site**. Common when testing an App Platform *dev server* (`localhost:<port>`) against a dev-net instance (`dhis2-agent-…:8080`) — the browser silently drops the cookie. For platform dev servers, skip cookies entirely and script the dev shell's own sign-in form; for vanilla apps, forward the API to `localhost` so both sides match. See `references/playwright-patterns.md`.
- A `fields=` query with brackets (`fields=categoryCombo[id,name]`) returns an empty body via curl → curl's URL globbing ate the brackets; the failure mode is empty output, not an error. Always use **`curl -g`** against DHIS2 APIs (urllib/requests are unaffected).
- A bundled app looks missing on 2.42+ → `/apps/<key>/index.html` 404s even when the app exists; `/apps/<key>` (no suffix) returns 200. Probe app existence via `GET /api/apps` (authoritative) or the suffix-less URL.
- App count doesn't match API count → the table may be client-side paginated (only the current page is in the DOM). Read the total from the `Pagination` footer, not from row count.
- Playwright launch fails with "Host system is missing dependencies" → `sudo $(which playwright) install-deps chromium`. Plain `apt-get install` won't work on Ubuntu 24.04 (`t64` package renames).
- Selector returns 0 results but element is visibly present → check `page.frames` first. **DHIS2 2.42+ serves installed apps inside a global-shell iframe**, while 2.41 and earlier serve them at top level — a suite that passes on 2.41 will time out on 2.42 if it queries the top document. Also possible: a wrapper widget (Choices.js, Materialize) hiding the real element — use `state="attached"` and locate the wrapper.
- A test that passed starts failing mid-session with `Connection refused` or a Playwright timeout → the instance may be restarting (Tomcat restarts take ~90 s and the broker still reports the instance as `running`). Re-probe `GET /api/system/info` and re-run before blaming the app.
- Validation appears to hang → check for a sequential per-item loop (common in legacy code) on a large instance. Wait on a deterministic DOM signal, not a fixed timeout.
- Dev server (`d2-app-scripts start`, `webpack-dev-server`) exits with `ENOENT … <file>.tmp.<pid>…` while you're editing source → the i18n / hot-reload watcher raced the editor's atomic temp-file rename. Harmless: finish the edits and restart the dev server.
- Verifying pinned GitHub Action SHAs in a workflow review, but the GitHub REST API rejects the sandbox token → no token needed: `git ls-remote --tags https://github.com/<org>/<repo> refs/tags/<tag> refs/tags/<tag>^{}` works unauthenticated and resolves annotated tags (the `^{}` row is the commit the SHA pin should match).
- A host you need is blocked by an egress firewall → report it; the user must allow it. Don't hunt for proxies.

## Bundled resources

- `references/playwright-patterns.md` — read before writing any UI test: auth without the login form, recon-then-act, event-stream capture, waiting on app state.
- `references/version-testing.md` — read before the multi-version pass: what drifts between DHIS2 versions and how to diff it.
- `references/server-quirks.md` — read before seeding test data or asserting on mutations: server behaviours (verify-after-write, refused operations, DB-level seeding, status codes) that make a naive test wrong.
- `scripts/probe.py` — one-shot smoke probe; run against any new app before writing real tests.
- `assets/templates/` — skeletons for the three report documents.
