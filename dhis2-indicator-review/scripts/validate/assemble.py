"""Merge lint, server syntax, runtime and engine-test results into one status per item, a findings table and a
skeleton write-up. Type-neutral: what is specific to the metadata type comes from its adapter.

    python3 scripts/validate/assemble.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS]
                                         [--manual manual-findings.json] [--overwrite-md]

Inputs (work_dir; missing optional files are skipped, so assemble can run after lint alone):
  findings.json   (type lint, required)        syntax.json  (type syntax)      analytics.json (type query)
  compare.json    (type compare)               unsat.json   (type gen)
  manual-findings.json (optional, written by the analyst after reading items by hand):
     {"findings": [{"id": "<uid>", "status": "ALWAYS_ZERO|WRONG_VALUE|REVIEW|FAILS", "category": "reversed-period-window",
                    "detail": "why"}],
      "suppress": [{"id": "<uid>", "category": "mixed-and-or-without-parens | test:zero-despite-witness | *",
                    "why": "read by hand: brackets not needed"}]}
Outputs (work_dir; <p> = the type's file prefix, e.g. pi):
  <p>-status.csv   id, code, name, type, aggregationType, status, issue_categories, analytics_total_in_test,
                   engine_check (match | mismatch | unsupported | parse | analytics-error | '')
  <p>-findings.csv id, code, name, severity (= status level), category, detail; sorted by severity
  summary.json     {"status": {id: status}, "counts": {...}, "categories": {...}, "aux": {...}}
  <P>-VALIDATION.md skeleton with the counts and TODO markers for the narrative (only written when absent, or with
                   --overwrite-md). The narrative (causes, examples, behaviours, fix order) is written by the analyst.
Statuses, worst first: FAILS (analytics error) > ALWAYS_ZERO > WRONG_VALUE > REVIEW > OK.
Rows: lint findings (adapter.lint_status), server rejections of item texts, findings of the aux objects an item uses
("uses-<check>", "uses-invalid-<aux kind>-filter"; via adapter.aux_users), analytics errors, engine-check mismatches,
generator gaps, the type's own rows (adapter.status_rows) and manual findings.
Config keys: metadata, work_dir, title, dhis2_version, exported, test_period (+ type keys).
"""
import collections as C, csv, datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, cli  # noqa: E402

PRI = {'FAILS': 0, 'ALWAYS_ZERO': 1, 'WRONG_VALUE': 2, 'REVIEW': 3}
NAMES = {0: 'FAILS', 1: 'ALWAYS_ZERO', 2: 'WRONG_VALUE', 3: 'REVIEW', 9: 'OK'}



def fmtv(v):
    """A total for a message: number, 'no value' or the raw text (e.g. NaN)."""
    if v is None:
        return 'no value'
    return f'{v:g}' if isinstance(v, (int, float)) else str(v)

def lint_status(A, f, obj):
    if hasattr(A, 'lint_status'):
        return A.lint_status(f, obj)
    st = getattr(A, 'LINT_STATUS', {})
    if f['check'] in st:
        return st[f['check']]
    return {'ERROR': 'WRONG_VALUE', 'WARN': 'REVIEW'}.get(f['severity'])


