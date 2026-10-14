"""Type adapter: aggregate indicators (numerator / denominator over aggregated data values), with the data elements
they read as the secondary ("aux") objects (fixes may change a data element, e.g. its aggregationType).

The core (run.py and the scripts outside types/) knows nothing about indicators: everything it needs comes from here.
Interface: references/data-formats.md, "Type adapters". Engine behaviours: references/indicators/behaviours.md.

Config keys of this type (besides the core keys in lib/config.py):
    "type": "indicators",
    "indicators": ["<uid or code>", ...] | "<indicator group uid>"   # optional scope; default every indicator in the export
    "org_units": {"root": ..., "facilities": [>= 2, best 3 test facilities], "scenario_prefix": ...}
                                                    # parent aggregation is tested at root; gen.py puts the edge cases
                                                    # (zero / missing denominator) at the last facility
    "test_period": "2026Q1"                         # data in every period of each data set's period type inside or
                                                    # around the quarter
The export: dependency exports of the data sets whose data elements the indicators read (data elements, category
model incl. COCs), plus indicators, indicatorTypes, constants, the org unit groups used by OUG{...} (without
members), and indicatorGroups when the scope is a group (examples/sl-aggregate/export_sl.py does this).
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, '..', '..', 'lib'))
if LIB not in sys.path:
    sys.path.insert(1, LIB)
import config  # noqa: E402
import common  # noqa: E402  (this folder's common.py: the type folder is first on sys.path)

TYPE = 'indicators'
ITEM_KIND = 'indicator'
AUX_KIND = 'dataElement'
PREFIX = 'indicator'
LABELS = {'item': 'indicator', 'items': 'indicators', 'Item': 'Indicator', 'Items': 'Indicators',
          'short': 'indicator', 'shorts': 'indicators',
          'aux': 'data element', 'auxs': 'data elements', 'aux_short': 'data element', 'aux_shorts': 'data elements',
          'auxField': 'Setting', 'auxRow': 'Setting',
          'record': 'data value', 'records': 'data values', 'truthUnit': 'data values', 'definition': 'numerator',
          'fields': 'numerator, denominator, indicator type, annualized, decimals'}
PAYLOAD_KEY = 'indicators'
PROPOSED_FILE = 'proposed-indicators.json'
AUX_FILE = 'proposed-dataElements.json'
TOPICS_FILE = os.path.join(HERE, 'topics.json')
DESCRIPTION_BRIEF = os.path.join(HERE, 'description_brief.md')
WITNESS_ARGS = []
MAINTENANCE_ROUTE = '/dhis-web-maintenance/index.html#/edit/indicatorSection/indicator/{}'
DX_TYPE = ('INDICATOR', 'indicator')
CORE_AUX_PROPAGATION = True

# lint check -> status (None: not a status); other ERROR -> WRONG_VALUE, WARN -> REVIEW, INFO ignored
LINT_STATUS = {
    'legacy-reporting-rate-operand': 'ALWAYS_ZERO', 'denominator-zero': 'ALWAYS_ZERO', 'empty-expression': 'FAILS',
    'unknown-indicator-type': 'FAILS',
    'unknown-de': 'WRONG_VALUE', 'unknown-coc': 'WRONG_VALUE', 'unknown-aoc': 'WRONG_VALUE', 'coc-not-in-de-combo': 'WRONG_VALUE',
    'aoc-not-in-data-set-combo': 'WRONG_VALUE', 'unknown-constant': 'WRONG_VALUE', 'unknown-org-unit-group': 'WRONG_VALUE',
    'division-in-numerator-denominator-1': 'REVIEW', 'factor-100-and-times-100': 'WRONG_VALUE',
    'percent-name-number-type': 'WRONG_VALUE', 'count-name-percent-type': 'WRONG_VALUE', 'name-factor-mismatch': 'WRONG_VALUE',
    'numerator-equals-denominator': 'WRONG_VALUE',
    'coc-not-fitting-combo': 'REVIEW', 'inner-division': 'REVIEW', 'overlapping-operands': 'REVIEW',
    'rate-name-denominator-1': 'REVIEW', 'annualized-without-population': 'REVIEW', 'population-rate-not-annualized': 'REVIEW',
    'duplicate-definition': 'REVIEW', 'parse-error': None,
}
AUX_INFO_CHECKS = set()
SYNTAX_CHECKS = {'parse-error', 'legacy-reporting-rate-operand', 'unknown-de', 'empty-expression'}


class IndConfig(config.Config):
    """Config with the aggregate-indicator keys and lookups (cfg.meta)."""
    MD_KEYS = ('indicators', 'indicatorTypes', 'indicatorGroups', 'dataElements', 'dataSets', 'categoryCombos', 'categories',
               'categoryOptions', 'categoryOptionCombos', 'constants', 'organisationUnitGroups', 'optionSets', 'options')
    DEFAULT_TITLE = 'Aggregate indicators'

    def __init__(self, path):
        super().__init__(path)
        self._meta = None

    @property
    def meta(self):
        if self._meta is None:
            self._meta = common.Meta(self.md)
        return self._meta


CONFIG = IndConfig


def __getattr__(name):
    if name == 'patterns':                         # the fix-pattern module, imported lazily
        import patterns
        return patterns
    raise AttributeError(name)


def _meta(cfg):
    return cfg.meta if hasattr(cfg, 'meta') else common.Meta(cfg.md)


# ------------------------------------------------------------------------------------------------ objects
def items(cfg):
    return common.scope(cfg)


def referenced_des(ind):
    return {m[0] for side in ('numerator', 'denominator') for m in common.REF_RX.findall(ind.get(side) or '')}


def aux_items(cfg):
    """The data elements the indicators in scope read (only those in the export)."""
    M = _meta(cfg)
    ids = set()
    for i in items(cfg):
        ids |= referenced_des(i)
    return [M.de[d] for d in sorted(ids) if d in M.de]


def aux_users(cfg, aux_id, objs):
    return [i['id'] for i in objs if aux_id in referenced_des(i)]


def apply_overlay(cfg, ov, path):
    common.apply_overlay(cfg, ov, path)


def scope(cfg):
    want = cfg.raw.get('indicators')
    if isinstance(want, str):
        g = _meta(cfg).ig.get(want) or {}
        return {'id': want, 'name': g.get('name', want)}
    return {'id': 'indicators', 'name': 'selected indicators' if want else 'all indicators in the export'}


def scope_text(cfg, n_items=None, n_aux=None):
    n = len(items(cfg)) if n_items is None else n_items
    m = len(aux_items(cfg)) if n_aux is None else n_aux
    return f"{n} aggregate indicators ({scope(cfg)['name']}) and the {m} data elements they read"


def names(cfg, form_names=False):
    """uid -> name of the data elements, COCs, constants, groups and data sets a numerator or a diff may show."""
    M, out = _meta(cfg), {}
    for k in ('coc', 'const', 'oug', 'ds'):
        out.update({u: o.get('name', u) for u, o in getattr(M, k).items()})
    for u, o in M.de.items():
        out[u] = (o.get('formName') or o.get('name', u)) if form_names else o.get('name', u)
    return out


def clean(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def type_text(cfg, ind):
    M = _meta(cfg)
    it = M.it.get((ind.get('indicatorType') or {}).get('id')) or {}
    f = it.get('factor')
    return f"{it.get('name', (ind.get('indicatorType') or {}).get('id', '?'))} (×{f:g})" if isinstance(f, (int, float)) else it.get('name', '?')


def describe(cfg, ind):
    fields = [{'key': 'numerator', 'label': 'Numerator', 'text': clean(ind.get('numerator'))[:1400], 'code': True},
              {'key': 'denominator', 'label': 'Denominator', 'text': clean(ind.get('denominator'))[:1400], 'code': True},
              {'key': 'indicatorType', 'label': 'Indicator type', 'text': type_text(cfg, ind), 'code': False}]
    extra = []
    if ind.get('annualized'):
        extra.append('annualized')
    if ind.get('decimals') is not None:
        extra.append(f"{ind['decimals']} decimals")
    if extra:
        fields.append({'key': 'settings', 'label': 'Settings', 'text': ', '.join(extra), 'code': False})
    f = _meta(cfg).factor(ind)
    return {'id': ind['id'], 'code': ind.get('code') or '', 'name': ind.get('name') or ind['id'],
            'type': ('×' + f'{f:g}') if f is not None else '?', 'agg': 'annualized' if ind.get('annualized') else '',
            'fields': fields}


def diff_fields(ind, cfg=None):
    """Canonical text per changeable field (approval page diff and read-back comparison)."""
    it = (ind.get('indicatorType') or {}).get('id') or ''
    return {'Numerator': clean(ind.get('numerator')), 'Denominator': clean(ind.get('denominator')),
            'Indicator type': type_text(cfg, ind) if cfg else it,
            'Annualized': 'yes' if ind.get('annualized') else 'no',
            'Decimals': '' if ind.get('decimals') is None else str(ind.get('decimals')),
            'Numerator description': ind.get('numeratorDescription') or '',
            'Denominator description': ind.get('denominatorDescription') or '',
            'description': ind.get('description') or ''}


def aux_describe(cfg, de):
    M = _meta(cfg)
    ds = [f"{M.ds[d].get('name', d)} ({M.ds_period_type(d)})" for d in M.de_ds.get(de['id'], [])]
    rows = [('aggregationType', 'Aggregation type', de.get('aggregationType') or '(none)'),
            ('valueType', 'Value type', de.get('valueType') or ''),
            ('zeroIsSignificant', 'Zero is significant', 'yes' if de.get('zeroIsSignificant') else 'no'),
            ('categoryCombo', 'Category combo', M.cc.get((de.get('categoryCombo') or {}).get('id'), {}).get('name', '')),
            ('dataSets', 'Data sets', '; '.join(ds) or '(none)')]
    return {'id': de['id'], 'name': de.get('name') or de['id'], 'type': de.get('aggregationType') or '',
            'fields': [{'key': k, 'label': l, 'text': t, 'code': False} for k, l, t in rows]}


def aux_diff_fields(de, cfg=None):
    """The data element settings fixes may change. zeroIsSignificant is left out: import_data.py changes it on the
    test copy on purpose (A3), so a read-back would always differ."""
    return {'Aggregation type': de.get('aggregationType') or '(none)', 'Value type': de.get('valueType') or '',
            'Category combo': (de.get('categoryCombo') or {}).get('id', '')}


def truth_text(truth):
    vals = [t for t in truth if t.get('value') not in (None, '')]
    return (f"{len(vals):,} generated data values ({len({t['de'] for t in vals})} data elements, {len({t['ou'] for t in vals})} org units; "
            f"{len(truth) - len(vals):,} cells deliberately blank)")


# ------------------------------------------------------------------------------------------------ validate
def lint_status(f, ind):
    chk = f['check']
    if chk in LINT_STATUS:
        return LINT_STATUS[chk]
    return {'ERROR': 'WRONG_VALUE', 'WARN': 'REVIEW'}.get(f['severity'])


def status_rows(cfg, P, data):
    """Engine-check observations specific to aggregate indicators. "No value" rows only for indicators that no lint
    finding (on the indicator or on a data element it reads) explains."""
    rows = []
    flagged = {f['id'] for f in data['lint'] if f['severity'] in ('ERROR', 'WARN')}
    for iid, x in data['compare'].items():
        if x.get('status') != 'compared' or iid not in P:
            continue
        explained = iid in flagged or bool(referenced_des(P[iid]) & flagged)
        cells = (data['analytics'].get(iid) or {}).get('cells') or {}
        nan = sorted(k for k, v in cells.items() if v == 'NaN')
        if nan:
            rows.append((iid, 'WRONG_VALUE', 'test:nan-values', f"analytics returns NaN at {', '.join(nan[:4])} (a division by zero inside the numerator or denominator, A4)"))
        if x.get('analytics_total') is None and x.get('total_ok') and not explained:
            if not x.get('matches'):
                rows.append((iid, 'REVIEW', 'test:reads-no-data', 'the test data has no value for any operand: the indicator reads cells that data entry cannot fill (see the other findings)'))
            else:
                rows.append((iid, 'REVIEW', 'test:no-value-in-quarter', f"{x['matches']} data values are read, but the quarter at the root has no value (denominator 0 or missing, A1/A2, or data in a longer period, A7)"))
    return rows


def texts_to_validate(ind):
    return [(k, k, ind.get(k) or '') for k in ('numerator', 'denominator')]


def validate_text(kind, text):
    import syntax
    return syntax.validate_text(kind, text)


def aux_texts_to_validate(cfg, before, after, all_options=False):
    return []                                      # data element settings have no expression text


# ------------------------------------------------------------------------------------------------ fix
def named(cfg, t):
    """Expression with uids replaced by names: #{ANC 1st visit.Fixed}."""
    M = _meta(cfg)

    def de(m):
        parts = [p for p in m.group(1).split('.')]
        out = [M.name('de', parts[0])] + [('all' if p == '*' else M.name('coc', p)) for p in parts[1:]]
        return '#{' + '.'.join(out) + '}'
    t = re.sub(r'#\{([^}]*)\}', de, t or '')
    t = re.sub(r'C\{(\w+)\}', lambda m: 'C{%s}' % M.name('const', m[1]), t)
    t = re.sub(r'R\{(\w+)\.(\w+)\}', lambda m: 'R{%s.%s}' % (M.name('ds', m[1]), m[2]), t)
    return re.sub(r'OUG\{(\w+)\}', lambda m: 'OUG{%s}' % M.name('oug', m[1]), t)


