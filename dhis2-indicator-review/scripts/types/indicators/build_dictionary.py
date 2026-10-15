"""Plain-language dictionary of the aggregate indicators in scope: numerator and denominator rendered with names.

    python3 scripts/types/indicators/build_dictionary.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS]

Each side is rendered term by term when it is a sum or difference of operands ("ANC 1st visit: Fixed, Outreach");
any other shape (products, divisions, functions) is shown as the expression with names in place of uids. Every row
also lists the data elements read with their aggregation type, period type and data sets, the indicator type and
factor, annualization and decimals, the indicator's own descriptions, and the lint findings and test status when
they exist (findings.json, indicator-status.csv). Offline.

Output: <work_dir>/dictionary.json {"title", "scope", "rows": [{"id", "code", "name", "type", "factor", "annualized",
        "decimals", "numerator": {"text", "terms": [{"sign", "what", "detail"}], "expression"}, "denominator": {...},
        "dataElements": [{"id", "name", "aggregationType", "periodType", "dataSets"}], "description",
        "numeratorDescription", "denominatorDescription", "status", "findings": [check]}]}
Then assemble_dict.py renders <work_dir>/indicator-dictionary.html. Config keys: metadata, work_dir, title, indicators.
"""
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import indexpr as X  # noqa: E402
import adapter as A  # noqa: E402


def main():
    ap = IC.add_common(IC.parser(__doc__), tag=False)
    args = ap.parse_args()
    cfg = IC.load(args)
    M = IC.Meta(cfg.md)
    inds = IC.select(cfg, args.items)
    findings = {}
    for f in IC.read_json(cfg.out('findings.json'), []):
        findings.setdefault(f['id'], []).append(f['check'])
    status = {}
    p = cfg.out('indicator-status.csv')
    if os.path.exists(p):
        with open(p) as fh:
            status = {r['id']: r['status'] for r in csv.DictReader(fh)}
    rows = []
    for i in sorted(inds, key=lambda x: x.get('name') or ''):
        des = sorted(A.referenced_des(i) & set(M.de))
        rows.append({'id': i['id'], 'code': i.get('code') or '', 'name': i.get('name'), 'type': A.type_text(cfg, i),
                     'factor': M.factor(i), 'annualized': bool(i.get('annualized')), 'decimals': i.get('decimals'),
                     'numerator': side(cfg, M, i.get('numerator')), 'denominator': side(cfg, M, i.get('denominator')),
                     'dataElements': [{'id': d, 'name': M.name('de', d), 'aggregationType': M.de[d].get('aggregationType'),
                                       'periodType': M.de_period_type(d) if M.de_ds.get(d) else None,
                                       'dataSets': [M.ds[x].get('name') for x in M.de_ds.get(d, [])]} for d in des],
                     'description': i.get('description') or '', 'numeratorDescription': i.get('numeratorDescription') or '',
                     'denominatorDescription': i.get('denominatorDescription') or '',
                     'status': status.get(i['id'], ''), 'findings': sorted(set(findings.get(i['id'], [])))})
    out = {'title': cfg.title, 'scope': A.scope_text(cfg, len(inds)), 'rows': rows}
    IC.write_json(cfg.out('dictionary.json'), out)
    print(f'{len(rows)} indicators -> {cfg.out("dictionary.json")}')


def side(cfg, M, text):
    text = (text or '').strip()
    rec = {'text': '', 'terms': [], 'expression': A.named(cfg, text)}
    if text in ('1', '1.0'):
        rec['text'] = '1 (a count, no denominator)'
        return rec
    try:
        ast = X.parse(text)
    except (X.ParseError, X.Unsupported):
        rec['text'] = rec['expression']
        return rec
    terms = additive(ast, '+')
    if terms is None:
        rec['text'] = rec['expression']
        return rec
    grouped = []                                   # consecutive cells of one data element with one sign -> one line
    for sign, n in terms:
        what, detail = term(M, n)
        if grouped and n[0] == 'de' and grouped[-1]['_de'] == n[1] and grouped[-1]['sign'] == sign and n[2] not in (None, '*'):
            grouped[-1]['detail'] += ' + ' + detail
            continue
        grouped.append({'sign': sign, 'what': what, 'detail': detail, '_de': n[1] if n[0] == 'de' and n[2] not in (None, '*') else None})
    for g in grouped:
        g.pop('_de')
    rec['terms'] = grouped
    rec['text'] = ' '.join(('' if k == 0 and g['sign'] == '+' else g['sign'] + ' ') + g['what'] + (f" ({g['detail']})" if g['detail'] else '')
                           for k, g in enumerate(grouped))
    return rec


def additive(n, sign):
    """[(sign, operand node)] for a sum/difference of operands (factors of 1 allowed), else None."""
    if n[0] == 'bin' and n[1] in ('+', '-'):
        a = additive(n[2], sign)
        b = additive(n[3], sign if n[1] == '+' else ('-' if sign == '+' else '+'))
        return None if a is None or b is None else a + b
    if n[0] == 'bin' and n[1] == '*' and n[3] == ('num', 1.0):
        return additive(n[2], sign)
    if n[0] in ('de', 'const', 'oug', 'other', 'days', 'num'):
        return [(sign, n)]
    return None


def term(M, n):
    k = n[0]
    if k == 'de':
        nm = M.name('de', n[1])
        if n[2] in (None, '*'):
            det = '' if M.de_combo(n[1]) == M.default_cc else 'all disaggregations'
        else:
            det = M.coc.get(n[2], {}).get('name', n[2] + ' (unknown option combo)')
        if n[3] not in (None, '*'):
            det += ('; ' if det else '') + f"{M.coc.get(n[3], {}).get('name', n[3])}"
        return nm, det
    if k == 'const':
        c = M.const.get(n[1]) or {}
        return f"constant {c.get('name', n[1])}", f"= {c.get('value')}" if c else 'unknown'
    if k == 'oug':
        return f"number of org units in group {M.name('oug', n[1])}", ''
    if k == 'other' and n[1] == 'R':
        ds, _, metric = n[2].partition('.')
        return f"{M.name('ds', ds)} {metric.lower().replace('_', ' ')}", '%' if metric == 'REPORTING_RATE' else ''
    if k == 'days':
        return 'days in the period', ''
    if k == 'num':
        return f'{n[1]:g}', ''
    return X.unparse(n), ''


if __name__ == '__main__':
    main()
