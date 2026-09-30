#!/usr/bin/env python3
"""Generate synthetic data for a DHIS2 instance so metadata-integrity fixes are
realistically exercised (empty DBs never trip the E1120/E4030 data guards, and
give nothing to A/B-diff).

Coverage goal: "a few values per data element" — every aggregate DE gets a value
under EACH of its category option combos (so structural COC/combo fixes become
real data-migration exercises, incl. disjoint COCs), at a small sample of
org-units x periods; every tracker program gets a handful of enrolled TEIs with
events across all stages filling program-stage data elements.

Deterministic (seeded) so re-runs reproduce the same fixture. Aggregate values go
via /api/dataValueSets (chunked); tracker via /api/tracker (async, polled).

Env: DHIS2_BASE_URL + DHIS2_AUTH (user:pass or token:XXX), or --url/--auth. The d2_client.py
convention (DHIS2_API_TOKEN, or DHIS2_USER + DHIS2_PASS) is accepted too, so one .env serves both.
"""
import argparse, os, sys, json, time, random, hashlib
import requests
from requests.auth import HTTPBasicAuth

VT_NUMERIC = {"INTEGER","INTEGER_POSITIVE","INTEGER_ZERO_OR_POSITIVE","INTEGER_NEGATIVE",
              "NUMBER","UNIT_INTERVAL","PERCENTAGE"}

def seeded(*parts):
    """Stable pseudo-random int from a key (so a given cell always gets same value)."""
    h = hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:8], 16)

def value_for(vt, optionset_codes, key, has_optionset=False):
    r = seeded(key)
    if has_optionset:
        # DE/TEA constrained to an option set: only a real code is valid.
        # Empty option set (itself an integrity issue) -> no valid value -> skip.
        if not optionset_codes:
            return None
        return optionset_codes[r % len(optionset_codes)]
    if optionset_codes:
        return optionset_codes[r % len(optionset_codes)]
    if vt in ("INTEGER_ZERO_OR_POSITIVE","INTEGER_POSITIVE","INTEGER"):
        return str(r % 100 + 1)
    if vt == "INTEGER_NEGATIVE":
        return str(-(r % 100 + 1))
    if vt in ("NUMBER",):
        return str(round((r % 10000) / 100.0, 2))
    if vt == "UNIT_INTERVAL":
        return str(round((r % 100) / 100.0, 2))
    if vt == "PERCENTAGE":
        return str(r % 101)
    if vt in ("BOOLEAN","TRUE_ONLY"):
        return "true"
    if vt in ("DATE","DATETIME"):
        d = f"20{20+(r%5)}-{(r%12)+1:02d}-{(r%27)+1:02d}"
        return d + ("T09:00:00.000" if vt=="DATETIME" else "")
    if vt == "TIME":
        return f"{r%24:02d}:{r%60:02d}"
    if vt in ("TEXT","LONG_TEXT","MULTI_TEXT"):
        return f"SYN-{r%1000}"
    if vt == "PHONE_NUMBER":
        return f"07{r%100000000:08d}"
    if vt == "EMAIL":
        return f"syn{r%10000}@example.org"
    if vt == "URL":
        return f"https://example.org/{r%10000}"
    if vt in ("AGE",):
        return f"20{20+(r%5)}-01-01"
    if vt == "COORDINATE":
        return f"[{round((r%360)-180,3)},{round((r%180)-90,3)}]"
    # ORGANISATION_UNIT, USERNAME, FILE_RESOURCE, IMAGE, etc. -> skip (return None)
    return None


class D2:
    def __init__(self, base, auth):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        if auth.startswith("token:"):
            self.s.headers["Authorization"] = f"ApiToken {auth[6:]}"
        else:
            u,_,p = auth.partition(":"); self.s.auth = HTTPBasicAuth(u,p)
    def get(self, path, **params):
        params.setdefault("paging","false")
        r = self.s.get(f"{self.base}/api/{path}", params=params, timeout=300); r.raise_for_status()
        return r.json()
    def post(self, path, payload, **params):
        r = self.s.post(f"{self.base}/api/{path}", params=params, json=payload, timeout=600)
        return r


