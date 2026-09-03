"""Import per-type metadata files into a DHIS2 instance in dependency order.

Reads a directory of `<plural>.json` files (each containing an array of
objects, as produced by fetch_metadata.py --split or transform_metadata.py), wraps each into a `{plural: [...]}`
payload, and POSTs them to /api/metadata one type at a time.

Why per-type and not one big payload?
- Easier to diagnose which type a server-side error came from.
- A 500 on one type does not block others.
- You can re-run only the failed types after fixing them.

Why a fixed dependency order?
- DHIS2 references resolve at write time. With atomicMode=NONE missing
  references become per-row errors instead of aborting the whole batch,
  but it is still preferable to import dependencies first so they exist
  when dependents arrive.

Large imports (tens of thousands of org units and up):
- `--chunk-size 5000` splits big types into batches; organisationUnits are
  additionally ordered shallow-first (by hierarchy level) so a child's
  parent always exists before the child arrives.
- Batches above `--async-threshold` objects POST with async=true and poll
  the import task, avoiding request timeouts on slow imports.
- `--resume` skips objects whose UID already exists on the server, making
  a crashed bulk load re-runnable without redoing finished batches.
- Transient connection errors are retried with exponential backoff.

Adapting across DHIS2 versions:
- The dependency order is mostly stable across 2.40-2.43, but new types
  appear (e.g. trackerDataView*, route, etc.) and a few are renamed.
- Unknown types are appended at the end of the order. If you see ordering
  problems, edit the ORDER list at the top.
- Types not present in the source directory are skipped silently.
"""
import argparse
import json
import os
import sys
import time
import requests
from requests.auth import HTTPBasicAuth

# Dependency-aware order (rough levels). Anything not listed is appended.
ORDER = [
    "attributes",
    "constants",
    "legendSets",
    "userRoles",
    "optionGroupSets", "categoryOptionGroupSets", "indicatorGroupSets",
    "options", "optionSets", "optionGroups",
    "categoryOptions", "categoryOptionGroups",
    "categories",
    "categoryCombos",
    "categoryOptionCombos",
    "organisationUnitLevels",
    "organisationUnits",  # before groups: groups own member refs to org units
    "organisationUnitGroups", "organisationUnitGroupSets",
    "trackedEntityAttributes", "trackedEntityTypes", "relationshipTypes",
    "indicatorTypes",
    "dataElements", "dataElementGroups", "dataElementGroupSets",
    "indicators", "indicatorGroups",
    "validationRules", "validationRuleGroups", "validationNotificationTemplates",
    "predictors", "predictorGroups",
    "dataEntryForms", "sections", "dataSets",
    "programs",
    "programStages", "programSections",
    "programStageSections",
    "programIndicators", "programIndicatorGroups",
    "programRuleVariables", "programRules", "programRuleActions",
    "programNotificationTemplates", "trackedEntityInstanceFilters",
    "sqlViews", "reports",
    "mapViews",  # before maps: pre-saving views avoids the 2.42 embedded-view flush crash
    "maps",
    "visualizations", "eventVisualizations",
    "dashboards",
    "userGroups",
    "users",
    "aggregateDataExchanges",
    "routes",
]

# Types to skip by default. categoryOptionCombos are NOT here: COC UIDs should
# migrate with the metadata (section greyedFields, predictor outputCombo, and
# dataElementOperands reference COCs by UID and break if the target regenerates
# them). To consciously regenerate instead, pass --exclude categoryOptionCombos
# and run POST /api/maintenance/categoryOptionComboUpdate after the import.
EMBEDDED_OWNED = set()

TRANSIENT_ERRORS = (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError)


# Server-stamped user references; present as owner=true in schemas but never
# gate an import (dangling values are re-stamped). Including them would rope
# nearly every type into one giant cluster via User.
BOOKKEEPING_REFS = {"createdBy", "lastUpdatedBy", "user"}

# DataDimensionItem (and its operand/reporting-rate variants) have NO schema
# entry, so the schema graph cannot see that analytical objects resolve data
# dimension references at flush time (missing ones crash the whole payload).
# These supplements close that gap.
DIMENSION_SOURCES = ["dataElements", "indicators", "programIndicators",
                     "dataSets", "programs", "programStages",
                     "trackedEntityAttributes", "categoryOptionCombos",
                     "expressionDimensionItems", "legendSets"]
EXTRA_DEPS = {t: DIMENSION_SOURCES for t in
              ["visualizations", "eventVisualizations", "eventReports",
               "eventCharts", "maps", "mapViews"]}
