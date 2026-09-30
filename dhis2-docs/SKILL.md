---
name: dhis2-docs
description: "Use when working with the DHIS2 Web API or looking up DHIS2 documentation: executing ad-hoc API calls against a live instance, endpoint behavior and parameters, developer guides, implementation or system-administration guidance, or DHIS2 concepts. Triggers on requests to connect to a DHIS2 instance, fetch data elements, indicators, organisation units, or analytics, find how an endpoint works or what parameters it accepts, or any question that requires consulting the official DHIS2 docs. For bulk metadata export/transform/import, use dhis2-metadata instead."
---

## Overview

Two things in one skill: how to find and fetch official DHIS2 documentation from docs.dhis2.org, and the conventions for making ad-hoc API calls against a live instance. For docs, the site publishes machine-readable indexes (`llms.txt`) and serves every page as Markdown, so every lookup is the same two steps: fetch an index to find the page, then fetch the page as `.md`. Never guess page URLs.

---

## Lookup workflow

**1. Fetch the right index** (WebFetch or equivalent):

| Question is about | Index URL | Size |
|---|---|---|
| Web API, developer guides, Android SDK, app development | `https://docs.dhis2.org/en/llms-develop.txt` | ~6 KB |
| Implementation guidance (config, tracker design, data quality, …) | `https://docs.dhis2.org/en/llms-implement.txt` | larger (~400 pages) |
| System administration (installation, upgrades, monitoring) | `https://docs.dhis2.org/en/llms-manage.txt` | small |
| End-user app guides (Capture, Maintenance, analytics apps, Android) | `https://docs.dhis2.org/en/llms-use.txt` | medium |
| Cross-cutting topics | `https://docs.dhis2.org/en/llms-topics.txt` | medium |
| Unsure which section | `https://docs.dhis2.org/llms.txt` (master index, all sections) | ~94 KB |

For API questions, `llms-develop.txt` is almost always the right starting point and is cheap enough to fetch every time.

**2. Pick the page from the index and fetch it with `.html` replaced by `.md`.** Every docs page serves its Markdown source this way (`Content-Type: text/markdown`), which is far better to read than the rendered HTML:

```
https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-243/metadata.html   ← index links this
https://docs.dhis2.org/en/develop/using-the-api/dhis-core-version-243/metadata.md    ← fetch this
```

Some pages (e.g. `metadata.md`) are very large; if a fetch is truncated, fetch again with a prompt targeting the specific section.

## Versions

The indexes link the **current stable** version (e.g. `dhis-core-version-243`). If the user is on a different DHIS2 version, substitute the version segment in the URL — API behavior changes between releases:

```
dhis-core-version-243      DHIS2 2.43 (stable at time of writing)
dhis-core-version-242      DHIS2 2.42
dhis-core-version-241      DHIS2 2.41
dhis-core-version-master   development branch
```

Older doc pages show API-versioned paths like `/api/33/configuration/corsAllowlist` — the numeric prefix is optional and the unversioned path (`/api/configuration/corsAllowlist`) works on current servers. Don't copy `/api/NN/` verbatim from examples.

---

## Reading DHIS2 core source

When the docs don't settle a question (exact authority semantics, what an endpoint really validates), read the implementation. A blobless sparse clone of [dhis2/dhis2-core](https://github.com/dhis2/dhis2-core) is fast (~53 MB, under a minute) and greppable:

```bash
git clone --filter=blob:none --no-checkout --depth 1 --branch 2.42 \
    https://github.com/dhis2/dhis2-core /tmp/dhis2-core
cd /tmp/dhis2-core
git sparse-checkout set dhis-2/dhis-api dhis-2/dhis-services dhis-2/dhis-web-api
git checkout   # REQUIRED: sparse-checkout alone leaves zero files, which looks like a failed clone
```

For a single known file, `raw.githubusercontent.com/dhis2/dhis2-core/<branch>/<path>` also works. GitHub code search requires auth, so grep the sparse clone instead.

---

## Working against an instance

Conventions for ad-hoc API calls (plain `curl` or a few lines of Python — no special tooling needed).

**Credentials.** Look for a `.env` (searching upward from CWD) with:

```
DHIS2_BASE_URL=https://myhost.org/dhis     # no /api suffix
DHIS2_API_TOKEN=d2pat_...                  # personal access token, or
DHIS2_AUTH=admin:district                  # user:password (typical for sandboxes)
```

If credentials are missing, ask the user, write them to `.env` in CWD (preserving any existing variables), and make sure `.env` is gitignored. The `dhis2-metadata` scripts read the same variables.

**Auth header for PATs is `Authorization: ApiToken {token}` — NOT `Bearer`.** Basic auth works as usual (`curl -u user:pass`).

**Always `curl -g` against DHIS2 APIs.** Any `fields=` selection with brackets (`fields=categoryCombo[id,name]`) is silently eaten by curl's URL globbing — the failure mode is an empty body, not an error. `-g` disables globbing; urllib/requests are unaffected.

