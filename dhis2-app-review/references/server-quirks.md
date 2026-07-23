# DHIS2 server quirks that affect testing and seeding

Read before writing test data, asserting on an app's mutations, or installing
apps. These are **server** behaviours that make a naive test wrong — not app
bugs. Verify against the DHIS2 version under test; most span 2.40 → current
unless noted.

## Assert against server state, not "every operation succeeded"

Real instances refuse operations that look like they should work, for
server-side reasons the app can't predict:

- **User disable, 2.42+**: some demo users (e.g. `arabic` on the SL demo)
  cannot be disabled even by a superuser — `409 E1004`. Neither `access.update`
  nor `?canManage=true` predicts it.
- **Duplicate translations, 2.40+**: the API rejects a second translation for
  the same locale/property with `E1106`, and enforces whole-object atomicity —
  you cannot partially fix a multi-duplicate object.

So a fix / bulk-operation tool will legitimately leave some rows unchanged.
Assert **"the app's final report matches the API's actual state afterwards"**,
not "every row was fixed". Scale completion timeouts to real-data volumes —
hundreds of objects processed sequentially take minutes; a suite-level
`FIX_TIMEOUT_MS` env var beats a hard-coded wait.

## Verify after write — some writes return 200 OK but no-op

- **`categoryOptionCombos` PUT** returns a bare `200 {"status":"OK"}` but is
  **silently ignored**. Read the object back and diff — the status code lies.
- **Objects with embedded owned collections** (e.g. `maps`): the
  `GET …?fields=:owner` → edit → `PUT` round-trip re-inserts the embedded
  objects and fails with a `409` unique-constraint violation. Don't assume a
  `:owner` GET is a safe PUT payload.

General rule: after any metadata mutation whose success matters, GET the object
back and assert the field actually changed. HTTP 2xx is necessary, not
sufficient. The converse also holds — success isn't always `200`: partial PATCH
returns `204`, and metadata imports can return `200` with `status: "ERROR"` or
`"WARNING"` in the body. Check each endpoint's actual contract (accept any 2xx,
then read the body), never `== 200`.

## Seeding data the current server forbids

When the target state is "legacy data a current server won't let you create"
— the whole point of some cleanup tools (duplicate translations, orphaned
refs) — the API refuses to seed it (`E1106`, etc.). Write it at the **DB level**
instead, then clear the cache and read it back through the API to confirm the
server now serves the bad state:

```
# 1. jsonb UPDATE straight into the table (DB access via the dhis2-instances skill)
# 2. curl -s -u USER:PASS -X POST "$DHIS2_URL/api/maintenance?cacheClear=true"
# 3. GET the object through the API and confirm the forbidden state is present
```

Keep the seeding script in `e2e/` — it's reusable across versions.

## Derive test expectations from a post-seed read-back, not seeding intent

Demo databases carry surprises that change expected results — the Lao demo ships `aggregateExportCategoryOptionCombo: "NEW"` on TB program indicators; pre-existing rules and required attributes reject "minimal" tracker payloads on every Sierra Leone program. Two classes of false test failures follow from asserting what the seed script *meant* to create. After seeding, **read the objects back through the API and derive the suite's expected values from that read-back** (ideally the seed script emits an expectations file from what the server actually accepted). Also note: program indicator `code` is unique server-side (`E5003`) — a scenario needing two PIs matching one data element via `code` cannot be seeded; use a different matching strategy (e.g. `aggregateExportDataElement`).

## Prefer JSON Patch for single-object metadata edits

The `GET ?fields=:owner` → edit → `PUT` round-trip can 409 on embedded collections (above). The reliable alternative for targeted edits is JSON Patch:

```
PATCH /api/<type>/<uid>
Content-Type: application/json-patch+json

[{"op": "replace", "path": "/code", "value": "..."}]
```

Verified working for scalar fields, whole-collection `add` (`program.categoryMappings`) and whole-collection `replace` (`dataSet.dataSetElements`). Verify-after-write still applies.

Three caveats (verified 2.40–2.43):

- **JSON Patch re-validates the whole object**, so it can **409 on pre-existing integrity issues unrelated to the change** (`E6012` "attribute not assigned to type", `E6000` "program has more than one program instance", …). Old production metadata — exactly what bulk admin tools target — trips this constantly, so a tool under review will legitimately fail on some objects. Dedicated endpoints skip it.
- **Never patch `/sharing`.** `replace /sharing` returns 200 but **silently ignores** `public`/`external` (the patch value needs `publicAccess`/`externalAccess`, not the aliases a GET shows) — the object looks re-shared but stays publicly accessible. The correct tool is `PUT /api/sharing?type=<singular>&id=<uid>` (recipe in the `dhis2-docs` skill), which changes only sharing, preserves `owner`, and skips whole-object validation. Flag any app that patches sharing.
- **Plain-JSON partial PATCH is version-split**: `Content-Type: application/json` with body `{"name":"…"}` returns **204** on ≤2.41 but **415** on ≥2.42, where JSON Patch is required. A robust tool tries plain JSON first and falls back on 415; test both sides of the 2.42 boundary.

## Validating program rule conditions cheaply

`POST /api/programRules/condition/description?programId=<uid>` with the condition expression as a **text/plain** body returns `status: OK|ERROR` plus a human-readable message — a cheap validity check for generated conditions before driving the UI. Because it consumes text/plain, the `@dhis2/app-runtime` data engine (JSON only) can't call it — use fetch/curl.

## Asserting on rule effects: key on your rule's own UID

Don't assume a "minimal baseline tracker import succeeds cleanly" on demo programs — required attributes and pre-existing rules reject it. The robust pattern: key assertions on **the created rule's own UID in the tracker `validationReport`** — present for a violating payload, absent for a valid one — and tolerate unrelated import noise from the seed's other rules.

## `/api/system/tasks` (global) lags; per-job detail is live

`GET /api/system/tasks` returns a coarse, lagging summary (observed: 2 tasks, frozen) while `GET /api/system/tasks/{type}/{id}` returns live, advancing detail (observed: 67 entries with LOOP progress) *for the same running job*. A job-monitoring app — or a test polling for completion — cannot rely on the global map for live progress.

## Probing installed/bundled apps on 2.42+

`/apps/<key>/index.html` → **404** even when the app exists; `/apps/<key>` (no suffix) → 200. Probe existence via `GET /api/apps` (authoritative) or the suffix-less URL — an `/index.html` probe produces false "app missing" results.

## App install / CORS status codes

- **`POST /api/apps`** returns `201` on 2.42+ but `204` on ≤2.41 — accept any
  2xx, don't hard-check one code. `DELETE /api/apps/<key>` returns `204`.
- **`POST /api/configuration/corsWhitelist` replaces the whole list**, it does
  not append — easy to wipe the demo defaults. Always read-modify-write
  (`GET` → add your origin → `unique` → `POST` the full list back). The
  endpoint accepts **POST only** — `PUT` returns `405 Method Not Allowed`.
