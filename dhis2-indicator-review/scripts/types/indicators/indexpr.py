"""Independent evaluator for aggregate indicator expressions (numerator / denominator) over aggregated data values,
encoding the engine behaviours verified on DHIS2 2.43.2 (references/indicators/behaviours.md, ids A1-A19).

    import indexpr as X
    ast = X.parse('#{de.coc} + C{const}')                  # ParseError on bad syntax
    X.operands(ast) -> [('de', de, coc|None|'*', aoc|None|'*'), ('const', uid), ('oug', uid), ('days',), ...]
    eng = X.Engine(meta, values, setup)                    # meta = common.Meta, values = truth.json records
    eng.indicator(ind, ou, pe) -> {'value': float|None|'NaN', 'num': ..., 'den': ..., 'factor', 'annualization'}

Data model: a value record {"de", "coc", "aoc", "ou", "pe", "value"} (pe in DHIS2 ISO form: 202601, 2026Q1, 2026S1,
2026, 2026AprilS1 is not supported). An org unit is evaluated over itself and every org unit below it
(setup["parents"]: {child: parent}).

Rules (the ids are the behaviour ids):
  A1  missing operands: an operand with no data reads 0 when at least one other operand visited on the same side has
      data; when every visited operand is missing, that side is null and the indicator has no value. Constants do
      not rescue a side (#{a}+0 is null without data). "Visited" follows lazy evaluation (if() evaluates the
      condition and the branch taken); operands inside isNull / isNotNull / firstNonNull are not counted. OUG{} and
      C{} are not operands for this rule.
  A2  denominator 0 or null -> no value.
  A3  a stored zero reaches analytics only when the data element has zeroIsSignificant=true and the system setting
      keyIncludeZeroValuesInAnalytics is on (otherwise it is missing, not 0) -> Engine(zero_visible=...).
  A4  division by zero inside a side gives NaN (the analytics row carries "NaN"; no E7132 on 2.43.2).
  A5  parent org units: each operand is aggregated over the org units first, then the sides are divided
      (sum(num)/sum(den), not the mean of facility values).
  A6  aggregation over time and org units by the data element's aggregationType: SUM, COUNT (number of values),
      AVERAGE (mean of every value in the period and org units), AVERAGE_SUM_ORG_UNIT (per org unit: sum of
      value x days of the data period / days of the query period; data in a LONGER period than the query is used as
      is), others -> Unsupported.
  A7  SUM data in a period LONGER than the query period is not used (yearly data has no monthly/quarterly value).
  A8  annualized: x 365 / days in the query period.
  A9  rounding to the indicator's decimals (2 when unset) -> compare with round_to().
  A14 OUG{g}: number of org units of the group at or below the queried org unit (0 or 1 at a facility).
  A15 C{c}: the constant's value; [days]: days in the query period.
  A16 #{de.*.aoc} and #{de.coc.aoc} select an attribute option combo; #{de} = every COC and AOC (A18).
  A17 functions: if, isNull, isNotNull, firstNonNull (0 when every argument is null), greatest, least, log, log10,
      abs, sqrt (not verified), ^, %; a bare boolean (e.g. isNotNull(x) as a whole side) reads 0.
  A20 a side the server cannot parse (e.g. the old #{dataSet.REPORTING_RATE} form, "Expression is not well-formed")
      reads 0 in analytics, silently: a numerator gives 0 everywhere, a denominator no value. Modelled for the
      #{x.REPORTING_RATE} form; other parse errors are reported as status "parse".
  An unknown data element uid is a missing operand (A1).
  A21 R{ds.REPORTING_RATE}: 100 x complete registrations / (assigned org units x periods of the data set in the
      query period), at or below the queried org unit, ROUNDED TO 2 DECIMALS before it enters the expression
      (not rounded when the query has skipRounding=true; Engine.round_rates);
      ACTUAL_REPORTS / EXPECTED_REPORTS the two counts. A rate of 0
      (nothing reported) is a value for A1: (#{de}) * R{ds.REPORTING_RATE} / 100 is 0, not missing, without data.
Unsupported (raise Unsupported, the item is reported as not evaluated): other R{} metrics, I{}, D{}, A{},
N{}, subExpression, .aggregationType(), .periodOffset(), other aggregation types.
"""
import calendar, datetime as dt, math, re

RULES = {'A1': True, 'A3': True, 'A7': True, 'A20': True}


class ParseError(Exception):
    pass


class Unsupported(Exception):
    pass


