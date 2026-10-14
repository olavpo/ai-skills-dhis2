"""Merge lint, server syntax, runtime and engine-test results into one status per PI, a findings table and a
skeleton write-up.

    python3 scripts/validate/assemble.py [--config pireview.json] [--metadata OVERLAY ...] [--pis IDS]
                                         [--manual manual-findings.json] [--overwrite-md]

Inputs (work_dir; missing optional files are skipped, so assemble can run after lint alone):
  findings.json   (lint.py, required)          syntax.json  (syntax.py)        analytics.json (query.py)
  compare.json    (compare.py)                 unsat.json   (gen.py)
  manual-findings.json (optional, written by the analyst after reading PIs by hand):
     {"findings": [{"id": "<pi>", "status": "ALWAYS_ZERO|WRONG_VALUE|REVIEW|FAILS", "category": "reversed-period-window",
                    "detail": "why"}],
      "suppress": [{"id": "<pi>", "category": "mixed-and-or-without-parens | test:zero-despite-witness | *",
                    "why": "read by hand: brackets not needed"}]}
Outputs (work_dir):
  pi-status.csv    id, code, name, analyticsType, aggregationType, status, issue_categories, analytics_total_in_test,
                   engine_check (match | mismatch | unsupported | parse | analytics-error | '')
  pi-findings.csv  id, code, name, severity (= status level), category, detail; sorted by severity
  summary.json     {"status": {pi: status}, "counts": {...}, "categories": {...}, "mappings": {...}}
  PI-VALIDATION.md skeleton with the counts and TODO markers for the narrative (only written when absent, or with
                   --overwrite-md). The narrative (causes, examples, behaviours, fix order) is written by the analyst.
Statuses, worst first: FAILS (analytics error) > ALWAYS_ZERO > WRONG_VALUE > REVIEW > OK.
Config keys: metadata, program, work_dir, title, dhis2_version, exported, test_period.
"""
import collections as C, csv, datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config  # noqa: E401
import common

PRI = {'FAILS': 0, 'ALWAYS_ZERO': 1, 'WRONG_VALUE': 2, 'REVIEW': 3}
NAMES = {0: 'FAILS', 1: 'ALWAYS_ZERO', 2: 'WRONG_VALUE', 3: 'REVIEW', 9: 'OK'}
# lint check -> status (None: decided below / not a status); other ERROR -> WRONG_VALUE, WARN -> REVIEW, INFO ignored
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


