#!/usr/bin/env python3
"""Generate a portable, replayable remediation PLAYBOOK (.ipynb) from a list of fixes.

This is the executable companion to the .md/.csv documentation: as the cleanup is worked out on a
dedicated instance, each fix is also recorded here so an admin can later REPLAY it — one cell at a
time or "Run All" — first on staging, then on production.

Design guarantees (so the same notebook is safe on any clone of the source instance):
  * UID-based — every operation targets stable DHIS2 UIDs, never instance-specific numeric ids.
    SQL cells call resolve_id(table, uid) to look up THIS target's internal id at runtime.
  * Idempotent — each cell re-checks state and no-ops if the fix is already applied, so "Run All"
    is safe to run twice and safe on an instance where some fixes already exist.
  * Dry-run first — a DRY_RUN flag at the top prints intended actions without writing; flip to apply.
  * Verifiable — verify cells re-run the relevant integrity check and assert it is clear.
  * SQL is gated & ordered — SQL cells are clearly marked (require DB access + ALL), sit in the right
    position relative to API cells, and are followed by a cache-clear.

Usage (as a library, from the cleanup driver):
    from playbook import Playbook
    pb = Playbook("Integrity remediation — <instance>", section="integrity")
    pb.api_step(id="SEV-1", title="Make facility group sets non-compulsory",
                severity="SEVERE", check="org_units_not_in_compulsory_group_sets",
                rationale="Admin levels can't carry a facility type; the set shouldn't be compulsory.",
                reversible="Re-PATCH compulsory=true to revert.",
                code='''for uid in ["Bpx0589u8y0","J5jldMd8OHv","Cbuj0VCyDjL"]:
    cur = api("GET", f"/organisationUnitGroupSets/{uid}", params={"fields":"compulsory"})
    if cur and cur.json().get("compulsory") is False:
        print("already non-compulsory:", uid); continue
    api("PATCH", f"/organisationUnitGroupSets/{uid}", body=[{"op":"replace","path":"/compulsory","value":False}])''')
    pb.verify_step("org_units_not_in_compulsory_group_sets")
    pb.write("playbook-integrity.ipynb")

Then `python playbook.py --demo /tmp/x.ipynb` writes a tiny sample to validate the format.
"""
import json, argparse

