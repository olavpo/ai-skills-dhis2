# Web API quirks (verified behaviour the docs don't state)

Behaviour established by probing live instances. Each item names the versions it was checked on; re-check on others before relying on it.

## Contents

- Demo instances
- Reading: filters and field selections
- Writing metadata
- Privacy and access
- Errors that mislead

## Demo instances

- **play.im.dhis2.org stable URLs rotate.** They are named `stable-<major>-<minor>-<patch>` and disappear, without a redirect, when the next patch ships. A saved config pointing at one goes stale within months. In September 2026, `stable-2-43-0` and every `stable-2-42-*` path returned nginx 404, while `stable-2-43-1` and `dev` answered. Probe before trusting a saved URL:
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -u admin:district https://play.im.dhis2.org/<name>/api/system/info
  ```
  Or scrape the landing page for `stable-…` links. `dev` is always present. The demo's analytics window is roughly the current and prior year.

## Reading: filters and field selections

- **`:!empty` is not an operator on 2.40** (2.40.12: `400 E1003 "!empty is not a valid operator"`). It works on 2.43.1. Version-gate it, or filter client-side.
- **Filtering on `periodType` fails** (2.42.6: `/api/dataSets.json?filter=periodType:eq:Monthly` gives `400 E1003 "Unable to parse Monthly to PeriodType"`). Fetch `fields=id,periodType` and filter client-side.
- **`fields=:owner` on a program stage omits `program`** (2.43.1). A GET-then-PUT round trip of that payload fails with `409 E4053 "Program stage must reference a program"`. Re-add `program: {id}` before writing it back.
- **Nested field selections on user references are ignored** (2.40–2.43). `createdBy[id]` still returns the full `{id, code, name, displayName, username}` stub. Only `!createdBy` removes it. See *Privacy* below.

## Writing metadata

Partial-update content types (plain JSON PATCH vs JSON Patch) are in SKILL.md → *Updating single fields*. Beyond that, checked on 2.42.5.2:

- **Index-based JSON Patch paths into collections are unsafe.** Many DHIS2 collections are sets with unstable serialisation order. `/categoryMappings/29/...` hit one element on one request and a *different* one on the next. The patch returned 200 with empty `errorReports` while it silently changed an unrelated element. Replace the whole collection at a fixed path, and re-read afterwards: a 200 from JSON Patch proves nothing about what changed.
- **`op: replace` works on a field that is currently empty or absent.**
- **Reference validation is uneven.** `categoryMappingIds` on a program indicator is not validated, so a bogus UID is stored with 200. `optionMappings[].optionId` is checked for existence (`409 E4080`) but not for membership: an option from a different category is accepted. An `optionMappings[].filter` written as `""` reads back as absent.
- **A category option combo's `code` cannot be cleared through the API.** JSON Patch remove, replace-with-null, and PUT with `code` null or absent all return 200 and keep the old value. Setting or changing a code works. Clearing it takes SQL, then a cache clear; until the cache clear the API keeps serving the old value:
  ```sql
  UPDATE categoryoptioncombo SET code = NULL WHERE uid = '<uid>';
  ```
  ```bash
  curl -X POST -u admin:district "<base>/api/maintenance?cacheClear=true"
  ```
  This matters when restoring a shared test instance to its prior state.
- **SQL views are refused when the query *text* contains `users`, `userinfo` or `oauth2client`** (2.43.1). `GET /api/sqlViews/{uid}/data` returns `409 E4310 "SQL query contains references to protected tables"`. It is a plain word scan, so a JSON key such as `sharing->'users'` trips it even though no such table is referenced; `'user'||'s'` passes. Validate generated SQL through an actual SQL view, not only in psql.

## Privacy and access

- **`:owner` exports carry every editor's real name and username** (2.40.12, 2.41.9.1, 2.42.6, 2.43.1). `createdBy`/`lastUpdatedBy` on every object, and user references in collections such as `userGroups.users[]`, render as `{"id","code","name","displayName","username"}`. So do the `/api/metadata` default and the Import/Export app's default export. Before sharing an export, exclude them with `fields=:owner,!createdBy,!lastUpdatedBy`, or use the `dhis2-metadata` fetch, which strips PII.
- **Nested references in inverse collections are not sharing-filtered** (2.42.5.2). A user without read access to data set X still sees X's id and name through `dataElements?fields=dataSetElements[dataSet[id,displayName]]`. Membership counts from such queries are complete, but don't treat them as an access check.
- **`/api/dashboards` hides dashboards without public access, even for superusers** (2.38–2.41; fixed by 2.42). The list, `/{id}`, `/gist` and `/api/metadata?dashboards=true` disagree with each other. On those versions the list endpoint is not authoritative.

## Errors that mislead

- **An unknown `systemSettings` key gives HTTP 500 with an HTML stack trace on GET** (2.42.5.2). `POST` to the same key gives a clean `404 E1005`. A 500 there means "no such key", not a malformed request.
- **Metadata imports can persist and still return 500** (2.38.7). Re-read before retrying. The `dhis2-metadata` importer does this automatically.
