# Testing an SDK app

Four levels, fastest first. The first two catch most bugs; the last two are the ones that prove
the app writes what DHIS2 expects.

## 1. JVM tests (domain, rules, structure)

No emulator. Load the program's metadata package JSON (the same files you import on the test
server) into the rule adapter's neutral classes and assert effects — see `rule-engine.md`. Pass the
package path with `tasks.test { systemProperty("packageDir", …) }`. Also unit-test date arithmetic
(age from years around birthdays and 29 February, due dates), completion checks, and any policy
(what is editable, when to purge) as pure functions.

## 2. Instrumented tests against a disposable server

Run on an emulator against a DHIS2 instance created for the purpose (broker instance in the
sandbox; `dhis2-instances` skill), seeded with the program's metadata and a test user.

- Pass server, user and password as runner arguments; read them with
  `InstrumentationRegistry.getArguments()`. The emulator reaches the host as `10.0.2.2`, so the
  server URL is `http://10.0.2.2:<host port>`; the app's network security config must allow
  cleartext for `10.0.2.2`.
- Log in and download metadata once, in `@BeforeClass`; order tests with
  `@FixMethodOrder(MethodSorters.NAME_ASCENDING)`.
- In Kotlin, `fun t1_x(): Unit = runBlocking { … }` — without `: Unit` a test whose last expression
  returns a value is rejected ("Method … should be void") and the whole class fails to initialise.
- Read results back from the server with `HttpURLConnection` + Basic auth
  (`/api/tracker/trackedEntities/<uid>?program=…&fields=…`); a 404 proves a deletion.
- Tests that paid off: the exact objects written (types, org units, dates, statuses, values);
  event counts per enrollment (catches a future SDK that auto-generates events); status changes
  before sending; an empty unit; upload of one unit by filter while another stays `TO_POST`;
  a forced partial import (a future enrollment date is a reliable trigger) that is reported, fixed
  and re-sent; edit after sending; `wipeData()` and reserved values; deleting a record with
  follow-up work is refused (create the follow-up event through the API first).
- When a status assertion fails, log every object's `syncState`, `aggregatedSyncState` and
  `deleted`, and all import conflicts — the headline message is often a follow-on.
- **Check the clocks before a session**: compare `adb shell date` with `serverDate` from
  `/api/system/info`. Test instances often run on UTC; with the emulator ahead of the server, every
  enrollment made after local midnight is rejected as a future date until the server's date
  catches up.
- **Offline**: `adb shell cmd connectivity airplane-mode enable|disable` toggles the emulator's
  network cleanly — use it for "entered offline, synced later" tests.
- **Capture the exact payloads**: point the app at a small logging reverse proxy (e.g. on
  `$SANDBOX_HOST_PORT` in the sandbox, app URL `http://10.0.2.2:$SANDBOX_HOST_PORT`) that forwards
  to the instance and records every `/api/tracker` request body and job report. It turns a device
  test into API evidence and shows what the SDK really sends.
- **Metadata changes need a fresh download**: after changing the program on the server (stage
  settings, text patterns, rules), re-download metadata (or clear app data and log in again) before
  testing — otherwise the app runs against its stale copy.

Running in the agent sandbox: `./gradlew connectedDebugAndroidTest` hangs (AGP uses its own
x86_64 `adb`). Instead:

```bash
./gradlew :data:assembleDebugAndroidTest
adb install -r -t data/build/outputs/apk/androidTest/debug/data-debug-androidTest.apk   # a library's test APK contains the library
adb shell am instrument -w -r -e serverUrl http://10.0.2.2:9013 -e username u -e password 'p' \
    [-e class 'pkg.MyTest#t1_x'] <applicationId>.test/androidx.test.runner.AndroidJUnitRunner
```

Runner arguments defined in Gradle don't apply this way; pass them with `-e`.

## 3. Model equivalence with Capture

Compare the *shape* of what the app wrote with records written the Capture way on the same server
(entered in Capture, or by a loader that mirrors Capture): which objects exist, which data elements
and attributes each carries, statuses, event dates relative to enrollment dates, scheduled due-date
offsets, relationships per derived record. Ignore values and fields that depend on answers. A
script over `/api/tracker` does this in seconds and can run after every change. (It found a bug in
the reference loader — relationships silently never written — not in the app.)

## 4. The UI on a device

General emulator driving — adb taps and `uiautomator dump`, Maestro flows, `screenrecord` for
demo videos, the emulator's time zone, mock GPS, and keeping UI drivers out of foreground commands
that can time out — is owned by `dhis2-prototyping` (`references/android-flows.md`,
`references/recording-and-media.md`, `references/setup.md`). Installing APKs and reading the
screen over the host's adb is in `dhis2-android-testing`.

Facts specific to a Compose app built with the design system:
- floating action buttons are not in the `uiautomator` tree — tap by point;
- a top-bar icon's `contentDescription` can collide with a button text (e.g. "Done") — give
  icons distinct descriptions or `testTag`s;
- hide the keyboard with BACK only when `dumpsys input_method` shows `mInputShown=true` (Gboard
  ignores ESC), otherwise BACK leaves the screen.
