"""Write the meeting decisions document from review.json and a snapshot of the decisions.

Inputs:  work_dir/review.json; a decisions snapshot (--decisions, default work_dir/decisions.json) in any of:
           - the page's "Export decisions" file  {"format": "pireview-meeting-decisions", "decisions": {key: doc}}
           - a dump of the artifact db collection "decisions": {key: doc} or [{"id": key, "data": doc}, ...]
             (doc = {"verdict", "note", "by", "byName", "at", "excluded": [uid], "items": {uid: fix|ok|rename|domain}})
           - several files (repeat --decisions): merged per topic, newest "at" wins
Output:  work_dir/PI-REVIEW-DECISIONS.md (or --out): summary, decision table, follow-ups, per-topic detail,
         per-item checklists for "Check each item" topics.
Config:  title, program, exported, dhis2_version, work_dir.
Options: --link NAME=URL (repeatable) adds source links; --meeting TEXT describes the meeting (date, who);
         --clarifications FILE appends a Markdown section written by hand (e.g. answers from comment threads).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config
import collections as C, json

ap = config.arg_parser(__doc__.split('\n')[0])
ap.add_argument('--decisions', action='append', help='decisions snapshot / export file (repeatable)')
ap.add_argument('--out', help='output Markdown (default work_dir/PI-REVIEW-DECISIONS.md)')
ap.add_argument('--link', action='append', default=[], metavar='NAME=URL', help='source link shown in the header')
ap.add_argument('--meeting', help='one line about the meeting, e.g. "7 October 2026, programme team and data managers"')
ap.add_argument('--clarifications', help='Markdown file appended as "Clarifications"')
args = ap.parse_args()
cfg = config.load(args)

with open(cfg.out('review.json')) as f:
    R = json.load(f)
T = R['topics']
META = R.get('cfg', {})
SEV = {'FAILS': 'Fails in analytics', 'ALWAYS_ZERO': 'Always zero', 'WRONG_VALUE': 'Wrong value', 'REVIEW': 'Needs review'}
LABEL = {'agree': 'Agreed, fix', 'each': 'Check each item', 'park': 'Parked', 'domain': 'Needs domain input',
         'disagree': 'Not a problem', None: 'Not decided'}
MEANING = {
    'Agreed, fix': 'The finding and the proposed fix are accepted for every item in the topic, except items marked as exceptions.',
    'Check each item': 'The topic groups different cases; there is no group decision. Each item needs its own decision (checklists below).',
    'Parked': 'Deferred until the condition in the note is met.',
    'Needs domain input': 'The programme team has to decide what the indicator should count.',
    'Not a problem': 'The room decided the finding does not need a change.',
    'Not decided': 'No decision was recorded for the topic.',
}
ITEM = {'fix': 'fix', 'ok': 'OK as is', 'rename': 'rename', 'domain': 'needs domain input'}


def normalise(raw):
    """Any supported snapshot shape -> {key: doc}."""
    if isinstance(raw, dict) and isinstance(raw.get('decisions'), (dict, list)):
        raw = raw['decisions']
    if isinstance(raw, list):  # [{"id"/"doc_id"/"key": k, "data": {...}}] or flat docs with an id
        out = {}
        for x in raw:
            k = x.get('id') or x.get('doc_id') or x.get('key')
            out[k] = x.get('data', {k2: v for k2, v in x.items() if k2 not in ('id', 'doc_id', 'key')})
        return out
    out = {}
    for k, v in raw.items():
        if isinstance(v, list):  # compact form [verdict, note, at]
            v = dict(zip(('verdict', 'note', 'at'), v))
        if isinstance(v, dict):
            out[k] = v
    return out


dec = {}
for path in args.decisions or [cfg.out('decisions.json')]:
    if not os.path.exists(path):
        sys.exit(f'no decisions snapshot at {path}: dump the "decisions" collection of the review page, or export it from the page')
    with open(path) as f:
        for k, d in normalise(json.load(f)).items():
            if k not in dec or (d.get('at') or '') > (dec[k].get('at') or ''):
                dec[k] = d
unknown = sorted(set(dec) - {t['key'] for t in T})
if unknown:
    print('decisions for topics not in review.json (ignored):', unknown, file=sys.stderr)

D = {t['key']: dec.get(t['key'], {}) for t in T}
num = {t['key']: n for n, t in enumerate(T, 1)}
lab = {t['key']: LABEL.get(D[t['key']].get('verdict'), D[t['key']].get('verdict')) for t in T}
cnt = C.Counter(lab.values())
EACH = [t for t in T if lab[t['key']] == 'Check each item']
unit = lambda t: 'mappings' if t['kind'] == 'mapping' else 'PIs'
note = lambda t: (D[t['key']].get('note') or '').strip()
cell = lambda s: s.replace('|', '\\|').replace('\n', ' ')
excl = lambda t: [i for i in t['items'] if i['id'] in set(D[t['key']].get('excluded') or [])]


def item_name(i):
    return f"`{i['code']}` {i.get('label') or i['name']}" if i.get('code') else (i.get('label') or i['name'])


L = []
w = L.append
title = META.get('title') or cfg.title
w(f'# {title}: program indicator review decisions\n')
if args.meeting:
    w(f'**Review meeting:** {args.meeting}  ')
ats = sorted(d['at'] for d in D.values() if d.get('at'))
whos = sorted({d.get('byName') or d.get('by') for d in D.values() if d.get('byName') or d.get('by')})
if ats:
    w(f"**Decisions recorded:** {ats[0][:16].replace('T', ' ')} – {ats[-1][:16].replace('T', ' ')} UTC" + (f" ({len(whos)} recorder{'s' if len(whos) != 1 else ''})" if whos else '') + '  ')
scope = f"{R.get('total', META.get('piCount', '?')):,} program indicators and {R.get('mappings', META.get('mappingCount', 0)):,} disaggregation mappings on program {META.get('program') or cfg.program_id} (`{META.get('programId') or cfg.program_id}`)"
if META.get('exported') or cfg.exported:
    scope += f", from the metadata export of {META.get('exported') or cfg.exported}"
if META.get('dhis2Version') or cfg.dhis2_version:
    scope += f", validated on DHIS2 {META.get('dhis2Version') or cfg.dhis2_version}"
w(f'**Scope:** {scope}.  ')
links = []
for x in args.link:
    name, _, url = x.partition('=')
    links.append(f'[{name}]({url})' if url else name)
w('**Sources:** ' + ', '.join(links + ['`review.json`', '`pi-findings.csv`']) + '. Page data version `' + str(META.get('dataVersion', '')) + '`.\n')

w('## Summary\n')
decided = sum(1 for t in T if D[t['key']].get('verdict'))
w(f'{decided} of {len(T)} topics have a decision.\n')
w('| Decision | Topics | Meaning |\n|---|---:|---|')
for k in ['Agreed, fix', 'Check each item', 'Parked', 'Needs domain input', 'Not a problem', 'Not decided']:
    if cnt.get(k) or k != 'Not decided':
        w(f'| {k} | {cnt.get(k, 0)} | {MEANING[k]} |')
w('')
nex = sum(len(excl(t)) for t in T if lab[t['key']] == 'Agreed, fix')
w(f'{nex} item(s) in agreed topics were marked as exceptions; the fix applies to every other item listed. '
  '"Check each item" is not a group approval: only the items with a recorded per-item decision are decided.\n' if nex else
  'No items in agreed topics were marked as exceptions, so for "Agreed, fix" topics the fix applies to every item listed. '
  '"Check each item" is not a group approval: only the items with a recorded per-item decision are decided.\n')

w('## Decisions\n')
w('| # | Topic | Severity | Affected | Decision | Note |\n|---|---|---|---:|---|---|')
for t in T:
    w(f"| {num[t['key']]} | {cell(t['title'])} | {SEV.get(t['severity'], t['severity'])} | {t['count']} | {lab[t['key']]} | {cell(note(t))} |")
w('')

w('## Follow-up\n')
for k, intro in [('Check each item', 'checklists under [Per-item checklists](#per-item-checklists)'), ('Needs domain input', None),
                 ('Parked', None), ('Not decided', None)]:
    ts = [t for t in T if lab[t['key']] == k]
    if not ts:
        continue
    w(f'**{k}**' + (f' ({intro})' if intro else ''))
    for t in ts:
        extra = ''
        if k == 'Check each item':
            di = D[t['key']].get('items') or {}
            extra = f" — {sum(1 for i in t['items'] if i['id'] in di)} of {len(t['items'])} items decided"
        w(f"- Topic {num[t['key']]}, **{t['title']}** ({t['count']} PIs){extra}" + (f': {note(t)}' if note(t) else ''))
    w('')
ag = [t for t in T if lab[t['key']] == 'Agreed, fix' and note(t)]
if ag:
    w('**Agreed, with notes from the meeting**')
    for t in ag:
        w(f"- Topic {num[t['key']]}, **{t['title']}**: {note(t)}")
    w('')
if args.clarifications:
    with open(args.clarifications) as f:
        w('## Clarifications\n')
        w(f.read().strip() + '\n')

w('## Topic details\n')
w('Each topic lists the proposed fix from the review and the affected items. Mapping topics list the mappings; every indicator using them is affected.\n')
for t in T:
    d = D[t['key']]
    w(f"### {num[t['key']]}. {t['title']}\n")
    w(f"- **Decision:** {lab[t['key']]}" + (f' — *{note(t)}*' if note(t) else '') + (f" ({d.get('byName')}, {d['at'][:16].replace('T', ' ')} UTC)" if d.get('byName') and d.get('at') else ''))
    w(f"- **Severity:** {SEV.get(t['severity'], t['severity'])} · **Confidence:** {t['confidence']} · **Indicators affected:** {t['count']}")
    w(f"- **Problem:** {t['what']}")
    w(f"- **Proposed fix:** {t['fix']}" + (' (to be decided per item)' if lab[t['key']] == 'Check each item' else ''))
    w(f"- **Evidence:** {t['evidence']}")
    ex = excl(t)
    if lab[t['key']] == 'Check each item':
        w('- **Items:** see the checklist for this topic under [Per-item checklists](#per-item-checklists).')
    elif t['kind'] == 'pi':
        c = sorted(i['code'] or i['id'] for i in t['items'] if i not in ex)
        w(f"- **Indicators ({len(c)}):** " + (', '.join(f'`{x}`' for x in c) or 'none besides the exceptions'))
    else:
        w(f"- **Mappings ({len(t['items']) - len(ex)}):** " + '; '.join(f"{m['name']} ({m['piCount']} PIs)" for m in t['items'] if m not in ex))
    if ex and lab[t['key']] != 'Check each item':
        w(f"- **Exceptions (not affected, per the meeting):** " + ', '.join(item_name(i) for i in ex))
    w('')

if EACH:
    w('## Per-item checklists\n')
    w('One line per item with its specific finding. Items decided in the page are ticked with the decision; record the rest as **fix** (and how), **OK as is**, **rename** or **needs domain input**.\n')
    for t in EACH:
        di = D[t['key']].get('items') or {}
        w(f"### Topic {num[t['key']]}: {t['title']} ({len(t['items'])})\n")
        if note(t):
            w(f'*Meeting note: {note(t)}*\n')
        for i in sorted(t['items'], key=lambda x: x.get('code') or x['name']):
            if t['kind'] == 'mapping':
                det, what = ' '.join(i.get('issues', [])), None
            else:
                det = ' '.join(x for x in i['details'] if not x.startswith('What it counts:'))
                what = next((x[len('What it counts: '):] for x in i['details'] if x.startswith('What it counts:')), None)
            v = di.get(i['id'])
            w(f"- [{'x' if v else ' '}] {item_name(i)} — {det}" + (f' *What it counts now:* {what}' if what else '')
              + f"  \n  Decision: {('**' + ITEM.get(v, v) + '**') if v else '[ ]'}")
        w('')

w('## Suggested fix order\n')
w('A suggestion from the severities and decisions; it was not decided in the meeting.\n')
step = 0
for sev in ['FAILS', 'ALWAYS_ZERO', 'WRONG_VALUE', 'REVIEW']:
    ts = [num[t['key']] for t in T if t['severity'] == sev and lab[t['key']] == 'Agreed, fix']
    if ts:
        step += 1
        w(f"{step}. {SEV[sev]}: topic{'s' if len(ts) > 1 else ''} {', '.join(map(str, ts))}.")
if EACH:
    step += 1
    w(f"{step}. Per-item reviews: topic{'s' if len(EACH) > 1 else ''} {', '.join(str(num[t['key']]) for t in EACH)}, using the checklists.")
wait = [t for t in T if lab[t['key']] in ('Parked', 'Needs domain input', 'Not decided')]
if wait:
    step += 1
    w(f"{step}. Waiting for a decision or a condition: " + ', '.join(f"topic {num[t['key']]} ({lab[t['key']].lower()})" for t in wait) + '.')
w('\nAfter fixing, re-run the validation on a fresh test instance to confirm.')

out = args.out or cfg.out('PI-REVIEW-DECISIONS.md')
with open(out, 'w') as f:
    f.write('\n'.join(L) + '\n')
print(f'{len(L)} lines -> {out}', dict(cnt))
