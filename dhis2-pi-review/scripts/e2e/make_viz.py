"""Saved pivot tables for the evidence screenshots (Data Visualizer), on the test instance.

1. "scenarios": every scenario indicator (the probe used "after" where a check has one) as rows, every scenario org
   unit as columns, the scenario period(s) as filter (as rows when scenarios use different periods);
2. one "breakdown" table per category named in any scenario's cellsAfter: the scenario PIs using that category as
   rows, its options as columns, all test data under cfg.root, the test period.

Names start with "ZZZPIREVIEW" so they are easy to find and delete. UIDs are kept in viz-ids.json and reused, so a
re-run updates the same visualizations (CREATE_AND_UPDATE). Run after `run_e2e.py after`; then shots.py.

Inputs : scenario-checks.json, metadata-after.json, probe-ids.json (optional), viz-ids.json (optional)
Outputs: viz-ids.json {"scenarios": uid, "breakdown:<category uid>": uid}
Config : metadata, program, work_dir, test_period, org_units.root, test_instance.
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, d2  # noqa: E402
import e2elib as E  # noqa: E402


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    cfg = config.load(ap.parse_args())
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    S = E.jload(cfg.out('scenario-checks.json'), [])
    if not S:
        sys.exit('no scenario-checks.json: run scen.py first')
    md = E.after_metadata(cfg)
    probes = E.jload(cfg.out('probe-ids.json'), {})
    V = E.jload(cfg.out('viz-ids.json'), {})
    pid = lambda s: probes.get(s['probe']['after'], s['probe']['after']) if s.get('probe') else s['pi']
    pis = list(dict.fromkeys(pid(s) for s in S))
    ous = list(dict.fromkeys(s['ou'] for s in S))
    pes = list(dict.fromkeys(s['pe'] for s in S))
    want = {'scenarios': None}
    cats = {}
    for s in S:
        for c in s.get('expectCellsAfter') or {}:
            cid = E.resolve_category(md, c)
            if cid:
                cats.setdefault(cid, []).append(s['pi'])
                want['breakdown:' + cid] = None
    missing = [k for k in want if k not in V]
    V.update(zip(missing, d2.uids(len(missing)) if missing else []))
    dxi = lambda ids: [{'dataDimensionItemType': 'PROGRAM_INDICATOR', 'programIndicator': {'id': i}} for i in ids]
    viz = [{'id': V['scenarios'], 'name': f'ZZZPIREVIEW e2e: scenario indicators after the fix ({", ".join(pes)})', 'type': 'PIVOT_TABLE',
            'columnDimensions': ['ou'], 'rowDimensions': ['dx'] + (['pe'] if len(pes) > 1 else []),
            'filterDimensions': [] if len(pes) > 1 else ['pe'], 'dataDimensionItems': dxi(pis),
            'organisationUnits': [{'id': o} for o in ous], 'periods': [{'id': p} for p in pes],
            'showDimensionLabels': True, 'hideEmptyRows': False}]
    CAT = {c['id']: c for c in md['categories']}
    for cid, ids in cats.items():
        viz.append({'id': V['breakdown:' + cid], 'name': f'ZZZPIREVIEW e2e: {CAT[cid]["name"]} breakdown, all test data ({cfg.test_period})'[:230],
                    'type': 'PIVOT_TABLE', 'columnDimensions': [cid], 'rowDimensions': ['dx'], 'filterDimensions': ['pe', 'ou'],
                    'dataDimensionItems': dxi(list(dict.fromkeys(ids))),
                    'categoryDimensions': [{'category': {'id': cid}, 'categoryOptions': [{'id': o['id']} for o in CAT[cid].get('categoryOptions', [])]}],
                    'organisationUnits': [{'id': cfg.root}], 'periods': [{'id': cfg.test_period}],
                    'showDimensionLabels': True, 'colTotals': True, 'rowTotals': True})
    s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', {'visualizations': viz})
    E.log_step(cfg, 'saved pivot tables', {'http': s, 'status': j.get('status'), 'ids': V})
    E.jdump(V, cfg.out('viz-ids.json'))
    print('visualizations', s, j.get('status'), V)


if __name__ == '__main__':
    main()