def recent_periods(period_type, n):
    """A few recent periods for the given DHIS2 period type (fixed base year for reproducibility)."""
    Y = 2024
    pt = (period_type or "Monthly").lower()
    if pt == "yearly":   return [str(Y-i) for i in range(n)]
    if pt == "monthly":  return [f"{Y}{m:02d}" for m in range(1, n+1)]
    if pt == "quarterly":return [f"{Y}Q{q}" for q in range(1, n+1)]
    if pt == "weekly":   return [f"{Y}W{w}" for w in range(1, n+1)]
    if pt == "daily":    return [f"{Y}0{1}{d:02d}" for d in range(1, n+1)]
    if pt in ("sixmonthly",): return [f"{Y}S{s}" for s in range(1, min(n,2)+1)]
    if pt in ("financialjuly","financialoct","financialapril"):
        suf = {"financialjuly":"July","financialoct":"Oct","financialapril":"April"}[pt]
        return [f"{Y-i}{suf}" for i in range(n)]
    return [f"{Y}{m:02d}" for m in range(1, n+1)]


def gen_aggregate(d2, n_ou, n_period, chunk, optionset_cache, dry):
    print("== AGGREGATE ==", flush=True)
    ds = d2.get("dataSets.json",
                fields="id,name,periodType,categoryCombo[id],"
                       "dataSetElements[dataElement[id,valueType,categoryCombo[id],optionSet[id]],categoryCombo[id]],"
                       "organisationUnits[id]")["dataSets"]
    combos = {c["id"]: c for c in d2.get("categoryCombos.json",
              fields="id,categoryOptionCombos[id]")["categoryCombos"]}
    def coc_ids(combo_id):
        return [c["id"] for c in combos.get(combo_id, {}).get("categoryOptionCombos", [])]
    def opts(os_id):
        if os_id not in optionset_cache:
            o = d2.get(f"optionSets/{os_id}.json", fields="options[code]")
            optionset_cache[os_id] = [x["code"] for x in o.get("options",[]) if x.get("code") is not None]
        return optionset_cache[os_id]

    values = []
    total = 0
    for dset in ds:
        ous = [o["id"] for o in dset.get("organisationUnits",[])][:n_ou]
        if not ous:
            continue
        periods = recent_periods(dset.get("periodType"), n_period)
        aocs = coc_ids(dset["categoryCombo"]["id"]) or ["__default__"]
        aoc = aocs[0]  # one attribute combo to bound volume
        for dse in dset.get("dataSetElements",[]):
            de = dse["dataElement"]
            cc = (dse.get("categoryCombo") or de.get("categoryCombo") or {}).get("id")
            cocs = coc_ids(cc) if cc else []
            if not cocs:
                continue
            has_os = bool(de.get("optionSet"))
            os_codes = opts(de["optionSet"]["id"]) if has_os else None
            for ou in ous:
                for per in periods:
                    for coc in cocs:
                        v = value_for(de["valueType"], os_codes, (de["id"],ou,per,coc), has_optionset=has_os)
                        if v is None:
                            continue
                        dv = {"dataElement":de["id"],"period":per,"orgUnit":ou,
                              "categoryOptionCombo":coc,"value":v}
                        if aoc != "__default__":
                            dv["attributeOptionCombo"] = aoc
                        values.append((dset["id"], dv))
    print(f"  built {len(values)} aggregate data values", flush=True)
    if dry:
        return
    # One payload per dataset, with "dataSet" set: from 2.43 a data element in several datasets is
    # rejected ("Data set detection failed, found multiple sets") unless the payload names its dataset.
    by_ds = {}
    for ds_id, dv in values:
        by_ds.setdefault(ds_id, []).append(dv)
    sent, n = 0, 0
    for ds_id, dvs in by_ds.items():
        for i in range(0, len(dvs), chunk):
            batch = dvs[i:i+chunk]; n += 1
            r = d2.post("dataValueSets.json", {"dataSet": ds_id, "dataValues": batch},
                        importStrategy="CREATE_AND_UPDATE", skipAudit="true")
            try:
                j = r.json(); resp = j.get("response", j)
                ic = resp.get("importCount") or {}
                conflicts = resp.get("conflicts") or []
            except Exception:
                ic, conflicts = {"raw": r.text[:200]}, []
            sent += len(batch)
            print(f"  dvset {n} (dataSet {ds_id}): HTTP {r.status_code} {ic}  ({sent}/{len(values)})", flush=True)
            for c in conflicts[:3]:
                print(f"    conflict: {c.get('errorCode','')} {str(c.get('value') or c.get('object',''))[:160]}", flush=True)


