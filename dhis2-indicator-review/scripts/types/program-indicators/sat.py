"""Small constraint satisfier: find field values that make a PI filter (or a mapping filter) true or false.

Used by gen.py (one witness record per PI) and by the e2e scenario builder (scen.py) (fill disaggregation fields so a
record lands in exactly one option per category). Stdlib only; evaluation goes through piexpr, so the solver and the
engine check share one set of semantics.

    import sat
    S = sat.Solver(md, program, period=(start, end), seed=42, resolution=cfg.resolution)
    asg = S.witness(pi['filter'])            # Assign {leaf node: typed value} or None
    S.fmt(node, value)                        # typed value -> DHIS2 string for the payload
    S.satisfy(node, True, asg)                # mutates asg; True on success (check with ev afterwards)
    S.candidates(node)                        # values worth trying for a leaf (option codes, literals +- resolution,
                                              #   dates around the period and around d2:*Between literals)
    S.edge_values(node)                       # band edges from the program's categoryMappings (+- resolution)
    S.ctx_from(asg)                           # piexpr ctx for an assignment
    S.meta(node), S.uid(), S.stage_des        # helpers

Leaves are piexpr nodes: ('de', stage, de), ('tea', uid), ('var', 'event_date'|'enrollment_date'|'incident_date').
Assignment values are typed (float for numbers, datetime.date for dates, 'true'/'false' for booleans, str otherwise).

Back-compat: module-level functions (candidates, satisfy, ctx_from, fmt, meta, uid, leaves, Assign, d, prog) work
after sat.init(md_or_path, program_id=None, period=None, seed=7), or automatically when $SAT_MD is set at import
($SAT_PERIOD, e.g. 2026Q1, and $SAT_SEED optional).
"""
import datetime as dt, json, os, random, string, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import piexpr as P

DATE_VARS = ('event_date', 'enrollment_date', 'incident_date')
UNIT_DAYS = {'d2:daysBetween': 1, 'd2:weeksBetween': 7, 'd2:monthsBetween': 30.44, 'd2:yearsBetween': 365.25,
             'd2:minutesBetween': 1 / 1440}


class Assign(dict):
    pass


