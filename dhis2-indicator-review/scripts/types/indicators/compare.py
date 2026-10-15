"""Engine check: recompute every aggregate indicator at each org unit and period from the generated values with the
independent evaluator (indexpr.py) and diff against analytics. Offline.

    python3 scripts/types/indicators/compare.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS] [--tag TAG]
                                                [--truth FILE ...] [--analytics FILE] [--setup FILE] [--out FILE]
                                                [--rule A1=0 ...]

  --metadata   overlays: evaluate the definitions as they are on the instance at query time, e.g.
               `--metadata proposed-indicators.json proposed-dataElements.json --tag after`
  --items      only these indicators
  --tag        read analytics-<tag>.json, write compare-<tag>.json
  --truth      truth files, concatenated (default truth.json; e.g. truth.json truth-fix.json data/scen-truth.json)
  --setup      test setup (default testsetup.json: org unit parents, group members, zero handling)
  --rule       switch a behaviour of the evaluator off, e.g. --rule A1=0 (missing operands as 0 everywhere) to see
               whether a mismatch comes from that rule; ids in indexpr.RULES

Output: <work_dir>/compare.json (compare-<tag>.json): one record per indicator in the analytics file:
  {"id", "name", "code", "type": "<indicator type> ×<factor>", "agg": "annualized" | "", "pe",
   "status": "compared|parse|unsupported|analytics-error", "detail",
   "expected_total", "analytics_total", "total_ok"        (root, quarter; null = no value, "NaN" = NaN)
   "cells_ok", "cell_diffs": {"<ou>|<pe>": [expected, analytics]} (first 6), "n_cell_diffs", "n_cells",
   "matches": number of data values the indicator reads at the root in the quarter,
   "units_in_no_cell": 0, "units_in_several_cells": 0     (core fields; no breakdown categories for indicators)}
Values are compared exactly (relative 1e-9) when query.py asked for unrounded values (the default), else at the
indicator's decimals (2 when unset, A9).
Config keys: metadata, work_dir, test_period, org_units.root / facilities.
"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import indexpr as X  # noqa: E402


def main():
    ap = IC.add_common(IC.parser(__doc__))
    ap.add_argument('--truth', nargs='+')
    ap.add_argument('--analytics')
    ap.add_argument('--setup')
    ap.add_argument('--out')
    ap.add_argument('--rule', nargs='+', default=[])
    args = ap.parse_args()
    cfg = IC.load(args)
    rules = {}
    for r in args.rule:
        k, _, v = r.partition('=')
        if k not in X.RULES:
            sys.exit(f'unknown rule {k}; known: {sorted(X.RULES)}')
        rules[k] = v not in ('0', 'false', 'off', 'no')
    truth = []
    for t in args.truth or [cfg.out('truth.json')]:
        truth += IC.read_json(t if os.path.exists(t) else cfg.resolve(t))
    eng = engine(cfg, truth, IC.read_json(args.setup or cfg.out('testsetup.json'), {}), rules)
    an = IC.read_json(args.analytics or IC.tagged(cfg, 'analytics.json', args.tag))
    report = [compare_one(cfg, eng, i, an[i['id']], truth) for i in IC.select(cfg, args.items) if i['id'] in an]
    out = args.out or IC.tagged(cfg, 'compare.json', args.tag)
    IC.write_json(out, report)
    summarise(report)
    print('->', out)


def engine(cfg, truth, setup, rules=None):
    setup = dict(setup)
    parents = dict(setup.get('parents') or {})
    for t in truth:                                    # scenario / extra org units sit directly below the root
        if t['ou'] != cfg.root:
            parents.setdefault(t['ou'], cfg.root)
    setup['parents'] = parents
    return X.Engine(cfg.meta if hasattr(cfg, 'meta') else IC.Meta(cfg.md), truth, setup, rules=rules)


def same(e, a, ind, rounded=True):
    """Exact comparison (relative 1e-9) for unrounded analytics; at the indicator's decimals for rounded values (A9)."""
    if e is None or a is None or isinstance(e, str) or isinstance(a, str):
        return e == a
    if not rounded:
        return abs(e - a) <= 1e-9 * max(1.0, abs(e), abs(a))
    dec = ind.get('decimals')
    tol = 0.5 * 10 ** -(dec if isinstance(dec, int) else 2) + 1e-9
    return abs(e - a) <= tol


