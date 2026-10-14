"""Server syntax check of every proposed expression, filter and mapping filter (read-only).

    python3 scripts/fix/syntax_check.py [--config pireview.json] [--all-options]

Input   work_dir/fixes.json (after build_payload.py, or straight after build_fixes.py)
Output  work_dir/syntax-check.json  {"pis": {"<code or uid>": {"expression"|"filter": {status, message, description}}},
                                     "mappings": {"<mapping> / <option>": {filter, status, message, description}}}
Exit code 1 when any text fails.
Uses POST /api/programIndicators/{expression,filter}/description (I2). This does not write to the instance, so it may
run against any instance with the same metadata; syntax OK does not prove the filter runs as SQL (I3): query analytics.
Mapping filters: the changed options, or every option of a changed mapping with --all-options (one malformed option
breaks every PI using the mapping, E6). Connection from the environment (lib/d2.py). Config keys: work_dir.
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, d2


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--all-options', action='store_true', help='check every option filter of each changed mapping')
    a = ap.parse_args()
    cfg = config.load(a)
    with open(cfg.out('fixes.json')) as f:
        F = json.load(f)
    out, bad, n = {'pis': {}, 'mappings': {}}, 0, 0
    for r in F['pis']:
        res = {}
        for kind in ('expression', 'filter'):
            txt = r['after'].get(kind)
            if not txt:
                continue
            ok, msg, desc = d2.validate_text(kind, txt)
            res[kind] = {'status': 'OK' if ok else 'ERROR', 'message': msg, 'description': (desc or '')[:300]}
            n += 1
            if not ok:
                bad += 1
                print('BAD', r.get('code') or r['id'], kind, msg)
        out['pis'][r.get('code') or r['id']] = res
    co = {c['id']: c.get('name') for c in cfg.md['categoryOptions']}
    for m in F.get('mappings', []):
        prev = {om['optionId']: om.get('filter') or '' for om in m['before'].get('optionMappings', [])}
        for om in m['after'].get('optionMappings', []):
            txt = om.get('filter') or ''
            if not (a.all_options or txt != prev.get(om['optionId'])):
                continue
            label = f"{m['name']} / {co.get(om['optionId'], om['optionId'])}"
            if not txt.strip():
                out['mappings'][label] = {'filter': txt, 'status': 'ERROR', 'message': 'empty filter', 'description': None}
                bad += 1; print('BAD', label, 'empty filter'); continue
            ok, msg, desc = d2.validate_text('filter', txt)
            out['mappings'][label] = {'filter': txt, 'status': 'OK' if ok else 'ERROR', 'message': msg, 'description': desc}
            n += 1
            if not ok:
                bad += 1
                print('BAD', label, msg)
    with open(cfg.out('syntax-check.json'), 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f'checked {n} texts ({len(out["pis"])} PIs, {len(out["mappings"])} mapping filters); failures: {bad}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
