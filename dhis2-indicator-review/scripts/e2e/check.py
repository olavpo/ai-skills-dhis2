"""Engine check before/after + scenario pass/fail -> results.json, then the evidence skeleton E2E-TEST.md.

Engine check: the type's compare.py compares analytics-<tag>.json with the independent evaluator on ALL test records
(phase-1 truth.json + witness truth-fix.json + scenario data + any --truth files, merged into truth-e2e.json), for
the original metadata (before) and the proposed overlays (after), restricted to affected-ids.json. An item's total
is equal when compare's expected_total (which already applies the type's breakdown rules) equals analytics.
Scenario check: scenario-before/after.json against the expected values in scenario-checks.json (written by hand);
a check passes when before, after and every expected cell match ("error" matches any failed query).

Inputs : truth*.json, analytics-before/after.json, scenario-before/after.json, scenario-checks.json, affected-ids.json
Outputs: truth-e2e.json, compare-before/after.json (by compare.py), results.json, E2E-TEST.md (via evidence.py)
Options: --truth FILE (extra truth files, e.g. probe data), --skip-compare (reuse existing compare-<tag>.json)
Config : metadata, work_dir (+ type keys).
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, config  # noqa: E402
import e2elib as E  # noqa: E402
import evidence  # noqa: E402


def merge_truth(cfg, extra):
    t, src = [], {}
    for f in ['truth.json', 'truth-fix.json', os.path.join('data', 'scen-truth.json')] + list(extra or []):
        p = f if os.path.isabs(f) else os.path.join(cfg.work_dir, f)
        if os.path.exists(p):
            part = E.jload(p)
            src[f] = len(part)
            t += part
    E.jdump(t, cfg.out('truth-e2e.json'), indent=None)
    return t, src


def engine(cfg, tag, ccfg, skip):
    if not skip:
        E.run_type(cfg, 'compare.py', *E.overlay_args(cfg, tag), '--items', cfg.out('affected-ids.json'), '--tag', tag,
                   '--truth', cfg.out('truth-e2e.json'))
    R = E.jload(os.path.join(cfg.work_dir, f'compare-{tag}.json'), [])
    an = E.jload(os.path.join(cfg.work_dir, f'analytics-{tag}.json'), {})
    P = {p['id']: p for p in adapters.load(cfg).items(ccfg)}
    lab = lambda k: E.item_label(P[k]) if k in P else k
    per = {}
    for r in R if isinstance(R, list) else [{'id': k, **v} for k, v in R.items()]:
        if r.get('expected_total') is None:
            continue
        exp, got = r['expected_total'], r.get('analytics_total')
        per[r['id']] = {'expected': exp, 'analytics': got, 'totalOk': got is not None and abs(exp - got) < 1e-6, 'cellsOk': r.get('cells_ok')}
    errs = {k: v['total_err'] for k, v in an.items() if v.get('total_err')}
    return {'queried': len(an), 'errors': len(errs), 'errorByItem': {lab(k): e[:160] for k, e in errs.items()},
            'compared': len(per), 'totalMismatch': [lab(k) for k, v in per.items() if not v['totalOk']],
            'cellSets': sum(1 for v in per.values() if v['cellsOk'] is not None),
            'cellMismatch': [lab(k) for k, v in per.items() if v['cellsOk'] is False], 'perItem': per}


def scenarios(cfg):
    S = E.jload(os.path.join(cfg.work_dir, 'scenario-checks.json'), [])
    B = E.jload(os.path.join(cfg.work_dir, 'scenario-before.json'), {})
    A = E.jload(os.path.join(cfg.work_dir, 'scenario-after.json'), {})
    out = []
    for s in S:
        b, a = B.get(s['key'], {}), A.get(s['key'], {})
        val = lambda r: None if not r else ('error' if r.get('status') != 200 else r.get('total'))
        bv, av = val(b), val(a)
        cells = a.get('cells') or {}
        cells_ok = all(E.same_value((cells.get(c) or {}).get(o, 0.0), v) for c, cs in s['expectCellsAfter'].items() for o, v in cs.items())
        out.append({**{k: s.get(k) for k in ('key', 'scenario', 'group', 'code', 'title', 'note', 'ou', 'pe', 'persons', 'probe')},
                    'item': E.scenario_item(s),
                    'expectBefore': s['expectBefore'], 'gotBefore': bv, 'beforeError': b.get('error'),
                    'expectAfter': s['expectAfter'], 'gotAfter': av, 'afterError': a.get('error'),
                    'expectCells': s['expectCellsAfter'], 'gotCells': cells,
                    'pass': bv is not None and av is not None and E.same_value(bv, s['expectBefore']) and E.same_value(av, s['expectAfter']) and cells_ok})
    return out


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--truth', nargs='*', default=[], help='extra truth files (relative to the work dir or absolute)')
    ap.add_argument('--skip-compare', action='store_true', help='reuse compare-before/after.json')
    args = ap.parse_args()
    cfg = config.load(args)
    E.after_metadata(cfg)
    E.affected(cfg)
    truth, src = merge_truth(cfg, args.truth)
    res = {'truthPersons': len(truth), 'truthSources': src, 'engine': {}, 'scenarios': scenarios(cfg)}
    for tag, ccfg in (('before', cfg), ('after', E.after_cfg(cfg))):
        res['engine'][tag] = engine(cfg, tag, ccfg, args.skip_compare)
    E.jdump(res, cfg.out('results.json'))
    for tag in ('before', 'after'):
        e = res['engine'][tag]
        print(f"{tag:6} queried {e['queried']}, errors {e['errors']} | totals {e['compared'] - len(e['totalMismatch'])}/{e['compared']} equal"
              f" | cell sets {e['cellSets'] - len(e['cellMismatch'])}/{e['cellSets']} equal")
    for s in res['scenarios']:
        print(f"{'PASS' if s['pass'] else 'FAIL'} {s['key'][:28]:28} {s['code'][:14]:14} before {s['gotBefore']!s:6} (exp {s['expectBefore']!s:6})"
              f" after {s['gotAfter']!s:6} (exp {s['expectAfter']!s})")
    print(sum(s['pass'] for s in res['scenarios']), '/', len(res['scenarios']), 'scenario checks pass')
    print('evidence skeleton:', evidence.write(cfg))


if __name__ == '__main__':
    main()
