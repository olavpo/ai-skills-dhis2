# Android automation with Maestro

> From a household enumeration PoC that compared six tracker designs (DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42, September 2026). Scripts named here are bundled under `scripts/` (see "Bundled scripts" in SKILL.md); `scripts/<project>/` stands for the project's own generator package, which you write.

### Bundled tools

| Script | Use |
|---|---|
| `scripts/android/maestro_flow.py` | `Flow` builder: captions (`cap()`), taps, typing, the ensure-visible pattern, radio/tile/bottom-sheet/multi-select widgets, save-and-complete; counts taps, characters, scrolls and screens as it goes. `write()` gives one YAML for dry runs, `write_phases()` one YAML per caption for recording. `--example DIR` writes a small sample |
| `scripts/android/ui.py` | `uiautomator dump` as a readable list (text, content-desc, resource-id, bounds), plus `find`, `tap` and `wait` by regex; retries empty dumps. Importable |
| `scripts/android/login.py` | Clean state for a pass: optional `pm clear` + permission grants, relaunch until the login screen really shows, run your per-version Maestro login flow with `${SERVER}`/`${USER}`/`${PASSWORD}`, wait for the first screen after the metadata download |
| `scripts/android/record.py` | Runs a phased flow with one screen recording per phase and renders the captioned video (see `recording-and-media.md`) |

Write one generator module per project on top of `Flow` (a function per step: open program,
register, visit, add member…), driven by the scenario data, so every design option and every
scenario household is generated the same way. Write the counts to a JSON next to the flows; the
effort table is computed from them. A final pass is then a loop:

```bash
python3 scripts/android/login.py --flow flows/login.yaml --server http://10.0.2.2:<port> --user <enumerator> --clear
for f in flows/android/*/; do
  python3 scripts/android/record.py "$f" --out report --title "$(basename $f) · Capture 3.4.2" --short || break
  # sync, then read the entry back from the server before the next flow
done
```

The login flow is per app version (the login screen differs between releases): build it from
`ui.py` output and keep one file per version tested.

### Setup and running

- Maestro 2.10 with the 5037 forward works against the host's emulator; each `maestro test` has
  15–20 s JVM start-up, so write whole flows, not one command per call. For exploration, short
  throw-away flows plus `uiautomator dump` are fine.
- **`uiautomator dump` is the best pair of eyes** (works even when screenshots are blocked):
  `scripts/android/ui.py` prints text, content-desc, id and bounds of every node.
- `--debug-output <dir>` writes `commands.json` with a timestamp, duration and status per command,
  under a **hidden** `.maestro` directory (`glob(..., include_hidden=True)`). Labels on commands
  (`label: "CAP|Register household"`) come through, which is how captions are timed.
  `record.py` reads them from there.
- Never `pkill -f <pattern>` a pattern that appears in your own command line: it kills your shell
  (exit 144). Use recorded PIDs.
- **A foreground command that times out keeps running.** A timed-out chain of `adb shell input`
  calls went on tapping into the next flow. Run UI drivers in the shell's background mode, or wrap
  each chain in an inner `timeout 60 …`.
- `uiautomator dump` sometimes returns an empty hierarchy while the screen is animating: retry
  after a second before concluding an element is missing.
- `adb shell input text` can drop characters on long strings: read the field back (dump) and
  retype, or type in short chunks. Maestro's `inputText` is more reliable.

### Selectors that work on the Compose UI

- Text selectors are regular expressions matched against the **whole** text: escape them
  (`re.escape`), and allow the mandatory marker: `"Children 3–5, male.*"` for `Children 3–5, male *`.
- **Floating action buttons ("New household", "New member") are not in the accessibility tree.**
  Tap by point. Maestro percentages must be **integers** (`"78%,90.7%"` fails to parse, and the error
  only shows in `commands.json`); use pixels (`Flow.tap(point=…)` rejects decimal percentages).
  The button sits higher in programs with a map view.
- The "+" on a relationship type has content-desc "New event": tap it by that label, not by point
  (a point tap fired before the screen loaded).
- Radio options repeat (Yes/No everywhere): anchor with `below: <label regex>`.
- Gboard ignores ESC (`keyevent 111`): hide the keyboard with BACK (`keyevent 4`), and only when
  `adb shell dumpsys input_method` shows `mInputShown=true`, or BACK leaves the screen.

### The ensure-visible pattern

Before every tap, `scrollUntilVisible` the target (uncounted automation aid), because the keyboard,
the FAB and long forms hide fields. Rules:

- Fill fields **top to bottom**: `scrollUntilVisible` only scrolls down by default.
- Scroll to the **option**, not the label (`element: {text: "No", below: "1.13 Household type…"}`): the label
  can be visible while its options are under the fold. Once the label has scrolled off the top,
  `below` fails; for unique option texts, scroll to the text alone.
- Use `visibilityPercentage: 60`, not 100.
- **Inside bottom sheets** (long option sets, multi-select) scroll to the option too: 8–10 options fit.
- After a tap that makes rules show new fields, wait (`waitForAnimationToEnd` + a short pause)
  before scrolling to them; otherwise the scroll can run past the fields as they appear. One flow
  (one variant's second household) still failed deterministically here; budget for one such dead end.

### Other Android specifics

- Section headers must be tapped to open; the section's own "Next" is under the save FAB — tapping
  "Next" saved the form instead.
- Completing: save FAB (`id: actionButton`) → "Complete". Registration save: `id: save`.
- Single-select bottom sheets need "Done"; tile-rendered ones don't: make "Done" `optional: true`.
- Returning to a program list after a dashboard is unpredictable with `back`; relaunch with
  `launchApp: {stopApp: true}` and count it as the two back taps a person would make.
- Record the **app version** in every video title; smoke-test the key flow on an older release
  (3.2.1.2 ran the whole C flow unchanged; only the login screen differs, so the login script
  needed a variant).

### Always verify on the server

After each flow, sync and read the data back through the API (`scripts/check_entry.py`). A "synced"
status in the app, or the flow's own sync step reporting success, was **not** proof: the first full
Android pass synced nothing. Capture 3.4.2 also does not always list server-rejected uploads in
its sync error log, so the API read-back is the only check that counts.

### Time zones

The emulator used the host's time zone (UTC+2), the server UTC. After local midnight every new
record was rejected as future-dated ("Enrollment date … cannot be a future date") until the server's
date caught up. Before recording, compare `adb shell date` with `serverDate` from `/api/system/info`,
and put device and server on the target time zone (for the Sierra Leone demo, `Africa/Freetown`):

```bash
adb shell settings put global auto_time_zone 0
adb shell cmd alarm set-timezone Africa/Freetown
```
