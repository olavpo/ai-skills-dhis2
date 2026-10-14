"""Static lint of every aggregate indicator, and of the data elements they read, against the metadata export.

    python3 scripts/types/indicators/lint.py [--config pireview.json] [--metadata OVERLAY ...] [--items IDS] [--tag TAG]

Input:  the export (cfg.metadata: indicators, indicatorTypes, data elements, category model incl. COCs, data sets,
        constants, org unit groups), plus optional overlays (e.g. proposed-indicators.json).
Output: <work_dir>/findings.json (findings-<tag>.json): list of
        {"severity": "ERROR|WARN|INFO", "check": "<id>", "id": "<indicator or data element uid>",
         "name": "<indicator name> | data element: <name>", "detail": "..."}
Findings on data elements (aux objects) carry the data element's uid; assemble marks every indicator that reads it
("uses-<check>"). Offline, no instance needed. Config keys: metadata, work_dir, indicators (scope).

Checks (id, severity, what). Ids are stable: review topics, fix patterns and assemble key on them.
  References
  parse-error                        WARN   the local parser cannot read the text (server syntax check decides)
  unknown-de                         ERROR  #{x...}: x is not a data element in the export
  unknown-coc                        ERROR  #{de.x}: x is not a category option combo in the export
  unknown-aoc                        ERROR  #{de.coc.x}: x is not a category option combo in the export
  coc-not-in-de-combo                ERROR  the COC belongs to another category combo than the data element's (or its
                                            data set override): the term reads only data stored before the change;
                                            2.43.2 refuses new values with it (E8024, A13)
  coc-not-fitting-combo              WARN   COCs attached to the data element's combo whose options don't match the
                                            combo's categories (left over from an earlier combo); one finding per side
  aoc-not-in-data-set-combo          ERROR  the attribute option combo is not one of the data set's attribute combo
  unknown-constant                   ERROR  C{x} not in the export
  unknown-org-unit-group             ERROR  OUG{x} not in the export
  unknown-indicator-type             ERROR  indicator type missing / not in the export
  legacy-reporting-rate-operand      WARN   #{dataSet.REPORTING_RATE}: old operand form; R{dataSet.REPORTING_RATE} is current
  unsupported-operand                INFO   R{}, I{}, D{}, A{}, N{}, subExpression...: not evaluated by the engine check
  Data elements read
  de-not-numeric                     ERROR  value type is not numeric/boolean (text, date, ...): never aggregates
  de-in-no-data-set                  WARN   the data element is in no data set: 2.43.2 refuses its values (E8003)
  de-domain-tracker                  WARN   a tracker data element: aggregate analytics has no values for it
  sum-data-long-period               WARN   a SUM data element entered Yearly/SixMonthly/...: no value in any shorter
                                            period (monthly, quarterly views stay empty, A7)
  numerator-zeros-dropped            INFO   numerator data elements with zeroIsSignificant=false: a reported 0 never
                                            reaches analytics, so a facility reporting 0 shows no value (A1, A3)
  (on the data element, id = data element uid; every indicator reading it gets "uses-<check>")
  de-aggregation-count               ERROR  aggregationType COUNT on a numeric data element: counts values, not their sum
  de-aggregation-none                ERROR  aggregationType NONE: not aggregated
  de-aggregation-average             WARN   aggregationType AVERAGE on a data element entered monthly or more often:
                                            parents get the MEAN of the facilities, quarters the mean of the months (A6)
  de-aggregation-other               INFO   LAST / FIRST / MIN / MAX / ... (not evaluated by the engine check)
  Expression and type
  division-in-numerator-denominator-1 WARN  numerator divides by data and the denominator is 1: NaN where the divisor
                                            is 0 (A4) instead of no value; same values elsewhere (operands are
                                            aggregated before the expression is evaluated, A5)
  inner-division                     WARN   a division by data inside the numerator or denominator (NaN at 0, A4)
  numerator-equals-denominator       ERROR  same expression on both sides: always 1 × factor
  denominator-zero                   ERROR  denominator is the literal 0
  empty-expression                   ERROR  numerator or denominator empty
  overlapping-operands               WARN   the same data counted twice on one side (#{de} with #{de.coc}, or a term twice)
  factor-100-and-times-100           ERROR  indicator type factor 100 and the numerator multiplied by 100 again
  percent-name-number-type           ERROR  the name says %, percent, coverage, proportion or rate but the factor is 1
                                            and nothing multiplies by 100
  count-name-percent-type            ERROR  denominator 1 (a count) with a factor other than 1
  rate-name-denominator-1            WARN   the name says rate/coverage/% but the denominator is 1 (not a rate)
  name-factor-mismatch               ERROR  the name says "per 1000 / 10 000 / 100 000" and the factor differs
  explicit-100-number-type           INFO   Number type with an explicit *100 (works; a Percent type is clearer)
  annualized-without-population      WARN   annualized, but the denominator is not an annual figure (no yearly /
                                            AVERAGE_SUM_ORG_UNIT data element): the result is scaled for nothing
  population-rate-not-annualized     WARN   monthly counts over a yearly population (AVERAGE_SUM_ORG_UNIT, Yearly) and
                                            not annualized: a monthly value is 1/12 of the annual rate
  duplicate-definition               WARN   different indicators with the same numerator, denominator, factor, annualized
  duplicate-name / duplicate-shortName / duplicate-code   WARN
"""
import collections as C, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402
import indexpr as X  # noqa: E402

