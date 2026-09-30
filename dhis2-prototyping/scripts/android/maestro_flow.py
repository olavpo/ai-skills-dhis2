#!/usr/bin/env python3
"""Build Maestro flows for Capture Android from Python, with effort counts.

    from maestro_flow import Flow, rx
    f = Flow("A_hh1")
    f.cap("Register the household")        # caption + phase boundary
    f.field("Phone number.*", "0761234567", kb=False)
    f.radio("No", label="Has a latrine.*")
    f.save_complete()
    f.write("flows/A_hh1.yaml")              # one file, for dry runs
    f.write_phases("flows/A_hh1/")           # one file per caption, for record.py
    counts = f.counts()                      # {"taps":…, "chars":…, "scrolls":…, "screens":…}

    python3 maestro_flow.py --example OUT_DIR   # writes a tiny example flow

Every command is classified when it is added (tap, typed characters, scroll, screen
change), so the effort counts come from exactly what the flow does. `ensure()` scrolls
a target into view before a tap; it is an automation aid and is not counted as effort.
Captions are labelled no-op commands (`CAP|text`) that record.py turns into subtitles
and phase boundaries.

Selectors are regular expressions matched against the whole text: pass literal
labels through `rx()`, and end mandatory labels with `.*` to allow the `*` marker.
"""
import json
import os
import re
import sys


def rx(s):
    """Escape a literal label for a Maestro text selector (keeps spaces readable)."""
    return re.escape(s).replace("\\ ", " ")


def q(s):
    return json.dumps(s, ensure_ascii=False)


_PCT_POINT = re.compile(r"^\s*(\d+(?:\.\d+)?)%\s*,\s*(\d+(?:\.\d+)?)%\s*$")


def check_point(point):
    """Maestro only parses integer percentages ("78%,90%"); a decimal one fails silently
    (the error only shows in commands.json). Pixels ("880,2150") are always fine."""
    m = _PCT_POINT.match(point)
    if m and any("." in g for g in m.groups()):
        raise ValueError(f"Maestro point percentages must be integers, got {point!r}; use pixels")
    return point.replace(" ", "")