def main():
    ap = common.add_common(common.parser(__doc__), tag=False)
    ap.add_argument('--manual', help='manual findings file (default manual-findings.json in work_dir, if present)')
    ap.add_argument('--overwrite-md', action='store_true', help='rewrite PI-VALIDATION.md even if it exists')
    args = ap.parse_args()
    cfg = common.load(args)
    W = cfg.out
    P = {p['id']: p for p in common.select_pis(cfg, args.pis)}
    MAP = {m['id']: m for m in cfg.program.get('categoryMappings') or []}
    lint = common.read_json(W('findings.json'))
    syn = common.read_json(W('syntax.json'), [])
    an = common.read_json(W('analytics.json'), {})
    cmp_ = {x['id']: x for x in common.read_json(W('compare.json'), [])}
    unsat = {u[0]: u[1] for u in common.read_json(W('unsat.json'), [])}
    manual = common.read_json(args.manual or W('manual-findings.json'), {})

    rows = []

    def add(pid, sev, cat, detail):
        if pid in P:
            rows.append((pid, sev, cat, detail))

    # lint on PIs
    for f in lint:
        pid, chk = f['id'], f['check']
        if pid not in P:
            continue
        if chk == 'contains-literal-not-an-option-code':
            sev = 'ALWAYS_ZERO' if f['severity'] == 'ERROR' and '||' not in (P[pid].get('filter') or '') else 'WRONG_VALUE'
        elif chk in LINT_STATUS:
            sev = LINT_STATUS[chk]
        else:
            sev = {'ERROR': 'WRONG_VALUE', 'WARN': 'REVIEW'}.get(f['severity'])
        if chk == 'boundaries-missing' and f['severity'] == 'WARN':
            sev = 'REVIEW'
        if sev:
            add(pid, sev, chk, f['detail'])
    # mapping problems -> every PI that uses the mapping
    bad_maps = C.defaultdict(list)
    for f in lint:
        if f['id'] in MAP and (f['severity'] in ('ERROR', 'WARN') or f['check'] == 'mapping-option-values-unmapped'):
            bad_maps[f['id']].append(f)
    bad_syntax_maps = C.defaultdict(list)
    for s in syn:
        if not s.get('ok', s.get('message') == 'Valid'):
            if s['id'] in MAP:
                bad_syntax_maps[s['id']].append(s)
            else:
                add(s['id'], 'FAILS', 'server-rejects-' + s['where'], s.get('description') or s.get('message'))
    users = C.defaultdict(list)
    for pid, p in P.items():
        for mid in p.get('categoryMappingIds') or []:
            if not mid:
                continue
            users[mid].append(pid)
            mname = MAP.get(mid, {}).get('mappingName', mid)
            for f in bad_maps.get(mid, []):
                sev = 'WRONG_VALUE' if f['severity'] == 'ERROR' else 'REVIEW'
                add(pid, sev, 'uses-' + f['check'], f"{mname}: {f['detail']}")
            for s in bad_syntax_maps.get(mid, []):     # E6: one bad mapping filter breaks every PI using it
                add(pid, 'FAILS', 'uses-invalid-mapping-filter', f"{mname} [{s['where']}]: {s.get('message')}")
    # runtime
    for pid, a in an.items():
        if a.get('total_err'):
            add(pid, 'FAILS', 'analytics-error', a['total_err'])
        elif a.get('cells_err'):
            add(pid, 'FAILS', 'analytics-error-disaggregated', a['cells_err'])
    # engine test
    for pid, x in cmp_.items():
        if x.get('status') != 'compared':
            continue
        if x['type'] == 'EVENT' and x['expected_total_ignoring_disagg'] > x['analytics_total']:
            add(pid, 'REVIEW', 'test:total-loses-undisaggregatable-records',
                f"test data: {x['expected_total_ignoring_disagg']:g} records pass the filter, analytics total is {x['analytics_total']:g} (records outside the mapping cells are dropped, C1)")
        if x['type'] == 'ENROLLMENT' and x.get('cells_ok') and x.get('cells_sum_equals_total') is False:
            add(pid, 'WRONG_VALUE', 'test:cells-do-not-add-up', f"test data: total {x['analytics_total']:g}, cells sum to {x.get('sum_cells_analytics', 0):g} (C2)")
        if not x['total_ok'] or x.get('cells_ok') is False:
            add(pid, 'REVIEW', 'test:engine-mismatch',
                f"analytics {x['analytics_total']:g} vs evaluator {x['expected_total']:g}" + (f"; {x.get('n_cell_diffs')} cells differ" if x.get('cells_ok') is False else '')
                + ": read by hand (unmodelled DHIS2 behaviour or evaluator gap)")
    hard = {r[0] for r in rows if PRI[r[1]] <= 1}
    for pid in P:
        if pid in unsat:
            add(pid, 'REVIEW', 'test:generator-could-not-satisfy', unsat[pid])
        elif pid in an and an[pid].get('total') == 0 and pid not in hard and cmp_.get(pid, {}).get('status') == 'compared':
            add(pid, 'REVIEW', 'test:zero-despite-witness', 'a witness was generated for the filter but analytics counts 0: read the filter by hand')
    # manual findings and suppressions
    for f in manual.get('findings', []):
        if f.get('status') in PRI:
            add(f['id'], f['status'], f.get('category', 'manual'), f.get('detail', ''))
    sup = {(s['id'], s.get('category') or s.get('check') or '*') for s in manual.get('suppress', [])}
    rows = [r for r in rows if (r[0], r[2]) not in sup and (r[0], '*') not in sup]

    by_pi = C.defaultdict(list)
    for r in rows:
        by_pi[r[0]].append(r)
    status = {pid: min((PRI[r[1]] for r in by_pi.get(pid, [])), default=9) for pid in P}
    write_csvs(cfg, P, rows, by_pi, status, cmp_)
    counts = C.Counter(NAMES[s] for s in status.values())
    cats = C.Counter(c for rs in by_pi.values() for c in {r[2] for r in rs})
    map_err = sorted({f['id'] for f in lint if f['id'] in MAP and f['severity'] == 'ERROR'})
    unused = sorted(m for m in MAP if m not in users)
    summary = {'status': {k: NAMES[v] for k, v in status.items()}, 'counts': {k: counts.get(k, 0) for k in list(PRI) + ['OK']},
               'categories': dict(cats.most_common()), 'mappings': {'total': len(MAP), 'withErrors': map_err, 'unused': unused}}
    common.write_json(W('summary.json'), summary)
    print({k: counts.get(k, 0) for k in list(PRI) + ['OK']})
    for k, v in cats.most_common(25):
        print(f'{v:5} {k}')
    md = W('PI-VALIDATION.md')
    if args.overwrite_md or not os.path.exists(md):
        write_md(cfg, md, P, summary, rows, lint, syn, an, cmp_, unsat)
        print('->', md)
    else:
        print(f'{md} exists: not overwritten (use --overwrite-md)')
    print('->', W('pi-status.csv'), W('pi-findings.csv'), W('summary.json'))


