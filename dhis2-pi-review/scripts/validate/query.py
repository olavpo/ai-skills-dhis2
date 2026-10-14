"""Runtime check: query every PI through /api/analytics, as a total and broken down by its disaggregation categories.

    python3 scripts/validate/query.py [--config pireview.json] [--metadata OVERLAY ...] [--pis IDS] [--tag TAG]
                                      [--pe 2026Q1] [--ou UID] [--out FILE] [--threads 4]

  --metadata   overlays (see common.py). The definitions on the INSTANCE are what analytics uses; overlays only change
               which PIs are queried, their categories and the query period (e.g. after importing a proposed payload,
               pass it so the cohort period and categories follow the new definitions).
  --pis        only these PIs (JSON list file or comma list); e.g. the affected PIs of an e2e run
  --tag        write analytics-<tag>.json (e.g. --tag before / --tag after)
  --pe         test period (default cfg.test_period); cohort PIs with Monthly/Quarterly... offsets are queried in the
               quarter whose offset window lands on it (piexpr.Engine.query_pe; P1: untyped offsets are ignored)
  --ou         org unit (default cfg.root)

Read-only on the instance. Run analytics first (validate/import_data.py does).
Output: <work_dir>/analytics.json (or --out):
  {"<piUid>": {"total": 12.0, "total_err": null, "pe": "2026Q1", "ou": "<uid>", "cells": {"<optA>|<optB>": 3.0},
               "cells_err": null, "cats": ["<categoryUid>", ...]}}
  cells keys join the option uids in the order of "cats"; cells is null when the PI has no disaggregation.
Config keys: metadata, program, work_dir, test_period, org_units.root, dhis2_version.
"""
import collections as C, concurrent.futures as F, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, d2, piexpr as P  # noqa: E401
import common


def main():
    ap = common.add_common(common.parser(__doc__))
    ap.add_argument('--pe', help='test period (default cfg.test_period)')
    ap.add_argument('--ou', help='org unit (default cfg.root)')
    ap.add_argument('--out', help='output file (default analytics[-tag].json in work_dir)')
    ap.add_argument('--threads', type=int, default=4)
    args = ap.parse_args()
    cfg = common.load(args)
    d2.set_log(cfg.out('api-log.jsonl'))
    eng = P.Engine(cfg.md, cfg.program, cfg.dhis2_version)
    test_pe, ou = args.pe or cfg.test_period, args.ou or cfg.root
    pis = common.select_pis(cfg, args.pis)

    def get(params):
        try:
            s, j = d2.analytics(params)
        except Exception as e:          # network trouble: record it for this PI, keep going
            return None, f'request failed: {e}'
        if s != 200:
            return None, f"HTTP {s}: {(j.get('message') or j.get('_raw') or '')[:300]}"
        return j, None

    def one(p):
        pe = eng.query_pe(p, test_pe)
        rec = {'total': None, 'total_err': None, 'pe': pe, 'ou': ou, 'cells': None, 'cells_err': None, 'cats': eng.categories(p)}
        j, err = get([('dimension', f"dx:{p['id']}"), ('filter', f'pe:{pe}'), ('filter', f'ou:{ou}')])
        if err:
            rec['total_err'] = err
        else:
            rows = j.get('rows') or []
            rec['total'] = float(rows[0][-1]) if rows else 0.0
        cats = rec['cats']
        if cats:
            params = [('dimension', f"dx:{p['id']}")] + [('dimension', c) for c in cats] + [('filter', f'pe:{pe}'), ('filter', f'ou:{ou}')]
            j, err = get(params)
            if err:
                rec['cells_err'] = err
            else:
                hdr = [h['name'] for h in j.get('headers', [])]
                if all(c in hdr for c in cats + ['value']):
                    rec['cells'] = {'|'.join(r[hdr.index(c)] for c in cats): float(r[hdr.index('value')]) for r in j.get('rows') or []}
                else:
                    rec['cells_err'] = f'unexpected response headers {hdr}'
        return p['id'], rec

    with F.ThreadPoolExecutor(args.threads) as ex:
        res = dict(ex.map(one, pis))
    out = args.out or common.tagged(cfg, 'analytics.json', args.tag)
    common.write_json(out, res)
    te = [r for r in res.values() if r['total_err']]
    ce = [r for r in res.values() if r['cells_err']]
    print(f'{len(res)} PIs queried; total errors: {len(te)}; disaggregation errors: {len(ce)} -> {out}')
    for k, n in C.Counter(r['total_err'][:160] for r in te).most_common(8):
        print(f'  {n:4} {k}')
    for k, n in C.Counter(r['cells_err'][:160] for r in ce).most_common(8):
        print(f'  {n:4} (cells) {k}')


if __name__ == '__main__':
    main()
