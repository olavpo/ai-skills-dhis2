"""Fetch metadata and/or schemas from a live DHIS2 instance, optionally transformed.

The transformation flags are shared with transform_metadata.py. Here they work
in two layers: matching fields are excluded server-side via the `fields=`
parameter (e.g. `:owner,!sharing,!translations,!email,...`) so the data never
leaves the server, and the local transform pass then runs on the download to
catch what field filtering cannot reach (embedded objects, user references
inside collections). `createdBy`/`lastUpdatedBy` are excluded by default: they
carry the creator's real name and username on every object and are re-stamped
on import anyway. Pass an explicit --fields to take full control.

Examples:
    # Plain export + schemas
    python fetch_metadata.py --url http://localhost:9021 --auth user:pass \\
        --metadata --schemas --out ./export

    # Anonymized, unshared, untranslated, split per type — ready for AI or sharing
    python fetch_metadata.py --url ... --auth user:pass \\
        --metadata --schemas --anonymize --unshare --delocalize --split --out ./export

    # Gentle full export from production: every type, paginated and throttled
    python fetch_metadata.py --url ... --auth token:$PAT \\
        --all-types --page-size 200 --delay 0.3 --exclude users --anonymize --out ./export

    # Only specific types, filtered
    python fetch_metadata.py --url ... --auth user:pass \\
        --types dataElements,indicators,programs --filter "name:like:Malaria" --out ./slice

    # Incremental: only objects created/changed since a date (per-type modes only)
    python fetch_metadata.py --url ... --auth user:pass \\
        --all-types --page-size 200 --since 2026-01-01 --out ./export
"""
import argparse
import json
import os
import sys
import time

import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transform_metadata as tm  # noqa: E402

TRANSIENT_ERRORS = (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError)


