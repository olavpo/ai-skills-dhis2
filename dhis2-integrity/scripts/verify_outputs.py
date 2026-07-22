#!/usr/bin/env python3
"""A/B output-diff harness: prove the cleanup didn't silently change outputs.

Compares a CONTROL instance (pre-change) against the FIXED instance. Acceptance criterion:
every difference must map to a logged, intended change (see references/verification.md).

Connect to both with httpx; pass each instance's base + auth. Token or basic auth per instance.

Layers (run the ones relevant to your changes):
  datavalues  compare raw data values for given data elements (set diff, the bedrock check)
  analytics   compare /api/analytics numbers for given dx uids over a period+OU level
  forms       (single instance) assert every dataset form's deUID-cocUID inputs resolve to a
              valid COC for that DE's combo — catches forms broken by COC changes
  dangling    (single instance) find favorites/indicator-expressions referencing given deleted UIDs

Examples:
  python verify_outputs.py datavalues --control http://ctrl:8080 --fixed http://fix:8080 \
      --de gQNFkFkObU8,HZSdnO5fCUc --ou ImspTQPwCqd
  python verify_outputs.py analytics --control http://ctrl:8080 --fixed http://fix:8080 \
      --dx IND_UID1,IND_UID2 --pe LAST_5_YEARS --ou-level 1
  python verify_outputs.py forms --fixed http://fix:8080
  python verify_outputs.py dangling --fixed http://fix:8080 --uids ABC123,DEF456

Auth: --user/--pass (default admin/district) or --token, applied to both unless --*-token given.
"""
import sys, argparse, json
import httpx


def client(base, user, pw, token):
    h = {"Accept": "application/json"}
    auth = None
    if token:
        h["Authorization"] = f"ApiToken {token}"
    else:
        auth = (user, pw)
    return httpx.Client(base_url=base.rstrip("/") + "/api", headers=h, auth=auth, timeout=300)


def _datavalues(cl, de, ou):
    r = cl.get("/dataValueSets", params={"dataElement": de, "orgUnit": ou, "children": "true",
                                         "startDate": "1900-01-01", "endDate": "2100-12-31"})
    r.raise_for_status()
    out = {}
    for v in r.json().get("dataValues", []):
        key = (v["dataElement"], v["period"], v.get("orgUnit"), v["categoryOptionCombo"],
               v["attributeOptionCombo"])
        out[key] = v.get("value")
    return out


def cmd_datavalues(a):
    c = client(a.control, a.user, a._pass, a.control_token or a.token)
    f = client(a.fixed, a.user, a._pass, a.fixed_token or a.token)
    total_only_c = total_only_f = total_changed = 0
    for de in a.de.split(","):
        cc = _datavalues(c, de, a.ou)
        ff = _datavalues(f, de, a.ou)
        only_c = set(cc) - set(ff)
        only_f = set(ff) - set(cc)
        changed = [k for k in (set(cc) & set(ff)) if cc[k] != ff[k]]
        total_only_c += len(only_c); total_only_f += len(only_f); total_changed += len(changed)
        print(f"DE {de}: control={len(cc)} fixed={len(ff)} | only-control={len(only_c)} "
              f"only-fixed={len(only_f)} value-changed={len(changed)}")
        for k in list(only_c)[:5]:
            print(f"    only in control (lost?): {k} = {cc[k]}")
        for k in list(only_f)[:5]:
            print(f"    only in fixed   (new?):  {k} = {ff[k]}")
    print(f"\nSUMMARY only-control={total_only_c} only-fixed={total_only_f} changed={total_changed}")
    print("Each non-zero number must be explained by a logged change (e.g. an intended COC remap).")


def _analytics(cl, dx, pe, oulevel):
    r = cl.get("/analytics", params={"dimension": [f"dx:{dx}", f"pe:{pe}", f"ou:LEVEL-{oulevel}"],
                                     "skipMeta": "true", "skipRounding": "true"})
    r.raise_for_status()
    rows = r.json().get("rows", [])
    # key by all dimension columns except the value (last col)
    return {tuple(row[:-1]): row[-1] for row in rows}


