"""Group the validation findings into meeting topics and build the review page.

One topic = one cause = one proposed fix. Topics come from a catalogue keyed by finding category
(scripts/review/topics.json), optionally extended per program with --topics-extra. Every finding ends
up in exactly one topic; findings no topic covers go into a catch-all topic and are printed, so nothing
is lost silently. Topics are ordered FAILS -> ALWAYS_ZERO -> WRONG_VALUE -> REVIEW.

Inputs (work_dir):  findings.json (lint), pi-findings.csv, pi-status.csv, compare.json (optional),
                    truth.json (optional, only counted); metadata from the config;
                    --descriptions DIR (optional): per-PI descriptions {uid: {description, nameMismatch|concern}}
Outputs (work_dir): review.json, pi-review.html (from review_template.html, unless --no-html)
Config keys:        metadata, program, work_dir, title, dhis2_version, exported

Extension file (--topics-extra, repeatable), same shape as topics.json ({"topics": [...]}):
  - a topic with a catalogue key overrides the given fields (title, fix, severity, checks, ...);
    {"key": ..., "drop": true} removes a catalogue topic;
  - a new key adds a topic (from checks, match, compare selectors, nameMismatch, or explicit items),
    placed after the topic named in "after" (default: its split parent, else the end of its severity);
  - "items": [{"id": "<uid or code>", "details": ["..."]}] adds explicit items with their detail lines;
  - "splitFrom": ["<key>"] takes the rows of those topics that match "match" (and/or the explicit
    item ids) out of them into this topic, e.g. one over-matching literal that needs its own decision;
  - "count": overrides the displayed indicator count;
  - "onlyUnexplained": true keeps a PI only if no other topic claims a finding on it (for symptom categories
    such as analytics-error); explained symptom rows are added as evidence to the explaining topic's item.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config
import collections as C, csv, glob, hashlib, json, re, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
SEV = ['FAILS', 'ALWAYS_ZERO', 'WRONG_VALUE', 'REVIEW']
RANK = {s: i for i, s in enumerate(SEV)} | {'OK': 9}
LINT_SEV = {'ERROR': 'WRONG_VALUE', 'WARN': 'REVIEW', 'INFO': 'REVIEW'}  # lint severity -> status, when no CSV

ap = config.arg_parser(__doc__.split('\n')[0])
ap.add_argument('--topics', default=os.path.join(HERE, 'topics.json'), help='topic catalogue')
ap.add_argument('--topics-extra', action='append', default=[], help='per-program extension file (repeatable)')
ap.add_argument('--descriptions', help='folder of per-PI description JSON files (name-vs-definition topic)')
ap.add_argument('--no-html', action='store_true', help='write review.json only')
ap.add_argument('--max-details', type=int, default=6, help='detail lines kept per item (default 6)')
args = ap.parse_args()
cfg = config.load(args)
md = cfg.md
PIS = cfg.pis
P = {p['id']: p for p in PIS}
BY_CODE = {}
for p in PIS:
    if p.get('code'):
        BY_CODE.setdefault(p['code'], p['id']); BY_CODE.setdefault(p['code'].upper(), p['id'])
MAP = {m['id']: m for m in cfg.program.get('categoryMappings', [])}
COPT = {o['id']: o['name'] for o in md['categoryOptions']}
CAT = {c['id']: c['name'] for c in md['categories']}


def jload(name, default=None):
    path = cfg.out(name)
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def csvload(name):
    path = cfg.out(name)
    if not os.path.exists(path):
        return None
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def clean(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def label(name, code):
    """Name without a leading copy of the code (many programs prefix names with the code)."""
    if code and name.startswith(code):
        rest = name[len(code):].lstrip(' -:–')
        return rest or name
    return name


# ---------------- inputs ----------------
lint = jload('findings.json', [])
status_rows = csvload('pi-status.csv')
pf_rows = csvload('pi-findings.csv')
cmp_raw = jload('compare.json', [])
CMP = {x['id']: x for x in cmp_raw} if isinstance(cmp_raw, list) else dict(cmp_raw)
truth = jload('truth.json')
if not lint and pf_rows is None:
    sys.exit(f'no findings.json or pi-findings.csv in {cfg.work_dir}: run the validate phase first')

DESC = {}
if args.descriptions:
    for f in sorted(glob.glob(os.path.join(args.descriptions, '*.json'))):
        with open(f) as fh:
            DESC.update(json.load(fh))


def resolve_pi(ref):
    return ref if ref in P else BY_CODE.get(ref) or BY_CODE.get(str(ref).upper())


# A "row" is one finding on one PI: (pid, category, severity-as-status, detail). pi-findings.csv is the
# merged view of every validation layer; without it, fall back to the PI-level lint findings.
rows = []
if pf_rows is not None:
    for r in pf_rows:
        if r['id'] in P:
            rows.append({'pid': r['id'], 'cat': r['category'], 'sev': r['severity'], 'detail': r['detail']})
else:
    for f in lint:
        if f['id'] in P and f['severity'] in ('ERROR', 'WARN'):
            rows.append({'pid': f['id'], 'cat': f['check'], 'sev': LINT_SEV[f['severity']], 'detail': f['detail']})
# lint findings that name a shared value instead of an object (duplicate-code etc.): one row per PI with that value
for f in lint:
    if f['id'] not in P and f['id'] not in MAP and f['severity'] in ('ERROR', 'WARN'):
        fld = f['check'].split('duplicate-', 1)[-1] if f['check'].startswith('duplicate-') else None
        hit = [p['id'] for p in PIS if fld and p.get(fld) == f['name']]
        for pid in hit or []:
            rows.append({'pid': pid, 'cat': f['check'], 'sev': LINT_SEV[f['severity']], 'detail': f['detail']})
        if not hit:
            print('  not linked to a PI or mapping, skipped:', f['check'], f['name'], file=sys.stderr)
map_findings = [f for f in lint if f['id'] in MAP]

# ---------------- topics: catalogue + extensions ----------------
with open(args.topics) as f:
    topics = [dict(t) for t in json.load(f)['topics']]
for path in (args.topics_extra or cfg.topics_extra):
    with open(path) as f:
        ext = json.load(f)
    for t in ext.get('topics', []):
        old = next((x for x in topics if x['key'] == t['key']), None)
        if old and t.get('drop'):
            topics.remove(old)
        elif old:
            items = t.pop('items', None)
            old.update(t)
            if items:
                old['items'] = old.get('items', []) + items
        else:  # new topic: after "after", else after its split parent, else at the end (then sorted by severity)
            anchor = t.get('after') or (t.get('splitFrom') or [None])[0]
            pos = next((n + 1 for n, x in enumerate(topics) if x['key'] == anchor), len(topics))
            topics.insert(pos, dict(t))
for t in topics:
    t.setdefault('kind', 'pi'); t.setdefault('checks', [])
    for k in ('title', 'confidence', 'confidenceWhy', 'what', 'evidence', 'fix'):
        t.setdefault(k, '')
    t['_explicit'] = t.pop('items', [])
    if t.get('splitFrom') and not t['checks']:
        t['checks'] = sorted({c for k in t['splitFrom'] for x in topics if x['key'] == k for c in x['checks']})


def matches(t, detail):
    if t.get('match') and not re.search(t['match'], detail or ''):
        return False
    if t.get('notMatch') and re.search(t['notMatch'], detail or ''):
        return False
    return True


# claim order: split topics, then topics with a match filter, then the rest (catalogue order inside)
order = sorted(range(len(topics)), key=lambda i: (0 if topics[i].get('splitFrom') else 1 if topics[i].get('match') else 2, i))
claimed = [None] * len(rows)
for i in order:
    t = topics[i]
    if t['kind'] != 'pi':
        continue
    checks = set(t['checks'])
    for n, r in enumerate(rows):
        if claimed[n] is None and r['cat'] in checks and matches(t, r['detail']):
            claimed[n] = t['key']

# Symptom topics ("onlyUnexplained": analytics errors, server rejections) keep a PI only when no other topic
# explains it; otherwise the symptom row is shown as evidence on the PI's item in the explaining topic.
explained = C.defaultdict(list)  # pid -> symptom details
claimers = C.defaultdict(set)
for n, r in enumerate(rows):
    if claimed[n]:
        claimers[r['pid']].add(claimed[n])
symptom = {t['key'] for t in topics if t.get('onlyUnexplained')}
# A server rejection is a cause of its own: only a syntax-type topic explains it (an AVERAGE on a count
# doesn't explain an unquoted UID). Analytics errors are explained by any other topic on the PI.
syntax_topics = {t['key'] for t in topics if t['key'] not in symptom and
                 (any(c.startswith('server-rejects') for c in t['checks']) or
                  {'parse-error', 'unsupported-function', 'unknown-de', 'de-not-in-stage'} & set(t['checks']))}
for t in topics:
    if t['key'] not in symptom:
        continue
    for n, r in enumerate(rows):
        others = claimers[r['pid']] - symptom
        if r['cat'].startswith('server-rejects'):
            others &= syntax_topics
        if claimed[n] == t['key'] and others:
            claimed[n] = '#explained'
            explained[r['pid']].append(f"{r['cat']}: {r['detail']}")

# mapping topics claim mapping lint findings; their 'uses-<check>' PI rows count as covered
mclaimed = [None] * len(map_findings)
for i in order:
    t = topics[i]
    if t['kind'] != 'mapping':
        continue
    for n, f in enumerate(map_findings):
        if mclaimed[n] is None and f['check'] in t['checks'] and matches(t, f['detail']):
            mclaimed[n] = t['key']
covered_by_mapping = {'uses-' + c for t in topics if t['kind'] == 'mapping' for c in t['checks']}

# ---------------- items ----------------
users = C.defaultdict(list)
for p in PIS:
    for m in p.get('categoryMappingIds') or []:
        if m:
            users[m].append(p.get('code') or p['id'])


def test_of(pid):
    x = CMP.get(pid, {})
    if 'analytics_total' not in x:
        return None
    return {'analytics': x['analytics_total'], 'filterMatches': x.get('expected_total_ignoring_disagg')}


def pi_item(pid, details, limit=None):
    p = P[pid]
    ds = list(dict.fromkeys(d for d in details if d))
    return {'id': pid, 'code': p.get('code') or '', 'name': p['name'], 'label': label(p['name'], p.get('code')),
            'type': p.get('analyticsType'), 'agg': p.get('aggregationType'), 'expr': clean(p.get('expression')),
            'filter': clean(p.get('filter'))[:1400], 'details': ds[:limit] if limit else ds, 'test': test_of(pid)}


def map_item(mid, issues):
    m = MAP[mid]
    return {'id': mid, 'name': m.get('mappingName') or mid, 'category': CAT.get(m.get('categoryId'), ''),
            'piCount': len(users[mid]), 'pis': sorted(users[mid])[:400],
            'options': [{'name': COPT.get(o['optionId'], o['optionId']), 'filter': clean(o.get('filter')) or '(no filter)'}
                        for o in m.get('optionMappings', [])],
            'issues': list(dict.fromkeys(issues))}


def compare_rows(sel):
    """Engine-check selectors on compare.json -> {pid: [detail]}."""
    out = C.defaultdict(list)
    for pid, x in CMP.items():
        if pid not in P or 'analytics_total' not in x:
            continue
        exp = x.get('expected_total_ignoring_disagg') if x.get('type') == 'ENROLLMENT' else x.get('expected_total')
        if 'total-mismatch' in sel and exp is not None and exp != x['analytics_total']:
            out[pid].append(f"engine check: independent calculation {exp:g}, analytics {x['analytics_total']:g}")
        if 'cells-mismatch' in sel and x.get('cells_ok') is False:
            out[pid].append(f"engine check: breakdown cells differ ({len(x.get('cell_diffs') or [])} cells)")
        if 'units-in-no-cell' in sel and (x.get('units_in_no_cell') or 0) > 0:
            out[pid].append(f"test data: {x['units_in_no_cell']} records match no breakdown cell")
        if 'units-in-several-cells' in sel and (x.get('units_in_several_cells') or 0) > 0:
            out[pid].append(f"test data: {x['units_in_several_cells']} records match several cells of one category")
    return out


shown_symptoms = set()  # symptom rows go on the PI's item in the first (most severe) topic that explains it
topics.sort(key=lambda t: RANK.get(t.get('severity'), 5))
extra_ids = set()  # PIs placed by a source that isn't in pi-status (name mismatch, explicit, compare)
for t in topics:
    acc = C.defaultdict(list)
    if t['kind'] == 'pi':
        for n, r in enumerate(rows):
            if claimed[n] == t['key']:
                acc[r['pid']].append(r['detail'])
        for pid in acc:
            if pid in explained and pid not in shown_symptoms:
                acc[pid] += explained[pid]; shown_symptoms.add(pid)
        for pid, ds in compare_rows(set(t.get('compare') or [])).items():
            acc[pid] += ds; extra_ids.add(pid)
        if t.get('nameMismatch'):
            for pid, x in DESC.items():
                why = x.get('nameMismatch') or x.get('concern')
                if pid in P and why:
                    acc[pid] += [why] + ([f"What it counts: {x['description']}"] if x.get('description') else [])
                    extra_ids.add(pid)
        for e in t['_explicit']:
            pid = resolve_pi(e['id'])
            if not pid:
                print(f"  topic {t['key']}: explicit item {e['id']} is not a PI of the program", file=sys.stderr); continue
            acc[pid] += e.get('details', []); extra_ids.add(pid)
        t['items'] = [pi_item(pid, ds, None if t['_explicit'] else args.max_details)
                      for pid, ds in sorted(acc.items(), key=lambda kv: (P[kv[0]].get('code') or '', P[kv[0]]['name']))]
        t['count'] = t.get('count') or len(t['items'])
    else:
        for n, f in enumerate(map_findings):
            if mclaimed[n] == t['key']:
                acc[f['id']].append(f['detail'])
        for e in t['_explicit']:
            if e['id'] in MAP:
                acc[e['id']] += e.get('details', [])
            else:
                print(f"  topic {t['key']}: explicit mapping {e['id']} is not on the program", file=sys.stderr)
        t['items'] = sorted((map_item(m, ds) for m, ds in acc.items()), key=lambda x: -x['piCount'])
        t['count'] = t.get('count') or len({u for x in t['items'] for u in users[x['id']]})

# split topics: their PIs leave the parent topics
for t in topics:
    ids = {i['id'] for i in t['items']}
    for k in t.get('splitFrom') or []:
        for parent in topics:
            if parent['key'] == k and parent['kind'] == t['kind']:
                parent['items'] = [i for i in parent['items'] if i['id'] not in ids]
                parent['count'] = len(parent['items'])

# ---------------- catch-all: nothing silently lost ----------------
loose = C.defaultdict(list)
loose_sev = {}
for n, r in enumerate(rows):
    if claimed[n] is None and r['cat'] not in covered_by_mapping:
        loose[r['pid']].append(f"[{r['cat']}] {r['detail']}")
        loose_sev[r['cat']] = min(loose_sev.get(r['cat'], 'REVIEW'), r['sev'] if r['sev'] in RANK else 'REVIEW', key=RANK.get)
loose_maps = C.defaultdict(list)
for n, f in enumerate(map_findings):
    if mclaimed[n] is None and f['severity'] in ('ERROR', 'WARN'):
        loose_maps[f['id']].append(f"[{f['check']}] {f['detail']}")
GENERIC = dict(confidence='medium', confidenceWhy='These findings matched no topic in the catalogue; each needs to be read on its own.',
               evidence='See the detail line on each item (the finding category is in brackets).',
               fix='Decide per item, or add a topic for this category to the catalogue (--topics-extra) and rebuild.', checkEach=True)
if loose:
    sev = min(loose_sev.values(), key=RANK.get)
    topics.append(dict(GENERIC, key='other', severity=sev, kind='pi', checks=sorted(loose_sev), title='Other findings not grouped into a topic',
                       what='Findings of these categories: ' + ', '.join(sorted(loose_sev)) + '.',
                       items=[pi_item(pid, ds) for pid, ds in sorted(loose.items(), key=lambda kv: P[kv[0]].get('code') or '')], count=len(loose)))
if loose_maps:
    cats = sorted({f['check'] for n, f in enumerate(map_findings) if mclaimed[n] is None and f['severity'] in ('ERROR', 'WARN')})
    its = sorted((map_item(m, ds) for m, ds in loose_maps.items()), key=lambda x: -x['piCount'])
    topics.append(dict(GENERIC, key='other-mapping', severity='REVIEW', kind='mapping', checks=cats, title='Other mapping findings not grouped into a topic',
                       what='Mapping findings of these categories: ' + ', '.join(cats) + '.', items=its,
                       count=len({u for x in its for u in users[x['id']]})))

T = [t for t in topics if t['items']]
for t in T:
    t.pop('_explicit', None); t.pop('match', None); t.pop('notMatch', None); t.pop('nameMismatch', None); t.pop('compare', None)
    t['checkEach'] = bool(t.get('checkEach'))
T.sort(key=lambda t: RANK.get(t['severity'], 5))  # stable: catalogue order inside a severity

# ---------------- index, names, counts ----------------
status = {r['id']: r.get('status', 'OK') for r in status_rows or []}
if status_rows is None:
    for r in rows:
        status[r['pid']] = min(status.get(r['pid'], 'OK'), r['sev'] if r['sev'] in RANK else 'REVIEW', key=RANK.get)
topics_of = C.defaultdict(list)
for t in T:
    if t['kind'] == 'pi':
        for i in t['items']:
            topics_of[i['id']].append(t['key'])
index = []
for pid, p in P.items():
    st = status.get(pid, 'OK')
    for k in topics_of[pid]:  # sources outside pi-status (names, explicit items; all topics without it) can make it worse
        t = next(x for x in T if x['key'] == k)
        if (pid in extra_ids or status_rows is None) and RANK[t['severity']] < RANK.get(st, 9):
            st = t['severity']
    index.append({'id': pid, 'code': p.get('code') or '', 'name': p['name'], 'label': label(p['name'], p.get('code')),
                  'status': st, 'topics': topics_of[pid]})

names = {}
for de in md['dataElements']:
    names[de['id']] = de.get('formName') or de['name']
for a in md['trackedEntityAttributes']:
    names[a['id']] = a['name']
for s in md['programStages']:
    names[s['id']] = s['name']
used = set()
for t in T:
    for i in t['items']:
        for txt in [i.get('filter', ''), i.get('expr', '')] + [o['filter'] for o in i.get('options', [])]:
            used |= set(re.findall(r'[A-Za-z][A-Za-z0-9]{10}', txt or ''))
names = {k: v for k, v in names.items() if k in used}

counts = C.Counter(x['status'] for x in index)
version = hashlib.sha1(json.dumps([[t['key'], [i['id'] for i in t['items']]] for t in T]).encode()).hexdigest()[:12]
meta = {'title': cfg.title, 'program': cfg.program.get('name', cfg.program_id), 'programId': cfg.program_id,
        'dhis2Version': cfg.dhis2_version, 'exported': cfg.exported, 'piCount': len(P), 'mappingCount': len(MAP),
        'persons': len(truth) if isinstance(truth, list) else None, 'engineCheck': bool(CMP),
        'built': dt.date.today().isoformat(), 'dataVersion': version}
review = {'cfg': meta, 'names': names, 'topics': T, 'index': index, 'counts': counts, 'total': len(index), 'mappings': len(MAP)}
out_json = cfg.out('review.json')
with open(out_json, 'w') as f:
    json.dump(review, f, ensure_ascii=False, separators=(',', ':'))

if not args.no_html:
    with open(os.path.join(HERE, 'review_template.html')) as f:
        tpl = f.read()
    title = re.sub(r'[<&>]', '', cfg.title)
    html = tpl.replace('__TITLE__', title).replace('__DATA__', json.dumps(review, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/'))
    with open(cfg.out('pi-review.html'), 'w') as f:
        f.write(html)

# ---------------- report ----------------
print(f"{len(T)} topics, {len(index)} PIs {dict(counts)}, data version {version} -> {out_json}")
for t in T:
    print(f"  {t['severity']:12} {t['confidence']:6} {t['count']:5}  {t['key']}{'  (check each)' if t['checkEach'] else ''}")
if loose or loose_maps:
    print('UNCOVERED findings (put in the catch-all topics; add topics for them with --topics-extra):')
    for c, n in C.Counter(r['cat'] for k, r in enumerate(rows) if claimed[k] is None and r['cat'] not in covered_by_mapping).most_common():
        print(f'  pi       {n:5}  {c}')
    for c, n in C.Counter(f['check'] for k, f in enumerate(map_findings) if mclaimed[k] is None and f['severity'] in ('ERROR', 'WARN')).most_common():
        print(f'  mapping  {n:5}  {c}')
else:
    print('every finding is in a topic')