class Solver:
    def __init__(self, md, program, period, seed=42, resolution=None, rng=None):
        """md: metadata dict; program: program object; period: (start date, end date) of the test period;
        resolution(ref, value_type) -> smallest step of a numeric field (default: 1 for INTEGER*, else 0.001)."""
        self.d = md
        self.prog = program
        self.PS, self.PE = period
        self.rng = rng or random.Random(seed)
        self.resolution_fn = resolution
        self.DE = {x['id']: x for x in md.get('dataElements', [])}
        self.TEA = {x['id']: x for x in md.get('trackedEntityAttributes', [])}
        OPT = {x['id']: x for x in md.get('options', [])}
        self.codes = {k['id']: [OPT[o['id']]['code'] for o in k.get('options', []) if o['id'] in OPT]
                      for k in md.get('optionSets', [])}
        self.stage_des = {s['id']: {x['dataElement']['id'] for x in s.get('programStageDataElements', [])}
                          for s in md.get('programStages', [])}
        self.maps = {m['id']: m for m in program.get('categoryMappings') or []}
        self.lits, self.between, self.edges = {}, {}, {}
        for p in md.get('programIndicators', []):
            self.collect(p.get('filter')); self.collect(p.get('expression'))
        for m in self.maps.values():
            for om in m.get('optionMappings', []):
                self.collect(om.get('filter'), mapping=True)

    # --- literals seen per operand
    def collect(self, text, mapping=False):
        try:
            ast = P.parse(text or '1')
        except Exception:
            return
        for term, op, lit in P.comparisons(ast):
            if term[0] in ('de', 'tea'):
                self.lits.setdefault(term, set()).add(lit[1])
                if mapping and lit[0] == 'num' and op in ('<', '>', '<=', '>='):
                    self.edges.setdefault(term, set()).add(lit[1])
            elif term[0] == 'fn' and term[1] in UNIT_DAYS and lit[0] == 'num':
                for pos, arg in enumerate(term[2][:2]):     # pos 0: start date (lies before), 1: end date (after)
                    for leaf in P.leaves(arg):
                        self.between.setdefault(leaf, set()).add((term[1], lit[1], pos if arg == leaf else None))
        for n in P.walk(ast):
            if n[0] == 'fn' and n[1] in ('contains', 'containsItems') and n[2] and n[2][0][0] in ('de', 'tea'):
                for a in n[2][1:]:
                    if a[0] == 'str':
                        self.lits.setdefault(n[2][0], set()).add(a[1])

    def meta(self, node):
        if node[0] == 'de':
            return self.DE.get(node[2])
        if node[0] == 'tea':
            return self.TEA.get(node[1])
        return None

    def value_type(self, node):
        if node[0] == 'var':
            return 'DATE'
        return (self.meta(node) or {}).get('valueType', 'TEXT')

    def resolution(self, node):
        vt = self.value_type(node)
        if self.resolution_fn:
            return self.resolution_fn(P.unparse(node), vt)
        return 1.0 if vt.startswith('INTEGER') else 0.001

    def uid(self):
        r = self.rng
        return r.choice(string.ascii_letters) + ''.join(r.choice(string.ascii_letters + string.digits) for _ in range(10))

    # --- candidate values
    def _date_candidates(self, node):
        PS, PE = self.PS, self.PE
        out = [PE + dt.timedelta(days=k) for k in (-1, 0, 1, 10, 30, 60, 100, 200, -10, -30, -60, -89, -100, -200,
                                                   -365, -400, -600, -800)]
        mid = PS + (PE - PS) / 2
        for fn, lit, pos in self.between.get(node, ()):
            span = dt.timedelta(days=round(lit * UNIT_DAYS[fn]))
            for ref in (PS, mid, PE):
                for base in {0: (ref - span,), 1: (ref + span,)}.get(pos, (ref - span, ref + span)):
                    out += [base + dt.timedelta(days=k) for k in (-2, -1, 0, 1, 2)]
        return out

    def candidates(self, node):
        r = self.rng
        if node[0] == 'var':
            if node[1] == 'program_stage_id':
                return [x['id'] for x in self.prog.get('programStages', [])]
            if node[1] in DATE_VARS:
                return [self.PS + dt.timedelta(days=i) for i in range(0, (self.PE - self.PS).days, 7)] + [self.PS, self.PE]
            return [None]
        o = self.meta(node) or {}
        vt = o.get('valueType', 'TEXT')
        L = self.lits.get(node, set())
        if o.get('optionSet'):
            c = list(self.codes.get(o['optionSet']['id'], []))
            r.shuffle(c)
            return c or ['X']
        if vt in P.DATE_TYPES:
            return self._date_candidates(node)
        if vt in P.NUMERIC_TYPES:
            res = self.resolution(node)
            out = set()
            for v in L:
                v = P.to_num(v)
                if v is None:
                    continue
                out |= {v, v + 1, v - 1, v + res, v - res}
                if res <= 0.5:
                    out |= {v + 0.5, v - 0.5}
            out |= {0.0, 1.0, 3.0, 10.0, 30.0, 100.0, 500.0}
            out = sorted(round(x, 6) for x in out if x >= 0 or vt in ('NUMBER', 'INTEGER', 'INTEGER_NEGATIVE'))
            if vt == 'PERCENTAGE':
                out = [x for x in out if x <= 100]
            if vt == 'UNIT_INTERVAL':
                out = [x for x in out if 0 <= x <= 1] + [0.0, 0.5, 1.0]
            if vt.startswith('INTEGER'):
                out = sorted({float(int(x)) for x in out})
            r.shuffle(out)
            return out
        if vt in P.BOOL_TYPES:
            return ['true', 'false'] if vt == 'BOOLEAN' else ['true']
        c = [v for v in L if isinstance(v, str)] or ['Some text']
        r.shuffle(c)
        return c

    def edge_values(self, node):
        """Values on and around every band edge of a numeric field in the program's mapping filters, plus one value
        inside each band. Empty when the field has no numeric mapping edges."""
        E = sorted(self.edges.get(node, ()))
        if not E:
            return []
        res = self.resolution(node)
        out = set()
        for e in E:
            out |= {e - res, e, e + res}
        for a, b in zip(E, E[1:]):
            out.add(a + (b - a) / 2)
        out |= {E[0] / 2 if E[0] > 0 else E[0] - 1, E[-1] + max(1.0, res)}
        vt = self.value_type(node)
        out = {round(x, 6) for x in out if x >= 0 or vt in ('NUMBER', 'INTEGER', 'INTEGER_NEGATIVE')}
        if vt.startswith('INTEGER'):
            out = {float(int(round(x))) for x in out}
        return sorted(out)

    def fmt(self, node, v):
        if isinstance(v, dt.date):
            return v.isoformat()
        if isinstance(v, float):
            if self.value_type(node).startswith('INTEGER'):
                return str(int(round(v)))
            return '%.10g' % v
        return v

    def ctx_from(self, asg):
        PS, PE = self.PS, self.PE

        def ctx(n):
            if n[0] == 'var':
                if n[1] == 'analytics_period_end':
                    return PE
                if n[1] == 'analytics_period_start':
                    return PS
                if n[1] == 'current_date':          # evaluated at query time
                    return dt.date.today()
                if n[1] == 'program_stage_id':      # the stage of the event (gen.py places the event there)
                    return asg.get(n)
                if n[1] in DATE_VARS:
                    return P.to_date(asg.get(n, PS + dt.timedelta(days=40)))
                return None
            v = asg.get(n)
            if v is None:
                return None
            o = self.meta(n) or {}
            vt = o.get('valueType')
            if vt in P.DATE_TYPES and not isinstance(v, dt.date):
                return P.to_date(v)
            if vt in P.NUMERIC_TYPES:
                return P.to_num(v)
            if vt in P.BOOL_TYPES:
                return 1.0 if v in ('true', True, 1.0) else 0.0
            return v
        return ctx

    def is_(self, n, asg, want):
        try:
            return P.ev(n, self.ctx_from(asg), P.Mode()) is want
        except P.Unsupported:
            return False

    def satisfy(self, n, want, asg):
        """Mutate asg so that node n evaluates to `want` (True/False). Returns True on (probable) success:
        always confirm with ev afterwards."""
        t = n[0]
        if t == 'bin' and n[1] == '&&':
            if want:
                ok = self.satisfy(n[2], True, asg) and self.satisfy(n[3], True, asg)
                return (ok and self.is_(n, asg, True)) or self.search(n, want, asg)
            return self.satisfy(self.rng.choice([n[2], n[3]]), False, asg)
        if t == 'bin' and n[1] == '||':
            if not want:
                return self.satisfy(n[2], False, asg) and self.satisfy(n[3], False, asg)
            opts = [n[2], n[3]]
            self.rng.shuffle(opts)
            for o in opts:
                trial = Assign(asg)
                if self.satisfy(o, True, trial) and self.is_(o, trial, True):
                    asg.clear(); asg.update(trial); return True
            return False
        if t == 'not':
            return self.satisfy(n[1], not want, asg)
        if self.is_(n, asg, want):
            return True
        if t == 'fn' and n[1] in ('d2:hasValue', 'isNotNull', 'isNull'):
            leaf = n[2][0]
            if leaf[0] not in ('de', 'tea'):
                return False
            has = want if n[1] != 'isNull' else not want
            asg[leaf] = self.candidates(leaf)[0] if has else None
            return True
        if t == 'fn' and n[1] in ('contains', 'containsItems') and want:
            leaf = n[2][0]
            vals = [a[1] for a in n[2][1:] if a[0] == 'str']
            cur = asg.get(leaf)
            o = self.meta(leaf) or {}
            if o.get('valueType') == 'MULTI_TEXT' and cur:
                asg[leaf] = ','.join([cur] + vals)
            else:
                # pick an option code containing the literal if one exists
                c = [x for x in self.candidates(leaf) if isinstance(x, str) and all(v in x for v in vals)]
                asg[leaf] = c[0] if c else ','.join(vals)
            return self.is_(n, asg, want)
        # generic: fix all free leaves but one and search over that one; try each free leaf as the searched one,
        # data fields before variables (a date of birth is easier to move than the enrollment date)
        lv = list(dict.fromkeys(P.leaves(n)))
        free = sorted((l for l in lv if l not in asg), key=lambda l: l[0] == 'var')
        for target in (free or lv[-1:]):
            trial = Assign(asg)
            for l in free:
                if l != target:
                    trial[l] = self.candidates(l)[0]
            for c in self.candidates(target):
                trial[target] = c
                if self.is_(n, trial, want):
                    asg.clear(); asg.update(trial)
                    return True
        return False

    def search(self, n, want, asg):
        """Fallback for conjunctions whose parts share a field (5 <= age < 15): move one data field at a time
        over its candidates, judging the whole node."""
        for leaf in [l for l in dict.fromkeys(P.leaves(n)) if l[0] != 'var'] + [l for l in dict.fromkeys(P.leaves(n)) if l[0] == 'var']:
            trial = Assign(asg)
            for c in self.candidates(leaf):
                trial[leaf] = c
                if self.is_(n, trial, want):
                    asg.clear(); asg.update(trial)
                    return True
        return False

    def witness(self, text, tries=12, base=None):
        """An assignment that makes `text` true, or None. base: values already fixed (copied, not mutated)."""
        try:
            f = P.parse((text or '').strip() or 'true')
        except P.ParseError:
            return None
        for _ in range(tries):
            asg = Assign(base or {})
            if self.satisfy(f, True, asg) and self.is_(f, asg, True):
                return asg
        return None


