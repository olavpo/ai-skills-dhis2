"""Generic fix generators: one function per *clear* pattern in references/fix-patterns.md.

Nothing here knows a program: fields, option codes, category options and mappings come from the
metadata (`Meta`), and the field a mapping disaggregates on is read from the mapping's own filters.

A PI generator takes (pi, meta, ctx) and edits `pi` (a deep copy of the exported object) in place;
a mapping generator takes (mapping, meta, ctx) and edits the mapping copy. Both return a list of
change notes and raise FixError when the pattern does not apply cleanly (the item then goes on the
"not proposed" list with the message). `ctx` is a dict for side outputs:
  ctx['unresolved'] -> [(group, message)]   parts of an item the pattern could not fix (partial fix)
  ctx['skipped']    -> [[mapping, option, before, would-be, reason]]   age-band safeguards
  ctx['opts']       -> per-group options from the fixes file (e.g. {'periodType': 'Monthly'})
"""
import hashlib, re

REF = r'(?:#\{\w+\.\w+\}|A\{\w+\}|V\{\w+\})'          # field / variable reference
Q = r"(?:'[^']*'|\"[^\"]*\")"                        # quoted literal (DHIS2 accepts both, I5)
DATE = r"'\d{4}-\d\d-\d\d'"
NUMERIC = {'NUMBER', 'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE', 'INTEGER_ZERO_OR_POSITIVE', 'PERCENTAGE', 'UNIT_INTERVAL'}
FLIP = {'>': '<', '<': '>', '>=': '<=', '<=': '>=', '==': '==', '!=': '!='}


class FixError(Exception):
    pass


def uid_for(seed):
    """Deterministic DHIS2-style UID (re-running the build gives the same ids)."""
    h = hashlib.sha256(seed.encode()).digest()
    a = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    return a[h[0] % 52] + ''.join((a + '0123456789')[b % 62] for b in h[1:11])


def ws_pattern(old):
    """Regex for `old` that ignores whitespace differences."""
    pat = r'\s*'.join(re.escape(c) for c in old if not c.isspace())
    return (r'\s*' + pat) if old[:1].isspace() else pat


def sub_once(text, old, new, what):
    """Whitespace-insensitive literal replace; the pattern must occur exactly once."""
    out, n = re.subn(ws_pattern(old), lambda m: new, text or '')
    if n != 1:
        raise FixError(f'{what}: pattern matched {n} times: {old!r}')
    return out.strip()


def norm(s):
    """Normalised literal for matching: case, spaces and punctuation ignored."""
    return re.sub(r'[^0-9a-z]', '', (s or '').casefold())


def quote(code):
    return '"%s"' % code if "'" in code else "'%s'" % code


def unquote(lit):
    return lit[1:-1]


# ------------------------------------------------------------------------------ metadata view
class Meta:
    """Lookups over the export that the generators need."""

    def __init__(self, md, program):
        self.md, self.program = md, program
        self.de = {x['id']: x for x in md.get('dataElements', [])}
        self.tea = {x['id']: x for x in md.get('trackedEntityAttributes', [])}
        self.stage = {x['id']: x for x in md.get('programStages', [])}
        self.cat = {x['id']: x for x in md.get('categories', [])}
        self.co = {x['id']: x for x in md.get('categoryOptions', [])}
        self.cc = {x['id']: x for x in md.get('categoryCombos', [])}
        self.maps = {m['id']: m for m in program.get('categoryMappings') or []}
        opts = {o['id']: o for o in md.get('options', [])}
        self.os = {}  # optionSet id -> [(code, name)]
        for s in md.get('optionSets', []):
            self.os[s['id']] = [(o.get('code'), o.get('name')) for o in (opts.get(r['id'], r) for r in s.get('options', []))
                                if o.get('code') is not None]
        for o in md.get('options', []):  # options exported only at top level
            sid = (o.get('optionSet') or {}).get('id')
            if sid and (o.get('code'), o.get('name')) not in self.os.setdefault(sid, []):
                self.os[sid].append((o.get('code'), o.get('name')))

    def field(self, ref):
        """Data element / attribute object for a #{stage.de} or A{tea} reference (None otherwise)."""
        m = re.fullmatch(r'#\{\w+\.(\w+)\}', ref) or re.fullmatch(r'A\{(\w+)\}', ref)
        if not m:
            return None
        return (self.de if ref.startswith('#') else self.tea).get(m[1])

    def field_name(self, ref):
        f = self.field(ref)
        return f.get('formName') or f.get('name') if f else ref

    def value_type(self, ref):
        f = self.field(ref)
        return f.get('valueType') if f else None

    def codes(self, ref):
        """[(code, name)] of the field's option set, or None when it has none."""
        f = self.field(ref)
        sid = ((f or {}).get('optionSet') or {}).get('id')
        return self.os.get(sid) if sid else None

    def co_name(self, oid):
        c = self.co.get(oid) or {}
        return c.get('name') or oid

    def pi_categories(self, p):
        """Non-default categories of the PI's category combo and attribute combo."""
        out = set()
        for k in ('categoryCombo', 'attributeCombo'):
            cc = self.cc.get((p.get(k) or {}).get('id'))
            if not cc or cc.get('name') == 'default':
                continue
            out |= {c['id'] for c in cc.get('categories', []) if self.cat.get(c['id'], {}).get('name') != 'default'}
        return out