# ------------------------------------------------------------------------------ tokens and parser
TOKEN = re.compile(r'''\s*(?:
    (?P<num>\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+)
  | (?P<de>\#\{[^}]*\})
  | (?P<op>[A-Z]+\{[^}]*\})
  | (?P<days>\[days\])
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<sym>==|!=|<=|>=|&&|\|\||[-+*/%^()<>!,.])
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
)''', re.X)
UID = r'[A-Za-z][A-Za-z0-9]{10}'
FUNCS = {'if', 'isnull', 'isnotnull', 'firstnonnull', 'greatest', 'least', 'log', 'log10', 'abs', 'sqrt'}
UNSUPPORTED_FUNCS = {'subexpression', 'aggregationtype', 'periodoffset', 'maxdate', 'mindate', 'yearlyperiodcount',
                     'orgunit', 'removezeros', 'normdistcum', 'normdistden', 'contains', 'containsitems'}


def tokenize(s):
    out, i, s = [], 0, s or ''
    while i < len(s):
        if s[i:].strip() == '':
            break
        m = TOKEN.match(s, i)
        if not m or m.end() == i:
            raise ParseError(f'unexpected character at {i}: {s[i:i + 20]!r}')
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        i = m.end()
    return out


class Parser:
    """Precedence (low -> high): || ; && ; == != ; < > <= >= ; + - ; * / % ; unary - + ! ; ^ ; atoms."""

    def __init__(self, text):
        self.t = tokenize(text)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if tok[0] is None or (val is not None and tok[1] != val):
            raise ParseError(f'expected {val or "a token"}, got {tok[1]!r}')
        self.i += 1
        return tok

    def parse(self):
        if not self.t:
            raise ParseError('empty expression')
        e = self.or_()
        if self.i != len(self.t):
            raise ParseError(f'unexpected {self.peek()[1]!r}')
        return e

    def bin(self, nxt, ops):
        e = nxt()
        while self.peek()[1] in ops or (self.peek()[0] == 'id' and self.peek()[1].lower() in ops):
            op = self.take()[1]
            op = {'and': '&&', 'or': '||'}.get(op.lower(), op)
            e = ('bin', op, e, nxt())
        return e

    def or_(self):
        return self.bin(self.and_, ('||', 'or'))

    def and_(self):
        return self.bin(self.eq, ('&&', 'and'))

    def eq(self):
        return self.bin(self.rel, ('==', '!='))

    def rel(self):
        return self.bin(self.add, ('<', '>', '<=', '>='))

    def add(self):
        return self.bin(self.mul, ('+', '-'))

    def mul(self):
        return self.bin(self.unary, ('*', '/', '%'))

    def unary(self):
        k, v = self.peek()
        if v in ('-', '+', '!') or (k == 'id' and v.lower() == 'not'):
            self.take()
            return ('un', {'not': '!'}.get(v.lower(), v), self.unary())
        return self.pow()

    def pow(self):
        e = self.postfix()
        if self.peek()[1] == '^':
            self.take()
            return ('bin', '^', e, self.unary())     # right associative
        return e

    def postfix(self):
        e = self.atom()
        while self.peek()[1] == '.' and self.peek(1)[0] == 'id':
            self.take('.')
            name = self.take()[1]
            raise Unsupported(f'.{name}(...) is not evaluated')
        return e

    def atom(self):
        k, v = self.take()
        if k == 'num':
            return ('num', float(v))
        if k == 'days':
            return ('days',)
        if k == 'str':
            return ('str', v[1:-1])
        if k == 'de':
            return operand_de(v)
        if k == 'op':
            return operand_other(v)
        if v == '(':
            e = self.or_()
            self.take(')')
            return e
        if k == 'id':
            lv = v.lower()
            if lv in ('true', 'false'):
                return ('bool', lv == 'true')
            if self.peek()[1] != '(':
                raise ParseError(f'unknown name {v!r}')
            self.take('(')
            args = []
            if self.peek()[1] != ')':
                args.append(self.or_())
                while self.peek()[1] == ',':
                    self.take(',')
                    args.append(self.or_())
            self.take(')')
            if lv in UNSUPPORTED_FUNCS:
                raise Unsupported(f'{v}() is not evaluated')
            if lv not in FUNCS:
                raise ParseError(f'unknown function {v}()')
            return ('fn', lv, args)
        raise ParseError(f'unexpected {v!r}')


