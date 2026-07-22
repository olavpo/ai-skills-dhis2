"""Fetch metadata and/or the schema definitions from a live DHIS2 instance.

Examples:
    python fetch_metadata.py --url http://localhost:9021 --auth claude:Test12345 \\
        --metadata --schemas --out ./export

    # Fetch only specific object types
    python fetch_metadata.py --url ... --auth user:pass \\
        --types dataElements,indicators,programs --out ./export

    # Gentle full export from a production instance: every metadata type,
    # paginated and throttled so no single request is heavy on the server
    python fetch_metadata.py --url ... --auth token:$PAT \\
        --all-types --page-size 200 --delay 0.3 --exclude users,userGroups --out ./export

    # Incremental: only objects created/changed since a date
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


def fetch_type(session, base, ptype, args, filters):
    """Fetch one metadata type; paginated when --page-size is set."""
    params = {"fields": args.fields}
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


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("DHIS2_BASE_URL"),
                   help="DHIS2 base URL, e.g. http://localhost:9021 (default: $DHIS2_BASE_URL)")
    p.add_argument("--auth", default=default_auth(),
                   help="user:password or 'token:<PAT>' for personal access token "
                        "(default: $DHIS2_AUTH, or token:$DHIS2_API_TOKEN)")
    p.add_argument("--out", default=".", help="Output directory")
    p.add_argument("--metadata", action="store_true",
                   help="Fetch full metadata.json in ONE request (heavy on large "
                        "instances; prefer --all-types --page-size for production)")
    p.add_argument("--schemas", action="store_true", help="Fetch schemas.json (with property details)")
    p.add_argument("--types", default=None,
                   help="Comma-separated metadata types to fetch instead of full metadata "
                        "(e.g. dataElements,indicators,programs)")
    p.add_argument("--all-types", action="store_true",
                   help="Fetch every metadata type the instance exposes (discovered "
                        "via /api/schemas). Combine with --page-size/--delay for a "
                        "server-friendly full export.")
    p.add_argument("--exclude", default="",
                   help="Comma-separated types to skip (used with --types/--all-types)")
    p.add_argument("--filter", action="append", default=None,
                   help="DHIS2 filter expression applied per type (e.g. 'name:like:Malaria'). "
                        "Repeatable; multiple filters combine with AND unless --root-junction OR. "
                        "Use with --types.")
    p.add_argument("--root-junction", default=None, choices=["AND", "OR"],
                   help="How multiple --filter expressions combine (server default: AND)")
    p.add_argument("--since", default=None,
                   help="Only objects created/changed on or after this date "
                        "(adds a lastUpdated:ge: filter; ISO date, e.g. 2026-01-01). "
                        "lastUpdated is set on creation too, so this captures both.")
    p.add_argument("--page-size", type=int, default=0,
                   help="Fetch each type page by page with this many objects per "
                        "request instead of one unbounded request (lower = gentler "
                        "on the server; 200 is a good production default). "
                        "0 (default) = single request per type.")
    p.add_argument("--delay", type=float, default=0.0,
                   help="Seconds to sleep between requests (pages and types), "
                        "spreading load on a production server (e.g. 0.3)")
    p.add_argument("--fields", default=":owner",
                   help="Field set for type-specific fetches (default ':owner', which returns "
                        "all owned/persisted fields and is the right shape for re-import).")
    args = p.parse_args()

    if not args.url or not args.auth:
        p.error("--url and --auth are required (or set DHIS2_BASE_URL and "
                "DHIS2_AUTH or DHIS2_API_TOKEN)")

    os.makedirs(args.out, exist_ok=True)

    session = requests.Session()
    if args.auth.startswith("token:"):
        session.headers.update({"Authorization": f"ApiToken {args.auth[6:]}"})
    else:
        user, _, password = args.auth.partition(":")
        session.auth = HTTPBasicAuth(user, password)

    base = args.url.rstrip("/") + "/api"

    # Probe instance + version
    info = fetch(session, f"{base}/system/info")
    version = info.get("version", "?")
    print(f"Connected to {args.url} (DHIS2 {version})")

    if args.schemas:
        out = os.path.join(args.out, "schemas.json")
        data = fetch(session, f"{base}/schemas.json", params={"fields": "*,properties"})
        with open(out, "w") as f:
            json.dump(data, f)
        print(f"Saved: {out} ({len(data.get('schemas', []))} schemas)")

    filters = list(args.filter) if args.filter else []
    if args.since:
        filters.append(f"lastUpdated:ge:{args.since}")

    if args.types or args.all_types:
        if args.types:
            types = [t.strip() for t in args.types.split(",") if t.strip()]
        else:
            types = metadata_types(session, base)
        excluded = {t.strip() for t in args.exclude.split(",") if t.strip()}
        types = [t for t in types if t not in excluded]
        merged = {}
        for ptype in types:
            try:
                items = fetch_type(session, base, ptype, args, filters)
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
        out = os.path.join(args.out, "metadata.json")
        with open(out, "w") as f:
            json.dump(merged, f)
        print(f"Saved: {out}")
    elif args.metadata:
        out = os.path.join(args.out, "metadata.json")
        data = fetch(session, f"{base}/metadata.json")
        with open(out, "w") as f:
            json.dump(data, f)
        sized = sum(len(v) for v in data.values() if isinstance(v, list))
        print(f"Saved: {out} ({sized} objects across {sum(1 for v in data.values() if isinstance(v, list))} types)")

    if not (args.metadata or args.schemas or args.types or args.all_types):
        print("Nothing fetched. Pass --metadata, --schemas, --types, or --all-types.",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
