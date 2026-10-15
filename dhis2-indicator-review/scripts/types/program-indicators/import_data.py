"""Load generated test data into the TEST instance and build analytics.

    python3 scripts/types/program-indicators/import_data.py [--config pireview.json] [--file tracker.json ...] [--batch 200]
                                            [--org-units UID ...] [--no-user-ou] [--no-analytics] [--analytics-only]
                                            [--last-years N]

Steps (each logged to <work_dir>/api-log.jsonl):
  1. refuse unless cfg.require_test_instance() passes ("test_instance": true in the config);
  2. create the test org units if missing: cfg.root (a new root) and cfg.facilities + --org-units below it
     (e.g. one extra org unit for a data-dependent probe, see probe.py);
  3. assign cfg.program to those org units;
  4. give the connected user capture/view/search access to cfg.root if it has none (skip with --no-user-ou);
  5. set system setting dataOutputPeriodTypes when empty (event analytics fails with "column yearly does not exist"
     on a fresh instance otherwise, E9);
  6. POST each --file (default tracker.json in work_dir) to
     /api/tracker?async=false&importStrategy=CREATE_AND_UPDATE&validationMode=SKIP in batches, after dropping data
     values whose data element is not in the event's stage (2.43 answers HTTP 500 for those under validationMode=SKIP);
  7. run the analytics tables job (d2.run_analytics) unless --no-analytics.
Gotcha: tracker DELETE is a soft delete; re-importing deleted UIDs fails with a duplicate key. Regenerate with a
new --seed instead.

Output: <work_dir>/import.json (summary per step). Config keys: metadata, program, work_dir, test_period,
org_units.root / facilities, test_instance. Connection: DHIS2_URL + DHIS2_TOKEN or DHIS2_USER/DHIS2_PASSWORD.
"""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import config, d2  # noqa: E401
import common

PERIOD_TYPES = ['Daily', 'Weekly', 'Monthly', 'BiMonthly', 'Quarterly', 'SixMonthly', 'Yearly', 'FinancialApril',
                'FinancialJuly', 'FinancialOct']


def main():
    ap = common.parser(__doc__)
    ap.add_argument('--file', nargs='+', help='tracker payload files (default tracker.json in work_dir)')
    ap.add_argument('--batch', type=int, default=200, help='tracked entities (or events) per request')
    ap.add_argument('--org-units', nargs='*', default=[], help='extra test org units to create below cfg.root')
    ap.add_argument('--no-user-ou', action='store_true', help='do not touch the connected user\'s org units')
    ap.add_argument('--no-analytics', action='store_true')
    ap.add_argument('--analytics-only', action='store_true', help='only run the analytics tables job')
    ap.add_argument('--last-years', type=int, help='analytics lastYears (default: covers the test period - 3 years)')
    args = ap.parse_args()
    cfg = config.load(args)
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    summary = {}
    if not args.analytics_only:
        summary['orgUnits'] = ensure_org_units(cfg, args.org_units)
        summary['programOrgUnits'] = assign_program(cfg, [cfg.root] + list(cfg.facilities) + args.org_units)
        if not args.no_user_ou:
            summary['userOrgUnits'] = ensure_user_access(cfg)
        summary['dataOutputPeriodTypes'] = ensure_output_period_types()
        stage_des = {s['id']: {x['dataElement']['id'] for x in s.get('programStageDataElements', [])} for s in cfg.md['programStages']}
        summary['imports'] = [import_file(f if os.path.exists(f) else cfg.resolve(f), stage_des, args.batch)
                              for f in (args.file or [cfg.out('tracker.json')])]
    if not args.no_analytics:
        y = int(cfg.test_period[:4])
        last = args.last_years or max(3, dt.date.today().year - y + 3)
        secs, msg, lvl = d2.run_analytics(last)
        summary['analytics'] = {'seconds': secs, 'message': msg, 'level': lvl, 'lastYears': last}
        print(f'analytics: {msg} ({secs}s, lastYears={last})')
    common.write_json(cfg.out('import.json'), summary)
    print('->', cfg.out('import.json'))


def ensure_org_units(cfg, extra):
    want = [cfg.root] + list(cfg.facilities) + list(extra)
    have = {o['id'] for o in d2.get(f"/api/organisationUnits?filter=id:in:[{','.join(want)}]&fields=id&paging=false")['organisationUnits']}
    new = []
    for i, uid in enumerate(want):
        if uid in have:
            continue
        o = {'id': uid, 'name': f'Test org unit {uid}', 'shortName': f'Test {uid}', 'openingDate': '2000-01-01'}
        if uid == cfg.root:
            o.update(name=f'Test root {uid}', shortName=f'Test root {uid}')
        else:
            o['parent'] = {'id': cfg.root}
        new.append(o)
    if new:
        s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', {'organisationUnits': new})
        print('created org units', [o['id'] for o in new], s, j.get('status'))
        if s >= 400:
            sys.exit(f'org unit creation failed: {json.dumps(j)[:500]}')
    return {'created': [o['id'] for o in new], 'existing': sorted(have)}