# ------------------------------------------------------------------------------ PI patterns
ADD_R = re.compile(r'd2:addDays\(\s*(' + REF + r')\s*,\s*(-?\d+)\s*\)\s*(>=|<=|==|!=|>|<)\s*(' + REF + '|' + DATE + ')')
ADD_L = re.compile(r'(' + REF + '|' + DATE + r')\s*(>=|<=|==|!=|>|<)\s*d2:addDays\(\s*(' + REF + r')\s*,\s*(-?\d+)\s*\)')


def fix_adddays(p, M, ctx):
    """d2:addDays is not supported in server PI expressions (I4). addDays(D, n) op R  ==  daysBetween(R, D) op -n."""
    ch = []
    for field in ('filter', 'expression'):
        t = p.get(field) or ''
        if 'd2:addDays' not in t:
            continue
        def r_side(m):
            ch.append({'field': field, 'note': f'd2:addDays({m[1]}, {m[2]}) {m[3]} {m[4]} → d2:daysBetween({m[4]}, {m[1]}) {m[3]} {-int(m[2])}'})
            return f'd2:daysBetween({m[4]}, {m[1]}) {m[3]} {-int(m[2])}'
        def l_side(m):
            op = FLIP[m[2]]
            ch.append({'field': field, 'note': f'{m[1]} {m[2]} d2:addDays({m[3]}, {m[4]}) → d2:daysBetween({m[1]}, {m[3]}) {op} {-int(m[4])}'})
            return f'd2:daysBetween({m[1]}, {m[3]}) {op} {-int(m[4])}'
        t = ADD_L.sub(l_side, ADD_R.sub(r_side, t))
        if 'd2:addDays' in t:
            raise FixError(f'adddays: a d2:addDays in the {field} is not of the form d2:addDays(field, n) <op> date')
        p[field] = t
    if not ch:
        raise FixError('adddays: no d2:addDays found')
    return ch


def fix_eventcount(p, M, ctx):
    """V{event_count} in an ENROLLMENT PI fails (E1); V{enrollment_count} counts the enrollments."""
    if p.get('analyticsType') != 'ENROLLMENT':
        raise FixError('eventcount: not an enrollment indicator')
    if (p.get('expression') or '').strip() != 'V{event_count}':
        raise FixError('eventcount: expression is not exactly V{event_count}')
    if re.search(r'V\{event_(count|date)\}', p.get('filter') or ''):
        raise FixError('eventcount: the filter also uses an event variable; fixing only the expression would leave it broken')
    p['expression'] = 'V{enrollment_count}'
    return [{'field': 'expression', 'note': 'V{event_count} → V{enrollment_count}'}]


def fix_aggcount(p, M, ctx):
    """A count-style expression (V{event_count}, V{enrollment_count}, 1) with AVERAGE, MAX/MIN or no aggregation type
    returns 1 or fails in analytics (E2-E4, and SQL 42883 for ENROLLMENT + AVERAGE on 2.43.2); COUNT counts the rows."""
    e = (p.get('expression') or '').strip()
    if e not in ('V{event_count}', 'V{enrollment_count}', '1'):
        raise FixError(f'aggcount: expression {e!r} is not a plain count (V{{tei_count}} and value expressions need a decision)')
    old = p.get('aggregationType') or '(none)'
    if old in ('COUNT', 'SUM'):
        raise FixError(f'aggcount: aggregation type is already {old}')
    if e == 'V{event_count}' and p.get('analyticsType') == 'ENROLLMENT':
        raise FixError('aggcount: V{event_count} in an enrollment indicator; see the eventcount group')
    p['aggregationType'] = 'COUNT'
    return [{'field': 'aggregationType', 'note': f'aggregationType {old} → COUNT'}]


REV = [re.compile(r'V\{analytics_period_start\}\s*>=\s*(' + REF + r')\s*&&\s*V\{analytics_period_end\}\s*<=\s*\1(?![\w}])'),
       re.compile(r'(' + REF + r')\s*<=\s*V\{analytics_period_start\}\s*&&\s*\1\s*>=\s*V\{analytics_period_end\}')]


def fix_reversed(p, M, ctx):
    """'start >= X && end <= X' can only hold for a zero-length period: X between start and end."""
    t, n = p.get('filter') or '', 0
    for rx in REV:
        t, k = rx.subn(r'\1 >= V{analytics_period_start} && \1 <= V{analytics_period_end}', t)
        n += k
    if n != 1:
        raise FixError(f'reversed: period test matched {n} times')
    p['filter'] = t
    return [{'field': 'filter', 'note': 'date between period start and end (the comparison was reversed)'}]


CONTRA = re.compile(r'(' + REF + r')\s*==\s*(' + Q + r')\s*&&\s*\1\s*==\s*(' + Q + ')')


