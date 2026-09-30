# Playwright patterns for DHIS2 apps

Hard-won patterns for UI-testing DHIS2 apps. Read this before writing any test — including an ad-hoc check written during a fix or development session, which is where most of these traps were re-learned.

## Three rules that apply to every script

1. **On 2.42+ every text assertion is frame-aware and every wait polls.** The global shell serves apps inside an iframe, so `page.inner_text("body")` reads the shell, not the app, and gives false FAILs. Either load the app with `?redirect=false` (next section), or gather text from every frame: `"\n".join(f.inner_text("body") for f in page.frames)`, wrapped in a try/except. This also applies to popups opened with `expect_page()`, which land in the shell too. Never use a fixed `wait_for_timeout` for routing or for a mutation. Poll for the condition instead (`for _ in range(40): …; if ok: break; page.wait_for_timeout(250)`). A fixed 7 s was enough on 2.43 and too short on 2.42.
2. **`@dhis2/ui` renders `data-test`, and Playwright's `get_by_test_id` looks for `data-testid`.** It returns 0 matches while the screenshot shows the element. Call `p.selectors.set_test_id_attribute("data-test")` once, or use `locator("[data-test='…']")`.
3. **Treat a failed POST/PUT as a failed flow.** Record responses with status ≥ 400 (see the event streams below). A rejected save often shows only a transient toast, so the flow looks as if it passed.

## Installed bundle: load it with `?redirect=false` (preferred for scripted checks)

Driving a dev server against a remote instance from the sandbox is fragile: the login cookie is cross-site, and some `d2-app-scripts` versions do not proxy `/api` (see the dev-server section below). For automated verification, install the built zip and drive it on the instance's own origin:

```python
# after: build → POST /api/apps with the zip (see SKILL.md "Verification pass")
cn, cv = get_session_cookie(base, user, password)          # helper below
ctx.add_cookies([{"name": cn, "value": cv, "url": base}])   # same origin as the app
page.goto(f"{base}/api/apps/<app-key>/index.html?redirect=false")
```

Without `?redirect=false`, 2.42+ answers with a `302` to `/apps/<key>`, and the app renders inside the global-shell iframe. Every locator then needs a frame hop, and a top-level heading wait times out after 60 s. With it, the app is the top document and no frame handling is needed. Only use the frame-aware patterns when you are deliberately testing the shell integration.

**Non-superuser test users need the app's `M_` authority.** Without it, the shell shows `Unable to find an app for this URL`, even though `GET /api/apps/<key>/index.html` returns 200. The authority name is generated from the app's short name. Dashes are stripped and spaces become underscores (`tool-job-status` → `M_tooljobstatus`), while the key turns spaces into dashes. So renaming `A B` to `A-B` keeps the key but changes the authority. A guessed name is accepted into the role and grants nothing. Read the real one from `GET /api/authorities?pageSize=2000`.

**Reset app-owned state first.** A suite that saves settings to the dataStore leaves the next run starting from different state, and every count shifts on rerun, which looks like a version regression. `DELETE /api/dataStore/<namespace>/<key>` before the suite starts makes runs repeatable.

## Authentication: skip the login form

DHIS2's React-based login form does not respond reliably to scripted form-fills. Authenticate via the API and inject the session cookie instead. **Use Basic-auth `GET /api/me` — not `POST /api/auth/login`:**

```python
import base64, urllib.request

def get_session_cookie(base_url, user, password):
    req = urllib.request.Request(
        f"{base_url}/api/me",
        headers={"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()},
    )
    with urllib.request.urlopen(req) as r:
        for c in r.headers.get_all("Set-Cookie") or []:
            head = c.split(";", 1)[0]
            n, _, v = head.partition("=")
            if "JSESSIONID" in n:
                return n.strip(), v.strip()
    raise RuntimeError("No JSESSIONID returned")

# Then in Playwright:
# ctx.add_cookies([{"name": cn, "value": cv, "domain": "localhost", "path": "/", ...}])
```

The cookie name varies per instance (e.g. `JSESSIONID`, `JSESSIONID_hmis_trk`) — match on the substring `JSESSIONID`.

**Why not `POST /api/auth/login`**: on 2.40 that endpoint doesn't exist and responds `302 → /dhis-web-commons/security/login.action`. urllib follows redirects by default, so the request ends with HTTP 200 — no `HTTPError` fires — and you silently capture the *anonymous* session cookie set by the login page. The test then renders the login screen instead of the app, surfacing only as a baffling selector timeout much later. Basic-auth `/api/me` yields an authenticated cookie on every version 2.40 → current.

## App Platform dev server: script the shell's own login form