**Keep responses out of context.** Default to `pageSize=50` and a narrow `fields=` selection (e.g. `fields=id,name`) on collection GETs. When a response may be large (full metadata, analytics, schemas), write it to a file and inspect it selectively (`jq`, Read with offsets) instead of printing it in full.

**Verified quirks live in `references/api-quirks.md`.** Read it before concluding an endpoint is broken, or before writing a script that filters, patches collections or exports metadata for sharing. It covers rotating play.im.dhis2.org demo URLs, unsupported filter operators, unsafe index-based JSON Patch paths, unclearable COC codes, SQL-view word bans, and `:owner` exports leaking editors' names.

**Writes.** Before any POST/PUT/PATCH/DELETE to an instance that is not localhost or explicitly disposable, show the request (method, URL, body) and get the user's confirmation. Sandboxes and throwaway test instances don't need per-write confirmation. If a disposable instance is needed and the `dhis2-instances` skill is available, it can provision one.

---

## Quick reference

Use this to answer the most common API questions without fetching anything.

### Object filters

```
filter=name:like:Malaria
filter=valueType:eq:NUMBER
filter=shortName:ilike:art
```

Operators: `eq` `!eq` `like` `ilike` `gt` `gte` `lt` `lte` `in` `!in` `null` `!null` `empty` `!empty` (`!empty` is rejected on 2.40; see `references/api-quirks.md`)

Multiple filters default to AND logic. Use `rootJunction=OR` for OR logic:

```
filter=name:like:HIV&filter=name:like:Malaria&rootJunction=OR
```

### Field filters

```
fields=id,name,valueType
fields=:all
fields=:identifiable
fields=id,name,organisationUnits[id,name]   # nested
fields=*,!href,!access                       # exclude fields
```

### Pagination

```
page=1&pageSize=50
```

Response includes a `pager` object: `{ page, pageSize, pageCount, total }`.

### Changing sharing on a single object

Use the dedicated sharing endpoint — not JSON-Patch, not a full-object PUT:

```
PUT /api/sharing?type=dataElement&id=<uid>
Content-Type: application/json

{"object": {
  "publicAccess": "--------",
  "externalAccess": false,
  "userAccesses": [],
  "userGroupAccesses": [{"id": "<groupUid>", "access": "rw------"}]
}}
```

- `type` is the **singular** schema name (`dataElement`, not `dataElements`). `GET /api/schemas?fields=singular,plural,dataShareable` gives the plural→singular map and the `dataShareable` flag (metadata-only vs data sharing) in one call.
- The endpoint changes **only** sharing — it never touches `owner` — and does **not** re-validate the rest of the object, so it works on messy legacy objects that fail import validation.
- It fully replaces `publicAccess`, `externalAccess`, `userAccesses` and `userGroupAccesses` with what you send.
- Access strings are 8-char octets: metadata rw = `rw------`, metadata + read-only data = `rwr-----`, metadata + read/write data = `rwrw----`, none = `--------`.

**Footgun — never JSON-Patch `/sharing`:** `[{"op":"replace","path":"/sharing","value":{"public":"--------",…}}]` returns **200**, applies `userGroups`/`users`, and **silently ignores** `public`/`external` — inside a patch value the fields must be named `publicAccess`/`externalAccess`, not the `public`/`external` aliases a GET shows. The object looks re-shared but stays publicly accessible, and nothing errors.

### Updating single fields (name, code, …)

Partial updates are version-split by content-type:

| DHIS2 version | `PATCH /api/<plural>/<uid>` | Result |
|---|---|---|
| ≤ 2.41 | `Content-Type: application/json`, body `{"name":"…"}` | **204** No Content |
| ≥ 2.42 | same request | **415** Unsupported Media Type |
| ≥ 2.42 | `Content-Type: application/json-patch+json`, body `[{"op":"replace","path":"/name","value":"…"}]` | 200 |

Portable pattern: try the plain-JSON PATCH first; on **415**, retry as JSON-Patch and remember the choice for the rest of the run.

Two general rules for writes:

- **JSON-Patch (and any full-object write) re-validates the whole object**, so it can **409 on pre-existing integrity issues unrelated to your change** (`E6012` "attribute not assigned to type", `E6000`, …) — common on old production metadata. Prefer dedicated endpoints (`/api/sharing`) where they exist; on 2.42+ there is no partial-update escape hatch for other fields.
- **Success isn't always 200.** Partial PATCH returns 204; metadata imports can return 200 with `"status": "ERROR"`/`"WARNING"` in the body. Treat any 2xx as transport success, then check the response body and (for writes that matter) re-GET the object to confirm the change landed.

### Analytics endpoint

```
GET /api/analytics
  ?dimension=dx:UID1;UID2          # data elements / indicators
  &dimension=pe:LAST_12_MONTHS     # period
  &dimension=ou:LEVEL-1            # org unit level
  &displayProperty=NAME
  &skipMeta=false
```

Common period shortcuts: `THIS_YEAR`, `LAST_YEAR`, `LAST_12_MONTHS`, `THIS_QUARTER`, `202301` (specific month).
