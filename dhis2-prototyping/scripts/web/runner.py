"""Playwright driver for scripted DHIS2 Capture (web) entry flows, with effort counting.

    from runner import Web
    w = Web(user="demo_enumerator", password="...", video_dir="/tmp/vid")   # or D2_USER/D2_PASS
    w.goto_capture("/search?programId=<uid>&orgUnitId=<uid>")
    w.cap("Register a new household")        # caption, timed from the start of the video
    w.text("Phone number", "0761234567")
    ...
    video = w.close(); w.save_log("run.json")

Every action is logged with a timestamp relative to context creation (close enough to the
video start), plus running counts of clicks, typed characters and screen changes. The log
feeds render.py (captions + counter) and the effort table.

Environment:
    D2_WEB   base URL of the instance through a *localhost* origin (default http://localhost:8089).
             Capture refuses to load over plain http on any other origin; forward one first:
             socat TCP-LISTEN:8089,bind=127.0.0.1,fork,reuseaddr TCP:<dhis2-host>:8080
    D2_USER / D2_PASS   default credentials (else admin/district).

Record failed API writes as flow failures: `w.api_errors` holds every POST/PUT >= 400. A save
that fails in Capture only shows a transient toast, so a flow that "passed" may have saved nothing.
"""
import base64
import json
import os
import re
import time

BASE = os.environ.get("D2_WEB", "http://localhost:8089").rstrip("/")


class FlowError(Exception):
    pass


