"""Smoke test for our App Platform app "Facility Overview" (key: facility-overview).

Builds are installed with: curl -u admin:district -F file=@build/bundle/facility-overview-1.2.0.zip $BASE/api/apps
Passes against our 2.41 test server, times out on 2.42.
"""
import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DHIS2_URL", "http://localhost:8080")
USER = "admin"
PASS = "district"


def login_cookie():
    data = json.dumps({"username": USER, "password": PASS}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/auth/login", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        for c in r.headers.get_all("Set-Cookie") or []:
            n, _, v = c.split(";", 1)[0].partition("=")
            if n == "JSESSIONID":
                return v
    raise RuntimeError("no cookie")


def api_count(path, key):
    req = urllib.request.Request(f"{BASE}/api/{path}")
    req.add_header("Authorization", "Basic YWRtaW46ZGlzdHJpY3Q=")
    with urllib.request.urlopen(req) as r:
        return len(json.load(r)[key])


def main():
    expected = api_count("organisationUnits?level=2&paging=false&fields=id", "organisationUnits")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "JSESSIONID", "value": login_cookie(), "url": BASE}])
        page = ctx.new_page()
        page.goto(f"{BASE}/api/apps/facility-overview/index.html")
        page.wait_for_timeout(7000)
        assert "Facility Overview" in page.inner_text("body")
        page.get_by_test_id("org-unit-table").wait_for()
        rows = page.locator("[data-test='dhis2-uicore-datatablerow']").count()
        assert rows == expected, f"table shows {rows}, API has {expected}"
        page.get_by_test_id("refresh-button").click()
        page.wait_for_timeout(3000)
        assert "Updated" in page.inner_text("body")
        print("PASS")
        browser.close()


if __name__ == "__main__":
    main()