# RelationshipConstraint also has no schema entry, hiding these references:
EXTRA_DEPS["relationshipTypes"] = ["trackedEntityTypes", "programs",
                                   "programStages"]

# Clusters that are circular per the schema but empirically import BETTER
# split per-type with a repeat step: a combined options+optionSets payload
# mass-ignores options (E5002/E6012 pairs, verified on 2.42.5.1), while
# options -> optionSets -> options again imports cleanly in one pass.
CLUSTER_OVERRIDES = [
    ({"options", "optionSets"}, [["options"], ["optionSets"], ["options"]]),
]


def schema_groups(schemas_path):
    """Derive dependency-ordered import groups from schemas.json.

    Builds a type graph from owned reference properties (recursing into
    embedded objects so e.g. a map inherits its mapViews' references), finds
    strongly connected components (= circular clusters that must share one
    payload, e.g. dataSet<->section, program<->programStage), and returns the
    condensation in topological order: a list of groups, each a list of plural
    type names. Guaranteed correct for the schema it was computed from,
    unlike the hand-maintained ORDER fallback."""
    with open(schemas_path) as f:
        schemas = json.load(f).get("schemas", [])
    by_klass = {s["klass"]: s for s in schemas if s.get("klass")}
    meta = {s["klass"]: s["plural"] for s in schemas
            if s.get("metadata") and s.get("plural") and s.get("relativeApiEndpoint")}

    def ref_targets(schema, seen):
        out = set()
        for p in schema.get("properties", []):
            if not p.get("owner") or p.get("name") in BOOKKEEPING_REFS:
                continue
            if p.get("propertyType") == "REFERENCE":
                t = p.get("klass")
            elif (p.get("propertyType") == "COLLECTION"
                  and p.get("itemPropertyType") == "REFERENCE"):
                t = p.get("itemKlass")
            else:
                continue
            if not t or t == schema.get("klass") or t not in by_klass:
                continue
            if t in meta:
                out.add(t)
            elif by_klass[t].get("embeddedObject") and t not in seen:
                out |= ref_targets(by_klass[t], seen | {t})  # lift embedded refs
        return out

    graph = {k: ref_targets(by_klass[k], {k}) & set(meta) for k in meta}
    plural_to_klass = {v: k for k, v in meta.items()}
    for plural, deps in EXTRA_DEPS.items():
        k = plural_to_klass.get(plural)
        if k:
            graph[k] |= {plural_to_klass[d] for d in deps
                         if d in plural_to_klass and plural_to_klass[d] != k}

    # Tarjan strongly connected components
    index, low, on_stack, stack = {}, {}, set(), []
    sccs, counter = [], [0]

    def strongconnect(v):
        index[v] = low[v] = counter[0]; counter[0] += 1
        stack.append(v); on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); on_stack.discard(w); comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    sys.setrecursionlimit(10000)
    for v in sorted(meta):
        if v not in index:
            strongconnect(v)

    # topological order of the condensation (dependencies first)
    comp_of = {v: i for i, comp in enumerate(sccs) for v in comp}
    indeg = {i: 0 for i in range(len(sccs))}
    dependents = {i: set() for i in range(len(sccs))}
    for v, targets in graph.items():
        for w in targets:
            a, b = comp_of[w], comp_of[v]   # edge: v depends on w
            if a != b and b not in dependents[a]:
                dependents[a].add(b)
                indeg[b] += 1
    ready = sorted(i for i, d in indeg.items() if d == 0)
    order = []
    while ready:
        i = ready.pop(0)
        order.append(i)
        for j in sorted(dependents[i]):
            indeg[j] -= 1
            if indeg[j] == 0:
                ready.append(j)
    return [sorted(meta[k] for k in sccs[i]) for i in order]