def write_csvs(cfg, P, rows, by_pi, status, cmp_):
    with open(cfg.out('pi-status.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'code', 'name', 'analyticsType', 'aggregationType', 'status', 'issue_categories', 'analytics_total_in_test', 'engine_check'])
        for pid, p in sorted(P.items(), key=lambda kv: (kv[1].get('code') or '', kv[1].get('name') or '')):
            x = cmp_.get(pid, {})
            eng = x.get('status', '')
            if eng == 'compared':
                eng = 'match' if x['total_ok'] and x.get('cells_ok', True) else 'mismatch'
            w.writerow([pid, p.get('code'), p.get('name'), p.get('analyticsType'), p.get('aggregationType'), NAMES[status[pid]],
                        ';'.join(sorted({r[2] for r in by_pi.get(pid, [])})), x.get('analytics_total', ''), eng])
    with open(cfg.out('pi-findings.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'code', 'name', 'severity', 'category', 'detail'])
        seen = set()
        for pid, sev, cat, det in sorted(rows, key=lambda r: (PRI[r[1]], P[r[0]].get('code') or '', r[2])):
            if (pid, cat, det) in seen:
                continue
            seen.add((pid, cat, det))
            w.writerow([pid, P[pid].get('code'), P[pid].get('name'), sev, cat, det])


def write_md(cfg, path, P, summary, rows, lint, syn, an, cmp_, unsat):
    c = summary['counts']
    truth = common.read_json(cfg.out('truth.json'), [])
    compared = [x for x in cmp_.values() if x.get('status') == 'compared']
    dis = [x for x in compared if 'cells_ok' in x]
    per_status = C.defaultdict(C.Counter)
    for pid, sev, cat, _ in rows:
        per_status[sev][cat] += 1
    L = [f"# {cfg.title}: program indicator validation", '',
         f"**Scope:** {len(P)} program indicators and {summary['mappings']['total']} category mappings of program "
         f"`{cfg.program_id}` ({cfg.program.get('name', '')}), from the export of {cfg.exported or '<!-- TODO: date -->'}.",
         f"**Test instance:** DHIS2 {cfg.dhis2_version or '<!-- TODO: version -->'} with only this metadata and synthetic data in {cfg.test_period}.",
         f"**Date:** {dt.date.today().isoformat()}.", '',
         'Per-PI results: `pi-status.csv` (one row per PI) and `pi-findings.csv` (one row per issue).', '',
         '## Summary', '', '| Status | PIs | Meaning |', '|---|---:|---|',
         f"| **FAILS** | {c['FAILS']} | Analytics returns an error for the PI; it shows nothing in dashboards or reports. |",
         f"| **ALWAYS_ZERO** | {c['ALWAYS_ZERO']} | Runs, but can never count anything. |",
         f"| **WRONG_VALUE** | {c['WRONG_VALUE']} | Runs, but counts the wrong records or loses them in the breakdown. |",
         f"| **REVIEW** | {c['REVIEW']} | Probably a problem; depends on intent or on real data. |",
         f"| OK | {c['OK']} | No issue found by any check. |", '',
         f"Of the {summary['mappings']['total']} category mappings, {len(summary['mappings']['withErrors'])} with defects, "
         f"{len(summary['mappings']['unused'])} unused.", '']
    if compared:
        L += [f"Engine check: of {len(compared)} PIs that run and could be evaluated, "
              f"{sum(x['total_ok'] for x in compared)} totals matched the independent recomputation exactly"
              + (f"; {sum(x['cells_ok'] for x in dis)} of {len(dis)} breakdown tables matched" if dis else '') + '.',
              '<!-- TODO: explain every mismatch (DHIS2 behaviour or evaluator gap) and state the conclusion: are the problems in the configuration or the engine? -->', '']
    L += ['## How it was validated', '',
          f"1. **Static lint** ({len({f['check'] for f in lint})} checks fired, {len(lint)} findings) of every expression, filter and mapping filter against the metadata.",
          f"2. **Server syntax validation** of {len(syn)} texts; {sum(1 for s in syn if not s.get('ok'))} rejected." if syn else '2. **Server syntax validation**: <!-- TODO: not run -->',
          f"3. **Runtime check**: {len(an)} PIs queried; {sum(1 for a in an.values() if a.get('total_err'))} errors on the total, "
          f"{sum(1 for a in an.values() if a.get('cells_err'))} on the breakdown." if an else '3. **Runtime check**: <!-- TODO: not run -->',
          f"4. **Engine and intent test with data**: {len(truth)} generated people, {sum(len(t['events']) for t in truth)} events "
          f"(one witness per PI, noise, out-of-period controls); {len(unsat)} filters the generator could not satisfy." if truth else
          '4. **Engine and intent test with data**: <!-- TODO: not run -->',
          '<!-- TODO: anything specific (field resolution used for bands, cohort query periods, PIs read by hand) -->', '',
          '## Findings by category', '']
    for i, st in enumerate(PRI):
        L += [f"### {'ABCD'[i]}. {st} ({c[st]})", '', '| Category | PIs |', '|---|---:|']
        L += [f'| `{k}` | {v} |' for k, v in per_status[st].most_common()]
        L += ['', f'<!-- TODO: group into causes; per cause: plain explanation, count, examples (codes) -->', '']
    L += ['## DHIS2 behaviours this configuration trips over', '',
          '<!-- TODO: list the behaviours (references/dhis2-behaviours.md ids) that explain findings or mismatches -->', '',
          '## Recommended fix order', '', '<!-- TODO -->', '', '## Limitations', '',
          '<!-- TODO: what the synthetic data cannot show; intent-dependent findings; PIs not evaluated -->', '']
    with open(path, 'w') as f:
        f.write('\n'.join(L))


if __name__ == '__main__':
    main()