def fix_contradiction(p, M, ctx):
    """F == a && F == b (a != b) can never hold; when the name says either is meant: (F == a || F == b).
    Only run for items confirmed per item; other contradiction shapes go in the fixes file."""
    t = p.get('filter') or ''
    hits = [m for m in CONTRA.finditer(t) if unquote(m[2]) != unquote(m[3])]
    if len(hits) != 1:
        raise FixError(f'contradiction: "field == a && field == b" matched {len(hits)} times')
    m = hits[0]
    p['filter'] = t[:m.start()] + f'({m[1]} == {m[2]} || {m[1]} == {m[3]})' + t[m.end():]
    return [{'field': 'filter', 'note': f'{M.field_name(m[1])} {m[2]} AND {m[3]} → {m[2]} OR {m[3]}'}]


LIT_CMP = re.compile(r'(' + REF + r')\s*(==|!=)\s*(' + Q + ')')
LIT_FN = re.compile(r'(contains|containsItems)\(\s*(' + REF + r')\s*,\s*(' + Q + r')\s*\)')


def match_code(lit, codes, substring_ok=False):
    """The single option code a literal stands for, or (None, reason)."""
    cs = [c for c, _ in codes]
    if lit in cs:
        return lit, None
    if substring_ok and any(lit in c for c in cs):
        return lit, None  # contains(): a substring of codes is a legitimate search
    hit = {c for c, n in codes if norm(c) == norm(lit) or norm(n) == norm(lit)}
    if len(hit) == 1:
        return hit.pop(), None
    return None, f'{lit!r}: {len(hit)} option codes match' + (f' ({", ".join(sorted(hit))})' if hit else '')


def fix_literal_text(t, M, where):
    """Replace literals that are not option codes by their single matching code. Returns (text, notes, unresolved)."""
    edits, notes, bad = [], [], []
    for rx, fi, li, sub_ok in ((LIT_CMP, 1, 3, False), (LIT_FN, 2, 3, True)):
        for m in rx.finditer(t):
            codes = M.codes(m[fi])
            if codes is None:
                continue
            lit = unquote(m[li])
            code, why = match_code(lit, codes, sub_ok and m[1] == 'contains')
            if code is None:
                bad.append(f'{M.field_name(m[fi])} {why}')
            elif code != lit:
                edits.append((m.start(li), m.end(li), quote(code)))
                notes.append(f'{M.field_name(m[fi])}: {m[li]} → {quote(code)} (the option code)')
    for s, e, new in sorted(edits, reverse=True):
        t = t[:s] + new + t[e:]
    return t, notes, bad


def fix_literals(p, M, ctx):
    """Option-code literal: exactly one code matches after normalising case/space/punctuation (or the option name)."""
    ch, bad = [], []
    for field in ('filter', 'expression'):
        t, notes, b = fix_literal_text(p.get(field) or '', M, field)
        if notes:
            p[field] = t
            ch += [{'field': field, 'note': n} for n in notes]
        bad += b
    for b in bad:
        ctx['unresolved'].append(('literals', 'no single matching option code: ' + b))
    if not ch:
        raise FixError('literals: no literal with a single matching option code' + (f' ({"; ".join(bad)})' if bad else ''))
    return ch


# precedence: bracket each run of ||-joined operands at a level that mixes && and ||
def split_top(s):
    """[operand, op, operand, ...] at top level (outside quotes/brackets)."""
    out, depth, i, start, q = [], 0, 0, 0, None
    while i < len(s):
        c = s[i]
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0 and s[i:i + 2] in ('&&', '||'):
            out += [s[start:i].strip(), s[i:i + 2]]
            i += 2; start = i; continue
        i += 1
    out.append(s[start:].strip())
    return out


def wrapped(s):
    s = s.strip()
    if not (s.startswith('(') and s.endswith(')')):
        return False
    depth, q = 0, None
    for i, c in enumerate(s):
        if q:
            if c == q: q = None
        elif c in "'\"": q = c
        elif c == '(': depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0 and i != len(s) - 1:
                return False
    return True


def refs(s):
    return set(re.findall(r'#\{\w+\.\w+\}|A\{\w+\}', s))


def regroup(s, notes):
    parts = split_top(s)
    ops = parts[1::2]
    operands = [('(' + regroup(x.strip()[1:-1], notes) + ')') if wrapped(x) else x for x in parts[0::2]]
    if '&&' in ops and '||' in ops:
        chunks, cur = [], [operands[0]]
        for op, nxt in zip(ops, operands[1:]):
            if op == '&&':
                chunks.append(cur); cur = [nxt]
            else:
                cur.append(nxt)
        chunks.append(cur)
        out = []
        for ch in chunks:
            if len(ch) == 1:
                out.append(ch[0]); continue
            dedup = list(dict.fromkeys(re.sub(r'\s+', ' ', x) for x in ch))
            f = set().union(*(refs(x) for x in dedup))
            if len(f) != 1:
                raise FixError(f'precedence: an "or" group tests several fields ({", ".join(sorted(f)) or "none"}), so the intended reading is not clear')
            if len(dedup) < len(ch):
                notes.append(f'removed a duplicated alternative: {dedup[0]}')
            out.append(dedup[0] if len(dedup) == 1 else '(' + ' || '.join(dedup) + ')')
            if len(dedup) > 1:
                j = ' || '.join(dedup)
                notes.append('bracketed: ' + j[:140] + ('…' if len(j) > 140 else ''))
        return ' && '.join(out)
    res = operands[0]
    for op, nxt in zip(ops, operands[1:]):
        res += f' {op} {nxt}'
    return res


