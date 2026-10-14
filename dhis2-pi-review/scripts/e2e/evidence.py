"""Evidence skeleton for the end-to-end test -> <work_dir>/E2E-TEST.md (called by check.py; re-run after shots.py).

Fills in what the files know: the result table (engine check before/after, scenario checks, import and read-back),
the PIs still failing grouped by error, the API call sequence from api-log.jsonl (consecutive identical calls
collapsed), the scenario table and the screenshots. Leaves <!-- TODO --> markers for what needs judgement: the causes
of the remaining failures, DHIS2 behaviours confirmed, the summary. Never overwrites a file without --force when it
no longer looks like a skeleton (i.e. once the TODO markers are gone).

Inputs : results.json, fixes.json, e2e-steps.jsonl, api-log.jsonl, syntax-check.json (optional), shots/captions.json
Config : title, dhis2_version, exported, test_period, program, work_dir.
"""
import collections, datetime as dt, json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config  # noqa: E402
import e2elib as E  # noqa: E402

TODO = '<!-- TODO'


def jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def call_key(c):
    path, _, q = c['path'].partition('?')
    path = re.sub(r'/[A-Za-z][A-Za-z0-9]{10}(?=/|$)', '/<uid>', path)
    path = re.sub(r'/system/tasks/.*', '/system/tasks/…', path)
    keep = [p for p in q.split('&') if p.split('=')[0] in ('importStrategy', 'atomicMode', 'validationMode', 'async', 'lastYears')]
    return f"{c['method']} {path}{'?' + '&'.join(keep) if keep else ''}"


def call_sequence(api):
    seq = []
    for c in api:
        k = call_key(c)
        if seq and seq[-1]['call'] == k:
            seq[-1]['n'] += 1
            seq[-1]['status'][c['status']] += 1
        else:
            seq.append({'call': k, 'n': 1, 'status': collections.Counter([c['status']]), 'first': c['t']})
    return seq


def syntax_summary(cfg):
    p = os.path.join(cfg.work_dir, 'syntax-check.json')
    if not os.path.exists(p):
        return None
    j = E.jload(p)
    rows = []
    if isinstance(j, dict) and ('pis' in j or 'mappings' in j):     # fix/syntax_check.py: {pis|mappings: {id: {field: result}}}
        for grp in ('pis', 'mappings'):
            for fields in (j.get(grp) or {}).values():
                rows += [r for r in fields.values() if isinstance(r, dict)]
    else:
        rows = j if isinstance(j, list) else (j.get('results') or list(j.values()))
    bad = [r for r in rows if isinstance(r, dict) and (r.get('ok') is False or (r.get('status') not in (None, 'OK')))]
    return len(rows), len(bad)


def md_cell(v):
    return str(v).replace('|', '\\|').replace('\n', ' ')


