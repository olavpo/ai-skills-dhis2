"""DHIS2 program-indicator expressions: parser, SQL-null evaluator, and the analytics semantics in one place.

This module is the single source of DHIS2 semantics for the validate and e2e phases. Everything that decides
"what should analytics return for this PI on these records" lives here, so the generator (sat.py), the engine
check (compare.py) and the e2e checks agree. Stdlib only, no instance access.

Layers
  1. parse(text) -> AST tuples              ('num', 1.0) ('str', 'x') ('bool', True) ('de', stage, de) ('tea', uid)
                                            ('var', name) ('not', n) ('neg', n) ('bin', op, a, b) ('fn', name, [args])
     helpers: walk, leaves, conjuncts, comparisons, unparse
  2. ev(node, ctx, mode=None)               SQL null semantics; ctx(leaf) returns the raw value of a de/tea/var leaf.
     Mode(coalesce_text, blank_num_zero)    per-PI-type comparison rules (C3, C4 below)
  3. Engine(md, program)                    record-level semantics of analytics:
        .categories(p)                      disaggregation categories of a PI (category combo + attribute combo)
        .query_pe(p, test_pe)               quarter to query so the PI's offset window lands on test_pe (P1, P2)
        .window(p, pe)                      enrollment/event windows from the analytics period boundaries (P1, P3, C5)
        .evaluate(p, truth, pe, cats)       expected total and cells from truth.json records (C1, C2, C4, C5, E4)

Behaviours encoded (ids from references/program-indicators/behaviours.md, verified on DHIS2 2.43.2; see RULES):
  C1  EVENT PIs with a disaggregation drop records that match 0 or >1 option in any mapped category from the TOTAL too
  C2  ENROLLMENT PIs keep such records in the total but not in any cell
  C3  blank TEXT compared with ==/!= : EVENT -> NULL (excluded); ENROLLMENT -> coalesced to '' (so `!= 'x'` is true)
  C4  ENROLLMENT PIs compare a blank NUMERIC data element as 0 in the PI filter only (not in mapping filters,
      not inside d2:hasValue, not in d2:daysBetween on dates)
  C5  ENROLLMENT PIs read the latest event of the stage, also after the period, unless an EVENT_DATE boundary caps it
  C6  EVENT PI rows only carry their own stage's values (other stages are NULL)
  E4  no aggregation type with a count-style expression -> value 1 whatever the count
  P1  offsetPeriods without offsetPeriodType are ignored
  P3  a missing AFTER_START (or BEFORE_END) boundary means no lower (upper) bound
Assumed, not yet probed (marked `assumed` in RULES): offsets in period types other than Monthly; EVENT PIs with
ENROLLMENT_DATE boundaries filter on the enrollment date; an AFTER_START EVENT_DATE boundary on an ENROLLMENT PI
sets a lower bound of the event lookup; V{enrollment_count} in an EVENT PI counts distinct enrollments.

Per-version switches: Engine(md, program, rules={'C4': False, ...}) or RULES_BY_VERSION; compare.py exposes them
as --rule C4=0. Re-verify on a new DHIS2 version with probe.py before relying on them.
"""
import calendar, datetime as dt, re
from collections import defaultdict, namedtuple

# ---------------------------------------------------------------- tokenizer / parser
TOK = re.compile(r"""\s*(?:
  (?P<num>\d+(?:\.\d+)?|\.\d+)
 |(?P<str>'[^']*'|"[^"]*")
 |(?P<de>\#\{\w+\.\w+\})
 |(?P<tea>A\{\w+\})
 |(?P<var>V\{\w+\})
 |(?P<fn>d2:\w+\(|containsItems\(|contains\(|(?:if|isNull|isNotNull|greatest|least|firstNonNull|abs|ceil|floor|round)\()
 |(?P<op>==|!=|<=|>=|&&|\|\||[<>!+\-*/%^(),])
 |(?P<kw>true|false|and|or|not)
)""", re.X)


class ParseError(Exception):
    pass


class Unsupported(Exception):
    """A construct the evaluator does not model (the PI is reported as unsupported, not as a mismatch)."""


def tokenize(s):
    pos, out = 0, []
    s = s.rstrip()
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m or m.end() == pos:
            raise ParseError(f"bad token at {pos}: {s[pos:pos+20]!r}")
        pos = m.end()
        k = m.lastgroup
        out.append((k, m.group(k)))
    return out


