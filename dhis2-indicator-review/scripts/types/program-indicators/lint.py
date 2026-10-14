"""Static lint of every program indicator and every program category mapping against the metadata export.

    python3 scripts/types/program-indicators/lint.py [--config pireview.json] [--metadata OVERLAY ...] [--pis IDS] [--tag TAG]

Input:  the export (cfg.metadata) of cfg.program, plus optional overlays (e.g. a proposed payload, see common.py).
Output: <work_dir>/findings.json (findings-<tag>.json with --tag): list of
        {"severity": "ERROR|WARN|INFO", "check": "<id>", "id": "<PI or mapping uid>", "name": "<PI name> | mapping: <name>",
         "detail": "..."}
Config keys: metadata, program, work_dir, field_resolution (numeric band gaps/overlaps are judged at the field's
real resolution: cfg.resolution()), optional "active_name_pattern" (regex for "active/current" indicator names;
default below). Offline, no instance needed.

Checks (id, severity, what). Ids are stable: review topics and assemble.py key on them.
  References
  unknown-stage                      ERROR  #{stage.de}: stage not in the export
  unknown-de                         ERROR  data element not in the export
  de-not-in-stage                    ERROR  data element is not in the stage it is read from (SQL 42703, E5)
  de-skip-analytics                  ERROR  data element has skipAnalytics on that stage
  unknown-tea                        ERROR  attribute not in the export
  tea-not-in-program                 ERROR  attribute not assigned to the program
  event-var-in-enrollment-pi         ERROR  V{event_count}/V{event_date} in an ENROLLMENT PI (SQL 42703, E1)
  unsupported-function               ERROR  function the server does not support in PIs (d2:addDays, I4)
  parse-error                        WARN   the local parser cannot read the text (server syntax check decides)
  Literals
  literal-not-an-option-code         ERROR  comparison literal is not an option code (hint: name / case / similar)
  numeric-compare-on-text-option     ERROR  < > <= >= on an option set whose value type is not numeric
  numeric-compare-on-non-numeric     ERROR  < > <= >= on a non-numeric, non-date field
  string-literal-on-numeric          WARN   quoted literal compared with a numeric field
  string-literal-on-boolean          ERROR  'true'/'Yes' literal on a BOOLEAN/TRUE_ONLY field (analytics stores 1/0)
  free-text-equality                 INFO   == / != on free text without an option set
  literal-whitespace                 WARN   literal with leading/trailing whitespace
  contains-literal-not-an-option-code ERROR/WARN contains() literal matches no code (ERROR) / is only a substring (WARN)
  contains-substring-overmatch       WARN   contains() literal is a code but also a substring of other codes (C8)
  contains-on-free-text              INFO   contains() on a free-text field
  Structure / logic
  unbalanced-parens                  ERROR  parentheses do not balance
  mixed-and-or-without-parens        WARN   && and || at one nesting level (&& binds tighter)
  contradictory-equalities           ERROR  one field required to equal two values (or equal and not equal) in one && chain
  contradictory-range                ERROR  numeric bounds on one term that no value satisfies
  impossible-period-window           ERROR  date must be <= period start and >= period end (reversed window)
  exact-day-equality                 WARN   d2:daysBetween(...) == N with N >= 7 counts only that exact day
  enrollment-blank-number-as-zero    WARN   ENROLLMENT filter where 0 satisfies a numeric comparison and no d2:hasValue
                                            guard: a blank value compares as 0 and is counted (C4)
  active-name-enrollment-start-boundary WARN "active/current" name on an ENROLLMENT PI with an AFTER_START enrollment
                                            boundary (counts only people enrolled in the period)
  active-name-no-event-cap           WARN   "active/current" ENROLLMENT PI reading event values without an EVENT_DATE
                                            BEFORE_END boundary (a later visit after the period decides, C5)
  PI configuration
  no-filter                          INFO   PI without a filter
  no-aggregation-type                ERROR  aggregationType missing (E3/E4)
  count-expression-wrong-aggregation ERROR  count expression with MAX/MIN/AVERAGE... (E2)
  value-expression-with-count        WARN   value expression with COUNT (counts rows, ignores the value)
  event-pi-not-equal-excludes-blank  INFO   EVENT PI with X != 'v': blank X excluded (included in ENROLLMENT PIs, C3)
  event-pi-cross-stage               ERROR  EVENT PI referencing several stages (C6: always 0)
  enrollment-pi-event-values         INFO   ENROLLMENT PI reading event values (latest event of the stage)
  event-pi-tei-count                 INFO   EVENT PI with V{tei_count} (distinct persons, C9)
  boundaries-missing                 ERROR/WARN no boundary (ERROR) / only one (WARN; fine for "on or before end")
  offset-without-period-type         ERROR  offsetPeriods without offsetPeriodType (ignored, P1)
  boundary-offsets-differ            ERROR  start and end offsets differ
  orgunit-field                      INFO   orgUnitField set
  unknown-category-combo             ERROR  category combo not in the export
  null-mapping-id                    ERROR  null in categoryMappingIds (HTTP 500, E7)
  unknown-mapping                    ERROR  categoryMappingId not defined on the program
  two-mappings-same-category         ERROR  two mappings for one category
  mapping-category-not-in-combo      ERROR  mapping for a category in neither combo
  mapping-stage-differs-from-pi      ERROR  EVENT PI with a mapping on another stage (C7: always 0)
  combo-category-without-mapping     ERROR  disaggregation category without a mapping (E8)
  duplicate-code / duplicate-name / duplicate-shortName  WARN  several PIs share the value
  duplicate-definition               WARN   different PIs with an identical definition (incl. boundaries, mappings)
  Category mappings (id = mapping uid, name "mapping: ...")
  mapping-unknown-category           ERROR  category not in the export
  mapping-missing-options            ERROR  category options without a mapping
  mapping-foreign-options            ERROR  mapped options not in the category
  mapping-duplicate-option           ERROR  option mapped twice
  mapping-option-without-filter      ERROR  option with an empty filter
  mapping-identical-filters          ERROR  same filter on several options
  mapping-range-vs-name              ERROR  numeric band disagrees with the option name (e.g. "15-19 years")
  mapping-range-gap                  ERROR  values between two bands that no option takes (at the field resolution)
  mapping-range-overlap              ERROR  values two bands both take (at the field resolution)
  mapping-option-values-unmapped     INFO   option-set values not mapped to any category option
  mapping-value-in-two-options       ERROR  one option-set value mapped to two category options
  (the reference/literal/structure checks above also run on every mapping filter)
"""
import collections as C, math, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import config, piexpr as P  # noqa: E401
import common

