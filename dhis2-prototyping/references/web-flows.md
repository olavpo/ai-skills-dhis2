# Web automation with Playwright

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026).

Bundled in `scripts/web/` (Playwright; `pip install playwright` and `playwright install chromium` if missing):

| Script | Use |
|---|---|
| `runner.py` | `Web` driver: login, Capture iframe, stable field locators, widget helpers, dialog answers, failed-write capture, timed action log with click/character counts; `run_flow(name, fn, out_dir, record=True)` writes `<name>.json` (+ `.webm`) and fails the flow on any POST/PUT ≥ 400 |
| `example_flow.py` | A search → register → fill → save flow showing the calls; copy it per design option |
| `render.py` | Burns captions (`w.cap`) and a running click/typing counter into the video as ASS subtitles; `--short` adds a ~1-minute uniform speed-up |
| `shot_dashboard.py` | Dashboard screenshot: tall viewport, scroll to load lazy items, then shoot |

Keep a flow as plain Python functions per scenario step (search, household form, roster, linked
entities) so options share code, and drive them from a small loop over options × scenario cases.

General Playwright-on-DHIS2 patterns (the global-shell iframe on 2.42+, the localhost origin
Capture needs, session cookies across origins) are in `dhis2-app-review/references/playwright-patterns.md`;
read it first. What follows is specific to scripted Capture entry flows.

- Launch Chromium with `--disable-dev-shm-usage`: the sandbox's 64 MB `/dev/shm` crashes long runs.
  Close the browser at the end of every script; orphaned Chromium processes pile up.
- Log in by `GET /api/me` with Basic auth on the browser context; then open
  `/dhis-web-capture/index.html#…` through the localhost forward (`D2_WEB`, default
  `http://localhost:8089`; `socat TCP-LISTEN:8089,bind=127.0.0.1,fork,reuseaddr TCP:<dhis2-host>:8080`).
- **Capture runs in an iframe** (global shell): find the frame whose URL contains `dhis-web-capture`
  and query inside it (see `playwright-patterns.md`).
- Useful deep links: `#/search?programId=&orgUnitId=`, `#/new?…`, `#/enrollment?enrollmentId=&…`.
- Find fields by text, return a **stable** locator: containers are `[data-test="form-field"]` (with an
  inner `data-test="form-field-<uid>"`) and `[data-test^="dataentry-field-"]`. Index-based locators
  broke when rules re-rendered the form.
- Widgets: radios — click the input by label with `force=True` (the input overlays the label);
  single selects — `[role=combobox]` then `[role=option][aria-label="…"]` in Capture's own selects
  (`@dhis2/ui` SingleSelect options have no role: `[data-test="dhis2-uicore-singleselectoption"]`;
  `runner.select` tries both); multi-select —
  `[data-test="dhis2-uicore-select-input"]`, options `dhis2-uicore-multiselectoption`, Escape to close;
  AGE — inputs 0 (date), 1 (years); coordinates — inputs with class `_latitudeTextInput…`;
  org unit — click the trigger, type a few letters, click the node.
- Dates: read the current value first and type only if different (some forms prefill, some don't).
- Focus inputs with `.focus()` rather than clicking: date pickers pop up and cover neighbours.
- **Record failed API calls** (`page.on("response")`, status ≥ 400 on POST/PUT) and treat them as flow
  failures. A save that fails shows only a transient toast; the first supervisor flow "passed" while
  the enrolment was rejected.
- Dialogs to expect: "Generate new event — create another event?", "Discard unsaved changes?",
  duplicate warnings. New events completed from the Complete button may not ask.
- Web flows take about a minute per household; debug them while the Android pass runs.
