"""Type adapter: program indicators (PIs) of one tracker or event program, and the program's category mappings
(program disaggregation) as the secondary ("aux") objects.

The core (run.py and the scripts outside types/) knows nothing about PIs: everything it needs comes from here.
Interface: references/data-formats.md, "Type adapters".

Config keys of this type (besides the core keys in lib/config.py):
    "program": "IpHINAT79UW",               # optional when the export holds one program
    "main_stage": null,                     # optional; default: the stage most referenced by PIs
    "field_resolution": {"#{stage.de}": 0.001},   # optional; smallest step of a numeric field
    "active_name_pattern": null             # optional regex: names meaning "status at period end" (lint)
The export is a dependency export of the program (GET /api/programs/<uid>/metadata.json), plus the data sets the PIs
report into if the dictionary should show the aggregate link.
"""
import copy, json, os, re, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, '..', '..', 'lib'))
if LIB not in sys.path:
    sys.path.insert(1, LIB)
import config  # noqa: E402
import common  # noqa: E402  (this folder's common.py: the type folder is first on sys.path)

TYPE = 'program-indicators'
ITEM_KIND = 'pi'
AUX_KIND = 'mapping'
AUX_REPLACES_ALL = True   # the mapping patch replaces the program's whole categoryMappings list
PREFIX = 'pi'
LABELS = {'item': 'program indicator', 'items': 'program indicators', 'short': 'PI', 'shorts': 'PIs',
          'aux': 'category mapping', 'auxs': 'category mappings', 'Item': 'Program indicator', 'Items': 'Program indicators',
          'aux_short': 'mapping', 'aux_shorts': 'mappings', 'auxField': 'Mapping filter', 'auxRow': 'Category option',
          'record': 'record', 'records': 'records', 'truthUnit': 'people', 'definition': 'filter',
          'fields': 'filter, expression, period boundaries, mapping list'}
PAYLOAD_KEY = 'programIndicators'
PROPOSED_FILE = 'proposed-programIndicators.json'
AUX_FILE = 'proposed-categoryMappings.patch.json'
TOPICS_FILE = os.path.join(HERE, 'topics.json')
DESCRIPTION_BRIEF = os.path.join(HERE, 'description_brief.md')
WITNESS_ARGS = ['--noise', '100', '--controls', '20']
MAINTENANCE_ROUTE = '/dhis-web-maintenance/index.html#/edit/indicatorSection/programIndicator/{}'
DX_TYPE = ('PROGRAM_INDICATOR', 'programIndicator')

# lint check -> status (None: decided in lint_status / not a status); other ERROR -> WRONG_VALUE, WARN -> REVIEW, INFO ignored
LINT_STATUS = {
    'de-not-in-stage': 'FAILS', 'unknown-de': 'FAILS', 'unknown-stage': 'FAILS', 'unknown-tea': 'FAILS',
    'event-var-in-enrollment-pi': 'FAILS', 'count-expression-wrong-aggregation': 'FAILS', 'unsupported-function': 'FAILS',
    'null-mapping-id': 'FAILS', 'combo-category-without-mapping': 'FAILS',
    'event-pi-cross-stage': 'ALWAYS_ZERO', 'mapping-stage-differs-from-pi': 'ALWAYS_ZERO',
    'boundary-offsets-differ': 'ALWAYS_ZERO', 'contradictory-equalities': 'ALWAYS_ZERO',
    'contradictory-range': 'ALWAYS_ZERO', 'impossible-period-window': 'ALWAYS_ZERO',
    'no-aggregation-type': 'WRONG_VALUE', 'literal-not-an-option-code': 'WRONG_VALUE',
    'mixed-and-or-without-parens': 'WRONG_VALUE', 'contains-substring-overmatch': 'WRONG_VALUE',
    'literal-whitespace': 'WRONG_VALUE', 'offset-without-period-type': 'REVIEW',
    'contains-literal-not-an-option-code': None, 'duplicate-definition': None, 'parse-error': None,
}
AUX_INFO_CHECKS = {'mapping-option-values-unmapped'}   # INFO findings on a mapping that still mark its users (assemble)
SYNTAX_CHECKS = {'parse-error', 'unsupported-function', 'unknown-de', 'de-not-in-stage'}  # explain a server rejection