def fix_precedence(p, M, ctx):
    """'A && B || C' means (A && B) || C. Bracket the || runs, only when each run tests one field."""
    notes = []
    new = regroup(p.get('filter') or '', notes)
    if not notes:
        raise FixError('precedence: nothing to bracket')
    p['filter'] = new
    return [{'field': 'filter', 'note': n} for n in notes]


def fix_offsettype(p, M, ctx):
    """offsetPeriods without offsetPeriodType are ignored (P1). Set the type when the offsets are clearly month counts
    (a multiple of 3 months, or the number appears with 'month' in the name); a fixes-file periodType overrides."""
    forced = ctx['opts'].get('periodType')
    n = 0
    for b in p.get('analyticsPeriodBoundaries') or []:
        k = b.get('offsetPeriods')
        if not k or b.get('offsetPeriodType'):
            continue
        if not forced and not (abs(k) % 3 == 0 or re.search(rf'\b{abs(k)}\s*-?\s*m(onth|th)', p.get('name') or '', re.I)):
            raise FixError(f'offsettype: offset {k} is not clearly a number of months; set "periodType" for this group in the fixes file')
        b['offsetPeriodType'] = forced or 'Monthly'
        n += 1
    if not n:
        raise FixError('offsettype: no offset without a period type')
    return [{'field': 'analyticsPeriodBoundaries', 'note': f'offsetPeriodType set to {forced or "Monthly"} on {n} boundar{"y" if n == 1 else "ies"}'}]


def fix_nullmap(p, M, ctx):
    """A null in categoryMappingIds makes analytics fail with HTTP 500 (E7). Remove it if the rest still covers the PI's categories."""
    ids_ = p.get('categoryMappingIds') or []
    if None not in ids_:
        raise FixError('nullmap: no null entry')
    keep = [x for x in ids_ if x]
    covered = {M.maps[x]['categoryId'] for x in keep if x in M.maps}
    missing = M.pi_categories(p) - covered
    if missing:
        raise FixError('nullmap: the remaining mappings do not cover ' + ', '.join(M.cat.get(c, {}).get('name', c) for c in missing))
    p['categoryMappingIds'] = keep
    k = ids_.count(None)
    return [{'field': 'categoryMappingIds', 'note': f'removed {k} empty (null) mapping entr{"y" if k == 1 else "ies"}'}]


NUM = r'-?\d+(?:\.\d+)?'
OPS = {'>=': lambda a, b: a >= b, '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '<': lambda a, b: a < b,
       '==': lambda a, b: a == b, '!=': lambda a, b: a != b}


def fix_numnull(p, M, ctx):
    """ENROLLMENT PIs compare a blank numeric data element as 0 in the filter (C4). Guard every comparison run that 0
    satisfies with d2:hasValue(field). Confirm per item that the comparison means "a value in this range"."""
    if p.get('analyticsType') != 'ENROLLMENT':
        raise FixError('numnull: not an enrollment indicator (blank numbers are NULL in event indicators)')
    t = p.get('filter') or ''
    cmp_ = r'(#\{\w+\.\w+\})\s*(>=|<=|==|!=|>|<)\s*(' + NUM + ')'
    run = re.compile(cmp_ + r'(?:\s*&&\s*\1\s*(?:>=|<=|==|!=|>|<)\s*' + NUM + ')*')
    edits, ch = [], []
    for m in run.finditer(t):
        ref = m[1]
        if (M.value_type(ref) or '') not in NUMERIC or f'd2:hasValue({ref})' in t.replace(' ', ''):
            continue
        before = t[:m.start()].rstrip()
        if before[-1:] in '+-*/%,' or (before.endswith('(') and re.search(r'[\w:]\($', before)):
            continue  # part of arithmetic or a function argument
        comps = re.findall(re.escape(ref) + r'\s*(>=|<=|==|!=|>|<)\s*(' + NUM + ')', m[0])
        if not all(OPS[op](0, float(v)) for op, v in comps):
            continue
        edits.append((m.start(), m.end(), f'(d2:hasValue({ref}) && {m[0]})'))
        ch.append({'field': 'filter', 'note': f'{M.field_name(ref)} must be recorded for “{m[0].strip()}” (a blank value was compared as 0)'})
    if not edits:
        raise FixError('numnull: no unguarded numeric comparison that a blank (0) satisfies')
    for s, e, new in sorted(edits, reverse=True):
        t = t[:s] + new + t[e:]
    p['filter'] = t
    return ch


