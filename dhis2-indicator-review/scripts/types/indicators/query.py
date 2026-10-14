"""Runtime check: query every aggregate indicator through /api/analytics at the test root and at each facility, for
each month of the test quarter and for the quarter.

    python3 scripts/types/indicators/query.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS] [--tag TAG]
                                              [--pe 2026Q1] [--ou UID ...] [--out FILE] [--threads 4] [--rounded]

  --metadata   overlays (see common.py): only change which indicators are queried; analytics uses the definitions
               on the INSTANCE
  --items      only these indicators (JSON list file or comma list)
  --tag        write analytics-<tag>.json
  --pe         test quarter (default cfg.test_period); the months inside it are queried too
  --ou         org units (default cfg.root + cfg.facilities); the first one is the "total" org unit
  --rounded    ask for values rounded to the indicator's decimals, as users see them. Default: skipRounding=true, so
               the engine check compares exact values (rounded values hide small differences, e.g. a COUNT instead of
               a SUM over large denominators rounds to 0.00 either way)

One request per indicator (dx:<ind>, ou: all org units, pe: months + quarter). Read-only. Run analytics first.
Output: <work_dir>/analytics.json (or --out):
  {"<indUid>": {"total": 12.5 | null | "NaN", "total_err": null, "pe": "2026Q1", "ou": "<root>",
                "cells": {"<ou>|<pe>": 3.0 | "NaN"}, "cells_err": null, "status": 200, "rounded": false}}
  total = the root in the quarter; null = no value (analytics returned no row); "NaN" = the row's value was NaN
  (a division by zero inside the numerator or denominator, A4). Cells without a row are absent.
Config keys: metadata, work_dir, test_period, org_units.root / facilities.
"""
import collections as C, concurrent.futures as F, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import indexpr as X  # noqa: E402
import d2  # noqa: E402


def main():
    ap = IC.add_common(IC.parser(__doc__))
    ap.add_argument('--pe')
    ap.add_argument('--ou', nargs='+')
    ap.add_argument('--out')
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--rounded', action='store_true', help='query rounded values (as users see them) instead of skipRounding=true')
    args = ap.parse_args()
    cfg = IC.load(args)
    d2.set_log(cfg.out('api-log.jsonl'))
    pe = args.pe or cfg.test_period
    pes = X.periods_covering('Monthly', pe) + [pe]
    ous = args.ou or [cfg.root] + list(cfg.facilities)
    inds = IC.select(cfg, args.items)

    def one(i):
        rec = {'total': None, 'total_err': None, 'pe': pe, 'ou': ous[0], 'cells': {}, 'cells_err': None, 'status': None,
               'rounded': args.rounded}
        res = query_cells(i['id'], ous, pes, not args.rounded)
        rec['status'] = res['status']
        if res['error']:
            rec['total_err'] = rec['cells_err'] = res['error']
        else:
            rec['cells'] = res['cells']
            rec['total'] = res['cells'].get(f'{ous[0]}|{pe}')
        return i['id'], rec

    with F.ThreadPoolExecutor(args.threads) as ex:
        res = dict(ex.map(one, inds))
    out = args.out or IC.tagged(cfg, 'analytics.json', args.tag)
    IC.write_json(out, res)
    errs = [r for r in res.values() if r['total_err']]
    print(f"{len(res)} indicators queried ({len(ous)} org units x {len(pes)} periods); errors: {len(errs)}; "
          f"no value at the root in {pe}: {sum(1 for r in res.values() if not r['total_err'] and r['total'] is None)}; "
          f"NaN cells: {sum(1 for r in res.values() for v in r['cells'].values() if v == 'NaN')} -> {out}")
    for k, n in C.Counter(r['total_err'][:160] for r in errs).most_common(8):
        print(f'  {n:4} {k}')


def value(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(x):
        return 'NaN'
    if math.isinf(x):
        return 'Infinity' if x > 0 else '-Infinity'
    return x


def query_cells(uid, ous, pes, skip_rounding=False, filters=()):
    """{'cells': {'ou|pe': value}, 'error': str|None, 'status': http} for one indicator."""
    params = [('dimension', 'dx:' + uid), ('dimension', 'ou:' + ';'.join(ous)), ('dimension', 'pe:' + ';'.join(pes))]
    params += list(filters)
    if skip_rounding:
        params.append(('skipRounding', 'true'))
    try:
        s, j = d2.analytics(params)
    except Exception as e:  # network trouble: record it, keep going
        return {'cells': {}, 'error': f'request failed: {e}', 'status': None}
    if s != 200:
        return {'cells': {}, 'error': f"HTTP {s} {j.get('errorCode', '')}: {(j.get('message') or j.get('_raw') or '')[:300]}", 'status': s}
    h = [x['name'] for x in j.get('headers', [])]
    if not all(k in h for k in ('ou', 'pe', 'value')):
        return {'cells': {}, 'error': f'unexpected response headers {h}', 'status': s}
    return {'cells': {f"{r[h.index('ou')]}|{r[h.index('pe')]}": value(r[h.index('value')]) for r in j.get('rows') or []},
            'error': None, 'status': s}


if __name__ == '__main__':
    main()