BT = {'EVENT_DATE': 'Event date', 'ENROLLMENT_DATE': 'Enrollment date', 'INCIDENT_DATE': 'Incident date'}
BTYPE = {'AFTER_START_OF_REPORTING_PERIOD': 'after start of period', 'BEFORE_END_OF_REPORTING_PERIOD': 'before end of period',
         'AFTER_END_OF_REPORTING_PERIOD': 'after end of period', 'BEFORE_START_OF_REPORTING_PERIOD': 'before start of period'}


# ------------------------------------------------------------------------------------------------ config
class PIConfig(config.Config):
    """Config with the program-indicator keys and lookups (cfg.program, cfg.pis, cfg.main_stage...)."""
    MD_KEYS = ('dataElements', 'trackedEntityAttributes', 'programStages', 'programIndicators', 'optionSets',
               'options', 'categories', 'categoryOptions', 'categoryCombos', 'dataSets', 'programs')
    DEFAULT_TITLE = 'Program indicators'

    def __init__(self, path):
        super().__init__(path)
        r = self.raw
        self.field_resolution = r.get('field_resolution', {})
        self.active_name_pattern = r.get('active_name_pattern')  # regex for "status at period end" names; None = lint default
        self._program = r.get('program')
        self._main_stage = r.get('main_stage')

    @property
    def program(self):
        """The program object under review."""
        progs = self.md['programs']
        if self._program:
            for p in progs:
                if p['id'] == self._program:
                    p.setdefault('categoryMappings', [])
                    return p
            sys.exit(f'program {self._program} is not in {self.metadata_path}')
        if len(progs) != 1:
            sys.exit(f'{len(progs)} programs in the export: set "program" in {self.path}')
        progs[0].setdefault('categoryMappings', [])
        return progs[0]

    @property
    def program_id(self):
        return self.program['id']

    @property
    def tracked_entity_type(self):
        t = self.program.get('trackedEntityType')
        return t['id'] if t else None

    @property
    def pis(self):
        """Program indicators of the program under review."""
        pid = self.program_id
        return [p for p in self.md['programIndicators'] if (p.get('program') or {}).get('id', pid) == pid]

    @property
    def main_stage(self):
        if self._main_stage:
            return self._main_stage
        c = Counter()
        for p in self.pis:
            for t in (p.get('expression') or '', p.get('filter') or ''):
                c.update(re.findall(r'#\{(\w+)\.\w+\}', t))
        stages = {s['id'] for s in self.md['programStages']}
        for s, _ in c.most_common():
            if s in stages:
                return s
        return self.md['programStages'][0]['id'] if self.md['programStages'] else None

    def resolution(self, ref, value_type=None):
        """Smallest step of a numeric field: config, else from the value type."""
        if ref in self.field_resolution:
            return float(self.field_resolution[ref])
        return 1.0 if (value_type or '').startswith('INTEGER') else 0.001


CONFIG = PIConfig


def __getattr__(name):
    if name == 'patterns':                         # the fix-pattern module, imported lazily
        import patterns
        return patterns
    raise AttributeError(name)


# ------------------------------------------------------------------------------------------------ objects
def items(cfg):
    return cfg.pis


def aux_items(cfg):
    return cfg.program.get('categoryMappings') or []


def aux_users(cfg, aux_id, objs):
    return [p['id'] for p in objs if aux_id in (p.get('categoryMappingIds') or [])]


def apply_overlay(cfg, ov, path):
    common.apply_overlay(cfg, ov, path)


def scope(cfg):
    return {'id': cfg.program_id, 'name': cfg.program.get('name', cfg.program_id)}


def scope_text(cfg, n_items=None, n_aux=None):
    n = len(items(cfg)) if n_items is None else n_items
    m = len(aux_items(cfg)) if n_aux is None else n_aux
    return f"{n} program indicators and {m} category mappings of program `{cfg.program_id}` ({cfg.program.get('name', '')})"


def names(cfg, form_names=False):
    """uid -> name of the fields, stages and mappings an expression or a diff may show."""
    md, out = cfg.md, {}
    for de in md['dataElements']:
        out[de['id']] = (de.get('formName') or de['name']) if form_names else de.get('name', de['id'])
    for k in ('trackedEntityAttributes', 'programStages'):
        out.update({x['id']: x.get('name', x['id']) for x in md[k]})
    if not form_names:
        for m in aux_items(cfg):
            out[m['id']] = m.get('mappingName', m['id'])
    return out


