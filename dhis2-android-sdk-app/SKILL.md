---
name: dhis2-android-sdk-app
description: Build a native Android app on the DHIS2 Android SDK (org.hisp.dhis:android-core) — a custom data-collection or tracker app that logs in, downloads metadata, writes tracked entities, enrollments, events and relationships offline, evaluates program rules with the DHIS2 rule engine, and syncs. Use this skill whenever the user wants to create, scaffold, extend, debug or test an Android app that uses the DHIS2 SDK, pins SDK / rule-engine / expression-parser versions, writes tracker data from Kotlin, maps SDK program rules into the rule engine, handles import conflicts or partial uploads, or uses the DHIS2 mobile UI library (designsystem-android) — even if they only say "a custom Android app for DHIS2", "an app instead of Capture" or "offline tracker app". Not for choosing the tracker data model or Capture vs a custom app (dhis2-tracker-design), driving or testing the existing Capture app (dhis2-android-testing), or web apps (dhis2-apps).
---

# Building an app on the DHIS2 Android SDK

The SDK does the hard parts of an offline DHIS2 client — authentication, a local database,
metadata download, tracker upload with conflict tracking — but it is a library, not a framework:
auto-generated events, program rules, form logic and "what is ready to upload" are the app's job.
The fastest way to get each of those right is to copy what the Capture app does, because Capture
is built on the same SDK and the server expects data shaped the way Capture writes it.

The guidance here comes from building a household-listing app of this shape (five modules, a
rule adapter, a writer, a sync manager and instrumented tests against a live server); the class
names used below (`Writer`, `RuleEngineAdapter`, `MetadataRepository`, `SyncManager`) are that
layout, not SDK types.

## Workflow

Working in the agent sandbox (Linux aarch64)? Read `references/build-env.md` first — the build
needs workarounds there.

1. **Clone the references before designing anything.** `dhis2-android-capture-app` (`main`) and
   `dhis2-android-sdk` at the tag you will pin. Grep them for every API question — it beats
   guessing and beats the docs, which lag the code. Useful places:
   - Capture `gradle/libs.versions.toml` — the version set known to work together.
   - Capture `dhis2-mobile-program-rules/` — SDK → rule engine mapping.
   - Capture `form/.../RulesUtilsProviderImpl.kt` — how effects become form state.
   - SDK `core/src/main/java/org/hisp/dhis/android/core/<module>/` — repositories, `*CreateProjection`, `RelationshipHelper`, `GeometryHelper`.
   - `dhis2-mobile-ui` at the pinned tag — component signatures.
2. **Pin the version set and prove it builds** with a trivial module before writing features
   (section "Versions" below). Build problems (repositories, JitPack, parser clash, aapt2 on ARM)
   surface on day one this way instead of in the middle of feature work.
3. **Lay out modules so the SDK is behind one of them** and program rules can be tested without
   Android (section "Architecture").
4. **Write the domain rules and the rule adapter with JVM tests first**, against the real program
   metadata JSON. Seconds per run; this is the best feedback loop in the project.
5. **Write the data layer** (writer, reader, sync) and instrumented tests that run against a
   disposable DHIS2 instance and read results back through the Web API.
6. **Build the UI last**, as a thin layer over field state.
7. **Verify on a device against the server**: an app saying "synced" is not proof; read every
   object back through `/api/tracker`.

## Versions

Take the set from Capture `main` and change it only as a set. As of September 2026:

| Artifact | Version |
|---|---|
| `org.hisp.dhis:android-core` | 1.14.2 |
| `org.hisp.dhis.rules:rule-engine-jvm` | 3.8.1 |
| `org.hisp.dhis.lib.expression:expression-parser-jvm` | 1.4.3 (**forced**) |
| `org.hisp.dhis.mobile:designsystem-android` | 0.7.1 |
| `org.jetbrains.kotlinx:kotlinx-datetime` | `0.7.1-0.6.x-compat` (forced) |
| AGP / Gradle / Kotlin / KSP | 9.0.1 / 9.3.1 / 2.3.20 / 2.3.6 |
| compileSdk / minSdk | 36 / 23 |
| Room (this skill's choice for an app DB; not in Capture's catalog) | 2.8.4 |

Why forcing matters: the SDK and the rule engine each depend on the expression parser (SDK 1.14.2
on 1.3.1, the engine on 1.4.x). Two versions on the classpath give parse differences between the
app and the server. Force both the `-jvm` artifact and the multiplatform root module
`expression-parser` (a redirect with no jar of its own, but a drift check will flag it):

```kotlin
configurations.all { resolutionStrategy {
    force("org.hisp.dhis.lib.expression:expression-parser-jvm:1.4.3")
    force("org.hisp.dhis.lib.expression:expression-parser:1.4.3")
    force("org.jetbrains.kotlinx:kotlinx-datetime:0.7.1-0.6.x-compat")
} }
```

Add a Gradle task that resolves the app's runtime classpath and fails if the three DHIS2
artifacts drift from the catalog; a transitive bump otherwise slips in silently.

Repositories: the SDK's SMS module needs `com.github.dhis2:sms-compression:0.2.0`, published on
JitPack only. Add JitPack restricted to that group. Enable core library desugaring
(`desugar_jdk_libs` 2.1.5) in every Android module; the SDK needs it.

## Architecture

