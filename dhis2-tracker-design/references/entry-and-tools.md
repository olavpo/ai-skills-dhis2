# Entry cost and choice of tool

Entry cost is real (enumerator time is usually the largest cost of an enumeration), but it belongs to the tool, not the model. Estimate it per tool, then ask what a better tool would change before letting it decide the model.

## Settings that change entry cost (verified: DHIS2 2.42.6, Capture Android 3.4.2, web Capture 2.42)

| Setting | Android 3.4.2 | Web Capture 2.42 |
|---|---|---|
| Radio render type, data elements | Honoured, except long option sets (11 options) → bottom-sheet list | Honoured, including 11 options |
| Radio render type, attributes | Honoured | Ignored: always a drop-down |
| "Default" render, data elements | Tiles (one tap) in some stages, drop-downs in others | Drop-down (one extra click per field) |
| Yes/no fields | Always radio | Always radio |
| AGE value type | "Date of birth or age": 8 digits or years | Date input + years/months/days |
| Use first stage during registration | n/a | Works (one page), then opens the event in edit mode (+1 click) |
| Auto-generate + open after enrollment | Opens the event with an **empty date** (+3 taps) | Date empty, must be typed |
| Ask to create new event on completion | "Schedule next event?" (+2 taps) | New event opens on the Schedule tab; switching to Report asks to discard (+2 clicks) |
| Relationship "create new" | Search across all programs of the type, program not preselected, forced search, org unit picker | One form, program and org unit preselected |
| Registration | Forced search first when there are searchable attributes | Search optional; values carry over |

The relationship "create new" sequence on Android is the single biggest cost of linked-member designs (patterns 3 and 4).

Other findings: a multi-select (MULTI_TEXT) for "up to three reasons" is faster than one yes/no field per reason on both platforms, and is countable in analytics (`analytics-reach.md`); web Capture needs https (or localhost) and renders as one column at phone width.

Other Capture facts that affect a design's field use:

- **Minimum Capture Android 3.3.1** (SDK 1.13.1). On 3.0–3.3.0.x, when the tracked entity is accepted but its enrollment rejected, all attributes are marked synced and the retried enrollment loses them (ANDROSDK-2213).
- **Rejected uploads are hard to supervise.** On 3.4.2 a server rejection shows only as a per-record "Sync error" chip (the message is inside the record); Settings → sync error log does not list them. Plan a server-side check (records with sync conflicts, or expected vs received counts) rather than relying on enumerators to report.
- **Accessibility**: the floating "new record" buttons in Android 3.4.2 have no accessibility node, which matters for screen-reader users and for UI automation.

## Estimating entry time

Count what a person must do per household (taps, typed characters, scrolls, screen changes) for each design and platform, ideally by scripting the flow once and counting what actually ran. Call the result "interface time", state the timing model, and recommend a timed manual run and a field trial before trusting the absolute numbers; the relative ranking is the useful part. The keystroke-level model and how to script and count flows are in `dhis2-prototyping` (`references/effort.md`).

Household with 7 children, 2 out of school, in a PoC (interface time): Android A 5:06, C2 6:03, B 7:13, C 7:28; web A 3:49, C2 4:12, B 4:43, C 5:11; counts design D 4:22 / 3:05.

## Tool options for the same model

| Tool | Offline | Build | Maintenance | Notes |
|---|---|---|---|---|
| Capture Android | Yes | None | Core team | Works everywhere; slowest for linked members |
| Capture web on phones/computers | No | None | None | Free to trial where connectivity is good |
| Web app (PWA), short offline queue | Hours | ~6–8 weeks + pilot | 0.1–0.2 FTE | Encrypted queue of unsent households, sync and purge; common skills (React) |
| Web app (PWA), offline-first | Yes | ~8–11 weeks + pilot | 0.1–0.2 FTE | Offline unlock with an app PIN; no screenshot blocking |
| Android app on the DHIS2 SDK | Yes | ~5–8 weeks + pilot | 0.15–0.25 FTE | Offline login, encrypted storage, reserved values and sync come from the SDK; mostly UI work; needs Kotlin + SDK skills; each DHIS2 upgrade needs a compatible SDK. Built with `dhis2-android-sdk-app` |
| Android app without the SDK | Yes | ~9–12 weeks + pilot | 0.2–0.3 FTE | Rebuilds what the SDK provides; rarely worth it |

Estimates are judgements from one household-enumeration PoC (a narrow listing app), not measurements. A custom listing app removed roughly half the Android interface time (estimated ~3:45 for the same household) mostly by creating followed members from the roster row, prefilling dates, and skipping forced searches.

## Principles for a custom entry app

- **Keep the model identical to what Capture uses.** Then follow-up actors keep working in Capture, areas without the app enter the same structure in Capture, and indicators and dashboards do not change: no lock-in. Choose the model first (principle 1), then the app writes it.
- **Create-only.** The app registers and submits; corrections and follow-up stay in Capture. This keeps it small and keeps personal data off phones.
- **Submit a household as a unit.** A web app or backend job can post parent, events, members and relationships in one `/api/tracker` request with `atomicMode=ALL`, so a household is never half-synced. The Android SDK always uploads with `atomicMode=OBJECT` (each object accepted or rejected on its own; a relationship's other end travels in the same upload), so an SDK app must check each object's sync state and retry the rejected ones.
- **What the tool does not do for you.** `/api/tracker` runs program rules on import (mandatory fields and error actions reject payloads, assigned values must be computed by the writer). The Android SDK does not create auto-generated events, and for an ID with an org-unit code in its pattern it reserves values for every coded org unit in the capture scope at once, with no single-org-unit download. Deleting synced records needs the cascade-delete authorities (`F_TEI_CASCADE_DELETE`, `F_ENROLLMENT_CASCADE_DELETE`) for the app's users.
- **Sync and purge**: delete local data once the server accepts it; encrypt what waits.
- **Security basics**: https only, short sessions, a user that can be disabled centrally when a phone is lost, server time zone set to the country's.
