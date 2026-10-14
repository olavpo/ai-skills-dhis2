"""End-to-end test of the proposed fixes on a disposable test instance. One step per call:

  witness           generate witness data for the PROPOSED definitions (the type's gen.py on the proposed overlays,
                    the affected items only, --tag fix, its own seed)
  load              the type's e2e data: scenario org units, witness data, scenario data (adapter.e2e_load)
  revert            put the ORIGINAL definitions back (items and aux objects from the export, adapter.revert), so every
                    cycle starts from the production state
  reload-scenarios  delete the scenario data in the scenario org units and import it again (adapter.e2e_reload_scenarios;
                    new UIDs: run the type's scen.py first, deleted tracker objects keep their UIDs)
  before | after    run analytics, query every affected item (the type's query.py, tag before/after) and every scenario
                    check at its org unit and period (adapter.query -> scenario-before.json / scenario-after.json)
  apply             import the proposed payload (adapter.import_items, UPDATE, atomicMode=ALL) and the aux file
                    (adapter.import_aux), then read back every item and aux object (adapter.read_back / read_back_aux)
                    and diff its diff_fields against the payload

Usual order: scenarios (run.py scenarios) -> witness -> revert -> load -> before -> apply -> after -> check.py.
Re-run from revert: revert -> (scenarios) -> reload-scenarios -> before -> apply -> after -> check.py.

"Affected" = the items changed directly + every item using a changed aux object (affected-ids.json).
Every write goes through lib/d2.py and is logged to <work_dir>/api-log.jsonl; step summaries go to e2e-steps.jsonl
(step names: "import items", "import aux", "read-back" {items_read, item_diffs, aux_diffs}, plus the adapter's).

Inputs : fixes.json, the proposed payload and aux file (adapter.PROPOSED_FILE / AUX_FILE), scenario-checks.json +
         data/ (the type's scen.py), probe-ids.json (optional: scenario checks may query a probe), the metadata export.
Config : metadata, work_dir, test_period, org_units.root, org_units.scenario_prefix, test_instance (+ type keys).
"""
import datetime as dt, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, config, d2  # noqa: E402
import e2elib as E  # noqa: E402

STEPS = ('witness', 'load', 'revert', 'reload-scenarios', 'before', 'apply', 'after')


def probe_ids(cfg):
    p = os.path.join(cfg.work_dir, 'probe-ids.json')
    return E.jload(p) if os.path.exists(p) else {}


def query_scenarios(cfg, tag, qcfg):
    """Total (and cells per expected breakdown) of every scenario check at its org unit and period."""
    A = adapters.load(cfg)
    p = os.path.join(cfg.work_dir, 'scenario-checks.json')
    if not os.path.exists(p):
        print('no scenario-checks.json: run the scenarios step first; scenarios not queried')
        return {}
    probes, out = probe_ids(cfg), {}
    for s in E.jload(p):
        iid = E.scenario_item(s)
        if s.get('probe'):
            ref = s['probe'].get(tag)
            iid = probes.get(ref, ref)
        q = A.query(qcfg, iid, s['ou'], s['pe'])
        rec = {'pe': s['pe'], 'item': iid, 'status': q.get('status', 200 if q.get('error') is None else 'error')}
        if q.get('error') is None:
            rec['total'] = q['value']
        else:
            rec['error'] = q['error']
        cells = {}
        for by in s.get('expectCellsAfter') or {}:
            cq = A.query(qcfg, iid, s['ou'], s['pe'], by=by)
            cells[by] = cq.get('cells') if cq.get('error') is None else {'error': cq['error']}
        rec['cells'] = cells
        out[s['key']] = rec
    E.jdump(out, cfg.out(f'scenario-{tag}.json'))
    return out


def last_years(cfg):
    """Analytics years to cover: from the earliest year in the test data (adapter.earliest_year) to now."""
    A = adapters.load(cfg)
    y0 = A.earliest_year(cfg) if hasattr(A, 'earliest_year') else cfg.period_bounds()[0].year
    return max(2, dt.date.today().year - y0 + 1)


def read_back(cfg, A, payload, aux_all):
    """Field differences between what was sent and what the instance holds (diff_fields / aux_diff_fields)."""
    G = A.read_back(cfg, [p['id'] for p in payload])
    diffs = []
    for p in payload:
        g = G.get(p['id'])
        if not g:
            diffs.append({'item': E.item_label(p), 'field': '(missing on the instance)'})
            continue
        sent, got = A.diff_fields(p, cfg), A.diff_fields(g, cfg)
        for f in sent:
            if sent[f] != got.get(f):
                diffs.append({'item': E.item_label(p), 'field': f, 'sent': sent[f], 'got': got.get(f)})
    adiff = []
    if aux_all is not None:
        want = {m['id']: m for m in aux_all}
        have = A.read_back_aux(cfg, list(want))
        name = lambda m: A.aux_describe(cfg, m)['name']
        adiff = [{'aux': k, 'name': name(w)} for k, w in want.items()
                 if k not in have or A.aux_diff_fields(have[k], cfg) != A.aux_diff_fields(w, cfg)]
        adiff += [{'aux': k, 'name': name(h), 'extra': True} for k, h in have.items() if k not in want]
    return diffs, adiff, len(G)


