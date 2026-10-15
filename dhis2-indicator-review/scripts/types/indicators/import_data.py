"""Load generated aggregate test data into the TEST instance and build analytics (aggregate indicators).

    python3 scripts/types/indicators/import_data.py [--config pireview.json] [--file datavalues.json ...]
            [--org-units UID ...] [--org-units-file data/scen-ous.json] [--closed strip|force] [--no-user-ou]
            [--no-analytics] [--analytics-only] [--last-years N]

Steps (each logged to <work_dir>/api-log.jsonl; summary in <work_dir>/import.json):
  1. refuse unless cfg.require_test_instance() passes ("test_instance": true in the config);
  2. create the test org units if missing: cfg.root, cfg.facilities and --org-units below it; --org-units-file
     creates the org units of a metadata payload ({"organisationUnits": [...]}, e.g. scen.py's scen-ous.json);
  3. give the connected user capture and data-view access to cfg.root if it has none (skip with --no-user-ou);
  4. set the system setting dataOutputPeriodTypes when empty (a fresh 2.43 instance returns no aggregate analytics
     rows at all without it, A12);
  4b. COC fidelity: every category option combo the payload uses must exist on the instance with the export's uid.
     A fresh instance generates its own COC uids (categoryOptionComboUpdate), and 2.43 refuses to import COCs whose
     options do not match their combo's categories (E1130, e.g. COCs left from an earlier version of a combo).
     Missing COCs are imported per combo, replacing COCs the instance generated with the same options (only when
     they hold no data: test instance). COCs that do not fit their combo cannot be created (E1130) and are reported;
     gen.py does not generate values for them (A13);
  5. assign the data sets of every data element in the payload to the test org units; closed periods: data sets
     with dataInputPeriods or expiryDays get them removed on the TEST copy (--closed strip, default) or the import
     uses force=true (--closed force, superuser only). Recorded in import.json;
  6. org unit group membership for OUG{...} operands, as gen.py decided (testsetup.json "oug_members");
  7. zeros (testsetup.json "zeros": "significant"): set zeroIsSignificant=true on the data elements that get a 0 in
     the payload and the system setting keyIncludeZeroValuesInAnalytics=true, so reported zeros reach analytics
     (A3/A4). The changed data elements are recorded in import.json "zeroIsSignificant";
  8. remove commentOptionSet from the payload's data elements on the test copy (2.43.2 fails every value import of
     such a data element with HTTP 500, A11); recorded in import.json "commentOptionSets";
  8b. --clean: delete the values already stored for the payload's data sets, org units and periods first;
  9. POST each --file (default datavalues.json in work_dir) to /api/dataValueSets?importStrategy=CREATE_AND_UPDATE,
     one request per data set (A10), and check imported + updated == number of values in the payload (dropped zeros appear in neither ignored nor
     conflicts, A3); a shortfall is printed and recorded as "short";
  10. register every (data set, org unit, period, attribute option combo) with values as complete (reporting-rate
      operands need the completeness table; without any registration analytics answers E7144 for them);
  11. run the analytics tables job unless --no-analytics.

Output: <work_dir>/import.json. Config keys: metadata, work_dir, test_period, org_units.root / facilities,
test_instance. Connection: DHIS2_URL + DHIS2_TOKEN or DHIS2_USER/DHIS2_PASSWORD.
"""
import collections as C, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import config, d2  # noqa: E401,E402


