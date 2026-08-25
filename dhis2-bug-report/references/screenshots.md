# Screenshots

## Setup

Check for a browser before assuming there is none:

```bash
python3 -c "from playwright.sync_api import sync_playwright
with sync_playwright() as p: print(p.chromium.executable_path)"
```

If Chromium is already present this prints a path and there is nothing to install. `ls ~/.cache/ms-playwright` is not a reliable check, because the browsers may live elsewhere (for example `/opt/pw-browsers`).

If it is missing, `python3 -m playwright install chromium`. Avoid `--with-deps` unless the launch actually fails for a missing library: it runs `apt-get update`, which fails hard when any configured repository is unreachable, and that failure aborts the browser install too. If you hit that, move the offending file out of `/etc/apt/sources.list.d/` and retry.

Two things that will otherwise waste a cycle. Play instances serve a certificate Chromium rejects, so the browser context needs `ignore_https_errors=True`. And app pages behind DHIS2 auth redirect to the login screen, so log in first.

## Authenticating

`POST /api/auth/login` with a JSON body on the browser context's `request` object. That object shares its cookie jar with pages opened from the same context, so the session carries over without touching a login form:

```python
ctx = browser.new_context(ignore_https_errors=True)
ctx.request.post(base + "/api/auth/login",
                 data={"username": "admin", "password": "district"},
                 headers={"Content-Type": "application/json"})
page = ctx.new_page()
page.goto(base + "/dhis-web-data-visualizer/index.html#/" + uid)
```

Opening a saved visualization by uid in the URL fragment is why the workflow creates one through the API: the whole configuration loads from a single URL, identically on every instance.

Analytics can take a while. Wait for a selector, then sleep a few seconds more so cell values finish rendering; a screenshot taken at first paint shows empty cells and looks like a different bug.

One trap that costs a wasted screenshot. DHIS2 serves apps inside an iframe from the global shell, so `page.wait_for_selector("table")` at top level never matches — `document.querySelectorAll("table").length` returns 0 while the table is plainly visible. Use `page.frame_locator("iframe").locator("table")`. If the wait is wrapped in a bare try/except the failure is invisible and the screenshot succeeds anyway on the fixed sleep, which hides the problem until a slow instance produces an empty capture. `scripts/shoot.py` checks the frame first, falls back to top level, and warns on stderr if it never saw the selector.

## Capturing the app's own requests

To see what the app asks the server for, listen on the page's request event and filter. `scripts/shoot.py --requests analytics` does this. Useful for reading the exact analytics parameters the app sends, which is often the difference between guessing at a cause and knowing one.

## Cropping

Raw screenshots are mostly empty canvas. `scripts/crop.py` trims to the content and upscales. It needs Pillow (`pip install Pillow`).

Keep the layout controls in frame when they explain the configuration — the Columns and Rows chips make a pivot screenshot self-explanatory. Cut the left navigation panel, which never adds anything. The default `--left 232 --top 64` does both.

Set `--right` to something near the right edge of the table. Without it the crop keeps the full-width layout bar and you get a wide image with a small table in the corner.

Name files so the ticket can refer to them in order: `01-nan-subtotals-2.43.1.png`, `02-mixed-data-element.png`. Present them to the user alongside the ticket file.
