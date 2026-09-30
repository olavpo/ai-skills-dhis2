#!/usr/bin/env python3
"""One-shot smoke probe for a DHIS2 app under review.

Run this before writing real tests to see the lay of the land: frame structure,
console errors, failed requests, HTTP errors, and a full-page screenshot.

Usage:
    python3 probe.py --base http://dhis2-agent-review:8080 --dev http://localhost:8081/ \
                     [--user admin] [--password district] [--screenshot /tmp/probe.png] \
                     [--wait-selector "[data-test='dhis2-uicore-...']"]

--base is the DHIS2 instance; --dev is the app dev server (port from
webpack.config.js / d2.config.js — don't assume).
"""
import argparse
import sys
import urllib.request

from playwright.sync_api import sync_playwright


def login_cookie(base, user, password):
    """Return the authenticated session cookie (name, value).

    Uses Basic-auth `GET /api/me` — works on every DHIS2 version 2.40+.
    Cookie name varies per instance (JSESSIONID, JSESSIONID_hmis_trk, ...) —
    match on substring. We deliberately do NOT try `/api/auth/login` first:
    on 2.40 it 302s to the legacy login page, urllib follows the redirect,
    and we silently capture an *anonymous* cookie (HTTP 200, no error).
    """
    import base64
    req = urllib.request.Request(
        f"{base}/api/me",
        headers={"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()},
    )
    with urllib.request.urlopen(req) as r:
        for c in r.headers.get_all("Set-Cookie") or []:
            head = c.split(";", 1)[0]
            n, _, v = head.partition("=")
            if "JSESSIONID" in n:
                return n.strip(), v.strip()
    raise RuntimeError("No JSESSIONID returned from /api/me")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="DHIS2 instance URL")
    ap.add_argument("--dev", required=True, help="App dev-server URL")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="district")
    ap.add_argument("--screenshot", default="/tmp/probe.png")
    ap.add_argument("--wait-selector", default=None,
                    help="CSS selector to wait for after load. Use for apps "
                         "that poll/scan and never reach network-idle.")
    args = ap.parse_args()

    console, errors, failed, http_err = [], [], [], []
    from urllib.parse import urlparse
    dev_host = urlparse(args.dev).hostname or "localhost"

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = b.new_context()
        cn, cv = login_cookie(args.base, args.user, args.password)
        ctx.add_cookies([{
            "name": cn, "value": cv, "domain": dev_host, "path": "/",
            "httpOnly": True, "sameSite": "Lax",
        }])
        page = ctx.new_page()
        page.on("console", lambda m: console.append((m.type, m.text)))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: failed.append((r.url, r.failure)))
        page.on("response", lambda r: http_err.append((r.status, r.url)) if r.status >= 400 else None)
        page.goto(args.dev, wait_until="domcontentloaded")
        # Deliberately NOT networkidle: apps that poll/scan on load never go
        # idle and the wait times out. Wait on a DOM signal instead when given.
        if args.wait_selector:
            page.wait_for_selector(args.wait_selector, timeout=30_000)
        page.wait_for_timeout(2500)
        page.screenshot(path=args.screenshot, full_page=True)
        # A login screen means the session is NOT authenticated even though
        # nothing errored — fail loudly instead of printing a green-looking probe.
        login_markers = ("Please sign in", "j_username")
        auth_failed = False
        if any(page.get_by_text(m).count() for m in login_markers[:1]) \
                or page.locator("input[name=j_username]").count() \
                or "login" in page.url.lower():
            auth_failed = True
            print("AUTH FAILED — page is a login screen, not the app. "
                  "Dev server: fill the shell's sign-in form (see playwright-patterns.md); "
                  "instance-served: check cookie domain is same-site with the app origin.")
        print("URL:", page.url)
        print("Frames:", [f.url for f in page.frames])
        print("Console errors:", [m for t, m in console if t == "error"][:5])
        print("Page errors:", errors[:3])
        print("Failed requests:", failed[:5])
        print("HTTP >=400:", http_err[:5])
        print("Screenshot:", args.screenshot)
        b.close()
    if auth_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
