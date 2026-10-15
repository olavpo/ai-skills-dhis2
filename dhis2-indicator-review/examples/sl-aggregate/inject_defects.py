"""Test fixture only (not part of the skill): inject defects into the Sierra Leone aggregate export, so that the
lint checks, the import safeguards and the clear fix patterns get exercised. The demo itself already has some
(unsupported reporting-rate operands, Number-type "rate" indicators with denominator 1, ...).

    python3 inject_defects.py [--src export/metadata-raw.json] [--out export/metadata.json]

  D1 legacy COC      a category combo "Fixed/Outreach DELETE" (same category as "Fixed/Outreach", new COCs);
                     "ANC LLITN coverage" reads #{LLITN given at ANC 1st . <legacy Fixed>} -> the term never has data
  D2 *100 on Percent "PHU delivery rate (by ANC1)" numerator wrapped as (…)*100 on the Per cent type (×100 twice)
  D3 rate as Number  "NVP during labour rate" moved to the Number (factor 1) type -> reads 0.85 instead of 85
  D4 inner division  "Dropout rate Penta 1 - Measles": numerator ((P1)-(M))/(P1), denominator 1 -> E7132 when
                     Penta 1 is 0 for an org unit/period, and Σ of ratios instead of a pooled rate at parents
  D5 DE aggregation  "LLITN given at ANC 1st" aggregationType COUNT, "IPT 2nd dose given by TBA" AVERAGE
  D6 closed periods  "Child Health" gets one dataInputPeriod (2025-01 only): every 2026 value is rejected (E7643)
                     unless the import strips it
"""
import argparse, json

LEGACY_COMBO, LEGACY_FIXED, LEGACY_OUTREACH = 'LgcyFxOut01', 'LgcyFixed01', 'LgcyOutrch1'
CAT_LOC, OPT_FIXED, OPT_OUTREACH = 'fMZEcRHuamy', 'qkPbeWaFsnU', 'wbrDrL2aYEc'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='export/metadata-raw.json')
    ap.add_argument('--out', default='export/metadata.json')
    a = ap.parse_args()
    d = json.load(open(a.src))
    ind = {i['id']: i for i in d['indicators']}
    de = {x['id']: x for x in d['dataElements']}
    log = []
    # D1
    d['categoryCombos'].append({'id': LEGACY_COMBO, 'name': 'Fixed/Outreach DELETE', 'dataDimensionType': 'DISAGGREGATION',
                                'skipTotal': False, 'categories': [{'id': CAT_LOC}]})
    for uid, name, opt in ((LEGACY_FIXED, 'Fixed', OPT_FIXED), (LEGACY_OUTREACH, 'Outreach', OPT_OUTREACH)):
        d['categoryOptionCombos'].append({'id': uid, 'name': name, 'categoryCombo': {'id': LEGACY_COMBO},
                                          'categoryOptions': [{'id': opt}], 'ignoreApproval': False})
    i = ind['Tt5TAvdfdVK']
    i['numerator'] = i['numerator'].replace('#{ybzlGLjWwnK.pq2XI5kz2BY}', '#{ybzlGLjWwnK.%s}' % LEGACY_FIXED)
    log.append(('D1', i['name'], i['numerator']))
    # D2
    i = ind['n0GE1ISYrdM']
    i['numerator'] = '(' + i['numerator'] + ')*100'
    log.append(('D2', i['name'], i['numerator'][-40:]))
    # D3
    i = ind['GfYjGL16D0W']
    i['indicatorType'] = {'id': 'JkWynlWMjJR'}
    log.append(('D3', i['name'], 'Number (Factor 1)'))
    # D4
    i = ind['d9thHOJMROr']
    p1 = '(#{fClA2Erf6IO.Prlt0C1RF0s}+#{fClA2Erf6IO.V6L425pT3A0})'
    m = '(#{YtbsuPPo010.Prlt0C1RF0s}+#{YtbsuPPo010.V6L425pT3A0})'
    i['numerator'], i['denominator'] = f'({p1}-{m})/{p1}', '1'
    log.append(('D4', i['name'], i['numerator']))
    # D5
    de['ybzlGLjWwnK']['aggregationType'] = 'COUNT'
    de['SA7WeFZnUci']['aggregationType'] = 'AVERAGE'
    log.append(('D5', de['ybzlGLjWwnK']['name'] + ' COUNT', de['SA7WeFZnUci']['name'] + ' AVERAGE'))
    # D6
    ds = [x for x in d['dataSets'] if x['id'] == 'BfMAe6Itzgt'][0]
    ds['dataInputPeriods'] = [{'period': {'id': '202501'}, 'openingDate': '2025-01-01T00:00:00.000',
                               'closingDate': '2025-02-15T00:00:00.000'}]
    log.append(('D6', ds['name'], 'dataInputPeriods 202501 only'))
    json.dump(d, open(a.out, 'w'), indent=1)
    for x in log:
        print(*x)
    print('->', a.out)


if __name__ == '__main__':
    main()
