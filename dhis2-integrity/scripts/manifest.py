#!/usr/bin/env python3
"""FIX MANIFEST — the canonical, machine-readable record of a cleanup engagement.

One JSON file describes every applied fix (and every flagged non-fix). Everything the
engagement hands over is RENDERED from it, so the formats can never drift apart:

    manifest.json ──► change-proposal.md      (human-readable review document — always deliver)
                 ──► import-package.json     (plain /api/metadata import for the importable subset)
                 ──► playbook.ipynb          (operator console: DRY_RUN, preconditions, verify cells;
                                              built on playbook.Playbook)
                 ──► sql-fixes.sql           (standalone, psql-ready export of every SQL step — for the
                                              common case where the playbook runner has NO route to
                                              PostgreSQL and a DBA runs the SQL on the server itself)

Why a manifest (vs. writing the playbook directly):
  * national teams differ — some want a notebook, some only trust the Import/Export app, all need
    the review doc; one source renders all of them.
  * DRIFT GUARDS live in the data: every fix carries `preconditions` describing the expected current
    state. A replay on an instance that has drifted (or already has the fix) SKIPS with a message
    instead of clobbering. This is what makes replay-on-production defensible weeks after the
    fix was developed on an anonymized copy.

Schema (manifest_version 1) — see validate() for the enforced subset:
{
  "manifest_version": 1,
  "engagement": {
    "name": "...", "source_instance": "...", "dhis2_version": "...",
    "control_snapshot": "...", "decisions_doc": "...", "created": "YYYY-MM-DD"
  },
  "fixes": [
    {
      "id": "S1",                       # stable id, referenced by docs
      "title": "...",
      "check": "org_units_not_in_...",  # integrity check it addresses ("" if none)
      "severity": "SEVERE",
      "decision_ref": "decisions.md#S1",
      "rationale": "...",               # one-liner: why this fix is right
      "reversible": "...",              # how to undo
      "output_risk": "none|labels|analytics",
      "preconditions": [                # ALL must hold or the fix is SKIPPED (drift guard)
        {"type": "check_nonzero", "check": "..."},              # the issue still exists
        {"type": "object_exists",  "path": "/dataElements/Uid"},
        {"type": "object_missing", "path": "/indicators/Uid"},  # e.g. fix not already applied
        {"type": "custom", "code": "python -> bool expr using api()/sql()"}
      ],
      "steps": [                        # executed in order
        {"action": "api",  "method": "POST|PATCH|PUT|DELETE", "path": "/...",
         "params": {...}, "payload": {...}, "note": "..."},
        {"action": "sql",  "statement": "...", "note": "...", "cache_clear": true,
         "guard": "boolean SQL expr"},   # REQUIRED on destructive sql (DELETE / data migration):
                                         # asserts the expected PRE-fix state; rendered as a
                                         # DO-block that RAISEs (aborting the transaction) on drift
        {"action": "code", "code": "python using api()/sql()/ensure()/resolve_id()/new_uids()",
         "note": "..."}                 # for derive-at-runtime fixes (bucketing, dedup scans)
      ],
      # SQL AUTHORING RULE — guards are TIERED:
      # (base, every statement) SELF-GUARDING (idempotent), because the .sql export runs without the
      #   preconditions that guard playbook cells. Write state-transitions whose WHERE matches the
      #   PRE-state ("SET new WHERE old" -> re-run matches 0 rows); INSERTs use ON CONFLICT DO
      #   NOTHING; never accumulate ("SET x = x + ..") in a bare statement — if a fix genuinely must
      #   accumulate, delete/neutralize the source rows in the SAME statement's CTE or same
      #   transaction so a second run has nothing to add. validate() flags suspicious shapes.
      # (extra, destructive steps: DELETE / INSERT..SELECT data migrations) a `guard` boolean SQL
      #   expression asserting the expected pre-fix state. Rendered (in both the .sql export and the
      #   playbook) as a DO $$ .. RAISE EXCEPTION $$ block in the SAME transaction, so on drift the
      #   whole fix aborts instead of half-applying. validate() warns when it's missing.
      "verify": [
        {"type": "check", "check": "...", "expected": 0},
        {"type": "custom", "code": "python; assert/print"}
      ],
      "importable": false               # true => api POST /metadata payloads also emitted into the
    }                                   #          import package
  ],
  "flagged": [                          # deliberate NON-fixes — never rendered as executable
    {"check": "...", "summary": "...", "owner_action": "..."}
  ]
}

CLI:
  python manifest.py validate  manifest.json
  python manifest.py render    manifest.json --outdir DIR   (writes all four formats)
  python manifest.py demo      /tmp/demo.json               (writes a minimal valid example)
"""
import json, argparse, os, sys, textwrap

