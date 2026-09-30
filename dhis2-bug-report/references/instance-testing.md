# Instance testing

## Play instances

Public demos at `https://play.im.dhis2.org/<release>`, login `admin` / `district`. Release slugs follow the pattern `stable-2-43-1`, `stable-2-42-5-2`, `stable-2-41-9-1`. Check what is currently up rather than assuming a slug exists; they change with every patch release.

`curl -g` for anything with brackets in `fields=`, otherwise curl's globbing silently eats the query and returns an empty body.

Analytics tables on play are rebuilt nightly. `lastAnalyticsTableSuccess` in `/api/system/info` tells you how stale the figures are, which matters if you are reporting exact numbers.

Prefer fixed periods (`202601`) over relative ones (`LAST_3_MONTHS`) in anything you write into a ticket. Relative periods make the numbers unreproducible next month.

## Versions

Three separate facts, all worth recording.

Core version:

```
GET /api/system/info      -> version, revision, buildTime
```

Installed app version — the manifest is behind authentication, so use a session cookie or basic auth:

```
GET /<app-path>/manifest.webapp     e.g. /dhis-web-data-visualizer/manifest.webapp
                                    -> name, version, core_app
```

If the manifest 302s to the login page, the request is unauthenticated. Log in first via `POST /api/auth/login` with `{"username":..,"password":..}` and reuse the cookie jar.

To confirm the manifest is not stale, grep the compiled bundle for its version string. Read `index.html`, take the asset filenames from the `src`/`href` attributes, fetch the main bundle and look for `appVersion`.

Latest published version, through the instance's App Hub proxy:

```
GET /api/appHub/v2/apps?query=<App%20Name>
```

The result carries a `versions` array, each entry with `version`, `channel`, `minDhisVersion`, `maxDhisVersion` and a `created` epoch in milliseconds. Sort by version to find the newest, and note the channel — a newer version on a non-stable channel is a different claim from a newer stable release. `GET /api/appHub/v2/apps/<id>` does not return the same structure, so use the query form.

Cross-check against the repo when it matters:

```
https://raw.githubusercontent.com/dhis2/<app-repo>/master/package.json
```

Core apps often declare a wide compatibility range (`minDhisVersion` 2.40, no maximum), so one app build can serve several core versions. When it does, say so in the ticket: a fix lands once rather than needing backports.

## Analytics calls worth making

Values, with the metadata that explains how the app will aggregate them:

```
GET /api/analytics.json
  ?dimension=dx:<items>&dimension=pe:<periods>&dimension=ou:<orgunits>
```

Aggregation types per item — this is where `totalAggregationType` lives, and it is often the explanation for a totals bug:

```
GET /api/analytics?dimension=dx:<items>,pe:<period>&filter=ou:<ou>
    &skipData=true&includeMetadataDetails=true
```

Without `includeMetadataDetails=true` the metadata items carry only a name, which tells you nothing.

Numerator, denominator, factor, multiplier and divisor per row:

```
GET /api/analytics?dimension=dx:<items>,pe:<period>&filter=ou:<ou>&includeNumDen=true
```

Compare these columns across item types. Empty `multiplier`/`divisor` on one item type and populated on another is a strong lead when a computed cell comes out as `NaN`.

To see what an app actually asks for, capture its network requests in the headless browser rather than guessing. See `screenshots.md`.

## Creating a temporary visualization

The single-object POST accepts the layout through `columns` / `rows` / `filters`, and derives `dataDimensionItems`, `organisationUnits` and `periods` from the items you put there.

```json
{
  "name": "ZZTEST short description",
  "type": "PIVOT_TABLE",
  "colTotals": true, "rowTotals": true, "colSubTotals": true, "rowSubTotals": true,
  "digitGroupSeparator": "SPACE", "aggregationType": "DEFAULT",
  "columns": [{"dimension": "dx", "items": [
    {"id": "fbfJHSPpUQD"},
    {"id": "BfMAe6Itzgt.ACTUAL_REPORTS"}
  ]}],
  "rows": [
    {"dimension": "ou", "items": [{"id": "ImspTQPwCqd"}, {"id": "O6uvpzGd5pu"}]},
    {"dimension": "pe", "items": [{"id": "202601"}, {"id": "202602"}]}
  ],
  "filters": [],
  "sharing": {"public": "rw------"}
}
```

Data items go in `columns[].items` (or `rows[].items`) as plain ids. Completeness items use the composite form `<dataSetUid>.ACTUAL_REPORTS`, `.EXPECTED_REPORTS`, `.REPORTING_RATE`, `.ACTUAL_REPORTS_ON_TIME`, `.REPORTING_RATE_ON_TIME`.

Then open it in the app at `/<app-path>/index.html#/<uid>`.

### Traps

On the single-object `POST`/`PUT /api/visualizations`, sending the export-shape fields (`dataDimensionItems`, `periods`, `organisationUnits`, a `relativePeriods` object, `columnDimensions`/`rowDimensions`/`filterDimensions`) returns 201 and silently drops them; the object comes back with `dataDimensionItems: []`. Relative periods go in as `pe` items (`{"id":"LAST_12_MONTHS"}`). `POST /api/metadata` is the reverse: it keeps the export shape (a `relativePeriods` object becomes `rawPeriods`) and ignores `columns`/`rows`/`filters` (verified 2.42.6 and 2.43.1). Always read the object back before trusting it — request `:owner` fields, not the default field set, because the default view hides the difference.

`"sharing": {"public": "rwrw----"}` fails with E3011, since visualizations are not data-shareable. Use `rw------`.

Subtotal cells only render when an axis carries two or more dimensions. If subtotals are the thing under test and no subtotal row appears, check the layout before concluding the option is broken.

Verification pattern after any create:

```
GET /api/visualizations/<uid>?fields=:owner
```

Confirm the item list, the dimensions, the periods and the org units all survived.

### Cleanup

```
GET    /api/visualizations?fields=id,name&filter=name:like:ZZTEST&paging=false
DELETE /api/visualizations/<uid>
```

Re-run the GET afterwards and confirm it returns nothing.
