# Sync, conflicts, purge and editing

## Unit of work and status

Pick a unit of work the user thinks in (a household, a patient visit) and keep a status per unit
in your own small database (Room; SQLCipher comes with the SDK, key it from the Android Keystore).
It survives the purge, drives the home list and decides what gets uploaded:

| Status | Meaning |
|---|---|
| IN_PROGRESS | objects exist in the SDK; never uploaded |
| COMPLETE | marked complete, waiting to upload |
| SENT | every tracked entity of the unit is `SYNCED` |
| NEEDS_ATTENTION | the server rejected part of it; fix on the phone |
| FIXED_ELSEWHERE | a supervisor fixed it on the server; stop retrying |
| EDITING | a sent unit reopened for corrections |

Why per unit: the SDK uploads with `atomicMode=OBJECT`, so a unit can arrive half-imported. A
single "synced / not synced" flag for the whole phone hides that.

Don't pin an SDK older than 1.13.1: before it (Capture 3.0–3.3.0.x), a tracked entity accepted
with its enrollment rejected had all its attributes marked synced, so the retried enrollment went
up without them (ANDROSDK-2213).

## Upload

```kotlin
val teis = listOf(unitTei) + childTeis + pendingDeletedChildTeis
d2.trackedEntityModule().trackedEntityInstances().byUid().`in`(teis).blockingUpload()
```

- Relationships travel with the tracked entities they point at.
- Scope the list per unit. A list built from "all dirty objects in this org unit" leaks one unit's
  rejected deletion into another unit's upload and flags the wrong unit.
- After upload: SENT if every tracked entity still present is `SYNCED` (deleted ones vanish locally
  once their deletion is sent — treat missing as done); NEEDS_ATTENTION on `ERROR`/`WARNING`, with
  the root-cause conflict's plain message and the field/object it names; otherwise not sent yet.
- The SDK only re-sends an `ERROR` object after a local edit, and that edit overwrites a fix made
  in Capture. Rule: the phone owns a unit until it is clean; if a supervisor fixes it on the server
  instead, the user marks it "fixed elsewhere".

## Background sync

WorkManager: a periodic worker (network constraint) plus a one-off "sync now". Record when metadata
was last downloaded (including at login) so the first background run doesn't download it again —
a metadata download right after login otherwise competes with the user's first writes. After a
metadata download: reload rules, re-run any structure check.

## Purge

`wipeData()` removes all tracker data (and reserved values) at once; metadata stays. So:

- purge only when every live unit is clean (SENT / FIXED_ELSEWHERE) — never with unfinished work;
- if the app lets users edit sent units, also keep every unit that is still inside the editing
  window, or editing needs a download first (see below);
- purge only when online, and re-download reserved values straight after;
- mark ledger rows purged (keep the summary for the home list) and drop anything personal you kept.

## Editing sent data

Whether users may change data after it has been sent, and for how long, is a project decision —
many apps leave all corrections to Capture. Settle it with the implementation owner, write it as
one pure function (unit status + dates → editable / open-for-edit / read-only) so screens, writer
and purge all ask the same question, and unit-test it. Whatever the window, the mechanics are the
same:

- **The purge decides what is still on the phone.** `wipeData()` can't keep some units, so either
  hold the purge while any unit is inside the window, or let "Edit" download the unit again
  (`trackedEntityInstanceDownloader().byUid().in(...)`, finding child records through a back-link),
  which needs a connection. Anything the user typed that isn't in the data model is gone after the
  purge and must be rebuilt or re-entered.
- **Refresh before editing** a sent unit when online, so a fix made in Capture meanwhile isn't
  overwritten by the phone's older copy (the SDK never overwrites objects with unsent local changes).
- **Deleting after sync is a real server delete**: a synced object deleted locally is uploaded as a
  deletion. Refuse deleting a child record that other users have worked on (a follow-up event
  recorded, enrollment completed or cancelled, enrolled in another program), checked after the
  refresh.
- **Deleting needs server rights**: `F_TEI_CASCADE_DELETE` and `F_ENROLLMENT_CASCADE_DELETE`, which
  data-entry roles usually lack. Without them the upload fails with E1100/E1103; show a plain message
  ("ask a supervisor to delete it in Capture") rather than a raw conflict. Granting them widens
  rights on the server — the implementation owner's decision.
- Use a distinct status (EDITING) for a reopened sent unit: never offer "discard" on it, since that
  would upload deletions of data that is already on the server.

## Reserved values

Warn when few are left for the current org unit (e.g. < 20), block starting a new unit when none
are left offline, top up on every sync. `blockingGetValue` pops one value; a value popped and not
used (e.g. the user changed org unit) is simply wasted.
