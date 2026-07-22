#!/usr/bin/env python3
"""Auto-logging DHIS2 HTTP client shared by the cleanup scripts.

Auth via env vars (same convention as the dhis2-api skill):
  DHIS2_BASE_URL   e.g. http://dhis2-foo:8080   (no /api suffix)
  DHIS2_API_TOKEN  -> sent as 'Authorization: ApiToken <token>'
  or DHIS2_USER + DHIS2_PASS  -> HTTP basic auth

Every write (POST/PUT/PATCH/DELETE) is appended to OPS_LOG (default ./operations-log.md) so the
cleanup is fully auditable. Import this module, or instantiate D2() directly:

    from d2_client import D2
    d2 = D2()                      # reads env
    d2.get("/dataElements", fields="id,name", pageSize="5")
    d2.patch("/dataElements/abc", [{"op":"replace","path":"/name","value":"X"}], note="rename")
"""
import os, json, datetime
import httpx
try:
    from dotenv import load_dotenv, find_dotenv
    # usecwd=True: locate the .env relative to the WORKING directory (the per-instance run dir),
    # not this script's location — otherwise a stale .env higher up the skill tree (a previous
    # instance's) hijacks the connection and you hit "Name or service not known".
    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass

OPS_LOG = os.environ.get("DHIS2_OPS_LOG", "operations-log.md")


def _ensure_log_header(path):
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("# Operations Log\n\n| Timestamp | Method | Path | Note | HTTP |\n"
                    "|-----------|--------|------|------|------|\n")


class D2:
    def __init__(self, base=None, token=None, user=None, password=None, ops_log=None):
        base = (base or os.environ["DHIS2_BASE_URL"]).rstrip("/")
        self.base = base + "/api"
        self.ops_log = ops_log or OPS_LOG
        headers = {"Accept": "application/json"}
        auth = None
        token = token or os.environ.get("DHIS2_API_TOKEN")
        user = user or os.environ.get("DHIS2_USER")
        password = password or os.environ.get("DHIS2_PASS")
        if token:
            headers["Authorization"] = f"ApiToken {token}"
        elif user:
            auth = (user, password)
        self.client = httpx.Client(base_url=self.base, headers=headers, auth=auth, timeout=300)

    def _log(self, method, path, note, status):
        _ensure_log_header(self.ops_log)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.ops_log, "a") as f:
            f.write(f"| {ts} | {method} | `{path}` | {note} | {status} |\n")

    def get(self, path, **params):
        r = self.client.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    def get_raw(self, path, **params):
        return self.client.get(path, params=params or None)

    def post(self, path, body=None, note="", params=None):
        r = self.client.post(path, json=body, params=params)
        self._log("POST", path, note, r.status_code)
        return r

    def put(self, path, body, note=""):
        r = self.client.put(path, json=body)
        self._log("PUT", path, note, r.status_code)
        return r

    def patch(self, path, ops, note=""):
        """ops = list of JSON-Patch operations (application/json-patch+json)."""
        r = self.client.request("PATCH", path, json=ops,
                                 headers={"Content-Type": "application/json-patch+json"})
        self._log("PATCH", path, note, r.status_code)
        return r

    def delete(self, path, note=""):
        r = self.client.delete(path)
        self._log("DELETE", path, note, r.status_code)
        return r

    def cache_clear(self):
        return self.client.post("/maintenance", params={"cacheClear": "true"})


if __name__ == "__main__":
    d2 = D2()
    info = d2.get("/system/info")
    print("Connected:", d2.base, "| version", info.get("version"), info.get("revision"))
