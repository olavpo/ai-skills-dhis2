"""End-to-end test of the proposed fixes on a disposable test instance. One step per call:

  witness           generate witness data for the PROPOSED filters (validate/gen.py on metadata-after.json, the affected
                    PIs only) -> tracker-fix.json, truth-fix.json
  load              create the scenario org units under cfg.root, assign the program to them, import the witness data
                    (tracker-fix.json, if present) and the scenario data (data/scen-tracker.json)
  revert            put the ORIGINAL definitions back (PIs from the export, the program's categoryMappings from the
                    export), so every cycle starts from the production state
  reload-scenarios  delete the tracked entities (or events) in the scenario org units and import the scenario data
                    again (new UIDs: run scen.py first; deleted tracker objects keep their UIDs)
  before | after    run analytics, query every affected PI (validate/query.py, tag before/after) and every scenario
                    check at its org unit and period (-> scenario-before.json / scenario-after.json)
  apply             POST proposed-programIndicators.json (importStrategy=UPDATE, atomicMode=ALL), PATCH the program
                    with proposed-categoryMappings.patch.json (application/json-patch+json), then read back every
                    changed field of every PI and every mapping and diff it against the payload

Usual order: scen.py -> witness -> revert -> load -> before -> apply -> after -> check.py. Re-run from revert:
revert -> (scen.py) -> reload-scenarios -> before -> apply -> after -> check.py.

"Affected" = the PIs changed directly + every PI using a changed mapping (affected-ids.json).
Every write goes through lib/d2.py and is logged to <work_dir>/api-log.jsonl; step summaries go to e2e-steps.jsonl.

Inputs : fixes.json, proposed-programIndicators.json, proposed-categoryMappings.patch.json (optional),
         scenario-checks.json + data/scen-*.json (scen.py), probe-ids.json (probes.py, optional), the metadata export.
Config : metadata, program, work_dir, test_period, org_units.root, org_units.scenario_prefix, test_instance.
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config, d2  # noqa: E402
import e2elib as E  # noqa: E402

STEPS = ('witness', 'load', 'revert', 'reload-scenarios', 'before', 'apply', 'after')


def stats(j):
    r = j.get('response', j) if isinstance(j.get('response', j), dict) else j
    return r.get('stats')


def import_errors(j):
    r = j.get('response', j) if isinstance(j.get('response', j), dict) else j
    return [e.get('message') for tr in r.get('typeReports', []) for o in tr.get('objectReports', []) for e in o.get('errorReports', [])]


def tracker_errors(j):
    return [e.get('message') for e in (j.get('validationReport') or {}).get('errorReports', [])][:5]


def patch_program(cfg, ops):
    return d2.req('PATCH', f'/api/programs/{cfg.program_id}', ops, ctype='application/json-patch+json')


# ---------- scenario org units and data ----------
def scenario_ous(cfg):
    p = os.path.join(cfg.work_dir, 'data', 'scen-ous.json')
    return E.jload(p) if os.path.exists(p) else {'organisationUnits': []}


def ensure_scenario_ous(cfg):
    ous = scenario_ous(cfg)
    if not ous['organisationUnits']:
        return []
    s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', ous)
    E.log_step(cfg, 'scenario org units', {'http': s, 'status': j.get('status'), 'stats': stats(j)})
    _, p = d2.req('GET', f'/api/programs/{cfg.program_id}?fields=organisationUnits[id]', log=False)
    have = {o['id'] for o in p.get('organisationUnits', [])}
    new = [o['id'] for o in ous['organisationUnits'] if o['id'] not in have]
    if new:
        # replacing the whole list is what 2.43 accepts reliably; keep every org unit already assigned
        s, _ = patch_program(cfg, [{'op': 'replace', 'path': '/organisationUnits',
                                    'value': p.get('organisationUnits', []) + [{'id': i} for i in new]}])
        E.log_step(cfg, 'assign program to scenario org units', {'http': s, 'added': len(new)})
    return [o['id'] for o in ous['organisationUnits']]


def import_tracker(cfg, path, label):
    if not os.path.exists(path):
        print('skip (missing)', path)
        return
    s, j = d2.req('POST', '/api/tracker?async=false&validationMode=SKIP&importStrategy=CREATE', E.jload(path))
    E.log_step(cfg, 'tracker import ' + label, {'http': s, 'status': j.get('status'), 'stats': j.get('stats'), 'errors': tracker_errors(j)})
    print('tracker import', label, s, j.get('status'), j.get('stats'))


def delete_scenario_data(cfg, ous):
    gone = 0
    tracker = cfg.program.get('programType', 'WITH_REGISTRATION') != 'WITHOUT_REGISTRATION'
    for ou in ous:
        if tracker:
            _, j = d2.req('GET', f'/api/tracker/trackedEntities?orgUnits={ou}&program={cfg.program_id}&fields=trackedEntity&pageSize=1000', log=False)
            objs = [{'trackedEntity': t['trackedEntity']} for t in j.get('trackedEntities', j.get('instances', []))]
            body = {'trackedEntities': objs}
        else:
            _, j = d2.req('GET', f'/api/tracker/events?orgUnit={ou}&program={cfg.program_id}&fields=event&pageSize=1000', log=False)
            objs = [{'event': e['event']} for e in j.get('events', j.get('instances', []))]
            body = {'events': objs}
        if objs:
            d2.req('POST', '/api/tracker?async=false&importStrategy=DELETE', body)
            gone += len(objs)
    return gone


# ---------- queries ----------
def probe_ids(cfg):
    p = os.path.join(cfg.work_dir, 'probe-ids.json')
    return E.jload(p) if os.path.exists(p) else {}


def query_scenarios(cfg, tag, md):
    """Total (and cells per expected category) of every scenario check at its org unit and period."""
    p = os.path.join(cfg.work_dir, 'scenario-checks.json')
    if not os.path.exists(p):
        print('no scenario-checks.json: run scen.py first; scenarios not queried')
        return {}
    probes, out = probe_ids(cfg), {}
    CO = {c['id']: c['name'] for c in md['categoryOptions']}
    for s in E.jload(p):
        pid = s['pi']
        if s.get('probe'):
            ref = s['probe'].get(tag)
            pid = probes.get(ref, ref)
        st, j = d2.analytics([('dimension', 'dx:' + pid), ('filter', 'pe:' + s['pe']), ('filter', 'ou:' + s['ou'])])
        rec = {'pe': s['pe'], 'pi': pid, 'status': st}
        if st == 200:
            rec['total'] = float(j['rows'][0][-1]) if j.get('rows') else 0.0
        else:
            rec['error'] = (j.get('message') or j.get('_raw') or '')[:200]
        cells = {}
        for cat in s.get('expectCellsAfter') or {}:
            cid = E.resolve_category(md, cat)
            if not cid:
                cells[cat] = {'error': 'unknown category'}
                continue
            st2, j2 = d2.analytics([('dimension', 'dx:' + pid), ('dimension', cid), ('filter', 'pe:' + s['pe']), ('filter', 'ou:' + s['ou'])])
            if st2 == 200:
                h = [x['name'] for x in j2['headers']]
                cells[cat] = {CO.get(r[h.index(cid)], r[h.index(cid)]): float(r[h.index('value')]) for r in j2.get('rows', [])}
            else:
                cells[cat] = {'error': (j2.get('message') or '')[:200]}
        rec['cells'] = cells
        out[s['key']] = rec
    E.jdump(out, cfg.out(f'scenario-{tag}.json'))
    return out


def last_years(cfg):
    """Analytics years to cover: from the earliest date in the scenario/witness data to now."""
    import datetime as dt
    y0 = cfg.period_bounds()[0].year
    for f in ('data/scen-truth.json', 'truth-fix.json', 'truth.json'):
        p = os.path.join(cfg.work_dir, f)
        if os.path.exists(p):
            for t in E.jload(p):
                for d in [t.get('enrolledAt')] + [e['date'] for e in t.get('events', [])]:
                    if d:
                        y0 = min(y0, int(d[:4]))
    return max(2, dt.date.today().year - y0 + 1)


# ---------- read-back ----------
def norm(field, v):
    """Comparable form of a PI field as sent vs as read back."""
    if field == 'analyticsPeriodBoundaries':
        return sorted((b.get('boundaryTarget'), b.get('analyticsPeriodBoundaryType'), b.get('offsetPeriods') or 0, b.get('offsetPeriodType'))
                      for b in v or [])
    if isinstance(v, dict):
        return v.get('id', json.dumps(v, sort_keys=True))
    if isinstance(v, list):
        return sorted(norm(field, x) if x is not None else '' for x in v) if all(not isinstance(x, list) for x in v) else v
    return '' if v is None else v


def changed_fields(r):
    b, a = r['before'], r['after']
    return sorted(k for k in set(a) | set(b) if norm(k, a.get(k)) != norm(k, b.get(k)))


def norm_mapping(m):
    return (m.get('categoryId'), m.get('mappingName'), sorted((o.get('optionId'), (o.get('filter') or '').strip()) for o in m.get('optionMappings', [])))


def read_back(cfg, payload, patch):
    F = E.fixes(cfg)
    fields = {r['id']: changed_fields(r) for r in F['pis']}
    allf = sorted({f for fs in fields.values() for f in fs} | {'expression', 'filter', 'description'})
    sel = ','.join(f if f != 'analyticsPeriodBoundaries' else 'analyticsPeriodBoundaries[boundaryTarget,analyticsPeriodBoundaryType,offsetPeriods,offsetPeriodType]'
                   for f in allf)
    G, ids = {}, [p['id'] for p in payload]
    for i in range(0, len(ids), 100):
        _, got = d2.req('GET', f'/api/programIndicators?filter=id:in:[{",".join(ids[i:i + 100])}]&fields=id,code,{sel}&paging=false')
        G.update({p['id']: p for p in got.get('programIndicators', [])})
    diffs = []
    for p in payload:
        g = G.get(p['id'])
        if not g:
            diffs.append({'pi': E.pi_label(p), 'field': '(missing on the instance)'})
            continue
        for f in sorted(set(fields.get(p['id'], [])) | {'expression', 'filter', 'description'}):
            if norm(f, g.get(f)) != norm(f, p.get(f)):
                diffs.append({'pi': E.pi_label(p), 'field': f, 'sent': p.get(f), 'got': g.get(f)})
    mdiff = []
    if patch:
        _, prog = d2.req('GET', f'/api/programs/{cfg.program_id}?fields=categoryMappings')
        have = {m['id']: m for m in prog.get('categoryMappings', [])}
        want = {m['id']: m for op in patch if op.get('path') == '/categoryMappings' for m in op['value']}
        mdiff = [{'mapping': k, 'name': w.get('mappingName')} for k, w in want.items() if k not in have or norm_mapping(have[k]) != norm_mapping(w)]
        mdiff += [{'mapping': k, 'name': h.get('mappingName'), 'extra': True} for k, h in have.items() if k not in want]
    return diffs, mdiff, len(G)


# ---------- steps ----------
def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('step', choices=STEPS)
    ap.add_argument('--last-years', type=int, help='analytics years (default: from the test data dates)')
    args = ap.parse_args()
    cfg = config.load(args)
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    E.after_metadata(cfg)                 # metadata-after.json
    aff = E.affected(cfg)                 # affected-ids.json
    step = args.step
    F = E.fixes(cfg)

    if step == 'witness':
        # own seed: the same seed as phase 1 would regenerate the phase-1 UIDs and collide on import
        r = E.run_validate(cfg, 'gen.py', *E.overlay_args(cfg, 'after'), '--pis', cfg.out('affected-ids.json'), '--tag', 'fix',
                           '--seed', cfg.seed + 1000, '--noise', 100, '--controls', 20)
        E.log_step(cfg, 'witness data', {'rc': r.returncode, 'stdout': r.stdout[-1500:]})

    elif step == 'load':
        ensure_scenario_ous(cfg)
        import_tracker(cfg, os.path.join(cfg.work_dir, 'tracker-fix.json'), 'witness data')
        import_tracker(cfg, os.path.join(cfg.work_dir, 'data', 'scen-tracker.json'), 'scenario data')

    elif step == 'revert':
        ids = {r['id'] for r in F['pis']}
        orig = [p for p in cfg.md['programIndicators'] if p['id'] in ids]
        s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE&atomicMode=ALL', {'programIndicators': orig})
        E.log_step(cfg, 'revert programIndicators to export', {'http': s, 'status': j.get('status'), 'stats': stats(j), 'errors': import_errors(j)[:10]})
        print('revert PIs', s, j.get('status'), stats(j))
        if F['mappings'] or E.mapping_patch(cfg):
            s2, j2 = patch_program(cfg, [{'op': 'replace', 'path': '/categoryMappings', 'value': cfg.program.get('categoryMappings', [])}])
            E.log_step(cfg, 'revert categoryMappings to export', {'http': s2})
            print('revert mappings', s2)

    elif step == 'reload-scenarios':
        ous = ensure_scenario_ous(cfg)
        gone = delete_scenario_data(cfg, ous)
        p = os.path.join(cfg.work_dir, 'data', 'scen-tracker.json')
        s, j = d2.req('POST', '/api/tracker?async=false&validationMode=SKIP&importStrategy=CREATE', E.jload(p))
        E.log_step(cfg, 'reload scenario data', {'deleted': gone, 'http': s, 'status': j.get('status'), 'stats': j.get('stats'), 'errors': tracker_errors(j)})
        print('deleted', gone, 'imported', s, j.get('status'), j.get('stats'))

    elif step in ('before', 'after'):
        t, msg, lvl = d2.run_analytics(args.last_years or last_years(cfg))
        E.log_step(cfg, 'analytics ' + step, {'seconds': t, 'message': msg, 'level': lvl})
        print('analytics', t, msg)
        md_path = cfg.metadata_path if step == 'before' else E.metadata_after_path(cfg)
        r = E.run_validate(cfg, 'query.py', *E.overlay_args(cfg, step), '--pis', cfg.out('affected-ids.json'), '--tag', step)
        E.log_step(cfg, 'query ' + step, {'rc': r.returncode, 'pis': len(aff), 'stdout': r.stdout[-1500:]})
        sc = query_scenarios(cfg, step, E.jload(md_path))
        E.log_step(cfg, 'scenarios ' + step, {k: v.get('total', 'error') for k, v in sc.items()})
        for k, v in sc.items():
            print(f'  {k:28} {v.get("total", "error")!s:8} {v.get("error", "")}')

    elif step == 'apply':
        payload = E.proposed_pis(cfg)
        s, j = d2.req('POST', '/api/metadata?importStrategy=UPDATE&atomicMode=ALL', {'programIndicators': payload})
        errs = import_errors(j)
        E.log_step(cfg, 'import programIndicators', {'http': s, 'status': j.get('status'), 'stats': stats(j), 'errors': errs[:10]})
        print('PI import', s, j.get('status'), stats(j), errs[:5])
        patch = E.mapping_patch(cfg)
        if patch:
            s2, j2 = patch_program(cfg, patch)
            E.log_step(cfg, 'patch categoryMappings', {'http': s2, 'response': j2 if s2 >= 400 else None})
            print('mapping patch', s2, j2 if s2 >= 400 else '')
        diffs, mdiff, n = read_back(cfg, payload, patch)
        E.log_step(cfg, 'read-back', {'pis_read': n, 'pi_diffs': diffs[:50], 'mapping_diffs': mdiff})
        print(f'read back {n} PIs: {len(diffs)} field differences, {len(mdiff)} mapping differences')
        for d in diffs[:10]:
            print('  ', d)


if __name__ == '__main__':
    main()