def fetch(session, url, params=None, timeout=600, tries=5, backoff=2.0):
    """GET with retry + exponential backoff on transient connection errors."""
    for attempt in range(1, tries + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except TRANSIENT_ERRORS as e:
            if attempt == tries:
                raise
            wait = backoff * attempt
            print(f"  ! {type(e).__name__} on {url}, retry {attempt}/{tries - 1} "
                  f"in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)


def default_auth():
    auth = os.environ.get("DHIS2_AUTH")
    if auth:
        return auth
    token = os.environ.get("DHIS2_API_TOKEN")
    return f"token:{token}" if token else None


def metadata_types(session, base):
    """All plural type names the instance exposes as metadata endpoints."""
    data = fetch(session, f"{base}/schemas.json",
                 params={"fields": "name,plural,metadata,relativeApiEndpoint"})
    return sorted(s["plural"] for s in data.get("schemas", [])
                  if s.get("metadata") and s.get("relativeApiEndpoint"))


def build_fields(args, opts):
    """The `fields=` value: explicit --fields wins, else :owner minus exclusions."""
    if args.fields:
        return args.fields
    return ",".join([":owner"] + tm.fields_exclusions(opts, strip_audit_refs=True))


def fetch_type(session, base, ptype, args, fields, filters):
    """Fetch one metadata type; paginated when --page-size is set."""
    params = {"fields": fields}
    if filters:
        params["filter"] = filters
    if args.root_junction:
        params["rootJunction"] = args.root_junction

    if not args.page_size:
        params["paging"] = "false"
        data = fetch(session, f"{base}/{ptype}.json", params=params)
        return data.get(ptype, [])

    # order=id:asc gives a stable ordering so pages don't shift mid-export
    params.update({"paging": "true", "pageSize": str(args.page_size),
                   "order": "id:asc"})
    collected, page = [], 1
    while True:
        params["page"] = str(page)
        data = fetch(session, f"{base}/{ptype}.json", params=params)
        items = data.get(ptype, [])
        collected.extend(items)
        page_count = data.get("pager", {}).get("pageCount", 1)
        if page_count > 1:
            print(f"    page {page}/{page_count} (+{len(items)})", file=sys.stderr)
        if page >= page_count or not items:
            return collected
        page += 1
        if args.delay:
            time.sleep(args.delay)


def fetch_bulk(session, base, args, fields, excluded):
    """One /api/metadata request; excluded types are switched off server-side."""
    params = {"fields": fields}
    for t in excluded:
        params[t] = "false"
    return fetch(session, f"{base}/metadata.json", params=params)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("DHIS2_BASE_URL"),
                   help="DHIS2 base URL, e.g. http://localhost:9021 (default: $DHIS2_BASE_URL)")
    p.add_argument("--auth", default=default_auth(),
                   help="user:password or 'token:<PAT>' for personal access token "
                        "(default: $DHIS2_AUTH, or token:$DHIS2_API_TOKEN)")
    p.add_argument("--out", default=".", help="Output directory")

    what = p.add_argument_group("what to fetch")
    what.add_argument("--metadata", action="store_true",
                      help="Full metadata.json in ONE request (heavy on large "
                           "instances; prefer --all-types --page-size for production)")
    what.add_argument("--schemas", action="store_true",
                      help="Fetch schemas.json (with property details)")
    what.add_argument("--types", default=None,
                      help="Comma-separated metadata types to fetch instead of full metadata "
                           "(e.g. dataElements,indicators,programs)")
    what.add_argument("--all-types", action="store_true",
                      help="Fetch every metadata type the instance exposes (discovered "
                           "via /api/schemas). Combine with --page-size/--delay for a "
                           "server-friendly full export.")
    what.add_argument("--exclude", default="",
                      help="Comma-separated types to skip (e.g. users,userGroups); "
                           "works in every mode")
    what.add_argument("--filter", action="append", default=None,
                      help="DHIS2 filter expression applied per type (e.g. 'name:like:Malaria'). "
                           "Repeatable; multiple filters combine with AND unless --root-junction OR. "
                           "Per-type modes only.")
    what.add_argument("--root-junction", default=None, choices=["AND", "OR"],
                      help="How multiple --filter expressions combine (server default: AND)")
    what.add_argument("--since", default=None,
                      help="Only objects created/changed on or after this date "
                           "(adds a lastUpdated:ge: filter; ISO date, e.g. 2026-01-01). "
                           "lastUpdated is set on creation too, so this captures both.")
    what.add_argument("--fields", default=None,
                      help="Explicit field set (default: ':owner' minus createdBy/"
                           "lastUpdatedBy and minus whatever the transform flags exclude). "
                           "Passing this disables the default exclusions.")

    how = p.add_argument_group("server load")
    how.add_argument("--page-size", type=int, default=0,
                     help="Fetch each type page by page with this many objects per "
                          "request instead of one unbounded request (lower = gentler "
                          "on the server; 200 is a good production default). "
                          "0 (default) = single request per type.")
    how.add_argument("--delay", type=float, default=0.0,
                     help="Seconds to sleep between requests (pages and types), "
                          "spreading load on a production server (e.g. 0.3)")

    tr = p.add_argument_group("transform (same flags as transform_metadata.py; "
                              "excluded server-side where possible, then applied locally)")
    tm.Options.add_arguments(tr)
    tr.add_argument("--split", action="store_true",
                    help="Also write per-type files to <out>/split/")
    args = p.parse_args()
    opts = tm.Options.from_args(args)

    if not args.url or not args.auth:
        p.error("--url and --auth are required (or set DHIS2_BASE_URL and "
                "DHIS2_AUTH or DHIS2_API_TOKEN)")
    if not (args.metadata or args.schemas or args.types or args.all_types):
        p.error("nothing to fetch: pass --metadata, --schemas, --types, or --all-types")
    if opts.minimize and not args.schemas:
        p.error("--minimize needs the instance's schemas: add --schemas")

    os.makedirs(args.out, exist_ok=True)

    session = requests.Session()
    if args.auth.startswith("token:"):
        session.headers.update({"Authorization": f"ApiToken {args.auth[6:]}"})
    else:
        user, _, password = args.auth.partition(":")
        session.auth = HTTPBasicAuth(user, password)

    base = args.url.rstrip("/") + "/api"

    info = fetch(session, f"{base}/system/info")
    print(f"Connected to {args.url} (DHIS2 {info.get('version', '?')})")

    by_plural = by_klass = None
    if args.schemas:
        out = os.path.join(args.out, "schemas.json")
        data = fetch(session, f"{base}/schemas.json", params={"fields": "*,properties"})
        with open(out, "w") as f:
            json.dump(data, f)
        print(f"Saved: {out} ({len(data.get('schemas', []))} schemas)")
        by_plural, by_klass = tm.index_schemas(data)

    fields = build_fields(args, opts)
    excluded = {t.strip() for t in args.exclude.split(",") if t.strip()}
    filters = list(args.filter) if args.filter else []
    if args.since:
        filters.append(f"lastUpdated:ge:{args.since}")

    merged = None
    if args.types or args.all_types:
        if args.types:
            types = [t.strip() for t in args.types.split(",") if t.strip()]
        else:
            types = metadata_types(session, base)
        types = [t for t in types if t not in excluded]
        merged = {}
        for ptype in types:
            try:
                items = fetch_type(session, base, ptype, args, fields, filters)
            except requests.exceptions.HTTPError as e:
                # With --all-types some discovered endpoints may 4xx/5xx
                # (permissions, version quirks); skip rather than abort.
                if args.all_types:
                    print(f"  ! {ptype}: {e} (skipped)", file=sys.stderr)
                    continue
                raise
            if items:
                merged[ptype] = items
            print(f"  {ptype}: {len(items)}")
            if args.delay:
                time.sleep(args.delay)
    elif args.metadata:
        if filters:
            print("Note: --filter/--since only apply to --types/--all-types; ignored.",
                  file=sys.stderr)
        merged = fetch_bulk(session, base, args, fields, excluded)
        merged = {k: v for k, v in merged.items() if k not in excluded}

    if merged is not None:
        if not args.fields:
            # Server-side !createdBy/!lastUpdatedBy only reaches top-level objects;
            # embedded ones (programStageDataElements, mapViews, ...) carry their own.
            merged = tm.strip_keys(merged, tm.AUDIT_REF_FIELDS)
        if opts:
            merged = tm.transform(merged, opts, by_plural, by_klass)
        out = os.path.join(args.out, "metadata.json")
        with open(out, "w") as f:
            json.dump(merged, f)
        n_types = sum(1 for v in merged.values() if isinstance(v, list))
        n_obj = sum(len(v) for v in merged.values() if isinstance(v, list))
        print(f"Saved: {out} ({n_obj} objects across {n_types} types; fields={fields})")
        if args.split:
            files = tm.write_split(merged, os.path.join(args.out, "split"), quiet=True)
            print(f"Saved: {len(files)} per-type files to {os.path.join(args.out, 'split')}")


if __name__ == "__main__":
    main()