def fix_periodend(p, M, ctx):
    """'Status at the end of the period': count everyone enrolled by the period end, reading the latest event up to the
    period end (C5, P3). Drop ENROLLMENT_DATE AFTER_START, keep ENROLLMENT_DATE BEFORE_END, add EVENT_DATE BEFORE_END.
    Confirm per item (the name must say active / current / at period end)."""
    if p.get('analyticsType') != 'ENROLLMENT':
        raise FixError('periodend: not an enrollment indicator')
    b = p.get('analyticsPeriodBoundaries') or []
    key = lambda x: (x['boundaryTarget'], x['analyticsPeriodBoundaryType'])
    keep = [x for x in b if key(x) == ('ENROLLMENT_DATE', 'BEFORE_END_OF_REPORTING_PERIOD') and not x.get('offsetPeriods')]
    drop = [x for x in b if key(x) == ('ENROLLMENT_DATE', 'AFTER_START_OF_REPORTING_PERIOD') and not x.get('offsetPeriods')]
    evb = [x for x in b if key(x) == ('EVENT_DATE', 'BEFORE_END_OF_REPORTING_PERIOD') and not x.get('offsetPeriods')]
    other = [x for x in b if x not in keep + drop + evb]
    if len(keep) != 1 or other or (not drop and evb):
        raise FixError('periodend: boundaries are not "enrollment date in the period" (± an event-date end boundary)')
    p['analyticsPeriodBoundaries'] = keep + (evb or [{'boundaryTarget': 'EVENT_DATE', 'analyticsPeriodBoundaryType': 'BEFORE_END_OF_REPORTING_PERIOD',
                                                      'id': uid_for('evb-' + p['id'])}])
    notes = (['removed “enrollment date after start of period”'] if drop else []) + ([] if evb else ['added “event date before end of period”'])
    return [{'field': 'analyticsPeriodBoundaries', 'note': '; '.join(notes)}]


# ------------------------------------------------------------------------------ mapping patterns
def mapping_refs(m):
    return {r for om in m.get('optionMappings', []) for r in re.findall(r'#\{\w+\.\w+\}|A\{\w+\}', om.get('filter') or '')}


def mchange(m, k, new, note, M):
    om = m['optionMappings'][k]
    c = {'option': M.co_name(om['optionId']), 'before': om.get('filter') or '', 'after': new, 'note': note}
    om['filter'] = new
    return c


# age bands from option names. Bands are [lo, hi) in thousandths of the field's unit.
YR = r'(?:years?|yrs?|y)'
MO = r'(?:months?|mths?|mos?)'


def band_from_name(n, scale):
    """Band of an option name like '15-19Yrs', '65+Yrs', '<1Yr', '6-11Mths', 'Under 5 years'; scale = field units per year."""
    n = re.sub(r'\s*[\(\[][^)\]]*[\)\]]\s*$', '', n.strip())   # '5-14 years (new)' -> '5-14 years'
    yr = lambda a: round(a * scale * 1000)
    mo = lambda a: round(a * scale / 12 * 1000)
    for unit, f in ((YR, yr), (MO, mo)):
        m = re.fullmatch(rf'(\d+)\s*(?:-|–|to)\s*(\d+)\s*{unit}', n, re.I)
        if m:
            return f(int(m[1])), f(int(m[2]) + 1)
        m = re.fullmatch(rf'(\d+)\s*\+\s*{unit}', n, re.I) or re.fullmatch(rf'(\d+)\s*{unit}\s*(?:and|&|or)\s*(?:above|over|older)', n, re.I)
        if m:
            return f(int(m[1])), None
        m = re.fullmatch(rf'(?:<|under|less than)\s*(\d+)\s*{unit}', n, re.I)
        if m:
            return 0, f(int(m[1]))
    return None


def band_from_filter(f, ref, step=1):
    """[lo, hi) in thousandths for a pure band filter on `ref`; None if it is not a simple band.
    step: the field's resolution in thousandths (1000 for an integer field, where '<= 14' is '< 15')."""
    if not f or '||' in f:
        return None
    rest = f.replace(ref, 'AGE')
    lo, hi = 0, None
    for op, v in re.findall(r'AGE\s*(>=|<=|>|<)\s*(\d+(?:\.\d+)?)', rest):
        x = float(v) * 1000
        if op == '>=': lo = max(lo, round(x))
        elif op == '>': lo = max(lo, int(x) + step)
        elif op == '<': hi = round(x) if hi is None else min(hi, round(x))
        elif op == '<=': hi = int(x) + step if hi is None else min(hi, int(x) + step)
    if re.sub(r'[\s&()]', '', re.sub(r'AGE\s*(>=|<=|>|<)\s*\d+(?:\.\d+)?', '', rest)):
        return None
    return lo, hi


def fmt_num(x):
    v = x / 1000
    return ('%g' % v) if abs(v - round(v, 3)) < 1e-9 else ('%.3f' % v)


def overlap(x, y):
    if not x or not y:
        return False
    inf = 10 ** 12
    return x[0] < (y[1] if y[1] is not None else inf) and y[0] < (x[1] if x[1] is not None else inf)


def nested(x, y):
    inf = 10 ** 12
    hx, hy = x[1] if x[1] is not None else inf, y[1] if y[1] is not None else inf
    return (x[0] <= y[0] and hy <= hx) or (y[0] <= x[0] and hx <= hy)


