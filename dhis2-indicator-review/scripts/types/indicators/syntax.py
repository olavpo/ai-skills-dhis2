"""Server syntax check of every indicator numerator and denominator.

    python3 scripts/types/indicators/syntax.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS] [--tag TAG]
                                               [--threads 6]

Sends each text to GET /api/expressions/description?expression=<text> (read-only; the response is {"status": "OK" |
"ERROR", "message": "Valid" | "Expression is not well-formed", "description": "<names>"}). The description replaces
uids by names, which is a quick way to read a definition. A text the server cannot parse is not refused at import,
and analytics then reads it as 0 without an error (A20), so a rejection here is the only signal.

Input:  the export (+ overlays, e.g. a proposed payload to check before import).
Output: <work_dir>/syntax.json (syntax-<tag>.json): list of
        {"id", "name", "where": "numerator|denominator", "text", "status": "OK|ERROR", "message", "description", "ok"}
Config keys: metadata, work_dir. Connection: DHIS2_URL + DHIS2_TOKEN or DHIS2_USER/DHIS2_PASSWORD.
"""
import concurrent.futures as F, os, sys, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import d2  # noqa: E402


def validate_text(kind, text):
    """(ok, message, description) for one numerator or denominator (kind is not used: one endpoint for both)."""
    q = urllib.parse.urlencode({'expression': text or '1'})
    s, j = d2.req('GET', '/api/expressions/description?' + q, log=False, timeout=60)
    ok = s == 200 and j.get('status') == 'OK'
    return ok, j.get('message') or (j.get('_raw') or '')[:300], j.get('description')


def main():
    ap = IC.add_common(IC.parser(__doc__))
    ap.add_argument('--threads', type=int, default=6)
    args = ap.parse_args()
    cfg = IC.load(args)
    d2.set_log(cfg.out('api-log.jsonl'))
    jobs = [(i['id'], i.get('name'), side, i.get(side) or '') for i in IC.select(cfg, args.items) for side in ('numerator', 'denominator')]

    def call(j):
        oid, name, where, text = j
        try:
            ok, msg, desc = validate_text(where, text)
        except Exception as e:  # network trouble: record, don't abort the batch
            ok, msg, desc = False, f'request failed: {e}', None
        return {'id': oid, 'name': name, 'where': where, 'text': text, 'status': 'OK' if ok else 'ERROR',
                'message': msg, 'description': desc, 'ok': ok}

    with F.ThreadPoolExecutor(args.threads) as ex:
        out = list(ex.map(call, jobs))
    path = IC.tagged(cfg, 'syntax.json', args.tag)
    IC.write_json(path, out)
    bad = [o for o in out if not o['ok']]
    print(len(out), 'checked;', len(bad), 'invalid ->', path)
    for o in bad[:60]:
        print(f"{o['id']} {(o['name'] or '')[:55]} [{o['where']}] -> {o['message']}\n      {o['text'][:200]!r}")


if __name__ == '__main__':
    main()