def clean(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def boundaries_text(p):
    return [f"{BT.get(b.get('boundaryTarget'), b.get('boundaryTarget'))} {BTYPE.get(b.get('analyticsPeriodBoundaryType'), b.get('analyticsPeriodBoundaryType'))}"
            + (f" (offset {b['offsetPeriods']} {b.get('offsetPeriodType') or 'no period type'})" if b.get('offsetPeriods') else '')
            for b in p.get('analyticsPeriodBoundaries') or []]


def describe(cfg, p):
    fields = [{'key': 'filter', 'label': 'Filter', 'text': clean(p.get('filter'))[:1400], 'code': True},
              {'key': 'expression', 'label': 'Expression', 'text': clean(p.get('expression')), 'code': True}]
    b = boundaries_text(p)
    if b:
        fields.append({'key': 'boundaries', 'label': 'Period boundaries', 'text': '; '.join(sorted(b)), 'code': False})
    return {'id': p['id'], 'code': p.get('code') or '', 'name': p.get('name') or p['id'],
            'type': 'Enrollment' if p.get('analyticsType') == 'ENROLLMENT' else 'Event',
            'agg': p.get('aggregationType'), 'fields': fields}


def diff_fields(p, cfg=None):
    """Canonical text per changeable field (approval page diff and read-back comparison)."""
    nm = {m['id']: m.get('mappingName', m['id']) for m in aux_items(cfg)} if cfg else {}
    return {'Filter': p.get('filter') or '', 'Expression': p.get('expression') or '',
            'Analytics type': p.get('analyticsType') or '', 'Aggregation type': p.get('aggregationType') or '(none)',
            'Period boundaries': '\n'.join(sorted(boundaries_text(p))),
            'Category mappings used': '\n'.join(sorted((nm.get(x, x) if x else 'null (empty entry)') for x in p.get('categoryMappingIds') or [])),
            'Decimals': '' if p.get('decimals') is None else str(p.get('decimals')),
            'description': p.get('description') or ''}


def aux_describe(cfg, m):
    co = {o['id']: o.get('name', o['id']) for o in cfg.md['categoryOptions']}
    cat = {c['id']: c.get('name', c['id']) for c in cfg.md['categories']}
    return {'id': m['id'], 'name': m.get('mappingName') or m['id'], 'type': cat.get(m.get('categoryId'), ''),
            'fields': [{'key': o.get('optionId'), 'label': co.get(o.get('optionId'), o.get('optionId')),
                        'text': clean(o.get('filter')) or '(no filter)', 'code': True} for o in m.get('optionMappings', [])]}


def aux_diff_fields(m, cfg=None):
    co = {o['id']: o.get('name', o['id']) for o in cfg.md['categoryOptions']} if cfg else {}
    out = {'Category': m.get('categoryId') or '', 'Mapping name': m.get('mappingName') or ''}
    for o in sorted(m.get('optionMappings', []), key=lambda o: o.get('optionId') or ''):
        out[co.get(o.get('optionId'), o.get('optionId'))] = (o.get('filter') or '').strip()
    return out


def truth_text(truth):
    return f"{len(truth)} generated people, {sum(len(t.get('events', [])) for t in truth)} events"


# ------------------------------------------------------------------------------------------------ validate
def lint_status(f, p):
    chk = f['check']
    if chk == 'contains-literal-not-an-option-code':
        return 'ALWAYS_ZERO' if f['severity'] == 'ERROR' and '||' not in (p.get('filter') or '') else 'WRONG_VALUE'
    if chk == 'boundaries-missing' and f['severity'] == 'WARN':
        return 'REVIEW'
    if chk in LINT_STATUS:
        return LINT_STATUS[chk]
    return {'ERROR': 'WRONG_VALUE', 'WARN': 'REVIEW'}.get(f['severity'])


def status_rows(cfg, P, data):
    """Engine-check rows specific to program disaggregation (C1, C2)."""
    rows = []
    for pid, x in data['compare'].items():
        if x.get('status') != 'compared' or pid not in P:
            continue
        if x['type'] == 'EVENT' and x['expected_total_ignoring_disagg'] > x['analytics_total']:
            rows.append((pid, 'REVIEW', 'test:total-loses-undisaggregatable-records',
                         f"test data: {x['expected_total_ignoring_disagg']:g} records pass the filter, analytics total is {x['analytics_total']:g} (records outside the mapping cells are dropped, C1)"))
        if x['type'] == 'ENROLLMENT' and x.get('cells_ok') and x.get('cells_sum_equals_total') is False:
            rows.append((pid, 'WRONG_VALUE', 'test:cells-do-not-add-up', f"test data: total {x['analytics_total']:g}, cells sum to {x.get('sum_cells_analytics', 0):g} (C2)"))
    return rows


def texts_to_validate(p):
    return [(k, k, p[k]) for k in ('expression', 'filter') if p.get(k)]


def validate_text(kind, text):
    return common.validate_text(kind, text)


def aux_texts_to_validate(cfg, before, after, all_options=False):
    """Changed option filters of a mapping (every option with all_options): [(label, kind, text)]."""
    co = {c['id']: c.get('name') for c in cfg.md['categoryOptions']}
    prev = {om['optionId']: om.get('filter') or '' for om in (before or {}).get('optionMappings', [])}
    out = []
    for om in after.get('optionMappings', []):
        txt = om.get('filter') or ''
        if all_options or txt != prev.get(om['optionId']):
            out.append((f"{after.get('mappingName') or after['id']} / {co.get(om['optionId'], om['optionId'])}", 'filter', txt))
    return out


# ------------------------------------------------------------------------------------------------ fix
def desc_input(cfg, r, old_desc):
    """One entry of a description batch (fix/prep_desc.py): the corrected definition in words."""
    M = __getattr__('patterns').make_meta(cfg)

    def named(t):
        t = re.sub(r'#\{(\w+)\.(\w+)\}', lambda m: '#{%s.%s}' % (M.stage.get(m[1], {}).get('name', m[1]),
                                                                 (M.de.get(m[2]) or {}).get('formName') or (M.de.get(m[2]) or {}).get('name', m[2])), t or '')
        t = re.sub(r'A\{(\w+)\}', lambda m: 'A{%s}' % (M.tea.get(m[1]) or {}).get('name', m[1]), t)
        # stage UIDs compared with V{program_stage_id}, quoted or not
        return re.sub(r"(['\"]?)\b(\w{11})\b\1", lambda m: f"'{M.stage[m[2]]['name']}'" if m[2] in M.stage else m[0], t)

    def bnd(p):
        out = []
        for b in p.get('analyticsPeriodBoundaries') or []:
            s = f"{b['boundaryTarget']} {b['analyticsPeriodBoundaryType']}"
            if b.get('offsetPeriods'):
                s += f" offset {b['offsetPeriods']} {b.get('offsetPeriodType') or '(no period type: offset ignored)'}"
            out.append(s)
        return out

    def cats(p):
        return [M.cat[c]['name'] for c in sorted(M.pi_categories(p)) if c in M.cat]

    def stage(p):
        s = (p.get('programStage') or {}).get('id')
        return M.stage.get(s, {}).get('name') if s else None

    a_, b = r['after'], r['before']
    return {'id': r['id'], 'code': r.get('code'), 'name': r['name'], 'program': cfg.program.get('name'),
            'analyticsType': a_.get('analyticsType'), 'aggregationType': a_.get('aggregationType'), 'stage': stage(a_),
            'expression': named(a_.get('expression')), 'filter': named(a_.get('filter')), 'boundaries': bnd(a_),
            'disaggregatedBy': cats(a_),
            'oldExpression': named(b.get('expression')), 'oldFilter': named(b.get('filter')), 'oldBoundaries': bnd(b),
            'oldDescription': old_desc or b.get('description'),
            'changesMade': [named(c['note']) for c in r['changes']]}


def _dump(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def write_payload(cfg, items_after, aux_after, aux_changed_ids):
    """proposed-programIndicators.json, and the categoryMappings patch when a mapping changed (removed otherwise)."""
    _dump(cfg.out(PROPOSED_FILE), {PAYLOAD_KEY: items_after})
    out = [{'file': PROPOSED_FILE, 'what': f'{len(items_after)} program indicators',
            'import': 'POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL'}]
    ppath = cfg.out(AUX_FILE)
    if aux_changed_ids:
        _dump(ppath, [{'op': 'replace', 'path': '/categoryMappings', 'value': aux_after}])
        out.append({'file': AUX_FILE, 'what': f'{len(aux_changed_ids)} of {len(aux_after)} category mappings changed',
                    'import': f'PATCH /api/programs/{cfg.program_id} (Content-Type: application/json-patch+json)'})
    elif os.path.exists(ppath):
        os.remove(ppath)  # stale from an earlier run
    return out


def aux_export(cfg):
    return {'kind': 'jsonpatch', 'path': '/categoryMappings', 'all': aux_items(cfg), 'file': 'categoryMappings',
            'endpoint': f'PATCH /api/programs/{cfg.program_id}', 'contentType': 'application/json-patch+json',
            'why': 'category mappings live on the program'}


# ------------------------------------------------------------------------------------------------ instance
def import_items(cfg, objs, strategy='UPDATE'):
    import d2
    return d2.req('POST', f'/api/metadata?importStrategy={strategy}&atomicMode=ALL', {PAYLOAD_KEY: objs})


def _patch_program(cfg, ops):
    import d2
    return d2.req('PATCH', f'/api/programs/{cfg.program_id}', ops, ctype='application/json-patch+json')


def import_aux(cfg, aux_all):
    """Replace the program's category mappings (every mapping, fixed ones changed)."""
    return _patch_program(cfg, [{'op': 'replace', 'path': '/categoryMappings', 'value': aux_all}])


def revert(cfg, item_ids, aux_ids):
    """Put the exported definitions back. Returns [(step name, data)] for the step log."""
    from e2elib import import_stats, import_errors
    ids = set(item_ids)
    orig = [p for p in items(cfg) if p['id'] in ids]
    s, j = import_items(cfg, orig, 'CREATE_AND_UPDATE')
    out = [('revert programIndicators to export', {'http': s, 'status': j.get('status'), 'stats': import_stats(j), 'errors': import_errors(j)[:10]})]
    if aux_ids or os.path.exists(cfg.out(AUX_FILE)):
        s2, _ = import_aux(cfg, aux_items(cfg))
        out.append(('revert categoryMappings to export', {'http': s2}))
    return out


def read_back(cfg, ids):
    import d2
    sel = ('id,code,expression,filter,analyticsType,aggregationType,decimals,description,categoryMappingIds,'
           'analyticsPeriodBoundaries[boundaryTarget,analyticsPeriodBoundaryType,offsetPeriods,offsetPeriodType]')
    G = {}
    ids = list(ids)
    for i in range(0, len(ids), 100):
        _, got = d2.req('GET', f'/api/programIndicators?filter=id:in:[{",".join(ids[i:i + 100])}]&fields={sel}&paging=false')
        G.update({p['id']: p for p in got.get('programIndicators', [])})
    return G


def read_back_aux(cfg, ids):
    """Every mapping of the program on the instance (the patch replaces the whole list)."""
    import d2
    _, prog = d2.req('GET', f'/api/programs/{cfg.program_id}?fields=categoryMappings')
    return {m['id']: m for m in prog.get('categoryMappings', [])}


def query(cfg, item_id, ou, pe, by=None):
    """Value of one PI at ou/pe; with `by` (a category uid or name) also the cells keyed by option name."""
    import d2
    st, j = d2.analytics([('dimension', 'dx:' + item_id), ('filter', 'pe:' + pe), ('filter', 'ou:' + ou)])
    if st != 200:
        return {'value': 'error', 'error': (j.get('message') or j.get('_raw') or '')[:200], 'cells': {}, 'status': st}
    rec = {'value': float(j['rows'][0][-1]) if j.get('rows') else 0.0, 'error': None, 'cells': {}, 'status': st}
    if by:
        cid = common.resolve_category(cfg.md, by)
        if not cid:
            rec['cells'] = {'error': 'unknown category'}
            return rec
        co = {c['id']: c['name'] for c in cfg.md['categoryOptions']}
        st2, j2 = d2.analytics([('dimension', 'dx:' + item_id), ('dimension', cid), ('filter', 'pe:' + pe), ('filter', 'ou:' + ou)])
        if st2 == 200:
            h = [x['name'] for x in j2['headers']]
            rec['cells'] = {co.get(r[h.index(cid)], r[h.index(cid)]): float(r[h.index('value')]) for r in j2.get('rows', [])}
        else:
            rec['cells'] = {'error': (j2.get('message') or '')[:200]}
    return rec


# ------------------------------------------------------------------------------------------------ e2e data
def _scenario_ous(cfg):
    p = os.path.join(cfg.work_dir, 'data', 'scen-ous.json')
    if not os.path.exists(p):
        return {'organisationUnits': []}
    with open(p) as f:
        return json.load(f)


def _ensure_scenario_ous(cfg):
    import d2
    from e2elib import log_step, import_stats
    ous = _scenario_ous(cfg)
    if not ous['organisationUnits']:
        return []
    s, j = d2.req('POST', '/api/metadata?importStrategy=CREATE_AND_UPDATE', ous)
    log_step(cfg, 'scenario org units', {'http': s, 'status': j.get('status'), 'stats': import_stats(j)})
    _, p = d2.req('GET', f'/api/programs/{cfg.program_id}?fields=organisationUnits[id]', log=False)
    have = {o['id'] for o in p.get('organisationUnits', [])}
    new = [o['id'] for o in ous['organisationUnits'] if o['id'] not in have]
    if new:
        # replacing the whole list is what 2.43 accepts reliably; keep every org unit already assigned
        s, _ = _patch_program(cfg, [{'op': 'replace', 'path': '/organisationUnits',
                                     'value': p.get('organisationUnits', []) + [{'id': i} for i in new]}])
        log_step(cfg, 'assign program to scenario org units', {'http': s, 'added': len(new)})
    return [o['id'] for o in ous['organisationUnits']]


def _tracker_errors(j):
    return [e.get('message') for e in (j.get('validationReport') or {}).get('errorReports', [])][:5]


def _import_tracker(cfg, path, label):
    import d2
    from e2elib import log_step
    if not os.path.exists(path):
        print('skip (missing)', path)
        return
    with open(path) as f:
        body = json.load(f)
    s, j = d2.req('POST', '/api/tracker?async=false&validationMode=SKIP&importStrategy=CREATE', body)
    log_step(cfg, 'tracker import ' + label, {'http': s, 'status': j.get('status'), 'stats': j.get('stats'), 'errors': _tracker_errors(j)})
    print('tracker import', label, s, j.get('status'), j.get('stats'))


def e2e_load(cfg):
    """Scenario org units (+ program assignment), witness data (tracker-fix.json) and scenario data."""
    _ensure_scenario_ous(cfg)
    _import_tracker(cfg, os.path.join(cfg.work_dir, 'tracker-fix.json'), 'witness data')
    _import_tracker(cfg, os.path.join(cfg.work_dir, 'data', 'scen-tracker.json'), 'scenario data')


def e2e_reload_scenarios(cfg):
    """Delete the tracked entities (or events) in the scenario org units and import the scenario data again."""
    import d2
    from e2elib import log_step
    ous = _ensure_scenario_ous(cfg)
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
    with open(os.path.join(cfg.work_dir, 'data', 'scen-tracker.json')) as f:
        data = json.load(f)
    s, j = d2.req('POST', '/api/tracker?async=false&validationMode=SKIP&importStrategy=CREATE', data)
    log_step(cfg, 'reload scenario data', {'deleted': gone, 'http': s, 'status': j.get('status'), 'stats': j.get('stats'), 'errors': _tracker_errors(j)})
    print('deleted', gone, 'imported', s, j.get('status'), j.get('stats'))


def earliest_year(cfg):
    """Earliest year in the scenario / witness / validation data (analytics lastYears)."""
    y0 = cfg.period_bounds()[0].year
    for f in ('data/scen-truth.json', 'truth-fix.json', 'truth.json'):
        p = os.path.join(cfg.work_dir, f)
        if os.path.exists(p):
            with open(p) as fh:
                for t in json.load(fh):
                    for d in [t.get('enrolledAt')] + [e['date'] for e in t.get('events', [])]:
                        if d:
                            y0 = min(y0, int(d[:4]))
    return y0