def fix_agebands(m, M, ctx):
    """Malformed band filters and band filters that disagree with their option names are rewritten from the names:
    'a-bYrs' → >= a && < b+1; 'a+Yrs' → >= a; '<aYr' → < a (months likewise). Safeguards: the mapping filters on one
    numeric field; the field's unit (years or months) is read from the filters that already agree with their names;
    options whose names partially overlap a neighbour whose current band doesn't are left alone (inconsistent names);
    a mapping whose name bands partially overlap while the current bands overlap too mixes two schemes and is skipped.
    Returns changes tagged with group 'brokenmap' (malformed) or 'agebands'."""
    rs = mapping_refs(m)
    if len(rs) != 1:
        raise FixError(f'agebands: the mapping filters on {len(rs)} fields')
    ref = rs.pop()
    if (M.value_type(ref) or 'NUMBER') not in NUMERIC:
        raise FixError(f'agebands: {M.field_name(ref)} is not numeric')
    oms = m.get('optionMappings', [])
    names = [M.co_name(om['optionId']) for om in oms]
    step = 1000 if (M.value_type(ref) or '').startswith('INTEGER') else 1
    cur = [band_from_filter(om.get('filter'), ref, step) for om in oms]
    broken = [bool(re.fullmatch(r'(\s*' + re.escape(ref) + r')*\s*', om.get('filter') or '')) for om in oms]
    if not any(band_from_name(n, 1) for n in names):
        raise FixError('agebands: no option name is an age band')
    # overlap tests don't depend on the unit, so check the names first (in years)
    want = [band_from_name(n, 1) for n in names]
    amb, mixed = set(), []
    for i in range(len(oms)):
        for j in range(i + 1, len(oms)):
            if want[i] and want[j] and overlap(want[i], want[j]) and not nested(want[i], want[j]):
                if cur[i] and cur[j] and not overlap(cur[i], cur[j]):
                    amb |= {i, j}
                else:
                    mixed.append(f'{names[i]} / {names[j]}')
    if mixed:
        raise FixError('agebands: the option names form two overlapping schemes (' + '; '.join(mixed[:3]) + '); split the category first')
    # unit: years (scale 1) or months (scale 12), from options whose current band already agrees with the name
    score = {s: sum(1 for i, n in enumerate(names) if cur[i] and band_from_name(n, s) == cur[i] and cur[i][0]) for s in (1, 12)}
    if score[1] == score[12]:
        raise FixError('agebands: cannot tell from the filters whether the field is in years or months')
    scale = 1 if score[1] > score[12] else 12
    want = [band_from_name(n, scale) for n in names]
    held = 0
    ch = []
    for k, om in enumerate(oms):
        if not want[k] or (cur[k] == want[k] and not broken[k]):
            continue
        if cur[k] is None and not broken[k]:
            continue  # not a plain band: leave it
        f = om.get('filter') or ''
        had_lower = bool(re.search(r'>=?\s*0(?:\D|$)', f)) or broken[k]
        parts = ([f'{ref} >= {fmt_num(want[k][0])}'] if want[k][0] or had_lower else []) + \
                ([f'{ref} < {fmt_num(want[k][1])}'] if want[k][1] is not None else [])
        new = ' && '.join(parts)
        if k in amb:
            ctx['skipped'].append([m.get('mappingName'), names[k], f, new, 'option names overlap a neighbour whose current band does not'])
            held += 1
            continue
        hi = fmt_num(want[k][1]) if want[k][1] is not None else '∞'
        c = mchange(m, k, new, 'malformed filter written from the option name' if broken[k] else f'band now {fmt_num(want[k][0])}–{hi} ({"years" if scale == 1 else "months"})', M)
        c['group'] = 'brokenmap' if broken[k] else 'agebands'
        ch.append(c)
    if not ch:
        raise FixError('agebands: ' + ('the only changes are held back because neighbouring option names overlap (see skippedAgeChanges)'
                                       if held else 'every band already follows its option name'))
    return ch


def fix_mapliterals(m, M, ctx):
    """Option-code literals in mapping filters (same rule as for PIs)."""
    ch, bad = [], []
    for k, om in enumerate(m.get('optionMappings', [])):
        t, notes, b = fix_literal_text(om.get('filter') or '', M, 'mapping')
        bad += [f'{M.co_name(om["optionId"])}: {x}' for x in b]
        if notes:
            ch.append(mchange(m, k, t, '; '.join(notes), M))
    for b in bad:
        ctx['unresolved'].append(('mapliterals', 'no single matching option code: ' + b))
    if not ch:
        raise FixError('mapliterals: no literal with a single matching option code')
    return ch


def option_field(m, M):
    """The single option-set field the mapping's filters compare against."""
    rs = {r for r in mapping_refs(m) if M.codes(r) is not None}
    if len(rs) != 1:
        raise FixError(f'the mapping compares {len(rs)} option-set fields')
    return rs.pop()


def fix_nofilter(m, M, ctx):
    """A mapping option without a filter gets 'field == code' when exactly one unused code matches the option name."""
    ref = option_field(m, M)
    codes = M.codes(ref)
    used = {unquote(x) for om in m.get('optionMappings', []) for x in re.findall(Q, om.get('filter') or '')}
    ch, bad = [], []
    for k, om in enumerate(m.get('optionMappings', [])):
        if (om.get('filter') or '').strip():
            continue
        name = M.co_name(om['optionId'])
        hit = {c for c, n in codes if c not in used and (norm(c) == norm(name) or norm(n) == norm(name))}
        if len(hit) != 1:
            bad.append(f'{name}: {len(hit)} unused codes match')
            continue
        code = hit.pop()
        ch.append(mchange(m, k, f'{ref} == {quote(code)}', f'filter added: {M.field_name(ref)} = {quote(code)}', M))
        used.add(code)
    for b in bad:
        ctx['unresolved'].append(('nofilter', b))
    if not ch:
        raise FixError('nofilter: no option without a filter has exactly one matching code')
    return ch


