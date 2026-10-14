"""Engine check: recompute every PI total and disaggregation cell from the generated records with the independent
evaluator (lib/piexpr.py) and diff against analytics. Offline.

    python3 scripts/validate/compare.py [--config pireview.json] [--metadata OVERLAY ...] [--pis IDS] [--tag TAG]
                                        [--truth FILE ...] [--analytics FILE] [--out FILE] [--ou UID ...] [--rule C4=0 ...]

  --metadata   overlays (see common.py): evaluate the definitions as they are on the instance at query time, e.g.
               `--metadata proposed-programIndicators.json proposed-categoryMappings.patch.json --tag after`
  --pis        only these PIs (JSON list file or comma list)
  --tag        read analytics-<tag>.json, write compare-<tag>.json
  --truth      one or more truth files, concatenated (default truth.json in work_dir); e.g. validation data +
               fix witnesses + scenario records: --truth truth.json fix-truth.json scen-truth.json
  --analytics  analytics file (default analytics[-tag].json); only PIs present in it are compared
  --ou         only use truth records whose "ou" is one of these (default: all; analytics queried at cfg.root
               includes every test org unit below it)
  --rule       switch a DHIS2 behaviour of the evaluator off/on (ids in piexpr.RULES), e.g. --rule C4=0 to see
               whether a mismatch comes from the blank-number rule

Output: <work_dir>/compare.json (compare-<tag>.json): list of records, one per PI:
  {"id", "name", "code", "type", "agg", "pe",
   "status": "compared|parse|unsupported|analytics-error",
   "expected_total"                  what analytics should show (EVENT: records outside the cells dropped, C1;
                                     ENROLLMENT: all records, C2),
   "expected_total_ignoring_disagg"  all records passing the filter,
   "expected_total_in_cells"         only records in exactly one option of every category,
   "expected_total_any_match", "analytics_total", "total_ok", "units",
   with categories: "cells_ok", "cell_diffs" {key: [expected, analytics]} (first 6), "n_cell_diffs",
   "units_in_no_cell", "units_in_several_cells", "sum_cells_analytics", "cells_sum_equals_total", or "cells_err"}
  Change from the original format: expected_total now already applies C1/C2 (previously it was the in-cells
  total for both types and ENROLLMENT readers had to use expected_total_ignoring_disagg; that field is unchanged).
Config keys: metadata, program, work_dir, dhis2_version.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, piexpr as P  # noqa: E401
import common


def main():
    ap = common.add_common(common.parser(__doc__))
    ap.add_argument('--truth', nargs='+', help='truth files (default truth.json in work_dir)')
    ap.add_argument('--analytics', help='analytics file (default analytics[-tag].json)')
    ap.add_argument('--out', help='output file (default compare[-tag].json)')
    ap.add_argument('--ou', nargs='+', help='only truth records in these org units')
    ap.add_argument('--rule', nargs='+', default=[], help='behaviour switches, e.g. C4=0')
    args = ap.parse_args()
    cfg = common.load(args)
    rules = {}
    for r in args.rule:
        k, _, v = r.partition('=')
        if k not in P.RULES:
            sys.exit(f'unknown rule {k}; known: {sorted(P.RULES)}')
        rules[k] = v not in ('0', 'false', 'off', 'no')
    eng = P.Engine(cfg.md, cfg.program, cfg.dhis2_version, rules)
    truth = []
    for t in args.truth or [cfg.out('truth.json')]:
        truth += common.read_json(t if os.path.exists(t) else cfg.resolve(t))
    if args.ou:
        truth = [t for t in truth if t.get('ou') in set(args.ou)]
    an = common.read_json(args.analytics or common.tagged(cfg, 'analytics.json', args.tag))
    report = []
    for p in common.select_pis(cfg, args.pis):
        a = an.get(p['id'])
        if a is None:
            continue
        report.append(compare_one(eng, p, a, truth))
    out = args.out or common.tagged(cfg, 'compare.json', args.tag)
    common.write_json(out, report)
    summarise(report)
    print('->', out)


def same(expected, got, p):
    """Analytics rounds to the PI's decimals (2 when unset); compare at that precision."""
    dec = p.get('decimals')
    tol = 0.5 * 10 ** -(dec if isinstance(dec, int) else 2) + 1e-9
    return abs((expected or 0) - (got or 0)) <= tol