def operand_de(tok):
    body = tok[2:-1].strip()
    parts = [p.strip() for p in body.split('.')]
    if not 1 <= len(parts) <= 3 or not re.fullmatch(UID, parts[0]):
        raise ParseError(f'bad operand {tok}')
    for p in parts[1:]:
        if p != '*' and not re.fullmatch(UID, p) and p not in ('REPORTING_RATE', 'ACTUAL_REPORTS', 'EXPECTED_REPORTS',
                                                               'REPORTING_RATE_ON_TIME', 'ACTUAL_REPORTS_ON_TIME'):
            raise ParseError(f'bad operand {tok}')
    if len(parts) > 1 and not re.fullmatch(UID + r'|\*', parts[1]):
        return ('legacy-rate', parts[0], parts[1])
    return ('de', parts[0], parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None)


def operand_other(tok):
    m = re.fullmatch(r'([A-Z]+)\{([^}]*)\}', tok)
    kind, body = m.group(1), m.group(2).strip()
    if kind == 'C':
        if not re.fullmatch(UID, body):
            raise ParseError(f'bad constant {tok}')
        return ('const', body)
    if kind == 'OUG':
        if not re.fullmatch(UID, body):
            raise ParseError(f'bad org unit group {tok}')
        return ('oug', body)
    if kind in ('R', 'I', 'D', 'A', 'N', 'PS', 'V'):
        return ('other', kind, body)
    raise ParseError(f'unknown operand {tok}')


def parse(text):
    return Parser(text).parse()


def walk(n):
    yield n
    if n[0] == 'bin':
        yield from walk(n[2])
        yield from walk(n[3])
    elif n[0] == 'un':
        yield from walk(n[2])
    elif n[0] == 'fn':
        for a in n[2]:
            yield from walk(a)


def operands(ast):
    return [n for n in walk(ast) if n[0] in ('de', 'const', 'oug', 'days', 'other', 'legacy-rate')]


def unparse(n):
    k = n[0]
    if k == 'num':
        return f'{n[1]:g}'
    if k == 'de':
        return '#{' + '.'.join(x for x in n[1:] if x) + '}' if n[3] is None else '#{%s.%s.%s}' % (n[1], n[2] or '*', n[3])
    if k == 'const':
        return 'C{%s}' % n[1]
    if k == 'oug':
        return 'OUG{%s}' % n[1]
    if k == 'days':
        return '[days]'
    if k == 'bin':
        return f'({unparse(n[2])} {n[1]} {unparse(n[3])})'
    if k == 'un':
        return f'{n[1]}{unparse(n[2])}'
    if k == 'fn':
        return f"{n[1]}({', '.join(unparse(a) for a in n[2])})"
    if k == 'bool':
        return 'true' if n[1] else 'false'
    return str(n)


def divisions(ast):
    """(dividend, divisor) of every '/' whose divisor reads data: the inner divisions that give NaN at zero (A4)."""
    return [(n[2], n[3]) for n in walk(ast) if n[0] == 'bin' and n[1] == '/' and any(x[0] == 'de' for x in walk(n[3]))]


# ------------------------------------------------------------------------------ periods
def period_range(pe):
    """(start date, end date, period type) of a DHIS2 period id."""
    pe = str(pe)
    m = re.fullmatch(r'(\d{4})(\d{2})', pe)
    if m:
        y, mo = int(m[1]), int(m[2])
        return dt.date(y, mo, 1), dt.date(y, mo, calendar.monthrange(y, mo)[1]), 'Monthly'
    m = re.fullmatch(r'(\d{4})(\d{2})B', pe)
    if m:
        y, b = int(m[1]), int(m[2])
        s = dt.date(y, 2 * b - 1, 1)
        return s, dt.date(y, 2 * b, calendar.monthrange(y, 2 * b)[1]), 'BiMonthly'
    m = re.fullmatch(r'(\d{4})Q([1-4])', pe)
    if m:
        y, q = int(m[1]), int(m[2])
        return dt.date(y, 3 * q - 2, 1), dt.date(y, 3 * q, calendar.monthrange(y, 3 * q)[1]), 'Quarterly'
    m = re.fullmatch(r'(\d{4})S([12])', pe)
    if m:
        y, h = int(m[1]), int(m[2])
        return (dt.date(y, 1, 1), dt.date(y, 6, 30), 'SixMonthly') if h == 1 else (dt.date(y, 7, 1), dt.date(y, 12, 31), 'SixMonthly')
    m = re.fullmatch(r'(\d{4})', pe)
    if m:
        y = int(m[1])
        return dt.date(y, 1, 1), dt.date(y, 12, 31), 'Yearly'
    m = re.fullmatch(r'(\d{4})(April|July|Oct|Nov)', pe)
    if m:
        y, mo = int(m[1]), {'April': 4, 'July': 7, 'Oct': 10, 'Nov': 11}[m[2]]
        s = dt.date(y, mo, 1)
        e = dt.date(y + 1, mo, 1) - dt.timedelta(days=1)
        return s, e, 'Financial' + m[2]
    raise Unsupported(f'period {pe} is not supported by the evaluator')


