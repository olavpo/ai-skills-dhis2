---
name: dhis2-prototyping
description: "Run a comparative or pilot DHIS2 proof of concept end to end: build several design options side by side on a disposable instance, load synthetic data with known totals, verify every indicator against them, script and record data entry on Capture Android and web, estimate entry effort, and publish a report plus a shareable package (metadata, database dump, testers' guide). Use whenever the user wants to prototype or pilot-test a DHIS2 configuration, compare tracker or aggregate design options with evidence, produce entry-workflow videos or effort estimates, or hand colleagues a test instance to try the options — phrases like 'build a PoC', 'set up all options so we can compare', 'record the Android workflow', 'how long would entry take', 'make a test instance we can share'. For choosing the design itself use dhis2-tracker-design; a one-off dummy-data load for a single program is dhis2-metadata. This skill runs the process and delegates mechanics to other skills."
---

# DHIS2 prototyping

A prototype settles design questions with evidence: an indicator that cannot be built, a Capture screen sequence that doubles entry time, a registry step that does or does not need re-typing. Most of that evidence is found by trying, not by reading, so the goal is to get every option working side by side quickly, then measure.

A full comparison of six tracker designs with recorded flows took about 20 agent hours. Android automation was 9 of them. The order of work below is what would have made it faster.

## Order of work

1. **Plan from the brief.** List the options, the indicators, the actors, the scenario households/cases, and **every screen type and widget the flows will touch** (registration, auto-generated stage, repeatable stage, linked entity, each render type, org unit, coordinates, completion dialogs). Decide the scenario-data strategy now (step 7).
2. **Environment on day one** (`references/setup.md`): tools, port forwards, emulator permissions and mock GPS, Android settings on the server, a labelled broker instance (`dhis2-instances`), and **clocks and time zones** on device and server. Without the broker or the host's emulator (outside the agent sandbox), use any instance you can reset and dump, and a local emulator or device; the rest of the process is the same.
3. **Metadata generator** with deterministic UIDs, one module per option, all options on one instance with code prefixes (`references/generator.md`, `scripts/d2gen.py`). Import, then validate every expression through the API in bulk (`scripts/validate_package.py`).
4. **Synthetic population** loaded into every option, with ground truth computed in Python; verify every indicator against it and run analytics early (`references/synthetic-data.md`). This paid for itself many times: it caught metadata bugs, verification bugs, and later proved a refactor changed nothing.
5. **Dashboards** with explicit visualization fields and program-stage working lists.
6. **Manual pass through the Android UI** for every screen type, writing down selectors and quirks, before generating any flow. Most Android failures were surprises one manual pass would have found.
7. **Flow generators** (Android Maestro YAML, web Playwright) with captions and counted actions; scenario names and phone numbers per platform; a cleanup script (`references/android-flows.md`, `references/web-flows.md`).
8. **Dry-run all flows for all scenario households on both platforms**, and read each entry back from the server. Household 1 passing proves little about households 2 and 3.
9. **Final passes**: cleanup → web and Android. The emulator is a serial resource, so start the long Android pass as early as possible and do web, data and report work in parallel. Sync only after a successful Android flow.
10. **Media** (`references/recording-and-media.md`): renders, GIFs, stills, dashboard screenshots after the last analytics run.
11. **Effort table from the logs** (`references/effort.md`), report from data, check every number in the prose, publish (`references/reporting-and-sharing.md`).
12. **Scale check** on a temporary second instance, then delete it.
13. **Hand-over package**: importable metadata + loader, a tested database dump, a testers' guide, findings turned into bug reports.

## Rules that saved or cost the most time

- **Generate, never hand-edit** metadata JSON; deterministic UIDs make every re-import an update and let scripts refer to objects without lookups. Metadata import never deletes: remove what the generator no longer emits through the API.
- **Validate before concluding.** Before deciding the platform "can't" do something (a function, a render type), validate the expression or try it on the instance. A missed `containsItems()` cost 204 workaround rules.
- **Count from what ran**, never from the plan: effort, and "synced", are only true when the logs and the server say so.
- **The server is the source of truth**: after each flow, read the data back through the API. The first full Android pass reported success and synced nothing.
- **Keep the shared instance clean**: scenario entries under their own names; distorting tests (scale) on a temporary instance.
- **Media out of git** from the first commit; publish a size-limited subset.

## Delegation

