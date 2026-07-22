#!/usr/bin/env python3
"""Logged psycopg2 wrapper for the guard-blocked SQL fixes (see references/playbook.md §6).

Use ONLY when an API business-guard (E1120/E4030/E4056/E8031) blocks an otherwise-correct fix.
Every non-SELECT is appended to SQL_LOG with row count + a mandatory note explaining why the API
couldn't do it. Always `D2().cache_clear()` after metadata changes.

Connection via env vars:
  PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD   (or pass dsn=... )

  from db_client import DB
  db = DB()
  db.q("select count(*) from dataelement;")                       # SELECT -> rows
  db.q("update userinfo set username=%s where uid=%s;", ("new","ABC"),
       fetch=False, note="rename username (API forbids, E4056)")  # write -> rowcount, logged

Helper: db.fk_refs('categoryoptioncombo')  -> every (table,column) FK-referencing that table.
"""
import os, datetime
import psycopg2
# Load .env independently so db_client works standalone (not only after d2_client import).
# Without this, an import-alone falls back to localhost:5432 and silently misses PGHOST/PGPORT.
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

SQL_LOG = os.environ.get("DHIS2_SQL_LOG", "sql-operations-log.md")


def _ensure_log_header(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("# Direct SQL Operations Log\n\nDirect SQL used only where the DHIS2 API enforces "
                    "business guards that block the otherwise-correct fix.\n\n"
                    "| Timestamp | Rows | Note | SQL |\n|-----------|------|------|-----|\n")


class DB:
    def __init__(self, dsn=None, **kw):
        if dsn:
            self.dsn = dsn; self.kw = {}
        else:
            self.dsn = None
            self.kw = dict(
                host=kw.get("host", os.environ.get("PGHOST", "localhost")),
                port=kw.get("port", os.environ.get("PGPORT", "5432")),
                dbname=kw.get("dbname", os.environ.get("PGDATABASE", "dhis2")),
                user=kw.get("user", os.environ.get("PGUSER", "dhis")),
                password=kw.get("password", os.environ.get("PGPASSWORD", "dhis")),
            )

    def _connect(self):
        return psycopg2.connect(self.dsn) if self.dsn else psycopg2.connect(**self.kw)

    def q(self, sql, args=None, fetch=True, note=""):
        c = self._connect(); cur = c.cursor()
        cur.execute(sql, args or ())
        rows = cur.fetchall() if (fetch and cur.description) else None
        affected = cur.rowcount
        if not fetch:
            c.commit()
            _ensure_log_header(SQL_LOG)
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            flat = " ".join(sql.split())[:140]
            with open(SQL_LOG, "a") as f:
                f.write(f"| {ts} | {affected} | {note} | `{flat}` |\n")
        cur.close(); c.close()
        return rows if fetch else affected

    def fk_refs(self, table):
        """All (referencing_table, column) FKs pointing at `table` — run before any delete/migrate."""
        return self.q("""
            select tc.table_name, kcu.column_name
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu on kcu.constraint_name=tc.constraint_name
            join information_schema.constraint_column_usage ccu on ccu.constraint_name=tc.constraint_name
            where tc.constraint_type='FOREIGN KEY' and ccu.table_name=%s;""", (table,))


if __name__ == "__main__":
    db = DB()
    print("dataelement count:", db.q("select count(*) from dataelement;")[0][0])