def build(cfg):
    R = E.jload(cfg.out('results.json'), {})
    F = E.fixes(cfg)
    steps = jsonl(cfg.out('e2e-steps.jsonl'))
    api = jsonl(cfg.out('api-log.jsonl'))
    eb, ea = R.get('engine', {}).get('before', {}), R.get('engine', {}).get('after', {})
    sc = R.get('scenarios', [])
    last = lambda name: next((s for s in reversed(steps) if s['step'] == name), {})
    imp, rb, mp = last('import programIndicators'), last('read-back'), last('patch categoryMappings')
    syn = syntax_summary(cfg)
    affected = E.jload(cfg.out('affected-ids.json'), [])
    npis, nmaps = len(F['pis']), len(F['mappings'])
    L = []
    w = L.append
    w(f'# {cfg.title}: program indicator fixes, end-to-end test\n')
    w(f'**Date:** {dt.date.today().isoformat()}  ')
    w(f'**Instance:** DHIS2 {cfg.dhis2_version or "<!-- TODO version -->"}, disposable test instance, loaded with the metadata export'
      f'{" of " + cfg.exported if cfg.exported else ""} (program `{cfg.program_id}`).  ')
    w(f'**What was tested:** the exact payload offered for approval: `{E.PI_FILE}` ({npis} program indicators)'
      + (f' and `{E.PATCH_FILE}` ({nmaps} category mapping{"s" if nmaps != 1 else ""}).' if nmaps else '.') + '\n')
    w('## Result\n')
    w('| Check | Before fix | After fix |\n|---|---|---|')
    if syn:
        w(f'| Expressions and filters accepted by the server (`/api/programIndicators/{{expression,filter}}/description`) | n/a | {syn[0]} texts, {syn[1]} failures |')
    w(f"| Affected indicators queried in analytics | {eb.get('queried', '–')} | {ea.get('queried', '–')} |")
    w(f"| …of which fail in analytics | {eb.get('errors', '–')} | {ea.get('errors', '–')} |")
    for k, lbl in (('compared', 'Totals equal to the independent evaluator'),):
        w(f"| {lbl} | {eb.get(k, 0) - len(eb.get('totalMismatch', []))}/{eb.get(k, 0)} | {ea.get(k, 0) - len(ea.get('totalMismatch', []))}/{ea.get(k, 0)} |")
    w(f"| Breakdown tables (all cells) equal to the independent evaluator | {eb.get('cellSets', 0) - len(eb.get('cellMismatch', []))}/{eb.get('cellSets', 0)}"
      f" | {ea.get('cellSets', 0) - len(ea.get('cellMismatch', []))}/{ea.get('cellSets', 0)} |")
    nb = sum(1 for s in sc if s['gotBefore'] is not None and E.same_value(s['gotBefore'], s['expectBefore']))
    na = sum(1 for s in sc if s['gotAfter'] is not None and E.same_value(s['gotAfter'], s['expectAfter']))
    w(f'| Hand-written scenario checks | expected “before” value in {nb}/{len(sc)} | expected “after” value in {na}/{len(sc)} |')
    if imp:
        st = imp.get('stats') or {}
        w(f"| Import of the payload | | HTTP {imp.get('http')}, {st.get('updated', '?')} updated, {len(imp.get('errors') or [])} errors"
          + (f"; mapping patch HTTP {mp.get('http')}" if mp else '') + ' |')
    if rb:
        w(f"| Read-back of every changed field | | {len(rb.get('pi_diffs') or [])} PI differences, {len(rb.get('mapping_diffs') or [])} mapping differences |")
    w('')
    w(f'“Affected” = the {npis} indicators changed directly plus every indicator whose breakdown changes through a fixed mapping ({len(affected)} in total).\n')
    if ea.get('errorByPi'):
        w(f"The {ea['errors']} indicators that still fail after the fixes:\n")
        by = collections.defaultdict(list)
        for k, e in ea['errorByPi'].items():
            by[e].append(k)
        for e, ks in sorted(by.items(), key=lambda x: -len(x[1])):
            w(f'- {md_cell(e)}: {", ".join(sorted(ks))}')
        w('\n<!-- TODO: explain each remaining failure (open topic / not proposed), so a reader can see none is caused by the fixes. -->\n')
    if ea.get('totalMismatch') or ea.get('cellMismatch'):
        w(f"Differences from the independent evaluator after the fix: totals {', '.join(ea.get('totalMismatch', [])) or 'none'};"
          f" cells {', '.join(ea.get('cellMismatch', [])) or 'none'}. <!-- TODO: explain or fix before approval. -->\n")
    w('## Method\n')
    w('Two independent checks:\n')
    w('1. **Engine check:** every affected indicator total and breakdown cell from DHIS2 analytics is compared with a value computed '
      'directly from the raw test records by an independent evaluator. This proves analytics computes what the new definition says.')
    w('2. **Intent check:** small hand-written scenarios, each in its own org unit, with the value expected before and after the fix '
      'worked out by hand from what the indicator is meant to count. This proves the new definition counts what the indicator name says.\n')
    src = R.get('truthSources') or {}
    w(f"Test data: {R.get('truthPersons', 0):,} test clients ({', '.join(f'{k}: {v:,}' for k, v in src.items())}).\n")
    w('### Sequence of API calls\n')
    w(f'All write calls and their responses are in `api-log.jsonl` ({len(api)} calls); step summaries in `e2e-steps.jsonl`.\n')
    w('| # | Call | Count | HTTP status |\n|---|---|---|---|')
    for i, c in enumerate(call_sequence(api), 1):
        w(f"| {i} | `{md_cell(c['call'])}` | {c['n']} | {', '.join(f'{k}×{v}' for k, v in sorted(c['status'].items()))} |")
    w('\n<!-- TODO: if the sequence is long, keep this table in an appendix and summarise it by step (revert, data, analytics before, apply, read-back, analytics after). -->\n')
    w('## Scenarios\n')
    w('| Scenario | Indicator | Fix tested | Before (expected) | After (expected) | |\n|---|---|---|---|---|---|')
    for s in sc:
        w(f"| {md_cell(s['title'])} | `{md_cell(s['code'])}` | {md_cell(s['group'])} | {s['gotBefore']} ({s['expectBefore']}) | {s['gotAfter']} ({s['expectAfter']}) | {'Pass' if s['pass'] else '**Fail**'} |")
    w('\nNotes per scenario are in `scenario-checks.json` and in each group of the approval page.\n')
    w('## DHIS2 behaviour confirmed while testing\n')
    w('<!-- TODO: behaviours verified with probe indicators or scenarios during this run (and the DHIS2 version), or "none new". -->\n')
    shots = E.jload(cfg.out('shots', 'captions.json'), {})
    if shots:
        w('## Screenshots\n')
        for name, cap in shots.items():
            w(f'**{cap}**\n\n![{cap}](shots/{name}.png)\n')
    w('## Files\n')
    w(f'- `{E.PI_FILE}`, `{E.PATCH_FILE}`: what was tested; `fixes.json`: before/after per item')
    w('- `api-log.jsonl`, `e2e-steps.jsonl`, `analytics-before/after.json`, `scenario-before/after.json`, `results.json`: evidence\n')
    w('## Re-running\n')
    w('`run_e2e.py revert`, `scen.py`, `run_e2e.py reload-scenarios`, `run_e2e.py before`, `run_e2e.py apply`, `run_e2e.py after`, `check.py` '
      '(all with `--config`). <!-- TODO: state what is left on the test instance (test data, probe PIs, saved visualizations). -->')
    return '\n'.join(L) + '\n'


def write(cfg, force=False):
    path = cfg.out('E2E-TEST.md')
    if os.path.exists(path) and not force:
        with open(path) as f:
            if TODO not in f.read():
                return path + ' (kept: already edited; use evidence.py --force to regenerate)'
    with open(path, 'w') as f:
        f.write(build(cfg))
    return path


if __name__ == '__main__':
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--force', action='store_true', help='overwrite an edited E2E-TEST.md')
    a = ap.parse_args()
    print(write(config.load(a), a.force))