# --- code injected as the notebook's setup cell (self-contained helpers) --------------------------
SETUP_CODE = r'''# === Setup — run this first ===
# Target instance is taken from env vars, so this notebook is portable across staging/prod:
#   DHIS2_BASE_URL + (DHIS2_API_TOKEN | DHIS2_USER/DHIS2_PASS)
#   For SQL cells only: PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD
import os, time, httpx

DRY_RUN = True   # <<< leave True to preview; set False to actually apply, then re-run the cells

CONFIRM_TARGET = ""   # <<< OPERATOR ACK: this cell prints the target's system name below — after
                      #     verifying it IS the instance you mean to change (staging vs PROD!), copy
                      #     that exact name here and re-run. Non-DRY writes refuse until it matches.
                      #     (The per-fix drift guards catch the WRONG database; this ack catches the
                      #     right database at the wrong TIME.)

BASE = os.environ["DHIS2_BASE_URL"].rstrip("/") + "/api"
_h = {"Accept": "application/json"}; _auth = None
if os.environ.get("DHIS2_API_TOKEN"): _h["Authorization"] = f"ApiToken {os.environ['DHIS2_API_TOKEN']}"
else: _auth = (os.environ.get("DHIS2_USER","admin"), os.environ.get("DHIS2_PASS","district"))
_client = httpx.Client(base_url=BASE, headers=_h, auth=_auth, timeout=300)

_info = {}
try: _info = _client.get("/system/info").json()
except Exception as _e: print("⚠️ could not read /system/info:", _e)
_SYSTEM_NAME = _info.get("systemName", "?")
try:
    _oucount = _client.get("/organisationUnits", params={"fields": "id", "pageSize": 1, "totalPages": "true"}).json()["pager"]["total"]
except Exception: _oucount = "?"
print(f"Target identity: systemName={_SYSTEM_NAME!r} | DHIS2 {_info.get('version','?')} | {BASE} | {_oucount} org units")
if CONFIRM_TARGET == _SYSTEM_NAME and _SYSTEM_NAME != "?":
    print("✅ operator ack: CONFIRM_TARGET matches — non-DRY writes are enabled.")
else:
    print(f"🔒 no operator ack yet — DRY_RUN preview works; before applying, set CONFIRM_TARGET = {_SYSTEM_NAME!r} and re-run this cell.")

def _ack():
    if DRY_RUN: return
    if CONFIRM_TARGET != _SYSTEM_NAME or _SYSTEM_NAME == "?":
        raise RuntimeError(f"OPERATOR ACK REQUIRED: verify this is the intended instance, then set "
                           f"CONFIRM_TARGET = {_SYSTEM_NAME!r} in the Setup cell and re-run it.")

# SQL execution mode — auto-detected, override by assigning SQL_MODE yourself and re-running this cell:
#   "direct": PG env vars present -> sql() runs statements against the DB (honours DRY_RUN)
#   "server": no DB route from here -> sql() prints the statement + its block in the sql-fixes-*.sql
#             export for a DBA to run on the server; this notebook then continues, assuming it was run.
SQL_MODE = "direct" if os.environ.get("PGHOST") else "server"
print("Target:", BASE, "| DRY_RUN =", DRY_RUN, "| SQL_MODE =", SQL_MODE,
      "" if SQL_MODE == "direct" else "(no PG env — SQL cells will defer to the sql-fixes-*.sql export)")

def _say(msg, end="\n"):
    # flush so the message appears IMMEDIATELY (Jupyter/terminal), not buffered until the cell ends.
    print(msg, end=end, flush=True)

def api(method, path, body=None, params=None):
    """HTTP to the target. GET always runs; writes honour DRY_RUN.
    Writes show a ⏳ 'waiting on API' line BEFORE the call (so a slow op never looks hung), then overwrite
    it with ✅/❌ + status + elapsed time when the response returns."""
    if method == "GET":
        return _client.get(path, params=params)
    if DRY_RUN:
        _say(f"  [DRY] {method} {path}" + (f"  body={body}" if body else "")); return None
    _ack()
    hdr = {"Content-Type": "application/json-patch+json"} if method == "PATCH" else None
    _say(f"  ⏳ {method} {path} … (waiting on API)", end="")
    t = time.time()
    r = _client.request(method, path, json=body, params=params, headers=hdr)
    mark = "✅" if r.status_code < 300 else "❌"
    _say(f"\r  {mark} {method} {path} → {r.status_code}  ({time.time()-t:.1f}s)" + " " * 12)
    return r

def cache_clear():
    return api("POST", "/maintenance", params={"cacheClear": "true"})

def check(name, timeout=120):
    """Fresh-run one data-integrity check on the target and return its issue count.
    The recompute is slow (often 30-120s), so this prints a LIVE elapsed counter while polling — it's
    obvious it's working, not hung."""
    before = (_client.get("/dataIntegrity/details", params={"checks": name}).json().get(name) or {}).get("finishedTime")
    _client.post("/dataIntegrity/details", params={"checks": name})
    t = time.time(); end = t + timeout
    _say(f"  ⏳ integrity check '{name}' recomputing (up to {timeout}s)…", end="")
    while time.time() < end:
        d = _client.get("/dataIntegrity/details", params={"checks": name}).json()
        if name in d and d[name].get("finishedTime") != before:
            n = len(d[name].get("issues", [])); _say(f"\r  \U0001f4ca check '{name}': {n} issue(s)  ({time.time()-t:.0f}s)" + " " * 20); return n
        _say(f"\r  ⏳ integrity check '{name}'… {int(time.time()-t)}s elapsed (polling)", end="")
        time.sleep(3)
    _say(f"\r  ⚠️ check '{name}': TIMED OUT after {timeout}s" + " " * 20); return None

# --- SQL helpers (only needed for guard-blocked fixes; require DB access + ALL on the target) ------
def _db():
    import psycopg2
    return psycopg2.connect(host=os.environ.get("PGHOST","localhost"), port=os.environ.get("PGPORT","5432"),
        dbname=os.environ.get("PGDATABASE","dhis2"), user=os.environ.get("PGUSER","dhis"),
        password=os.environ.get("PGPASSWORD",""))

def resolve_id(table, uid, idcol=None):
    """Map a stable UID to THIS instance's internal numeric id (portability — never hardcode ids).
    Requires a DB route — a code step using resolve_id() cannot run in SQL_MODE='server'; such fixes
    must either run where a route exists, or be re-expressed as dedicated sql steps with UID subqueries
    (which export to sql-fixes-*.sql)."""
    try:
        c = _db()
    except Exception as e:
        raise RuntimeError(f"resolve_id({table!r}, {uid!r}) needs a DB route and none is available ({e}). "
                           "Run this fix where PG env vars work, or use a sql step with UID subqueries.") from e
    cur = c.cursor()
    cur.execute(f"select {idcol or table+'id'} from {table} where uid=%s", (uid,))
    row = cur.fetchone(); c.close()
    return row[0] if row else None

def sql(stmt, args=None, note=""):
    """Run a write SQL on the target (honours DRY_RUN and SQL_MODE).
    SQL_MODE == "server": no DB route from this environment — print the statement and its block in the
    sql-fixes-*.sql export for a DBA to run on the server, then CONTINUE (the notebook assumes it was
    applied; the fix's verify cell will prove it either way). Always cache_clear() after metadata SQL."""
    if DRY_RUN:
        _say(f"  [DRY-SQL] {note}: {stmt}  args={args}"); return None
    if SQL_MODE == "server":
        _say("  🛢️  SQL (no DB route from here) — have a DBA run this on the server")
        _say("      (it is a ready-made BEGIN..COMMIT block in the accompanying sql-fixes-*.sql):")
        for line in stmt.strip().splitlines():
            _say("      " + line)
        if args:
            _say(f"      -- args: {args}  (statement in the .sql export has them inlined)")
        _say("      ▶ when it has been applied, continue with the next cell — the verify will confirm.")
        return None
    _ack()
    try:
        c = _db()
    except Exception as e:
        _say(f"  ❌ no DB connection ({e}) — set PG env vars for direct mode, or set SQL_MODE='server' "
             "and run the block from sql-fixes-*.sql on the server instead.")
        return None
    _say(f"  ⏳ SQL: {note} … (waiting on DB)", end="")
    t = time.time()
    cur = c.cursor(); cur.execute(stmt, args or ()); n = cur.rowcount; c.commit(); c.close()
    _say(f"\r  ✅ SQL: {note} → {n} row(s)  ({time.time()-t:.1f}s)" + " " * 12); return n

def new_uids(n=1):
    """Mint n valid DHIS2 UIDs from /api/system/id. Use ONCE while authoring to pre-generate a stable id
    for a NEW object, then HARD-CODE that id into the playbook so every run / every instance reuses it."""
    return _client.get("/system/id", params={"limit": n}).json()["codes"]

def ensure(path, uid, body):
    """Idempotent create-by-UID for a NEW object. If /<path>/<uid> exists -> skip; else create it with that
    EXPLICIT id. Pre-generating + hard-coding the id makes creation: re-run-safe (no duplicate), portable
    (same id on dedicated/staging/prod, so references resolve), and reversible (delete exactly that uid).
    `path` = plural endpoint, e.g. 'organisationUnitGroups'. Membership/derived fields go in `body`."""
    if _client.get(f"/{path}/{uid}").status_code == 200:
        _say(f"  ⏭️  exists, skip: /{path}/{uid}"); return uid
    if DRY_RUN:
        _say(f"  [DRY] create /{path}/{uid}  {body.get('name','')}"); return uid
    _ack()
    b = dict(body); b["id"] = uid
    _say(f"  ⏳ creating /{path}/{uid} … (waiting on API)", end="")
    t = time.time(); r = _client.post(f"/{path}", json=b)
    _say(f"\r  ✅ create /{path}/{uid} → {r.status_code}  ({time.time()-t:.1f}s)" + " " * 12); return uid
'''

