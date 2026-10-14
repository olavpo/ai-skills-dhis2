"""Build deterministic fix proposals for the topics the review agreed on.

    python3 scripts/fix/build_fixes.py [--config pireview.json] [--review review.json] [--decisions decisions.json]
                                       [--fixes fixes-explicit.json] [--out fixes.json]

Inputs (default: in cfg.work_dir)
  metadata (cfg.metadata)                 exported items and aux objects (adapter.items / adapter.aux_items)
  review.json                             topics with items (phase 2); a topic may carry "checks": [lint checks]
  decisions.json | decisions-snapshot.json   verdict per topic key: {"key": {"verdict", "note", "excluded": [uid], "confirmed": [uid]}}
                                          or the snapshot shape {"key": [verdict, note, time]}; verdicts agree / check (check
                                          each item) / park / domain / disagree
  fixes-explicit.json (optional)          scope-specific decisions that code cannot make, see below
Output
  fixes.json (references/data-formats.md): type, scope, groups, items (complete before/after), aux (before/after),
  excluded (= not proposed, with reason and decision needed), errors, skippedAgeChanges; every record has "kind"
  (adapter.ITEM_KIND / AUX_KIND)

Which groups run. Each generic group (the type's patterns.GROUPS, fixed order) is linked to review topics by topic key or
by the lint checks a topic carries (override with "topics" in the fixes file). For an agreed topic the group runs on all
its items except the excluded ones; for a "check each item" topic only on items listed in the fixes file. Groups marked
confirm only change items listed per item in the fixes file (or a decision's "confirmed" list), even when the topic is
agreed.

fixes-explicit.json (JSON; item references are UIDs or codes, aux references UIDs or names;
the aux kind is accepted for "aux", e.g. "mapping", and "skip_<aux kind>s" for "skip_aux"):
  {"topics":  {"<group>": ["<topic key>"]},                    # link groups to topic keys (replaces the defaults)
   "groups":  {"<group>": {"enabled": true|false, "items": [ref], "scan": false, "title", "change", "why",
                           "topic": "<key>", "after": "<group>", "periodType": "Monthly", "options": ["option name"]}},
   "explicit": [{"group": "<group>", "items": [ref], "field": "<field>", "find": "old text", "replace": "new text",
                 "justification": "why this is the one correct change"},       # field default: patterns.EXPLICIT_FIELD
                {"group": "<group>", "aux": ref, ...type-specific keys (patterns.explicit_aux)...,
                 "justification": "..."}],
   "hold":    {"<ref>": "reason the item is left out of every group"},
   "skip_aux": {"<ref>": "reason"},
   "not_proposed": [{"topic": "<key>", "items": [ref], "reason": "...", "decision": "decision needed"}]}
"enabled": true runs a group without an agreed topic (e.g. a fix found in testing); "scan": true takes every item / aux
object in scope as a candidate (silently skipping those the pattern doesn't fit; not for confirm groups). An explicit entry
whose group is not a generic one defines a new group (give its title/change/why under "groups"; it runs after "after", by
default after the last group of its kind). "find": null replaces the whole field. Every find must match exactly once
(whitespace-insensitive).

Config keys: metadata, work_dir (+ type keys).
"""
import os, sys, json, copy, collections as C
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, config  # noqa: E402
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fix.common import FixError, sub_once  # noqa: E402

AGREE, CHECK = 'agree', 'check'
VERDICT = {'agree': AGREE, 'agreed': AGREE, 'fix': AGREE, 'check': CHECK, 'each': CHECK, 'check-each': CHECK, 'individual': CHECK}