def main():
    ap = IC.parser(__doc__)
    ap.add_argument('--file', nargs='+', help='data value set payloads (default datavalues.json in work_dir)')
    ap.add_argument('--org-units', nargs='*', default=[], help='extra test org units to create below cfg.root')
    ap.add_argument('--org-units-file', help='metadata payload with organisationUnits to create (e.g. data/scen-ous.json)')
    ap.add_argument('--setup', help='test setup file (default testsetup.json in work_dir, written by gen.py)')
    ap.add_argument('--closed', choices=['strip', 'force'], default='strip', help='closed periods: strip dataInputPeriods/expiryDays on the test copy (default) or import with force=true')
    ap.add_argument('--clean', action='store_true', help='first delete every data value of the payload\'s data sets in the payload\'s org units and periods (a re-generated data set must not mix with an earlier one)')
    ap.add_argument('--no-user-ou', action='store_true')
    ap.add_argument('--no-analytics', action='store_true')
    ap.add_argument('--analytics-only', action='store_true')
    ap.add_argument('--last-years', type=int)
    args = ap.parse_args()
    cfg = IC.load(args)
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    M = IC.Meta(cfg.md)
    summary = IC.read_json(cfg.out('import.json'), {}) if args.analytics_only else {}
    if not args.analytics_only:
        files = [f if os.path.exists(f) else cfg.resolve(f) for f in (args.file or [cfg.out('datavalues.json')])]
        payloads = [(f, IC.read_json(f)) for f in files]
        values = [v for _, p in payloads for v in p.get('dataValues', [])]
        setup = IC.read_json(args.setup or cfg.out('testsetup.json'), {})
        extra_ous = []
        if args.org_units_file:
            extra_ous = IC.read_json(args.org_units_file if os.path.exists(args.org_units_file) else cfg.resolve(args.org_units_file)).get('organisationUnits', [])
        summary['orgUnits'] = ensure_org_units(cfg, args.org_units, extra_ous)
        ous = [cfg.root] + list(cfg.facilities) + list(args.org_units) + [o['id'] for o in extra_ous]
        if not args.no_user_ou:
            summary['userOrgUnits'] = ensure_user_access(cfg)
        summary['dataOutputPeriodTypes'] = ensure_output_period_types()
        summary['cocs'] = align_cocs(M, values)
        summary['dataSets'] = assign_data_sets(M, values, [o for o in ous if o != cfg.root], args.closed, cfg.root)
        summary['orgUnitGroups'] = ensure_oug_members(setup.get('oug_members') or {})
        summary['zeroIsSignificant'] = ensure_zeros(M, values, setup.get('zeros', 'significant'))
        summary['commentOptionSets'] = drop_comment_option_sets(values)
        if args.clean:
            summary['cleaned'] = clean_values(M, values)
        summary['imports'] = [import_values(M, f, p, force=args.closed == 'force') for f, p in payloads]
        summary['completeness'] = register_complete(M, values)
    if not args.no_analytics:
        y = int(cfg.test_period[:4])
        last = args.last_years or max(3, dt.date.today().year - y + 3)
        secs, msg, lvl = d2.run_analytics(last)
        summary['analytics'] = {'seconds': secs, 'message': msg, 'level': lvl, 'lastYears': last}
        print(f'analytics: {msg} ({secs}s, lastYears={last})')
    IC.write_json(cfg.out('import.json'), summary)
    short = [i for i in summary.get('imports', []) if i.get('short')]
    print('->', cfg.out('import.json'))
    if short:
        sys.exit(f'{len(short)} payload(s) imported short: see import.json (a dropped value invalidates the comparison)')


def ensure_org_units(cfg, extra, extra_objs):
    want = [cfg.root] + list(cfg.facilities) + list(extra)
    ids = want + [o['id'] for o in extra_objs]
    have = {o['id'] for o in d2.get(f"/api/organisationUnits?filter=id:in:[{','.join(ids)}]&fields=id&paging=false")['organisationUnits']}
    new = []
    for uid in want:
        if uid in have:
            continue
        o = {'id': uid, 'name': f'Test org unit {uid}', 'shortName': f'Test {uid}', 'openingDate': '2000-01-01'}
        if uid == cfg.root:
            o.update(name=f'Test root {uid}', shortName=f'Test root {uid}')
        else:
            o['parent'] = {'id': cfg.root}
        new.append(o)
    new += [o for o in extra_objs if o['id'] not in have]
    if new:
        s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', {'organisationUnits': new})
        print('created org units', [o['id'] for o in new], s, j.get('status'))
        if s >= 400:
            sys.exit(f'org unit creation failed: {json.dumps(j)[:500]}')
    return {'created': [o['id'] for o in new], 'existing': sorted(have)}


def ensure_user_access(cfg):
    me = d2.get('/api/me?fields=id,organisationUnits[id],dataViewOrganisationUnits[id]')
    path = d2.get(f'/api/organisationUnits/{cfg.root}?fields=path')['path']
    anc = set(path.strip('/').split('/'))
    ops = [{'op': 'add', 'path': f'/{k}/-', 'value': {'id': cfg.root}}
           for k in ('organisationUnits', 'dataViewOrganisationUnits') if not anc & {o['id'] for o in me.get(k, [])}]
    if not ops:
        return {'changed': []}
    s, j = d2.req('PATCH', f"/api/users/{me['id']}", ops, ctype='application/json-patch+json')
    print('user org units', [o['path'] for o in ops], s)
    return {'changed': [o['path'] for o in ops], 'http': s}


