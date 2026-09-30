#!/usr/bin/env python3
"""Import generated metadata files in order and stop at the first failure.

    D2_URL=... D2_AUTH=user:pass python3 scripts/import_package.py metadata/01_base.json metadata/02_*.json

Each file is posted to /api/metadata with CREATE_AND_UPDATE, atomicMode=ALL and
identifier=UID, and the error reports are printed on failure (exit code 1).

Two-pass retry for E4047: DHIS2 (seen on 2.42) checks HIDEFIELD/SETMANDATORYFIELD
rule actions against the stage data elements already stored, not the ones in the
payload, so a stage that is new in this update fails with "DataElement ... is not
linked to any ProgramStageDataElement". The script then imports the file without
programRules/programRuleActions and imports it again in full.

Metadata import never deletes: objects the generator stopped emitting stay on
the server and keep acting. Delete them through the API.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from d2http import as_json, load_json, metadata_errors, session  # noqa: E402

RULE_TYPES = ("programRules", "programRuleActions")


def post(s, url, payload, strategy, skip_sharing):
    r = s.post(f"{url}/api/metadata", params={
        "importStrategy": strategy, "atomicMode": "ALL", "identifier": "UID",
        "skipSharing": str(skip_sharing).lower()}, json=payload, timeout=900)
    rep = as_json(r)
    return r, rep, rep.get("response", rep)


def import_file(s, url, path, strategy="CREATE_AND_UPDATE", skip_sharing=False):
    payload = load_json(path)
    name = os.path.basename(path)
    r, rep, body = post(s, url, payload, strategy, skip_sharing)
    if body.get("status") != "OK" and "E4047" in json.dumps(rep) and any(k in payload for k in RULE_TYPES):
        print(f"{name}: E4047 (new stage fields) - importing without rules first, then in full")
        post(s, url, {k: v for k, v in payload.items() if k not in RULE_TYPES}, strategy, skip_sharing)
        r, rep, body = post(s, url, payload, strategy, skip_sharing)
    print(name, r.status_code, body.get("status"), body.get("stats"))
    errs = list(metadata_errors(rep))
    for typ, uid, code, msg in errs[:50]:
        print(f"   {typ} {uid} {code} {msg[:200]}")
    if len(errs) > 50:
        print(f"   ... {len(errs) - 50} more")
    return body.get("status") == "OK" and not errs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--url", help="overrides D2_URL")
    ap.add_argument("--strategy", default="CREATE_AND_UPDATE")
    ap.add_argument("--skip-sharing", action="store_true")
    a = ap.parse_args()
    s, url = session(a.url)
    for path in a.files:
        if not import_file(s, url, path, a.strategy, a.skip_sharing):
            sys.exit(1)


if __name__ == "__main__":
    main()