ACTIONS = {"api", "sql", "code"}
PRECONDS = {"check_nonzero", "object_exists", "object_missing", "custom"}
VERIFIES = {"check", "custom"}


# ----------------------------------------------------------------------------- validation
def validate(m):
    """Raise ValueError on structural problems; return list of warnings."""
    warn = []
    if m.get("manifest_version") != 1:
        raise ValueError("manifest_version must be 1")
    eng = m.get("engagement") or {}
    for k in ("name", "source_instance", "dhis2_version"):
        if not eng.get(k):
            raise ValueError(f"engagement.{k} is required")
    ids = set()
    for f in m.get("fixes", []):
        fid = f.get("id")
        if not fid or fid in ids:
            raise ValueError(f"fix id missing or duplicate: {fid!r}")
        ids.add(fid)
        for k in ("title", "rationale"):
            if not f.get(k):
                raise ValueError(f"fix {fid}: {k} is required")
        if not f.get("steps"):
            raise ValueError(f"fix {fid}: needs at least one step")
        for s in f["steps"]:
            if s.get("action") not in ACTIONS:
                raise ValueError(f"fix {fid}: bad step action {s.get('action')!r}")
            if s["action"] == "api" and not (s.get("method") and s.get("path")):
                raise ValueError(f"fix {fid}: api step needs method+path")
            if s["action"] == "sql" and not s.get("statement"):
                raise ValueError(f"fix {fid}: sql step needs statement")
            if s["action"] == "code" and not s.get("code"):
                raise ValueError(f"fix {fid}: code step needs code")
        for p in f.get("preconditions", []):
            if p.get("type") not in PRECONDS:
                raise ValueError(f"fix {fid}: bad precondition type {p.get('type')!r}")
        for v in f.get("verify", []):
            if v.get("type") not in VERIFIES:
                raise ValueError(f"fix {fid}: bad verify type {v.get('type')!r}")
        if not f.get("preconditions"):
            warn.append(f"fix {fid}: no preconditions — replay cannot detect drift/already-applied")
        if not f.get("verify"):
            warn.append(f"fix {fid}: no verify — replay cannot prove the fix landed")
        if f.get("importable") and not any(
                s["action"] == "api" and s.get("method") == "POST" and "metadata" in s.get("path", "")
                for s in f["steps"]):
            warn.append(f"fix {fid}: importable=true but no POST /metadata step")
        for s in f["steps"]:
            if s["action"] == "code" and "sql(" in s.get("code", ""):
                warn.append(f"fix {fid}: sql() inside a code step is NOT exportable to the .sql file — "
                            "prefer a dedicated sql step with UID subqueries where possible")
            if s["action"] == "sql":
                warn.extend(f"fix {fid}: {w}" for w in _sql_idempotency_warnings(s["statement"]))
                if _is_destructive_sql(s["statement"]) and not s.get("guard"):
                    warn.append(f"fix {fid}: destructive sql step without a `guard` — add a boolean "
                                "SQL expression asserting the pre-fix state (rendered as an aborting "
                                "DO block) so a drifted database fails loudly instead of half-applying")
    return warn


def _is_destructive_sql(stmt):
    """DELETEs and INSERT..SELECT data migrations warrant an aborting guard, not just self-guarding."""
    su = " ".join(stmt.split()).upper()
    return (su.startswith("DELETE") or su.startswith("TRUNCATE")
            or (su.startswith("INSERT") and " SELECT " in su))