```
metadata/  JVM   generated UID constants for the program; structure check (optional)
domain/    JVM   form state (FieldState per UID), completion check, app checks, date arithmetic
rules/     JVM   neutral rule metadata → dhis2 rule engine → FieldState   (no SDK dependency)
data/      Android library — the only module that imports org.hisp.dhis.android.core
           D2Provider, Session, MetadataRepository, Writer, Reader, Rules service, SyncManager,
           WorkManager worker, the app's own Room DB if needed
app/       Compose UI over FieldState; view models; navigation
```

- **Key every form input by data element / attribute UID** and render a `FieldState` (hidden,
  mandatory, errors, warnings, assigned value). The rule adapter fills it, app checks add to it.
  This is what lets a custom layout use rules from metadata unchanged.
- **Read option lists, labels and mandatory flags from the SDK at run time**; hard-code only UIDs.
- **Keep `rules/` SDK-free**: map SDK `ProgramRule`/`ProgramRuleVariable` into your own small data
  classes in `data/`, and load the same classes from the program's metadata JSON in tests.
- **Run SDK calls on `Dispatchers.IO`**, not a private single thread: a background metadata
  download on a one-thread dispatcher blocks every form write behind it (seen: the app froze on
  a "Done" button for minutes).

## Writing tracker data

Details and the call cheat sheet are in `references/sdk-api.md`. The rules that matter most:

- **Write as the user goes**, like Capture: create the objects when the form starts and set values
  on each change. Nothing is uploaded until the app decides it is complete — `upload()` respects
  repository filters, so upload only what you choose (`byUid().in(...)`).
- **The SDK does not create auto-generated events** (stages with `autoGenerateEvent`); create them
  yourself, and a scheduled event needs event date null, a due date, then status `SCHEDULE`.
- **Every event's org unit is its enrollment's.** If the user changes the org unit, move every
  object (tracked entities, enrollments, events) — and regenerate `ORG_UNIT_CODE` patterned values.
- **Store rule-assigned values and clear hidden fields**, exactly as the engine says. The server
  runs the same rules on import and rejects data that breaks them. A field that is both hidden
  and assigned is a trap — see `references/rule-engine.md`.
- **Mandatory program attributes are checked on upload** (E1018/E1019), not when you set values:
  validate before marking complete.
- **Keep what the user typed that the data model can't hold** (separate name parts, "age in years")
  in your own store; reconstructing it later is a guess.
- **Dates**: the SDK takes `java.util.Date`; convert from `LocalDate` at local start of day; write
  AGE/DATE values as ISO `yyyy-MM-dd`. Warn when the device time zone is not the server's working
  zone — the server rejects "future" dates around midnight.

## Program rules

Evaluate them with `rule-engine-jvm`, mapping the SDK exactly as Capture does. Details, a
skeleton and the effect semantics are in `references/rule-engine.md`. In short: message = content
+ " " + data; ASSIGN values formatted by the target's value type; date value types map to TEXT;
nothing assigned to a hidden field; feed assigned values back and re-evaluate until stable, so a
rule reading an assigned field (an age calculated by another rule) sees it.

## Sync, conflicts, purge, editing

See `references/sync-and-editing.md`. Key facts: uploads use `atomicMode=OBJECT` (partial
imports are routine, keep a status per unit of work); a relationship's other end travels in the
same upload; the first conflict is often a follow-on message, so surface the root-cause code;
`wipeData()` is all-or-nothing and removes reserved values; whether and how long sent data stays
editable in the app is a project decision with consequences for the purge; deleting synced tracked
entities needs `F_TEI_CASCADE_DELETE` / `F_ENROLLMENT_CASCADE_DELETE` on the server.

## UI

The DHIS2 mobile UI library (`designsystem-android`) gives Capture's look: `DHIS2Theme`,
`InputText`, `InputPhoneNumber`, `InputYesNoField`, `InputRadioButton`, `InputChip`, `Button`,
`InfoBar`. They take `TextFieldValue` and `InputShellState`, so write one generic
`FieldInput(spec, value, state, onChange)` wrapper keyed by UID. Watch for: `InputRadioButton`
turns into a dropdown above a handful of options (extra tap per use — use chips for short lists);
floating action buttons are absent from the accessibility tree (UI automation must tap by point).
Use `FragmentActivity` if you need `BiometricPrompt`.

The SDK's manifest sets `android:networkSecurityConfig` (cleartext everywhere + ISRG roots) and
`allowBackup`; override with `tools:replace` and keep `@raw/isrgrootx1` / `@raw/isrgrootx2` in
your own config.

## Testing

See `references/testing.md`: JVM rule tests from metadata JSON, instrumented tests against a
disposable instance (run with `adb shell am instrument` in the sandbox), server read-back, a
model-equivalence check against Capture-shaped data, and Compose-specific UI facts. General
emulator driving (adb, Maestro, screen recording, time zone, GPS) lives in `dhis2-prototyping`
(`references/android-flows.md`, `references/recording-and-media.md`); installing and inspecting
APKs over the host adb in `dhis2-android-testing`.

## Things not yet verified

Behaviour when the server flips `encryptDB`; R8 keep rules for a release build (the broad
`-keep class org.hisp.dhis.** { *; }` used so far is untested); concurrency limits of SDK writes on
the IO pool under load; changes in SDK 1.15 (Capture `develop` is on a 1.15 snapshot with newer
AGP/Kotlin). Check these rather than assuming.