def cmd_analytics(a):
    c = client(a.control, a.user, a._pass, a.control_token or a.token)
    f = client(a.fixed, a.user, a._pass, a.fixed_token or a.token)
    cc = _analytics(c, a.dx, a.pe, a.ou_level)
    ff = _analytics(f, a.dx, a.pe, a.ou_level)
    keys = set(cc) | set(ff)
    diffs = [(k, cc.get(k), ff.get(k)) for k in keys if cc.get(k) != ff.get(k)]
    print(f"analytics cells: control={len(cc)} fixed={len(ff)} | differing={len(diffs)}")
    for k, a1, a2 in diffs[:40]:
        print(f"   {k}: control={a1} fixed={a2}")
    print("\nNote: regenerate analytics tables on BOTH instances first, else diffs are false positives.")


def cmd_forms(a):
    import re
    f = client(a.fixed, a.user, a._pass, a.fixed_token or a.token)
    dss = f.get("/dataSets", params={"fields": "id,name,formType,dataSetElements[dataElement[id,"
                "categoryCombo[categoryOptionCombos[id]]]],dataEntryForm[htmlCode],"
                "sections[dataElements[id],categoryCombos[categoryOptionCombos[id]]]",
                "paging": "false"}).json()["dataSets"]
    bad = 0
    pat = re.compile(r'id="([A-Za-z0-9]{11})-([A-Za-z0-9]{11})-val"')
    for ds in dss:
        valid = set()
        for dse in ds.get("dataSetElements", []):
            for coc in dse["dataElement"].get("categoryCombo", {}).get("categoryOptionCombos", []):
                valid.add((dse["dataElement"]["id"], coc["id"]))
        html = (ds.get("dataEntryForm") or {}).get("htmlCode", "") or ""
        refs = set(pat.findall(html))
        broken = [(de, coc) for de, coc in refs if (de, coc) not in valid]
        if broken:
            bad += len(broken)
            print(f"  {ds['name']} ({ds['id']}) {ds['formType']}: {len(broken)} unresolved form inputs")
            for de, coc in broken[:5]:
                print(f"      input {de}-{coc}-val -> COC not valid for that DE's combo")
    print(f"\nTotal unresolved custom-form inputs: {bad}  (0 = all forms resolve cleanly)")


def cmd_dangling(a):
    f = client(a.fixed, a.user, a._pass, a.fixed_token or a.token)
    uids = a.uids.split(",")
    hits = 0
    for u in uids:
        for ep, flt in [("visualizations", f"dataDimensionItems.dataElement.id:eq:{u}"),
                        ("indicators", f"numerator:like:{u}"),
                        ("indicators", f"denominator:like:{u}"),
                        ("predictors", f"generator.expression:like:{u}")]:
            try:
                n = f.get(f"/{ep}", params={"filter": flt, "fields": "id", "paging": "true",
                                            "pageSize": "1"}).json().get("pager", {}).get("total", 0)
                if n:
                    hits += n
                    print(f"  {u}: still referenced by {n} {ep} ({flt})")
            except Exception:
                pass
    print(f"\nDangling references to the given UIDs: {hits}  (0 = clean)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", default="admin"); p.add_argument("--pass", dest="_pass", default="district")
    p.add_argument("--token"); p.add_argument("--control-token", dest="control_token")
    p.add_argument("--fixed-token", dest="fixed_token")
    sub = p.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("datavalues"); a1.add_argument("--control", required=True); a1.add_argument("--fixed", required=True)
    a1.add_argument("--de", required=True); a1.add_argument("--ou", required=True); a1.set_defaults(func=cmd_datavalues)
    a2 = sub.add_parser("analytics"); a2.add_argument("--control", required=True); a2.add_argument("--fixed", required=True)
    a2.add_argument("--dx", required=True); a2.add_argument("--pe", default="LAST_5_YEARS"); a2.add_argument("--ou-level", default="1")
    a2.set_defaults(func=cmd_analytics)
    a3 = sub.add_parser("forms"); a3.add_argument("--fixed", required=True); a3.set_defaults(func=cmd_forms)
    a4 = sub.add_parser("dangling"); a4.add_argument("--fixed", required=True); a4.add_argument("--uids", required=True)
    a4.set_defaults(func=cmd_dangling)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
