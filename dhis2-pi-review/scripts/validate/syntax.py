"""Server syntax check of every PI expression, PI filter and category-mapping option filter.

    python3 scripts/validate/syntax.py [--config pireview.json] [--metadata OVERLAY ...] [--pis IDS] [--tag TAG]
                                       [--no-mappings] [--threads 6]

Sends each text to POST /api/programIndicators/{expression,filter}/description (text/plain, behaviour I2). Read-only
on the instance (the endpoint only validates). Note I3: the endpoint passes some filters that still fail as SQL, so
the runtime query (query.py) is still needed.

Input:  the export (+ overlays, e.g. a proposed payload to check before import).
Output: <work_dir>/syntax.json (syntax-<tag>.json): list of
        {"id", "name", "where": "expression|filter|option <name>", "text", "status": "OK|ERROR", "message", "description", "ok"}
Config keys: metadata, program, work_dir. Connection: DHIS2_URL + DHIS2_TOKEN or DHIS2_USER/DHIS2_PASSWORD.
"""
import concurrent.futures as F, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, d2  # noqa: E401
import common


def main():
    ap = common.add_common(common.parser(__doc__))
    ap.add_argument('--no-mappings', action='store_true', help='skip category-mapping filters')
    ap.add_argument('--threads', type=int, default=6)
    args = ap.parse_args()
    cfg = common.load(args)
    d2.set_log(cfg.out('api-log.jsonl'))
    jobs = []
    for p in common.select_pis(cfg, args.pis):
        jobs.append((p['id'], p.get('name'), 'expression', 'expression', p.get('expression') or ''))
        if (p.get('filter') or '').strip():
            jobs.append((p['id'], p.get('name'), 'filter', 'filter', p['filter']))
    if not args.no_mappings:
        copt = {o['id']: o['name'] for o in cfg.md['categoryOptions']}
        for m in cfg.program.get('categoryMappings') or []:
            for om in m.get('optionMappings', []):
                if (om.get('filter') or '').strip():
                    jobs.append((m['id'], 'mapping: ' + (m.get('mappingName') or m['id']),
                                 'option ' + copt.get(om['optionId'], om['optionId']), 'filter', om['filter']))

    def call(j):
        oid, name, where, kind, text = j
        try:
            ok, msg, desc = d2.validate_text(kind, text)
        except Exception as e:  # network trouble: record, don't abort the batch
            ok, msg, desc = False, f'request failed: {e}', None
        return {'id': oid, 'name': name, 'where': where, 'text': text, 'status': 'OK' if ok else 'ERROR',
                'message': msg, 'description': desc, 'ok': ok}

    with F.ThreadPoolExecutor(args.threads) as ex:
        out = list(ex.map(call, jobs))
    path = common.tagged(cfg, 'syntax.json', args.tag)
    common.write_json(path, out)
    bad = [o for o in out if not o['ok']]
    print(len(out), 'checked;', len(bad), 'invalid ->', path)
    for o in bad[:60]:
        print(f"{o['id']} {(o['name'] or '')[:55]} [{o['where']}] -> {o['message']}\n      {o['text'][:200]!r}")


if __name__ == '__main__':
    main()