def _guard_block(guard, fix_id, title):
    """Render a step's `guard` expression as an aborting DO block (same transaction as the step)."""
    msg = f"GUARD FAILED for {fix_id} ({title}): database state differs from the pre-fix state this was built on".replace("'", "''")
    return ("DO $$ BEGIN\n"
            f"  IF NOT ({guard}) THEN\n"
            f"    RAISE EXCEPTION '{msg}';\n"
            "  END IF;\n"
            "END $$;")


def _sql_idempotency_warnings(stmt):
    """Heuristic red flags for NON-self-guarding SQL (cannot prove idempotency statically, but these
    shapes are almost always re-run-unsafe in a standalone .sql file)."""
    import re as _re
    s = " ".join(stmt.split())          # collapse whitespace
    su = s.upper()
    w = []
    if su.startswith("INSERT") and "ON CONFLICT" not in su:
        w.append("INSERT without ON CONFLICT DO NOTHING — a re-run duplicates rows (or errors)")
    if _re.search(r"SET\s+(\w+)\s*=\s*[^,;]*\b\1\b\s*[+\-|]", s, _re.I):
        w.append("accumulating UPDATE (SET x = x + …) — a re-run double-applies; neutralize the "
                 "source rows in the same statement/transaction instead")
    if su.startswith("UPDATE") and " WHERE " not in su:
        w.append("UPDATE without WHERE — affects every row, and a re-run re-affects them")
    if su.startswith("DELETE") and " WHERE " not in su:
        w.append("DELETE without WHERE — empties the whole table")
    if su.startswith("UPDATE") and " WHERE " in su:
        set_cols = {m.group(1).lower() for m in _re.finditer(r"(?:SET|,)\s*(\w+)\s*=", s, _re.I)}
        where = s[su.index(" WHERE "):].lower()
        if set_cols and not any(c in where for c in set_cols):
            w.append("UPDATE whose WHERE never references a SET column — likely not a state-transition "
                     "(re-run re-matches the same rows); prefer 'SET new WHERE old'")
    return w


# ----------------------------------------------------------------------------- renderer: proposal
def render_proposal(m):
    eng = m["engagement"]
    L = [f"# Change proposal — {eng['name']}",
         "",
         f"Source: `{eng['source_instance']}` · DHIS2 **{eng['dhis2_version']}** · "
         f"control snapshot `{eng.get('control_snapshot','-')}` · decisions: `{eng.get('decisions_doc','-')}` · "
         f"prepared {eng.get('created','-')}",
         "",
         "Every change below was developed and verified on a copy of this system. Apply via the "
         "rendered playbook (stepwise, with preconditions and verification) or — for the importable "
         "subset — the metadata import package. Items under *Flagged for owner* are deliberate "
         "NON-changes that need a decision from the metadata owner.",
         "", "## Proposed changes", ""]
    for f in m.get("fixes", []):
        risk = f.get("output_risk", "none")
        L += [f"### {f['id']} — {f['title']}",
              f"- **Integrity check:** `{f.get('check','-')}` ({f.get('severity','-')})",
              f"- **Why:** {f['rationale']}",
              f"- **Output risk:** {risk}" + ("  ⚠️ can change analytics values" if risk == "analytics" else ""),
              f"- **Reversible:** {f.get('reversible','see control snapshot')}",
              f"- **How:** {len(f['steps'])} step(s): " +
              ", ".join(s["action"] + (f" {s.get('method','')} {s.get('path','')}" if s["action"] == "api" else "")
                        for s in f["steps"]),
              f"- **Decision record:** {f.get('decision_ref','-')}",
              ""]
    if m.get("flagged"):
        L += ["## Flagged for owner — decisions needed, intentionally NOT auto-fixed", ""]
        for fl in m["flagged"]:
            L += [f"- **`{fl.get('check','-')}`** — {fl.get('summary','')}",
                  f"  - Owner action: {fl.get('owner_action','review')}"]
        L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------------- renderer: import pkg
