"""Fix generators for aggregate indicators: one function per *clear* pattern in references/indicators/fix-patterns.md.

Nothing here knows an instance: data elements, option combos and indicator types come from the export (Meta).
An indicator generator takes (indicator, meta, ctx) and edits the indicator copy in place; a data-element generator
takes (data element, meta, ctx) and edits the data element copy. Both return a list of change notes
({'field', 'note'}) and raise FixError when the pattern does not apply cleanly (the item then goes on the
"not proposed" list with the message). ctx: {'unresolved': [(group, message)], 'skipped': [...], 'opts': {...}}.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, '..', '..'))
if SCRIPTS not in sys.path:
    sys.path.append(SCRIPTS)
from fix.common import FixError, sub_once, ws_pattern, norm, uid_for  # noqa: E402,F401
import common  # noqa: E402
import indexpr as X  # noqa: E402

EXPLICIT_FIELD = 'numerator'
COUNT_TYPES = {'NUMBER', 'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_ZERO_OR_POSITIVE'}
NOT_A_COUNT = re.compile(r'population|stock|balance|average|mean|\brate\b|%|percent|staff|beds?\b|score|target|days\b', re.I)
SHORT_PT = {'Daily', 'Weekly', 'WeeklyWednesday', 'WeeklyThursday', 'WeeklySaturday', 'WeeklySunday', 'BiWeekly', 'Monthly'}


class Meta(common.Meta):
    """common.Meta plus the indicator types by factor."""

    def __init__(self, md):
        super().__init__(md)
        self.by_factor = {}
        for t in md.get('indicatorTypes', []):
            self.by_factor.setdefault(float(t.get('factor') or 0), []).append(t)


def make_meta(cfg):
    return Meta(cfg.md)


# ------------------------------------------------------------------------------ text helpers
def unwrap(t):
    """Strip one pair of outer parentheses when they enclose the whole text."""
    t = t.strip()
    while t.startswith('(') and t.endswith(')'):
        depth = 0
        for k, ch in enumerate(t):
            depth += ch == '('
            depth -= ch == ')'
            if depth == 0 and k < len(t) - 1:
                return t
        t = t[1:-1].strip()
    return t


def top_level(t, op):
    """Positions of operator op at parenthesis depth 0 (braces of #{...} are skipped)."""
    out, depth, brace = [], 0, 0
    for k, ch in enumerate(t):
        if ch == '{':
            brace += 1
        elif ch == '}':
            brace -= 1
        elif not brace:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == op and depth == 0:
                out.append(k)
    return out


# ------------------------------------------------------------------------------ indicator patterns
TIMES100_END = re.compile(r'\s*\*\s*100(?:\.0*)?\s*$')
TIMES100_START = re.compile(r'^\s*100(?:\.0*)?\s*\*\s*')


def fix_drop100(i, M, ctx):
    """Factor-100 type and an explicit * 100 on the whole numerator: drop the * 100."""
    if M.factor(i) != 100:
        raise FixError('drop100: the indicator type does not have factor 100')
    t = (i.get('numerator') or '').strip()
    if TIMES100_END.search(t):
        rest = TIMES100_END.sub('', t)
    elif TIMES100_START.search(t):
        rest = TIMES100_START.sub('', t)
    else:
        raise FixError('drop100: the numerator is not "<expression> * 100"')
    u = unwrap(rest)
    if top_level(u, '+') or top_level(u, '-') or top_level(u, '/'):
        if u == rest.strip():                                   # "a + b * 100" multiplies only b
            raise FixError('drop100: * 100 applies to the last term only, not to the whole numerator')
    i['numerator'] = u
    return [{'field': 'numerator', 'note': 'numerator: “× 100” removed (the Percent type already multiplies by 100)'}]


def fix_percenttype(i, M, ctx):
    """Name says %/rate, factor 1, no * 100: the single factor-100 indicator type."""
    if M.factor(i) != 1:
        raise FixError('percenttype: the factor is not 1')
    if TIMES100_END.search(i.get('numerator') or '') or TIMES100_START.search(i.get('numerator') or ''):
        raise FixError('percenttype: the numerator already multiplies by 100')
    cands = M.by_factor.get(100.0, [])
    if len(cands) != 1:
        raise FixError(f'percenttype: {len(cands)} indicator types have factor 100 (choose one)')
    old = M.it.get((i.get('indicatorType') or {}).get('id'), {}).get('name', '?')
    i['indicatorType'] = {'id': cands[0]['id']}
    return [{'field': 'indicatorType', 'note': f"indicator type {old} (×1) → {cands[0].get('name')} (×100)"}]


def fix_movedivisor(i, M, ctx):
    """Numerator 'X / Y' (Y reads data) with denominator 1: numerator X, denominator Y."""
    if (i.get('denominator') or '').strip() not in ('1', '1.0'):
        raise FixError('movedivisor: the denominator is not 1')
    t = unwrap(i.get('numerator') or '')
    pos = top_level(t, '/')
    if len(pos) != 1 or top_level(t, '+') or top_level(t, '-') or top_level(t, '*'):
        raise FixError('movedivisor: the numerator is not a single "X / Y" at the top level')
    x, y = unwrap(t[:pos[0]]), unwrap(t[pos[0] + 1:])
    try:
        if not any(n[0] == 'de' for n in X.operands(X.parse(y))):
            raise FixError('movedivisor: the divisor reads no data')
        X.parse(x)
    except (X.ParseError, X.Unsupported) as e:
        raise FixError(f'movedivisor: {e}')
    i['numerator'], i['denominator'] = x, y
    return [{'field': 'numerator', 'note': 'numerator “X / Y” → X'}, {'field': 'denominator', 'note': 'denominator 1 → Y (the divisor)'}]


def fix_cocremap(i, M, ctx):
    """#{de.coc} with a COC of another combo: the COC of the data element's combo with the same category options."""
    ch = []
    for side in ('numerator', 'denominator'):
        t = i.get(side) or ''

        def sub(m):
            de, coc, aoc = m.group(1), m.group(2), m.group(3)
            if not coc or coc == '*' or coc not in M.coc or de not in M.de:
                return m.group(0)
            combos = M.de_combos(de)
            if (M.coc[coc].get('categoryCombo') or {}).get('id') in combos:
                return m.group(0)
            same = [c for cc in combos for c in M.cc_cocs.get(cc, []) if M.coc_options(c) == M.coc_options(coc) and M.coc_fits_combo(c, cc)]
            if len(same) != 1:
                ctx['unresolved'].append(('cocremap', f"{M.name('de', de)} / {M.coc[coc].get('name')}: {len(same)} option combos of the current combo have the same options"))
                return m.group(0)
            ch.append({'field': side, 'note': f"{M.name('de', de)}: option combo {coc} ({M.cc.get((M.coc[coc].get('categoryCombo') or {}).get('id'), {}).get('name')}) "
                                              f"→ {same[0]} ({M.cc.get(M.coc[same[0]]['categoryCombo']['id'], {}).get('name')}), same options: {M.coc[coc].get('name')}"})
            return '#{%s.%s%s}' % (de, same[0], f'.{aoc}' if aoc else '')
        i[side] = common.REF_RX.sub(sub, t)
    if not ch:
        raise FixError('cocremap: no option combo with exactly one match in the current combo')
    return ch


def fix_legacyrate(i, M, ctx):
    """#{dataSet.REPORTING_RATE} (old form, not parsed: analytics reads 0) → R{dataSet.REPORTING_RATE}."""
    ch = []
    rx = re.compile(r'#\{\s*(\w{11})\s*\.\s*(REPORTING_RATE|REPORTING_RATE_ON_TIME|ACTUAL_REPORTS|ACTUAL_REPORTS_ON_TIME|EXPECTED_REPORTS)\s*\}')
    for side in ('numerator', 'denominator'):
        t = i.get(side) or ''
        bad = [m for m in rx.finditer(t) if m.group(1) not in M.ds]
        if bad:
            raise FixError(f'legacyrate: {bad[0].group(1)} is not a data set in the export')
        new, n = rx.subn(lambda m: 'R{%s.%s}' % (m.group(1), m.group(2)), t)
        if n:
            i[side] = new
            ch.append({'field': side, 'note': f'{side}: #{{data set.REPORTING_RATE}} → R{{data set.REPORTING_RATE}} ({n}×)'})
    if not ch:
        raise FixError('legacyrate: no #{dataSet.REPORTING_RATE} operand')
    return ch


# ------------------------------------------------------------------------------ data element patterns
def fix_decount(d, M, ctx):
    """aggregationType COUNT / NONE on a numeric count data element → SUM."""
    agg = d.get('aggregationType') or 'NONE'
    if agg not in ('COUNT', 'NONE'):
        raise FixError(f'decount: aggregation type is {agg}')
    if d.get('valueType') not in COUNT_TYPES:
        raise FixError(f"decount: value type {d.get('valueType')} is not a count")
    d['aggregationType'] = 'SUM'
    return [{'field': 'aggregationType', 'note': f'aggregation type {agg} → SUM'}]


def fix_deaverage(d, M, ctx):
    """aggregationType AVERAGE on a monthly count data element → SUM (stocks, populations, scores: not clear)."""
    if d.get('aggregationType') != 'AVERAGE':
        raise FixError('deaverage: aggregation type is not AVERAGE')
    if d.get('valueType') not in COUNT_TYPES:
        raise FixError(f"deaverage: value type {d.get('valueType')} is not a count")
    pt = M.de_period_type(d['id']) if M.de_ds.get(d['id']) else None
    if pt not in SHORT_PT:
        raise FixError(f'deaverage: entered {pt or "in no data set"}, not monthly or more often')
    if NOT_A_COUNT.search(d.get('name') or '') or NOT_A_COUNT.search(d.get('formName') or ''):
        raise FixError(f"deaverage: the name ({d.get('name')}) suggests a stock, population, rate or score, not a count")
    d['aggregationType'] = 'SUM'
    return [{'field': 'aggregationType', 'note': 'aggregation type AVERAGE → SUM (a monthly count of events)'}]


def explicit_aux(d, entry, M, group):
    """Explicit edit of a data element: {"aux": "<uid or name>", "group": ..., "set": {"aggregationType": "SUM"}, "note"}."""
    sets = entry.get('set') or {}
    allowed = {'aggregationType', 'zeroIsSignificant', 'valueType'}
    if not sets or set(sets) - allowed:
        raise FixError(f'explicit {group}: "set" must change only {sorted(allowed)}')
    notes = []
    for k, v in sets.items():
        notes.append(f'{k} {d.get(k)} → {v}')
        d[k] = v
    return {'field': ', '.join(sets), 'note': entry.get('note') or entry.get('justification') or '; '.join(notes)}


# ------------------------------------------------------------------------------ catalogue
GROUPS = [
    dict(key='legacyrate', kind='indicator', fn=fix_legacyrate, topics=['legacy-rate'], checks=['legacy-reporting-rate-operand'],
         title='Write reporting rates as R{data set.REPORTING_RATE}', change='#{dataSet.REPORTING_RATE}  →  R{dataSet.REPORTING_RATE}',
         why='The old form is not parsed any more: analytics silently reads the whole numerator as 0. Same meaning in the current syntax.'),
    dict(key='cocremap', kind='indicator', fn=fix_cocremap, topics=['coc-other-combo'], checks=['coc-not-in-de-combo'],
         title='Point terms at the option combo of the current category combo', change='#{de.<option combo of an old combo>}  →  #{de.<option combo of the current combo with the same options>}',
         identical=False, why='New data is only stored under the current combo’s option combos; the term read an old cell. Only changed where exactly one current option combo has the same category options.'),
    dict(key='drop100', kind='indicator', fn=fix_drop100, topics=['double-100'], checks=['factor-100-and-times-100'],
         title='Remove the extra × 100 from percentages', change='numerator (X) * 100  →  X  (Percent type kept)',
         why='The Percent type already multiplies by 100; the value was 100 times too high.'),
    dict(key='percenttype', kind='indicator', fn=fix_percenttype, topics=['percent-number'], checks=['percent-name-number-type'],
         title='Rates and percentages on the Percent type', change='indicator type Number (×1)  →  Percent (×100)', confirm=True,
         why='The name says %, rate or coverage; with factor 1 the indicator shows 0.85 instead of 85. Confirm per indicator: a few “rates” are meant per one.'),
    dict(key='movedivisor', kind='indicator', fn=fix_movedivisor, topics=['ratio-in-numerator'], checks=['division-in-numerator-denominator-1'],
         title='Move the divisor from the numerator to the denominator', change='numerator X / Y, denominator 1  →  numerator X, denominator Y', identical=False,
         why='Same values at every level (analytics aggregates the operands before evaluating the expression); a zero divisor gives no value instead of NaN, and the denominator is visible as such.'),
    dict(key='decount', kind='dataElement', fn=fix_decount, topics=['de-count'], checks=['de-aggregation-count', 'de-aggregation-none'],
         title='Count data elements aggregate with SUM', change='data element aggregation type COUNT / NONE  →  SUM',
         why='COUNT counts the reports, not the events reported. Changes every indicator, pivot table and map that uses the data element.'),
    dict(key='deaverage', kind='dataElement', fn=fix_deaverage, topics=['de-average'], checks=['de-aggregation-average'],
         title='Monthly count data elements aggregate with SUM', change='data element aggregation type AVERAGE  →  SUM', confirm=True,
         why='AVERAGE gives the mean of facilities and months; a count of events adds up. Only for monthly counts; confirm per data element.'),
]
for g in GROUPS:
    g.setdefault('confirm', False)
    g.setdefault('identical', True)

NOT_CLEAR = {
    'coc-not-fitting-combo': ('The option combos are left over from an earlier version of the category combo; the data is stored under them.',
                              'Decide with the data owners: rebuild the combo and migrate the data, or rewrite the indicators against the current categories.'),
    'inner-division': ('Where the division sits decides the meaning; guarding it or moving it changes values above facility level.',
                       'Per indicator: guard with if(b > 0, a / b, 0) or move the divisor to the denominator.'),
    'overlapping-operands': ('A term is counted twice; the intended term (often another option combo) cannot be inferred.', 'Name the intended term per indicator.'),
    'population-rate-not-annualized': ('Whether a period value or an annual rate is wanted is a decision.', 'Set annualized per indicator.'),
    'annualized-without-population': ('Whether the scaling is wanted is a decision.', 'Clear annualized or change the denominator per indicator.'),
    'rate-name-denominator-1': ('Either the name or the definition is wrong.', 'Rename or add the denominator, per indicator.'),
    'duplicate-definition': ('Two indicators with the same definition: one is probably meant differently.', 'Decide per pair.'),
    'sum-data-long-period': ('Whether the yearly value should show in months and quarters is a decision (AVERAGE_SUM_ORG_UNIT does that).', 'Decide per data element.'),
    'de-in-no-data-set': ('Which data set should collect the data element is a decision.', 'Add it to a data set or point the indicator elsewhere.'),
    'count-name-percent-type': ('The factor or the definition is wrong; which is a decision.', 'Per indicator.'),
    'name-factor-mismatch': ('The name or the factor is wrong; which is a decision.', 'Per indicator.'),
    'numerator-equals-denominator': ('The intended numerator or denominator cannot be inferred.', 'Per indicator.'),
    'unknown-de': ('The intended object cannot be inferred from a missing uid.', 'Name the intended object per indicator.'),
    'de-aggregation-average': ('Not a monthly count, or the name suggests a stock, population or score.', 'Choose SUM, AVERAGE_SUM_ORG_UNIT or LAST per data element.'),
}
GENERIC_NOT_CLEAR = ('No deterministic fix pattern applies: the correction needs a choice.', 'The indicator owners decide the correction per item.')
