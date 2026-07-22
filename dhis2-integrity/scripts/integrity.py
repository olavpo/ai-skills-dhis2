#!/usr/bin/env python3
"""Run DHIS2 data-integrity checks correctly (handles the async + cache traps).

The /api/dataIntegrity endpoints are asynchronous AND cached: a GET right after a POST returns the
PREVIOUS result, and the `summary` run skips the slow/programmatic checks. This tool waits on each
check's `finishedTime` actually changing, and runs the slow checks explicitly.

Usage:
  python integrity.py inventory                 # summary + all slow checks; prints nonzero by severity
  python integrity.py run <check1,check2,...>    # fresh-run specific checks, print counts
  python integrity.py details <check>            # fresh-run one check, dump its issues as JSON
  python integrity.py catalog                    # list every check with severity + isSlow

Outputs raw JSON to ./integrity/ for the record.
"""
import sys, os, json, time
from d2_client import D2

OUTDIR = "integrity"


def catalog(d2):
    return d2.get("/dataIntegrity")


def _finished_times(d2, kind, checks=None):
    path = f"/dataIntegrity/{kind}"
    params = {"checks": ",".join(checks)} if checks else {}
    data = d2.get(path, **params)
    return {k: v.get("finishedTime") for k, v in data.items()}, data


def fresh_run(d2, kind, checks, timeout=300):
    """Trigger a run and wait until every requested check's finishedTime moves."""
    before, _ = _finished_times(d2, kind, checks)
    params = {"checks": ",".join(checks)} if checks else None
    d2.client.post(f"/dataIntegrity/{kind}", params=params)
    deadline = time.time() + timeout
    while time.time() < deadline:
        now, data = _finished_times(d2, kind, checks)
        keys = checks or list(data.keys())
        if keys and all(k in data and now.get(k) != before.get(k) for k in keys):
            return data
        time.sleep(3)
    return data  # best effort


def inventory(d2):
    os.makedirs(OUTDIR, exist_ok=True)
    cat = catalog(d2)
    sev = {c["name"]: c["severity"] for c in cat}
    slow = [c["name"] for c in cat if c.get("isSlow") or c.get("isProgrammatic")]
    # summary covers the fast checks. Run it until two consecutive runs agree on the nonzero set —
    # a freshly-(re)started instance returns partial/stale counts for the first minute or two.
    def _nonzero_keys(s):
        return {k for k, v in s.items() if v.get("count")}
    summary = fresh_run(d2, "summary", None)
    for attempt in range(3):
        again = fresh_run(d2, "summary", None)
        if _nonzero_keys(again) == _nonzero_keys(summary):
            summary = again
            break
        added = _nonzero_keys(again) - _nonzero_keys(summary)
        removed = _nonzero_keys(summary) - _nonzero_keys(again)
        print(f"  ⚠️ inventory not yet stable (instance still settling?): "
              f"+{sorted(added)} -{sorted(removed)} — re-running…", file=__import__("sys").stderr)
        summary = again
    json.dump(summary, open(f"{OUTDIR}/summary.json", "w"), indent=2)
    # slow/programmatic checks must be triggered via details
    slow_data = {}
    if slow:
        slow_data = fresh_run(d2, "details", slow)
        json.dump(slow_data, open(f"{OUTDIR}/slow_details.json", "w"), indent=2)
    nz = {}
    for k, v in summary.items():
        if v.get("count"):
            nz[k] = (v["count"], sev.get(k))
    for k in slow:
        v = slow_data.get(k)
        if v and v.get("issues"):
            nz[k] = (len(v["issues"]), sev.get(k))
    order = {"CRITICAL": 0, "SEVERE": 1, "WARNING": 2, "INFO": 3}
    print(f"\n=== Nonzero checks ({len(nz)}) — instance {d2.base} ===")
    for k, (c, s) in sorted(nz.items(), key=lambda x: (order.get(x[1][1], 9), -x[1][0])):
        print(f"  {s:8} {c:6} {k}")
    print(f"\nRaw JSON saved under ./{OUTDIR}/")
    return nz


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    d2 = D2()
    cmd = sys.argv[1]
    if cmd == "catalog":
        for c in sorted(catalog(d2), key=lambda x: (x["severity"], x["name"])):
            flag = "slow" if (c.get("isSlow") or c.get("isProgrammatic")) else ""
            print(f"  {c['severity']:8} {flag:4} {c['name']}")
    elif cmd == "inventory":
        inventory(d2)
    elif cmd == "run":
        checks = sys.argv[2].split(",")
        # decide summary vs details per check
        cat = {c["name"]: c for c in catalog(d2)}
        slow = [c for c in checks if cat.get(c, {}).get("isSlow") or cat.get(c, {}).get("isProgrammatic")]
        fast = [c for c in checks if c not in slow]
        results = {}
        if fast:
            results.update(fresh_run(d2, "summary", None))
        if slow:
            results.update(fresh_run(d2, "details", slow))
        for c in checks:
            v = results.get(c, {})
            n = v.get("count", len(v.get("issues", []))) if v else "?"
            print(f"  {n:6} {c}")
    elif cmd == "details":
        check = sys.argv[2]
        data = fresh_run(d2, "details", [check])
        print(json.dumps(data.get(check, {}), indent=2))
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