PCT_NAME = re.compile(r'%|\bper ?cent|\bpercentage\b|\bcoverage\b|\bproportion\b|\brate\b|\bshare\b', re.I)
PER_NAME = re.compile(r'\bper\s+(1[\s,.]?000|10[\s,.]?000|100[\s,.]?000|thousand|ten thousand|hundred thousand)\b', re.I)
RATIO_NAME = re.compile(r'\bper\b(?!\s*cent)|\bratio\b', re.I)
COUNT_AGG_OK = {'SUM', 'AVERAGE_SUM_ORG_UNIT'}
LONG_PT = {'Quarterly', 'QuarterlyNov', 'SixMonthly', 'SixMonthlyApril', 'SixMonthlyNov', 'Yearly', 'FinancialApril',
           'FinancialJuly', 'FinancialOct', 'FinancialNov'}
TIMES_100 = re.compile(r'\*\s*100(?:\.0*)?\s*$|^\s*100(?:\.0*)?\s*\*')


def main():
    ap = IC.add_common(IC.parser(__doc__))
    args = ap.parse_args()
    cfg = IC.load(args)
    L = Lint(cfg, IC.select(cfg, args.items))
    L.run()
    out = IC.tagged(cfg, 'findings.json', args.tag)
    IC.write_json(out, L.findings)
    cnt = C.Counter((f['severity'], f['check']) for f in L.findings)
    for (s, c), n in sorted(cnt.items()):
        print(f"{s:5} {c:40} {n}")
    print('indicators with >=1 ERROR:', len({f['id'] for f in L.findings if f['severity'] == 'ERROR' and f['id'] in L.ids}),
          '| data elements with findings:', len({f['id'] for f in L.findings if f['id'] in L.M.de}))
    print('->', out)