ACTIVE_DEFAULT = r'\b(active|current(ly)?|alive|still (on|in)|retained|on treatment|in care)\b'
NUMERIC = P.NUMERIC_TYPES | {'AGE'}
DATES = P.DATE_TYPES
OPERAND = r"(#\{\w+\.\w+\}|A\{\w+\})"
LIT = r"('(?:[^']*)'|\"(?:[^\"]*)\"|-?\d+(?:\.\d+)?|true|false)"
CMP = r"(==|!=|<=|>=|<|>)"
NOT_SUPPORTED = {'d2:addDays': 'I4: not supported in server analytics (the Android app evaluates it)'}


def main():
    ap = common.add_common(common.parser(__doc__))
    args = ap.parse_args()
    cfg = common.load(args)
    L = Lint(cfg, common.select_pis(cfg, args.items))
    L.run()
    out = common.tagged(cfg, 'findings.json', args.tag)
    common.write_json(out, L.findings)
    cnt = C.Counter((f['severity'], f['check']) for f in L.findings)
    for (s, c), n in sorted(cnt.items()):
        print(f"{s:5} {c:40} {n}")
    print('PIs with >=1 ERROR:', len({f['id'] for f in L.findings if f['severity'] == 'ERROR' and not f['name'].startswith('mapping')}))
    print('->', out)