def render_import_package(m):
    """Merge the payloads of importable POST /metadata steps into one import file.
    Returns (package, skipped_fix_ids) — fixes that are NOT expressible as an import
    (deletes, merges, SQL, runtime-derived) are skipped and must go via the playbook."""
    pkg, skipped = {}, []
    for f in m.get("fixes", []):
        if not f.get("importable"):
            skipped.append(f["id"])
            continue
        for s in f["steps"]:
            if s["action"] == "api" and s.get("method") == "POST" and "metadata" in s.get("path", ""):
                for typ, objs in (s.get("payload") or {}).items():
                    pkg.setdefault(typ, [])
                    have = {o.get("id") for o in pkg[typ]}
                    pkg[typ] += [o for o in objs if o.get("id") not in have]
    return pkg, skipped


# ----------------------------------------------------------------------------- renderer: sql export
def render_sql_export(m):
    """Standalone .sql file with every `action: sql` step, for running ON the database server
    (psql -f, \\i, or block-by-block copy/paste) when the playbook environment has no DB route.
    Each block: fix header + ordering warning + BEGIN/COMMIT wrapper. Returns None if no SQL steps.
    NOTE: SQL embedded inside `code` steps (via sql()/resolve_id()) is NOT exportable — validate()
    warns about those; prefer dedicated sql steps with UID subqueries so they land here."""
    eng = m["engagement"]
    blocks = []
    for f in m.get("fixes", []):
        steps = f.get("steps", [])
        for i, s in enumerate(steps, 1):
            if s["action"] != "sql":
                continue
            hdr = ["-- " + "=" * 76,
                   f"-- {f['id']} (step {i}/{len(steps)}) — {f['title']}",
                   f"--   check: {f.get('check', '-')}   decision: {f.get('decision_ref', '-')}"]
            if s.get("note"):
                hdr.append(f"--   note: {s['note']}")
            pre = [p for p in f.get("preconditions", [])]
            if pre:
                hdr.append("--   preconditions (verified by the playbook, NOT by this file): "
                           + "; ".join(p["type"] + (":" + p.get("check", p.get("path", ""))
                                                    if p["type"] != "custom" else " (custom)") for p in pre))
            after_api = any(x["action"] != "sql" for x in steps[:i - 1])
            before_api = any(x["action"] != "sql" for x in steps[i:])
            if after_api or before_api:
                pos = ([" AFTER this fix's earlier API steps"] if after_api else []) + \
                      ([" BEFORE its later API steps"] if before_api else [])
                hdr.append("--   !! ORDER: run this" + " and".join(pos) +
                           " — drive the sequence from the playbook, which pauses here.")
            if s.get("cache_clear"):
                hdr.append("--   afterwards clear the metadata cache (playbook does it, or:")
                hdr.append("--     curl -X POST -u <admin> '<DHIS2_BASE_URL>/api/maintenance?cacheClear=true' )")
            stmt = s["statement"].rstrip().rstrip(";") + ";"
            if s.get("guard"):
                hdr.append("--   guard: aborts this block (transaction rolls back) if the pre-fix state is absent")
                stmt = _guard_block(s["guard"], f["id"], f["title"]) + "\n" + stmt
            blocks.append("\n".join(hdr) + f"\nBEGIN;\n{stmt}\nCOMMIT;\n")
    if not blocks:
        return None
    head = [
        "-- " + "=" * 76,
        f"-- SQL fixes — {eng['name']}  (DHIS2 {eng['dhis2_version']}; prepared {eng.get('created', '-')})",
        f"-- Rendered from the fix manifest ({eng.get('decisions_doc', 'decisions.md')} records the decisions).",
        "--",
        "-- HOW TO USE — on the database server (or any host with a DB route):",
        "--   * whole file:        psql -h <host> -U <user> -d <db> -v ON_ERROR_STOP=1 -f this_file.sql",
        "--   * block by block:    copy/paste one BEGIN..COMMIT block at a time into psql (recommended",
        "--                        when interleaved with API steps — see the ORDER warnings).",
        "-- Each block is its own transaction: a failure rolls back only that block.",
        "--",
        "-- !! These statements were developed against a copy of this system. The playbook checks each",
        "--    fix's PRECONDITIONS before its steps; running this file standalone skips those guards —",
        "--    prefer driving the sequence from the playbook and executing blocks here as it pauses.",
        "-- !! All statements are UID-based (subqueries resolve internal ids on THIS database).",
        "-- !! IDEMPOTENCY CONTRACT: every block is authored as a self-guarding state-transition — its",
        "--    WHERE matches the PRE-fix state, so a re-run (or a run on an already-fixed database)",
        "--    reports 'UPDATE 0' / 'DELETE 0' and changes nothing. If a block reports 0 rows, it was",
        "--    already applied. If a block reports MORE rows than the change proposal describes, STOP",
        "--    and investigate before continuing — the database differs from the one this was built on.",
        "-- !! Destructive blocks (DELETEs, data migrations) additionally open with a DO-block GUARD",
        "--    that RAISEs on drift, rolling back that block — a 'GUARD FAILED' error is the guard",
        "--    working, not a bug: verify which database you are on before retrying anything.",
        "-- " + "=" * 76,
        "",
    ]
    return "\n".join(head) + "\n" + "\n".join(blocks)


