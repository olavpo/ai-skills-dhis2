#!/usr/bin/env python3
"""Delete scenario and test entries (not the synthetic population) before a final
recording pass. Dry run unless --delete is given.

    D2_URL=... D2_AUTH=user:pass python3 scripts/cleanup_scenario.py scenario-match.json [--delete]

scenario-match.json lists, per program, the attribute values that identify
scenario entries (the scenario's phone numbers and surnames for each platform,
plus names used while developing the flows):

    {"<program uid>": {"<attribute uid>": ["08012345678", "08112345678"],
                       "<attribute uid>": ["Testchild"]}}

The project's scenario module should write this file, so it always matches the
names the flows type. Run it before each final pass, never between flows of the
same pass. Deletion is soft: the UIDs cannot be reused, which does not matter for
entries created in Capture.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from d2http import as_json, load_json, session, tracker_errors  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("match")
    ap.add_argument("--delete", action="store_true", help="actually delete (default: list only)")
    ap.add_argument("--url", help="overrides D2_URL")
    a = ap.parse_args()
    s, url = session(a.url)
    found = {}
    for prog, attrs in load_json(a.match).items():
        for attr, values in attrs.items():
            for val in values:
                j = as_json(s.get(f"{url}/api/tracker/trackedEntities", params={
                    "program": prog, "filter": f"{attr}:eq:{val}", "ouMode": "ALL",
                    "fields": "trackedEntity", "pageSize": 200}))
                for te in j.get("trackedEntities", j.get("instances", [])):
                    found[te["trackedEntity"]] = f"{prog} {val}"
    print(len(found), "tracked entities match")
    for k, v in sorted(found.items(), key=lambda x: x[1]):
        print(" ", k, v)
    if not a.delete or not found:
        if found:
            print("dry run: pass --delete to remove them")
        return
    r = s.post(f"{url}/api/tracker", params={"async": "false", "importStrategy": "DELETE"},
               json={"trackedEntities": [{"trackedEntity": t} for t in found]})
    j = as_json(r)
    print(j.get("status"), j.get("stats"))
    for typ, uid, code, msg in list(tracker_errors(j))[:10]:
        # deleting tracked entities with enrollments needs the cascade-delete authorities
        print("  ", typ, uid, code, msg[:200])
    sys.exit(0 if j.get("status") == "OK" else 1)


if __name__ == "__main__":
    main()