def with_retry(fn, what, tries=5, backoff=2.0):
    """Call fn(); retry transient connection errors with exponential backoff."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except TRANSIENT_ERRORS as e:
            if attempt == tries:
                raise
            wait = backoff * attempt
            print(f"  ! {what}: {type(e).__name__}, retry {attempt}/{tries - 1} "
                  f"in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)


def default_auth():
    auth = os.environ.get("DHIS2_AUTH")
    if auth:
        return auth
    token = os.environ.get("DHIS2_API_TOKEN")
    return f"token:{token}" if token else None


def post_metadata(session, base_url, payload, params, timeout, what="metadata"):
    r = with_retry(lambda: session.post(f"{base_url}/api/metadata",
                                        params=params, json=payload,
                                        timeout=timeout),
                   f"POST {what}")
    try:
        body = r.json()
    except ValueError:
        return r.status_code, None, r.text[:500]
    return r.status_code, body, None


def post_metadata_async(session, base_url, payload, params, timeout,
                        poll_delay, what="metadata"):
    """POST with async=true, poll the import task, return the task summary.

    Same (status, body, err_text) shape as post_metadata; the summary is an
    ImportReport, so summarize() works on it unchanged. A completed task with
    no summary almost always means the server crashed/ran out of memory
    mid-import — reported via err_text."""
    p = dict(params)
    p["async"] = "true"
    r = with_retry(lambda: session.post(f"{base_url}/api/metadata",
                                        params=p, json=payload,
                                        timeout=timeout),
                   f"POST {what} (async)")
    if r.status_code not in (200, 202):
        return r.status_code, None, r.text[:500]
    try:
        tid = r.json().get("response", {}).get("id")
    except ValueError:
        tid = None
    if not tid:
        return r.status_code, None, f"no task id in async response: {r.text[:300]}"
    while True:
        time.sleep(poll_delay)
        tr = with_retry(lambda: session.get(
            f"{base_url}/api/system/tasks/METADATA_IMPORT/{tid}", timeout=120),
            "poll import task")
        try:
            notes = tr.json()
        except ValueError:
            notes = []
        if isinstance(notes, list) and any(n.get("completed") for n in notes):
            break
    sr = with_retry(lambda: session.get(
        f"{base_url}/api/system/taskSummaries/METADATA_IMPORT/{tid}", timeout=120),
        "fetch task summary")
    try:
        summary = sr.json()
    except ValueError:
        summary = None
    if not summary:
        return 500, None, ("task completed with NO summary — the server likely "
                           "crashed or ran out of memory mid-import; check the "
                           "server log")
    return 200, summary, None


def existing_ids(session, base_url, ptype, timeout):
    """All UIDs of ptype currently on the server (paged, ids only)."""
    ids, page = set(), 1
    while True:
        r = with_retry(lambda: session.get(
            f"{base_url}/api/{ptype}.json",
            params={"fields": "id", "paging": "true", "pageSize": "50000",
                    "page": str(page)}, timeout=timeout),
            f"fetch existing {ptype} ids")
        r.raise_for_status()
        body = r.json()
        batch = [o["id"] for o in body.get(ptype, []) if "id" in o]
        ids.update(batch)
        if page >= body.get("pager", {}).get("pageCount", 1) or not batch:
            return ids
        page += 1


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def ou_depths(items):
    """Depth of each org unit: `level`, else `path`, else parent links in-file.

    Parents referenced but not present in the file count as depth 0, so a
    subtree export still orders correctly relative to itself."""
    by_id = {o.get("id"): o for o in items if isinstance(o, dict)}
    memo = {}

    def depth(uid, seen):
        if uid in memo:
            return memo[uid]
        o = by_id.get(uid)
        if o is None:
            return 0
        if uid in seen:   # cycle in parent refs: push to the end
            return 9999
        if o.get("level"):
            d = o["level"]
        elif o.get("path"):
            d = o["path"].count("/")
        else:
            parent = (o.get("parent") or {}).get("id")
            d = 1 + depth(parent, seen | {uid}) if parent else 1
        memo[uid] = d
        return d

    return {o.get("id"): depth(o.get("id"), set()) for o in items
            if isinstance(o, dict)}


def batches_for(ptype, items, chunk_size):
    """Yield (label, batch). Org units are ordered shallow-first when chunked,
    so parents exist before their children arrive in a later batch."""
    if not chunk_size or len(items) <= chunk_size:
        yield "", items
        return
    if ptype == "organisationUnits":
        depths = ou_depths(items)
        by_depth = {}
        for o in items:
            by_depth.setdefault(depths.get(o.get("id"), 9999), []).append(o)
        for lvl in sorted(by_depth):
            group = by_depth[lvl]
            n = (len(group) + chunk_size - 1) // chunk_size
            for i, batch in enumerate(chunks(group, chunk_size), 1):
                yield f" L{lvl} {i}/{n}", batch
    else:
        n = (len(items) + chunk_size - 1) // chunk_size
        for i, batch in enumerate(chunks(items, chunk_size), 1):
            yield f" {i}/{n}", batch


def summarize(body):
    """Returns ((created, updated, deleted, ignored), error_count, top_error_codes)."""
    if not body:
        return (0, 0, 0, 0), 0, {}
    resp = body.get("response", body)
    totals = [0, 0, 0, 0]
    err_count = 0
    err_codes = {}
    for tr in resp.get("typeReports", []):
        s = tr.get("stats", {})
        totals[0] += s.get("created", 0)
        totals[1] += s.get("updated", 0)
        totals[2] += s.get("deleted", 0)
        totals[3] += s.get("ignored", 0)
        for obj in tr.get("objectReports", []):
            for er in obj.get("errorReports", []):
                err_count += 1
                code = er.get("errorCode")
                err_codes[code] = err_codes.get(code, 0) + 1
    return tuple(totals), err_count, err_codes


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("DHIS2_BASE_URL"),
                   help="Target DHIS2 base URL (default: $DHIS2_BASE_URL)")
    p.add_argument("--auth", default=default_auth(),
                   help="user:password or 'token:<PAT>' "
                        "(default: $DHIS2_AUTH, or token:$DHIS2_API_TOKEN)")
    p.add_argument("--src", required=True, help="Directory of per-type *.json files")
    p.add_argument("--schemas", default=None,
                   help="Path to schemas.json (from fetch_metadata.py --schemas). "
                        "When given, the import order and circular-reference "
                        "groups are DERIVED from the schema instead of the "
                        "hand-maintained ORDER list: types that reference each "
                        "other (dataSet<->section, program<->programStage) are "
                        "imported together in one payload so the server resolves "
                        "the cycle, making --passes 2 unnecessary.")
    p.add_argument("--exclude", default="",
                   help="Comma-separated types to skip entirely (e.g. users,userGroups)")
    p.add_argument("--include-embedded-owned", action="store_true",
                   help="Also import types that are usually owned by a parent "
                        "(e.g. mapViews). Off by default to avoid UID collisions.")
    p.add_argument("--strategy", default="CREATE_AND_UPDATE",
                   choices=["CREATE", "UPDATE", "CREATE_AND_UPDATE", "DELETE"])
    p.add_argument("--atomic-mode", default="NONE", choices=["ALL", "NONE"])
    p.add_argument("--skip-sharing", action="store_true", default=False,
                   help="Send skipSharing=true so the target keeps/derives its own "
                        "sharing. Off by default: whether sharing travels is decided "
                        "at export time (fetch/transform --unshare). Turn on when a raw "
                        "export references users/groups the target doesn't have.")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--passes", type=int, default=1,
                   help="Run the import N times. Useful for resolving forward refs "
                        "between types when the first pass leaves some unresolved.")
    p.add_argument("--chunk-size", type=int, default=0,
                   help="Split types with more objects than this into batches "
                        "(0 = one payload per type). For huge org unit trees, "
                        "5000 is a good value; org units are then imported "
                        "shallow-first so parents precede children.")
    p.add_argument("--async-threshold", type=int, default=3000,
                   help="Payloads above this many objects import with async=true "
                        "and task polling, avoiding request timeouts (0 = always "
                        "synchronous)")
    p.add_argument("--poll-delay", type=float, default=3.0,
                   help="Seconds between async task polls")
    p.add_argument("--batch-delay", type=float, default=0.0,
                   help="Seconds to sleep between payloads, giving a shared "
                        "server room to breathe (e.g. 1.0 for production)")
    p.add_argument("--resume", action="store_true",
                   help="Skip objects whose UID already exists on the server — "
                        "idempotent resume of a crashed bulk load. NOTE: existing "
                        "objects are then NOT updated.")
    p.add_argument("--dry-run", action="store_true",
                   help="List the import order without sending anything")
    args = p.parse_args()

    if not args.url or not args.auth:
        p.error("--url and --auth are required (or set DHIS2_BASE_URL and "
                "DHIS2_AUTH or DHIS2_API_TOKEN)")

    excluded = {t.strip() for t in args.exclude.split(",") if t.strip()}
    if not args.include_embedded_owned:
        excluded |= EMBEDDED_OWNED

    available = {f.removesuffix(".json") for f in os.listdir(args.src)
                 if f.endswith(".json")}
    available -= excluded

    if args.schemas:
        groups = []
        for g in schema_groups(args.schemas):
            for cluster, replacement in CLUSTER_OVERRIDES:
                if cluster <= set(g):
                    rest = sorted(set(g) - cluster)
                    groups += [r for r in replacement]
                    if rest:
                        groups.append(rest)
                    break
            else:
                groups.append(g)
        groups = [g for g in (sorted(set(g) & available) for g in groups) if g]
        covered = {t for g in groups for t in g}
        leftover = sorted(available - covered)
        if leftover:
            print(f"Note: types not in schema, appended: {leftover}", file=sys.stderr)
        groups += [[t] for t in leftover]
    else:
        ordered = [t for t in ORDER if t in available]
        leftover = sorted(available - set(ordered))
        if leftover:
            print(f"Note: appending un-ordered types: {leftover}", file=sys.stderr)
        groups = [[t] for t in ordered + leftover]

    if args.dry_run:
        for g in groups:
            print(" + ".join(g))
        return

    session = requests.Session()
    if args.auth.startswith("token:"):
        session.headers.update({"Authorization": f"ApiToken {args.auth[6:]}"})
    else:
        user, _, password = args.auth.partition(":")
        session.auth = HTTPBasicAuth(user, password)

    base_url = args.url.rstrip("/")
    params = {
        "atomicMode": args.atomic_mode,
        "importStrategy": args.strategy,
        "skipSharing": "true" if args.skip_sharing else "false",
        "importReportMode": "ERRORS",
    }

    overall = {"created": 0, "updated": 0, "deleted": 0, "ignored": 0, "errors": 0}
    failed_types = []

    def send(payload, name):
        n = sum(len(v) for v in payload.values())
        use_async = args.async_threshold and n > args.async_threshold
        t0 = time.time()
        if use_async:
            status, body, err_text = post_metadata_async(
                session, base_url, payload, params,
                args.timeout, args.poll_delay, what=name)
        else:
            status, body, err_text = post_metadata(
                session, base_url, payload, params, args.timeout, what=name)
        if body is None:
            print(f"[{name:35s}] HTTP {status}  FAILED: {err_text}")
            failed_types.append((name, status))
        else:
            (cr, up, de, ig), errs, codes = summarize(body)
            resp_status = (body.get("response", body).get("status")
                           or body.get("status") or "?")
            print(f"[{name:35s}] HTTP {status}  status={resp_status:8s}  "
                  f"+{cr} ~{up} -{de} x{ig} errs={errs}  {time.time() - t0:5.1f}s "
                  f"{dict(list(codes.items())[:3]) if codes else ''}")
            overall["created"] += cr
            overall["updated"] += up
            overall["deleted"] += de
            overall["ignored"] += ig
            overall["errors"] += errs
            if status >= 500:
                failed_types.append((name, status))
        if args.batch_delay:
            time.sleep(args.batch_delay)

    def load_items(ptype):
        with open(os.path.join(args.src, f"{ptype}.json")) as f:
            items = json.load(f)
        if items and args.resume:
            have = existing_ids(session, base_url, ptype, args.timeout)
            before = len(items)
            items = [o for o in items if o.get("id") not in have]
            if len(items) != before:
                print(f"[{ptype:35s}] resume: {before - len(items)} already "
                      f"present, importing {len(items)}")
        return items

    pass_errors = []
    for pass_n in range(1, args.passes + 1):
        if args.passes > 1:
            print(f"\n=== Pass {pass_n}/{args.passes} ===")
        errors_before = overall["errors"]
        for group in groups:
            if len(group) > 1:
                # circular cluster: one payload so the server resolves the cycle
                payload = {t: it for t in group if (it := load_items(t))}
                if payload:
                    send(payload, "+".join(payload))
                continue
            ptype = group[0]
            items = load_items(ptype)
            if not items:
                continue
            for label, batch in batches_for(ptype, items, args.chunk_size):
                send({ptype: batch}, f"{ptype}{label}")
        pass_errors.append(overall["errors"] - errors_before)

    print(f"\n=== Summary ===")
    print(f"Created: {overall['created']}, updated: {overall['updated']}, "
          f"ignored: {overall['ignored']}, errors: {overall['errors']}")
    if args.passes > 1:
        # Earlier-pass errors are often forward references a later pass
        # resolves (E5002); only errors that persist on the last pass are real.
        print(f"Object errors per pass: "
              f"{', '.join(f'pass {i + 1}: {n}' for i, n in enumerate(pass_errors))}")
        if pass_errors[-1]:
            print(f"Investigate the {pass_errors[-1]} final-pass errors; "
                  f"earlier-pass-only errors were deferred forward references.")
        else:
            print("No errors on the final pass — earlier-pass errors were "
                  "deferred forward references, since resolved.")
    if failed_types:
        print(f"\nServer errors:")
        for t, s in failed_types:
            print(f"  {t}: HTTP {s}")
        sys.exit(1)


if __name__ == "__main__":
    main()