class Flow:
    def __init__(self, name, app_id="com.dhis2", pause_ms=900):
        self.name = name
        self.app_id = app_id
        self.pause_ms = pause_ms
        self.taps = self.chars = self.scrolls = self.screens = 0
        self.phases = [["", []]]          # [caption, lines]; phase 0 = before the first caption

    @property
    def lines(self):
        return self.phases[-1][1]

    def _add(self, *lines):
        self.lines.extend(lines)

    # ------------------------------------------------------------ primitives
    def cap(self, text):
        """Caption shown from here until the next one; also starts a new recording phase."""
        self.phases.append([text, []])
        self._add("- evalScript:", "    script: ${1}", f"    label: {q('CAP|' + text)}")

    def pause(self, ms=None):
        """A visible pause for viewers (a wait for text that never appears)."""
        self._add("- extendedWaitUntil:", '    visible: "zzz-never-there"',
                  f"    timeout: {ms or self.pause_ms}", "    optional: true", '    label: "PAUSE"')

    def screen(self):
        """Count a screen change (the person has to orient themselves)."""
        self.screens += 1

    def tap(self, text=None, below=None, index=None, id=None, point=None, label=None,
            count=True, optional=False):
        if point:
            self._add("- tapOn:", f"    point: {q(check_point(point))}")
        elif id:
            self._add("- tapOn:", f"    id: {q(id)}")
        else:
            self._add("- tapOn:", f"    text: {q(text)}")
            if below:
                self._add(f"    below: {q(below)}")
            if index is not None:
                self._add(f"    index: {index}")
        self._add(f"    label: {q(label or 'TAP')}")
        if optional:
            self._add("    optional: true")
        if count:
            self.taps += 1

    def type(self, s):
        self._add(f"- inputText: {q(s)}")
        self.chars += len(s)

    def hide_kb(self):
        self._add("- hideKeyboard")
        self.taps += 1

    def back(self):
        self._add("- back")
        self.taps += 1

    def scroll(self):
        """A scroll the person makes (counted)."""
        self._add("- scroll")
        self.scrolls += 1

    def wait_for(self, text, ms=15000):
        self._add("- extendedWaitUntil:", f"    visible: {q(text)}", f"    timeout: {ms}")

    def settle(self, ms=600):
        """After a tap that makes rules show fields: let them appear before scrolling."""
        self._add("- waitForAnimationToEnd")
        self.pause(ms)

    def relaunch(self, count_as_taps=2):
        """Back to the app's start screen; `back` from a dashboard is unpredictable.
        Counted as the back taps a person would make."""
        self._add("- launchApp:", f"    appId: {q(self.app_id)}", "    stopApp: true")
        self.taps += count_as_taps

    # ---------------------------------------------------------------- widgets
    def ensure(self, text, below=None, direction=None, timeout=8000):
        """Scroll the target into view if needed (not counted). Scroll to the option, not
        its label: the label can be visible while its options are under the fold."""
        self._add("- scrollUntilVisible:")
        if below:
            self._add("    element:", f"      text: {q(text)}", f"      below: {q(below)}")
        else:
            self._add(f"    element: {q(text)}")
        self._add("    visibilityPercentage: 60", f"    timeout: {timeout}")
        if direction:
            self._add(f"    direction: {direction}")

    def field(self, label, value, kb=True):
        self.ensure(label)
        self.tap(label)
        self.type(value)
        if kb:
            self.hide_kb()

    def radio(self, option, label=None):
        """Radio and tile options repeat (Yes/No everywhere): anchor with the field label."""
        self.ensure(option, below=label)
        self.tap(option, below=label)

    def dropdown(self, label, option, sheet=True):
        """sheet=False: short option sets rendered as tiles (one tap, like radio).
        sheet=True: long lists open a bottom sheet; scroll inside it, then an optional Done
        (single-select sheets need it, tile-rendered ones do not)."""
        if not sheet:
            self.radio(rx(option), label=label)
            return
        self.ensure(label)
        self.tap(label)
        self.ensure(rx(option))
        self.tap(rx(option))
        self.tap("Done", optional=True)

    def multi(self, label, options):
        self.ensure(label)
        self.tap(label)
        for o in options:
            self.ensure(rx(o))
            self.tap(rx(o))
        self.tap("Done")

    def save_complete(self, save_id="actionButton", complete_text="Complete"):
        """Capture 3.x: the save FAB, then Complete in the dialog."""
        self.tap(id=save_id)
        self.tap(complete_text)
        self.screen()

    # ----------------------------------------------------------------- output
    def _header(self):
        return [f"appId: {self.app_id}", "---"]

    def yaml(self):
        body = [ln for _, lines in self.phases for ln in lines]
        return "\n".join(self._header() + body) + "\n"

    def write(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            f.write(self.yaml())
        return path

    def write_phases(self, directory):
        """One flow file per caption (NN.yaml) plus phases.json with the captions, so
        record.py can make one screen recording per phase."""
        os.makedirs(directory, exist_ok=True)
        index = []
        n = 0
        for caption, lines in self.phases:
            if not lines:
                continue
            name = f"{n:02d}.yaml"
            with open(os.path.join(directory, name), "w") as f:
                f.write("\n".join(self._header() + lines) + "\n")
            index.append({"file": name, "caption": caption})
            n += 1
        with open(os.path.join(directory, "phases.json"), "w") as f:
            json.dump({"flow": self.name, "phases": index, "counts": self.counts()}, f, indent=1)
        return index

    def counts(self):
        return {"taps": self.taps, "chars": self.chars, "scrolls": self.scrolls, "screens": self.screens}


def example():
    """A tiny, generic flow: open a program, search, register one tracked entity."""
    f = Flow("example")
    f.cap("Open the program")
    f.ensure(rx("Demo household program"))
    f.tap(rx("Demo household program"))
    f.screen()
    f.cap("Search by phone number before registering")
    f.field("Phone number.*", "0760000001", kb=False)
    f.tap("Search", index=1)
    f.screen()
    f.cap("Not found: register a new household")
    f.tap(point="880,2150")          # a FAB is not in the accessibility tree: tap by pixels
    f.field("Head of household.*", "Test Person")
    f.radio("No", label="Female-headed.*")
    f.settle()
    f.dropdown("Water source.*", "Protected well")
    f.save_complete()
    f.pause(1500)
    return f


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--example":
        fl = example()
        out = sys.argv[2]
        fl.write(os.path.join(out, "example.yaml"))
        fl.write_phases(os.path.join(out, "example"))
        print(json.dumps(fl.counts()))
    else:
        print(__doc__)
