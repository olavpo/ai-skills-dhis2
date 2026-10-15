"""Behaviour probe for aggregate indicators: create throwaway ZZZTEMP indicators, query them per org unit and period,
print the values, delete them.

    python3 scripts/types/indicators/probe.py 'NUMERATOR | DENOMINATOR' ['NUM | DEN' ...] [--factor 100]
            [--annualized] [--decimals N] [--pe 2026Q1 202601 ...] [--ou UID ...] [--skip-rounding] [--keep]
            [--config pireview.json]

Use it to verify an engine behaviour before relying on it (references/indicators/behaviours.md), e.g. whether a
numerator with no data gives no value or 0:
    probe.py '#{<deA>} | #{<deB>}' '#{<deA>} + 0 | #{<deB>}' --ou <ou with B only> --pe 202601

  pairs          one probe indicator per 'numerator | denominator' (denominator 1 when omitted)
  --factor       indicator type factor (an indicator type with that factor must exist; default 1)
  --annualized   annualized probes;  --decimals N  stored decimals (default: unset)
  --pe / --ou    periods and org units queried (default cfg.test_period and cfg.root + cfg.facilities); one request per
                 probe with every ou and pe as dimensions, so a division by zero (E7132) shows on the whole request
  --skip-rounding  query with skipRounding=true (shows the unrounded value)
  --keep         keep the probe indicators (named ZZZTEMP ...)

Data-dependent probes: write a small data value set in a dedicated org unit with one situation per org unit or
month (A: numerator only, B: denominator only, C: zero denominator, ...), load it with
  import_data.py --org-units <probeOU...> --file probe-values.json      (creates the OUs, runs analytics)
and query probe variants that differ in ONE detail. Never put real data in a probe.

Writes to the instance: refuses unless the config declares "test_instance": true. Output: printed table and
<work_dir>/probes.jsonl (one line per probe: numerator, denominator, settings, values per ou/pe or error).
Config keys: test_period, org_units.root / facilities, test_instance, work_dir.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import config, d2  # noqa: E401,E402


def main():
    ap = IC.parser(__doc__)
    ap.add_argument('pairs', nargs='+')
    ap.add_argument('--factor', type=float, default=1)
    ap.add_argument('--annualized', action='store_true')
    ap.add_argument('--decimals', type=int)
    ap.add_argument('--pe', nargs='+')
    ap.add_argument('--ou', nargs='+')
    ap.add_argument('--skip-rounding', action='store_true')
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()
    cfg = config.load(args)
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    types = d2.get('/api/indicatorTypes?fields=id,factor&paging=false')['indicatorTypes']
    it = next((t['id'] for t in types if float(t['factor']) == args.factor), None)
    if not it:
        sys.exit(f'no indicator type with factor {args.factor:g} on the instance')
    pes = args.pe or [cfg.test_period]
    ous = args.ou or [cfg.root] + list(cfg.facilities)
    ids = d2.uids(len(args.pairs))
    inds = []
    for u, pair in zip(ids, args.pairs):
        num, _, den = pair.partition('|')
        o = {'id': u, 'name': f'ZZZTEMP probe {u}', 'shortName': f'ZZZTEMP {u}', 'indicatorType': {'id': it},
             'numerator': num.strip(), 'denominator': den.strip() or '1', 'annualized': args.annualized}
        if args.decimals is not None:
            o['decimals'] = args.decimals
        inds.append(o)
    s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', {'indicators': inds})
    if s >= 400 or j.get('status') not in ('OK', 'SUCCESS'):
        sys.exit(f'probe indicator creation failed: HTTP {s} {json.dumps(j)[:500]}')
    try:
        with open(cfg.out('probes.jsonl'), 'a') as log:
            for o in inds:
                params = [('dimension', 'dx:' + o['id']), ('dimension', 'ou:' + ';'.join(ous)), ('dimension', 'pe:' + ';'.join(pes))]
                if args.skip_rounding:
                    params.append(('skipRounding', 'true'))
                st, r = d2.analytics(params)
                if st != 200:
                    vals = f"HTTP {st} {r.get('errorCode', '')}: {(r.get('message') or '')[:140]}"
                else:
                    h = [x['name'] for x in r['headers']]
                    vals = {f"{row[h.index('ou')]}/{row[h.index('pe')]}": float(row[h.index('value')]) for row in r.get('rows') or []}
                print(f"{o['numerator']}  |  {o['denominator']}   (factor {args.factor:g}{', annualized' if args.annualized else ''}"
                      f"{', decimals ' + str(args.decimals) if args.decimals is not None else ''})")
                if isinstance(vals, str):
                    print('    ', vals)
                else:
                    for ou in ous:
                        print('    ', ou, '  '.join(f"{pe}={vals[f'{ou}/{pe}']:g}" if f'{ou}/{pe}' in vals else f'{pe}=-' for pe in pes))
                log.write(json.dumps({'t': time.strftime('%Y-%m-%dT%H:%M:%S'), 'numerator': o['numerator'], 'denominator': o['denominator'],
                                      'factor': args.factor, 'annualized': args.annualized, 'decimals': args.decimals,
                                      'skipRounding': args.skip_rounding, 'ous': ous, 'pes': pes, 'values': vals}) + '\n')
    finally:
        if args.keep:
            print('kept:', ' '.join(o['id'] for o in inds))
        else:
            s, j = d2.req('POST', '/api/metadata?importStrategy=DELETE', {'indicators': [{'id': o['id']} for o in inds]})
            if s >= 400:
                print('warning: could not delete the probe indicators:', ' '.join(o['id'] for o in inds), file=sys.stderr)


if __name__ == '__main__':
    main()
