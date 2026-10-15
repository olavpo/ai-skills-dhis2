"""Approval page data and page: fixes + test results + screenshots -> fix-review.json and <p>-fix-review.html.

fix-review.json is everything a reviewer needs to decide, per fix group and per item (DATA in fix_review_template.html):
  title, type, scopeId, scopeName, labels (adapter.LABELS), payloadKey, auxExport (adapter.aux_export: how approved aux
  objects are exported), exported, generated, version (hash of the proposals; decisions files carry it), fileStem
  (export file names), notesLabel; groups [{key, title, short, change, why, identical, topicNum, topicTitle, isNew,
  kind, items, scenarios, note: {impact, text}}]; items [{id, code, name, groups, changes, before, after
  (adapter.diff_fields), full (complete proposed object), concern, open, failCause, test: {before, after, engineOk,
  cellsOk}}]; aux [{id, name, type, groups, changes, before, after (adapter.aux_diff_fields), users, afterFull, test}];
  excluded; names (uid -> name for the names/UIDs toggle); test {instance, period, truthPersons, scenarios, engine,
  stillFailing, steps, apiCalls, readBack, syntax}; shots {name: data URI}, shotCaptions.

<p>-fix-review.html (<p> = adapter.PREFIX) is the Claude-artifact variant: publish it with capabilities db (collections
approvals, descriptions), user and downloads. make_standalone.py derives the no-account variant.

Downstream notes (optional, config "notes"): {"_consumer": "Downstream report pipeline",
  "<group key>": {"impact": "same|none|changes|action", "text": "..."}}   (or ["impact", "text"])
shown per group as "<_consumer>: text" (amber when impact is "action").

Inputs : fixes.json; optional results.json, analytics-before/after.json, review.json (topic titles), shots/*.png +
         shots/captions.json, api-log.jsonl, e2e-steps.jsonl, syntax-check.json, the notes file
Outputs: fix-review.json, <p>-fix-review.html
Config : title, metadata, work_dir, test_period, dhis2_version, exported, notes (+ type keys).
"""
import base64, collections as C, datetime as dt, glob, hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'lib'))
sys.path.insert(0, os.path.join(HERE, '..', 'e2e'))
import adapters, config  # noqa: E402
from evidence import syntax_rows  # noqa: E402

TEMPLATE = os.path.join(HERE, 'fix_review_template.html')


def jload(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40] or 'review'


def short_label(g):
    if g.get('short'):
        return g['short']
    t = g.get('title') or g['key']
    return t if len(t) <= 24 else t[:22].rsplit(' ', 1)[0] + '…'


def val(a, k):
    v = (a or {}).get(k)
    if not v:
        return None
    if v.get('total_err'):
        return {'error': v['total_err'][:160], 'pe': v.get('pe')}
    return {'value': v.get('total'), 'pe': v.get('pe')}


def page(template_path, data, mode):
    """Template with the data and mode filled in (used here and by make_standalone.py)."""
    with open(template_path) as f:
        t = f.read()
    assert t.count('__DATA__') == 1 and t.count('__MODE__') == 1, 'template placeholders changed'
    js = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    return t.replace('__MODE__', mode).replace('__DATA__', js)


def notes(cfg):
    n = jload(cfg.notes_path, {}) if cfg.notes_path else {}
    label = n.pop('_consumer', None) or 'Other consumers of these definitions'
    out = {}
    for k, v in n.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            v = {'impact': v[0], 'text': v[1]}
        if isinstance(v, str):
            v = {'impact': 'info', 'text': v}
        out[k] = v
    return label, out


def syntax_summary(cfg):
    j = jload(cfg.out('syntax-check.json'))
    if j is None:
        return None
    rows = syntax_rows(j)
    bad = [r for r in rows if isinstance(r, dict) and (r.get('ok') is False or r.get('status') not in (None, 'OK'))]
    return {'texts': len(rows), 'failures': len(bad)}


