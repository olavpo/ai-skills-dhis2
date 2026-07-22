#!/usr/bin/env python3
"""REVIEW PACK — evidence worksheets for the manual-review items the integrity checks can't automate.

Produces CSV candidate lists + an INDEX.md. READ-ONLY (GETs only); never fixes anything.
The sheets map to the official docs' "manual review" items — see references/manual-review.md for the
contract (worksheets, not fixes) and how to run a review session over them.

Sheets:
  duplicate_data_sources.csv       name-similar data element clusters + where each lives (datasets)
  category_totals.csv              categories whose disaggregation may not sum meaningfully
  dashboard_items.csv              dashboard items with fixed periods/OUs; dashboards without sharing
  orgunit_assignment.csv           dataset/program OU-assignment anomalies (admin levels, 0/all)
  sharing.csv                      dataset/program sharing anomalies (public r/w, none at all)
  indicator_formula_candidates.csv flag-only handoff to the dhis2-indicators skill

Usage:  python review_pack.py --out review-pack [--cap 200]
Env:    DHIS2_BASE_URL + DHIS2_API_TOKEN | DHIS2_USER/DHIS2_PASS
"""
import argparse, csv, os, re, itertools, collections
import httpx

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass


def client():
    base = os.environ["DHIS2_BASE_URL"].rstrip("/") + "/api"
    headers, auth = {"Accept": "application/json"}, None
    if os.environ.get("DHIS2_API_TOKEN"):
        headers["Authorization"] = f"ApiToken {os.environ['DHIS2_API_TOKEN']}"
    else:
        auth = (os.environ.get("DHIS2_USER", "admin"), os.environ.get("DHIS2_PASS", "district"))
    return httpx.Client(base_url=base, headers=headers, auth=auth, timeout=300)


def get(c, path, **params):
    params.setdefault("paging", "false")
    r = c.get(path, params=params)
    r.raise_for_status()
    return r.json()


STOP = {"of", "the", "and", "in", "at", "by", "for", "with", "to", "a", "no", "number", "total"}


def norm_tokens(name):
    toks = re.findall(r"[a-z0-9<>+%-]+", (name or "").lower())
    return frozenset(t for t in toks if t not in STOP)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# UNIT-AWARE range parsing: '0-27 days' and '1-4 years' must not compare as bare numbers
# (that false-flagged a clean neonatal/infant/child banding as "overlapping").
UNIT_DAYS = {"d": 1, "day": 1, "days": 1, "w": 7, "wk": 7, "week": 7, "weeks": 7,
             "m": 30.44, "mo": 30.44, "mth": 30.44, "mths": 30.44, "month": 30.44, "months": 30.44,
             "y": 365.25, "yr": 365.25, "yrs": 365.25, "year": 365.25, "years": 365.25}
NUM_UNIT = r"(\d+)\s*(days?|weeks?|wk|months?|mths?|mo|years?|yrs?|y|m|d|w)?"


def _days(n, unit, default_unit):
    return int(n) * UNIT_DAYS[unit if unit in UNIT_DAYS else default_unit]


def parse_range(name, default_unit="years"):
    """Interval in DAYS from an option name: '0-27 days', '28 days-11 months', '<5', '15+'.
    A missing unit inherits the other bound's unit, else default_unit (age bands read as years).
    Returns (lo, hi) in days, or None if no numeric range is present."""
    s = (name or "").lower()
    m = re.search(NUM_UNIT + r"\s*[-–]\s*" + NUM_UNIT, s)
    if m:
        lo, ulo, hi, uhi = m.groups()
        uhi = uhi or default_unit
        ulo = ulo or uhi
        return (_days(lo, ulo, default_unit), _days(hi, uhi, default_unit))
    m = re.search(r"<\s*" + NUM_UNIT, s)
    if m:
        return (0, _days(m.group(1), m.group(2) or default_unit, default_unit) - 1)
    m = re.search(NUM_UNIT + r"\s*\+", s) or re.search(r">\s*" + NUM_UNIT, s)
    if m:
        return (_days(m.group(1), m.group(2) or default_unit, default_unit), 10 ** 9)
    return None


def overlapping_pairs(named_ranges):
    """[(name, (lo,hi))...] -> list of (name_a, name_b) whose intervals overlap. Transparent output:
    the sheet names the exact offending pair so a reviewer can immediately judge a false positive."""
    rs = sorted([(r, n) for n, r in named_ranges if r])
    return [(rs[i][1], rs[i + 1][1]) for i in range(len(rs) - 1) if rs[i][0][1] >= rs[i + 1][0][0]]


def write_csv(outdir, name, header, rows, cap):
    path = os.path.join(outdir, name)
    capped = len(rows) > cap
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header + ["decision"])
        for r in rows[:cap]:
            w.writerow(list(r) + [""])
    return path, len(rows), capped


