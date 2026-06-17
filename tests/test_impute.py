import os
import json
from fbroom.engine import Cleaner
from fbroom.recipe_schema import Recipe, CleaningStep

TEST_CSV = os.path.join('data', 'tmp_impute_test.csv')
OUT_CSV = os.path.join('data', 'outputs', 'tmp_impute_out.csv')


def write_test_csv():
    os.makedirs('data', exist_ok=True)
    with open(TEST_CSV, 'w', encoding='utf-8') as f:
        f.write('id,age,age_estimate,city\n')
        f.write('1,25,24,NY\n')
        f.write('2,,30,NY\n')
        f.write('3,40,,SF\n')
        f.write('4,,35,SF\n')


def test_impute_from_column():
    write_test_csv()
    cleaner = Cleaner()
    step = CleaningStep(action='impute', column='age', params={'strategy': 'from_column', 'source': 'age_estimate'})
    recipe = Recipe(sources=[{'path': TEST_CSV}], cleaning_steps=[step], outputs=[{'path': OUT_CSV}])
    preview = cleaner.preview_recipe(recipe, n=5)
    before = preview['before']
    after = preview['after']
    # row 2 age should be filled from age_estimate (30)
    assert before[1].get('age') in (None, '') or str(before[1].get('age')) == ''
    assert str(after[1].get('age')) == '30'


def test_impute_mean_group():
    # test group mean by city filling when source provided
    write_test_csv()
    cleaner = Cleaner()
    step = CleaningStep(action='impute', column='age', params={'strategy': 'mean', 'source': 'age_estimate', 'group_by': 'city'})
    recipe = Recipe(sources=[{'path': TEST_CSV}], cleaning_steps=[step], outputs=[{'path': OUT_CSV}])
    preview = cleaner.preview_recipe(recipe, n=5)
    after = preview['after']
    # For NY, mean of age_estimate is (24+30)/2=27 -> row1 already 25 remains, row2 fills to 27
    assert str(after[1].get('age')) in ('27.0', '27')


if __name__ == '__main__':
    write_test_csv()
    test_impute_from_column()
    test_impute_mean_group()
    print('impute tests passed')