def gen_tracker(d2, n_tei, dry):
    print("== TRACKER ==", flush=True)
    progs = d2.get("programs.json", fields="id,name,programType,trackedEntityType[id],"
                   "categoryCombo[id,categoryOptionCombos[id]],"
                   "programTrackedEntityAttributes[trackedEntityAttribute[id,valueType,optionSet[id],unique,generated]],"
                   "programStages[id,programStageDataElements[dataElement[id,valueType,optionSet[id]]]],"
                   "organisationUnits[id]")["programs"]
    optcache = {}
    def opts(os_id):
        if os_id not in optcache:
            o = d2.get(f"optionSets/{os_id}.json", fields="options[code]")
            optcache[os_id] = [x["code"] for x in o.get("options",[])]
        return optcache[os_id]

    import os.path
    rt = {"de":set(),"tea":set()}
    rtf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs")
    # caller may drop a rule_targets.json next to this run; best-effort
    for cand in ("rule_targets.json", os.environ.get("RULE_TARGETS","")):
        if cand and os.path.exists(cand):
            j=json.load(open(cand)); rt={"de":set(j.get("de",[])),"tea":set(j.get("tea",[]))}
            print(f"  skipping {len(rt['de'])} rule-target DEs, {len(rt['tea'])} TEAs", flush=True)
            break
    payload = {"trackedEntities":[]}
    for p in progs:
        ptype = p.get("programType")
        ous = [o["id"] for o in p.get("organisationUnits",[])][:3]
        if not ous:
            print(f"  program {p['name']!r}: no org units, skipping", flush=True); continue
        tet = (p.get("trackedEntityType") or {}).get("id")
        if not tet:
            # event programs (WITHOUT_REGISTRATION) have no TET: nesting them under trackedEntities fails
            # server-side ("TrackedEntity.getTrackedEntityType() is null") and sinks the whole job
            print(f"  program {p['name']!r} ({ptype}): no tracked entity type, skipping", flush=True); continue
        aoc = None
        cc = p.get("categoryCombo") or {}
        cocs = [c["id"] for c in cc.get("categoryOptionCombos",[])]
        if cocs: aoc = cocs[0]
        for i in range(n_tei):
            ou = ous[i % len(ous)]
            enr_date = f"2024-0{(i%9)+1}-15"
            # events for each stage
            events = []
            for stg in p.get("programStages",[]):
                dvs = []
                for psde in stg.get("programStageDataElements",[]):
                    de = psde["dataElement"]
                    if de["id"] in rt["de"]:
                        continue
                    has_os = bool(de.get("optionSet"))
                    oc = opts(de["optionSet"]["id"]) if has_os else None
                    v = value_for(de["valueType"], oc, (de["id"], p["id"], i), has_optionset=has_os)
                    if v is not None:
                        dvs.append({"dataElement":de["id"],"value":v})
                ev = {"programStage":stg["id"],"program":p["id"],"orgUnit":ou,
                      "occurredAt":enr_date,"status":"COMPLETED","dataValues":dvs}
                if aoc: ev["attributeOptionCombo"]=aoc
                events.append(ev)
            # TEA attribute values (skip unique/generated to avoid collisions)
            attrs = []
            for pa in p.get("programTrackedEntityAttributes",[]):
                ta = pa["trackedEntityAttribute"]
                if ta.get("unique") or ta.get("generated") or ta["id"] in rt["tea"]:
                    continue
                has_os = bool(ta.get("optionSet"))
                oc = opts(ta["optionSet"]["id"]) if has_os else None
                v = value_for(ta["valueType"], oc, (ta["id"], p["id"], i), has_optionset=has_os)
                if v is not None:
                    attrs.append({"attribute":ta["id"],"value":v})
            enrollment = {"program":p["id"],"orgUnit":ou,"enrolledAt":enr_date,
                          "occurredAt":enr_date,"status":"ACTIVE","events":events}
            te = {"orgUnit":ou,"trackedEntityType":tet,"attributes":attrs,
                  "enrollments":[enrollment]}
            payload["trackedEntities"].append(te)
        print(f"  program {p['name']!r} ({ptype}): staged {n_tei} TEIs x {len(p.get('programStages',[]))} stages", flush=True)
    n = len(payload["trackedEntities"])
    print(f"  built {n} tracked entities", flush=True)
    if dry or n==0:
        return
    r = d2.post("tracker.json", payload, async_="true", importStrategy="CREATE_AND_UPDATE", validationMode="SKIP", skipSideEffects="true")
    try:
        j = r.json()
    except Exception:
        print("  tracker POST raw:", r.text[:300], flush=True); return
    # poll job
    loc = (j.get("response",{}) or {}).get("id") or j.get("id")
    if not loc:
        print("  tracker import (sync?):", json.dumps(j)[:300], flush=True); return
    print(f"  tracker job {loc} polling…", flush=True)
    for _ in range(120):
        time.sleep(3)
        st = d2.get(f"tracker/jobs/{loc}.json")
        if isinstance(st, list):
            done = any(x.get("completed") for x in st) or any("completed" in str(x.get("message","")).lower() for x in st)
            last = st[-1] if st else {}
            if done or last.get("completed"):
                break
    rr = d2.s.get(f"{d2.base}/api/tracker/jobs/{loc}/report.json", timeout=300)
    if rr.status_code != 200:
        # a job that died server-side has no report (404) — its notifications are the only record
        print(f"  tracker report HTTP {rr.status_code} — job log:", flush=True)
        for x in (st if isinstance(st, list) else [])[:10]:
            print(f"    {x.get('level','')} {str(x.get('message',''))[:200]}", flush=True)
        return
    rep = rr.json()
    st = rep.get("status"); stats = rep.get("stats")
    print(f"  tracker import status={st} stats={stats}", flush=True)
    ve = rep.get("validationReport",{}).get("errorReports",[])
    if ve:
        seen=set()
        for e in ve:
            k=e.get("errorCode")
            if k not in seen:
                seen.add(k); print(f"    {k}: {e.get('message','')[:160]}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("DHIS2_BASE_URL"))
    env_auth = (os.environ.get("DHIS2_AUTH")
                or (f"token:{os.environ['DHIS2_API_TOKEN']}" if os.environ.get("DHIS2_API_TOKEN") else None)
                or (f"{os.environ['DHIS2_USER']}:{os.environ.get('DHIS2_PASS','')}" if os.environ.get("DHIS2_USER") else None))
    ap.add_argument("--auth", default=env_auth)
    ap.add_argument("--ous", type=int, default=3, help="aggregate: org units per dataset")
    ap.add_argument("--periods", type=int, default=2, help="aggregate: periods per dataset")
    ap.add_argument("--teis", type=int, default=5, help="tracker: TEIs per program")
    ap.add_argument("--chunk", type=int, default=10000)
    ap.add_argument("--skip-aggregate", action="store_true")
    ap.add_argument("--skip-tracker", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.url or not a.auth:
        ap.error("need --url/--auth or DHIS2_BASE_URL + DHIS2_AUTH")
    d2 = D2(a.url, a.auth)
    info = d2.get("system/info.json")
    print(f"Connected {d2.base} (DHIS2 {info.get('version')})", flush=True)
    oc = {}
    if not a.skip_aggregate:
        gen_aggregate(d2, a.ous, a.periods, a.chunk, oc, a.dry_run)
    if not a.skip_tracker:
        gen_tracker(d2, a.teis, a.dry_run)
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