class Web:
    def __init__(self, user=None, password=None, video_dir=None, slow=1.0,
                 viewport=(1280, 800), headless=True, base=None):
        from playwright.sync_api import sync_playwright  # imported late so --help works without it
        self.base = (base or BASE).rstrip("/")
        user = user or os.environ.get("D2_USER", "admin")
        password = password or os.environ.get("D2_PASS", "district")
        self.pw = sync_playwright().start()
        # the sandbox's 64 MB /dev/shm crashes long Chromium runs without this flag
        self.br = self.pw.chromium.launch(headless=headless, args=["--disable-dev-shm-usage"])
        kw = {"viewport": {"width": viewport[0], "height": viewport[1]}}
        if video_dir:
            kw["record_video_dir"] = video_dir
            kw["record_video_size"] = {"width": viewport[0], "height": viewport[1]}
        self.ctx = self.br.new_context(**kw)
        tok = base64.b64encode(f"{user}:{password}".encode()).decode()
        r = self.ctx.request.get(f"{self.base}/api/me", headers={"Authorization": f"Basic {tok}"})
        if not r.ok:
            self.close()
            raise FlowError(f"login as {user} failed: HTTP {r.status}")
        self.page = self.ctx.new_page()
        self.api_errors = []
        self.page.on("response", self._on_response)
        self.t0 = time.time()
        self.log = []
        self.taps = self.chars = self.screens = self.scrolls = 0
        self.slow = slow
        self.frame = None

    def _on_response(self, r):
        if "/api/" in r.url and r.status >= 400 and r.request.method in ("POST", "PUT"):
            try:
                self.api_errors.append((r.status, r.url.split("/api/", 1)[1][:80], r.text()[:1500]))
            except Exception:
                self.api_errors.append((r.status, r.url.split("/api/", 1)[1][:80], ""))

    # ---------------------------------------------------------------- logging
    def _log(self, kind, text=""):
        self.log.append({"t": round(time.time() - self.t0, 3), "kind": kind, "text": text,
                         "taps": self.taps, "chars": self.chars})

    def cap(self, text):
        """Caption shown from now until the next caption."""
        self._log("CAP", text)

    def pause(self, s=0.9):
        time.sleep(s * self.slow)

    def screen(self):
        """Count a screen change (a person has to reorient)."""
        self.screens += 1

    # ------------------------------------------------------------- navigation
    def goto_capture(self, hash_path, timeout=180000):
        """Open Capture at a hash route (e.g. /search?programId=&orgUnitId=, /new?...,
        /enrollment?enrollmentId=...). On 2.42+ Capture runs inside the global shell's iframe."""
        self.page.goto(f"{self.base}/dhis-web-capture/index.html#{hash_path}",
                       wait_until="networkidle", timeout=timeout)
        end = time.time() + 30
        self.frame = None
        while time.time() < end and self.frame is None:
            fr = [f for f in self.page.frames if "dhis-web-capture" in f.url]
            if fr:
                self.frame = fr[-1]
            else:
                time.sleep(0.5)
        if self.frame is None:          # older versions without the shell: the page itself
            self.frame = self.page.main_frame
        self.frame.wait_for_load_state("networkidle")
        self.screen()

    @property
    def f(self):
        return self.frame or self.page.main_frame

    # ---------------------------------------------------------------- actions
    def click(self, locator, count=True, timeout=20000, force=False):
        loc = self.f.locator(locator) if isinstance(locator, str) else locator
        loc.first.wait_for(state="attached" if force else "visible", timeout=timeout)
        loc.first.scroll_into_view_if_needed()
        loc.first.click(force=force)
        if count:
            self.taps += 1
        self._log("TAP")
        time.sleep(0.25 * self.slow)

    def click_text(self, text, exact=True, within=None, count=True):
        base = within if within is not None else self.f
        self.click(base.get_by_text(text, exact=exact), count=count)

    def button(self, name, exact=True, count=True, timeout=20000):
        self.click(self.f.get_by_role("button", name=name, exact=exact), count=count, timeout=timeout)

    def type_into(self, loc, text, delay=40):
        """Focus (not click: date pickers pop up and cover neighbours) and type."""
        loc.first.scroll_into_view_if_needed()
        loc.first.focus()
        self.taps += 1
        loc.first.press_sequentially(text, delay=delay)
        self.chars += len(text)
        self._log("TYPE", text)

    def field_box(self, label, timeout=20):
        """The form field whose visible text starts with `label`, as a *stable* locator
        (by its data-test id, not by index: rules re-render the form and shift indexes)."""
        js = """els => els.map(e => {
            const inner = e.querySelector('[data-test^="form-field-"]');
            const id = e.getAttribute('data-test') === 'form-field' && inner
                ? inner.getAttribute('data-test') : e.getAttribute('data-test');
            return [id, (e.innerText || '').trim()];
        })"""
        boxes = self.f.locator('[data-test="form-field"], [data-test^="dataentry-field-"]')
        end = time.time() + timeout
        while time.time() < end:
            for tid, t in boxes.evaluate_all(js):
                if tid and t.startswith(label):
                    return self.f.locator(f'[data-test="{tid}"]')
            time.sleep(0.3)
        raise FlowError(f"field {label!r} not found")

    def text(self, label, value):
        inp = self.field_box(label).locator("input, textarea")
        self.type_into(inp, value)
        inp.first.press("Tab")

    def radio(self, label, option):
        """Radio inputs overlay their labels: click the input by label with force."""
        self.click(self.field_box(label).get_by_label(option, exact=True), force=True)

    def _option(self, option, timeout=10):
        """Single-select option. Capture's own selects use [role=option][aria-label]; @dhis2/ui
        SingleSelect options have no ARIA role but a data-test. Use whichever is present."""
        cands = [self.f.locator(f'[role="option"][aria-label="{option}"]'),
                 self.f.locator('[data-test="dhis2-uicore-singleselectoption"]').filter(
                     has_text=re.compile("^" + re.escape(option) + "$"))]
        end = time.time() + timeout
        while time.time() < end:
            for c in cands:
                if c.count():
                    return c
            time.sleep(0.2)
        raise FlowError(f"option {option!r} not found")

    def select(self, label, option):
        box = self.field_box(label)
        trigger = box.locator('[role="combobox"], [data-test="dhis2-uicore-select-input"]')
        self.click(trigger)
        self.click(self._option(option))

    def multiselect(self, label, options):
        box = self.field_box(label)
        self.click(box.locator('[data-test="dhis2-uicore-select-input"]'))
        for o in options:
            self.click(self.f.locator('[data-test="dhis2-uicore-multiselectoption"]').filter(
                has_text=re.compile("^" + re.escape(o) + "$")))
        self.page.keyboard.press("Escape")     # closes the menu; a person would click away
        self.taps += 1
        self._log("TAP")

    def date(self, label, iso):
        """Type a date (YYYY-MM-DD, Capture's default web format) unless the field already
        holds it: some forms prefill today's date, some leave it empty."""
        inp = self.field_box(label).locator("input").first
        cur = inp.input_value()
        if cur == iso:
            return
        if cur:
            inp.focus()
            inp.press("Control+a")
            inp.press("Delete")
        self.type_into(inp, iso)
        inp.press("Tab")

    def age(self, label, years):
        """AGE value type: input 0 is the date, input 1 the years."""
        inp = self.field_box(label).locator("input").nth(1)
        self.type_into(inp, str(years))
        inp.press("Tab")

    def coords(self, lat, lon, box='[data-test="dataentry-field-geometry"]'):
        b = self.f.locator(box)
        for cls, v in (("latitude", lat), ("longitude", lon)):
            inp = b.locator(f'input[class*="_{cls}TextInput"]')
            self.type_into(inp, str(v))
            inp.press("Tab")

    def orgunit(self, label, search, name):
        """Org unit field: open the tree, type a few letters, click the node."""
        box = self.field_box(label)
        self.click(box.locator('[data-test="org-unit-selector-trigger"]'))
        inp = box.locator("input").first
        inp.press_sequentially(search, delay=60)
        self.chars += len(search)
        self._log("TYPE", search)
        time.sleep(1.5)
        self.click(self.f.get_by_text(name, exact=True).last)

    def answer_dialog(self, button, timeout=4000):
        """Click a dialog button if the dialog appears ("Yes, create new event", "Discard",
        duplicate warnings). Returns False if it did not appear: new events completed from the
        Complete button may not ask."""
        b = self.f.get_by_role("button", name=button, exact=True)
        try:
            b.first.wait_for(timeout=timeout)
        except Exception:
            return False
        self.button(button)
        return True

    # ----------------------------------------------------------------- output
    def shot(self, path, full_page=False):
        self.page.screenshot(path=path, full_page=full_page, animations="disabled")

    def counts(self):
        return {"taps": self.taps, "chars": self.chars, "scrolls": self.scrolls,
                "screens": self.screens}

    def save_log(self, path, **extra):
        with open(path, "w") as fh:
            json.dump({**extra, "counts": self.counts(), "log": self.log}, fh, indent=0)

    def close(self):
        """Close everything (orphaned Chromium processes pile up) and return the video path."""
        video = None
        try:
            if getattr(self, "page", None) and self.page.video:
                video = self.page.video.path()
        except Exception:
            pass
        for obj in ("ctx", "br"):
            try:
                getattr(self, obj).close()
            except Exception:
                pass
        try:
            self.pw.stop()
        except Exception:
            pass
        return video