PERIOD_TYPES = ['Daily', 'Weekly', 'Monthly', 'BiMonthly', 'Quarterly', 'SixMonthly', 'Yearly', 'FinancialApril',
                'FinancialJuly', 'FinancialOct']


def ensure_output_period_types():
    """A fresh 2.43 instance has no dataOutputPeriodTypes, and aggregate analytics then returns no rows at all (A12)."""
    s, cur = d2.req('GET', '/api/configuration/dataOutputPeriodTypes', log=False)
    if s == 200 and isinstance(cur, list) and cur:
        return {'existing': len(cur)}
    s, j = d2.req('POST', '/api/configuration/dataOutputPeriodTypes', [{'name': n} for n in PERIOD_TYPES])
    if s >= 400:
        s, j = d2.req('POST', '/api/configuration/dataOutputPeriodTypes', PERIOD_TYPES)
    print('dataOutputPeriodTypes set:', s)
    return {'set': PERIOD_TYPES, 'http': s}


# ------------------------------------------------------------------------------ COC fidelity
def align_cocs(M, values):
    used = sorted({v['categoryOptionCombo'] for v in values} | {v.get('attributeOptionCombo') for v in values if v.get('attributeOptionCombo')})
    have = {}
    for i in range(0, len(used), 100):
        part = used[i:i + 100]
        for o in d2.get(f"/api/categoryOptionCombos?filter=id:in:[{','.join(part)}]&fields=id&paging=false")['categoryOptionCombos']:
            have[o['id']] = True
    missing = [c for c in used if c not in have]
    out = {'used': len(used), 'missing': len(missing), 'imported': [], 'replaced': [], 'errors': []}
    if not missing:
        return out
    by_cc = C.defaultdict(list)
    for c in missing:
        if c not in M.coc:
            out['errors'].append(f'{c}: not in the export')
            continue
        by_cc[(M.coc[c].get('categoryCombo') or {}).get('id')].append(c)
    for cc, cocs in by_cc.items():
        fit = [c for c in M.cc_cocs.get(cc, []) if M.coc_fits_combo(c, cc)]
        stale = [c for c in cocs if not M.coc_fits_combo(c, cc)]
        if any(c in fit for c in cocs):
            # replace the instance's generated COCs of this combo by the export's (same options, other uids)
            inst = d2.get(f'/api/categoryOptionCombos?filter=categoryCombo.id:eq:{cc}&fields=id,categoryOptions[id]&paging=false')['categoryOptionCombos']
            mine = {M.coc_options(c): c for c in fit}
            for o in inst:
                opts = frozenset(x['id'] for x in o['categoryOptions'])
                if o['id'] not in mine.values() and opts in mine:
                    s, j = d2.req('DELETE', f"/api/categoryOptionCombos/{o['id']}")
                    out['replaced'].append([o['id'], mine[opts], s])
            objs = [strip(M.coc[c]) for c in fit]
            s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE&atomicMode=NONE', {'categoryOptionCombos': objs})
            out['imported'] += [c for c in fit]
            if s >= 400:
                out['errors'].append(f'combo {cc}: HTTP {s} {json.dumps(j)[:300]}')
        if stale:
            out['errors'].append(f'combo {cc}: {len(stale)} COC(s) do not fit the combo and cannot be created on 2.43 (E1130); gen.py does not fill them')
    print('COC fidelity:', {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()})
    for e in out['errors'][:5]:
        print('  ', e)
    return out


def strip(o):
    return {k: v for k, v in o.items() if k in ('id', 'name', 'code', 'categoryCombo', 'categoryOptions', 'ignoreApproval')}


