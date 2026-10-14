"""Test fixture only (not part of the skill): add program disaggregation with two deliberate defects to the SL
malaria program export, so the mapping checks, drop rules and band fixes get exercised.
  - age mapping on 'Age (years)' with a gap: 0-4 written as <= 3, so age 4 matches no option
  - sex mapping using the option NAME 'Female' instead of the code 'FEMALE'
and one new ENROLLMENT PI using both."""
import json, random, string
random.seed(11)
uid = lambda: random.choice(string.ascii_letters) + ''.join(random.choices(string.ascii_letters + string.digits, k=10))
d = json.load(open('export/metadata.json'))
prog = d['programs'][0]
AGE, SEX = 'A{B6TnnFMgmCk}', 'A{CklPZdOd6H1}'
def cat(name, opts):
    os_ = [{'id': uid(), 'name': f'{o} (malaria test)', 'shortName': f'{o} (mt)'} for o in opts]
    c = {'id': uid(), 'name': name, 'shortName': name, 'dataDimensionType': 'DISAGGREGATION', 'dataDimension': True,
         'categoryOptions': [{'id': o['id']} for o in os_]}
    return c, os_
age, age_o = cat('Age group (malaria test)', ['0-4 years', '5-14 years', '15+ years'])
sex, sex_o = cat('Sex (malaria test)', ['Female', 'Male'])
combo = {'id': uid(), 'name': 'Age group and sex (malaria test)', 'dataDimensionType': 'DISAGGREGATION',
         'categories': [{'id': age['id']}, {'id': sex['id']}]}
m_age = {'id': uid(), 'categoryId': age['id'], 'mappingName': 'Age group from Age (years)', 'optionMappings': [
    {'optionId': age_o[0]['id'], 'filter': f'{AGE} <= 3'},
    {'optionId': age_o[1]['id'], 'filter': f'{AGE} >= 5 && {AGE} <= 14'},
    {'optionId': age_o[2]['id'], 'filter': f'{AGE} >= 15'}]}
m_sex = {'id': uid(), 'categoryId': sex['id'], 'mappingName': 'Sex from attribute', 'optionMappings': [
    {'optionId': sex_o[0]['id'], 'filter': f"{SEX} == 'Female'"},
    {'optionId': sex_o[1]['id'], 'filter': f"{SEX} == 'MALE'"}]}
prog['categoryMappings'] = [m_age, m_sex]
targets = {'kL9jc0p6VlJ', 'Vmi9vYnmkfI', 'TN51OpurLU9'}
for p in d['programIndicators']:
    if p['id'] in targets:
        p['categoryCombo'] = {'id': combo['id']}; p['categoryMappingIds'] = [m_age['id'], m_sex['id']]
bd = lambda t: [{'boundaryTarget': t, 'analyticsPeriodBoundaryType': x} for x in
                ('AFTER_START_OF_REPORTING_PERIOD', 'BEFORE_END_OF_REPORTING_PERIOD')]
d['programIndicators'].append({'id': uid(), 'name': 'Malaria cases, persons (malaria test)', 'shortName': 'Malaria cases persons (mt)',
    'program': {'id': prog['id']}, 'analyticsType': 'ENROLLMENT', 'aggregationType': 'COUNT', 'expression': 'V{enrollment_count}',
    'filter': "#{hYyB7FUS5eR.aY4kB8kS521} == 'POSITIVE'", 'analyticsPeriodBoundaries': bd('ENROLLMENT_DATE'),
    'categoryCombo': {'id': combo['id']}, 'categoryMappingIds': [m_age['id'], m_sex['id']]})
d['categoryOptions'] += age_o + sex_o
d['categories'] += [age, sex]
d['categoryCombos'].append(combo)
json.dump(d, open('export/metadata-disagg.json', 'w'), indent=1)
json.dump({'categoryOptions': age_o + sex_o, 'categories': [age, sex], 'categoryCombos': [combo]}, open('export/inject-cats.json', 'w'))
json.dump({'programs': [prog], 'programIndicators': [p for p in d['programIndicators'] if p.get('categoryMappingIds')]},
          open('export/inject-prog.json', 'w'))
print('ok', combo['id'], m_age['id'], m_sex['id'])