class Lint:
    def __init__(self, cfg, pis):
        self.cfg, self.pis = cfg, pis
        d = self.d = cfg.md
        self.prog = cfg.program
        by = lambda k: {o['id']: o for o in d.get(k, [])}
        self.DE, self.OS, self.OPT, self.CC, self.CAT, self.COPT, self.TEA, self.STG = (by(k) for k in
            ['dataElements', 'optionSets', 'options', 'categoryCombos', 'categories', 'categoryOptions',
             'trackedEntityAttributes', 'programStages'])
        self.stage_des = {s['id']: {x['dataElement']['id']: x for x in s.get('programStageDataElements', [])} for s in d['programStages']}
        self.prog_teas = {x['trackedEntityAttribute']['id'] for x in self.prog.get('programTrackedEntityAttributes', [])}
        self.MAP = {m['id']: m for m in self.prog.get('categoryMappings') or []}
        self.os_codes = {k: [self.OPT[o['id']]['code'] for o in v.get('options', []) if o['id'] in self.OPT] for k, v in self.OS.items()}
        self.os_names = {k: [self.OPT[o['id']]['name'] for o in v.get('options', []) if o['id'] in self.OPT] for k, v in self.OS.items()}
        self.active = re.compile(cfg.active_name_pattern or ACTIVE_DEFAULT, re.I)
        self.map_stages = {}
        self.findings = []

    def add(self, sev, check, obj, detail):
        self.findings.append({'severity': sev, 'check': check, 'id': obj[0], 'name': obj[1], 'detail': detail})

    def run(self):
        self.mappings()
        self.indicators()

    # ------------------------------------------------------------ operands
    def resolve(self, tok):
        m = re.match(r"#\{(\w+)\.(\w+)\}", tok)
        if m:
            s, de = m.groups()
            return 'DE', {'stage': s, 'uid': de, 'obj': self.DE.get(de)}
        m = re.match(r"A\{(\w+)\}", tok)
        return 'TEA', {'uid': m.group(1), 'obj': self.TEA.get(m.group(1))}

    def label(self, tok):
        o = self.resolve(tok)[1]['obj']
        return f"{tok} ({o['name'] if o else '??'})"

    def node_meta(self, n):
        return self.DE.get(n[2]) if n[0] == 'de' else self.TEA.get(n[1]) if n[0] == 'tea' else None

    def resolution(self, term):
        if term[0] in ('de', 'tea'):
            return self.cfg.resolution(P.unparse(term), (self.node_meta(term) or {}).get('valueType'))
        if term[0] == 'fn' and term[1] in ('d2:daysBetween', 'd2:weeksBetween', 'd2:monthsBetween', 'd2:yearsBetween'):
            return 1.0
        return 0.001

    # ------------------------------------------------------------ generic text checks
    def check_expr(self, text, obj, where, pi_type=None):
        """Reference, literal and structure checks on one expression/filter. Returns the set of stages referenced."""
        if not text or not text.strip():
            return set()
        stages = set()
        for s, de in re.findall(r"#\{(\w+)\.(\w+)\}", text):
            stages.add(s)
            if s not in self.STG:
                self.add('ERROR', 'unknown-stage', obj, f"{where}: stage {s} not in program")
            elif de not in self.DE:
                self.add('ERROR', 'unknown-de', obj, f"{where}: data element {de} not in export")
            elif de not in self.stage_des[s]:
                self.add('ERROR', 'de-not-in-stage', obj, f"{where}: {de} ({self.DE[de]['name']}) is not a data element of stage {self.STG[s]['name']}")
            elif self.stage_des[s][de].get('skipAnalytics'):
                self.add('ERROR', 'de-skip-analytics', obj, f"{where}: {de} has skipAnalytics=true on stage {self.STG[s]['name']}")
        for a in re.findall(r"A\{(\w+)\}", text):
            if a not in self.TEA:
                self.add('ERROR', 'unknown-tea', obj, f"{where}: attribute {a} not in export")
            elif a not in self.prog_teas:
                self.add('ERROR', 'tea-not-in-program', obj, f"{where}: attribute {a} ({self.TEA[a]['name']}) not assigned to program")
        for v in re.findall(r"V\{(\w+)\}", text):
            if pi_type == 'ENROLLMENT' and v in ('event_count', 'event_date'):
                self.add('ERROR', 'event-var-in-enrollment-pi', obj, f"{where}: V{{{v}}} in ENROLLMENT PI: analytics query fails (SQL 42703)")
        for fn, why in NOT_SUPPORTED.items():
            if fn + '(' in text.replace(' ', ''):
                self.add('ERROR', 'unsupported-function', obj, f"{where}: {fn}() {why}")
        self.literals(text, obj, where)
        self.structure(text, obj, where)
        try:
            ast = P.parse(text)
        except P.ParseError as e:
            self.add('WARN', 'parse-error', obj, f"{where}: local parser: {e}")
            return stages
        self.logic(ast, obj, where)
        return stages

    def literals(self, text, obj, where):
        pairs = list(re.findall(OPERAND + r"\s*" + CMP + r"\s*" + LIT, text))
        pairs += [(a, P.FLIP[op], l) for l, op, a in re.findall(LIT + r"\s*" + CMP + r"\s*" + OPERAND, text)]
        for tok, op, lit in pairs:
            o = self.resolve(tok)[1]['obj']
            if not o:
                continue
            vt = o.get('valueType')
            is_str = lit[0] in "'\""
            val = lit[1:-1] if is_str else lit
            if o.get('optionSet'):
                osid = o['optionSet']['id']
                codes = self.os_codes.get(osid, [])
                if is_str or op in ('==', '!='):
                    if val not in codes:
                        self.add('ERROR', 'literal-not-an-option-code', obj, f"{where}: {self.label(tok)} {op} {lit}: {self.code_hint(osid, val)}")
                elif op in ('<', '>', '<=', '>=') and vt not in NUMERIC:
                    self.add('ERROR', 'numeric-compare-on-text-option', obj, f"{where}: {self.label(tok)} {op} {lit} but valueType {vt}")
            else:
                if op in ('<', '>', '<=', '>=') and vt not in NUMERIC | DATES:
                    self.add('ERROR', 'numeric-compare-on-non-numeric', obj, f"{where}: {self.label(tok)} {op} {lit} but valueType {vt}")
                if vt in NUMERIC and is_str:
                    self.add('WARN', 'string-literal-on-numeric', obj, f"{where}: {self.label(tok)} {op} {lit} (valueType {vt})")
                if vt in P.BOOL_TYPES and is_str:
                    self.add('ERROR', 'string-literal-on-boolean', obj, f"{where}: {self.label(tok)} {op} {lit} (valueType {vt}; analytics stores 1/0)")
                if vt in ('TEXT', 'LONG_TEXT') and is_str and op in ('==', '!='):
                    self.add('INFO', 'free-text-equality', obj, f"{where}: {self.label(tok)} {op} {lit}: free-text field without option set, exact match on typed text")
            if is_str and val != val.strip():
                self.add('WARN', 'literal-whitespace', obj, f"{where}: literal {lit} has leading/trailing whitespace")
        for tok, args in re.findall(r"contains(?:Items)?\(\s*" + OPERAND + r"\s*,([^)]*)\)", text):
            o = self.resolve(tok)[1]['obj']
            if not o:
                continue
            for lit in re.findall(r"'([^']*)'|\"([^\"]*)\"", args):
                val = lit[0] or lit[1]
                if o.get('optionSet'):
                    osid = o['optionSet']['id']
                    codes = self.os_codes.get(osid, [])
                    if val not in codes:
                        sub = [c for c in codes if val in c]
                        hint = f"not an exact code; substring of {sub[:4]}" if sub and val not in self.os_names.get(osid, []) \
                            else self.code_hint(osid, val, always_false=True)
                        self.add('WARN' if sub else 'ERROR', 'contains-literal-not-an-option-code', obj,
                                 f"{where}: contains({self.label(tok)}, '{val}'): {hint}")
                    others = [c for c in codes if val in c and c != val]
                    if others and val in codes:
                        self.add('WARN', 'contains-substring-overmatch', obj,
                                 f"{where}: contains({self.label(tok)}, '{val}') also matches other codes {others[:5]}")
                if o.get('valueType') != 'MULTI_TEXT' and not o.get('optionSet'):
                    self.add('INFO', 'contains-on-free-text', obj, f"{where}: contains() on {self.label(tok)} (valueType {o.get('valueType')}, no option set)")

    def code_hint(self, osid, val, always_false=False):
        codes, names = self.os_codes.get(osid, []), self.os_names.get(osid, [])
        ci = [c for c in codes if c.strip().lower() == val.strip().lower()]
        if val in names:
            return f"matches an option NAME, not a code (code is '{codes[names.index(val)]}')"
        if ci:
            return f"differs from option code {ci[0]!r} only by case/whitespace"
        close = [c for c in codes if val.lower()[:6] in c.lower()][:4]
        return ("matches no option code at all, always false" if always_false else
                f"no such option code in option set '{self.OS[osid]['name']}'") + (f"; similar: {close}" if close else '')

    def structure(self, text, obj, where):
        s = re.sub(r"'[^']*'|\"[^\"]*\"", "''", text)
        stack, mixed, depth = [set()], False, 0
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == '(':
                stack.append(set()); depth += 1
            elif ch == ')':
                if len(stack) > 1:
                    mixed |= stack.pop() == {'&&', '||'}
                depth -= 1
            elif s.startswith('&&', i) or s.startswith('||', i):
                stack[-1].add(s[i:i + 2]); i += 1
            i += 1
        mixed |= any(x == {'&&', '||'} for x in stack)
        if depth != 0:
            self.add('ERROR', 'unbalanced-parens', obj, f"{where}: parentheses unbalanced ({depth:+d})")
        if mixed:
            self.add('WARN', 'mixed-and-or-without-parens', obj, f"{where}: && and || mixed at one nesting level (&& binds tighter): {text.strip()[:220]}")

    def logic(self, ast, obj, where):
        """Contradictions inside every && chain of the AST."""
        def chains(n, in_and=False):
            if n[0] == 'bin' and n[1] == '&&':
                if not in_and:
                    yield P.conjuncts(n)
                yield from chains(n[2], True); yield from chains(n[3], True)
                return
            for c in n[1:]:
                for x in (c if isinstance(c, list) else [c]):
                    if isinstance(x, tuple):
                        yield from chains(x, False)
        roots = list(chains(ast)) or [[ast]]
        for conj in roots:
            eq, ne, lo, hi, pe = C.defaultdict(set), C.defaultdict(set), {}, {}, C.defaultdict(set)
            for c in conj:
                if not (c[0] == 'bin' and c[1] in P.FLIP):
                    continue
                a, op, b = c[2], c[1], c[3]
                for x, o, y in ((a, op, b), (b, P.FLIP[op], a)):
                    if y[0] == 'var' and y[1] in ('analytics_period_start', 'analytics_period_end') and x[0] != 'var':
                        pe[P.unparse(x)].add((o, y[1]))
                if b[0] in ('num', 'str') or a[0] in ('num', 'str'):
                    term, o, lit = (a, op, b) if b[0] in ('num', 'str') else (b, P.FLIP[op], a)
                    if term[0] in ('num', 'str'):
                        continue
                    k = P.unparse(term)
                    if o == '==':
                        eq[k].add(lit[1])
                    elif o == '!=':
                        ne[k].add(lit[1])
                    elif lit[0] == 'num':
                        incl = o in ('>=', '<=')
                        if o in ('>', '>='):
                            if k not in lo or lit[1] > lo[k][0] or (lit[1] == lo[k][0] and not incl):
                                lo[k] = (lit[1], incl)
                        elif k not in hi or lit[1] < hi[k][0] or (lit[1] == hi[k][0] and not incl):
                            hi[k] = (lit[1], incl)
            for k, vals in eq.items():
                if len(vals) > 1:
                    self.add('ERROR', 'contradictory-equalities', obj, f"{where}: {k} must equal {sorted(map(str, vals))} at the same time (probably || intended)")
                elif vals & ne.get(k, set()):
                    self.add('ERROR', 'contradictory-equalities', obj, f"{where}: {k} must equal and not equal {sorted(map(str, vals & ne[k]))}")
            for k in set(lo) & set(hi):
                (a, ai), (b, bi) = lo[k], hi[k]
                if a > b or (a == b and not (ai and bi)):
                    self.add('ERROR', 'contradictory-range', obj, f"{where}: {k} must be {'>=' if ai else '>'} {a:g} and {'<=' if bi else '<'} {b:g}")
            for k, vals in eq.items():
                for v in vals:
                    if isinstance(v, float) and ((k in lo and (v < lo[k][0] or (v == lo[k][0] and not lo[k][1]))) or
                                                 (k in hi and (v > hi[k][0] or (v == hi[k][0] and not hi[k][1])))):
                        self.add('ERROR', 'contradictory-range', obj, f"{where}: {k} == {v:g} outside its own bounds")
            for k, rel in pe.items():
                below_start = any(v == 'analytics_period_start' and o in ('<', '<=', '==') for o, v in rel)
                above_end = any(v == 'analytics_period_end' and o in ('>', '>=', '==') for o, v in rel)
                if below_start and above_end:
                    self.add('ERROR', 'impossible-period-window', obj, f"{where}: {k} must be on/before the period start and on/after the period end (reversed window?)")
        for term, op, lit in P.comparisons(ast):
            if term[0] == 'fn' and term[1] == 'd2:daysBetween' and op == '==' and lit[0] == 'num' and lit[1] >= 7:
                self.add('WARN', 'exact-day-equality', obj, f"{where}: {P.unparse(term)} == {lit[1]:g} counts only that exact day; a range (>=) may be intended")

    # ------------------------------------------------------------ category mappings
    def mappings(self):
        for mid, m in self.MAP.items():
            obj = (mid, 'mapping: ' + (m.get('mappingName') or mid))
            cat = self.CAT.get(m.get('categoryId'))
            if not cat:
                self.add('ERROR', 'mapping-unknown-category', obj, f"category {m.get('categoryId')} not in export"); continue
            cat_opts = [o['id'] for o in cat.get('categoryOptions', [])]
            mapped = [om['optionId'] for om in m.get('optionMappings', [])]
            nm = lambda o: self.COPT.get(o, {}).get('name', o)
            missing = [nm(o) for o in cat_opts if o not in mapped]
            if missing:
                self.add('ERROR', 'mapping-missing-options', obj, f"category '{cat['name']}' options with no mapping: {missing}")
            extra = [o for o in mapped if o not in cat_opts]
            if extra:
                self.add('ERROR', 'mapping-foreign-options', obj, f"mapped options not in category '{cat['name']}': {extra}")
            dup = [o for o, n in C.Counter(mapped).items() if n > 1]
            if dup:
                self.add('ERROR', 'mapping-duplicate-option', obj, f"options mapped twice: {[nm(o) for o in dup]}")
            filters = {}
            stages = set()
            for om in m.get('optionMappings', []):
                oname = nm(om['optionId'])
                f = om.get('filter')
                if not f or not f.strip():
                    self.add('ERROR', 'mapping-option-without-filter', obj, f"option '{oname}' has no filter"); continue
                filters[oname] = f
                stages |= self.check_expr(f, obj, f"option '{oname}'")
            self.map_stages[mid] = stages
            dupf = [k for k, v in C.Counter(filters.values()).items() if v > 1]
            if dupf:
                self.add('ERROR', 'mapping-identical-filters', obj, f"identical filter on several options: {dupf[:3]}")
            self.bands(obj, filters)
            self.value_coverage(obj, filters)

    def bands(self, obj, filters):
        """Numeric bands: name vs range, gaps and overlaps at the field resolution."""
        rngs, term = {}, None
        for oname, f in filters.items():
            try:
                ast = P.parse(f)
            except P.ParseError:
                return
            if any(n[0] == 'bin' and n[1] == '||' for n in P.walk(ast)):
                return
            cmps = list(P.comparisons(ast))
            if not cmps or any(o in ('==', '!=') for _, o, _ in cmps) or any(l[0] != 'num' for _, _, l in cmps):
                return
            keys = {P.unparse(t) for t, _, _ in cmps}
            if len(keys) != 1 or (term is not None and P.unparse(term) not in keys):
                return
            term = cmps[0][0]
            lo, hi = (-math.inf, True), (math.inf, True)
            for _, op, lit in cmps:
                v = lit[1]
                if op in ('>=', '>'):
                    lo = (v, op == '>=')
                else:
                    hi = (v, op == '<=')
            rngs[oname] = (lo, hi)
        if not rngs or len(rngs) != len(filters):
            return
        res = self.resolution(term)
        self.range_vs_name(obj, filters, rngs, res)
        iv = sorted(rngs.items(), key=lambda kv: (kv[1][0][0], kv[1][1][0]))
        for (n1, (lo1, hi1)), (n2, (lo2, hi2)) in zip(iv, iv[1:]):
            # gap: a representable value above band 1 and below band 2
            if representable(hi1[0], not hi1[1], lo2[0], not lo2[1], res):
                self.add('ERROR', 'mapping-range-gap', obj, f"gap between '{n1}' (<{'=' if hi1[1] else ''}{hi1[0]:g}) and '{n2}' (>{'=' if lo2[1] else ''}{lo2[0]:g}) at resolution {res:g}")
            if representable(max(lo1[0], lo2[0]), lo2[1] if lo2[0] >= lo1[0] else lo1[1], min(hi1[0], hi2[0]),
                             hi1[1] if hi1[0] <= hi2[0] else hi2[1], res):
                self.add('ERROR', 'mapping-range-overlap', obj, f"overlap between '{n1}' and '{n2}' at {max(lo1[0], lo2[0]):g}..{min(hi1[0], hi2[0]):g}")

    UNIT = {'yr': 1, 'yrs': 1, 'y': 1, 'year': 1, 'years': 1, 'mth': 1 / 12, 'mths': 1 / 12, 'm': 1 / 12, 'month': 1 / 12,
            'months': 1 / 12, 'mo': 1 / 12, 'mos': 1 / 12, 'dys': 1 / 365.25, 'days': 1 / 365.25, 'day': 1 / 365.25,
            'd': 1 / 365.25, 'wks': 7 / 365.25, 'weeks': 7 / 365.25, 'wk': 7 / 365.25, 'hrs': 1 / 8766}

    def name_range(self, n):
        """[lo, hi) in years from an age-band style option name ("15-19 years", "<5y", "50+ yrs"), or None."""
        t = n.strip().lower().replace(' ', '')
        U = self.UNIT
        m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)([a-z]+)", t)
        if m and m.group(3) in U:
            u = U[m.group(3)]; return float(m.group(1)) * u, (float(m.group(2)) + 1) * u
        m = re.fullmatch(r"<(\d+(?:\.\d+)?)([a-z]+)", t)
        if m and m.group(2) in U:
            return 0.0, float(m.group(1)) * U[m.group(2)]
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\+([a-z]+)", t) or re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]+)(andabove|andover|plus)", t)
        if m and m.group(2) in U:
            return float(m.group(1)) * U[m.group(2)], math.inf
        return None

    def range_vs_name(self, obj, filters, rngs, res):
        named = {o: self.name_range(o) for o in rngs}
        named = {o: r for o, r in named.items() if r}
        if not named:
            return
        # the field may hold years, months, weeks or days: use the scale under which most names agree
        def bad(scale):
            out = []
            tol = 4 / 365.25 * scale + res          # ~4 days, plus one resolution step
            for o, (elo, ehi) in named.items():
                (lo, _), (hi, _) = rngs[o]
                elo, ehi = elo * scale, ehi * scale
                if (abs(lo - elo) > tol) if lo > -math.inf else elo > tol:
                    out.append((o, 'lower', elo, lo))
                if (abs(hi - ehi) > tol) if ehi < math.inf else hi < math.inf:
                    out.append((o, 'upper', ehi, hi))
            return out
        best = min((bad(s) for s in (1, 12, 365.25 / 7, 365.25)), key=len)
        if len(named) > 1 and len({o for o, *_ in best}) >= len(named):
            return                                   # no scale fits any name: names are not bands of this field
        for o, side, want, got in best:
            self.add('ERROR', 'mapping-range-vs-name', obj, f"option '{o}' expects {side} bound ~{want:g} but filter has {got:g}: {filters[o]}")

    def value_coverage(self, obj, filters):
        eq = [re.findall(OPERAND + r"\s*==\s*['\"]([^'\"]*)['\"]", f) for f in filters.values()]
        toks = {t for e in eq for t, _ in e}
        if len(toks) == 1 and eq and all(e for e in eq):
            tok = toks.pop()
            o = self.resolve(tok)[1]['obj']
            if o and o.get('optionSet'):
                used = {v for e in eq for _, v in e}
                unmapped = [c for c in self.os_codes.get(o['optionSet']['id'], []) if c not in used]
                if unmapped:
                    self.add('INFO', 'mapping-option-values-unmapped', obj,
                             f"values of {self.label(tok)} not mapped to any category option (records with these values fall outside every cell): {unmapped[:8]}{'...' if len(unmapped) > 8 else ''}")
                multi = [v for v, n in C.Counter(v for e in eq for _, v in e).items() if n > 1]
                if multi:
                    self.add('ERROR', 'mapping-value-in-two-options', obj, f"value(s) {multi} map to more than one category option: double counting")

    # ------------------------------------------------------------ program indicators
    def indicators(self):
        pis = self.pis
        sig = C.defaultdict(list)
        for f in ('code', 'name', 'shortName'):
            for v, n in C.Counter(p.get(f) for p in pis).items():
                if v and n > 1:
                    self.add('WARN', f'duplicate-{f}', ('-', v), f"{n} program indicators share {f} {v!r}")
        for p in pis:
            obj = (p['id'], p.get('name', p['id']))
            t = p.get('analyticsType')
            agg = p.get('aggregationType')
            expr, flt = p.get('expression') or '', p.get('filter') or ''
            stages = self.check_expr(expr, obj, 'expression', t) | self.check_expr(flt, obj, 'filter', t)
            if not flt.strip():
                self.add('INFO', 'no-filter', obj, f"no filter (expression {expr})")
            if not agg or agg == 'NONE':
                self.add('ERROR', 'no-aggregation-type', obj, f"aggregationType missing (expression {expr}): analytics does not COUNT (E3/E4)")
            elif P.COUNT_EXPR.match(expr) and agg not in ('COUNT', 'SUM'):
                self.add('ERROR', 'count-expression-wrong-aggregation', obj, f"count-style expression {expr} with aggregationType {agg}")
            elif not P.COUNT_EXPR.match(expr) and agg == 'COUNT':
                self.add('WARN', 'value-expression-with-count', obj, f"expression {expr} with aggregationType COUNT counts rows, ignores the value")
            if t == 'EVENT' and re.search(OPERAND + r"\s*!=\s*['\"]", flt):
                self.add('INFO', 'event-pi-not-equal-excludes-blank', obj, "EVENT PI uses X != 'v': blank X is excluded (in ENROLLMENT PIs blank X is included)")
            if t == 'EVENT' and len(stages) > 1:
                self.add('ERROR', 'event-pi-cross-stage', obj, f"EVENT PI references several stages {[self.STG[s]['name'] for s in stages if s in self.STG]}: each event row only has its own stage's values, so the other conditions are always null")
            if t == 'ENROLLMENT' and stages:
                self.add('INFO', 'enrollment-pi-event-values', obj, "ENROLLMENT PI reads event data elements (latest event of the stage); boundaries are on enrollment date")
            if t == 'EVENT' and 'tei_count' in expr:
                self.add('INFO', 'event-pi-tei-count', obj, "EVENT PI with V{tei_count}: counts distinct persons with >=1 matching event in the period")
            bds = p.get('analyticsPeriodBoundaries') or []
            if not bds:
                self.add('ERROR', 'boundaries-missing', obj, "no analytics period boundaries")
            elif len(bds) < 2:
                self.add('WARN', 'boundaries-missing', obj, f"one boundary only: {[(b['boundaryTarget'], b['analyticsPeriodBoundaryType']) for b in bds]} (no lower/upper bound, P3)")
            offs = [(b['analyticsPeriodBoundaryType'], b.get('offsetPeriods'), b.get('offsetPeriodType')) for b in bds if b.get('offsetPeriods')]
            if any(o[2] is None for o in offs):
                self.add('ERROR', 'offset-without-period-type', obj, f"boundary offsets without offsetPeriodType are ignored (P1): {offs}")
            if offs and len({(b.get('offsetPeriods') or 0) for b in bds}) > 1:
                self.add('ERROR', 'boundary-offsets-differ', obj, f"start/end offsets differ: {offs}: window is empty or not the intended length")
            if p.get('orgUnitField'):
                self.add('INFO', 'orgunit-field', obj, f"orgUnitField={p['orgUnitField']}")
            self.semantics(p, obj, t, flt, stages, bds)
            mids = self.disaggregation(p, obj, t, stages)
            if mids is None:
                continue
            bnd = tuple(sorted((b['boundaryTarget'], b['analyticsPeriodBoundaryType'], b.get('offsetPeriodType'), b.get('offsetPeriods') or 0) for b in bds))
            sig[(t, agg, expr.strip(), flt.strip(), p['categoryCombo']['id'], (p.get('attributeCombo') or {}).get('id'), tuple(sorted(mids)), bnd)].append(p)
        for v in sig.values():
            if len(v) > 1:
                self.add('WARN', 'duplicate-definition', (v[0]['id'], v[0]['name']), "identical definition shared by: " + '; '.join(f"{x['id']} {x['name']}" for x in v))

    def semantics(self, p, obj, t, flt, stages, bds):
        """Lint for the verified behaviours that surprised people (C4, C5)."""
        if t != 'ENROLLMENT':
            return
        if flt.strip():
            try:
                ast = P.parse(flt)
            except P.ParseError:
                ast = None
            if ast is not None:
                guarded = {P.unparse(f[2][0]) for f in P.walk(ast) if f[0] == 'fn' and f[1] in ('d2:hasValue', 'isNotNull') and f[2]}
                seen = set()
                for term, op, lit in P.comparisons(ast):
                    if term[0] != 'de' or lit[0] != 'num' or term[2] not in self.DE:
                        continue
                    de = self.DE[term[2]]
                    if de.get('valueType') not in P.NUMERIC_TYPES or de.get('optionSet'):
                        continue
                    k = P.unparse(term)
                    if k in guarded or k in seen:
                        continue
                    if P.ev(('bin', op, ('num', 0.0), lit), lambda n: None) is True:
                        seen.add(k)
                        self.add('WARN', 'enrollment-blank-number-as-zero', obj,
                                 f"filter: {k} ({de['name']}) {op} {lit[1]:g} is true for 0, and ENROLLMENT PIs compare a blank value as 0 (C4): people without a value are counted; guard with d2:hasValue({k})")
        if not self.active.search(p.get('name') or ''):
            return
        if any(b['boundaryTarget'] == 'ENROLLMENT_DATE' and b['analyticsPeriodBoundaryType'].startswith('AFTER') for b in bds):
            self.add('WARN', 'active-name-enrollment-start-boundary', obj,
                     "name says active/current but the PI only counts people ENROLLED in the period (AFTER_START enrollment boundary); usually only BEFORE_END is intended")
        if stages and not any(b['boundaryTarget'] == 'EVENT_DATE' and b['analyticsPeriodBoundaryType'].startswith('BEFORE') for b in bds):
            self.add('WARN', 'active-name-no-event-cap', obj,
                     "name says active/current and the PI reads event values, but has no EVENT_DATE BEFORE_END boundary: the latest event even after the period decides (C5)")

    def disaggregation(self, p, obj, t, stages):
        cc = self.CC.get((p.get('categoryCombo') or {}).get('id'))
        if not cc:
            self.add('ERROR', 'unknown-category-combo', obj, f"categoryCombo {(p.get('categoryCombo') or {}).get('id')} not in export")
            return None
        ac = self.CC.get((p.get('attributeCombo') or {}).get('id'))
        cats = [c['id'] for c in cc.get('categories', [])] + ([c['id'] for c in ac.get('categories', [])] if ac and ac.get('name') != 'default' else [])
        mids = list(p.get('categoryMappingIds') or [])
        if any(x is None for x in mids):
            self.add('ERROR', 'null-mapping-id', obj, f"categoryMappingIds contains null (analytics HTTP 500, E7): {mids}")
            mids = [x for x in mids if x is not None]
        mcats = {}
        for mid in mids:
            m = self.MAP.get(mid)
            if not m:
                self.add('ERROR', 'unknown-mapping', obj, f"categoryMappingId {mid} not defined on program"); continue
            cname = self.CAT.get(m['categoryId'], {}).get('name', m['categoryId'])
            if m['categoryId'] in mcats:
                self.add('ERROR', 'two-mappings-same-category', obj, f"two mappings for category {cname}")
            mcats[m['categoryId']] = m
            if m['categoryId'] not in cats:
                self.add('ERROR', 'mapping-category-not-in-combo', obj, f"mapping '{m.get('mappingName')}' is for category '{cname}' which is in neither categoryCombo '{cc['name']}' nor attributeCombo '{ac['name'] if ac else None}'")
            ms = self.map_stages.get(mid) or set()
            if t == 'EVENT' and stages and ms and not ms <= stages:
                self.add('ERROR', 'mapping-stage-differs-from-pi', obj, f"mapping '{m.get('mappingName')}' filters on stage(s) {[self.STG[s]['name'] for s in ms if s in self.STG]} but EVENT PI rows are from {[self.STG[s]['name'] for s in stages if s in self.STG]}: that category is always null")
        for c in cats:
            if c not in mcats and self.CAT.get(c, {}).get('name') != 'default':
                self.add('ERROR', 'combo-category-without-mapping', obj, f"combo '{cc['name']}'/'{ac['name'] if ac else None}' category '{self.CAT.get(c, {}).get('name', c)}' has no category mapping: disaggregation cells cannot be produced (E8)")
        return mids


def representable(lo, lo_incl, hi, hi_incl, res):
    """Is there a multiple of res in the interval between lo and hi (inclusive flags)?"""
    if lo == -math.inf or hi == math.inf:
        return lo < hi
    eps = res * 1e-6
    first = math.ceil((lo - eps) / res) * res
    if not lo_incl and abs(first - lo) < eps:
        first += res
    return first < hi - eps or (abs(first - hi) < eps and hi_incl)


if __name__ == '__main__':
    main()