# ---------------------------------------------------------------- module-level API (back-compat)
_S = None
d = prog = None


def init(md, program_id=None, period=None, seed=7, resolution=None):
    """Create the default solver. md: dict or path; period: (start, end) dates or a quarter string."""
    global _S, d, prog
    if isinstance(md, str):
        with open(md) as f:
            md = json.load(f)
    progs = md.get('programs', [])
    pr = next((p for p in progs if p['id'] == program_id), None) if program_id else progs[0]
    pr.setdefault('categoryMappings', [])
    if period is None or isinstance(period, str):
        period = P.quarter_bounds(period or f'{dt.date.today().year}Q1')
    _S = Solver(md, pr, period, seed=seed, resolution=resolution)
    d, prog = md, pr
    return _S


def _need():
    if _S is None:
        raise RuntimeError('call sat.init(...) first (or set $SAT_MD)')
    return _S


def candidates(node): return _need().candidates(node)
def satisfy(n, want, asg, depth=0): return _need().satisfy(n, want, asg)
def ctx_from(asg): return _need().ctx_from(asg)
def fmt(node, v): return _need().fmt(node, v)
def meta(node): return _need().meta(node)
def uid(): return _need().uid()
def leaves(n): return P.leaves(n)


if os.environ.get('SAT_MD'):
    init(os.environ['SAT_MD'], period=os.environ.get('SAT_PERIOD'), seed=int(os.environ.get('SAT_SEED', '7')))