# --------------------------------------------------------------------- sheets
def sheet_duplicate_data_sources(c, cap):
    """Candidate duplicate DEs: identical normalized names, or high token overlap within a block."""
    des = get(c, "/dataElements", fields="id,name,domainType,dataSetElements[dataSet[id,name]]")["dataElements"]
    info = {}
    for d in des:
        ds = sorted({x["dataSet"]["name"] for x in d.get("dataSetElements", []) if x.get("dataSet")})
        info[d["id"]] = (d["name"], norm_tokens(d["name"]), "; ".join(ds)[:120], d.get("domainType", ""))
    # block by rarest token to avoid O(n^2) over everything
    tok_freq = collections.Counter(t for _, (nm, toks, _, _) in info.items() for t in toks)
    blocks = collections.defaultdict(list)
    for uid, (nm, toks, ds, dom) in info.items():
        if not toks:
            continue
        rare = min(toks, key=lambda t: tok_freq[t])
        blocks[rare].append(uid)
    pairs = []
    for uids in blocks.values():
        if len(uids) < 2 or len(uids) > 60:
            continue
        for a, b in itertools.combinations(uids, 2):
            sim = jaccard(info[a][1], info[b][1])
            if sim >= 0.8:
                pairs.append((round(sim, 2), a, info[a][0], info[a][2], b, info[b][0], info[b][2],
                              "same datasets" if info[a][2] == info[b][2] and info[a][2] else "different/none"))
    pairs.sort(key=lambda r: -r[0])
    return (["similarity", "uid_a", "name_a", "datasets_a", "uid_b", "name_b", "datasets_b", "where"], pairs)


def sheet_category_totals(c, cap):
    cats = get(c, "/categories", fields="id,name,dataDimensionType,categoryOptions[id,name]")["categories"]
    opt_owner = collections.defaultdict(list)
    for cat in cats:
        for o in cat.get("categoryOptions", []):
            opt_owner[o["id"]].append(cat["name"])
    rows = []
    for cat in cats:
        if cat["name"] == "default":
            continue
        opts = cat.get("categoryOptions", [])
        names = [o["name"] for o in opts]
        flags = []
        pairs = overlapping_pairs([(n, parse_range(n)) for n in names])
        if pairs:
            flags.append("overlapping ranges (double-counts on totals): " +
                         "; ".join(f"{a!r}∩{b!r}" for a, b in pairs[:3]))
        totalish = [n for n in names if re.search(r"\b(total|all|both|overall)\b", n, re.I)]
        if totalish:
            flags.append(f"total-like option(s): {', '.join(totalish[:3])} (sums option + total twice)")
        shared = [o["name"] for o in opts if len(opt_owner[o["id"]]) > 1]
        if len(shared) == len(opts) and opts:
            flags.append("ALL options shared with other categories (overlapping-banding design)")
        if len(opts) == 1:
            flags.append("single-option category (dimension adds nothing)")
        if flags:
            rows.append((cat["name"], len(opts), "; ".join(names)[:160], " | ".join(flags)))
    return (["category", "n_options", "options", "why_flagged"], rows)


def sheet_dashboard_items(c, cap):
    dashes = get(c, "/dashboards",
                 fields="id,name,sharing,dashboardItems[type,visualization[id,name,relativePeriods,periods~size,organisationUnits~size,userOrganisationUnit,userOrganisationUnitChildren]]")["dashboards"]
    rows = []
    for d in dashes:
        pub = (d.get("sharing") or {}).get("public", "")
        grp = len((d.get("sharing") or {}).get("userGroups") or {})
        if pub.startswith("--") and grp == 0:
            rows.append((d["name"], "(dashboard)", "no sharing at all — only the owner sees it"))
        for it in d.get("dashboardItems", []):
            v = it.get("visualization")
            if not v:
                continue
            rel = v.get("relativePeriods") or {}
            fixed_pe = (not any(rel.values())) and (v.get("periods", 0) or 0) > 0
            fixed_ou = ((v.get("organisationUnits", 0) or 0) > 0
                        and not v.get("userOrganisationUnit") and not v.get("userOrganisationUnitChildren"))
            why = []
            if fixed_pe:
                why.append("fixed periods (won't roll forward in time)")
            if fixed_ou:
                why.append("fixed org units (same view for every user)")
            if why:
                rows.append((d["name"], v.get("name", ""), " | ".join(why)))
    return (["dashboard", "item", "why_flagged"], rows)


def sheet_orgunit_assignment(c, cap):
    total_ou = get(c, "/organisationUnits", fields="id", pageSize=1, totalPages="true",
                   paging="true")["pager"]["total"]
    levels = {o["id"]: o["level"] for o in get(c, "/organisationUnits", fields="id,level")["organisationUnits"]}
    rows = []
    for typ in ("dataSets", "programs"):
        objs = get(c, f"/{typ}", fields="id,name,organisationUnits[id]")[typ]
        for o in objs:
            ous = [x["id"] for x in o.get("organisationUnits", [])]
            lv = collections.Counter(levels.get(u) for u in ous)
            admin = sum(n for l, n in lv.items() if l in (1, 2))
            why = []
            if not ous:
                why.append("assigned to 0 org units (unusable or archived?)")
            if admin:
                why.append(f"{admin} admin-level (1-2) assignments — data entry at national/province level?")
            if ous and len(ous) >= 0.98 * total_ou:
                why.append("assigned to ~ALL org units incl. non-facilities?")
            if why:
                rows.append((typ[:-1], o["name"], len(ous),
                             "; ".join(f"L{l}:{n}" for l, n in sorted(lv.items()) if l), " | ".join(why)))
    return (["type", "name", "n_orgunits", "levels", "why_flagged"], rows)