def badge(cfg, ind):
    M = cfg.meta if hasattr(cfg, 'meta') else IC.Meta(cfg.md)
    it = M.it.get((ind.get('indicatorType') or {}).get('id')) or {}
    f = it.get('factor')
    return f"{it.get('name', '?')} ×{f:g}" if isinstance(f, (int, float)) else it.get('name', '?')


def compare_one(cfg, eng, ind, a, truth):
    rec = {'id': ind['id'], 'name': ind.get('name'), 'code': ind.get('code'), 'type': badge(cfg, ind),
           'agg': 'annualized' if ind.get('annualized') else '', 'pe': a.get('pe')}
    if a.get('total_err'):
        return {**rec, 'status': 'analytics-error', 'detail': a['total_err']}
    exp = {}
    eng.round_rates = a.get('rounded', True)
    try:
        for key in sorted(set(a['cells']) | cell_keys(cfg, a)):
            ou, pe = key.split('|')
            v = eng.indicator(ind, ou, pe)['value']
            exp[key] = v
    except X.ParseError as e:
        return {**rec, 'status': 'parse', 'detail': str(e)}
    except X.Unsupported as e:
        return {**rec, 'status': 'unsupported', 'detail': str(e)}
    total_key = f"{a['ou']}|{a['pe']}"
    rd = a.get('rounded', True)
    diffs = {k: [exp.get(k), a['cells'].get(k)] for k in exp if not same(exp.get(k), a['cells'].get(k), ind, rd)}
    rec.update({'status': 'compared', 'expected_total': exp.get(total_key), 'analytics_total': a.get('total'),
                'total_ok': same(exp.get(total_key), a.get('total'), ind, rd), 'cells_ok': not diffs, 'exact': not rd,
                'cell_diffs': dict(sorted(diffs.items())[:6]), 'n_cell_diffs': len(diffs), 'n_cells': len(exp),
                'expected_cells': exp, 'matches': reads(eng, ind, a['ou'], a['pe']),
                'units_in_no_cell': 0, 'units_in_several_cells': 0})
    return rec


def cell_keys(cfg, a):
    pes = X.periods_covering('Monthly', a['pe']) + [a['pe']]
    ous = sorted({k.split('|')[0] for k in a['cells']} | {a['ou']} | set(cfg.facilities))
    return {f'{o}|{p}' for o in ous for p in pes}


def reads(eng, ind, ou, pe):
    """Number of data values the indicator's operands read at ou in pe (any aggregation)."""
    n = 0
    qs, qe, _ = X.period_range(pe)
    for side in ('numerator', 'denominator'):
        try:
            ops = [o for o in X.operands(X.parse(ind.get(side) or '1')) if o[0] == 'de']
        except (X.ParseError, X.Unsupported):
            continue
        for _, de, coc, aoc in ops:
            for r in eng.by_de.get(de, []):
                if r.get('value') in (None, ''):
                    continue
                if coc not in (None, '*') and r['coc'] != coc or aoc not in (None, '*') and r.get('aoc') != aoc:
                    continue
                s, e, _ = X.period_range(r['pe'])
                if eng.below(r['ou'], ou) and (s <= qe and e >= qs):
                    n += 1
    return n


def fmt(v):
    return '-' if v is None else v if isinstance(v, str) else f'{v:g}'


def summarise(report):
    cmp_ = [r for r in report if r['status'] == 'compared']
    print(f"compared {len(cmp_)} of {len(report)}: totals ok {sum(r['total_ok'] for r in cmp_)}, all cells ok "
          f"{sum(r['cells_ok'] for r in cmp_)} ({sum(r['n_cells'] for r in cmp_)} cells, "
          f"{sum(r['n_cell_diffs'] for r in cmp_)} differ); not compared: "
          f"{sum(r['status'] == 'analytics-error' for r in report)} analytics errors, "
          f"{sum(r['status'] == 'unsupported' for r in report)} unsupported, {sum(r['status'] == 'parse' for r in report)} parse")
    print('no value at the root in the quarter (both):', sum(1 for r in cmp_ if r['total_ok'] and r['analytics_total'] is None),
          '; NaN somewhere:', sum(1 for r in cmp_ if 'NaN' in [v for v in r['expected_cells'].values()]))
    for r in [r for r in cmp_ if not r['cells_ok']][:15]:
        k, (e, a) = next(iter(r['cell_diffs'].items()))
        print(f"  mismatch {r['id']} {(r['name'] or '')[:40]:40} {r['n_cell_diffs']} cells, e.g. {k}: expected {fmt(e)} analytics {fmt(a)}")


if __name__ == '__main__':
    main()