PREC = {'||': 1, 'or': 1, '&&': 2, 'and': 2, '==': 3, '!=': 3, '<': 4, '>': 4, '<=': 4, '>=': 4,
        '+': 5, '-': 5, '*': 6, '/': 6, '%': 6, '^': 7}


class Parser:
    def __init__(self, s):
        self.t = tokenize(s)
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def next(self):
        tok = self.peek(); self.i += 1; return tok

    def parse(self):
        if not self.t:
            raise ParseError('empty')
        e = self.expr(0)
        if self.i != len(self.t):
            raise ParseError(f"trailing tokens {self.t[self.i:self.i+3]}")
        return e

    def expr(self, minp):
        left = self.unary()
        while True:
            k, v = self.peek()
            if k not in ('op', 'kw') or v not in PREC or PREC[v] < minp:
                return left
            self.next()
            right = self.expr(PREC[v] + 1)
            left = ('bin', {'and': '&&', 'or': '||'}.get(v, v), left, right)

    def unary(self):
        k, v = self.next()
        if v in ('!', 'not'):
            return ('not', self.unary())
        if v == '-':
            return ('neg', self.unary())
        if v == '+':
            return self.unary()
        if v == '(':
            e = self.expr(0)
            if self.next()[1] != ')':
                raise ParseError('missing )')
            return e
        if k == 'num':
            return ('num', float(v))
        if k == 'str':
            return ('str', v[1:-1])
        if k == 'kw' and v in ('true', 'false'):
            return ('bool', v == 'true')
        if k == 'de':
            s, d = v[2:-1].split('.')
            return ('de', s, d)
        if k == 'tea':
            return ('tea', v[2:-1])
        if k == 'var':
            return ('var', v[2:-1])
        if k == 'fn':
            name = v[:-1]
            args = []
            if self.peek()[1] != ')':
                while True:
                    args.append(self.expr(0))
                    if self.peek()[1] == ',':
                        self.next(); continue
                    break
            if self.next()[1] != ')':
                raise ParseError('missing ) after args')
            return ('fn', name, args)
        raise ParseError(f"unexpected {k} {v}")


def parse(s):
    return Parser(s).parse()


def walk(n):
    yield n
    for c in n[1:]:
        if isinstance(c, tuple):
            yield from walk(c)
        elif isinstance(c, list):
            for x in c:
                yield from walk(x)


def leaves(n, dates=True):
    """Data leaves of a node: de/tea and V{program_stage_id}, plus V{event_date}/V{enrollment_date} when dates=True."""
    return [x for x in walk(n) if x[0] in ('de', 'tea') or (x[0] == 'var' and x[1] == 'program_stage_id') or
            (dates and x[0] == 'var' and x[1] in ('event_date', 'enrollment_date', 'incident_date'))]


def conjuncts(n):
    """Flatten a top-level && chain."""
    if n[0] == 'bin' and n[1] == '&&':
        return conjuncts(n[2]) + conjuncts(n[3])
    return [n]


def disjuncts(n):
    if n[0] == 'bin' and n[1] == '||':
        return disjuncts(n[2]) + disjuncts(n[3])
    return [n]


FLIP = {'<': '>', '>': '<', '<=': '>=', '>=': '<=', '==': '==', '!=': '!='}


def comparisons(n):
    """Yield (term, op, literal_node) for every comparison of a non-literal term with a literal, oriented term-first."""
    for x in walk(n):
        if x[0] == 'bin' and x[1] in FLIP:
            a, b = x[2], x[3]
            if b[0] in ('num', 'str', 'bool') and a[0] not in ('num', 'str', 'bool'):
                yield a, x[1], b
            elif a[0] in ('num', 'str', 'bool') and b[0] not in ('num', 'str', 'bool'):
                yield b, FLIP[x[1]], a


def unparse(n):
    """Canonical text of a node (used as a key for 'same term')."""
    t = n[0]
    if t == 'num':
        return '%g' % n[1]
    if t == 'str':
        return repr(n[1])
    if t == 'bool':
        return 'true' if n[1] else 'false'
    if t == 'de':
        return '#{%s.%s}' % (n[1], n[2])
    if t == 'tea':
        return 'A{%s}' % n[1]
    if t == 'var':
        return 'V{%s}' % n[1]
    if t == 'not':
        return '!(' + unparse(n[1]) + ')'
    if t == 'neg':
        return '-(' + unparse(n[1]) + ')'
    if t == 'bin':
        return '(' + unparse(n[2]) + ' ' + n[1] + ' ' + unparse(n[3]) + ')'
    if t == 'fn':
        return n[1] + '(' + ', '.join(unparse(a) for a in n[2]) + ')'
    return str(n)


