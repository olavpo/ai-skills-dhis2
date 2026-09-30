#!/usr/bin/env python3
"""Read back what an entry flow saved: tracked entities, enrollments, events and
relationships matching an attribute value, with codes instead of UIDs.

    D2_URL=... D2_AUTH=user:pass python3 scripts/check_entry.py --program <uid> --attr <uid> --value 08012345678

Run it after every scripted or manual flow: a "synced" status in Capture is not
proof that the server accepted the data. --code-prefix DEMO limits the code lookup
to the project's objects (codes are shown without the prefix).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from d2http import as_json, session  # noqa: E402

FIELDS = ("trackedEntity,orgUnit,attributes[attribute,value],"
          "enrollments[enrollment,status,enrolledAt,geometry,"
          "events[programStage,status,occurredAt,scheduledAt,dataValues[dataElement,value]]],"
          "relationships[relationshipType,from,to]")


def code_map(s, url, typ, prefix):
    params = {"fields": "id,code", "paging": "false"}
    if prefix:
        params["filter"] = f"code:like:{prefix}"
    out = {}
    for o in as_json(s.get(f"{url}/api/{typ}", params=params)).get(typ, []):
        c = o.get("code") or o["id"]
        out[o["id"]] = c[len(prefix) + 1:] if prefix and c.startswith(prefix + "_") else c
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--program", required=True)
    ap.add_argument("--attr", required=True, help="attribute uid to filter on")
    ap.add_argument("--value", required=True)
    ap.add_argument("--op", default="eq", help="filter operator (eq, like, ...)")
    ap.add_argument("--code-prefix")
    ap.add_argument("--url", help="overrides D2_URL")
    a = ap.parse_args()
    s, url = session(a.url)
    dn = code_map(s, url, "dataElements", a.code_prefix)
    tn = code_map(s, url, "trackedEntityAttributes", a.code_prefix)
    sn = code_map(s, url, "programStages", a.code_prefix)
    j = as_json(s.get(f"{url}/api/tracker/trackedEntities", params={
        "program": a.program, "filter": f"{a.attr}:{a.op}:{a.value}", "fields": FIELDS,
        "ouMode": "ACCESSIBLE", "pageSize": 50}))
    tes = j.get("trackedEntities", j.get("instances", []))
    if not tes:
        print("nothing found")
        sys.exit(1)
    for te in tes:
        print("TE", te["trackedEntity"], te.get("orgUnit"),
              {tn.get(x["attribute"], x["attribute"]): x["value"] for x in te.get("attributes", [])})
        for en in te.get("enrollments", []):
            print("  enrollment", en["status"], (en.get("enrolledAt") or "")[:10],
                  "geometry" if en.get("geometry") else "no geometry")
            for ev in en.get("events", []):
                print("    ", sn.get(ev["programStage"], ev["programStage"]), ev["status"],
                      (ev.get("occurredAt") or ev.get("scheduledAt") or "")[:10],
                      {dn.get(d["dataElement"], d["dataElement"]): d["value"] for d in ev.get("dataValues", [])})
        print("  relationships", len(te.get("relationships", [])))


if __name__ == "__main__":
    main()
