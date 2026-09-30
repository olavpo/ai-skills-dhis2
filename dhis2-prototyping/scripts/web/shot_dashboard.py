#!/usr/bin/env python3
"""Screenshot a DHIS2 dashboard for a report.

    python3 shot_dashboard.py URL DASHBOARD_UID out.png [--user U --password P] [--height 3200]

The dashboard scrolls inside its own container, so full_page does not capture it: use a tall
viewport, scroll to trigger lazily loaded items, return to the top, then shoot. Take dashboard
screenshots after the *last* analytics run (and re-run analytics after an instance restart:
analytics tables are UNLOGGED and come back empty).
"""
import argparse
import base64
import os
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("dashboard")
    ap.add_argument("out")
    ap.add_argument("--user", default=os.environ.get("D2_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("D2_PASS", "district"))
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=3200)
    ap.add_argument("--settle", type=float, default=20, help="seconds to wait for items to render")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(args=["--disable-dev-shm-usage"])
        try:
            ctx = br.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=1)
            tok = base64.b64encode(f"{a.user}:{a.password}".encode()).decode()
            r = ctx.request.get(f"{a.url.rstrip('/')}/api/me", headers={"Authorization": f"Basic {tok}"})
            if not r.ok:
                raise SystemExit(f"login failed: HTTP {r.status}")
            pg = ctx.new_page()
            pg.goto(f"{a.url.rstrip('/')}/dhis-web-dashboard/#/{a.dashboard}", wait_until="networkidle",
                    timeout=180000)
            time.sleep(a.settle)
            for _ in range(6):
                pg.mouse.wheel(0, 900)
                time.sleep(3)
            pg.keyboard.press("Home")
            time.sleep(3)
            pg.screenshot(path=a.out, full_page=True, animations="disabled")
        finally:
            br.close()
    print("saved", a.out)


if __name__ == "__main__":
    main()
