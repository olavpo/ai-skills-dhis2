#!/usr/bin/env python3
"""See and poke the Android screen through `uiautomator dump`.

    ui.py                         # print every node: text | content-desc | resource-id | bounds
    ui.py find REGEX              # nodes whose text or content-desc fully matches REGEX
    ui.py tap REGEX [--index N]   # tap the centre of the N-th match
    ui.py wait REGEX [--timeout S]

The dump works even when screenshots are blocked (allowScreenCapture off), and it is
the quickest way to find selectors for Maestro flows. It sometimes comes back empty
while the screen animates, so every read retries before giving up.

Device: --serial, else $ANDROID_SERIAL, else adb's default. Import it from other
scripts: `from ui import dump, find, tap, wait_for`.
"""
import argparse
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def adb(serial, *args, timeout=30):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def dump(serial=None, retries=4):
    """Return a list of nodes (dicts); retry when the hierarchy comes back empty."""
    for attempt in range(retries):
        adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = adb(serial, "exec-out", "cat", "/sdcard/ui.xml").stdout
        nodes = []
        if "<hierarchy" in xml:
            try:
                root = ET.fromstring(xml[xml.index("<?xml") if "<?xml" in xml else xml.index("<hierarchy"):])
            except ET.ParseError:
                root = None
            if root is not None:
                for n in root.iter("node"):
                    m = BOUNDS.match(n.get("bounds", ""))
                    box = tuple(int(v) for v in m.groups()) if m else None
                    nodes.append({"text": n.get("text", ""), "desc": n.get("content-desc", ""),
                                  "id": n.get("resource-id", ""), "cls": n.get("class", ""),
                                  "clickable": n.get("clickable") == "true", "bounds": box})
        if any(n["text"] or n["desc"] for n in nodes):
            return nodes
        time.sleep(1 + attempt)
    return []


def find(regex, serial=None, nodes=None):
    rx = re.compile(regex)
    nodes = dump(serial) if nodes is None else nodes
    return [n for n in nodes if (n["text"] and rx.fullmatch(n["text"])) or (n["desc"] and rx.fullmatch(n["desc"]))]


def centre(node):
    x1, y1, x2, y2 = node["bounds"]
    return (x1 + x2) // 2, (y1 + y2) // 2


def tap(regex, serial=None, index=0):
    hits = find(regex, serial)
    if len(hits) <= index:
        return False
    x, y = centre(hits[index])
    adb(serial, "shell", "input", "tap", str(x), str(y))
    return True


def wait_for(regex, serial=None, timeout=60, interval=1.5):
    end = time.time() + timeout
    while time.time() < end:
        if find(regex, serial):
            return True
        time.sleep(interval)
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="print", choices=["print", "find", "tap", "wait"])
    ap.add_argument("regex", nargs="?")
    ap.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"))
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=60)
    a = ap.parse_args()
    if a.action != "print" and not a.regex:
        ap.error(f"{a.action} needs a REGEX")
    if a.action == "print":
        nodes = dump(a.serial)
        if not nodes:
            sys.exit("empty hierarchy (screen animating, locked, or no device)")
        for n in nodes:
            if n["text"] or n["desc"] or n["id"]:
                print(f"{n['text']!r:40.40} | {n['desc']!r:30.30} | {n['id']:40.40} | {n['bounds']}")
    elif a.action == "find":
        for n in find(a.regex, a.serial):
            print(n["text"] or n["desc"], n["id"], n["bounds"], "centre", centre(n))
    elif a.action == "tap":
        sys.exit(0 if tap(a.regex, a.serial, a.index) else "no match")
    elif a.action == "wait":
        sys.exit(0 if wait_for(a.regex, a.serial, a.timeout) else "timed out")


if __name__ == "__main__":
    main()