Cookie injection is for *instance-served* apps. The App Platform **dev server** (`d2-app-scripts start`) shows the dev shell's own plain-HTML **"Please sign in" form** (Server / Username / Password) when it has no stored server URL — a valid cookie doesn't help, because the shell doesn't know which server to talk to. Unlike the instance's React login, this form **does** accept scripted fills:

```python
if page.get_by_text("Please sign in").count() > 0:
    inputs = page.locator("input")
    inputs.nth(0).fill("http://localhost:8080")   # the --proxy port, NOT the instance URL
    inputs.nth(1).fill(user)
    inputs.nth(2).fill(password)
    page.get_by_role("button", name="Sign in").click()
```

Start the dev server with `--proxy http://<instance>:8080` — the proxy serves the API on `localhost:8080`, which also sidesteps the cookie SameSite trap below. This is the low-friction path for platform apps; the port-forwarder below remains for vanilla/tool-template apps.

**Check that the proxy really proxies before scripting against it.** With `d2-app-scripts` 12.10.3 (vite), `--proxy` did not proxy `/api`: `curl http://localhost:<port>/api/me` returned `index.html` with HTTP 200, which looks like success. Without the proxy, the shell's login POST goes cross-origin, and its `SameSite=Lax` `JSESSIONID` is never sent back, so the shell sits on "Please sign in" indefinitely. If `/api/me` does not return JSON, use the installed bundle above.

## Common `@dhis2/ui` data-test roots

The grep pattern below derives any component's selectors, but these stable roots recur in every review:

| Component | data-test |
|---|---|
| DataTable / body / row | `dhis2-uicore-datatable`, `…-tablebody`, `…-datatablerow` |
| SingleSelect input / menu | `dhis2-uicore-select-input`, `dhis2-uicore-select-menu-menuwrapper` |
| Select options | `dhis2-uicore-singleselectoption`, `dhis2-uicore-multiselectoption` (no `role=option`: `@dhis2/ui` 10.x options carry no ARIA role) |
| Modal | `dhis2-uicore-modal` |
| AlertBar / dismiss | `dhis2-uicore-alertbar`, `dhis2-uicore-alertbar-dismiss` (a `div`, not a button) |
| Transfer | `dhis2-uicore-transfer` (sub: `…-filter`, `…-pickedoptions`, `…-actions-addindividual`) |
| Pagination | `dhis2-uiwidgets-pagination` — ⚠ *uiwidgets*, not *uicore* |

Two components follow **opposite `dataTest`-prop conventions**: `SingleSelectField` does *not* forward its `dataTest` prop to the inner select/clear elements (they keep the generic `dhis2-uicore-*` values above), while `Transfer` *derives* child test-ids from its prop (`<dataTest>-sourceoptions`, `-pickedoptions`, `-leftside`, …). Verify with the grep recipe below rather than assuming either convention.

## Recon-then-act, always

Don't write a 200-line script and run it once. Run `scripts/probe.py` first — it prints the DOM state, frame structure, console errors, and HTTP error responses. Adjust selectors from what you actually see, then write the full test. Common surprises:

- The app may run inside an iframe under the DHIS2 app shell. Check `page.frames`. **Find the app's frame by an element only the app has**, not by its title: on 2.43 the shell frame also shows the app title, so a title match returns the shell and every later click times out. Checks such as "double header bar" must count *visible* headers, because the app's own `<header>` is present but `display:none` inside the shell iframe.
- Prefer the app's own hash route (`#/programRules`) to clicking through navigation cards and overview pages.
- After clicking a `@dhis2/ui` select option, the portal backdrop (`#dhis2-portal-root div[class*="backdrop"]`) can linger and swallow the next click — wait for it to detach before the following interaction.
- Choices.js / similar wrappers hide the underlying `<select>`. Use `state="attached"` and locate the wrapper (`.choices__inner`, etc.).
- Materialize wraps checkboxes in a `<span>` overlay that blocks clicks. Use `.check(force=True)`.
- DHIS2 returns 200 with `{"status":"ERROR"}` for invalid expressions. HTTP-status checks miss this — inspect the response body.

## Always capture all four event streams

```python
page.on("console", lambda m: console.append((m.type, m.text)))
page.on("pageerror", lambda e: errors.append(str(e)))
page.on("requestfailed", lambda r: failed.append((r.url, r.failure)))
page.on("response", lambda r: http_errors.append((r.status, r.url)) if r.status >= 400 else None)
```

A test that "passes" but logged 12 console errors is not a passing test.

**Scope route mocks to the app's frame.** On 2.42+ the global shell calls `/api/apps` itself to locate the app. A `page.route` that fails `/api/apps` to exercise the app's error path blanks the whole shell instead. Match on `route.request.frame.url` containing `/<app-key>/`, and let the shell's requests through.