def days(pe):
    s, e, _ = period_range(pe)
    return (e - s).days + 1


def periods_covering(pt, pe):
    """Periods of type pt that the data generator fills for query period pe: the periods inside pe, or the one
    containing it when pt is longer."""
    s, e, _ = period_range(pe)
    out = []
    if pt == 'Monthly':
        d = s
        while d <= e:
            out.append(f'{d.year}{d.month:02d}')
            d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return out
    if pt == 'BiMonthly':
        return sorted({f'{s.year}{(m - 1) // 2 + 1:02d}B' for m in range(s.month, e.month + 1)})
    if pt == 'Quarterly':
        return sorted({f'{s.year}Q{(m - 1) // 3 + 1}' for m in range(s.month, e.month + 1)})
    if pt == 'SixMonthly':
        return sorted({f'{s.year}S{1 if m <= 6 else 2}' for m in range(s.month, e.month + 1)})
    if pt == 'Yearly':
        return [str(s.year)]
    if pt.startswith('Financial'):
        mo = {'FinancialApril': 4, 'FinancialJuly': 7, 'FinancialOct': 10, 'FinancialNov': 11}[pt]
        y = s.year if s.month >= mo else s.year - 1
        return [f'{y}{pt[9:]}']
    raise Unsupported(f'period type {pt} is not supported by the generator')


def round_to(v, decimals):
    if v is None or isinstance(v, str):
        return v
    d = decimals if isinstance(decimals, int) else 2
    return round(v + 0.0, d)