def main():
    ap = cli.add_common(cli.parser(__doc__), tag=False)
    ap.add_argument('--manual', help='manual findings file (default manual-findings.json in work_dir, if present)')
    ap.add_argument('--overwrite-md', action='store_true', help='rewrite <P>-VALIDATION.md even if it exists')
    args = ap.parse_args()
    cfg = cli.load(args)
    A = adapters.load(cfg)
    W = cfg.out
    P = {p['id']: p for p in cli.select(cfg, args.items)}
    AUX = {m['id']: m for m in (A.aux_items(cfg) if A.AUX_KIND else [])}
    lint = cli.read_json(W('findings.json'))
    syn = cli.read_json(W('syntax.json'), [])
    an = cli.read_json(W('analytics.json'), {})
    cmp_ = {x['id']: x for x in cli.read_json(W('compare.json'), [])}
    unsat = {u[0]: u[1] for u in cli.read_json(W('unsat.json'), [])}
    manual = cli.read_json(args.manual or W('manual-findings.json'), {})

    rows = []

    def add(pid, sev, cat, detail):
        if pid in P:
            rows.append((pid, sev, cat, detail))

    # lint on items
    for f in lint:
        pid = f['id']
        if pid not in P:
            continue
        sev = lint_status(A, f, P[pid])
        if sev:
            add(pid, sev, f['check'], f['detail'])
    # aux problems -> every item that uses the aux object
    info_checks = getattr(A, 'AUX_INFO_CHECKS', set())
    bad_aux = C.defaultdict(list)
    for f in lint:
        if f['id'] in AUX and (f['severity'] in ('ERROR', 'WARN') or f['check'] in info_checks):
            bad_aux[f['id']].append(f)
    bad_syntax_aux = C.defaultdict(list)
    for s in syn:
        if not s.get('ok', s.get('message') == 'Valid'):
            if s['id'] in AUX:
                bad_syntax_aux[s['id']].append(s)
            else:
                add(s['id'], 'FAILS', 'server-rejects-' + s['where'], s.get('description') or s.get('message'))
    items = list(P.values())
    users = {mid: set(A.aux_users(cfg, mid, items)) for mid in AUX}
    for pid in P:
        for mid in AUX:
            if pid not in users[mid]:
                continue
            mname = A.aux_describe(cfg, AUX[mid])['name']
            for f in bad_aux.get(mid, []):
                sev = 'WRONG_VALUE' if f['severity'] == 'ERROR' else 'REVIEW'
                add(pid, sev, 'uses-' + f['check'], f"{mname}: {f['detail']}")
            for s in bad_syntax_aux.get(mid, []):     # one bad aux text breaks every item using it
                add(pid, 'FAILS', f'uses-invalid-{A.AUX_KIND}-filter', f"{mname} [{s['where']}]: {s.get('message')}")
    # runtime
    for pid, a in an.items():
        if a.get('total_err'):
            add(pid, 'FAILS', 'analytics-error', a['total_err'])
        elif a.get('cells_err'):
            add(pid, 'FAILS', 'analytics-error-disaggregated', a['cells_err'])
    # type-specific rows (e.g. breakdown behaviours of the engine check)
    if hasattr(A, 'status_rows'):
        for row in A.status_rows(cfg, P, {'lint': lint, 'syntax': syn, 'analytics': an, 'compare': cmp_, 'unsat': unsat}):
            add(*row)
    # engine test
    for pid, x in cmp_.items():
        if x.get('status') != 'compared':
            continue
        if not x['total_ok'] or x.get('cells_ok') is False:
            add(pid, 'REVIEW', 'test:engine-mismatch',
                f"analytics {fmtv(x.get('analytics_total'))} vs evaluator {fmtv(x.get('expected_total'))}" + (f"; {x.get('n_cell_diffs')} cells differ" if x.get('cells_ok') is False else '')
                + ": read by hand (unmodelled DHIS2 behaviour or evaluator gap)")
    hard = {r[0] for r in rows if PRI[r[1]] <= 1}
    for pid in P:
        if pid in unsat:
            add(pid, 'REVIEW', 'test:generator-could-not-satisfy', unsat[pid])
        elif pid in an and an[pid].get('total') == 0 and pid not in hard and cmp_.get(pid, {}).get('status') == 'compared':
            add(pid, 'REVIEW', 'test:zero-despite-witness', 'a witness was generated for the definition but analytics counts 0: read it by hand')
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
    write_csvs(cfg, A, P, rows, by_pi, status, cmp_)
    counts = C.Counter(NAMES[s] for s in status.values())
    cats = C.Counter(c for rs in by_pi.values() for c in {r[2] for r in rs})
    aux_err = sorted({f['id'] for f in lint if f['id'] in AUX and f['severity'] == 'ERROR'})
    unused = sorted(m for m in AUX if not users[m])
    summary = {'status': {k: NAMES[v] for k, v in status.items()}, 'counts': {k: counts.get(k, 0) for k in list(PRI) + ['OK']},
               'categories': dict(cats.most_common()), 'aux': {'kind': A.AUX_KIND, 'total': len(AUX), 'withErrors': aux_err, 'unused': unused}}
    cli.write_json(W('summary.json'), summary)
    print({k: counts.get(k, 0) for k in list(PRI) + ['OK']})
    for k, v in cats.most_common(25):
        print(f'{v:5} {k}')
    md = W(adapters.out_name(A, 'VALIDATION.md'))
    if args.overwrite_md or not os.path.exists(md):
        write_md(cfg, A, md, P, summary, rows, lint, syn, an, cmp_, unsat)
        print('->', md)
    else:
        print(f'{md} exists: not overwritten (use --overwrite-md)')
    print('->', W(adapters.out_name(A, 'status.csv')), W(adapters.out_name(A, 'findings.csv')), W('summary.json'))


def write_csvs(cfg, A, P, rows, by_pi, status, cmp_):
    with open(cfg.out(adapters.out_name(A, 'status.csv')), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'code', 'name', 'type', 'aggregationType', 'status', 'issue_categories', 'analytics_total_in_test', 'engine_check'])
        for pid, p in sorted(P.items(), key=lambda kv: (kv[1].get('code') or '', kv[1].get('name') or '')):
            x = cmp_.get(pid, {})
            eng = x.get('status', '')
            if eng == 'compared':
                eng = 'match' if x['total_ok'] and x.get('cells_ok', True) else 'mismatch'
            dsc = A.describe(cfg, p)
            w.writerow([pid, p.get('code'), p.get('name'), dsc.get('type'), dsc.get('agg'), NAMES[status[pid]],
                        ';'.join(sorted({r[2] for r in by_pi.get(pid, [])})), x.get('analytics_total', ''), eng])
    with open(cfg.out(adapters.out_name(A, 'findings.csv')), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'code', 'name', 'severity', 'category', 'detail'])
        seen = set()
        for pid, sev, cat, det in sorted(rows, key=lambda r: (PRI[r[1]], P[r[0]].get('code') or '', r[2])):
            if (pid, cat, det) in seen:
                continue
            seen.add((pid, cat, det))
            w.writerow([pid, P[pid].get('code'), P[pid].get('name'), sev, cat, det])


