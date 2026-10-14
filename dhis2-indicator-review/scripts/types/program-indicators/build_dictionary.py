"""Build the PI dictionary data: a plain-language rendering of every program indicator, linked to the
aggregate data element with the same code and the data sets it is in.

For each PI: what it counts (headline), the period rule, the filter as a list of conditions in words,
the disaggregation, and - when the export has aggregate data elements and data sets - the link
PI code -> aggregate data element code (exact match, then case-insensitive) -> data sets, with the
breakdown compared against the data element's *effective* category combo (a data set's override wins)
and the data set's attribute combo. Without data sets the link columns are simply empty.

Inputs:  metadata (config); work_dir/pi-status.csv (optional); work_dir/review.json (optional, topic titles per PI)
Outputs: work_dir/dictionary.json; with --desc-batches N also work_dir/dict-desc/in/batchK.json, the input
         for sub-agents that write one plain-language description per PI (output expected in
         work_dir/dict-desc/out/batchK.json as {uid: {"description": "...", "nameMismatch": null | "..."}})
Config:  metadata, program, work_dir, exported, dhis2_version, title
Next:    assemble_dict.py -> pi-dictionary.html
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import config, piexpr
import collections as C, csv, json, re

ap = config.arg_parser(__doc__.split('\n')[0])
ap.add_argument('--desc-batches', type=int, default=0, metavar='N', help='also write N input batches for description sub-agents')
args = ap.parse_args()
cfg = config.load(args)
d = cfg.md
PIS = cfg.pis
DE = {x['id']: x for x in d['dataElements']}
TEA = {x['id']: x for x in d['trackedEntityAttributes']}
STG = {s['id']: s['name'] for s in d['programStages']}
CC = {c['id']: c for c in d['categoryCombos']}
CAT = {c['id']: c for c in d['categories']}
COPT = {o['id']: o['name'] for o in d['categoryOptions']}
MAP = {m['id']: m for m in cfg.program.get('categoryMappings', [])}
OPT = {o['id']: o for o in d['options']}
DSETS = d.get('dataSets') or []
DS = {x['id']: x for x in DSETS}

status = {}
if os.path.exists(cfg.out('pi-status.csv')):
    with open(cfg.out('pi-status.csv'), newline='', encoding='utf-8-sig') as f:
        status = {r['id']: r for r in csv.DictReader(f)}
topics_by_pi = C.defaultdict(list)
if os.path.exists(cfg.out('review.json')):
    with open(cfg.out('review.json')) as f:
        for t in json.load(f)['topics']:
            if t['kind'] == 'pi':
                for i in t['items']:
                    topics_by_pi[i['id']].append(t['title'])

# aggregate data elements by code (exact, then case-insensitive) and the data sets they are in
AGG = [x for x in d['dataElements'] if x.get('code') and x.get('domainType', 'AGGREGATE') == 'AGGREGATE']
agg_exact = {x['code']: x for x in AGG}
agg_ci = {}
for x in AGG:
    agg_ci.setdefault(x['code'].upper(), x)
de_ds = C.defaultdict(list)
for ds in DSETS:
    for e in ds.get('dataSetElements', []):
        de_ds[e['dataElement']['id']].append(ds['id'])
code2name = {}
for os_ in d['optionSets']:
    for o in os_.get('options', []):
        if o['id'] in OPT:
            code2name[(os_['id'], OPT[o['id']].get('code'))] = OPT[o['id']]['name']


# ---------------- plain-language renderer (over piexpr's AST) ----------------
def fname(node):
    if node[0] == 'de':
        de = DE.get(node[2])
        return (de.get('formName') or de['name']).strip() if de else node[2]
    if node[0] == 'tea':
        t = TEA.get(node[1])
        return t['name'].strip() if t else node[1]
    return None


VARS = {'event_date': 'the event date', 'enrollment_date': 'the enrollment date', 'incident_date': 'the incident date',
        'analytics_period_start': 'the start of the reporting period', 'analytics_period_end': 'the end of the reporting period',
        'current_date': 'today', 'event_count': 'the number of events', 'tei_count': 'the number of persons',
        'enrollment_count': 'the number of enrollments', 'due_date': 'the due date', 'value_count': 'the number of values',
        'zero_pos_value_count': 'the number of zero or positive values', 'creation_date': 'the creation date',
        'scheduled_date': 'the scheduled date', 'program_stage_name': 'the stage name', 'org_unit_count': 'the number of org units'}


def opt_label(node, code):
    meta = DE.get(node[2]) if node[0] == 'de' else TEA.get(node[1]) if node[0] == 'tea' else None
    if meta and meta.get('optionSet'):
        nm = code2name.get((meta['optionSet']['id'], code))
        if nm and nm != code:
            return f'“{nm}”'
    return f'“{code}”'


def val(n, ctx_field=None):
    t = n[0]
    if t in ('de', 'tea'):
        return f'‹{fname(n)}›'
    if t == 'var':
        return VARS.get(n[1], n[1])
    if t == 'num':
        v = n[1]
        return str(int(v)) if v == int(v) else f'{v:g}'
    if t == 'str':
        return opt_label(ctx_field, n[1]) if ctx_field is not None else f'“{n[1]}”'
    if t == 'bool':
        return 'true' if n[1] else 'false'
    if t == 'neg':
        return '−' + val(n[1])
    if t == 'fn':
        name, a = n[1], n[2]
        if name in ('d2:daysBetween', 'd2:weeksBetween', 'd2:monthsBetween', 'd2:yearsBetween', 'd2:minutesBetween') and len(a) == 2:
            return f'the number of {name[3:-7].lower()} from {val(a[0])} to {val(a[1])}'
        if name == 'd2:addDays' and len(a) == 2:
            return f'{val(a[0])} plus {val(a[1])} days'
        return f'{name}({", ".join(val(x) for x in a)})'
    if t == 'bin' and n[1] in '+-*/%^':
        return f'({val(n[2])} {n[1]} {val(n[3])})'
    return '…'


CMP_WORD = {'==': 'is', '!=': 'is not', '<': 'is less than', '<=': 'is at most', '>': 'is more than', '>=': 'is at least'}
CMP_DATE = {'<': 'is before', '<=': 'is on or before', '>': 'is after', '>=': 'is on or after', '==': 'is', '!=': 'is not'}
DATE_VARS = {'event_date', 'enrollment_date', 'incident_date', 'analytics_period_start', 'analytics_period_end',
             'current_date', 'due_date', 'creation_date', 'scheduled_date'}


def is_date(n):
    if n[0] == 'var':
        return n[1] in DATE_VARS
    if n[0] in ('de', 'tea'):
        m = DE.get(n[2]) if n[0] == 'de' else TEA.get(n[1])
        return bool(m) and m.get('valueType') in ('DATE', 'DATETIME', 'AGE')
    return n[0] == 'fn' and n[1] == 'd2:addDays'


def flip(op):
    return {'<': '>', '>': '<', '<=': '>=', '>=': '<='}.get(op, op)


def leaf(n):
    """one condition in words"""
    t = n[0]
    if t == 'not':
        inner = n[1]
        if inner[0] == 'fn' and inner[1] == 'd2:hasValue':
            return f'{val(inner[2][0])} is blank'
        return f'not ({cond(inner)})'
    if t == 'fn':
        name, a = n[1], n[2]
        if name == 'd2:hasValue' and a:
            return f'{val(a[0])} is filled in'
        if name == 'contains' and a:
            return f'{val(a[0])} contains the text ' + ' and '.join(f'“{x[1]}”' if x[0] == 'str' else val(x) for x in a[1:])
        if name == 'containsItems' and a:
            return f'{val(a[0])} includes ' + ' and '.join(opt_label(a[0], x[1]) if x[0] == 'str' else val(x) for x in a[1:])
        return val(n)
    if t == 'bin' and n[1] in CMP_WORD:
        a, b, op = n[2], n[3], n[1]
        if a[0] in ('num', 'str', 'var') and b[0] in ('de', 'tea', 'fn'):
            a, b, op = b, a, flip(op)
        words = CMP_DATE if (is_date(a) or is_date(b)) else CMP_WORD
        field = a if a[0] in ('de', 'tea') else None
        return f'{val(a)} {words[op]} {val(b, field)}'
    if t in ('de', 'tea'):
        return f'{val(n)} is yes'
    return val(n)


def conj(n):
    return conj(n[2]) + conj(n[3]) if n[0] == 'bin' and n[1] == '&&' else [n]


def disj(n):
    return disj(n[2]) + disj(n[3]) if n[0] == 'bin' and n[1] == '||' else [n]


def collapse_or(parts):
    """X == a || X == b  ->  X is one of a, b"""
    eqs = [p for p in parts if p[0] == 'bin' and p[1] == '==' and p[2][0] in ('de', 'tea') and p[3][0] == 'str']
    if len(eqs) == len(parts) and len({repr(p[2]) for p in eqs}) == 1:
        f = eqs[0][2]
        return f'{val(f)} is one of ' + ', '.join(opt_label(f, p[3][1]) for p in eqs)
    cont = [p for p in parts if p[0] == 'fn' and p[1] in ('contains', 'containsItems') and len(p[2]) == 2 and p[2][1][0] == 'str']
    if len(cont) == len(parts) and len({repr(p[2][0]) for p in cont}) == 1:
        f = cont[0][2][0]
        return f'{val(f)} contains the text ' + ' or '.join(f'“{p[2][1][1]}”' for p in cont)
    return None


def cond(n):
    if n[0] == 'bin' and n[1] == '||':
        parts = disj(n)
        return collapse_or(parts) or 'either ' + ' — or — '.join(cond(p) for p in parts)
    if n[0] == 'bin' and n[1] == '&&':
        return ' and '.join(cond(p) for p in conj(n))
    return leaf(n)


def range_merge(items):
    """'X is at least a' + 'X is less than b' on the same field -> one line"""
    out, used = [], set()
    for i, a in enumerate(items):
        if i in used:
            continue
        if a[0] == 'bin' and a[1] in ('>=', '>') and a[2][0] in ('de', 'tea') and a[3][0] == 'num':
            for j, b in enumerate(items):
                if j > i and j not in used and b[0] == 'bin' and b[1] in ('<', '<=') and repr(b[2]) == repr(a[2]) and b[3][0] == 'num':
                    used.add(j)
                    out.append(f'{val(a[2])} is {"at least" if a[1] == ">=" else "more than"} {val(a[3])} and {"below" if b[1] == "<" else "at most"} {val(b[3])}')
                    break
            else:
                out.append(cond(a))
        else:
            out.append(cond(a))
    return out


def conditions(text):
    if not text or not text.strip():
        return [], None
    try:
        return range_merge(conj(piexpr.parse(text))), None
    except Exception as e:  # unparsable filters are shown raw, with the reason
        return [], f'The filter could not be parsed ({e}).'


def stages_in(text):
    return sorted({s for s, _ in re.findall(r'#\{(\w+)\.(\w+)\}', text or '')})


def headline(p, stages):
    t, agg, ex = p.get('analyticsType'), p.get('aggregationType'), (p.get('expression') or '').strip()
    stg = ' or '.join(STG.get(s, s) for s in stages) if stages else None
    if ex == 'V{tei_count}':
        return (f'Number of persons with at least one matching {stg} event' if t == 'EVENT' and stg
                else 'Number of persons with a matching enrollment' if t == 'ENROLLMENT' else 'Number of persons')
    if ex in ('V{event_count}', '1') and t == 'EVENT':
        return f'Number of {stg} events' if stg else 'Number of events (any stage)'
    if ex in ('V{enrollment_count}', '1', 'V{event_count}') and t == 'ENROLLMENT':
        return 'Number of enrollments'
    try:
        e = val(piexpr.parse(ex))
    except Exception:
        e = ex
    verb = {'SUM': 'Total of', 'MAX': 'Highest value of', 'MIN': 'Lowest value of', 'AVERAGE': 'Average of',
            'COUNT': 'Number of records with'}.get(agg, 'Value of')
    return f'{verb} {e}' + (f' across matching {stg} events' if t == 'EVENT' and stg else ' across matching enrollments' if t == 'ENROLLMENT' else '')


PERIOD_UNIT = {'Daily': 'day', 'Weekly': 'week', 'Monthly': 'month', 'BiMonthly': 'two-month period', 'Quarterly': 'quarter',
               'SixMonthly': 'six-month period', 'Yearly': 'year', 'FinancialApril': 'financial year', 'FinancialJuly': 'financial year',
               'FinancialOct': 'financial year'}


def period_text(p):
    t = p.get('analyticsType')
    bs = p.get('analyticsPeriodBoundaries') or []
    target = {'EVENT_DATE': 'event date', 'ENROLLMENT_DATE': 'enrollment date', 'INCIDENT_DATE': 'incident date'}
    tg = {target.get(b['boundaryTarget'], b['boundaryTarget'].lower()) for b in bs}
    offs = {(b.get('offsetPeriods') or 0, b.get('offsetPeriodType')) for b in bs}
    where = ' / '.join(sorted(tg)) or ('event date' if t == 'EVENT' else 'enrollment date')
    if len(bs) < 2:
        kinds = ', '.join(b['analyticsPeriodBoundaryType'].lower().replace('_', ' ') for b in bs) or 'none'
        lead = f'Only one period boundary ({kinds}) on the {where}' if bs else 'No period boundaries'
        return lead + '; the window is open on the other side.'
    if not offs or offs == {(0, None)} or all(k == 0 for k, _ in offs):
        return f'{where[0].upper()}{where[1:]} falls in the reporting period.'
    if len(offs) == 1:
        k, ty = next(iter(offs))
        if not ty:
            return f'{where[0].upper()}{where[1:]} falls in the reporting period (an offset of {k} is set without a period type, so DHIS2 ignores it).'
        unit = PERIOD_UNIT.get(ty, ty.lower())
        return f"{where.capitalize()} falls in the period shifted {abs(k)} {unit}{'s' if abs(k) != 1 else ''} {'back' if k < 0 else 'forward'} from the reporting period (cohort)."
    return f"Boundary offsets differ ({', '.join(f'{k} {ty}' for k, ty in sorted(offs, key=str))}); the period window is irregular."


def disagg(p):
    out = []
    for key in ('categoryCombo', 'attributeCombo'):
        cc = CC.get((p.get(key) or {}).get('id'))
        if not cc or cc['name'] == 'default':
            continue
        for c in cc['categories']:
            cat = CAT.get(c['id'])
            if not cat or cat['name'] == 'default':
                continue
            m = next((MAP[x] for x in p.get('categoryMappingIds') or [] if x and x in MAP and MAP[x]['categoryId'] == c['id']), None)
            out.append({'category': cat['name'], 'mapping': m.get('mappingName') if m else None,
                        'options': [{'name': COPT.get(o['optionId'], o['optionId']), 'filter': re.sub(r'\s+', ' ', o.get('filter') or '').strip()}
                                    for o in m.get('optionMappings', [])] if m else []})
    return out


def cats_of(ccid):
    cc = CC.get(ccid)
    return [c['id'] for c in cc['categories'] if CAT.get(c['id'], {}).get('name') != 'default'] if cc else []


def cc_name(ccid):
    return CC[ccid]['name'] if ccid in CC else ccid


# ---------------- rows ----------------
used_cats = set()
rows = []
for p in PIS:
    code = p.get('code') or ''
    agg_de = agg_exact.get(code) if code else None
    match = 'exact' if agg_de else None
    if code and not agg_de and code.upper() in agg_ci:
        agg_de, match = agg_ci[code.upper()], 'case-insensitive'
    stages = stages_in((p.get('filter') or '') + ' ' + (p.get('expression') or ''))
    conds, perr = conditions(p.get('filter'))
    pcc, pac = cats_of(p['categoryCombo']['id']), cats_of((p.get('attributeCombo') or {}).get('id'))
    targets = []
    if agg_de:
        de_cc = (agg_de.get('categoryCombo') or {}).get('id')
        for ds in DSETS:
            for e in ds.get('dataSetElements', []):
                if e['dataElement']['id'] != agg_de['id']:
                    continue
                ecc = (e.get('categoryCombo') or {}).get('id') or de_cc  # data-set override wins
                dac = (ds.get('categoryCombo') or {}).get('id')
                hcc, hac = cats_of(ecc), cats_of(dac)
                targets.append({'ds': ds['id'], 'dsName': ds['name'], 'deCombo': cc_name(de_cc), 'effCombo': cc_name(ecc),
                                'overridden': bool(e.get('categoryCombo')) and ecc != de_cc, 'dsAttr': cc_name(dac),
                                'piCats': pcc, 'hmisCats': hcc, 'piAttr': pac, 'hmisAttr': hac,
                                'ccMatch': set(pcc) == set(hcc), 'acMatch': set(pac) == set(hac)})
                used_cats.update(pcc + hcc + pac + hac)
    dss = list(dict.fromkeys(t['ds'] for t in targets))
    rows.append({
        'id': p['id'], 'code': code, 'name': p['name'], 'label': p['name'][len(code):].lstrip(' -:–') if code and p['name'].startswith(code) and p['name'] != code else p['name'],
        'datasets': [{'id': x, 'name': DS[x]['name'], 'period': DS[x].get('periodType')} for x in dss],
        'type': p.get('analyticsType'), 'agg': p.get('aggregationType'),
        'headline': headline(p, stages), 'period': period_text(p), 'conditions': conds, 'parseError': perr,
        'disagg': disagg(p), 'boundaryTargets': sorted({b['boundaryTarget'] for b in p.get('analyticsPeriodBoundaries') or []}),
        'expr': re.sub(r'\s+', ' ', p.get('expression') or '').strip(), 'filter': re.sub(r'\s+', ' ', p.get('filter') or '').strip(),
        'description': p.get('description'), 'nameMismatch': None,
        'target': {'id': agg_de['id'], 'code': agg_de['code'], 'name': agg_de['name'],
                   'combo': cc_name((agg_de.get('categoryCombo') or {}).get('id')), 'match': match} if agg_de else None,
        'piCombo': cc_name(p['categoryCombo']['id']), 'attrCombo': cc_name((p.get('attributeCombo') or {}).get('id') or 'default'),
        'comboMatch': all(t['ccMatch'] for t in targets) if targets else None,
        'attrMatch': all(t['acMatch'] for t in targets) if targets else None, 'targets': targets,
        'status': status.get(p['id'], {}).get('status', 'OK' if status else 'UNKNOWN'), 'issues': topics_by_pi.get(p['id'], []),
    })

names = {}
for de in d['dataElements']:
    names[de['id']] = (de.get('formName') or de['name']).strip()
for t in d['trackedEntityAttributes']:
    names[t['id']] = t['name'].strip()
names.update(STG)
used = set()
for r in rows:
    used |= set(re.findall(r'[A-Za-z][A-Za-z0-9]{10}', r['filter'] + ' ' + r['expr'] + ' ' + ' '.join(o['filter'] for x in r['disagg'] for o in x['options'])))
names = {k: v for k, v in names.items() if k in used}
datasets = sorted(({'id': x['id'], 'name': x['name'], 'period': x.get('periodType'),
                    'count': sum(1 for r in rows if any(y['id'] == x['id'] for y in r['datasets']))} for x in DSETS), key=lambda x: x['name'])
datasets = [x for x in datasets if x['count']]  # only data sets some PI feeds
cats = {c: {'name': CAT[c]['name'], 'options': [COPT.get(o['id'], o['id']) for o in CAT[c]['categoryOptions']]} for c in used_cats if c in CAT}
out = cfg.out('dictionary.json')
with open(out, 'w') as f:
    json.dump({'title': cfg.title, 'program': cfg.program.get('name'), 'cats': cats, 'rows': rows, 'names': names, 'datasets': datasets,
               'exported': cfg.exported, 'version': cfg.dhis2_version, 'hasStatus': bool(status)}, f, ensure_ascii=False, separators=(',', ':'))
linked = sum(1 for r in rows if r['target'])
print(f"{len(rows)} PIs -> {out}: {sum(1 for r in rows if r['parseError'])} parse errors, {linked} linked to an aggregate data element by code "
      f"({sum(1 for r in rows if r['target'] and r['target']['match'] != 'exact')} case-insensitive), {len(datasets)} data sets, "
      f"{sum(1 for r in rows if r['comboMatch'] is False or r['attrMatch'] is False)} with a breakdown that differs")


# ---------------- input batches for the description sub-agents ----------------
def with_names(s):
    s = re.sub(r'#\{(\w+)\.(\w+)\}', lambda m: f'[{STG.get(m[1], m[1])} › {names.get(m[2], m[2])}]', s or '')
    return re.sub(r'A\{(\w+)\}', lambda m: f'[{names.get(m[1], m[1])}]', s)


if args.desc_batches:
    INSTR = ('For each PI write "description": 1-3 plain sentences (at most 450 characters) saying what the indicator counts as '
             'configured: unit, period rule, conditions, breakdown. Describe the definition, not the intent. Set "nameMismatch" to '
             'one sentence when the definition does not match the name (missing or extra condition, wrong unit), else null. '
             'Output: one JSON object {"<uid>": {"description": "...", "nameMismatch": null}} per batch, same file name, in dict-desc/out/.')
    n = max(1, args.desc_batches)
    for k in range(n):
        part = rows[k::n]
        batch = {'instructions': INSTR, 'pis': [{
            'id': r['id'], 'code': r['code'], 'name': r['name'], 'type': r['type'], 'aggregation': r['agg'],
            'headline': r['headline'].replace('‹', '').replace('›', ''), 'period': r['period'],
            'conditions': [c.replace('‹', '').replace('›', '') for c in r['conditions']],
            'breakdown': [x['category'] for x in r['disagg']], 'filter': with_names(r['filter']), 'expression': with_names(r['expr']),
            'currentDescription': r['description']} for r in part]}
        with open(cfg.out('dict-desc', 'in', f'batch{k + 1}.json'), 'w') as f:
            json.dump(batch, f, ensure_ascii=False, indent=1)
    print(f'{n} description batches -> {cfg.out("dict-desc", "in")}')