def compare_one(eng, p, a, truth):
    rec = {'id': p['id'], 'name': p.get('name'), 'code': p.get('code'), 'type': p['analyticsType'],
           'agg': p.get('aggregationType'), 'pe': a.get('pe')}
    if a.get('total_err'):
        return {**rec, 'status': 'analytics-error', 'detail': a['total_err']}
    try:
        r = eng.evaluate(p, truth, a['pe'], a.get('cats'))
    except P.ParseError as e:
        return {**rec, 'status': 'parse', 'detail': str(e)}
    except P.Unsupported as e:
        return {**rec, 'status': 'unsupported', 'detail': str(e)}
    got = a.get('total') or 0.0
    rec.update({'status': 'compared', 'expected_total': r['expected_total'], 'analytics_total': got,
                'total_ok': same(r['expected_total'], got, p), 'units': r['units'],
                'expected_total_ignoring_disagg': r['expected_total_ignoring_disagg'],
                'expected_total_in_cells': r['expected_total_in_cells'], 'expected_total_any_match': r['expected_total_any_match']})
    if a.get('cats'):
        if a.get('cells_err'):
            rec['cells_err'] = a['cells_err']
        else:
            exp, got_c = r['cells'], a.get('cells') or {}
            diffs = {k: [exp.get(k), got_c.get(k)] for k in set(exp) | set(got_c)
                     if not same(exp.get(k), got_c.get(k), p)}
            s = sum(got_c.values())
            rec.update({'cells_ok': not diffs, 'cell_diffs': dict(sorted(diffs.items())[:6]), 'n_cell_diffs': len(diffs),
                        'units_in_no_cell': r['units_in_no_cell'], 'units_in_several_cells': r['units_in_several_cells'],
                        'sum_cells_analytics': s,
                        'cells_sum_equals_total': same(s, got, p) if (p.get('expression') or '').strip() != 'V{tei_count}' else None})
    return rec


def summarise(report):
    cmp_ = [r for r in report if r['status'] == 'compared']
    print(f"compared {len(cmp_)} of {len(report)}; total ok {sum(r['total_ok'] for r in cmp_)}, "
          f"total mismatch {sum(not r['total_ok'] for r in cmp_)}; "
          f"not compared: {sum(r['status'] == 'analytics-error' for r in report)} analytics errors, "
          f"{sum(r['status'] == 'unsupported' for r in report)} unsupported, {sum(r['status'] == 'parse' for r in report)} parse")
    print('zero in both (filter never matched, incl. the witness):', sum(1 for r in cmp_ if r['total_ok'] and r['analytics_total'] == 0))
    dis = [r for r in cmp_ if 'cells_ok' in r]
    print(f"disaggregated compared {len(dis)}: cells ok {sum(r['cells_ok'] for r in dis)}, mismatch {sum(not r['cells_ok'] for r in dis)}; "
          f"PIs with records in no cell {sum(1 for r in dis if r['units_in_no_cell'])}, in several cells "
          f"{sum(1 for r in dis if r['units_in_several_cells'])}, analytics sum(cells) != total "
          f"{sum(1 for r in dis if r['cells_sum_equals_total'] is False)}")
    for r in [r for r in cmp_ if not r['total_ok']][:15]:
        print(f"  mismatch {r['id']} {(r['code'] or '')[:14]:14} {r['type'][:3]} expected {r['expected_total']:g} analytics {r['analytics_total']:g}")


if __name__ == '__main__':
    main()