def fix_mapprefix(m, M, ctx):
    """Prefix-coded options: every code of the option set starts with a group prefix that also starts a category option
    name ('ABC - Group …'); each such option becomes contains(field, 'ABC'). Confirm per mapping (ideally with another
    implementation that groups the same way). ctx['opts']['options'] may limit it to some option names."""
    ref = option_field(m, M)
    codes = [c for c, _ in M.codes(ref)]
    oms = m.get('optionMappings', [])
    only = set(ctx['opts'].get('options') or [])
    pref = {}
    for k, om in enumerate(oms):
        name = M.co_name(om['optionId'])
        g = re.match(r'\s*([A-Za-z0-9]{2,})\s*(?:-|–|:)', name)
        if g and (not only or name in only):
            pref[k] = g[1]
    if not pref:
        raise FixError('mapprefix: no option name starts with a code prefix')
    allp = set(pref.values())
    stray = [c for c in codes if not any(c.startswith(x) for x in allp)]
    if stray and not only:
        raise FixError('mapprefix: codes without one of the prefixes: ' + ', '.join(stray[:5]))
    ch = []
    for k, x in pref.items():
        if not any(c.startswith(x) for c in codes):
            raise FixError(f'mapprefix: no code starts with {x!r}')
        if any(x in c and not c.startswith(x) for c in codes):
            raise FixError(f'mapprefix: {x!r} also occurs inside other codes, so contains() would overmatch')
        n = sum(c.startswith(x) for c in codes)
        ch.append(mchange(m, k, f"contains({ref}, '{x}')", f'every {M.field_name(ref)} code starting with {x} ({n} codes)', M))
    return ch


