#!/usr/bin/env python3
"""Run DHIS2 data-integrity checks correctly (handles the async + cache traps).

The /api/dataIntegrity endpoints are asynchronous AND cached: a GET right after a POST returns the
PREVIOUS result, and the `summary` run skips the slow/programmatic checks. This tool waits on each
check's `finishedTime` actually changing, runs the slow checks explicitly, and refuses to report an
incomplete run as a result: a timed-out or near-empty summary (e.g. after a cacheClear, or with
DATA_INTEGRITY jobs stuck in SCHEDULED) exits non-zero instead of printing a false all-clear.

Usage:
  python integrity.py inventory                 # summary + all slow checks; prints nonzero by severity
  python integrity.py run <check1,check2,...>    # fresh-run specific checks, print counts
  python integrity.py details <check>            # fresh-run one check, dump its issues as JSON
  python integrity.py catalog                    # list every check with severity + isSlow

Outputs raw JSON to ./integrity/ for the record.
"""
import sys, os, json, time
from d2_client import D2


class IncompleteRun(RuntimeError):
    pass

OUTDIR = "integrity"


def catalog(d2):
    return d2.get("/dataIntegrity")


def _finished_times(d2, kind, checks=None):
    path = f"/dataIntegrity/{kind}"
    params = {"checks": ",".join(checks)} if checks else {}
    data = d2.get(path, **params)
    return {k: v.get("finishedTime") for k, v in data.items()}, data


def stuck_jobs(d2):
    """DATA_INTEGRITY jobs queued but never started. On 2.43.1 a cacheClear while jobs are queued leaves
    every later run SCHEDULED/NOT_STARTED until Tomcat restarts; the summary then just returns {}."""
    try:
        jobs = d2.get("/jobConfigurations", filter="jobType:eq:DATA_INTEGRITY",
                      fields="id,jobStatus,lastExecutedStatus", paging="false").get("jobConfigurations", [])
    except Exception:
        return None
    running = [j for j in jobs if j.get("jobStatus") == "RUNNING"]
    queued = [j for j in jobs if j.get("jobStatus") == "SCHEDULED" and j.get("lastExecutedStatus") == "NOT_STARTED"]
    return queued if queued and not running else []


def fresh_run(d2, kind, checks, timeout=300, expect=None):
    """Trigger a run and wait until every requested check's finishedTime moves.
    `expect` = check names a no-`checks` summary run must return. Raises IncompleteRun (with the partial
    data attached) on timeout, never returning stale or partial results as if they were fresh."""
    # read with explicit names whenever they are known: on 2.40 a bare GET /dataIntegrity/summary
    # returns {} even after the run completes
    read = checks or expect
    before, _ = _finished_times(d2, kind, read)
    params = {"checks": ",".join(checks)} if checks else None
    d2.client.post(f"/dataIntegrity/{kind}", params=params)
    deadline = time.time() + timeout
    keys = checks or expect or list(before.keys())
    data = {}
    while time.time() < deadline:
        now, data = _finished_times(d2, kind, read)
        moved = [k for k in keys if k in data and now.get(k) != before.get(k)]
        if keys and len(moved) == len(keys):
            return data
        # a summary may legitimately omit a few checks (errored server-side); accept it once everything
        # it returned is fresh and it covers >= 90% of what was expected — the caller lists the gaps
        if expect and not checks and moved and len(moved) >= 0.9 * len(keys) \
                and all(now.get(k) != before.get(k) for k in data if k in keys):
            return data
        if not keys and data and all(now.get(k) != before.get(k) for k in data):
            return data
        time.sleep(3)
    missing = [k for k in keys if k not in data or data[k].get("finishedTime") == before.get(k)]
    msg = f"{kind} run did not complete in {timeout}s: {len(missing)} of {len(keys)} checks not refreshed"
    stuck = stuck_jobs(d2)
    if stuck:
        msg += (f"; {len(stuck)} DATA_INTEGRITY jobs are SCHEDULED/NOT_STARTED with none RUNNING — the "
                "scheduler is stuck (seen after cacheClear on 2.43.1); restart Tomcat and re-run")
    err = IncompleteRun(msg)
    err.data, err.missing = data, missing
    raise err


def inventory(d2):
    os.makedirs(OUTDIR, exist_ok=True)
    cat = catalog(d2)
    sev = {c["name"]: c["severity"] for c in cat}
    slow = [c["name"] for c in cat if c.get("isSlow") or c.get("isProgrammatic")]
    fast = [c["name"] for c in cat if c["name"] not in slow]
    stuck = stuck_jobs(d2)
    if stuck:
        sys.exit(f"⛔ {len(stuck)} DATA_INTEGRITY jobs are SCHEDULED/NOT_STARTED and none RUNNING — the "
                 "scheduler is stuck (seen after cacheClear on 2.43.1). Restart Tomcat, then re-run.")
    # summary covers the fast checks. Run it until two consecutive runs agree on the nonzero set —
    # a freshly-(re)started instance returns partial/stale counts for the first minute or two.
    def _nonzero_keys(s):
        return {k for k, v in s.items() if v.get("count")}
    try:
        summary = fresh_run(d2, "summary", None, expect=fast)
        for attempt in range(3):
            again = fresh_run(d2, "summary", None, expect=fast)
            if _nonzero_keys(again) == _nonzero_keys(summary):
                summary = again
                break
            added = _nonzero_keys(again) - _nonzero_keys(summary)
            removed = _nonzero_keys(summary) - _nonzero_keys(again)
            print(f"  ⚠️ inventory not yet stable (instance still settling?): "
                  f"+{sorted(added)} -{sorted(removed)} — re-running…", file=sys.stderr)
            summary = again
        else:
            sys.exit("⛔ summary never stabilised over 4 runs — not reporting it as an inventory.")
    except IncompleteRun as e:
        sys.exit(f"⛔ NOT an inventory: {e}. A partial summary is not an all-clear.")
    json.dump(summary, open(f"{OUTDIR}/summary.json", "w"), indent=2)
    not_run = [k for k in fast if k not in summary]
    # slow/programmatic checks must be triggered via details
    slow_data = {}
    if slow:
        try:
            slow_data = fresh_run(d2, "details", slow, timeout=900)
        except IncompleteRun as e:
            slow_data = e.data
            not_run += e.missing
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
    if not_run:
        print(f"\n⚠️ {len(not_run)} checks did NOT complete — unknown, not zero:")
        for k in not_run:
            print(f"  {sev.get(k, '?'):8} {'?':>6} {k}")
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
        unknown = [c for c in checks if c not in cat]
        if unknown:
            sys.exit(f"⛔ unknown check name(s) on this version: {unknown} — `catalog` lists the valid ones")
        try:
            if fast:
                results.update(fresh_run(d2, "summary", None, expect=fast))
            if slow:
                results.update(fresh_run(d2, "details", slow))
        except IncompleteRun as e:
            sys.exit(f"⛔ {e}")
        for c in checks:
            v = results.get(c, {})
            n = v.get("count", len(v.get("issues", []))) if v else "?"
            print(f"  {n:6} {c}")
    elif cmd == "details":
        check = sys.argv[2]
        if check not in {c["name"] for c in catalog(d2)}:
            sys.exit(f"⛔ unknown check name on this version: {check!r} — `catalog` lists the valid ones")
        try:
            data = fresh_run(d2, "details", [check])
        except IncompleteRun as e:
            sys.exit(f"⛔ {e}")
        print(json.dumps(data.get(check, {}), indent=2))
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