# ------------------------------------------------------------------------------ data sets, groups, zeros
def assign_data_sets(M, values, ous, closed, root=None):
    """Each data set goes to the org units that get values for it (not to the root: an assigned org unit counts as an
    expected report in reporting rates, A21)."""
    des = {v['dataElement'] for v in values}
    dss = sorted({M.de_dataset(de) for de in des} - {None})
    out = {'assigned': [], 'closedPeriods': [], 'mode': closed}
    by_ds = {}
    for v in values:
        by_ds.setdefault(M.de_dataset(v['dataElement']), set()).add(v['orgUnit'])
    for ds in dss:
        ous_ds = [o for o in ous if o in by_ds.get(ds, ())]
        cur = d2.get(f'/api/dataSets/{ds}?fields=organisationUnits[id],dataInputPeriods,expiryDays')
        ops = []
        have = {o['id'] for o in cur.get('organisationUnits', [])}
        add = [o for o in dict.fromkeys(ous_ds) if o not in have]
        if add or root in have:
            keep = [o for o in cur.get('organisationUnits', []) if o['id'] != root]
            ops.append({'op': 'replace', 'path': '/organisationUnits', 'value': keep + [{'id': o} for o in add]})
        closed_info = {'dataSet': ds, 'name': M.ds.get(ds, {}).get('name'), 'dataInputPeriods': len(cur.get('dataInputPeriods') or []),
                       'expiryDays': cur.get('expiryDays') or 0}
        if closed_info['dataInputPeriods'] or closed_info['expiryDays']:
            out['closedPeriods'].append(closed_info)
            if closed == 'strip':
                ops += [{'op': 'replace', 'path': '/dataInputPeriods', 'value': []}, {'op': 'replace', 'path': '/expiryDays', 'value': 0}]
        if ops:
            s, j = d2.req('PATCH', f'/api/dataSets/{ds}', ops, ctype='application/json-patch+json')
            if s >= 400:
                sys.exit(f'data set {ds} update failed: HTTP {s} {json.dumps(j)[:400]}')
            out['assigned'].append(ds)
    if out['closedPeriods']:
        print(f"closed periods on {len(out['closedPeriods'])} data set(s): {'stripped on the test copy' if closed == 'strip' else 'import with force=true'}")
    return out


def ensure_oug_members(members):
    out = {}
    for g, ous in members.items():
        cur = d2.get(f'/api/organisationUnitGroups/{g}?fields=organisationUnits[id]')
        have = {o['id'] for o in cur.get('organisationUnits', [])}
        if set(ous) - have:
            s, j = d2.req('PATCH', f'/api/organisationUnitGroups/{g}', [{'op': 'replace', 'path': '/organisationUnits',
                          'value': cur.get('organisationUnits', []) + [{'id': o} for o in ous if o not in have]}], ctype='application/json-patch+json')
            out[g] = {'added': sorted(set(ous) - have), 'http': s}
        else:
            out[g] = {'added': []}
    return out


def ensure_zeros(M, values, mode):
    if mode != 'significant':
        return {'mode': mode, 'changed': []}
    zero_des = sorted({v['dataElement'] for v in values if str(v.get('value')).strip() in ('0', '0.0')})
    changed = []
    for de in zero_des:
        cur = d2.get(f'/api/dataElements/{de}?fields=zeroIsSignificant')
        if not cur.get('zeroIsSignificant'):
            s, j = d2.req('PATCH', f'/api/dataElements/{de}', [{'op': 'replace', 'path': '/zeroIsSignificant', 'value': True}],
                          ctype='application/json-patch+json')
            changed.append(de)
            if s >= 400:
                print('warning: zeroIsSignificant not set on', de, s)
    s, _ = d2.req('POST', '/api/systemSettings/keyIncludeZeroValuesInAnalytics?value=true', None)
    print(f'zeros: zeroIsSignificant set on {len(changed)} data element(s) (test copy); keyIncludeZeroValuesInAnalytics=true ({s})')
    return {'mode': mode, 'changed': changed, 'keyIncludeZeroValuesInAnalytics': s}


def clean_values(M, values):
    """Delete the data values already on the instance for the payload's data sets, org units and periods."""
    import indexpr as X
    pes = sorted({v['period'] for v in values})
    start = min(X.period_range(p)[0] for p in pes).isoformat()
    end = max(X.period_range(p)[1] for p in pes).isoformat()
    ous = sorted({v['orgUnit'] for v in values})
    dss = sorted({M.de_dataset(v['dataElement']) for v in values} - {None})
    gone = 0
    for ds in dss:
        q = f"/api/dataValueSets.json?dataSet={ds}&startDate={start}&endDate={end}&" + '&'.join(f'orgUnit={o}' for o in ous)
        cur = d2.get(q).get('dataValues', [])
        if cur:
            s, j = d2.req('POST', '/api/dataValueSets?importStrategy=DELETE&async=false', {'dataSet': ds, 'dataValues': cur})
            gone += len(cur)
    print(f'clean: {gone} existing data values deleted ({len(dss)} data sets, {len(ous)} org units, {start}..{end})')
    return {'deleted': gone, 'start': start, 'end': end}


