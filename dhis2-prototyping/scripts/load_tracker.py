#!/usr/bin/env python3
"""Load a synthetic tracker population in batches, with error reports.

    D2_URL=... D2_AUTH=user:pass python3 scripts/load_tracker.py data/population_A.json [--batch 40]

The input is what the project's population code writes (see
references/synthetic-data.md): a JSON object with any of

    "trackedEntities": [...]   full tracker payloads (enrollments and events nested)
    "relationships":   [...]
    "transfers":       [{"trackedEntity", "program", "orgUnit"}]   ownership transfers
    "geometry":        [{"enrollment", "lon", "lat"}]              see --geometry-sql

Tracked entities go in batches of --batch (40-100 work; 20 000 households,
221 595 objects, loaded in 5.5 minutes) to POST /api/tracker?async=false with
atomicMode=OBJECT, so one bad record does not drop its batch. Relationships go
after all tracked entities, 200 per request.

/api/tracker runs program rules on import: mandatory-field and error actions
reject records, and a rule-assigned value that is missing (E1019) or different
(E1309) rejects the enrollment. Compute every assigned value in the generator.

Include the seed in every UID key (data{SEED}:...): deleted tracked entities are
soft-deleted and their UIDs cannot be reused, so a test load with the same keys
blocks the real one.

It can also be imported as a library: `post_tracker`, `reserve_values`.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from d2http import as_json, load_json, session, tracker_errors  # noqa: E402


def post_tracker(s, url, payload, strategy="CREATE_AND_UPDATE"):
    r = s.post(f"{url}/api/tracker", params={"async": "false", "importStrategy": strategy,
                                             "atomicMode": "OBJECT", "reportMode": "ERRORS"},
               json=payload, timeout=900)
    j = as_json(r)
    return j.get("status"), j.get("stats") or {}, list(tracker_errors(j))


def reserve_values(s, url, attribute_uid, n=200, org_unit_code=None, expiration=60):
    """Reserve generated values (text-pattern attributes). ORG_UNIT_CODE(...) patterns need
    the org unit code; values for synthetic data should sit in a range real entry never
    reaches, because new sequences start at 00001 per pattern value."""
    params = {"numberToReserve": n, "expiration": expiration}
    if org_unit_code:
        params["ORG_UNIT_CODE"] = org_unit_code
    r = s.get(f"{url}/api/trackedEntityAttributes/{attribute_uid}/generateAndReserve", params=params)
    return [x["value"] for x in as_json(r)]


def show(prefix, errs, limit=5):
    for typ, uid, code, msg in errs[:limit]:
        print(f"  {prefix} {typ} {uid} {code} {msg[:200]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", help="population JSON file(s)")
    ap.add_argument("--url", help="overrides D2_URL")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--strategy", default="CREATE_AND_UPDATE", help="or DELETE to remove a load")
    ap.add_argument("--geometry-sql", metavar="FILE",
                    help="write UPDATE statements for 'geometry' rows instead of sending them; "
                         "enrollment geometry via /api/tracker was rejected with E1074 on 2.42.6. "
                         "Run the file with psql against the instance database.")
    a = ap.parse_args()
    s, url = session(a.url)
    grand = collections.Counter()
    failed = 0
    sql = []
    for path in a.files:
        data = load_json(path)
        tes = data.get("trackedEntities", [])
        totals = collections.Counter()
        for i in range(0, len(tes), a.batch):
            status, stats, errs = post_tracker(s, url, {"trackedEntities": tes[i:i + a.batch]}, a.strategy)
            totals.update(stats)
            show("ERR", errs)
            failed += len(errs)
        rels = data.get("relationships", [])
        for i in range(0, len(rels), 200):
            status, stats, errs = post_tracker(s, url, {"relationships": rels[i:i + 200]}, a.strategy)
            totals.update({"rel_" + k: v for k, v in stats.items()})
            show("REL", errs)
            failed += len(errs)
        moved = 0
        for t in data.get("transfers", []):
            r = s.put(f"{url}/api/tracker/ownership/transfer", params=t)
            moved += r.ok
            if not r.ok and moved == 0:
                print("  transfer failed", r.status_code, r.text[:200])
        for g in data.get("geometry", []):
            if not str(g["enrollment"]).isalnum():
                sys.exit(f"bad enrollment uid in geometry: {g['enrollment']!r}")
            sql.append(f"UPDATE enrollment SET geometry = ST_SetSRID(ST_MakePoint({float(g['lon'])}, "
                       f"{float(g['lat'])}), 4326) WHERE uid = '{g['enrollment']}';")
        print(os.path.basename(path), "TEs", len(tes), "rels", len(rels), "transfers", moved, dict(totals))
        grand.update(totals)
    if sql:
        if a.geometry_sql:
            with open(a.geometry_sql, "w") as f:
                f.write("\n".join(sql) + "\n")
            print(f"wrote {len(sql)} geometry updates to {a.geometry_sql}")
        else:
            print(f"{len(sql)} geometry rows ignored: pass --geometry-sql FILE")
    print("total", dict(grand), "errors", failed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
