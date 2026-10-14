"""Scenario builder for aggregate indicators: turns hand-written intent scenarios into data values, one org unit per
scenario.

A scenario is a handful of data values with the value an indicator should have BEFORE and AFTER the fix, worked out
by hand from what the indicator is meant to show (never from the evaluator). Write them per review into
<work_dir>/scenarios.json; format (see scenarios.example.json next to this script):

    {"scenarios": [{
        "id": "double-100",                     # unique key
        "group": "drop100",                     # fix group key(s) from fixes.json, '+'-joined
        "title": "...", "note": "why these values are expected",
        "pe": "2026Q1",                         # optional; query period (default cfg.test_period); also 202601, 2026...
        "values": [{"dataElement": "<uid, code or name>", "coc": "<uid or name>", "aoc": "<uid or name>",
                    "period": "202601", "value": "12"}],
        "expect": [{"item": "<indicator uid, code or name>", "before": 1200, "after": 12,
                    "cellsAfter": {"pe": {"202601": 12}}}]           # optional: months of pe, adapter.query(by='pe')
    }]}

coc may be omitted for a data element with the default category combo; aoc defaults to the default attribute option
combo. Option combos may be given by name ("Fixed, <1y"); a COC outside the data element's combo is refused (2.43
refuses such values, A13). Expected values are what analytics shows: rounded to the indicator's decimals (2 when
unset); use null for "no value" (e.g. a zero denominator, A2).
Each scenario's values go to its own org unit below cfg.root (prefix org_units.scenario_prefix), so expectations
are read off that org unit alone. Data elements whose data set has a longer period type (Yearly population) take a
period of that type ("2026").

Inputs : <work_dir>/scenarios.json (or --scenarios), the metadata export (+ the proposed payload for names of new items).
Outputs: <work_dir>/data/scen-values.json ({"dataValues": [...]}: import_data.py / adapter.e2e_load),
         data/scen-truth.json (truth.json format, case "scenario:<id>"), data/scen-ous.json (org units),
         scenario-checks.json (one row per expectation: key, scenario, group, item, pi, code, name, title, note, ou, pe,
         persons (= data values), probe, expectBefore, expectAfter, expectCellsAfter).
Config : metadata, work_dir, test_period, org_units.root, org_units.scenario_prefix.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import indexpr as X  # noqa: E402


class Refused(Exception):
    pass


def find(objs, ref, what):
    hits = [o for o in objs if ref in (o['id'], o.get('code'), o.get('name'), o.get('shortName'), o.get('formName'))]
    if len({h['id'] for h in hits}) != 1:
        raise Refused(f'{what} {ref!r}: {"not found" if not hits else "ambiguous"}')
    return hits[0]


def main():
    ap = IC.parser(__doc__.split('\n')[0])
    ap.add_argument('--scenarios', help='scenario file (default <work_dir>/scenarios.json)')
    args = ap.parse_args()
    cfg = IC.load(args)
    for f in ('proposed-indicators.json', 'proposed-dataElements.json'):
        if os.path.exists(cfg.out(f)):
            IC.apply_overlay(cfg, IC.read_json(cfg.out(f)), f)
    M = IC.Meta(cfg.md)
    path = args.scenarios or os.path.join(cfg.work_dir, 'scenarios.json')
    if not os.path.exists(path):
        sys.exit(f'no {path}: write the scenarios first (format: scripts/types/indicators/scenarios.example.json)')
    raw = IC.read_json(path)
    scenarios = raw['scenarios'] if isinstance(raw, dict) else raw
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,9}', cfg.scenario_prefix):
        sys.exit(f'org_units.scenario_prefix {cfg.scenario_prefix!r} must be 1-10 letters/digits starting with a letter')
    width = 11 - len(cfg.scenario_prefix)
    values, truth, ous, checks, errors, seen = [], [], [], [], [], set()
    inds = cfg.md['indicators']
    for n, sc in enumerate(scenarios, 1):
        sid = sc['id']
        if sid in seen:
            sys.exit(f'duplicate scenario id {sid}')
        seen.add(sid)
        ou = cfg.scenario_prefix + str(n).zfill(width)
        try:
            vals = [value(M, v, ou) for v in sc.get('values', [])]
            exps = [(find(inds, e.get('item') or e.get('pi'), 'indicator'), e) for e in sc.get('expect', [])]
        except Refused as e:
            errors.append(f'scenario {sid}: {e}')
            continue
        ous.append({'id': ou, 'name': f'{cfg.scenario_prefix} {n:02d} {sid}'[:230], 'shortName': f'{cfg.scenario_prefix} {n:02d} {sid}'[:50],
                    'openingDate': '2000-01-01', 'parent': {'id': cfg.root}})
        for v in vals:
            values.append(v)
            truth.append({'de': v['dataElement'], 'coc': v['categoryOptionCombo'], 'aoc': v['attributeOptionCombo'], 'ou': ou,
                          'pe': v['period'], 'value': v['value'], 'case': 'scenario:' + sid})
        many = len(exps) > 1
        for ind, e in exps:
            lab = ind.get('code') or ind['name']
            checks.append({'key': f'{sid}:{lab}' if many else sid, 'scenario': sid, 'group': sc.get('group', ''),
                           'item': ind['id'], 'pi': ind['id'], 'code': lab, 'name': ind.get('name'), 'title': sc.get('title', sid),
                           'note': e.get('note') or sc.get('note', ''), 'ou': ou, 'pe': e.get('pe') or sc.get('pe') or cfg.test_period,
                           'persons': len(vals), 'probe': e.get('probe'), 'expectBefore': e.get('before'), 'expectAfter': e.get('after'),
                           'expectCellsAfter': e.get('cellsAfter') or {}})
    for e in errors:
        print('REFUSED', e)
    if errors:
        sys.exit(f'{len(errors)} scenario(s) refused; nothing written')
    IC.write_json(cfg.out('data', 'scen-values.json'), {'dataValues': values})
    IC.write_json(cfg.out('data', 'scen-truth.json'), truth)
    IC.write_json(cfg.out('data', 'scen-ous.json'), {'organisationUnits': ous})
    IC.write_json(cfg.out('scenario-checks.json'), checks)
    print(f'{len(scenarios)} scenarios, {len(checks)} checks, {len(values)} data values, org units {ous[0]["id"] if ous else "-"}..')


def value(M, v, ou):
    de = find(list(M.de.values()), v['dataElement'], 'data element')
    if not M.de_ds.get(de['id']):
        raise Refused(f"{de['name']}: in no data set (2.43 refuses its values)")
    cocs = M.de_cocs(de['id'])
    if v.get('coc'):
        c = find([M.coc[x] for x in cocs if x in M.coc], v['coc'], f"option combo of {de['name']}")
    elif len(cocs) == 1:
        c = M.coc[cocs[0]]
    else:
        raise Refused(f"{de['name']}: give coc (one of {', '.join(M.coc[x]['name'] for x in cocs[:6])})")
    if not any(M.coc_fits_combo(c['id'], cc) for cc in M.de_combos(de['id'])):
        raise Refused(f"{de['name']} / {c['name']}: the option combo does not fit its combo's categories (a 2.43 test copy cannot hold it)")
    ds = M.de_dataset(de['id'])
    aocs = M.ds_aocs(ds)
    if v.get('aoc'):
        a = find([M.coc[x] for x in aocs if x in M.coc], v['aoc'], 'attribute option combo')['id']
    elif len(aocs) == 1:
        a = aocs[0]
    else:
        raise Refused(f"{de['name']}: its data set has an attribute combo, give aoc")
    pe = str(v['period'])
    try:
        _, _, pt = X.period_range(pe)
    except X.Unsupported as e:
        raise Refused(str(e))
    want = M.de_period_type(de['id'])
    if pt != want:
        raise Refused(f"{de['name']}: period {pe} is {pt}, the data set is {want}")
    return {'dataElement': de['id'], 'categoryOptionCombo': c['id'], 'attributeOptionCombo': a, 'orgUnit': ou,
            'period': pe, 'value': str(v['value'])}


if __name__ == '__main__':
    main()
