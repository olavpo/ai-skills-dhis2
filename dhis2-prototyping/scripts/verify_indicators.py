#!/usr/bin/env python3
"""Check indicator values in analytics against ground truth computed from the
synthetic population.

    D2_URL=... D2_AUTH=user:pass python3 scripts/verify_indicators.py truth.json [--out report/verification.json]

The project's own code computes the ground truth from the same population it
loaded (see references/synthetic-data.md) and writes truth.json:

    {
      "window": {"startDate": "2026-01-01", "endDate": "2026-09-23"},   # or "pe": "2026"
      "ou": "<root org unit uid>",
      "checks": [
        {"option": "A", "name": "PCT_CASES", "dx": "<indicator/PI uid>", "expected": 23.4,
         "tolerance": 0.11}                                  # default 0.5; null expected = skip
      ],
      "disaggregations": [
        {"option": "A", "name": "cases by sex x age", "dx": "<uid>",
         "dimensions": ["<category uid>", "<category uid>"],
         "expected": {"<coUid>;<coUid>": 12, ...}}           # option uids in dimension order
      ],
      "unsupported": {"E": {"PCT_CASES": "no denominator in this design"}}   # copied to the output
    }

Restrict the window to the synthetic period once scenario entries exist, so manual
and scripted entry does not shift the totals; event-dated indicators count by event
date, enrollment-dated by enrollment date, so compute their truth the same way.
Indicators using V{current_date} are evaluated when analytics runs, so compute
their truth with today's date. Run analytics first; clear the cache
(POST /api/maintenance/cacheClear) after changing program indicators.

Exit code 1 when any check fails.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from d2http import load_json, session  # noqa: E402


def period_params(t):
    w = t.get("window", {})
    if "pe" in w:
        return [("filter", "pe:" + w["pe"])]
    return [("startDate", w["startDate"]), ("endDate", w["endDate"])]


def query(s, url, t, dx, dims=()):
    params = [("dimension", "dx:" + ";".join(dx)), ("filter", "ou:" + t["ou"]), ("skipMeta", "true")]
    params += period_params(t) + [("dimension", d) for d in dims]
    r = s.get(f"{url}/api/analytics", params=params, timeout=600)
    if not r.ok:
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    return r.json().get("rows", []), None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("truth")
    ap.add_argument("--url", help="overrides D2_URL")
    ap.add_argument("--option", action="append", help="only these options")
    ap.add_argument("--out")
    a = ap.parse_args()
    t = load_json(a.truth)
    s, url = session(a.url)

    checks = [c for c in t.get("checks", []) if not a.option or c.get("option") in a.option]
    results = collections.defaultdict(dict)
    bad = 0
    got = {}
    dxs = sorted({c["dx"] for c in checks})
    for i in range(0, len(dxs), 40):
        rows, err = query(s, url, t, dxs[i:i + 40])
        if err:
            print("query error:", err)
            bad += 1
            continue
        for dx, val in rows:
            got[dx] = float(val)
    for c in checks:
        exp, tol = c.get("expected"), c.get("tolerance", 0.5)
        g = got.get(c["dx"], 0.0)          # no row = no value = 0 for counts
        ok = exp is None or abs(g - exp) <= tol
        results[c.get("option", "")][c["name"]] = {"expected": exp, "got": g, "ok": ok}
        if not ok:
            bad += 1
            print(f"  {c.get('option', ''):4} {c['name']:24} expected {exp} got {g}")

    for dsg in t.get("disaggregations", []):
        if a.option and dsg.get("option") not in a.option:
            continue
        rows, err = query(s, url, t, [dsg["dx"]], dsg["dimensions"])
        if err:
            results[dsg.get("option", "")][dsg["name"]] = {"ok": False, "error": err}
            print(f"  {dsg.get('option', ''):4} {dsg['name']}: {err}")
            bad += 1
            continue
        n = len(dsg["dimensions"])
        got_d = {";".join(r[1:1 + n]): float(r[1 + n]) for r in rows}
        diff = {k: (v, got_d.get(k, 0.0)) for k, v in dsg["expected"].items()
                if abs(got_d.get(k, 0.0) - v) > dsg.get("tolerance", 0.5)}
        extra = {k: v for k, v in got_d.items() if k not in dsg["expected"] and v}
        ok = not diff and not extra
        results[dsg.get("option", "")][dsg["name"]] = {"ok": ok, "cells": len(got_d),
                                                        "mismatches": diff, "unexpected": extra}
        if not ok:
            bad += 1
            print(f"  {dsg.get('option', ''):4} {dsg['name']}: {len(diff)} cells differ, "
                  f"{len(extra)} unexpected; first: {(list(diff.items()) or list(extra.items()))[:3]}")

    for opt, res in sorted(results.items()):
        print(f"{opt or '-':4} {sum(r['ok'] for r in res.values())}/{len(res)} match")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"results": results, "unsupported": t.get("unsupported", {})}, f, indent=1,
                      ensure_ascii=False)
    print("mismatches:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