# ---------------------------------------------------------------- evaluation
def to_num(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except Exception:
        return None


def to_date(v):
    if v is None:
        return None
    if isinstance(v, dt.date):
        return v if not isinstance(v, dt.datetime) else v.date()
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def months_between(a, b):
    # postgres: extract(year from age(b,a))*12 + extract(month from age(b,a))
    sign = 1
    if b < a:
        a, b, sign = b, a, -1
    m = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        m -= 1
    return sign * m


def truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return bool(v)


class Mode:
    """Comparison rules for one evaluation. coalesce_text: blank text == '' (C3, ENROLLMENT);
    blank_num_zero: set of numeric data-element uids whose blank value compares as 0 (C4, ENROLLMENT filters)."""
    __slots__ = ('coalesce_text', 'blank_num_zero')

    def __init__(self, coalesce_text=False, blank_num_zero=()):
        self.coalesce_text = coalesce_text
        self.blank_num_zero = frozenset(blank_num_zero)


# Module-level defaults, kept for callers that set them directly (older scripts did P.COALESCE_TEXT = ...).
COALESCE_TEXT = False
NUMERIC_DES = set()


def _default_mode():
    return Mode(COALESCE_TEXT, NUMERIC_DES)


def _leaf(n, ctx, mode):
    v = ctx(n)
    if v is None and n[0] == 'de' and n[2] in mode.blank_num_zero:
        return 0.0
    return v


def ev(n, ctx, mode=None):
    """Evaluate node n. Returns float / str / bool / date / None (SQL NULL)."""
    mode = mode or _default_mode()
    t = n[0]
    if t in ('num', 'str', 'bool'):
        return n[1]
    if t in ('de', 'tea', 'var'):
        return _leaf(n, ctx, mode)
    if t == 'not':
        v = ev(n[1], ctx, mode)
        return None if v is None else (not truthy(v))
    if t == 'neg':
        v = to_num(ev(n[1], ctx, mode))
        return None if v is None else -v
    if t == 'bin':
        op = n[1]
        if op in ('&&', '||'):
            a, b = ev(n[2], ctx, mode), ev(n[3], ctx, mode)
            a = None if a is None else truthy(a); b = None if b is None else truthy(b)
            if op == '&&':
                if a is False or b is False:
                    return False
                return None if a is None or b is None else True
            if a is True or b is True:
                return True
            return None if a is None or b is None else False
        a, b = ev(n[2], ctx, mode), ev(n[3], ctx, mode)
        # C3: ENROLLMENT analytics coalesces text operands to '' (blank != 'X' is true); numbers/dates stay NULL
        if mode.coalesce_text and op in ('==', '!=') and (a is None) != (b is None) and isinstance(a if b is None else b, str):
            a = '' if a is None else a
            b = '' if b is None else b
        if a is None or b is None:
            return None
        if op in '+-*/%^':
            a, b = to_num(a), to_num(b)
            if a is None or b is None or (op in '/%' and b == 0):
                return None
            return {'+': lambda: a + b, '-': lambda: a - b, '*': lambda: a * b, '/': lambda: a / b,
                    '%': lambda: a % b, '^': lambda: a ** b}[op]()
        # comparison: date if either side is a date, numeric if either side is a number, else string
        if isinstance(a, dt.date) or isinstance(b, dt.date):
            a, b = to_date(a), to_date(b)
        elif isinstance(a, (int, float)) or isinstance(b, (int, float)):
            a, b = to_num(a), to_num(b)
        else:
            a, b = str(a), str(b)
        if a is None or b is None:
            return None
        return {'==': a == b, '!=': a != b, '<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[op]
    if t == 'fn':
        return _fn(n[1], n[2], ctx, mode)
    raise ValueError(t)


def _fn(name, args, ctx, mode):
    E = lambda a: ev(a, ctx, mode)
    if name in ('d2:hasValue', 'isNotNull', 'isNull'):
        a = args[0]
        v = ctx(a) if a[0] in ('de', 'tea', 'var') else E(a)   # raw value: C4 does not apply inside hasValue
        has = v is not None and v != ''
        return (not has) if name == 'isNull' else has
    if name in ('contains', 'containsItems'):
        v = E(args[0])
        if v is None:
            return None
        if name == 'contains':                                   # C8: substring match
            return all(str(E(a)) in str(v) for a in args[1:])
        items = [x.strip() for x in str(v).split(',')]
        return all(str(E(a)) in items for a in args[1:])
    if name in ('d2:daysBetween', 'd2:weeksBetween', 'd2:monthsBetween', 'd2:yearsBetween', 'd2:minutesBetween'):
        a, b = to_date(ctx(args[0]) if args[0][0] in ('de', 'tea', 'var') else E(args[0])), \
               to_date(ctx(args[1]) if args[1][0] in ('de', 'tea', 'var') else E(args[1]))
        if a is None or b is None:
            return None
        if name == 'd2:daysBetween':
            return float((b - a).days)
        if name == 'd2:weeksBetween':
            return float(int((b - a).days / 7))
        if name == 'd2:minutesBetween':
            return float((b - a).days * 1440)
        m = months_between(a, b)
        return float(m if name == 'd2:monthsBetween' else int(m / 12))
    if name == 'd2:addDays':   # I4: not supported by the server; evaluated here only for the generator
        a, k = to_date(E(args[0])), to_num(E(args[1]))
        return None if a is None or k is None else a + dt.timedelta(days=int(k))
    if name == 'd2:condition':
        if args[0][0] != 'str':
            raise Unsupported('d2:condition with a non-literal condition')
        c = ev(parse(args[0][1]), ctx, mode)
        return E(args[1]) if c is not None and truthy(c) else E(args[2])
    if name == 'if':
        c = E(args[0])
        return E(args[1]) if c is not None and truthy(c) else E(args[2])
    if name in ('d2:zing', 'd2:oizp'):
        v = to_num(E(args[0]))
        if v is None:
            return None
        return max(v, 0.0) if name == 'd2:zing' else (1.0 if v >= 0 else 0.0)
    if name in ('d2:zpvc', 'd2:countIfZeroPos'):
        return float(sum(1 for a in args if (to_num(E(a)) or -1) >= 0))
    if name in ('greatest', 'least', 'firstNonNull'):
        vals = [E(a) for a in args]
        if name == 'firstNonNull':
            return next((v for v in vals if v is not None), None)
        vals = [to_num(v) for v in vals if to_num(v) is not None]
        return (max if name == 'greatest' else min)(vals) if vals else None
    if name in ('abs', 'ceil', 'floor', 'round'):
        v = to_num(E(args[0]))
        if v is None:
            return None
        import math
        return float({'abs': abs, 'ceil': math.ceil, 'floor': math.floor, 'round': round}[name](v))
    raise Unsupported(name)


# ---------------------------------------------------------------- value types
NUMERIC_TYPES = {'NUMBER', 'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE', 'INTEGER_ZERO_OR_POSITIVE',
                 'PERCENTAGE', 'UNIT_INTERVAL'}
DATE_TYPES = {'DATE', 'DATETIME', 'AGE'}
BOOL_TYPES = {'BOOLEAN', 'TRUE_ONLY'}
COUNT_EXPR = re.compile(r"^\s*(V\{(event_count|tei_count|enrollment_count)\}|1)\s*$")


def typed(meta, v):
    """DHIS2 stored string -> evaluator value for a data element / attribute with metadata `meta`."""
    if v is None or v == '':
        return None
    vt = (meta or {}).get('valueType')
    if vt in NUMERIC_TYPES:
        return to_num(v)
    if vt in DATE_TYPES:
        return to_date(v)
    if vt in BOOL_TYPES:
        return 1.0 if str(v).lower() == 'true' else 0.0
    return v


# ---------------------------------------------------------------- periods
PERIOD_MONTHS = {'Monthly': 1, 'BiMonthly': 2, 'Quarterly': 3, 'QuarterlyNov': 3, 'SixMonthly': 6, 'SixMonthlyApril': 6,
                 'SixMonthlyNov': 6, 'Yearly': 12, 'FinancialApril': 12, 'FinancialJuly': 12, 'FinancialOct': 12,
                 'FinancialNov': 12}
PERIOD_DAYS = {'Daily': 1, 'Weekly': 7, 'WeeklyWednesday': 7, 'WeeklyThursday': 7, 'WeeklySaturday': 7,
               'WeeklySunday': 7, 'BiWeekly': 14}


def quarter_bounds(pe):
    if len(pe) != 6 or pe[4] != 'Q':
        raise ValueError(f'expected a quarter like 2026Q1, got {pe!r}')
    y, q = int(pe[:4]), int(pe[5])
    s = dt.date(y, 3 * q - 2, 1)
    return s, add_months(s, 3) - dt.timedelta(days=1)


def add_months(d0, k):
    """Same day k months later, clamped to the month's last day."""
    y, m = divmod(d0.year * 12 + d0.month - 1 + k, 12)
    return dt.date(y, m + 1, min(d0.day, calendar.monthrange(y, m + 1)[1]))


def month_end(d0):
    return dt.date(d0.year, d0.month, calendar.monthrange(d0.year, d0.month)[1])


def shift_quarter(pe, months):
    y, q = int(pe[:4]), int(pe[5])
    k = y * 12 + (q - 1) * 3 + months
    return f'{k // 12}Q{(k % 12) // 3 + 1}'


# ---------------------------------------------------------------- analytics semantics
RULES = {
    'C1': 'EVENT PIs: records matching 0 or >1 option in a mapped category are dropped from the total',
    'C2': 'ENROLLMENT PIs: such records stay in the total, only the cells lose them',
    'C3': 'ENROLLMENT PIs: blank text coalesced to empty string in ==/!=',
    'C4': 'ENROLLMENT PIs: blank numeric data element compares as 0 in the PI filter (not in mapping filters)',
    'C5': 'ENROLLMENT PIs: latest event of the stage, capped only by an EVENT_DATE boundary',
    'E4': 'no aggregation type with a count expression: value 1',
    'P1': 'offsetPeriods without offsetPeriodType ignored',
}
RULES_BY_VERSION = {'2.43': {k: True for k in RULES}}   # add other versions as they are re-verified

Window = namedtuple('Window', 'lo hi ev_lo ev_hi en_lo en_hi')
FAR_PAST, FAR_FUTURE = dt.date(1900, 1, 1), dt.date(2999, 12, 31)


class Engine:
    def __init__(self, md, program, version=None, rules=None):
        self.md = md
        self.prog = program
        self.DE = {x['id']: x for x in md.get('dataElements', [])}
        self.TEA = {x['id']: x for x in md.get('trackedEntityAttributes', [])}
        self.CC = {c['id']: c for c in md.get('categoryCombos', [])}
        self.CAT = {c['id']: c for c in md.get('categories', [])}
        self.MAPS = {m['id']: m for m in program.get('categoryMappings') or []}
        self.numeric_des = {k for k, x in self.DE.items() if x.get('valueType') in NUMERIC_TYPES and not x.get('optionSet')}
        v = '.'.join((version or '2.43').split('.')[:2])
        self.rules = dict(RULES_BY_VERSION.get(v, RULES_BY_VERSION['2.43']))
        self.rules.update(rules or {})
        self._parsed = {}

    def parsed(self, text):
        if text not in self._parsed:
            self._parsed[text] = parse(text)
        return self._parsed[text]

    # --- disaggregation
    def categories(self, p):
        """Non-default categories of the PI's category combo and attribute combo, in that order."""
        out = []
        for k in ('categoryCombo', 'attributeCombo'):
            c = self.CC.get((p.get(k) or {}).get('id'))
            if c and c.get('name') != 'default':
                out += [x['id'] for x in c.get('categories', []) if self.CAT.get(x['id'], {}).get('name') != 'default']
        return out

    def mapping_for(self, p, cat):
        ms = [self.MAPS[x] for x in p.get('categoryMappingIds') or [] if x and x in self.MAPS and self.MAPS[x]['categoryId'] == cat]
        return ms[0] if ms else None

    # --- periods
    def _offset(self, b):
        k = b.get('offsetPeriods') or 0
        ty = b.get('offsetPeriodType')
        if k and not ty and self.rules['P1']:
            return 0, None                         # P1: ignored
        if not k:
            return 0, None
        if ty in PERIOD_MONTHS:
            return k * PERIOD_MONTHS[ty], 'm'
        if ty in PERIOD_DAYS:
            return k * PERIOD_DAYS[ty], 'd'
        raise Unsupported(f'offsetPeriodType {ty}')

    def main_target(self, p):
        return 'EVENT_DATE' if p['analyticsType'] == 'EVENT' else 'ENROLLMENT_DATE'

    def query_pe(self, p, test_pe):
        """Quarter to query so a cohort PI's offset window lands on test_pe (P2). Falls back to test_pe."""
        main = self.main_target(p)
        try:
            ks = {self._offset(b) for b in p.get('analyticsPeriodBoundaries') or [] if b.get('boundaryTarget') == main}
        except Unsupported:
            return test_pe
        if len(ks) != 1:
            return test_pe
        k, unit = ks.pop()
        if k == 0 or unit != 'm' or k % 3:
            return test_pe
        return shift_quarter(test_pe, -k)

    def window(self, p, pe):
        """Window for reporting quarter pe. lo/hi: main date (event date for EVENT, enrollment date for ENROLLMENT);
        ev_lo/ev_hi: event lookup of ENROLLMENT PIs (C5); en_lo/en_hi: enrollment date of EVENT PIs (assumed)."""
        RS, RE = quarter_bounds(pe)
        main = self.main_target(p)
        w = {'lo': FAR_PAST, 'hi': FAR_FUTURE, 'ev_lo': FAR_PAST, 'ev_hi': FAR_FUTURE, 'en_lo': FAR_PAST, 'en_hi': FAR_FUTURE}
        for b in p.get('analyticsPeriodBoundaries') or []:
            k, unit = self._offset(b)
            after = b['analyticsPeriodBoundaryType'].startswith('AFTER')
            if unit == 'd':
                date = (RS if after else RE) + dt.timedelta(days=k)
            else:
                date = add_months(RS, k) if after else month_end(add_months(RE.replace(day=1), k))
            tgt = b.get('boundaryTarget')
            if tgt == main:
                key = 'lo' if after else 'hi'
            elif tgt == 'EVENT_DATE' and main == 'ENROLLMENT_DATE':
                if not self.rules['C5']:
                    continue
                key = 'ev_lo' if after else 'ev_hi'
            elif tgt in ('ENROLLMENT_DATE', 'INCIDENT_DATE') and main == 'EVENT_DATE':
                key = 'en_lo' if after else 'en_hi'
            else:
                raise Unsupported(f'boundary target {tgt} on a {p["analyticsType"]} PI')
            # several boundaries of one kind: the tightest wins
            w[key] = max(w[key], date) if after else min(w[key], date)
        return Window(**w)

    # --- record contexts
    def ctx(self, te, event, kind, w, RPS, RPE):
        """Leaf resolver for one analytics row: an event (EVENT PIs) or an enrollment (ENROLLMENT PIs)."""
        edate = to_date(event['date']) if event else None
        latest = {}

        def lookup(st, de):
            # C5: latest event of the stage with a value, within the event lookup window
            key = (st, de)
            if key not in latest:
                c = [e for e in te['events'] if e['stage'] == st and e['values'].get(de) not in (None, '')
                     and w.ev_lo <= to_date(e['date']) <= w.ev_hi]
                latest[key] = max(c, key=lambda e: e['date'])['values'][de] if c else None
            return latest[key]

        def ctx(n):
            if n[0] == 'tea':
                return typed(self.TEA.get(n[1]), (te.get('attrs') or {}).get(n[1]))
            if n[0] == 'var':
                v = n[1]
                if v == 'analytics_period_end': return RPE
                if v == 'analytics_period_start': return RPS
                if v == 'event_date': return edate
                if v == 'enrollment_date': return to_date(te.get('enrolledAt'))
                if v == 'incident_date': return to_date(te.get('incidentAt') or te.get('enrolledAt'))
                if v == 'current_date': return dt.date.today()
                if v == 'program_stage_id': return event['stage'] if event else None
                if v == 'event_status': return (event or {}).get('status', 'COMPLETED') if event else None
                if v == 'enrollment_status': return te.get('status', 'ACTIVE')
                if v == 'org_unit': return te.get('ou')
                if v in ('event_count', 'tei_count', 'enrollment_count'): return 1.0
                raise Unsupported(f'V{{{v}}}')
            st, de = n[1], n[2]
            if kind == 'EVENT':                                       # C6
                return typed(self.DE.get(de), event['values'].get(de)) if event['stage'] == st else None
            return typed(self.DE.get(de), lookup(st, de))
        return ctx

    def modes(self, kind):
        """(filter mode, expression/mapping mode) for a PI type."""
        enr = kind == 'ENROLLMENT'
        coalesce = enr and self.rules['C3']
        return (Mode(coalesce, self.numeric_des if enr and self.rules['C4'] else ()), Mode(coalesce, ()))

    # --- the PI value
    def evaluate(self, p, truth, pe, cats=None):
        """Expected analytics result of PI p for quarter pe over truth records (truth.json format).

        Returns {'units', 'expected_total', 'expected_total_ignoring_disagg', 'expected_total_in_cells',
        'expected_total_any_match', 'cells' {key: value}, 'units_in_no_cell', 'units_in_several_cells'}.
        expected_total applies C1/C2: it is what analytics should show. Raises Unsupported / ParseError."""
        kind = p['analyticsType']
        cats = self.categories(p) if cats is None else (cats or [])
        w = self.window(p, pe)
        RPS, RPE = quarter_bounds(pe)
        fmode, xmode = self.modes(kind)
        flt = self.parsed((p.get('filter') or '').strip() or 'true')
        ex = (p.get('expression') or '').strip()
        is_count = bool(COUNT_EXPR.match(ex))
        expr = None if is_count else self.parsed(ex)
        mapped = []
        for c in cats:
            m = self.mapping_for(p, c)
            opts = []
            for om in (m or {}).get('optionMappings', []):
                f = (om.get('filter') or '').strip()
                opts.append((om['optionId'], self.parsed(f) if f else None))
            mapped.append(opts)
        units = []                       # (te, enrollment, value, [matching option ids per category])
        for te in truth:
            if kind == 'EVENT':
                en = to_date(te.get('enrolledAt'))
                if en and not (w.en_lo <= en <= w.en_hi):
                    continue
                rows = [e for e in te['events'] if w.lo <= to_date(e['date']) <= w.hi]
            else:
                en = to_date(te.get('enrolledAt'))
                rows = [None] if en and w.lo <= en <= w.hi else []
            for e in rows:
                ctx = self.ctx(te, e, kind, w, RPS, RPE)
                if ev(flt, ctx, fmode) is not True:
                    continue
                val = 1.0 if is_count else to_num(ev(expr, ctx, xmode))
                matches = [[oid for oid, f in opts if f is not None and ev(f, ctx, xmode) is True] for opts in mapped]
                units.append((te.get('te'), te.get('enrollment'), val, matches))
        agg = p.get('aggregationType')

        def aggregate(us):
            if not us:
                return 0.0
            if ex == 'V{tei_count}':
                return float(len({u[0] for u in us}))
            if ex == 'V{enrollment_count}':
                return float(len({u[1] for u in us}))       # EVENT: distinct enrollments (assumed)
            if not agg or agg == 'NONE':
                if is_count and self.rules['E4']:
                    return 1.0                              # E4: averages the per-row 1
                raise Unsupported(f'aggregationType {agg} with expression {ex}')
            if agg == 'COUNT':
                return float(len(us))
            vals = [u[2] for u in us if u[2] is not None]
            if not vals:
                return 0.0
            if agg in ('SUM', 'AVERAGE_SUM_ORG_UNIT', 'LAST', 'LAST_AVERAGE_ORG_UNIT'):
                return float(sum(vals))
            if agg == 'MAX':
                return max(vals)
            if agg == 'MIN':
                return min(vals)
            if agg == 'AVERAGE':
                return sum(vals) / len(vals)
            raise Unsupported(f'aggregationType {agg}')

        exact = [u for u in units if all(len(m) == 1 for m in u[3])] if cats else units
        anym = [u for u in units if all(len(m) >= 1 for m in u[3])] if cats else units
        total_all, total_cells = aggregate(units), aggregate(exact)
        expected = total_cells if (kind == 'EVENT' and self.rules['C1']) else total_all
        cells = {}
        if cats:
            by = defaultdict(list)
            for u in exact:
                by['|'.join(m[0] for m in u[3])].append(u)
            cells = {k: aggregate(v) for k, v in by.items()}
        return {'units': len(units), 'expected_total': expected, 'expected_total_ignoring_disagg': total_all,
                'expected_total_in_cells': total_cells, 'expected_total_any_match': aggregate(anym), 'cells': cells,
                'units_in_no_cell': sum(1 for u in units if any(len(m) == 0 for m in u[3])),
                'units_in_several_cells': sum(1 for u in units if any(len(m) > 1 for m in u[3]))}
