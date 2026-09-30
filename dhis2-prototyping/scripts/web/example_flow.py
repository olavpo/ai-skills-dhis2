#!/usr/bin/env python3
"""Example web Capture flow built on runner.py: search, register, fill a few fields, save.

    python3 example_flow.py --program <uid> --orgunit <uid> [--record] [--out runs/]

Adapt the field labels to your program; labels match by prefix of the visible text.
Prefer different names/phones per platform for the same scenario case, or the second
platform's search finds the first platform's entry and the flow is no longer a first visit.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def flow(w, program, orgunit):
    w.cap("Search for the household by phone")
    w.goto_capture(f"/search?programId={program}&orgUnitId={orgunit}")
    w.click_text("Search by attributes")
    w.text("Phone number", "0760000001")
    w.button("Search by attributes")
    w.screen()
    w.pause(1.2)
    w.cap("Not found: register a new household")
    w.button("Create new")
    w.screen()
    w.cap("Household details")
    w.text("Name of household head", "Aminata Example")
    w.select("Sex of household head", "Female")
    w.radio("Nomadic settlement", "No")
    w.button("Save household")      # the save button's label depends on the TET name
    w.screen()
    w.answer_dialog("Yes, create new event")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--program", required=True)
    ap.add_argument("--orgunit", required=True)
    ap.add_argument("--name", default="web_example")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    from runner import run_flow
    r = run_flow(a.name, lambda w: flow(w, a.program, a.orgunit), a.out, record=a.record)
    sys.exit(1 if r["error"] else 0)


if __name__ == "__main__":
    main()
