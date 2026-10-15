"""Generate synthetic aggregate data for the engine check: a distinct value in every cell the indicators read.

    python3 scripts/types/indicators/gen.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS] [--tag TAG]
                                            [--seed N] [--blank-share 0.08] [--zero-share 0.05] [--zeros significant|asis]

Cells: for every data element an indicator in scope reads, every COC of its category combo(s) (as exported: the
COCs whose categoryCombo is the data element's combo or a data set override), every attribute option combo of its
data set whose category options are valid in the period, every test facility (cfg.facilities), and every period of
the data set's period type that lies in cfg.test_period or contains it (Monthly: the 3 months; Yearly: the year).
COCs of OTHER combos are never filled: data entry cannot produce them and 2.43.2 refuses them on import (A13), so a
#{de.coc} with such a COC reads nothing, as in production for new data. Neither are COCs whose category options do
not match their own combo's categories (left over from an earlier version of the combo): a 2.43 instance refuses to
create them in that combo (E1130) and refuses data for them through any other combo (E8024), so the test copy cannot
hold them; they are listed in testsetup.json "stale_cocs" and lint reports the indicators reading them.
Values are distinct positive integers per cell (larger for AVERAGE_SUM_ORG_UNIT data elements such as populations),
'true'/'false' for BOOLEAN / TRUE_ONLY, plus deliberate edge cases:
  * blanks (--blank-share of the cells) and zeros (--zero-share);
  * zero denominator: at the LAST facility, in the 2nd month, every monthly data element read by any denominator is 0;
  * missing denominator: at the last facility, in the 3rd month, those data elements are blank;
  * missing numerator: at the first facility, in the 3rd month, the monthly data elements read only by numerators are blank;
  * OUG{...} groups: the first half of the facilities are members (testsetup.json; import_data.py applies it).
Data elements in no data set get no values (2.43.2 refuses them, E8003); text data elements are skipped.
With --tag (e.g. the e2e witness step, --tag fix, on the proposed definitions) only cells NOT already in truth.json
are generated, so the earlier data and truth stay valid.

Output (work_dir, names with -<tag> when given):
  datavalues.json  {"dataValues": [...]}: POST /api/dataValueSets payload (import_data.py splits it by data set)
  truth.json       [{"de", "coc", "aoc", "ou", "pe", "value", "case"}]: what the evaluator reads; blank cells have
                   "value": null (case "blank", "blank-den", "blank-num"); "zero", "zero-den" mark deliberate zeros
  testsetup.json   {"zeros": "significant|asis", "oug_members": {group: [ou]}, "parents": {ou: parent},
                   "zero_significant": [de], "include_zeros": true, "facilities", "root", "test_period"}
                   (written without --tag only)
--zeros significant (default): import_data.py sets zeroIsSignificant on every data element that gets a 0 and turns on
keyIncludeZeroValuesInAnalytics, so the zeros reach analytics (A3); asis: the instance keeps its settings and the
evaluator drops zeros of data elements with zeroIsSignificant=false.
Config keys: metadata, work_dir, test_period, org_units.root / facilities, seed, indicators (scope).
"""
import json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import indexpr as X  # noqa: E402