def write_md(cfg, A, path, P, summary, rows, lint, syn, an, cmp_, unsat):
    c = summary['counts']
    L_ = A.LABELS
    S, Ss, I, Is = L_['short'], L_['shorts'], L_['item'], L_['items']
    aux = summary['aux']
    truth = cli.read_json(cfg.out('truth.json'), [])
    compared = [x for x in cmp_.values() if x.get('status') == 'compared']
    dis = [x for x in compared if 'cells_ok' in x]
    per_status = C.defaultdict(C.Counter)
    for pid, sev, cat, _ in rows:
        per_status[sev][cat] += 1
    status_csv, findings_csv = adapters.out_name(A, 'status.csv'), adapters.out_name(A, 'findings.csv')
    L = [f"# {cfg.title}: {I} validation", '',
         f"**Scope:** {A.scope_text(cfg, len(P), aux['total'])}, from the export of {cfg.exported or '<!-- TODO: date -->'}.",
         f"**Test instance:** DHIS2 {cfg.dhis2_version or '<!-- TODO: version -->'} with only this metadata and synthetic data in {cfg.test_period}.",
         f"**Date:** {dt.date.today().isoformat()}.", '',
         f'Per-{S} results: `{status_csv}` (one row per {S}) and `{findings_csv}` (one row per issue).', '',
         '## Summary', '', f'| Status | {Ss} | Meaning |', '|---|---:|---|',
         f"| **FAILS** | {c['FAILS']} | Analytics returns an error for the {S}; it shows nothing in dashboards or reports. |",
         f"| **ALWAYS_ZERO** | {c['ALWAYS_ZERO']} | Runs, but can never count anything. |",
         f"| **WRONG_VALUE** | {c['WRONG_VALUE']} | Runs, but counts the wrong records or loses them in the breakdown. |",
         f"| **REVIEW** | {c['REVIEW']} | Probably a problem; depends on intent or on real data. |",
         f"| OK | {c['OK']} | No issue found by any check. |", '']
    if A.AUX_KIND:
        L += [f"Of the {aux['total']} {L_['auxs']}, {len(aux['withErrors'])} with defects, {len(aux['unused'])} unused.", '']
    if compared:
        L += [f"Engine check: of {len(compared)} {Ss} that run and could be evaluated, "
              f"{sum(x['total_ok'] for x in compared)} totals matched the independent recomputation exactly"
              + (f"; {sum(x['cells_ok'] for x in dis)} of {len(dis)} breakdown tables matched" if dis else '') + '.',
              '<!-- TODO: explain every mismatch (DHIS2 behaviour or evaluator gap) and state the conclusion: are the problems in the configuration or the engine? -->', '']
    truth_txt = A.truth_text(truth) if hasattr(A, 'truth_text') else f'{len(truth)} generated records'
    L += ['## How it was validated', '',
          f"1. **Static lint** ({len({f['check'] for f in lint})} checks fired, {len(lint)} findings) of every expression"
          + (f" and every {L_['aux']}" if A.AUX_KIND else '') + ' against the metadata.',
          f"2. **Server syntax validation** of {len(syn)} texts; {sum(1 for s in syn if not s.get('ok'))} rejected." if syn else '2. **Server syntax validation**: <!-- TODO: not run -->',
          f"3. **Runtime check**: {len(an)} {Ss} queried; {sum(1 for a in an.values() if a.get('total_err'))} errors on the total, "
          f"{sum(1 for a in an.values() if a.get('cells_err'))} on the breakdown." if an else '3. **Runtime check**: <!-- TODO: not run -->',
          f"4. **Engine and intent test with data**: {truth_txt} "
          f"(one witness per {S}, noise, out-of-period controls); {len(unsat)} definitions the generator could not satisfy." if truth else
          '4. **Engine and intent test with data**: <!-- TODO: not run -->',
          f'<!-- TODO: anything specific (field resolution used for bands, cohort query periods, {Ss} read by hand) -->', '',
          '## Findings by category', '']
    for i, st in enumerate(PRI):
        L += [f"### {'ABCD'[i]}. {st} ({c[st]})", '', f'| Category | {Ss} |', '|---|---:|']
        L += [f'| `{k}` | {v} |' for k, v in per_status[st].most_common()]
        L += ['', '<!-- TODO: group into causes; per cause: plain explanation, count, examples (codes) -->', '']
    L += ['## DHIS2 behaviours this configuration trips over', '',
          f'<!-- TODO: list the behaviours (references/{A.TYPE}/behaviours.md ids) that explain findings or mismatches -->', '',
          '## Recommended fix order', '', '<!-- TODO -->', '', '## Limitations', '',
          f'<!-- TODO: what the synthetic data cannot show; intent-dependent findings; {Ss} not evaluated -->', '']
    with open(path, 'w') as f:
        f.write('\n'.join(L))


if __name__ == '__main__':
    main()
