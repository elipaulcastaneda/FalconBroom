import os
from fbroom.engine import Cleaner
from fbroom.recipe_schema import Recipe, CleaningStep


TEST_CSV = os.path.join('data', 'tmp_numeric_transform_test.csv')
OUT_CSV = os.path.join('data', 'outputs', 'tmp_numeric_transform_out.csv')


def write_test_csv():
    os.makedirs('data', exist_ok=True)
    with open(TEST_CSV, 'w', encoding='utf-8') as f:
        f.write('id,amount,currency\n')
        f.write('1,-10.5,USD\n')
        f.write('2,5.25,USD\n')
        f.write('3,-3.0,EUR\n')


def test_numeric_transform_scoping():
    write_test_csv()
    cleaner = Cleaner()
    step = CleaningStep(action='numeric_transform', column='amount', params={'operation': 'abs'})
    recipe = Recipe(sources=[{'path': TEST_CSV}], cleaning_steps=[step], outputs=[{'path': OUT_CSV}])
    preview = cleaner.preview_recipe(recipe, n=10)
    before = preview['before']
    after = preview['after']

    # before: first and third rows negative, second positive
    assert float(before[0].get('amount')) < 0
    assert float(before[1].get('amount')) > 0
    assert float(before[2].get('amount')) < 0

    # after: amounts should be absolute values
    assert float(after[0].get('amount')) == abs(float(before[0].get('amount')))
    assert float(after[1].get('amount')) == abs(float(before[1].get('amount')))
    assert float(after[2].get('amount')) == abs(float(before[2].get('amount')))

    # currency column should be untouched and preserve casing
    assert before[0].get('currency') == after[0].get('currency') == 'USD'
    assert before[2].get('currency') == after[2].get('currency') == 'EUR'