# ------------------------------------------------------------------------------ catalogue
# Fixed group order. 'topics' = default review topic keys, 'checks' = lint checks a topic may carry; 'confirm' = only
# items confirmed per item (fixes file or decisions) are changed even when the topic is agreed.
GROUPS = [
    dict(key='adddays', kind='pi', fn=fix_adddays, topics=['adddays', 'unsupported-function'], checks=['unsupported-function'],
         title='Replace d2:addDays (not supported by server analytics)', change='d2:addDays(D, n) > V{analytics_period_end}  →  d2:daysBetween(V{analytics_period_end}, D) > -n',
         why='Same meaning, written with a function server analytics supports (d2:addDays fails in analytics but works on Android).'),
    dict(key='periodend', kind='pi', fn=fix_periodend, confirm=True, topics=['periodend', 'enrollment-window'], checks=['active-name-enrollment-start-boundary', 'active-name-no-event-cap'],
         title='“At the end of the period”: everyone enrolled by the period end, using events up to the period end',
         change='Boundaries: remove “enrollment date after start of period”; keep “enrollment date before end of period”; add “event date before end of period”.',
         why='Without the start boundary, clients enrolled in earlier periods are included. The event-date boundary makes the indicator read the latest event up to the period end, not a later one.'),
    dict(key='eventcount', kind='pi', fn=fix_eventcount, topics=['eventcount', 'eventvar-enrollment'], checks=['event-var-in-enrollment-pi'],
         title='V{event_count} in enrollment indicators', change='expression V{event_count}  →  V{enrollment_count}',
         why='V{event_count} does not exist for enrollment indicators (SQL error 42703). V{enrollment_count} counts the enrollments that pass the filter.'),
    dict(key='aggcount', kind='pi', fn=fix_aggcount, topics=['agg-on-count', 'noagg'], checks=['count-expression-wrong-aggregation', 'no-aggregation-type'],
         title='Count indicators with an aggregation type that can’t count', change='aggregationType AVERAGE / MAX / MIN / (none)  →  COUNT',
         why='With a count expression these aggregation types return 1 whatever the count, or fail in analytics. COUNT counts the events or enrollments that pass the filter.'),
    dict(key='reversed', kind='pi', fn=fix_reversed, topics=['reversed'], checks=['impossible-period-window', 'reversed-period-window'],
         title='Reporting-period test written backwards',
         change='V{analytics_period_start} >= D && V{analytics_period_end} <= D  →  D >= V{analytics_period_start} && D <= V{analytics_period_end}',
         why='The original condition can only be true for a zero-length period, so these indicators were always 0.'),
    dict(key='contradiction', kind='pi', fn=fix_contradiction, confirm=True, topics=['contradiction'], checks=['contradictory-equalities', 'contradictory-range', 'contradictory-filter'],
         title='Contradictory conditions', change='Per indicator (see each change).', identical=False,
         why='Each condition could never be true. The rewrite follows the indicator name.'),
    dict(key='literals', kind='pi', fn=fix_literals, topics=['literals'], checks=['literal-not-an-option-code', 'literal-whitespace'],
         title='Literals that do not match an option code (clear cases only)', change='Per literal (see each change).', identical=False,
         why='Only literals with exactly one matching option code are changed.'),
    dict(key='precedence', kind='pi', fn=fix_precedence, topics=['precedence'], checks=['mixed-and-or-without-parens'],
         title='Brackets around the “or” groups', change='Each run of “or” alternatives on the same field is put in brackets.', identical=False,
         why='&& binds tighter than ||, so A && B || C means (A && B) || C. Every bracketed group tests one field against alternative values, so the intended reading is clear.'),
    dict(key='offsettype', kind='pi', fn=fix_offsettype, topics=['offsettype'], checks=['offset-without-period-type'],
         title='Period offsets without a period type', change='offsetPeriodType: (empty)  →  Monthly',
         why='Without an offsetPeriodType DHIS2 ignores the offset, so cohort indicators count the current period.'),
    dict(key='nullmap', kind='pi', fn=fix_nullmap, topics=['nullmap'], checks=['null-mapping-id'],
         title='Remove the empty (null) entry from the indicator’s category mapping list', change='categoryMappingIds: [mapping, null, …]  →  [mapping, …]',
         why='A null entry makes analytics fail with HTTP 500. Only applied where the remaining mappings still cover every disaggregation category.'),
    dict(key='numnull', kind='pi', fn=fix_numnull, confirm=True, topics=['numnull', 'blank-as-zero'], checks=['enrollment-blank-number-as-zero'],
         title='Blank numbers count as 0 in enrollment indicators', change='Add d2:hasValue(field) in front of a numeric condition that 0 satisfies', identical=False,
         why='In enrollment indicators a blank numeric value is compared as 0, so clients with no value are counted by conditions such as “< 1000” or “>= 0”.'),
    dict(key='brokenmap', kind='mapping', fn=None, topics=['brokenmap', 'mapping-structure'], checks=['unbalanced-parens', 'server-rejects-mapping'],
         title='Malformed band filters in a mapping', change='Malformed option filters written from the option name', identical=False,
         why='One malformed mapping filter makes every indicator using the mapping fail.'),
    dict(key='agebands', kind='mapping', fn=fix_agebands, topics=['bandgaps', 'bandoverlap', 'agebands'],
         checks=['mapping-range-gap', 'mapping-range-overlap', 'mapping-range-vs-name'],
         title='Age bands follow the option names', change='Option “a-bYrs” → age >= a && age < b+1; “a+Yrs” → age >= a; “<aYr” → age < a', identical=False,
         why='Ages with decimals fall between bands such as “<= 14” and “>= 15”. Only options whose band changes are touched; roll-up options that overlap by design are left as they are.'),
    dict(key='mapliterals', kind='mapping', fn=fix_mapliterals, topics=['mapliterals'], checks=['literal-not-an-option-code', 'literal-whitespace'],
         title='Mapping filters that do not match an option code', change='Per literal (see each change).', identical=False,
         why='Only literals with exactly one matching option code are changed.'),
    dict(key='mapprefix', kind='mapping', fn=fix_mapprefix, confirm=True, topics=[], checks=[],
         title='Mapping options grouped by code prefix', change="Option “ABC - …” = contains(field, 'ABC')", identical=False,
         why='Every code of the option set starts with its group prefix, so the group is all codes with that prefix.'),
    dict(key='nofilter', kind='mapping', fn=fix_nofilter, topics=['nofilter'], checks=['mapping-option-without-filter'],
         title='Mapping options with no filter (clear cases only)', change='Per option (see each change).', identical=False,
         why='Only options whose name matches exactly one unused option code get a filter.'),
]
for g in GROUPS:
    g.setdefault('confirm', False)
    g.setdefault('identical', True)

# Reasons for topics that have no clear pattern (references/fix-patterns.md, "Not clear"), keyed by topic key or lint check.
NOT_CLEAR = {
    'event-pi-cross-stage': ('An event indicator reads several stages; making it an enrollment indicator changes the unit counted and which date the period applies to.',
                             'Choose per indicator: enrollment type (and which date the period applies to) or a single-stage definition.'),
    'mapping-stage-differs-from-pi': ('The disaggregation reads a field from another stage than the one counted.',
                                      'Add mappings for that stage (needs the field there) or change the indicator type.'),
    'de-not-in-stage': ('A field read by the filter is not in the stage.', 'Add the field to the stage (program change, no historic data) or drop the condition.'),
    'no-aggregation-type': ('The intended aggregation depends on what the indicator should return.', 'Choose the aggregation type per indicator.'),
    'count-expression-wrong-aggregation': ('The intended aggregation depends on what the indicator should return.', 'Choose the aggregation type per indicator.'),
    'contains-substring-overmatch': ('contains() may be meant as a substring search.', 'Confirm per field whether whole-item matching (containsItems) is meant.'),
    'mapping-option-values-unmapped': ('Records with unmapped values are dropped from event indicators.', 'Per mapping: add the missing values, an “Unknown/Other” option, or accept dropping.'),
    'mapping-range-overlap': ('Overlapping roll-up options need their own category.', 'Split the category (changes the category model and data set mapping) or accept.'),
    'duplicate-definition': ('Two indicators with the same definition: one of them is probably meant differently.', 'Decide per pair what each should count.'),
}
GENERIC_NOT_CLEAR = ('No deterministic fix pattern applies: the correction needs a choice.', 'The programme team decides the correction per indicator.')
