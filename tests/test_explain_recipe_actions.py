import pytest
from fbroom.workflow_rules import explain_recipe
from fbroom.recipe_schema import CleaningStep, Recipe


def make_profile():
    return {
        "order_date": {"dtype": "date", "nulls": 2},
        "delivery_date": {"dtype": "date", "nulls": 0},
        "product": {"dtype": "str", "nulls": 0},
        "city": {"dtype": "str", "nulls": 5},
        "postal_code": {"dtype": "str", "nulls": 0},
        "age": {"dtype": "int", "nulls": 1},
    }


def test_explain_recipe_various_actions():
    profile = make_profile()
    steps = [
        CleaningStep(action='cast', column='order_date', params={'to_type': 'datetime', 'format': '%Y-%m-%d'}),
        CleaningStep(action='normalize', column='product', params={'case': 'lower'}),
        CleaningStep(action='impute', column='city', params={'strategy': 'empty_string'}),
        CleaningStep(action='move_by_type', column=None, params={'source': 'postal_code', 'target': 'city', 'type': 'string'}),
        CleaningStep(action='swap_by_types', column=None, params={'moves': [{'source': 'city', 'target': 'postal_code', 'type': 'string'}]}),
        CleaningStep(action='remove_by_type', column='product', params={'target_type': 'numeric'}),
        CleaningStep(action='map', column='product', params={'mapping': {'0': 'unknown', '1': 'known'}}),
        CleaningStep(action='regex_replace', column='product', params={'pattern': '^[A-Z]+', 'replace': ''}),
        CleaningStep(action='bucketize', column='age', params={'buckets': [{'min': 0, 'max': 5, 'label': 'low'}]}),
        CleaningStep(action='conditional', column='vip', params={'value': True, 'condition': {'column': 'age', 'op': '>', 'value': 65}}),
        CleaningStep(action='join', column=None, params={'left': 'A', 'right': 'B', 'keys': ['email'], 'fuzzy': True}),
        CleaningStep(action='deduplicate', column='id', params={}),
        CleaningStep(action='rename', column='cust_id', params={'new_name': 'customer_id'}),
    ]
    r = Recipe(sources=[{"path": "in.csv"}], cleaning_steps=steps, joins=[], outputs=[{"path": "out.csv"}])
    explanations = explain_recipe(r, profile)
    reasons = "\n".join(e.reason for e in explanations)
    # basic assertions that explanations contain key phrases for the types
    assert 'convert' in reasons.lower() or 'datetime' in reasons.lower()
    assert 'normalization' in reasons.lower() or 'normalize' in reasons.lower()
    assert 'missing values' in reasons.lower()
    assert 'move values' in reasons.lower() or 'move' in reasons.lower()
    assert 'swap' in reasons.lower() or 'swap/move' in reasons.lower()
    assert 'remove values' in reasons.lower() or 'remove' in reasons.lower()
    assert 'map specific' in reasons.lower() or 'map' in reasons.lower()
    assert 'regex' in reasons.lower()
    assert 'bucket' in reasons.lower()
    assert 'conditionally set' in reasons.lower() or 'condition' in reasons.lower()
    assert 'fuzzy' in reasons.lower() or 'join' in reasons.lower()
    assert 'duplicate' in reasons.lower() or 'dedupe' in reasons.lower()
    assert 'rename' in reasons.lower()
