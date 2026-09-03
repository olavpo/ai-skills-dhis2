#!/usr/bin/env python3
"""Export ALL metadata from a DHIS2 instance SLOWLY (per type, paginated, throttled) and anonymize it.

NOTE: dhis2-metadata's `fetch_metadata.py --all-types --page-size 200 --delay 0.3 --anonymize`
now does the same job, and better — it excludes PII/sharing/translations server-side via
`fields=:owner,!email,...` so they never leave the server. Prefer it; this script is kept for
callers that depend on its single-file interface.

Walks each metadata type page-by-page with a delay between requests, so the load is spread out,
then strips PII so the dump is safe to take off-site / restore onto a sandbox.

The anonymization rules MIRROR the dhis2-metadata skill's `transform_metadata.py --anonymize` (the
canonical reference): strip email/phone/names/usernames/addresses everywhere, replace required user
fields with placeholders so user objects stay valid, and reduce createdBy/lastUpdatedBy to {id}.

Auth via env (same as the other scripts): DHIS2_BASE_URL + DHIS2_API_TOKEN, or DHIS2_USER/DHIS2_PASS.

Examples:
  python export_metadata_safe.py --out anon.metadata.json                 # slow export + anonymize
  python export_metadata_safe.py --out anon.metadata.json --keep-raw      # also keep the un-anonymized raw
  python export_metadata_safe.py --out anon.metadata.json --page-size 100 --delay 0.5
  python export_metadata_safe.py --out anon.metadata.json --redact-ou-names --drop-coordinates
  python export_metadata_safe.py --out raw.metadata.json --no-anonymize   # just the gentle export
"""
import os, sys, json, time, argparse
from d2_client import D2

# ---- anonymization config (mirrors dhis2-metadata --anonymize, expanded) -------------------------
PII_FIELDS = {
    "email", "phoneNumber", "firstName", "surname", "address", "contactPerson",
    "openId", "ldapId", "whatsApp", "facebookMessenger", "telegram", "twitter",
    "welcomeMessage", "jobTitle", "introduction", "nationality", "employer", "education",
    "interests", "languages", "birthday", "gender", "avatar", "idToken",
}
SECRET_FIELDS = {"password", "secret", "twoFactorSecret", "previousPasswords", "ldapId", "code"}
REDUCE_TO_ID = {"createdBy", "lastUpdatedBy", "user"}  # don't embed a whole (possibly PII) user inline


def anonymize_obj(obj, ou_names=False, drop_coords=False, top_type=None):
    """Recursively strip PII. `top_type` is the plural type name for top-level objects."""
    if isinstance(obj, list):
        return [anonymize_obj(x, ou_names, drop_coords) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if k in PII_FIELDS or (k in SECRET_FIELDS and k != "code"):
            continue
        if k in REDUCE_TO_ID and isinstance(v, dict) and "id" in v:
            out[k] = {"id": v["id"]}
            continue
        out[k] = anonymize_obj(v, ou_names, drop_coords)
    return out


def anonymize(data, ou_names=False, drop_coords=False):
    for plural, items in data.items():
        if not isinstance(items, list):
            continue
        for o in items:
            if not isinstance(o, dict):
                continue
            # generic PII strip + createdBy reduction
            cleaned = anonymize_obj(o, ou_names, drop_coords)
            o.clear(); o.update(cleaned)
            uid = o.get("id", "x")
            if plural == "users":
                o["firstName"] = "User"
                o["surname"] = uid
                o["username"] = f"user_{uid}"
                o.pop("userCredentials", None)
            if plural == "organisationUnits":
                for f in ("contactPerson", "address", "email", "phoneNumber"):
                    o.pop(f, None)
                if drop_coords:
                    o.pop("geometry", None); o.pop("coordinates", None)
                if ou_names:
                    o["name"] = f"OrgUnit {uid}"
                    o["shortName"] = f"OrgUnit {uid}"[:50]
    return data


# ---- slow per-type export ------------------------------------------------------------------------
def metadata_types(d2):
    schemas = d2.get("/schemas", fields="name,plural,metadata,relativeApiEndpoint")["schemas"]
    return sorted(s["plural"] for s in schemas
                  if s.get("metadata") and s.get("relativeApiEndpoint"))


def export(d2, types, page_size, delay):
    data = {}
    for plural in types:
        page, collected = 1, []
        while True:
            r = d2.get_raw(f"/{plural}.json", fields=":owner", paging="true",
                           pageSize=str(page_size), page=str(page), order="id:asc")
            if r.status_code != 200:
                print(f"  ! {plural}: HTTP {r.status_code} (skipped)", file=sys.stderr)
                break
            body = r.json()
            items = body.get(plural, [])
            collected.extend(items)
            pager = body.get("pager", {})
            pc = pager.get("pageCount", 1)
            print(f"  {plural}: page {page}/{pc} (+{len(items)}, total {len(collected)})", file=sys.stderr)
            if page >= pc or not items:
                break
            page += 1
            time.sleep(delay)
        if collected:
            data[plural] = collected
        time.sleep(delay)
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="Output file (anonymized metadata.json)")
    p.add_argument("--page-size", type=int, default=200, help="Objects per request (lower = gentler)")
    p.add_argument("--delay", type=float, default=0.3, help="Seconds to sleep between requests")
    p.add_argument("--types", default=None, help="Comma-separated subset of plural type names")
    p.add_argument("--exclude", default="", help="Comma-separated plural type names to skip")
    p.add_argument("--keep-raw", action="store_true", help="Also write <out>.raw.json before anonymizing")
    p.add_argument("--no-anonymize", action="store_true", help="Skip anonymization (raw export only)")
    p.add_argument("--exclude-users", action="store_true", help="Drop users/userGroups entirely")
    p.add_argument("--redact-ou-names", action="store_true", help="Replace org unit names with 'OrgUnit <id>'")
    p.add_argument("--drop-coordinates", action="store_true", help="Remove org unit geometry/coordinates")
    args = p.parse_args()

    d2 = D2()
    types = [t.strip() for t in args.types.split(",")] if args.types else metadata_types(d2)
    excl = {t.strip() for t in args.exclude.split(",") if t.strip()}
    if args.exclude_users:
        excl |= {"users", "userGroups"}
    types = [t for t in types if t not in excl]
    print(f"Exporting {len(types)} metadata types from {d2.base} "
          f"(pageSize={args.page_size}, delay={args.delay}s)…", file=sys.stderr)

    data = export(d2, types, args.page_size, args.delay)
    if args.keep_raw and not args.no_anonymize:
        raw = args.out.rsplit(".", 1)[0] + ".raw.json"
        json.dump(data, open(raw, "w"), indent=1)
        print(f"Raw (un-anonymized) written -> {raw}", file=sys.stderr)
    if not args.no_anonymize:
        data = anonymize(json.loads(json.dumps(data)), args.redact_ou_names, args.drop_coordinates)
    json.dump(data, open(args.out, "w"), indent=1)
    total = sum(len(v) for v in data.values() if isinstance(v, list))
    print(f"\nDone: {total} objects across {len(data)} types -> {args.out}"
          f"{'' if args.no_anonymize else ' (anonymized)'}")


if __name__ == "__main__":
    main()
