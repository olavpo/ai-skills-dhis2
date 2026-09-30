"""Shared HTTP helpers for the prototyping scripts.

Connection comes from the environment, never from defaults in code:

    export D2_URL=http://dhis2-<name>:8080      # no trailing slash needed
    export D2_AUTH=<user>:<password>             # or D2_TOKEN=d2pat_...

Every script also takes --url to override D2_URL.
"""
import json
import os
import sys


def session(url=None):
    """Return (requests.Session, base_url). Exits with a clear message when
    requests is missing or the connection is not configured."""
    try:
        import requests
    except ImportError:
        sys.exit("needs requests: python3 -m venv .venv && .venv/bin/pip install requests")
    base = (url or os.environ.get("D2_URL", "")).rstrip("/")
    if not base:
        sys.exit("set D2_URL (or pass --url)")
    s = requests.Session()
    if os.environ.get("D2_TOKEN"):
        s.headers["Authorization"] = "ApiToken " + os.environ["D2_TOKEN"]
    elif os.environ.get("D2_AUTH"):
        s.auth = tuple(os.environ["D2_AUTH"].split(":", 1))
    else:
        sys.exit("set D2_AUTH=user:password or D2_TOKEN")
    return s, base


def as_json(r):
    """r.json(), or exit with the status and the start of the body (an HTML login
    page, a proxy error) when the server did not answer with JSON."""
    try:
        return r.json()
    except ValueError:
        sys.exit(f"{r.request.method} {r.url} -> HTTP {r.status_code}, not JSON: {r.text[:200]!r}")


def metadata_errors(report):
    """Yield (type, uid, errorCode, message) from a /api/metadata import report."""
    body = report.get("response", report)
    for tr in body.get("typeReports", []):
        for orr in tr.get("objectReports", []):
            for er in orr.get("errorReports", []):
                yield (tr.get("klass", "").rsplit(".", 1)[-1], orr.get("uid"),
                       er.get("errorCode"), er.get("message", ""))


def tracker_errors(report):
    """Yield (trackerType, uid, errorCode, message) from a /api/tracker import report."""
    for e in report.get("validationReport", {}).get("errorReports", []):
        yield e.get("trackerType"), e.get("uid"), e.get("errorCode"), e.get("message", "")


def load_json(path):
    with open(path) as f:
        return json.load(f)