# ----------------------------------------------------------------------------- renderer: playbook
def _precond_code(p):
    if p["type"] == "check_nonzero":
        return (f'_n = check("{p["check"]}")\n'
                f'if not _n: _skip.append("check {p["check"]} already 0 (applied or never present)")')
    if p["type"] == "object_exists":
        return (f'_r = api("GET", "{p["path"]}", params={{"fields": "id"}})\n'
                f'if _r is None or _r.status_code != 200: _skip.append("missing: {p["path"]}")')
    if p["type"] == "object_missing":
        return (f'_r = api("GET", "{p["path"]}", params={{"fields": "id"}})\n'
                f'if _r is not None and _r.status_code == 200: _skip.append("already exists: {p["path"]}")')
    if p["type"] == "custom":
        return (f'if not ({p["code"]}): _skip.append("custom precondition failed: '
                + p.get("note", p["code"]).replace('"', "'")[:80] + '")')
    raise ValueError(p["type"])


def _step_code(s, fix_id="", step_no=0, eng_name=""):
    if s["action"] == "api":
        args = [f'"{s["method"]}"', f'"{s["path"]}"']
        if s.get("payload") is not None:
            args.append("body=" + json.dumps(s["payload"], indent=None))
        if s.get("params"):
            args.append("params=" + json.dumps(s["params"], indent=None))
        return f'api({", ".join(args)})' + (f'  # {s["note"]}' if s.get("note") else "")
    if s["action"] == "sql":
        stmt = s["statement"].replace('"""', r'\"\"\"')
        ref = (f"# NO DB ROUTE FROM HERE? This statement is block '{fix_id} (step {step_no}/…)' in "
               f"sql-fixes-{eng_name}.sql — have a DBA run that block on the server, then continue below.\n")
        code = ref
        if s.get("guard"):
            g = _guard_block(s["guard"], fix_id, "guard").replace('"""', r'\"\"\"')
            code += f'sql("""{g}""", note="aborting guard — must pass before the step")\n'
        code += f'sql("""{stmt}""", note="{s.get("note","")}")'
        if s.get("cache_clear"):
            code += "\ncache_clear()"
        return code
    if s["action"] == "code":
        return s["code"]
    raise ValueError(s["action"])