def run_flow(name, fn, out_dir, record=False, **web_kw):
    """Run `fn(w)` and write <out_dir>/<name>.json with error, seconds, counts and the timed log.
    A failed POST/PUT counts as a failure even if every UI step passed. With record=True the
    raw video lands at <out_dir>/<name>.webm."""
    import shutil
    os.makedirs(out_dir, exist_ok=True)
    vid_tmp = os.path.join(out_dir, f".vid_{name}")
    shutil.rmtree(vid_tmp, ignore_errors=True)
    w = Web(video_dir=vid_tmp if record else None, **web_kw)
    err = None
    try:
        fn(w)
        w.cap("Done")
        w.pause(1.5)
    except Exception as e:  # keep the log and a screenshot for debugging
        err = f"{type(e).__name__}: {str(e)[:500]}"
        try:
            w.shot(os.path.join(out_dir, f"{name}_error.png"))
        except Exception:
            pass
    secs = round(time.time() - w.t0, 1)
    if not err and w.api_errors:
        err = "API error: " + json.dumps(w.api_errors[0])[:600]
    video = w.close()
    w.save_log(os.path.join(out_dir, f"{name}.json"), flow=name, error=err, seconds=secs,
               api_errors=w.api_errors)
    if record and video:
        shutil.move(video, os.path.join(out_dir, f"{name}.webm"))
        shutil.rmtree(vid_tmp, ignore_errors=True)
    summary = {"flow": name, "error": err, "seconds": secs, "counts": w.counts()}
    print(json.dumps(summary))
    return summary