**Shell hygiene for suites.** `RC=$?` after a pipeline records the exit status of the last command (`python3 suite.py | tail; echo RC=$?` reports `tail`'s 0). Use `set -o pipefail` or `${PIPESTATUS[0]}`. Run background Python with `python3 -u`: with block buffering, stdout redirected to a log can stay empty even though the script exits 0. Launch Chromium with `--disable-dev-shm-usage` (the sandbox's `/dev/shm` is 64 MB), and close the browser as a script's last step, because subagents leave orphaned Chromium processes behind.

## Wait on app state, not on time

Around a mutation, poll for the server effect rather than sleeping. One step clicked submit, waited 2.5 s, then asked the API whether the object existed. The POST landed about 3 s later, so it reported a false FAIL. Because its cleanup sat inside the `if created:` branch, it also left the object behind on the instance. Put cleanup outside the success branch (see SKILL.md "Cleanup discipline").

Long-running validations / reports can take minutes. Don't hard-code `wait_for_timeout(30000)` — wait on a deterministic DOM signal:

```python
page.wait_for_function(
    "() => document.querySelector('.progress-container').style.display === 'none' "
    "&& !document.getElementById('runButton').disabled",
    timeout=240_000,
)
```

## Measuring layout stability (jank findings)

"The table jumps when I click X" style UX complaints are objectively testable. Capture `bounding_box()` of stable landmarks (column headers, the acted-on row) before and after the interaction and diff the coordinates:

```python
headers = root.locator("[data-test='my-table'] thead th")
before = [headers.nth(i).bounding_box()["x"] for i in range(headers.count())]
row.locator("button").click()
row.locator("[data-test='status-tag']").wait_for()
after = [headers.nth(i).bounding_box()["x"] for i in range(headers.count())]
shift = [round(a - b) for a, b in zip(after, before)]  # all zeros = stable
```

Common root cause in DHIS2 apps: an auto-layout `<table>` with an initially-empty column (e.g. a Status column that later receives a Tag) — the browser rebalances all column widths when content appears. Fix: give the column a fixed `width` on its header. Verify the fix with the same measurement (expect all-zero shifts).

## Derive `@dhis2/ui` selectors from `node_modules`

For apps built on `@dhis2/ui`, grep the *installed* package for the `data-test` roots and sub-element suffixes it emits — selectors are correct first time, no probe-adjust iterations:

```bash
# Root data-test values used by the component (e.g. a Transfer, SingleSelect, AlertBar):
grep -rhon 'dhis2-uicore-[a-z-]*' node_modules/.pnpm/@dhis2-ui+transfer*/**/build/cjs/*.js \
  | grep -o 'dhis2-uicore-[a-z-]*' | sort -u

# Sub-element suffixes appended off the root (e.g. …-transfer-filter, …-transfer-actions-addindividual):
grep -rhon 'dataTest}-[a-z-]*' node_modules/.pnpm/@dhis2-ui+transfer*/**/build/cjs/*.js \
  | grep -o 'dataTest}-[a-z-]*' | sort -u
```

Adjust the package glob to whichever `@dhis2-ui/*` component you're targeting (`@dhis2-ui+singleselect*`, `@dhis2-ui+alertbar*`, …). Some components (SingleSelect menus, Popovers, Modals) render through a portal / `Layer` but stay in the app's document — frame-scoped locators find them.

## Cookie SameSite: same *site*, not same *port*

Injecting the instance's `JSESSIONID` only works when the app origin and the API origin are **same-site** — same hostname; the port doesn't matter. Testing an App Platform **dev server** from inside the sandbox breaks this: the app is served at `localhost:<devPort>` but its API is the dev-net instance (`dhis2-agent-…:8080`), a different host — so the browser drops the cookie and you get the login screen with no error. (Installing the built zip sidesteps it: the app and its API then share the instance origin.)

Fix: forward the instance's API to `localhost:<port>` so both sides are `localhost`, mirroring the host user's setup. Simplest is `socat` (preinstalled in the standard agent-sandbox):

```bash
socat TCP-LISTEN:8080,fork,reuseaddr TCP:dhis2-agent-x:8080 &
```

Or, with no dependency at all, a tiny Python forwarder:

```python
import socket, threading

def _pipe(a, b):
    try:
        while (chunk := a.recv(65536)):
            b.sendall(chunk)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def forward(local_port, remote_host, remote_port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", local_port)); srv.listen()
    while True:
        client, _ = srv.accept()
        upstream = socket.create_connection((remote_host, remote_port))
        threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
        threading.Thread(target=_pipe, args=(upstream, client), daemon=True).start()

# threading.Thread(target=forward, args=(8080, "dhis2-agent-x", 8080), daemon=True).start()
# then point the dev server's API and your cookie domain at localhost:8080
```