def sheet_sharing(c, cap):
    rows = []
    for typ in ("dataSets", "programs"):
        for o in get(c, f"/{typ}", fields="id,name,sharing")[typ]:
            sh = o.get("sharing") or {}
            pub = sh.get("public", "")
            ngrp = len(sh.get("userGroups") or {})
            why = []
            if pub[:4] == "rwrw":
                why.append("public READ-WRITE (anyone can edit data/metadata)")
            if pub.startswith("--") and ngrp == 0:
                why.append("no access configured at all (only owner/superusers)")
            if why:
                rows.append((typ[:-1], o["name"], pub, ngrp, " | ".join(why)))
    return (["type", "name", "public", "n_user_groups", "why_flagged"], rows)


def sheet_indicator_formula_candidates(c, cap):
    inds = get(c, "/indicators",
               fields="id,name,numerator,denominator,indicatorType[name,factor]")["indicators"]
    rows = []
    for i in inds:
        f = (i.get("indicatorType") or {}).get("factor")
        nm = i["name"]
        num, den = (i.get("numerator") or "").strip(), (i.get("denominator") or "").strip()
        why = []
        if re.search(r"%|percent|proportion|coverage rate", nm, re.I) and f not in (100,):
            why.append(f"name suggests %, indicatorType factor={f}")
        if f == 100 and re.search(r"\b(number|count|total)\b", nm, re.I) and "%" not in nm:
            why.append("factor=100 but name suggests a count")
        if num and num == den:
            why.append("numerator == denominator (always factor)")
        if den == "1" and re.search(r"\brate\b|%|\bper\b|\bproportion\b", nm, re.I):
            why.append("denominator is literal 1 but name suggests a rate")
        if why:
            rows.append((i["id"], nm, (i.get("indicatorType") or {}).get("name", ""), num[:60], den[:60],
                         " | ".join(why)))
    return (["uid", "name", "indicator_type", "numerator", "denominator", "why_flagged"], rows)


SHEETS = {
    "duplicate_data_sources.csv": (sheet_duplicate_data_sources,
        "Candidate duplicate data elements (name similarity ≥0.8, blocked by rarest token). Similarity ≠ duplicate — judge semantics; resolution often = form redesign with stakeholders."),
    "category_totals.csv": (sheet_category_totals,
        "Categories whose disaggregation may not sum to a meaningful total (overlapping ranges, total-like options, fully-shared option sets, single-option)."),
    "dashboard_items.csv": (sheet_dashboard_items,
        "Dashboard items using fixed periods/org units (stale or wrong for other users) and dashboards nobody else can see."),
    "orgunit_assignment.csv": (sheet_orgunit_assignment,
        "Dataset/program org-unit assignment anomalies: none, admin-level, or ~all org units. Country policy decides what is correct."),
    "sharing.csv": (sheet_sharing,
        "Dataset/program sharing anomalies: public read-write, or no access configured."),
    "indicator_formula_candidates.csv": (sheet_indicator_formula_candidates,
        "FLAG-ONLY handoff: candidates for semantic indicator-formula review. Do the actual review with the dhis2-indicators skill (authoring/validation is its domain)."),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="review-pack")
    ap.add_argument("--cap", type=int, default=200, help="max rows per sheet (cap is REPORTED, never silent)")
    ap.add_argument("--only", default="", help="comma-separated sheet names (default: all)")
    a = ap.parse_args()
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    os.makedirs(a.out, exist_ok=True)
    c = client()
    info = get(c, "/system/info")
    index = [f"# Review pack — {os.environ['DHIS2_BASE_URL']} (DHIS2 {info.get('version')})",
             "",
             "Evidence worksheets for the docs' MANUAL metadata-review items. **Candidates, not defects —",
             "and worksheets, not fixes.** Fill the `decision` column with the owner; decided fixes then",
             "flow through decisions.md into the fix manifest. See references/manual-review.md.", ""]
    for name, (fn, desc) in SHEETS.items():
        if only and name not in only:
            continue
        header, rows = fn(c, a.cap)
        path, n, capped = write_csv(a.out, name, header, rows, a.cap)
        capnote = f" — **CAPPED at {a.cap} of {n}**" if capped else ""
        index.append(f"- **{name}** ({min(n, a.cap)} rows{capnote}): {desc}")
        print(f"  {name}: {n} candidates{' (capped)' if capped else ''}")
    open(os.path.join(a.out, "INDEX.md"), "w").write("\n".join(index) + "\n")
    print("wrote", os.path.join(a.out, "INDEX.md"))


if __name__ == "__main__":
    main()