INTRO_MD = """# {title}

**Replayable remediation playbook** — the executable companion to the written report.

### Launching this notebook
```bash
pip install jupyterlab httpx python-dotenv psycopg2-binary      # one-time
export DHIS2_BASE_URL="https://your-instance.org"                # NO /api suffix
export DHIS2_USER="admin"; export DHIS2_PASS="•••"               # or DHIS2_API_TOKEN
# SQL cells only: export PGHOST=… PGPORT=… PGDATABASE=… PGUSER=… PGPASSWORD=…
jupyter lab playbook.ipynb                                       # opens in your browser
```
(In a headless sandbox: add `--no-browser --ip 0.0.0.0 --port "$SANDBOX_HOST_PORT"` and open
`http://localhost:$SANDBOX_HOST_PORT` on the host. No Jupyter? `jupyter nbconvert --execute` runs it
headlessly — honours `DRY_RUN` — or copy the Setup cell + a fix cell into a plain `.py`.)

How to use:
1. Set the target via environment variables (`DHIS2_BASE_URL` + token or `DHIS2_USER`/`DHIS2_PASS`;
   for SQL cells also `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD`), then run the **Setup** cell.
2. With `DRY_RUN = True` (default), run cells to **preview** every action — nothing is written.
3. **Operator ack:** the Setup cell prints the target's system name. Verify it is the instance you mean
   to change (staging vs PROD!), copy that exact name into `CONFIRM_TARGET`, and re-run Setup. Non-DRY
   writes refuse to run until it matches — the drift guards catch the *wrong* database; this catches the
   right database at the wrong *time*.
4. Apply **one fix** (run its cell) or **everything** (Run All) by setting `DRY_RUN = False` and re-running.
5. Recommended flow: run on **staging** first, confirm the verify cells are clear, then repeat on **production**.

### SQL cells — two ways to run them
Most real deployments do **not** expose PostgreSQL to the machine running this notebook; usually only a
DBA on the server can run SQL. The Setup cell auto-detects which situation you are in (`SQL_MODE`):

- **`direct`** (PG env vars set — e.g. a local sandbox or a host with a DB route): SQL cells execute
  against the database directly, honouring `DRY_RUN`.
- **`server`** (no PG env vars): SQL cells do **not** attempt a connection. Each one prints its exact
  statement and its block reference in the accompanying **`sql-fixes-*.sql`** export — hand that block to
  the DBA (`psql -v ON_ERROR_STOP=1 -f`, or copy/paste one BEGIN..COMMIT block), confirm it ran, then
  continue with the next cell. Drive the ORDER from this notebook: it pauses at each SQL step exactly
  where the statement belongs in the fix sequence.

Either way, the verify cells afterwards prove the fix landed.

**Reading the output while a cell runs:** a line shows **⏳ … (waiting on API/DB)** the moment a write or
an integrity check starts, so a slow call never looks hung; it's replaced by **✅** (or **❌**) with the
HTTP status and **elapsed time** when the response returns. Integrity checks (`check(...)`) recompute
server-side and can take 30–120s — they print a **live "Ns elapsed" counter** while polling. A busy `[*]`
on the cell with no new ⏳ line just means the current call hasn't returned yet.

Every fix cell is **idempotent** (safe to re-run) and targets **UIDs** (portable across instances).
⚠️ Cells marked **SQL** require direct database access **and** the `ALL` authority on the target, run in
the order shown, and are followed by a cache-clear. They exist only where the DHIS2 API forbids the fix.
"""