## Testing a program rule firing in the Capture app

To verify client-side rule behaviour (warnings, errors, hide-field) end-to-end in the Capture app on a dev-net instance, three quirks stack up:

1. **Capture's App-Platform build refuses a non-`localhost` origin**. It shows "The application could not be loaded … privacy mode", because it needs a secure context. `--unsafely-treat-insecure-origin-as-secure` did not help in headless mode. Run a loopback forwarder to the instance (the `socat` or Python forwarder above, bound to `127.0.0.1`), drive `http://localhost:<port>` instead of `http://dhis2-agent-x:8080`, and set cookies for domain `localhost`.
2. **Locate the app inside `/dhis-web-capture/index.html` via `page.frames`** — on 2.42+ it's wrapped in the global-shell iframe like any other app.
3. **Mandatory fields render the label with a trailing `*`**, so exact-text lookups miss — use `get_by_text("First name", exact=False)` or match on the label prefix.
4. **Capture labels fields with the data element's `formName`**, while the maintenance apps show its `name`. A selector built from metadata `name` finds nothing in the form, so fetch `formName`/`displayFormName` too.

For scripting whole Capture entry flows (stable field containers, radios, multi-selects, AGE inputs, deep links, dialogs), see `dhis2-prototyping/references/web-flows.md`.

Server-side, the cheap pre-check is `POST /api/programRules/condition/description?programId=<uid>` — see `references/server-quirks.md`.

## Completion signals: read the alert, then wait for it to detach

`@dhis2/ui` surfaces success/error through an AlertBar (`[data-test='dhis2-uicore-alertbar']`). Read its text to assert the outcome, then wait for `state="detached"` (alerts auto-hide) before the next action — otherwise the *next* wait matches the still-visible stale alert and you assert on the wrong state.

Warning alerts do not auto-hide, and they persist across in-app tab switches, so one toast appears in every later screenshot. Dismiss them explicitly. The control is `[data-test='dhis2-uicore-alertbar-dismiss']`, a `div`: `alertbar button` or `get_by_role("button")` match nothing, and the loop concludes there are no toasts. Sweep every frame, since on 2.42+ the shell has its own (usually empty) alert stack and the app's alerts render in the app frame. Alerts can appear staggered, so retry a few times about 700 ms apart:

```python
def dismiss_toasts(page, rounds=4):
    for _ in range(rounds):
        hits = [b for f in page.frames for b in f.locator("[data-test='dhis2-uicore-alertbar-dismiss']").all()]
        if not hits:
            page.wait_for_timeout(700); continue
        for b in hits:
            try: b.click(timeout=2000)
            except Exception: pass
```

## Modals and selects

- **Escape closes the DHIS2 modal, not the select menu open inside it.** Pressing Escape to clear a leftover menu dismissed the whole modal, and the next `Save` click timed out with no hint why.
- **A MultiSelect menu stays open after a selection** (by design; a SingleSelect closes). Its backdrop swallows clicks elsewhere in the modal: `Save` retried for 30 s against `…backdrop… subtree intercepts pointer events`. Close it by clicking its own input again, or wait for the backdrop to detach.
- **Scope select locators to their section**, e.g. `get_by_text("<section heading>").locator("xpath=ancestor::div[1]")`, rather than `.first`. `.first` broke as soon as a new select rendered earlier in the modal.

## Screenshots

Screenshots of the older Struts-era apps need `page.screenshot(path=…, animations="disabled", timeout=60000)`. Without it, their spinners never let the page settle, and the screenshot times out at 30 s.

## Counting: use the Pagination footer, not DOM rows

Client-side-paginated tables hold only the current page in the DOM, so counting `<tr>` gives the page size, not the total — a false "app count ≠ API count" failure. Read the total from the `Pagination` footer text ("… items 1-10 of 36") instead.

The empty state is a real row: `DataTable` renders "No items found" as a `<tr>` in `tbody`. Counting rows after deleting the last item gives 1, which looks like "row not removed". Filter that row out.

## Sort-order oracles

Python's `sorted(key=str.lower)` is not an oracle for a JS app's `a.localeCompare(b, undefined, {sensitivity: 'base'})`. Names containing punctuation and digits sort differently under the two comparators (14 of 118 category names disagreed on one seed). Build the expected order with the same comparator (`node -e …`) before filing a sort bug.

## Locators can't mix CSS and text engines in one selector

Playwright rejects a single comma selector that mixes engines (`.foo, text=Bar`). Use `.or_()` instead:

```python
page.locator("[data-test='status-tag']").or_(page.get_by_text("No results"))
```