def build(cfg):
    A = adapters.load(cfg)
    F = jload(cfg.out('fixes.json')) or sys.exit(f'no {cfg.out("fixes.json")}: run the fix phase first')
    for k in ('groups', 'items', 'aux', 'excluded'):
        F.setdefault(k, [])
    R = jload(cfg.out('results.json'), {})
    AB, AA = jload(cfg.out('analytics-before.json'), {}), jload(cfg.out('analytics-after.json'), {})
    review = jload(cfg.out('review.json'), {})
    TOPIC = {t['key']: t for t in review.get('topics', [])}
    names = A.names(cfg)
    proposed = {r['id']: r['after'] for r in F['items']}
    all_items = [proposed.get(p['id'], p) for p in A.items(cfg)]
    label = lambda p: p.get('code') or p.get('name') or p['id']
    eng = ((R.get('engine') or {}).get('after') or {}).get('perItem', {})

    scen_by_group = C.defaultdict(list)
    for s in R.get('scenarios', []):
        for g in (s.get('group') or '').split('+'):
            scen_by_group[g.strip()].append(s['key'])

    items_out = []
    for r in F['items']:
        e = eng.get(r['id']) or {}
        opn = r.get('open') or []
        items_out.append({'id': r['id'], 'code': r.get('code') or '', 'name': r.get('name') or r['after'].get('name', r['id']), 'groups': r['groups'],
                    'changes': r.get('changes', []), 'before': A.diff_fields(r['before'], cfg), 'after': A.diff_fields(r['after'], cfg),
                    'concern': r.get('concern'), 'open': opn, 'failCause': opn[0]['title'] if opn else None,
                    'test': {'before': val(AB, r['id']), 'after': val(AA, r['id']), 'engineOk': e.get('totalOk'), 'cellsOk': e.get('cellsOk')},
                    'full': r['after']})
    aux_out = []
    for m in F['aux']:
        uids = set(A.aux_users(cfg, m['id'], all_items))
        users = [p for p in all_items if p['id'] in uids]
        checked = [p for p in users if (eng.get(p['id']) or {}).get('cellsOk') is not None]
        aux_out.append({'id': m['id'], 'name': m.get('name') or A.aux_describe(cfg, m['after'])['name'], 'type': m.get('type', ''),
                     'groups': m['groups'], 'changes': m.get('changes', []), 'users': m.get('users') or [label(p) for p in users],
                     'before': A.aux_diff_fields(m['before'], cfg), 'after': A.aux_diff_fields(m['after'], cfg), 'afterFull': m['after'],
                     'test': {'checked': len(checked), 'ok': sum(1 for p in checked if eng[p['id']]['cellsOk'])}})
    notes_label, NOTES = notes(cfg)
    aux_ids = {m['id'] for m in aux_out}
    groups = []
    for g in F['groups']:
        items = [p['id'] for p in items_out if g['key'] in p['groups']] + [m['id'] for m in aux_out if g['key'] in m['groups']]
        if not items:
            continue
        topic = TOPIC.get(g.get('topic'))
        groups.append({**{k: g.get(k) for k in ('key', 'title', 'change', 'why', 'identical', 'topicNum')},
                       'short': short_label(g), 'topicTitle': g.get('topicTitle') or (topic or {}).get('title'),
                       'isNew': bool(g.get('isNew') or g.get('new')), 'kind': A.AUX_KIND if items[0] in aux_ids else A.ITEM_KIND,
                       'items': items, 'scenarios': scen_by_group.get(g['key'], []), 'note': NOTES.get(g['key'])})

    caps = jload(cfg.out('shots', 'captions.json'), {})
    shots = {}
    for f in sorted(glob.glob(cfg.out('shots', '[0-9]*.png'))):
        with open(f, 'rb') as fh:
            shots[os.path.basename(f)[:-4]] = 'data:image/png;base64,' + base64.b64encode(fh.read()).decode()
    steps = jsonl(cfg.out('e2e-steps.jsonl'))
    rb = next((s for s in reversed(steps) if s['step'] == 'read-back'), None)
    ea = (R.get('engine') or {}).get('after') or {}
    by_err = C.defaultdict(list)
    open_of = {p['code'] or p['id']: p['failCause'] for p in items_out}
    for code, err in (ea.get('errorByItem') or {}).items():
        by_err[open_of.get(code) or err].append(code)
    test = {'instance': f'DHIS2 {cfg.dhis2_version}' if cfg.dhis2_version else '', 'period': cfg.test_period,
            'truthPersons': R.get('truthPersons', 0), 'scenarios': R.get('scenarios', []),
            'engine': {k: {x: v.get(x) for x in ('queried', 'errors', 'compared', 'totalMismatch', 'cellSets', 'cellMismatch', 'errorByPi')}
                       for k, v in (R.get('engine') or {}).items()} or None,
            'stillFailing': [{'cause': k, 'codes': sorted(v)} for k, v in sorted(by_err.items(), key=lambda x: -len(x[1]))],
            'steps': steps, 'apiCalls': len(jsonl(cfg.out('api-log.jsonl'))),
            'readBack': {'items': len(rb.get('item_diffs') or []), 'aux': len(rb.get('aux_diffs') or [])} if rb else None,
            'syntax': syntax_summary(cfg)}
    if not test['engine']:
        test['engine'] = {}
    for m in aux_out:
        names.setdefault(m['id'], m['name'])
    sc = A.scope(cfg)
    out = {'title': cfg.title, 'type': A.TYPE, 'scopeId': sc['id'], 'scopeName': sc['name'], 'labels': A.LABELS,
           'kinds': {'item': A.ITEM_KIND, 'aux': A.AUX_KIND}, 'payloadKey': A.PAYLOAD_KEY, 'prefix': A.PREFIX,
           'auxExport': A.aux_export(cfg) if A.AUX_KIND else None,
           'exported': cfg.exported, 'generated': dt.date.today().isoformat(),
           'fileStem': slug(cfg.title), 'notesLabel': notes_label,
           'groups': groups, 'items': items_out, 'aux': aux_out, 'excluded': F['excluded'],
           'names': names, 'test': test, 'shots': shots, 'shotCaptions': {k: caps.get(k, k) for k in shots}}
    out['version'] = hashlib.sha256(json.dumps([items_out, aux_out], sort_keys=True).encode()).hexdigest()[:12]
    return out


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    args = ap.parse_args()
    cfg = config.load(args)
    d = build(cfg)
    s = json.dumps(d, ensure_ascii=False, separators=(',', ':'))
    with open(cfg.out('fix-review.json'), 'w') as f:
        f.write(s)
    html = page(TEMPLATE, d, 'artifact')
    path = cfg.out(adapters.out_name(adapters.load(cfg), 'fix-review.html'))
    with open(path, 'w') as f:
        f.write(html)
    print(f"groups {len(d['groups'])}, items {len(d['items'])}, aux {len(d['aux'])}, version {d['version']}, "
          f"{len(s) // 1024} KB data ({sum(len(v) for v in d['shots'].values()) // 1024} KB screenshots) -> {path}")


if __name__ == '__main__':
    main()