def aux_after_all(cfg, F):
    """Every aux object as it should be after the fix (exported, fixed ones replaced), or None when none changed."""
    if not F['aux']:
        return None
    A = adapters.load(cfg)
    fixed = {m['id']: m['after'] for m in F['aux']}
    return [fixed.get(m['id'], m) for m in A.aux_items(cfg)] + [m for k, m in fixed.items() if k not in {x['id'] for x in A.aux_items(cfg)}]


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('step', choices=STEPS)
    ap.add_argument('--last-years', type=int, help='analytics years (default: from the test data dates)')
    args = ap.parse_args()
    cfg = config.load(args)
    A = adapters.load(cfg)
    cfg.require_test_instance()
    d2.set_log(cfg.out('api-log.jsonl'))
    E.after_metadata(cfg)                 # metadata-after.json
    aff = E.affected(cfg)                 # affected-ids.json
    step = args.step
    F = E.fixes(cfg)

    if step == 'witness':
        # own seed: the same seed as phase 1 would regenerate the phase-1 UIDs and collide on import
        r = E.run_type(cfg, 'gen.py', *E.overlay_args(cfg, 'after'), '--items', cfg.out('affected-ids.json'), '--tag', 'fix',
                       '--seed', cfg.seed + 1000, *getattr(A, 'WITNESS_ARGS', []))
        E.log_step(cfg, 'witness data', {'rc': r.returncode, 'stdout': r.stdout[-1500:]})

    elif step == 'load':
        adapters.need(A, 'e2e_load')(cfg)

    elif step == 'revert':
        for name, data in A.revert(cfg, [r['id'] for r in F['items']], sorted(E.changed_aux_ids(cfg))):
            E.log_step(cfg, name, data)
            print(name, data.get('http'), data.get('status', ''), data.get('stats', ''))

    elif step == 'reload-scenarios':
        adapters.need(A, 'e2e_reload_scenarios')(cfg)

    elif step in ('before', 'after'):
        t, msg, lvl = d2.run_analytics(args.last_years or last_years(cfg))
        E.log_step(cfg, 'analytics ' + step, {'seconds': t, 'message': msg, 'level': lvl})
        print('analytics', t, msg)
        r = E.run_type(cfg, 'query.py', *E.overlay_args(cfg, step), '--items', cfg.out('affected-ids.json'), '--tag', step)
        E.log_step(cfg, 'query ' + step, {'rc': r.returncode, 'items': len(aff), 'stdout': r.stdout[-1500:]})
        sc = query_scenarios(cfg, step, cfg if step == 'before' else E.after_cfg(cfg))
        E.log_step(cfg, 'scenarios ' + step, {k: v.get('total', 'error') for k, v in sc.items()})
        for k, v in sc.items():
            print(f'  {k:28} {v.get("total", "error")!s:8} {v.get("error", "")}')

    elif step == 'apply':
        payload = E.proposed_items(cfg)
        s, j = A.import_items(cfg, payload, 'UPDATE')
        errs = E.import_errors(j)
        E.log_step(cfg, 'import items', {'what': A.PAYLOAD_KEY, 'http': s, 'status': j.get('status'), 'stats': E.import_stats(j), 'errors': errs[:10]})
        print('import', A.PAYLOAD_KEY, s, j.get('status'), E.import_stats(j), errs[:5])
        aux_all = aux_after_all(cfg, F)
        if aux_all is not None:
            s2, j2 = A.import_aux(cfg, aux_all)
            E.log_step(cfg, 'import aux', {'what': A.LABELS['auxs'], 'http': s2, 'response': j2 if s2 >= 400 else None})
            print('import', A.LABELS['auxs'], s2, j2 if s2 >= 400 else '')
        diffs, adiff, n = read_back(cfg, A, payload, aux_all)
        E.log_step(cfg, 'read-back', {'items_read': n, 'item_diffs': diffs[:50], 'aux_diffs': adiff})
        print(f'read back {n} {A.LABELS["shorts"]}: {len(diffs)} field differences, {len(adiff)} {A.LABELS["aux_short"]} differences')
        for d in diffs[:10]:
            print('  ', d)


if __name__ == '__main__':
    main()