def assign_program(cfg, ous):
    pid = cfg.program_id
    cur = d2.get(f'/api/programs/{pid}?fields=organisationUnits[id]')['organisationUnits']
    have = {o['id'] for o in cur}
    add = [o for o in dict.fromkeys(ous) if o not in have]
    if add:
        s, j = d2.req('PATCH', f'/api/programs/{pid}', [{'op': 'replace', 'path': '/organisationUnits',
                                                         'value': cur + [{'id': o} for o in add]}], ctype='application/json-patch+json')
        print('assigned program to', add, s)
        if s >= 400:
            sys.exit(f'program assignment failed: {json.dumps(j)[:500]}')
    return {'added': add}


def ensure_user_access(cfg):
    """The importing/querying user needs the test tree in its capture, data-view and search scope."""
    me = d2.get('/api/me?fields=id,organisationUnits[id],dataViewOrganisationUnits[id],teiSearchOrganisationUnits[id]')
    path = d2.get(f'/api/organisationUnits/{cfg.root}?fields=path')['path']
    anc = set(path.strip('/').split('/'))
    ops = []
    for k in ('organisationUnits', 'dataViewOrganisationUnits', 'teiSearchOrganisationUnits'):
        if not anc & {o['id'] for o in me.get(k, [])}:
            ops.append({'op': 'add', 'path': f'/{k}/-', 'value': {'id': cfg.root}})
    if not ops:
        return {'changed': []}
    s, j = d2.req('PATCH', f"/api/users/{me['id']}", ops, ctype='application/json-patch+json')
    print('user org units', [o['path'] for o in ops], s)
    if s >= 400:
        print('warning: could not add the test root to the user; analytics at the root may be refused:', json.dumps(j)[:300])
    return {'changed': [o['path'] for o in ops], 'http': s}


def ensure_output_period_types():
    s, cur = d2.req('GET', '/api/configuration/dataOutputPeriodTypes', log=False)
    if s == 200 and isinstance(cur, list) and cur:
        return {'existing': len(cur)}
    s, j = d2.req('POST', '/api/configuration/dataOutputPeriodTypes', [{'name': n} for n in PERIOD_TYPES])
    if s >= 400:      # some versions take plain names
        s, j = d2.req('POST', '/api/configuration/dataOutputPeriodTypes', PERIOD_TYPES)
    print('dataOutputPeriodTypes set:', s)
    return {'set': PERIOD_TYPES, 'http': s}


def import_file(path, stage_des, batch):
    with open(path) as f:
        payload = json.load(f)
    dropped = 0

    def clean(ev):
        nonlocal dropped
        keep = [v for v in ev.get('dataValues', []) if v['dataElement'] in stage_des.get(ev['programStage'], ())]
        dropped += len(ev.get('dataValues', [])) - len(keep)
        ev['dataValues'] = keep

    key = 'trackedEntities' if payload.get('trackedEntities') else 'events'
    items = payload.get(key, [])
    for it in items:
        for ev in ([it] if key == 'events' else [e for en in it.get('enrollments', []) for e in en.get('events', [])]):
            clean(ev)
    stats, errors, http = {'created': 0, 'updated': 0, 'ignored': 0}, [], []
    for i in range(0, len(items), batch):
        s, j = d2.req('POST', '/api/tracker?async=false&importStrategy=CREATE_AND_UPDATE&validationMode=SKIP',
                      {key: items[i:i + batch]}, timeout=1800)
        http.append(s)
        for k in stats:
            stats[k] += (j.get('stats') or {}).get(k, 0)
        errors += [e.get('message') for e in (j.get('validationReport') or {}).get('errorReports', [])][:5]
        if s >= 400 and not j.get('validationReport'):
            errors.append(f"HTTP {s}: {(j.get('message') or j.get('_raw') or '')[:200]}")
        print(f'  {os.path.basename(path)} batch {i // batch + 1}: HTTP {s} {j.get("status")} {j.get("stats")}')
    print(f'{path}: {len(items)} {key}, {dropped} data values dropped (data element not in the stage), {stats}, {len(errors)} errors')
    return {'file': path, 'items': len(items), 'droppedValues': dropped, 'stats': stats, 'http': sorted(set(http)), 'errors': errors[:20]}


if __name__ == '__main__':
    main()