def desc_input(cfg, r, old_desc):
    a, b = r['after'], r['before']
    return {'id': r['id'], 'code': r.get('code'), 'name': r['name'], 'indicatorType': type_text(cfg, a),
            'annualized': bool(a.get('annualized')), 'decimals': a.get('decimals'),
            'numerator': named(cfg, a.get('numerator')), 'denominator': named(cfg, a.get('denominator')),
            'oldNumerator': named(cfg, b.get('numerator')), 'oldDenominator': named(cfg, b.get('denominator')),
            'oldIndicatorType': type_text(cfg, b), 'oldDescription': old_desc or b.get('description'),
            'oldNumeratorDescription': b.get('numeratorDescription'), 'oldDenominatorDescription': b.get('denominatorDescription'),
            'changesMade': [named(cfg, c['note']) for c in r['changes']]}


def _dump(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def write_payload(cfg, items_after, aux_after, aux_changed_ids):
    """proposed-indicators.json, and proposed-dataElements.json with the changed data elements (removed otherwise)."""
    _dump(cfg.out(PROPOSED_FILE), {PAYLOAD_KEY: items_after})
    out = [{'file': PROPOSED_FILE, 'what': f'{len(items_after)} indicators',
            'import': 'POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL'}]
    path = cfg.out(AUX_FILE)
    changed = [d for d in (aux_after or []) if d['id'] in set(aux_changed_ids or [])]
    if changed:
        _dump(path, {'dataElements': changed})
        out.append({'file': AUX_FILE, 'what': f'{len(changed)} data elements changed',
                    'import': 'POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL'})
    elif os.path.exists(path):
        os.remove(path)  # stale from an earlier run
    return out


def aux_export(cfg):
    return {'kind': 'metadata', 'key': 'dataElements', 'file': 'dataElements', 'all': [],
            'endpoint': 'POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL', 'contentType': 'application/json',
            'why': 'data elements are separate metadata objects'}


# ------------------------------------------------------------------------------------------------ instance
def import_items(cfg, objs, strategy='UPDATE'):
    import d2
    return d2.req('POST', f'/api/metadata?importStrategy={strategy}&atomicMode=ALL', {PAYLOAD_KEY: objs})


AUX_FIELDS = ('aggregationType', 'zeroIsSignificant', 'valueType', 'domainType')


def import_aux(cfg, aux_all):
    """Apply the data element changes: PATCH only the fields that differ from the export (aggregationType ...), so
    the test copy keeps the settings import_data.py made there (zeroIsSignificant, no commentOptionSet). The
    production payload (proposed-dataElements.json) holds the complete objects."""
    import d2
    orig = {d['id']: d for d in aux_items(cfg)}
    res, n = [], 0
    for d in aux_all:
        o = orig.get(d['id'], {})
        ops = [{'op': 'replace', 'path': '/' + k, 'value': d.get(k)} for k in AUX_FIELDS if d.get(k) != o.get(k)]
        if ops:
            s, j = d2.req('PATCH', f"/api/dataElements/{d['id']}", ops, ctype='application/json-patch+json')
            res.append({'id': d['id'], 'http': s, 'ops': ops, 'error': None if s < 400 else str(j)[:300]})
            n += 1
    worst = max([r['http'] for r in res], default=200)
    return worst, {'status': 'OK' if worst < 400 else 'ERROR', 'patched': res, 'count': n}


def revert(cfg, item_ids, aux_ids):
    """Put the exported definitions back. Returns [(step name, data)] for the step log.
    Data elements are reverted on the fields fixes change (AUX_FIELDS but zeroIsSignificant) and keep the test copy's
    own settings (zeroIsSignificant, commentOptionSet as import_data.py set them)."""
    import d2
    from e2elib import import_stats, import_errors
    ids = set(item_ids)
    orig = [i for i in items(cfg) if i['id'] in ids]
    s, j = import_items(cfg, orig, 'CREATE_AND_UPDATE')
    out = [('revert indicators to export', {'http': s, 'status': j.get('status'), 'stats': import_stats(j), 'errors': import_errors(j)[:10]})]
    if aux_ids:
        res = []
        for d in [d for d in aux_items(cfg) if d['id'] in set(aux_ids)]:
            ops = [{'op': 'replace', 'path': '/' + k, 'value': d.get(k)} for k in AUX_FIELDS if k != 'zeroIsSignificant' and d.get(k) is not None]
            s2, _ = d2.req('PATCH', f"/api/dataElements/{d['id']}", ops, ctype='application/json-patch+json')
            res.append(s2)
        out.append(('revert data elements to export', {'http': max(res) if res else None, 'count': len(res)}))
    return out


def read_back(cfg, ids):
    import d2
    sel = 'id,code,name,numerator,denominator,indicatorType[id],annualized,decimals,description,numeratorDescription,denominatorDescription'
    G, ids = {}, list(ids)
    for i in range(0, len(ids), 100):
        _, got = d2.req('GET', f'/api/indicators?filter=id:in:[{",".join(ids[i:i + 100])}]&fields={sel}&paging=false')
        G.update({p['id']: p for p in got.get('indicators', [])})
    return G


def read_back_aux(cfg, ids):
    import d2
    G, ids = {}, list(ids)
    for i in range(0, len(ids), 100):
        _, got = d2.req('GET', f'/api/dataElements?filter=id:in:[{",".join(ids[i:i + 100])}]&fields=id,name,aggregationType,zeroIsSignificant,valueType,categoryCombo[id]&paging=false')
        G.update({p['id']: p for p in got.get('dataElements', [])})
    return G


def query(cfg, item_id, ou, pe, by=None):
    """Value of one indicator at ou/pe; by='pe' adds the months of pe as cells, by='ou' the org units below ou
    (cells keyed by period id / org unit name). Value None = no value (analytics returned no row)."""
    import d2
    import indexpr as X
    st, j = d2.analytics([('dimension', 'dx:' + item_id), ('filter', 'pe:' + pe), ('filter', 'ou:' + ou)])
    if st != 200:
        return {'value': 'error', 'error': (j.get('message') or j.get('_raw') or '')[:200], 'cells': {}, 'status': st}
    rows = j.get('rows') or []
    from query import value as fval
    rec = {'value': fval(rows[0][-1]) if rows else None, 'error': None, 'cells': {}, 'status': st}
    if by in ('pe', 'month', 'months'):
        st2, j2 = d2.analytics([('dimension', 'dx:' + item_id), ('dimension', 'pe:' + ';'.join(X.periods_covering('Monthly', pe))), ('filter', 'ou:' + ou)])
        if st2 == 200:
            h = [x['name'] for x in j2['headers']]
            rec['cells'] = {r[h.index('pe')]: fval(r[h.index('value')]) for r in j2.get('rows', [])}
        else:
            rec['cells'] = {'error': (j2.get('message') or '')[:200]}
    elif by in ('ou', 'children'):
        st2, j2 = d2.analytics([('dimension', 'dx:' + item_id), ('dimension', f'ou:{ou};CHILDREN'), ('filter', 'pe:' + pe)])
        if st2 == 200:
            h = [x['name'] for x in j2['headers']]
            nm = (j2.get('metaData') or {}).get('items') or {}
            rec['cells'] = {nm.get(r[h.index('ou')], {}).get('name', r[h.index('ou')]): fval(r[h.index('value')]) for r in j2.get('rows', [])}
        else:
            rec['cells'] = {'error': (j2.get('message') or '')[:200]}
    return rec


# ------------------------------------------------------------------------------------------------ e2e data
def _run_import(cfg, *args):
    import subprocess
    cmd = [sys.executable, os.path.join(HERE, 'import_data.py'), '--config', cfg.path, '--no-analytics', *args]
    print('$', ' '.join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout[-3000:])
    if r.returncode:
        sys.stderr.write(r.stderr[-3000:])
    return r


def e2e_load(cfg):
    """Scenario org units, witness data (datavalues-fix.json) and scenario data (data/scen-values.json)."""
    from e2elib import log_step
    files = [p for p in (cfg.out('datavalues-fix.json'), os.path.join(cfg.work_dir, 'data', 'scen-values.json')) if os.path.exists(p)]
    ous = os.path.join(cfg.work_dir, 'data', 'scen-ous.json')
    if not files:
        print('nothing to load (no datavalues-fix.json or data/scen-values.json)')
        return
    args = ['--file', *files]
    if os.path.exists(ous):
        args += ['--org-units-file', ous]
    r = _run_import(cfg, *args)
    log_step(cfg, 'load witness and scenario data', {'rc': r.returncode, 'files': [os.path.basename(f) for f in files], 'stdout': r.stdout[-1500:]})


def e2e_reload_scenarios(cfg):
    """Replace the data values in the scenario org units by data/scen-values.json."""
    from e2elib import log_step
    p = os.path.join(cfg.work_dir, 'data', 'scen-values.json')
    ous = os.path.join(cfg.work_dir, 'data', 'scen-ous.json')
    r = _run_import(cfg, '--file', p, '--org-units-file', ous, '--clean')
    log_step(cfg, 'reload scenario data', {'rc': r.returncode, 'stdout': r.stdout[-1500:]})


def earliest_year(cfg):
    import indexpr as X
    y0 = cfg.period_bounds()[0].year
    for f in ('data/scen-truth.json', 'truth-fix.json', 'truth.json'):
        p = os.path.join(cfg.work_dir, f)
        if os.path.exists(p):
            with open(p) as fh:
                for t in json.load(fh):
                    try:
                        y0 = min(y0, X.period_range(t['pe'])[0].year)
                    except X.Unsupported:
                        pass
    return y0
