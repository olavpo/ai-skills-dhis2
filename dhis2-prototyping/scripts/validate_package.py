#!/usr/bin/env python3
"""Validate every expression in a metadata package against a live DHIS2 instance.

    D2_AUTH=admin:district python3 validate_package.py https://server metadata/*.json

Checks program rule conditions and action expressions, program indicator
expressions and filters, indicator numerators/denominators, and program
category-mapping filters (2.42+), each through the server's own
.../description endpoint. Prints every invalid one and exits non-zero if any.
Run it after each import: the server's parser is the only authority, and
validating an expression is how you find out whether a function exists before
concluding the platform cannot do something.
"""
import json
import os
import sys


def main(url, files):
    try:
        import requests
    except ImportError:
        sys.exit("needs requests: pip install requests")
    s = requests.Session()
    s.auth = tuple(os.environ.get("D2_AUTH", "admin:district").split(":", 1))
    s.headers["Content-Type"] = "text/plain"
    meta = {}
    for f in files:
        for typ, objs in json.load(open(f)).items():
            meta.setdefault(typ, []).extend(objs)

    bad = checked = 0

    def check(path, expr, label, params=None):
        nonlocal bad, checked
        checked += 1
        resp = s.post(f"{url}/api/{path}/description", params=params or {}, data=expr.encode())
        try:
            j = resp.json()
        except ValueError:
            sys.exit(f"HTTP {resp.status_code} from {resp.url} is not JSON (wrong URL or D2_AUTH?):\n"
                     f"{resp.text[:200]}")
        if j.get("status") != "OK":
            bad += 1
            print(f"BAD {label}: {j.get('message') or j}\n    {expr[:300]}")

    actions = {a["id"]: a for a in meta.get("programRuleActions", [])}
    for r in meta.get("programRules", []):
        pid = r["program"]["id"]
        if r.get("condition"):
            check("programRules/condition", r["condition"], f"rule '{r['name']}' condition",
                  {"programId": pid})
        for a in r.get("programRuleActions", []):
            data = actions.get(a["id"], a).get("data")
            if data:
                check("programRuleActions/data/expression", data, f"rule '{r['name']}' action data",
                      {"programId": pid})
    for pi in meta.get("programIndicators", []):
        for kind in ("expression", "filter"):
            if pi.get(kind):
                check(f"programIndicators/{kind}", pi[kind], f"PI '{pi['name']}' {kind}")
    for ind in meta.get("indicators", []):
        for kind in ("numerator", "denominator"):
            if ind.get(kind):
                check("indicators/expression", ind[kind], f"indicator '{ind['name']}' {kind}")
    for prog in meta.get("programs", []):
        for cm in prog.get("categoryMappings", []):
            for om in cm.get("optionMappings", []):
                if om.get("filter"):
                    check("programIndicators/filter", om["filter"], f"mapping in '{prog['name']}'")
    print(f"checked {checked}, invalid {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1].rstrip("/"), sys.argv[2:]))