class Playbook:
    def __init__(self, title, section="integrity"):
        self.title = title
        self.section = section
        self.cells = [self._md(INTRO_MD.format(title=title)), self._code(SETUP_CODE)]

    def _cell_id(self):
        # nbformat >=4.5 requires a unique per-cell id (MissingIDFieldWarning otherwise)
        import uuid
        return uuid.uuid4().hex[:12]

    def _md(self, text):
        return {"cell_type": "markdown", "id": self._cell_id(), "metadata": {},
                "source": text.splitlines(keepends=True)}

    def _code(self, code):
        return {"cell_type": "code", "id": self._cell_id(), "metadata": {}, "execution_count": None,
                "outputs": [], "source": code.splitlines(keepends=True)}

    def _header(self, id, title, severity, check, rationale, reversible, kind):
        tag = "🛢️ SQL" if kind == "sql" else "API"
        md = (f"## [{severity}] {id} — {title}  ·  _{tag}_\n\n"
              f"- **Resolves check:** `{check}`\n"
              f"- **Why:** {rationale}\n")
        if reversible:
            md += f"- **Reversible:** {reversible}\n"
        if kind == "sql":
            md += ("- **⚠️ Requires:** direct DB access + `ALL`. Run in order; a cache-clear follows.\n")
        return self._md(md)

    def api_step(self, id, title, severity, check, rationale, code, reversible=None):
        self.cells.append(self._header(id, title, severity, check, rationale, reversible, "api"))
        self.cells.append(self._code(code.rstrip() + "\n"))

    def sql_step(self, id, title, severity, check, rationale, code, reversible=None):
        self.cells.append(self._header(id, title, severity, check, rationale, reversible, "sql"))
        body = code.rstrip() + "\ncache_clear()\n"
        self.cells.append(self._code(body))

    def verify_step(self, check, expected=0, note=""):
        """Verify a check reached its expected count. Use expected>0 for checks with a documented,
        intentionally-accepted residual (e.g. ambiguous cases left for an owner) — otherwise Run-All
        trips on a hard assert==0 that was never going to be 0."""
        hdr = f"### ✅ Verify — `{check}` should now be {expected}"
        if note:
            hdr += f"  \n_{note}_"
        self.cells.append(self._md(hdr))
        self.cells.append(self._code(
            f'n = check("{check}")\n'
            f'assert n <= {expected}, f"REGRESSION: {check} = {{n}} (> expected {expected}) — investigate"\n'
            f'print("OK" if n == {expected} else f"{{n}} residual (expected {expected}, documented)")\n'))

    def note(self, markdown):
        self.cells.append(self._md(markdown))

    def to_notebook(self):
        return {"cells": self.cells, "metadata": {"language_info": {"name": "python"},
                "kernelspec": {"name": "python3", "display_name": "Python 3"}},
                "nbformat": 4, "nbformat_minor": 5}

    def write(self, path):
        json.dump(self.to_notebook(), open(path, "w"), indent=1)
        return path


