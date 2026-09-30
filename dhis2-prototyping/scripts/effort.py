#!/usr/bin/env python3
"""Effort counts and keystroke-level time estimates from what the entry flows
actually ran.

    python3 scripts/effort.py --android runs/android/*/commands*.json --planned flows/android/counts.json \
                              --web runs/web_*.json --out report/effort.json

Inputs, one file per flow (the flow name is the file's stem, or its parent
directory's name for Maestro's commands*.json):

  --android  Maestro --debug-output command logs (commands*.json, under a hidden
             .maestro directory). Counted from COMPLETED commands only: taps
             (tapOn*, hideKeyboard, back), typed characters (inputText), scrolls
             (scroll, and scrollUntilVisible taking > 1.5 s - quicker ones found the
             target on screen), and --launch-taps per launchApp (default 2: a
             relaunch stands for the two back presses a person would make).
  --planned  optional {flow: {"taps", "chars", "scrolls", "screens"}} from the flow
             generator: screen changes come from here (Maestro cannot tell), and a
             flow whose run failed falls back to these counts, marked "planned".
  --web      JSON with "counts": {"taps", "chars", "scrolls", "screens"} (what the
             Playwright runner writes); files with a non-empty "error" are skipped.

The model (seconds per operation) is a rough keystroke-level model (Card, Moran
and Newell; mobile values after Holleis et al. 2007). It estimates interface
time only - no conversation, no reading documents. State it in the report, call
the result "interface time", and recommend a timed manual run and a field trial.
Automation run time is not effort: Maestro spends most of it on scroll checks.
"""
import argparse
import glob
import json
import os
import re

MODEL = {
    "android": {"tap": 1.2, "char": 0.35, "scroll": 1.0, "screen": 1.5},
    "web": {"tap": 1.3, "char": 0.25, "scroll": 1.0, "screen": 1.5},
}
MODEL_NOTE = ("Android: 1.2 s per tap (point + touch + short decision), 0.35 s per typed character "
              "(on-screen keyboard), 1.0 s per scroll, 1.5 s per screen change (load + orient). "
              "Web: 1.3 s per click, 0.25 s per typed character, 1.0 s per scroll, 1.5 s per screen "
              "change. Interface time only.")


def estimate(platform, c):
    m = MODEL[platform]
    return round(c["taps"] * m["tap"] + c["chars"] * m["char"] + c["scrolls"] * m["scroll"]
                 + c.get("screens", 0) * m["screen"])


def maestro_counts(cmds, launch_taps=2, scroll_ms=1500):
    taps = chars = scrolls = 0
    for c in cmds:
        md, cmd = c.get("metadata", {}), c.get("command", {})
        if md.get("status") != "COMPLETED" or not cmd:
            continue
        k = next(iter(cmd))
        if k.startswith("tapOn") or k in ("hideKeyboardCommand", "backPressCommand"):
            taps += 1
        elif k == "launchAppCommand":
            taps += launch_taps
        elif k == "inputTextCommand":
            chars += len(cmd[k].get("text", ""))
        elif k in ("scrollUntilVisible", "scrollUntilVisibleCommand"):
            scrolls += md.get("duration", 0) > scroll_ms
        elif k == "scrollCommand":
            scrolls += 1
    return {"taps": taps, "chars": chars, "scrolls": scrolls}


def flow_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.startswith("commands"):
        stem = os.path.basename(os.path.dirname(os.path.abspath(path))).lstrip(".")
    return re.sub(r"^(android|web)_", "", stem)


def expand(patterns):
    out = []
    for p in patterns or []:
        out += sorted(glob.glob(p, recursive=True, include_hidden=True)) or [p]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--android", nargs="*", default=[])
    ap.add_argument("--planned")
    ap.add_argument("--web", nargs="*", default=[])
    ap.add_argument("--launch-taps", type=int, default=2)
    ap.add_argument("--out")
    a = ap.parse_args()
    planned = json.load(open(a.planned)) if a.planned else {}
    out = {}
    for path in expand(a.android):
        flow = flow_name(path)
        cmds = json.load(open(path))
        c = maestro_counts(cmds, a.launch_taps)
        failed = any(x.get("metadata", {}).get("status") == "FAILED" for x in cmds)
        pl = planned.get(flow, {})
        if failed:
            if not pl:
                print(f"{flow}: run failed and no planned counts - skipped")
                continue
            c = dict(pl, planned=True, note="scripted run did not complete; counts from the generated flow")
        else:
            c["screens"] = pl.get("screens", 0)
        c["est_seconds"] = estimate("android", c)
        out.setdefault(flow, {})["android"] = c
    for path in expand(a.web):
        d = json.load(open(path))
        if d.get("error") or "counts" not in d:
            continue
        c = dict(d["counts"])
        c["est_seconds"] = estimate("web", c)
        if "seconds" in d:
            c["automation_seconds"] = d["seconds"]
        out.setdefault(d.get("flow") or flow_name(path), {})["web"] = c
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"model": MODEL, "note": MODEL_NOTE, "flows": out}, f, indent=1)
    print(f"{'flow':12} {'A taps':>6} {'chars':>5} {'scr':>4} {'est s':>6} | {'W clicks':>8} {'chars':>5} {'est s':>6}")
    for k in sorted(out):
        an, w = out[k].get("android", {}), out[k].get("web", {})
        mark = "*" if an.get("planned") else ""
        print(f"{k:12} {an.get('taps', ''):>6} {an.get('chars', ''):>5} {an.get('scrolls', ''):>4} "
              f"{str(an.get('est_seconds', '')) + mark:>6} | {w.get('taps', ''):>8} {w.get('chars', ''):>5} "
              f"{w.get('est_seconds', ''):>6}")
    if any(v.get("android", {}).get("planned") for v in out.values()):
        print("* planned counts (the scripted run failed)")


if __name__ == "__main__":
    main()