def main():
    ap = IC.add_common(IC.parser(__doc__))
    ap.add_argument('--seed', type=int)
    ap.add_argument('--blank-share', type=float, default=0.08)
    ap.add_argument('--zero-share', type=float, default=0.05)
    ap.add_argument('--zeros', choices=['significant', 'asis'], default='significant')
    args = ap.parse_args()
    cfg = IC.load(args)
    M = IC.Meta(cfg.md)
    inds = IC.select(cfg, args.items)
    rng = random.Random((args.seed if args.seed is not None else cfg.seed) + (1000 if args.tag else 0))
    num_des, den_des, skipped = set(), set(), []
    for i in inds:
        for side, acc in (('numerator', num_des), ('denominator', den_des)):
            try:
                ast = X.parse(i.get(side) or '1')
            except (X.ParseError, X.Unsupported) as e:
                skipped.append([i['id'], f'{side}: {e}'])
                ast = None
            if ast is not None:
                acc.update(n[1] for n in X.operands(ast) if n[0] == 'de' and n[1] in M.de)
            # operands outside our parser (e.g. after an unsupported postfix) are found with the token regex
            acc.update(m[0] for m in IC.REF_RX.findall(i.get(side) or '') if m[0] in M.de)
    des = sorted(num_des | den_des)
    facs = list(cfg.facilities) or [cfg.root]
    months = X.periods_covering('Monthly', cfg.test_period)
    existing = set()
    if args.tag and os.path.exists(cfg.out('truth.json')):
        existing = {(t['de'], t['coc'], t['aoc'], t['ou'], t['pe']) for t in IC.read_json(cfg.out('truth.json'))}
    pool = list(range(1, 100000))
    rng.shuffle(pool)
    big = list(range(1000, 200000, 7))
    rng.shuffle(big)
    values, truth, no_ds, stale = [], [], [], []
    for de in des:
        obj = M.de[de]
        vt = obj.get('valueType') or 'NUMBER'
        if vt not in IC.NUMERIC:
            skipped.append([de, f'value type {vt}: not generated'])
            continue
        ds = M.de_dataset(de)
        if not ds:
            no_ds.append(de)
            continue
        pt = M.de_period_type(de)
        try:
            pes = X.periods_covering(pt, cfg.test_period)
        except X.Unsupported as e:
            skipped.append([de, str(e)])
            continue
        cocs = [c for c in M.de_cocs(de) if valid_options(M, c, pes)]
        stale += [[de, c] for c in cocs if not any(M.coc_fits_combo(c, cc) for cc in M.de_combos(de))]
        cocs = [c for c in cocs if any(M.coc_fits_combo(c, cc) for cc in M.de_combos(de))]
        aocs = [a for a in M.ds_aocs(ds) if valid_options(M, a, pes)]
        for ou in facs:
            for pe in pes:
                for aoc in aocs:
                    for coc in cocs:
                        if (de, coc, aoc, ou, pe) in existing:
                            continue
                        case = None
                        r = rng.random()
                        if vt in ('TRUE_ONLY', 'BOOLEAN'):
                            v = None if r < 0.3 else ('true' if vt == 'TRUE_ONLY' or r < 0.75 else 'false')
                        elif r < args.blank_share:
                            v, case = None, 'blank'
                        elif r < args.blank_share + args.zero_share:
                            v, case = '0', 'zero'
                        else:
                            v = str(big.pop() if obj.get('aggregationType', '').startswith('AVERAGE') else pool.pop())
                        if pt == 'Monthly' and len(facs) > 1:
                            if ou == facs[-1] and pe == months[1] and de in den_des:
                                v, case = '0', 'zero-den'
                            elif ou == facs[-1] and pe == months[2] and de in den_des:
                                v, case = None, 'blank-den'
                            elif ou == facs[0] and pe == months[2] and de in num_des and de not in den_des:
                                v, case = None, 'blank-num'
                        if v == '0' and vt == 'INTEGER_POSITIVE':
                            v, case = None, 'blank'           # 0 is not a valid INTEGER_POSITIVE (E8122)
                        if v is None:                     # blank: recorded so a --tag run leaves it blank
                            truth.append({'de': de, 'coc': coc, 'aoc': aoc, 'ou': ou, 'pe': pe, 'value': None, 'case': case or 'blank'})
                            continue
                        values.append({'dataElement': de, 'categoryOptionCombo': coc, 'attributeOptionCombo': aoc,
                                       'orgUnit': ou, 'period': pe, 'value': v})
                        truth.append({'de': de, 'coc': coc, 'aoc': aoc, 'ou': ou, 'pe': pe, 'value': v, 'case': case})
    ougs = sorted({n[1] for i in inds for side in ('numerator', 'denominator') for n in safe_operands(i.get(side)) if n[0] == 'oug'})
    members = {g: facs[:max(1, len(facs) // 2)] for g in ougs}
    zero_des = sorted({v['dataElement'] for v in values if v['value'] == '0'})
    IC.write_json(IC.tagged(cfg, 'datavalues.json', args.tag), {'dataValues': values}, indent=None)
    IC.write_json(IC.tagged(cfg, 'truth.json', args.tag), truth, indent=None)
    if not args.tag:
        setup = {'zeros': args.zeros, 'oug_members': members, 'parents': {f: cfg.root for f in facs},
                 'zero_significant': zero_des if args.zeros == 'significant' else [], 'include_zeros': args.zeros == 'significant',
                 'facilities': facs, 'root': cfg.root, 'test_period': cfg.test_period, 'months': months,
                 'skipped': skipped, 'no_data_set': no_ds, 'stale_cocs': stale}
        IC.write_json(cfg.out('testsetup.json'), setup)
    cases = {}
    for t in truth:
        if t['value'] is None and not t['case']:
            continue
        cases[t['case'] or 'value'] = cases.get(t['case'] or 'value', 0) + 1
    print(f'{len(inds)} indicators read {len(des)} data elements; {len(values)} values generated '
          f'({", ".join(f"{k} {v}" for k, v in sorted(cases.items()))}); {len(no_ds)} data elements in no data set '
          f'(no values: {", ".join(no_ds[:5])}); {len(stale)} (data element, COC) pairs with a COC that does not fit its combo '
          f'(no values: 2.43 cannot hold them, A13); {len(skipped)} skipped')
    print('->', IC.tagged(cfg, 'datavalues.json', args.tag), IC.tagged(cfg, 'truth.json', args.tag),
          '' if args.tag else cfg.out('testsetup.json'))


def safe_operands(text):
    try:
        return X.operands(X.parse(text or '1'))
    except (X.ParseError, X.Unsupported):
        return []


def valid_options(M, coc, pes):
    """Every category option of the COC/AOC is valid during the generated periods (start/end dates)."""
    if not pes:
        return False
    s = min(X.period_range(p)[0] for p in pes).isoformat()
    e = max(X.period_range(p)[1] for p in pes).isoformat()
    for o in M.coc.get(coc, {}).get('categoryOptions', []):
        co = M.co.get(o['id']) or {}
        if co.get('startDate') and co['startDate'][:10] > s:
            return False
        if co.get('endDate') and co['endDate'][:10] < e:
            return False
    return True


if __name__ == '__main__':
    main()
