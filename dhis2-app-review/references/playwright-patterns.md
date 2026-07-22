# Playwright patterns for DHIS2 apps

Hard-won patterns for UI-testing DHIS2 apps. Read this before writing any test.

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

## Common `@dhis2/ui` data-test roots

The grep pattern below derives any component's selectors, but these stable roots recur in every review:

| Component | data-test |
|---|---|
| DataTable / body / row | `dhis2-uicore-datatable`, `…-tablebody`, `…-datatablerow` |
| SingleSelect input / menu | `dhis2-uicore-select-input`, `dhis2-uicore-select-menu-menuwrapper` |
| Modal | `dhis2-uicore-modal` |
| AlertBar | `dhis2-uicore-alertbar` |
| Transfer | `dhis2-uicore-transfer` (sub: `…-filter`, `…-pickedoptions`, `…-actions-addindividual`) |
| Pagination | `dhis2-uiwidgets-pagination` — ⚠ *uiwidgets*, not *uicore* |

## Recon-then-act, always

Don't write a 200-line script and run it once. Run `scripts/probe.py` first — it prints the DOM state, frame structure, console errors, and HTTP error responses. Adjust selectors from what you actually see, then write the full test. Common surprises:

- The app may run inside an iframe under the DHIS2 app shell. Check `page.frames`.
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

## Wait on app state, not on time

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

1. **Capture's App-Platform build refuses a non-`localhost` origin** ("application could not be loaded"). Run an in-process loopback tunnel to the instance (the port-forwarder pattern below) and drive `http://localhost:8080` instead of `http://dhis2-agent-x:8080`.
2. **Locate the app inside `/dhis-web-capture/index.html` via `page.frames`** — on 2.42+ it's wrapped in the global-shell iframe like any other app.
3. **Mandatory fields render the label with a trailing `*`**, so exact-text lookups miss — use `get_by_text("First name", exact=False)` or match on the label prefix.

Server-side, the cheap pre-check is `POST /api/programRules/condition/description?programId=<uid>` — see `references/server-quirks.md`.

## Completion signals: read the alert, then wait for it to detach

`@dhis2/ui` surfaces success/error through an AlertBar (`[data-test='dhis2-uicore-alertbar']`). Read its text to assert the outcome, then wait for `state="detached"` (alerts auto-hide) before the next action — otherwise the *next* wait matches the still-visible stale alert and you assert on the wrong state.

## Counting: use the Pagination footer, not DOM rows

Client-side-paginated tables hold only the current page in the DOM, so counting `<tr>` gives the page size, not the total — a false "app count ≠ API count" failure. Read the total from the `Pagination` footer text ("… items 1-10 of 36") instead.

## Locators can't mix CSS and text engines in one selector

Playwright rejects a single comma selector that mixes engines (`.foo, text=Bar`). Use `.or_()` instead:

```python
page.locator("[data-test='status-tag']").or_(page.get_by_text("No results"))
```