def _demo(path):
    pb = Playbook("DEMO — remediation playbook", section="integrity")
    pb.api_step(id="SEV-EX", title="Set facility group sets non-compulsory", severity="SEVERE",
                check="org_units_not_in_compulsory_group_sets",
                rationale="Admin levels can't carry a facility type, so the set shouldn't be compulsory.",
                reversible="PATCH compulsory=true to revert.",
                code='''for uid in ["Bpx0589u8y0"]:
    cur = api("GET", f"/organisationUnitGroupSets/{uid}", params={"fields":"compulsory"})
    if cur is not None and cur.json().get("compulsory") is False:
        print("already non-compulsory:", uid)
    else:
        api("PATCH", f"/organisationUnitGroupSets/{uid}",
            body=[{"op":"replace","path":"/compulsory","value":False}])''')
    pb.verify_step("org_units_not_in_compulsory_group_sets")
    pb.sql_step(id="SEV-EX2", title="Fix Births combo category (Gender→Location)", severity="SEVERE",
                check="category_option_combos_disjoint",
                rationale="API E1120 blocks changing a combo's categories with live data; do it in SQL, UID-resolved.",
                code='''combo = resolve_id("categorycombo", "m2jTvAj5kkm")
gender = resolve_id("category", "cX5k9anHEHd", idcol="categoryid")
loc = resolve_id("category", "x3uo8LqiTBk", idcol="categoryid")
sql("update categorycombos_categories set categoryid=%s where categorycomboid=%s and categoryid=%s",
    (loc, combo, gender), note="Births: Gender->Location PHU/Community")''')
    pb.write(path)
    # validate it's well-formed and the helpers parse
    nb = json.load(open(path))
    assert nb["nbformat"] == 4 and len(nb["cells"]) >= 6
    compile("".join(nb["cells"][1]["source"]), "<setup>", "exec")
    print(f"wrote + validated demo notebook ({len(nb['cells'])} cells) -> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", metavar="PATH", help="write a small sample notebook to validate the format")
    a = ap.parse_args()
    if a.demo:
        _demo(a.demo)
    else:
        ap.print_help()