def load_json(path, default=None):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def load_decisions(path):
    raw = load_json(path, {}) or {}
    raw = raw.get('decisions', raw) if isinstance(raw, dict) else raw
    if isinstance(raw, list):  # list of documents with an id/key (artifact db dump: [{"id", "data": doc}])
        raw = {x.get('key') or x.get('id'): x.get('data', x) for x in raw}
    out = {}
    for k, v in raw.items():
        if isinstance(v, list):
            v = {'verdict': v[0], 'note': v[1] if len(v) > 1 else None}
        v = v.get('data', v) if isinstance(v, dict) and 'verdict' not in v else v
        marks = v.get('items') or {}       # meeting page, "Check each item": {uid: fix|ok|rename|domain}
        if isinstance(marks, list):
            marks = {i: 'fix' for i in marks}
        out[k] = {'verdict': VERDICT.get((v.get('verdict') or '').lower(), (v.get('verdict') or '').lower()),
                  'note': v.get('note'),
                  'excluded': set(v.get('excluded') or []) | {i for i, m in marks.items() if m != 'fix'},
                  'confirmed': set(v.get('confirmed') or []) | {i for i, m in marks.items() if m == 'fix'}}
    return out


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--review', help='review.json (default: work_dir/review.json)')
    ap.add_argument('--decisions', help='decisions (default: work_dir/decisions.json or decisions-snapshot.json)')
    ap.add_argument('--fixes', help='scope-specific fixes file (default: work_dir/fixes-explicit.json, optional)')
    ap.add_argument('--out', help='output (default: work_dir/fixes.json)')
    a = ap.parse_args()
    cfg = config.load(a)
    A = adapters.load(cfg)
    P = A.patterns
    IK, AK = A.ITEM_KIND, A.AUX_KIND
    M = P.make_meta(cfg)
    IT = {p['id']: p for p in A.items(cfg)}
    AUX = {m['id']: m for m in A.aux_items(cfg)} if AK else {}
    AUXD = {k: A.aux_describe(cfg, m) for k, m in AUX.items()}
    review = load_json(a.review or cfg.out('review.json'), {'topics': []})
    dpath = a.decisions or next((cfg.out(f) for f in ('decisions.json', 'decisions-snapshot.json') if os.path.exists(cfg.out(f))), None)
    dec = load_decisions(dpath)
    X = load_json(a.fixes or cfg.fixes_file, {}) or {}
    if not dec:
        print('no decisions file: only groups enabled in the fixes file run', file=sys.stderr)

    def aux_ref(e):
        """The aux reference of an explicit entry ("aux", or the type's AUX_KIND as key)."""
        return e.get('aux', e.get(AK)) if AK else None

    topics = review.get('topics', [])
    T = {t['key']: t for t in topics}
    TNUM = {t['key']: n for n, t in enumerate(topics, 1)}
    bycode = {p.get('code'): p['id'] for p in IT.values() if p.get('code')}
    byauxname = {d['name']: k for k, d in AUXD.items()}
    errors, excluded = [], []

    def pid(ref):
        return ref if ref in IT else bycode.get(ref)

    def mid(ref):
        return ref if ref in AUX else byauxname.get(ref)

    def rid(ref):
        return pid(ref) or mid(ref)

    def label(i):
        if i in IT:
            return IT[i].get('code') or IT[i]['name']
        return (AUXD.get(i) or {}).get('name') or i

    def verdict(k):
        return (dec.get(k) or {}).get('verdict')

    # ---------------------------------------------------------------- groups and their topics
    XG = X.get('groups', {})
    groups = [dict(g) for g in P.GROUPS]
    known = {g['key'] for g in groups}
    for e in X.get('explicit', []):  # new groups defined by explicit entries
        k = e['group']
        if k in known:
            continue
        kind = AK if aux_ref(e) is not None else IK
        spec = XG.get(k, {})
        g = dict(key=k, kind=kind, fn=None, confirm=False, identical=False, topics=[spec['topic']] if spec.get('topic') else [], checks=[],
                 title=spec.get('title', k), change=spec.get('change', 'Per item (see each change).'), why=spec.get('why', ''))
        after = spec.get('after') or [x['key'] for x in groups if x['kind'] == kind][-1]
        groups.insert([x['key'] for x in groups].index(after) + 1, g)
        known.add(k)
    for g in groups:
        for f in ('title', 'change', 'why'):
            if XG.get(g['key'], {}).get(f):
                g[f] = XG[g['key']][f]
        if g['key'] in X.get('topics', {}):
            g['topics'], g['checks'] = X['topics'][g['key']], []
        g['matched'] = [t['key'] for t in topics if t.get('kind', IK) == g['kind'] and (t['key'] in g['topics'] or set(t.get('checks') or []) & set(g['checks']))]
    ORDER = [g['key'] for g in groups]
    G = {g['key']: g for g in groups}

    hold = {rid(k) or k: v for k, v in X.get('hold', {}).items()}
    skip_aux = {mid(k) or k: v for k, v in {**X.get(f'skip_{AK}s', {}), **X.get('skip_aux', {})}.items()}

    # ---------------------------------------------------------------- candidates per group
    # targets[item] = {group: {'topics': [...], 'scan': bool}}
    targets = C.defaultdict(dict)
    unconfirmed = C.defaultdict(list)  # (topic, group) -> [item]

    def want(item, g, topic=None, scan=False, generic=True):
        """Register item for group g; generic=False when only explicit entries apply to it."""
        if item in hold:
            return
        t = targets[item].setdefault(g['key'], {'topics': [], 'scan': scan, 'generic': False})
        t['scan'] = t['scan'] and scan
        t['generic'] = t['generic'] or (generic and g['fn'] is not None)
        if topic and topic not in t['topics']:
            t['topics'].append(topic)

    def fits(i, g):
        return (i in IT) if g['kind'] == IK else (i in AUX)

    for g in groups:
        if g['fn'] is None:
            continue
        spec = XG.get(g['key'], {})
        if spec.get('enabled') is False:
            continue
        listed = {rid(r) for r in spec.get('items', [])} - {None}
        for k in g['matched']:
            v = verdict(k)
            if v not in (AGREE, CHECK):
                continue
            listed_here = listed | {rid(r) for r in dec[k]['confirmed']} - {None}
            for it in T[k].get('items', []):
                i = it['id']
                if not fits(i, g) or i in dec[k]['excluded']:
                    continue
                if v == AGREE and not g['confirm'] or i in listed_here:
                    want(i, g, k)
                elif i not in hold:
                    unconfirmed[(k, g['key'])].append(i)
        if spec.get('enabled') or listed:
            for i in listed:
                if fits(i, g):
                    want(i, g, spec.get('topic') or (g['matched'] or [None])[0])
        if spec.get('scan') and not g['confirm']:
            for i in (IT if g['kind'] == IK else AUX):
                want(i, g, spec.get('topic') or (g['matched'] or [None])[0], scan=True)

    explicit = C.defaultdict(list)  # (item, group) -> [entry]
    for e in X.get('explicit', []):
        if aux_ref(e) is not None:
            i = mid(aux_ref(e))
            if not i:
                errors.append([aux_ref(e), e['group'], f'explicit: unknown {A.LABELS["aux"]}']); continue
            explicit[(i, e['group'])].append(e)
            want(i, G[e['group']], XG.get(e['group'], {}).get('topic') or (G[e['group']]['matched'] or [None])[0], generic=False)
        else:
            for r in e.get('items', []):
                i = pid(r)
                if not i:
                    errors.append([r, e['group'], f'explicit: unknown {A.LABELS["item"]}']); continue
                explicit[(i, e['group'])].append(e)
                want(i, G[e['group']], XG.get(e['group'], {}).get('topic') or (G[e['group']]['matched'] or [None])[0], generic=False)

    def run_explicit(obj, i, gk, is_aux):
        ch = []
        for e in explicit.get((i, gk), []):
            note = e.get('justification') or e.get('note') or ''
            if is_aux:
                ch.append(P.explicit_aux(obj, e, M, gk))
            else:
                f = e.get('field', P.EXPLICIT_FIELD)
                obj[f] = e['replace'] if e.get('find') is None else sub_once(obj.get(f), e['find'], e['replace'], f'explicit {gk}')
                ch.append({'field': f, 'note': note})
        return ch

    # ---------------------------------------------------------------- apply
    items_out, aux_out = [], []
    ctx_base = lambda gk: {'unresolved': [], 'skipped': skipped, 'opts': XG.get(gk, {})}
    skipped, failed = [], C.defaultdict(list)  # (topic, group, message) -> [item]
    for i, gs in targets.items():
        is_aux = i in AUX
        before = AUX[i] if is_aux else IT[i]
        if is_aux and i in skip_aux:
            failed[((gs[next(iter(gs))]['topics'] or [None])[0], '*', skip_aux[i])].append(i)
            continue
        after = copy.deepcopy(before)
        changes = []
        for gk in sorted(gs, key=ORDER.index):
            g, info = G[gk], gs[gk]
            ctx = ctx_base(gk)
            try:
                ch = g['fn'](after, M, ctx) if info['generic'] else []
            except FixError as e:
                if not info['scan']:
                    errors.append([label(i), gk, str(e)])
                    if not explicit.get((i, gk)):
                        failed[((info['topics'] or [None])[0], gk, str(e).split(':', 1)[-1].strip())].append(i)
                ch = []
            try:
                ch += run_explicit(after, i, gk, is_aux)
            except FixError as e:
                errors.append([label(i), gk, str(e)])
            for c in ch:
                c.setdefault('group', gk)
                changes.append(c)
            for grp, msg in ctx['unresolved']:
                failed[((info['topics'] or [None])[0], grp, msg)].append(i)
        if not changes:
            continue
        gl = sorted({c['group'] for c in changes}, key=ORDER.index)
        if is_aux:
            aux_out.append({'id': i, 'kind': AK, 'name': AUXD[i]['name'], 'type': AUXD[i].get('type'),
                             'groups': gl, 'changes': changes, 'before': before, 'after': after,
                             'users': sorted(IT[u].get('code') or u for u in A.aux_users(cfg, i, list(IT.values())))})
        else:
            items_out.append({'id': i, 'kind': IK, 'code': before.get('code'), 'name': before['name'], 'type': A.describe(cfg, before).get('type'),
                            'groups': gl, 'changes': changes, 'before': before, 'after': after, 'concern': None})

    # ---------------------------------------------------------------- not proposed
    fixed_topics = C.defaultdict(set)  # item -> topics handled by a group that changed it
    for r in items_out + aux_out:
        for gk in r['groups']:
            fixed_topics[r['id']] |= set(targets[r['id']].get(gk, {}).get('topics') or []) | set(G[gk]['matched'])
    for (topic, gk, msg), items in failed.items():
        items = [i for i in dict.fromkeys(items)]
        excluded.append({'topic': topic, 'group': None if gk == '*' else gk, 'items': items, 'codes': [label(i) for i in items],
                         'reason': msg, 'decision': 'Decide the correction for these items (the generic pattern does not fit).'})
    for (topic, gk), items in unconfirmed.items():
        items = [i for i in items if gk not in targets.get(i, {})]
        if items:
            excluded.append({'topic': topic, 'group': gk, 'items': items, 'codes': [label(i) for i in items],
                             'reason': f'{G[gk]["title"]}: this pattern changes meaning per item and needs per-item confirmation.',
                             'decision': 'Confirm each item (list it under groups.%s.items in the fixes file) or give an explicit replacement.' % gk})
    disabled = {g['key'] for g in groups if XG.get(g['key'], {}).get('enabled') is False}
    covered = {k for g in groups for k in g['matched'] if (g['fn'] and g['key'] not in disabled) or any(gk == g['key'] for (_, gk) in explicit)}
    manual = {e['topic'] for e in X.get('not_proposed', [])}
    for t in topics:
        k = t['key']
        if verdict(k) not in (AGREE, CHECK) or k in covered or k in manual:
            continue
        off = [g['key'] for g in groups if k in g['matched'] and g['key'] in disabled]
        if off:
            reason, decision = f'The fix group(s) {", ".join(off)} are disabled in the fixes file.', 'Record why, or enable the group.'
        else:
            reason, decision = next((P.NOT_CLEAR[c] for c in [k] + list(t.get('checks') or []) if c in P.NOT_CLEAR), P.GENERIC_NOT_CLEAR)
        items = [it['id'] for it in t.get('items', []) if it['id'] not in dec[k]['excluded']]
        # symptoms of a broken aux object (e.g. cells don't add up, records dropped) on items whose aux objects are fixed
        users_of = {m['id']: set(A.aux_users(cfg, m['id'], list(IT.values()))) for m in aux_out}
        via = [i for i in items if t.get('kind', IK) == IK and any(i in u for u in users_of.values())]
        if via:
            names = sorted({m['name'] for m in aux_out if users_of[m['id']] & set(via)})
            excluded.append({'topic': k, 'group': None, 'items': via, 'codes': [label(i) for i in via], 'resolvedBy': names,
                             'reason': f'Expected to be resolved by the {A.LABELS["aux_short"]} fixes (' + ', '.join(names) + f'); no change to the {A.LABELS["item"]} itself.',
                             'decision': 'None, if the end-to-end engine check shows totals and cells matching after the fix.'})
            items = [i for i in items if i not in via]
        if items:
            excluded.append({'topic': k, 'group': None, 'items': items, 'codes': [label(i) for i in items], 'reason': reason, 'decision': decision})
    for e in X.get('not_proposed', []):
        items = [rid(r) or r for r in e.get('items', [])]
        excluded.append({'topic': e.get('topic'), 'group': None, 'items': items, 'codes': [label(i) for i in items],
                         'reason': e['reason'], 'decision': e.get('decision', '')})
    for i, why in hold.items():
        excluded.append({'topic': None, 'group': None, 'items': [i], 'codes': [label(i)], 'reason': why, 'decision': ''})

    # other open topics per changed item, for context
    membership = C.defaultdict(list)
    for t in topics:
        for it in t.get('items', []):
            membership[it['id']].append(t['key'])
    for r in items_out:
        r['open'] = [{'topic': k, 'num': TNUM[k], 'title': T[k]['title']} for k in membership[r['id']] if k not in fixed_topics[r['id']]]

    used = {gk for r in items_out + aux_out for gk in r['groups']}
    gout = []
    for g in groups:
        if g['key'] not in used:
            continue
        tk = XG.get(g['key'], {}).get('topic') or (g['matched'] or [None])[0]
        gout.append({'key': g['key'], 'kind': g['kind'], 'topic': tk, 'topicNum': TNUM.get(tk), 'title': g['title'], 'change': g['change'],
                     'why': g['why'], 'identical': g['identical'], 'verified': XG.get(g['key'], {}).get('verified'),
                     'count': sum(g['key'] in r['groups'] for r in items_out + aux_out)})
    out = {'type': A.TYPE, 'scope': A.scope(cfg)['id'], 'groups': gout, 'items': sorted(items_out, key=lambda r: r['code'] or r['id']),
           'aux': aux_out, 'excluded': excluded, 'errors': errors, 'skippedAgeChanges': skipped}
    path = a.out or cfg.out('fixes.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f'{path}: {len(items_out)} {A.LABELS["shorts"]}, {len(aux_out)} {A.LABELS["aux_shorts"]}, {len(excluded)} not-proposed entries, {len(errors)} errors')
    print('  groups:', dict(C.Counter(gk for r in items_out + aux_out for gk in r['groups'])))
    for s in skipped:
        print('  age change skipped by safeguard:', s[0], '/', s[1], '-', s[4])
    for e in errors:
        print('  not applied:', *e)


if __name__ == '__main__':
    main()