class Lint:
    def __init__(self, cfg, inds):
        self.cfg, self.inds = cfg, inds
        self.M = IC.Meta(cfg.md)
        self.ids = {i['id'] for i in inds}
        self.findings = []

    def add(self, sev, check, obj, detail):
        self.findings.append({'severity': sev, 'check': check, 'id': obj[0], 'name': obj[1], 'detail': detail})

    def dname(self, de):
        return self.M.name('de', de)

    def run(self):
        used_des = C.defaultdict(set)
        sig = C.defaultdict(list)
        for f in ('name', 'shortName', 'code'):
            c = C.Counter((i.get(f) or '').strip().lower() for i in self.inds if i.get(f))
            for v, n in c.items():
                if n > 1:
                    who = [i for i in self.inds if (i.get(f) or '').strip().lower() == v]
                    for i in who:
                        self.add('WARN', f'duplicate-{f}', (i['id'], i['name']), f"{n} indicators share {f} {v!r}")
        for i in self.inds:
            obj = (i['id'], i.get('name', i['id']))
            asts = {}
            for side in ('numerator', 'denominator'):
                asts[side] = self.side(i, side, obj, used_des)
            self.semantics(i, obj, asts)
            key = (norm(i.get('numerator')), norm(i.get('denominator')), self.M.factor(i), bool(i.get('annualized')))
            sig[key].append(i)
        for v in sig.values():
            if len(v) > 1:
                for i in v:
                    self.add('WARN', 'duplicate-definition', (i['id'], i['name']),
                             'same numerator, denominator, factor and annualized as: ' + '; '.join(f"{x['id']} {x['name']}" for x in v if x is not i))
        self.data_elements(used_des)

    # ------------------------------------------------------------------ one side
    def side(self, i, side, obj, used_des):
        text = (i.get(side) or '').strip()
        if not text:
            self.add('ERROR', 'empty-expression', obj, f'{side} is empty')
            return None
        try:
            ast = X.parse(text)
        except X.ParseError as e:
            self.add('WARN', 'parse-error', obj, f'{side}: {e}')
            return None
        except X.Unsupported as e:
            self.add('INFO', 'unsupported-operand', obj, f'{side}: {e}: not evaluated by the engine check, read it by hand')
            return None
        terms = C.Counter(additive_terms(ast))
        whole = {t[0] for t in terms if t[1] is None and t[2] is None}
        self._stale = []
        for n in X.operands(ast):
            k = n[0]
            if k == 'de':
                _, de, coc, aoc = n
                if de not in self.M.de:
                    if de in self.M.ds:
                        self.add('ERROR', 'unknown-de', obj, f'{side}: #{{{de}...}} is a data set, not a data element')
                    else:
                        self.add('ERROR', 'unknown-de', obj, f'{side}: data element {de} is not in the export')
                    continue
                used_des[de].add((i['id'], side))
                if coc not in (None, '*'):
                    self.check_coc(obj, side, de, coc)
                if aoc not in (None, '*'):
                    self.check_aoc(obj, side, de, aoc)
            elif k == 'const' and n[1] not in self.M.const:
                self.add('ERROR', 'unknown-constant', obj, f'{side}: constant {n[1]} is not in the export')
            elif k == 'oug' and n[1] not in self.M.oug:
                self.add('ERROR', 'unknown-org-unit-group', obj, f'{side}: org unit group {n[1]} is not in the export')
            elif k == 'other':
                self.add('INFO', 'unsupported-operand', obj, f'{side}: {n[1]}{{{n[2]}}} is not evaluated by the engine check; read it by hand')
            elif k == 'legacy-rate':
                self.add('WARN', 'legacy-reporting-rate-operand', obj,
                         f'{side}: #{{{n[1]}.{n[2]}}} is the old reporting-rate operand form; the current form is R{{{n[1]}.{n[2]}}}'
                         + (f" (data set {self.M.ds[n[1]]['name']})" if n[1] in self.M.ds else ' (not a data set in the export)'))
        if self._stale:
            M = self.M
            owner = (M.coc[self._stale[0][1]].get('categoryCombo') or {}).get('id')
            cats = ', '.join(M.cat.get(c['id'], {}).get('name', c['id']) for c in M.cc.get(owner, {}).get('categories', []))
            self.add('WARN', 'coc-not-fitting-combo', obj,
                     f"{side}: {len(self._stale)} term(s) use COCs attached to combo '{M.cc.get(owner, {}).get('name')}' whose options "
                     f"are not one per category of that combo ({cats}), e.g. '{M.coc[self._stale[0][1]].get('name')}' ({self._stale[0][1]}): "
                     'left over from an earlier version of the combo. The data stored under them still counts, but they cannot be '
                     'recreated on 2.43 (E1130: a metadata copy or a combo rebuild drops them) and data entry built from the '
                     'current categories produces other COCs; the test copy cannot hold them, so the engine check reads nothing')
        dup = [t for t, c in terms.items() if c > 1]
        over = [t for t in terms if t[1] is not None and t[0] in whole]
        if dup or over:
            self.add('WARN', 'overlapping-operands', obj, f'{side}: ' + '; '.join(
                [f'{self.dname(t[0])} ({self.coc_label(t[1])}) appears {terms[t]} times' for t in dup] +
                [f'{self.dname(t[0])} is read whole (#{{{t[0]}}}) and also by COC ({self.coc_label(t[1])})' for t in over]))
        return ast

    def coc_label(self, coc):
        return 'all COCs' if coc in (None, '*') else self.M.name('coc', coc)

    def check_coc(self, obj, side, de, coc):
        M = self.M
        if coc not in M.coc:
            self.add('ERROR', 'unknown-coc', obj, f'{side}: #{{{de}.{coc}}}: COC {coc} is not in the export')
            return
        combos = M.de_combos(de)
        owner = (M.coc[coc].get('categoryCombo') or {}).get('id')
        if owner not in combos:
            same = [c for cc in combos for c in M.cc_cocs.get(cc, []) if M.coc_options(c) == M.coc_options(coc)]
            self.add('ERROR', 'coc-not-in-de-combo', obj,
                     f"{side}: #{{{de}.{coc}}}: COC '{M.coc[coc].get('name')}' belongs to combo "
                     f"'{M.cc.get(owner, {}).get('name', owner)}', not to {self.dname(de)}'s "
                     f"{' / '.join(repr(M.cc.get(c, {}).get('name', c)) for c in combos)}: the term reads only data stored "
                     'before the combo changed (new values with it are refused, E8024)'
                     + (f"; same options in the current combo: {', '.join(same)}" if same else '; no COC of the current combo has the same options'))
        elif not M.coc_fits_combo(coc, owner):
            self._stale.append((de, coc))

    def check_aoc(self, obj, side, de, aoc):
        M = self.M
        if aoc not in M.coc:
            self.add('ERROR', 'unknown-aoc', obj, f'{side}: attribute option combo {aoc} is not in the export')
            return
        allowed = {a for ds in M.de_ds.get(de, []) for a in M.ds_aocs(ds)}
        if allowed and aoc not in allowed:
            self.add('ERROR', 'aoc-not-in-data-set-combo', obj,
                     f"{side}: attribute option combo '{M.coc[aoc].get('name')}' is not one of the attribute combos of {self.dname(de)}'s data set(s)")

    # ------------------------------------------------------------------ indicator-level semantics
    def semantics(self, i, obj, asts):
        M = self.M
        name = i.get('name') or ''
        num, den = (i.get('numerator') or '').strip(), (i.get('denominator') or '').strip()
        f = M.factor(i)
        it = M.it.get((i.get('indicatorType') or {}).get('id'))
        if not it:
            self.add('ERROR', 'unknown-indicator-type', obj, f"indicator type {(i.get('indicatorType') or {}).get('id')} is not in the export")
        den1 = den in ('1', '1.0')
        if den in ('0', '0.0'):
            self.add('ERROR', 'denominator-zero', obj, 'denominator is 0: never a value')
        if num and norm(num) == norm(den) and not den1:
            self.add('ERROR', 'numerator-equals-denominator', obj, f'numerator and denominator are the same ({num[:80]}): always {f or 1:g}')
        nast, dast = asts.get('numerator'), asts.get('denominator')
        ndiv = X.divisions(nast) if nast else []
        ddiv = X.divisions(dast) if dast else []
        if ndiv and den1:
            self.add('WARN', 'division-in-numerator-denominator-1', obj,
                     f"the numerator divides by data ({X.unparse(ndiv[0][1])[:90]}) and the denominator is 1: analytics shows NaN where the "
                     'divisor is 0 (A4) instead of leaving the cell empty, and the definition hides the rate\'s denominator. '
                     'Values are otherwise the same: operands are aggregated before the expression is evaluated (A5). Put the divisor in the denominator.')
        elif ndiv or ddiv:
            self.add('WARN', 'inner-division', obj,
                     f"division by data inside the {'numerator' if ndiv else 'denominator'} ({X.unparse((ndiv or ddiv)[0][1])[:90]}): NaN where it is 0 (A4)")
        times100 = bool(TIMES_100.search(num)) or bool(re.search(r'\)\s*\*\s*100\b', num))
        if f == 100 and times100:
            self.add('ERROR', 'factor-100-and-times-100', obj, f'indicator type {it.get("name") if it else "?"} (×100) and the numerator is multiplied by 100 again: the value is 100 times too high')
        pct = bool(PCT_NAME.search(name)) and not RATIO_NAME.search(name)
        if f == 1 and pct and not den1 and not times100:
            self.add('ERROR', 'percent-name-number-type', obj, f"the name says {PCT_NAME.search(name).group(0)!r} but the indicator type has factor 1 and nothing multiplies by 100: 0.85 instead of 85")
        if f == 1 and times100:
            self.add('INFO', 'explicit-100-number-type', obj, 'Number type with an explicit *100 in the numerator: works; a Percent type states it')
        if den1 and f not in (None, 1) and not ndiv:
            self.add('ERROR', 'count-name-percent-type', obj, f'denominator 1 (a count) with factor {f:g}: the count is multiplied by {f:g}')
        if den1 and not ndiv and PCT_NAME.search(name) and not re.search(r'\bstock|\btotal|\bnumber\b', name, re.I):
            self.add('WARN', 'rate-name-denominator-1', obj, f"the name says {PCT_NAME.search(name).group(0)!r} but the denominator is 1: a count, not a rate")
        m = PER_NAME.search(name)
        if m and f is not None:
            want = {'thousand': 1000, 'ten thousand': 10000, 'hundred thousand': 100000}.get(m.group(1).lower(),
                                                                                            int(re.sub(r'\D', '', m.group(1)) or 0))
            if want and want != f:
                self.add('ERROR', 'name-factor-mismatch', obj, f"the name says 'per {m.group(1)}' but the factor is {f:g}")
        self.period_logic(i, obj, nast, dast)

    def period_logic(self, i, obj, nast, dast):
        M = self.M

        def des(ast):
            return [n[1] for n in X.operands(ast) if n[0] == 'de' and n[1] in M.de] if ast else []

        dd, nd = des(dast), des(nast)
        annual = [d for d in dd if (M.de[d].get('aggregationType') or '').startswith('AVERAGE') and M.de_period_type(d) in ('Yearly',) or M.de_period_type(d).startswith('Financial')]
        if i.get('annualized') and dd and not annual:
            self.add('WARN', 'annualized-without-population', obj,
                     'annualized, but the denominator reads no annual figure (no yearly AVERAGE_SUM_ORG_UNIT data element such as a population): '
                     f'a monthly value is multiplied by 365/31 for nothing ({", ".join(sorted({self.dname(d) for d in dd})[:3])})')
        if not i.get('annualized') and annual and nd and all(M.de_period_type(d) not in LONG_PT for d in nd):
            self.add('WARN', 'population-rate-not-annualized', obj,
                     f'monthly counts over a yearly figure ({", ".join(sorted({self.dname(d) for d in annual}))}) without annualized: '
                     'a month shows 1/12 and a quarter 1/4 of the annual rate')
        zdes = sorted({d for d in nd if not M.de[d].get('zeroIsSignificant') and M.de[d].get('valueType') not in ('TRUE_ONLY',)})
        if zdes and len(zdes) == len(set(nd)):
            self.add('INFO', 'numerator-zeros-dropped', obj,
                     f"every numerator data element has zeroIsSignificant=false ({', '.join(self.dname(d) for d in zdes[:4])}{'…' if len(zdes) > 4 else ''}): "
                     'a facility that reports 0 shows no value instead of 0 (A1, A3)')

    # ------------------------------------------------------------------ data elements
    def data_elements(self, used):
        M = self.M
        for de, uses in sorted(used.items()):
            o = M.de[de]
            obj = (de, 'data element: ' + o.get('name', de))
            vt, agg = o.get('valueType') or '', o.get('aggregationType') or ''
            users = sorted({u for u, _ in uses})
            ulab = f"read by {len(users)} indicator(s)"
            if vt not in IC.NUMERIC:
                self.add('ERROR', 'de-not-numeric', obj, f'value type {vt}: never aggregates into an indicator ({ulab})')
                continue
            if (o.get('domainType') or 'AGGREGATE') != 'AGGREGATE':
                self.add('WARN', 'de-domain-tracker', obj, f'domain type {o.get("domainType")}: aggregate analytics has no values for it ({ulab})')
            if not M.de_ds.get(de):
                self.add('WARN', 'de-in-no-data-set', obj, f'in no data set: values cannot be entered or imported (E8003), so it never has data ({ulab})')
            pt = M.de_period_type(de) if M.de_ds.get(de) else None
            if agg == 'COUNT' and vt not in ('TRUE_ONLY', 'BOOLEAN'):
                self.add('ERROR', 'de-aggregation-count', obj, f'aggregationType COUNT on a {vt} data element: analytics counts the values (e.g. 3 reports) instead of adding them up ({ulab})')
            elif agg in ('NONE', ''):
                self.add('ERROR', 'de-aggregation-none', obj, f'aggregationType {agg or "(none)"}: not aggregated ({ulab})')
            elif agg == 'AVERAGE' and pt and pt not in LONG_PT:
                self.add('WARN', 'de-aggregation-average', obj, f'aggregationType AVERAGE on a data element entered {pt}: parent org units get the mean of the facilities and a quarter the mean of the months (A6); SUM adds them up ({ulab})')
            elif agg not in COUNT_AGG_OK and agg not in ('AVERAGE', 'COUNT'):
                self.add('INFO', 'de-aggregation-other', obj, f'aggregationType {agg}: not evaluated by the engine check ({ulab})')
            if agg == 'SUM' and pt in LONG_PT:
                self.add('WARN', 'sum-data-long-period', obj, f'SUM data entered {pt}: no value in any shorter period, so monthly and quarterly views of the indicator stay empty (A7) ({ulab})')


def additive_terms(n):
    """Data operands that are added or subtracted at the top level of a side (not inside a product or division)."""
    if n[0] == 'bin' and n[1] in ('+', '-'):
        return additive_terms(n[2]) + additive_terms(n[3])
    if n[0] == 'un' and n[1] in ('-', '+'):
        return additive_terms(n[2])
    if n[0] == 'bin' and n[1] == '*' and n[3] == ('num', 1.0):
        return additive_terms(n[2])
    return [n[1:]] if n[0] == 'de' else []


def norm(t):
    return re.sub(r'\s+', '', t or '')


if __name__ == '__main__':
    main()