| Need | Skill |
|---|---|
| Choosing and arguing the design | `dhis2-tracker-design` |
| Instances, analytics runs, dumps | `dhis2-instances` |
| Expression syntax, indicator testing | `dhis2-indicators` |
| Bulk import/export, dummy data, import errors | `dhis2-metadata` |
| Basic adb driving, installing APKs | `dhis2-android-testing` (Maestro flows, `screenrecord` and emulator preparation for recordings are owned here) |
| A custom Android app as one of the options | `dhis2-android-sdk-app` |
| Findings turned into platform bug reports | `dhis2-bug-report` |
| Web app testing patterns | `dhis2-app-review` (`references/playwright-patterns.md`) |
| Report as an artifact, diagrams | artifact skills; `references/reporting-and-sharing.md` for what worked |
| Testers' deck | `org-templates` + `pptx` |

## Bundled scripts

| Script | Use |
|---|---|
| `scripts/d2gen.py` | `Gen(prefix)`: deterministic `g.uid(key)`, an object accumulator (`add`, `get`, `write`), builders for option sets, data elements and attributes with a project prefix in codes. Import it from the project's generator |
| `scripts/validate_package.py` | Validates every program rule condition and action expression, program indicator expression and filter, indicator formula and category-mapping filter in a set of metadata JSON files against a live instance |
| `scripts/make_standalone.py` | Turns a published report (HTML + media folders) into one self-contained file with re-encoded media, for readers without artifact access |
| `scripts/d2http.py` | Shared session from `D2_URL`/`D2_AUTH`/`D2_TOKEN` (no default credentials), non-JSON guard, import error-report readers |
| `scripts/import_package.py` | Imports metadata files in order (`atomicMode=ALL`), prints error reports, stops at the first failure; on E4047 imports without rules first, then in full |
| `scripts/load_tracker.py` | Batched `/api/tracker` loader for a generated population (tracked entities, relationships, ownership transfers), error reports, `reserve_values()`, SQL for enrollment geometry (E1074 workaround) |
| `scripts/verify_indicators.py` | Compares analytics with a ground-truth `truth.json` (values and category disaggregations, within a date window); prints mismatches, exits 1 on any |
| `scripts/check_entry.py` | Reads back the tracked entities, enrollments, events and relationships matching an attribute value, shown with codes |
| `scripts/cleanup_scenario.py` | Deletes scenario/test entries listed in a match file; dry run unless `--delete` |
| `scripts/effort.py` | Keystroke-level interface-time estimate from Maestro and web run logs (or planned counts); writes `effort.json` |
| `scripts/svg_diagram.py` | Inline-SVG building blocks for report diagrams (lanes, boxes, arrows) that follow the page's light/dark theme |
| `scripts/android/maestro_flow.py` | `Flow` builder for Capture Android flows (captions, ensure-visible, widgets) counting taps, characters, scrolls and screens; one YAML, or one per phase |
| `scripts/android/ui.py` | `uiautomator dump` as readable nodes; find, tap and wait by regex, with retries |
| `scripts/android/login.py` | Clear app state, relaunch until the login screen shows, run the per-version Maestro login flow, wait until ready |
| `scripts/android/record.py` | One `screenrecord` per phase, clips padded to wall-clock length, ASS captions, counter and title burned in, optional one-minute cut, run log with caption times |
| `scripts/web/runner.py` | Playwright driver for web Capture: login, iframe, field and widget helpers, failed-write capture, timed log with counts, `run_flow` |
| `scripts/web/example_flow.py` | Example search → register → save flow to copy per option |
| `scripts/web/render.py` | Burns captions and a click counter into a recorded web flow, optional one-minute cut |
| `scripts/web/shot_dashboard.py` | Dashboard screenshot with a tall viewport and lazy-load scrolling |
| `scripts/media/gifs.py` | GIF cut at a caption's time from a run log, or at `--at` |
| `scripts/media/grid.py` | Side-by-side comparison video at one speed, holding last frames, with labels |

Dependencies: `pip install requests pillow imageio-ffmpeg playwright` in a venv (none is in the sandbox image except Playwright); Maestro and a JRE as in `references/setup.md`. Every script prints usage with `--help`. The project's own generator and population (`scripts/<project>/`, built on `d2gen.py`) and the report builder are patterns described in the references, not bundled.

## References

| File | Read when |
|---|---|
| `references/setup.md` | Setting up tools, forwards, emulator, Android settings, instances |
| `references/generator.md` | Writing the metadata generator; platform behaviours that break generated metadata |
| `references/synthetic-data.md` | Population, ground truth, verification, scenario data, loader throughput |
| `references/android-flows.md` | Maestro flows on Capture Android |
| `references/web-flows.md` | Playwright flows on web Capture |
| `references/recording-and-media.md` | Videos with captions, one-minute versions, GIFs, screenshots |
| `references/effort.md` | Turning flow logs into effort estimates |
| `references/reporting-and-sharing.md` | Report, artifact limits, single-file export, dump, testers' guide |