def render_playbook(m, section="integrity"):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from playbook import Playbook
    eng = m["engagement"]
    pb = Playbook(f"Remediation playbook — {eng['name']} (DHIS2 {eng['dhis2_version']})", section=section)
    for f in m.get("fixes", []):
        pre = "\n".join(_precond_code(p) for p in f.get("preconditions", []))
        steps = "\n".join(_step_code(s, fix_id=f["id"], step_no=i, eng_name=eng["name"])
                          for i, s in enumerate(f["steps"], 1))
        body = "_skip = []\n"
        if pre:
            body += pre + "\n"
        body += ("if _skip:\n"
                 '    _say("⛔ SKIPPED (drift guard): " + "; ".join(_skip))\n'
                 '    _say("   → the fix is already applied, or this instance differs from the one it was developed on. Review before forcing.")\n'
                 "else:\n" + textwrap.indent(steps, "    "))
        kind = "sql" if any(s["action"] == "sql" for s in f["steps"]) else "api"
        add = pb.sql_step if kind == "sql" else pb.api_step
        add(id=f["id"], title=f["title"], severity=f.get("severity", ""),
            check=f.get("check", ""), rationale=f["rationale"],
            reversible=f.get("reversible"), code=body)
        for v in f.get("verify", []):
            if v["type"] == "check":
                pb.verify_step(v["check"], expected=v.get("expected", 0))
            else:
                pb.api_step(id=f["id"] + "-verify", title=f"verify {f['id']}", severity="",
                            check=f.get("check", ""), rationale="custom verification",
                            code=v["code"])
    if m.get("flagged"):
        lines = ["## ⚠️ Flagged for owner — do NOT auto-fix",
                 "These need a decision from the metadata owner; they are deliberately not executable:", ""]
        lines += [f"- **`{fl.get('check','-')}`** — {fl.get('summary','')} *(owner: {fl.get('owner_action','review')})*"
                  for fl in m["flagged"]]
        pb.note("\n".join(lines))
    return pb


# ----------------------------------------------------------------------------- CLI
def _demo(path):
    demo = {
        "manifest_version": 1,
        "engagement": {"name": "demo", "source_instance": "http://localhost:8080",
                       "dhis2_version": "2.42", "created": "2026-01-01"},
        "fixes": [{
            "id": "D1", "title": "Uniform program-rule priority", "check": "program_rules_no_priority",
            "severity": "WARNING", "rationale": "behaviour-neutral; clears the warning",
            "reversible": "priorities were NULL before", "output_risk": "none",
            "preconditions": [{"type": "check_nonzero", "check": "program_rules_no_priority"}],
            "steps": [{"action": "sql", "statement": "UPDATE programrule SET priority=1 WHERE priority IS NULL",
                       "cache_clear": True}],
            "verify": [{"type": "check", "check": "program_rules_no_priority", "expected": 0}],
            "importable": False}],
        "flagged": [{"check": "data_elements_aggregate_with_different_period_types",
                     "summary": "5 DEs in datasets of different period types",
                     "owner_action": "choose the canonical period type per DE"}],
    }
    json.dump(demo, open(path, "w"), indent=1)
    print("wrote", path)
    return demo


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("manifest")
    r = sub.add_parser("render"); r.add_argument("manifest"); r.add_argument("--outdir", required=True)
    d = sub.add_parser("demo"); d.add_argument("path")
    a = ap.parse_args()
    if a.cmd == "demo":
        m = _demo(a.path)
        print("validate:", validate(m) or "OK")
        return
    m = json.load(open(a.manifest))
    warns = validate(m)
    for w in warns:
        print("WARN:", w)
    if a.cmd == "validate":
        print("OK —", len(m.get("fixes", [])), "fixes,", len(m.get("flagged", [])), "flagged")
        return
    os.makedirs(a.outdir, exist_ok=True)
    name = m["engagement"]["name"]
    p = os.path.join(a.outdir, f"change-proposal-{name}.md")
    open(p, "w").write(render_proposal(m)); print("wrote", p)
    pkg, skipped = render_import_package(m)
    p = os.path.join(a.outdir, f"import-package-{name}.json")
    json.dump(pkg, open(p, "w"), indent=1)
    print(f"wrote {p} ({sum(len(v) for v in pkg.values())} objects; "
          f"NOT importable, playbook-only: {', '.join(skipped) or 'none'})")
    sql_text = render_sql_export(m)
    if sql_text:
        p = os.path.join(a.outdir, f"sql-fixes-{name}.sql")
        open(p, "w").write(sql_text)
        print(f"wrote {p} (run on the DB server: psql -v ON_ERROR_STOP=1 -f …, or block-by-block)")
    else:
        print("no sql steps — no .sql export needed")
    pb = render_playbook(m)
    p = os.path.join(a.outdir, f"playbook-{name}.ipynb")
    pb.write(p); print("wrote", p)


if __name__ == "__main__":
    main()