# ------------------------------------------------------------------------------ engine
class Engine:
    def __init__(self, meta, values, setup=None, zero_visible=None, rules=None):
        """meta: common.Meta; values: truth records; setup: testsetup.json (parents, oug_members, zeros);
        zero_visible(de) -> bool: does a stored 0 of this data element reach analytics (A3)."""
        self.M = meta
        self.setup = setup or {}
        self.rules = dict(RULES, **(rules or {}))
        self.parents = self.setup.get('parents') or {}
        self.oug = {g: set(v) for g, v in (self.setup.get('oug_members') or {}).items()}
        if zero_visible is None:
            # gen.py --zeros significant: import_data.py sets zeroIsSignificant on every data element that gets a 0 in
            # any payload it loads, and turns keyIncludeZeroValuesInAnalytics on
            allz = self.setup.get('zeros', 'significant') == 'significant'
            key = self.setup.get('include_zeros', True)
            zero_visible = lambda de: key and (allz or bool((meta.de.get(de) or {}).get('zeroIsSignificant')))
        self.zero_visible = zero_visible
        self.by_de = {}
        for v in values:
            self.by_de.setdefault(v['de'], []).append(v)
        # reporting rates (A21): import_data.py registers every (data set, org unit, period, aoc) that got values as
        # complete and assigns each data set to the org units that get values for it
        self.complete, self.assigned = set(), {}
        self.round_rates = True          # analytics rounds R{} to 2 decimals, except with skipRounding=true (A21)
        for v in values:
            ds = meta.de_dataset(v['de'])
            if ds and v.get('value') not in (None, ''):
                self.complete.add((ds, v['ou'], v['pe']))
                self.assigned.setdefault(ds, set()).add(v['ou'])
        self._anc = {}

    def ancestors(self, ou):
        if ou not in self._anc:
            out, cur, seen = {ou}, ou, set()
            while cur in self.parents and cur not in seen:
                seen.add(cur)
                cur = self.parents[cur]
                out.add(cur)
            self._anc[ou] = out
        return self._anc[ou]

    def below(self, rec_ou, ou):
        return ou in self.ancestors(rec_ou)

    # -------------------------------------------------- operand values
    def de_value(self, de, coc, aoc, ou, pe):
        """Aggregated value of one data element operand at ou/pe (None when there is no data). A6/A7/A3."""
        rows = self.by_de.get(de, [])
        obj = self.M.de.get(de) or {}
        agg = obj.get('aggregationType') or 'SUM'
        vt = obj.get('valueType') or 'NUMBER'
        qs, qe, _ = period_range(pe)
        qdays = (qe - qs).days + 1
        inside, longer = [], []
        for r in rows:
            if coc not in (None, '*') and r['coc'] != coc:
                continue
            if aoc not in (None, '*') and r.get('aoc') != aoc:
                continue
            if not self.below(r['ou'], ou):
                continue
            x = num_value(r['value'], vt)
            if x is None:
                continue
            if x == 0 and self.rules['A3'] and not self.zero_visible(de):
                continue
            s, e, _ = period_range(r['pe'])
            if s >= qs and e <= qe:
                inside.append((r, x, (e - s).days + 1))
            elif s <= qs and e >= qe:
                longer.append((r, x))
        if agg == 'SUM':
            vals = [x for _, x, _ in inside] + ([] if self.rules['A7'] else [x for _, x in longer])
            return sum(vals) if vals else None
        if agg == 'COUNT':
            n = len(inside) + (0 if self.rules['A7'] else len(longer))
            return float(n) if n else None
        if agg == 'AVERAGE':
            vals = [x for _, x, _ in inside] + [x for _, x in longer]
            return sum(vals) / len(vals) if vals else None
        if agg == 'AVERAGE_SUM_ORG_UNIT':
            if not inside and not longer:
                return None
            return sum(x * d for _, x, d in inside) / qdays + sum(x for _, x in longer)
        raise Unsupported(f'aggregationType {agg} of data element {de} is not evaluated')

    def reporting(self, body, ou, pe):
        """R{ds.REPORTING_RATE | ACTUAL_REPORTS | EXPECTED_REPORTS} (A21): actual = complete registrations in the
        org units below ou in the data set's periods inside pe; expected = assigned org units x those periods."""
        ds, metric = body.split('.')
        if len(self.M.ds_aocs(ds)) > 1:
            raise Unsupported(f'reporting rate of {ds}: data sets with an attribute combo are not evaluated')
        pt = self.M.ds_period_type(ds)
        qs, qe, _ = period_range(pe)
        pes = [p for p in periods_covering(pt, pe) if period_range(p)[0] >= qs and period_range(p)[1] <= qe]
        ous = [o for o in self.assigned.get(ds, ()) if self.below(o, ou)]
        expected = len(ous) * len(pes)
        actual = sum(1 for o in ous for p in pes if (ds, o, p) in self.complete)
        if metric == 'ACTUAL_REPORTS':
            return float(actual)
        if metric == 'EXPECTED_REPORTS':
            return float(expected)
        if not expected:
            return None
        rate = 100.0 * actual / expected
        return round(rate, 2) if self.round_rates else rate     # rounded to 2 decimals unless skipRounding

    def oug_count(self, g, ou):
        return float(sum(1 for m in self.oug.get(g, ()) if self.below(m, ou)))

    # -------------------------------------------------- expression
    def side(self, text, ou, pe):
        """Value of a numerator or denominator: float, None (no value, A1) or NaN (A4)."""
        ast = parse(text)
        if self.rules['A20'] and any(n[0] == 'legacy-rate' for n in walk(ast)):
            return 0.0                       # A20: the server cannot parse the side; analytics reads it as 0
        for n in walk(ast):
            if n[0] == 'other' and not (n[1] == 'R' and re.fullmatch(UID + r'\.(REPORTING_RATE|ACTUAL_REPORTS|EXPECTED_REPORTS)', n[2])):
                raise Unsupported(f'{n[1]}{{{n[2]}}} is not evaluated')
        st = {'visited': 0, 'found': 0}
        v = self.ev(ast, ou, pe, st, True)
        if self.rules['A1'] and st['visited'] and not st['found']:
            return None
        if isinstance(v, bool):
            return 0.0                       # A17: a bare boolean side reads 0
        return z(v)

    def ev(self, n, ou, pe, st, count):
        k = n[0]
        if k == 'num':
            return n[1]
        if k == 'bool':
            return n[1]
        if k == 'str':
            return n[1]
        if k == 'days':
            return float(days(pe))
        if k == 'const':
            c = self.M.const.get(n[1])
            if c is None:
                raise Unsupported(f'constant {n[1]} is not in the export')
            return float(c.get('value') or 0)
        if k == 'oug':
            return self.oug_count(n[1], ou)
        if k == 'other':                     # R{ds.METRIC}, checked in side(); an operand for A1 (verified)
            v = self.reporting(n[2], ou, pe)
            if count:
                st['visited'] += 1
                st['found'] += v is not None
            return v
        if k == 'de':
            v = self.de_value(n[1], n[2], n[3], ou, pe)
            if count:
                st['visited'] += 1
                st['found'] += v is not None
            return v                         # None: missing (read as 0 by the operators, A1)
        if k == 'un':
            x = self.ev(n[2], ou, pe, st, count)
            if n[1] == '!':
                return not truthy(x)
            x = z(x)
            return -x if n[1] == '-' else x
        if k == 'bin':
            op = n[1]
            if op in ('&&', '||'):
                a = truthy(self.ev(n[2], ou, pe, st, count))
                if op == '&&' and not a:
                    return False
                if op == '||' and a:
                    return True
                return truthy(self.ev(n[3], ou, pe, st, count))
            a, b = self.ev(n[2], ou, pe, st, count), self.ev(n[3], ou, pe, st, count)
            if op in ('==', '!='):
                r = z(a) == z(b) if not (isinstance(a, str) or isinstance(b, str)) else a == b
                return r if op == '==' else not r
            a, b = z(a), z(b)
            if op in ('<', '>', '<=', '>='):
                return {'<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[op]
            if op == '+':
                return a + b
            if op == '-':
                return a - b
            if op == '*':
                return a * b
            if op == '/':
                return a / b if b != 0 else math.nan         # A4
            if op == '%':
                return math.fmod(a, b) if b != 0 else math.nan
            if op == '^':
                try:
                    return float(a) ** float(b)
                except (OverflowError, ZeroDivisionError, ValueError):
                    return math.nan
        if k == 'fn':
            f, args = n[1], n[2]
            if f == 'if':
                if len(args) != 3:
                    raise ParseError('if() takes 3 arguments')
                c = self.ev(args[0], ou, pe, st, count)
                return self.ev(args[1] if truthy(c) else args[2], ou, pe, st, count)
            if f in ('isnull', 'isnotnull'):
                v = self.ev(args[0], ou, pe, st, False)
                return (v is None) if f == 'isnull' else (v is not None)
            if f == 'firstnonnull':
                for a in args:
                    v = self.ev(a, ou, pe, st, False)
                    if v is not None:
                        return v
                return 0.0
            vals = [self.ev(a, ou, pe, st, count) for a in args]
            if f in ('greatest', 'least'):
                vals = [z(v) for v in vals]
                return max(vals) if f == 'greatest' else min(vals)
            x = z(vals[0])
            if f == 'log':
                return math.log(x) if x > 0 else (-math.inf if x == 0 else math.nan)
            if f == 'log10':
                return math.log10(x) if x > 0 else (-math.inf if x == 0 else math.nan)
            if f == 'abs':
                return abs(x)
            if f == 'sqrt':
                return math.sqrt(x) if x >= 0 else math.nan
        raise Unsupported(f'cannot evaluate {n!r}')

    # -------------------------------------------------- indicator
    def indicator(self, ind, ou, pe):
        num = self.side(ind.get('numerator') or '', ou, pe)
        den = self.side(ind.get('denominator') or '', ou, pe)
        factor = self.M.factor(ind)
        if factor is None:
            raise Unsupported('indicator type not in the export')
        ann = 365.0 / days(pe) if ind.get('annualized') else 1.0
        rec = {'num': num, 'den': den, 'factor': factor, 'annualization': ann}
        if num is None or den is None:
            rec['value'] = None
        elif math.isnan(num) or math.isnan(den):
            rec['value'] = 'NaN'
        elif den == 0:
            rec['value'] = None                       # A2
        else:
            rec['value'] = finite(num / den * factor * ann)
        return rec


def finite(v):
    """Non-finite values as analytics returns them: 'NaN', 'Infinity', '-Infinity'."""
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    return v


def z(v):
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, str):
        raise Unsupported('text value in arithmetic')
    return v


def truthy(v):
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and math.isnan(v):
        return False
    return v != 0


def num_value(raw, vt):
    if raw is None or raw == '':
        return None
    s = str(raw).strip().lower()
    if vt in ('TRUE_ONLY', 'BOOLEAN'):
        return 1.0 if s in ('true', '1') else (0.0 if s in ('false', '0') else None)
    try:
        return float(s)
    except ValueError:
        return None
