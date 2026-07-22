#!/usr/bin/env python3
"""Export a complete DHIS2 metadata .json dump, and import it onto a target (sandbox) instance.

See references/dump-and-sandbox.md for the workflow and the import gotchas (default-UID collisions,
dependency ordering, sharing/users).

Export (from the source in your env vars):
  python metadata_dump.py export --out original.metadata.json
        [--skip-sharing] [--skip-users]

Import (into a target — pass its connection, or set env vars to point at it):
  python metadata_dump.py import --in fixed.metadata.json \
        --base http://dhis2-sandbox:8080 --user admin --pass district
        [--no-async]        # synchronous import for small dumps

Diff two dumps (reviewable record of what changed):
  python metadata_dump.py diff --a original.metadata.json --b fixed.metadata.json
"""
import sys, os, json, time, argparse
from d2_client import D2

VOLATILE = {"lastUpdated", "created", "href", "access", "lastUpdatedBy", "sharing", "user"}


def export(args):
    d2 = D2()
    params = {"download": "true"}
    if args.skip_sharing:
        params["skipSharing"] = "true"
    # /api/metadata.json returns every metadata type; large but a single document
    r = d2.get_raw("/metadata.json", **params)
    r.raise_for_status()
    data = r.json()
    if args.skip_users:
        for k in ("users", "userRoles", "userGroups"):
            data.pop(k, None)
    json.dump(data, open(args.out, "w"), indent=1)
    types = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    print(f"Exported {sum(types.values())} objects across {len(types)} types -> {args.out}")


def _target(args):
    return D2(base=args.base, user=args.user, password=getattr(args, "password", None) or args.__dict__.get("pass"))


def do_import(args):
    d2 = D2(base=args.base, user=args.user, password=args._pass)
    body = json.load(open(args.infile))
    params = {"importMode": "COMMIT", "atomicMode": "NONE", "identifier": "UID",
              "importStrategy": "CREATE_AND_UPDATE", "mergeMode": "REPLACE"}
    if not args.no_async:
        params["async"] = "true"
    print("Importing (pass 1)…")
    r = d2.client.post("/metadata", json=body, params=params, timeout=1800)
    print("  pass 1:", r.status_code, _summary(r))
    # second pass resolves forward references left by the first
    print("Importing (pass 2 — resolve forward refs)…")
    r2 = d2.client.post("/metadata", json=body, params=params, timeout=1800)
    print("  pass 2:", r2.status_code, _summary(r2))
    print("Review the import summary for residual errorReports.")


def _summary(r):
    try:
        j = r.json()
        st = j.get("status") or j.get("response", {}).get("status")
        stats = j.get("stats") or j.get("response", {}).get("stats")
        return f"status={st} stats={stats}"
    except Exception:
        return r.text[:120]


def _normalize(obj):
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in sorted(obj.items()) if k not in VOLATILE}
    if isinstance(obj, list):
        return [_normalize(x) for x in obj]
    return obj


def diff(args):
    a = json.load(open(args.a)); b = json.load(open(args.b))
    types = sorted(set(a) | set(b))
    for t in types:
        if not isinstance(a.get(t), list) and not isinstance(b.get(t), list):
            continue
        am = {o["id"]: _normalize(o) for o in a.get(t, []) if isinstance(o, dict) and "id" in o}
        bm = {o["id"]: _normalize(o) for o in b.get(t, []) if isinstance(o, dict) and "id" in o}
        added = set(bm) - set(am)
        removed = set(am) - set(bm)
        changed = [i for i in (set(am) & set(bm)) if am[i] != bm[i]]
        if added or removed or changed:
            print(f"\n## {t}: +{len(added)} -{len(removed)} ~{len(changed)}")
            for i in list(removed)[:20]:
                print(f"   - removed {i} {a and ''}")
            for i in list(added)[:20]:
                print(f"   + added   {i}")
            for i in changed[:20]:
                print(f"   ~ changed {i}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("export"); pe.add_argument("--out", required=True)
    pe.add_argument("--skip-sharing", action="store_true"); pe.add_argument("--skip-users", action="store_true")
    pe.set_defaults(func=export)
    pi = sub.add_parser("import"); pi.add_argument("--in", dest="infile", required=True)
    pi.add_argument("--base", required=True); pi.add_argument("--user", default="admin")
    pi.add_argument("--pass", dest="_pass", default="district"); pi.add_argument("--no-async", action="store_true")
    pi.set_defaults(func=do_import)
    pd = sub.add_parser("diff"); pd.add_argument("--a", required=True); pd.add_argument("--b", required=True)
    pd.set_defaults(func=diff)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
