"""Probe program indicators for the end-to-end test: throwaway copies of a definition that differ in one detail.

Two uses:
1. a data-dependent DHIS2 behaviour (e.g. does an ENROLLMENT PI read events after the period end?): put the
   situation in a scenario (scen.py, 2-4 clients in its own org unit) and compare probes that differ in one detail
   (with and without an EVENT_DATE boundary);
2. a fix the real PI can't show yet (e.g. its breakdown still drops every record because of another open topic):
   a probe with the old and one with the new filter, without the breakdown; reference them from the scenario as
   "probe": {"before": "<key>", "after": "<key>"}.

Probe file (<work_dir>/probes.json):
    {"probes": [{"key": "active-no-cap", "analyticsType": "ENROLLMENT", "aggregationType": "COUNT",
                 "expression": "V{enrollment_count}", "filter": "...",
                 "boundaries": [["ENROLLMENT_DATE", "BEFORE_END_OF_REPORTING_PERIOD"],
                                ["EVENT_DATE", "BEFORE_END_OF_REPORTING_PERIOD", -6, "Monthly"]],
                 "copyOf": "<PI uid or code>"}]}           # optional: start from that PI (after the fix), override the rest
Default boundaries: EVENT_DATE (EVENT) / ENROLLMENT_DATE (ENROLLMENT) after start and before end of the period.

  probes.py create   -> POST /api/metadata (CREATE_AND_UPDATE), names "ZZZPROBE <key>", writes probe-ids.json {key: uid}
  probes.py query    -> value of every probe at --ou (default every scenario org unit) for --pe (default test_period)
  probes.py delete   -> DELETE every probe in probe-ids.json
Writes go through lib/d2.py (logged) and require "test_instance": true.
Config : metadata, program, work_dir, test_period, org_units, test_instance.
"""
import copy, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, d2  # noqa: E402
import e2elib as E  # noqa: E402

KEEP = ('analyticsType', 'aggregationType', 'expression', 'filter', 'analyticsPeriodBoundaries', 'categoryCombo',
        'attributeCombo', 'categoryMappingIds', 'decimals')


def boundaries(spec, kind):
    if spec is None:
        t = 'EVENT_DATE' if kind == 'EVENT' else 'ENROLLMENT_DATE'
        spec = [[t, 'AFTER_START_OF_REPORTING_PERIOD'], [t, 'BEFORE_END_OF_REPORTING_PERIOD']]
    out = []
    for b in spec:
        if isinstance(b, dict):
            out.append(b)
            continue
        d = {'boundaryTarget': b[0], 'analyticsPeriodBoundaryType': b[1]}
        if len(b) > 2 and b[2]:
            d['offsetPeriods'] = b[2]
        if len(b) > 3 and b[3]:
            d['offsetPeriodType'] = b[3]
        out.append(d)
    return out


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('action', choices=('create', 'query', 'delete'))
    ap.add_argument('--probes', help='probe file (default <work_dir>/probes.json)')
    ap.add_argument('--ou', nargs='*', help='org units to query (default: every scenario org unit)')
    ap.add_argument('--pe', help='period to query (default test_period)')
    args = ap.parse_args()
    cfg = config.load(args)
    d2.set_log(cfg.out('api-log.jsonl'))
    ids_path = cfg.out('probe-ids.json')
    ids = E.jload(ids_path, {})
    if args.action == 'create':
        cfg.require_test_instance()
        spec = E.jload(args.probes or os.path.join(cfg.work_dir, 'probes.json'))
        md = E.after_metadata(cfg, write=False)
        new = [p['key'] for p in spec['probes'] if p['key'] not in ids]
        ids.update(zip(new, d2.uids(len(new)) if new else []))
        pis = []
        for p in spec['probes']:
            base = {}
            if p.get('copyOf'):
                src = E.resolve_pi(md, p['copyOf'])
                if not src:
                    sys.exit(f'copyOf: unknown PI {p["copyOf"]}')
                base = {k: copy.deepcopy(src[k]) for k in KEEP if k in src}
            kind = p.get('analyticsType', base.get('analyticsType', 'EVENT'))
            obj = {**base, 'id': ids[p['key']], 'name': f'ZZZPROBE {p["key"]}'[:230], 'shortName': f'ZZZPROBE {p["key"]}'[:50],
                   'program': {'id': cfg.program_id}, 'analyticsType': kind,
                   'aggregationType': p.get('aggregationType', base.get('aggregationType', 'COUNT'))}
            for k in ('expression', 'filter'):
                if k in p:
                    obj[k] = p[k]
            obj.setdefault('expression', 'V{event_count}' if kind == 'EVENT' else 'V{enrollment_count}')
            if 'boundaries' in p or 'analyticsPeriodBoundaries' not in obj:
                obj['analyticsPeriodBoundaries'] = boundaries(p.get('boundaries'), kind)
            if not p.get('keepBreakdown'):
                for k in ('categoryCombo', 'attributeCombo', 'categoryMappingIds'):
                    obj.pop(k, None)
            pis.append(obj)
        s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE&atomicMode=ALL', {'programIndicators': pis})
        E.log_step(cfg, 'create probe PIs', {'http': s, 'status': j.get('status'), 'keys': list(ids)})
        E.jdump(ids, ids_path)
        print('probes', s, j.get('status'), ids)
    elif args.action == 'query':
        ous = args.ou or [o['id'] for o in E.jload(cfg.out('data', 'scen-ous.json'), {'organisationUnits': []})['organisationUnits']] or [cfg.root]
        for k, u in ids.items():
            vals = []
            for ou in ous:
                st, j = d2.analytics([('dimension', 'dx:' + u), ('filter', 'pe:' + (args.pe or cfg.test_period)), ('filter', 'ou:' + ou)])
                vals.append((j['rows'][0][-1] if j.get('rows') else '0') if st == 200 else f'HTTP {st} {(j.get("message") or "")[:80]}')
            print(f'{k:30}', ' '.join(map(str, vals)))
    else:
        cfg.require_test_instance()
        for k, u in list(ids.items()):
            s, _ = d2.req('DELETE', f'/api/programIndicators/{u}')
            print('delete', k, s)
            if s in (200, 204, 404):
                ids.pop(k)
        E.jdump(ids, ids_path)


if __name__ == '__main__':
    main()