def register_complete(M, values):
    """Mark every (data set, org unit, period, attribute option combo) that got values as complete, so reporting-rate
    operands (R{ds.REPORTING_RATE}) have a completeness table: without any registration analytics answers E7144
    "a referenced table does not exist" for them."""
    regs = sorted({(M.de_dataset(v['dataElement']), v['orgUnit'], v['period'], v.get('attributeOptionCombo') or M.default_coc)
                   for v in values} - {(None,)}, key=str)
    regs = [r for r in regs if r[0]]
    body = {'completeDataSetRegistrations': [{'dataSet': d, 'organisationUnit': o, 'period': p, 'attributeOptionCombo': a, 'completed': True}
                                             for d, o, p, a in regs]}
    s, j = d2.req('POST', '/api/completeDataSetRegistrations?async=false', body)
    r = j.get('response', j) if isinstance(j.get('response'), dict) else j
    print(f'completeness: {len(regs)} registrations, HTTP {s} {r.get("importCount") or r.get("status")}')
    return {'registrations': len(regs), 'http': s, 'importCount': r.get('importCount')}


def drop_comment_option_sets(values):
    """2.43.2 answers HTTP 500 ("DataEntryGroup.values() because valid is null") for every value of a data element
    with a commentOptionSet (A11). Remove it on the test copy; recorded in import.json."""
    des = sorted({v['dataElement'] for v in values})
    changed = []
    for i in range(0, len(des), 100):
        part = des[i:i + 100]
        for o in d2.get(f"/api/dataElements?filter=id:in:[{','.join(part)}]&fields=id,commentOptionSet&paging=false")['dataElements']:
            if o.get('commentOptionSet'):
                s, _ = d2.req('PATCH', f"/api/dataElements/{o['id']}", [{'op': 'remove', 'path': '/commentOptionSet'}],
                              ctype='application/json-patch+json')
                changed.append([o['id'], o['commentOptionSet']['id'], s])
    if changed:
        print(f'commentOptionSet removed from {len(changed)} data element(s) on the test copy (A11)')
    return changed


def import_values(M, path, payload, force=False):
    """POST the values grouped by data set (2.43 refuses a payload spanning several data sets: "Data set detection
    failed, found multiple sets", A10). Values of data elements in no data set go in a payload without dataSet."""
    vals = payload.get('dataValues', [])
    n = len(vals)
    groups = {}
    for v in vals:
        groups.setdefault(M.de_dataset(v['dataElement']), []).append(v)
    q = '/api/dataValueSets?importStrategy=CREATE_AND_UPDATE&async=false' + ('&force=true' if force else '')
    tot, conflicts, http, parts = {'imported': 0, 'updated': 0, 'ignored': 0, 'deleted': 0}, [], set(), []
    for ds, vs in groups.items():
        body = {'dataValues': vs}
        if ds:
            body['dataSet'] = ds
        s, j = d2.req('POST', q, body, timeout=1800)
        r = j.get('response', j) if isinstance(j.get('response'), dict) else j
        ic = r.get('importCount') or {}
        for k in tot:
            tot[k] += ic.get(k, 0)
        http.add(s)
        conflicts += (r.get('conflicts') or [])[:10]
        parts.append({'dataSet': ds, 'values': len(vs), 'http': s, 'importCount': ic})
    got = tot['imported'] + tot['updated']
    rec = {'file': path, 'values': n, 'http': sorted(http), 'importCount': tot, 'short': n - got if got != n else 0,
           'conflicts': conflicts[:20], 'force': force, 'parts': parts}
    print(f'{os.path.basename(path)}: {n} values in {len(groups)} data set payload(s), HTTP {sorted(http)} {tot}'
          + (f'  SHORT by {n - got}' if got != n else ''))
    for c in conflicts[:5]:
        print('   conflict:', json.dumps(c)[:200])
    return rec

if __name__ == '__main__':
    main()
