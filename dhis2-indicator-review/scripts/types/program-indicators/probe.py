"""Behaviour probe: create throwaway ZZZTEMP program indicators, query them, print the values, delete them.

    python3 scripts/types/program-indicators/probe.py --type EVENT|ENROLLMENT [--expr 'V{event_count}'] 'filter 1' ['filter 2' ...]
                                      [--agg COUNT] [--boundaries SPEC ...] [--pe 2026Q1] [--ou UID] [--keep]
                                      [--config pireview.json]

Use it to verify an engine behaviour before relying on it (references/program-indicators/behaviours.md), e.g. whether a blank
number compares as 0:
    probe.py --type ENROLLMENT "#{<stage>.<numDe>} < 3" "d2:hasValue(#{<stage>.<numDe>}) && #{<stage>.<numDe>} < 3"

  filters        one probe PI per filter ('' or 'true' for no filter), all with the same expression/boundaries
  --expr         expression (default V{event_count} for EVENT, V{enrollment_count} for ENROLLMENT)
  --agg          aggregationType (default COUNT)
  --boundaries   one or more probe variants, each a comma-separated list of TARGET:TYPE[:OFFSET[:PERIODTYPE]]
                 with TARGET in EVENT_DATE / ENROLLMENT_DATE / INCIDENT_DATE and TYPE AFTER_START / BEFORE_END
                 (short for AFTER_START_OF_REPORTING_PERIOD / BEFORE_END_OF_REPORTING_PERIOD). Every filter is probed
                 with every variant. Default: AFTER_START and BEFORE_END on the main date of the type.
                 e.g. --boundaries ENROLLMENT_DATE:BEFORE_END \\
                                   ENROLLMENT_DATE:BEFORE_END,EVENT_DATE:BEFORE_END \\
                                   ENROLLMENT_DATE:AFTER_START:-6:Monthly,ENROLLMENT_DATE:BEFORE_END:-6:Monthly \\
                                   ENROLLMENT_DATE:AFTER_START:-6,ENROLLMENT_DATE:BEFORE_END:-6
  --pe / --ou    query period and org unit (default cfg.test_period, cfg.root)
  --keep         do not delete the probe PIs (they are named ZZZTEMP ... so they are easy to find)

Data-dependent probes (the probe1 pattern). Some behaviours only show with a specific situation in the data, e.g.
"does an ENROLLMENT PI read a visit AFTER the period?" (C5) or "is an offset without a period type ignored?" (P1):
  1. write a tiny tracker payload with 2-4 people in a dedicated org unit, each showing one situation, e.g.
     A: enrolled before the period, a visit inside the period with status X, a later visit after the period with Y;
     B: the same without the later visit; C: enrolled exactly 6 months before the period;
  2. load it:  import_data.py --org-units <probeOU> --file probe-tracker.json   (creates the OU, runs analytics);
  3. query probe variants that differ in ONE detail:
     probe.py --type ENROLLMENT --ou <probeOU> "#{<stage>.<statusDe>} == 'X'" \\
              --boundaries ENROLLMENT_DATE:BEFORE_END ENROLLMENT_DATE:BEFORE_END,EVENT_DATE:BEFORE_END
     Expected if C5 holds: 1 (only B) without the event-date cap, 2 with it.
  4. record the result (version, values) in the behaviours reference; never put real people's data in a probe.

Writes to the instance: refuses unless the config declares "test_instance": true. Output: printed table and
<work_dir>/probes.jsonl (one line per probe: filter, boundaries, value or error).
Config keys: program, test_period, org_units.root, test_instance, work_dir.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import config, d2  # noqa: E401
import common

SHORT = {'AFTER_START': 'AFTER_START_OF_REPORTING_PERIOD', 'BEFORE_END': 'BEFORE_END_OF_REPORTING_PERIOD',
         'AFTER_END': 'AFTER_END_OF_REPORTING_PERIOD', 'BEFORE_START': 'BEFORE_START_OF_REPORTING_PERIOD'}


def parse_boundaries(spec):
    out = []
    for part in [x for x in spec.split(',') if x.strip()]:
        f = part.strip().split(':')
        if len(f) < 2:
            sys.exit(f'bad boundary {part!r}: TARGET:TYPE[:OFFSET[:PERIODTYPE]]')
        b = {'boundaryTarget': f[0], 'analyticsPeriodBoundaryType': SHORT.get(f[1], f[1])}
        if len(f) > 2 and f[2]:
            b['offsetPeriods'] = int(f[2])
        if len(f) > 3 and f[3]:
            b['offsetPeriodType'] = f[3]
        out.append(b)
    return out


def main():
    ap = common.parser(__doc__)
    ap.add_argument('--type', required=True, choices=['EVENT', 'ENROLLMENT'])
    ap.add_argument('--expr')
    ap.add_argument('--agg', default='COUNT')
    ap.add_argument('--boundaries', nargs='+')
    ap.add_argument('--pe')
    ap.add_argument('--ou')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('filters', nargs='+')
    args = ap.parse_args()
    cfg = config.load(args)
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    t = args.type
    expr = args.expr or ('V{event_count}' if t == 'EVENT' else 'V{enrollment_count}')
    main_t = 'EVENT_DATE' if t == 'EVENT' else 'ENROLLMENT_DATE'
    variants = [parse_boundaries(b) for b in args.boundaries] if args.boundaries else \
        [parse_boundaries(f'{main_t}:AFTER_START,{main_t}:BEFORE_END')]
    combos = [(f, b) for f in args.filters for b in variants]
    ids = d2.uids(len(combos))
    pis = []
    for u, (f, b) in zip(ids, combos):
        pis.append({'id': u, 'name': f'ZZZTEMP probe {u}', 'shortName': f'ZZZTEMP {u}', 'program': {'id': cfg.program_id},
                    'analyticsType': t, 'aggregationType': args.agg, 'expression': expr,
                    'filter': f if f.strip() not in ('', 'true') else None, 'analyticsPeriodBoundaries': b})
    s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', {'programIndicators': pis})
    if s >= 400 or j.get('status') not in ('OK', 'SUCCESS'):
        sys.exit(f'probe PI creation failed: HTTP {s} {json.dumps(j)[:500]}')
    pe, ou = args.pe or cfg.test_period, args.ou or cfg.root
    try:
        with open(cfg.out('probes.jsonl'), 'a') as log:
            for p, (f, b) in zip(pis, combos):
                st, r = d2.analytics([('dimension', 'dx:' + p['id']), ('filter', 'pe:' + pe), ('filter', 'ou:' + ou)])
                val = (float(r['rows'][0][-1]) if r.get('rows') else 0.0) if st == 200 else f"HTTP {st}: {(r.get('message') or '')[:160]}"
                bs = ','.join(f"{x['boundaryTarget']}:{x['analyticsPeriodBoundaryType'].split('_OF_')[0]}"
                              f"{':' + str(x['offsetPeriods']) if x.get('offsetPeriods') else ''}"
                              f"{':' + x['offsetPeriodType'] if x.get('offsetPeriodType') else ''}" for x in b)
                print(f"{str(val):>10}  {f.strip() or '(no filter)'}   [{bs}]")
                log.write(json.dumps({'t': time.strftime('%Y-%m-%dT%H:%M:%S'), 'type': t, 'expression': expr, 'filter': f,
                                      'boundaries': b, 'pe': pe, 'ou': ou, 'value': val, 'pi': None if not args.keep else p['id']}) + '\n')
    finally:
        if args.keep:
            print('kept:', ' '.join(p['id'] for p in pis))
        else:
            s, j = d2.req('POST', '/api/metadata?importStrategy=DELETE', {'programIndicators': [{'id': p['id']} for p in pis]})
            if s >= 400:
                print('warning: could not delete the probe PIs:', ' '.join(p['id'] for p in pis), file=sys.stderr)


if __name__ == '__main__':
    main()
