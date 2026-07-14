import pytest
from fbroom.workflow_rules import recipe_from_plain_english


def make_profile():
    return {
        "a": {"dtype": "int", "nulls": 0},
        "b": {"dtype": "int", "nulls": 0},
        "c": {"dtype": "str", "nulls": 0},
        "d": {"dtype": "int", "nulls": 0},
        "e": {"dtype": "str", "nulls": 0},
    }


def test_compound_nested_conditionals():
    text = "If a > 10 and (b < 5 or c == 'x') then set d to 1; if e == 'z' then normalize c"
    profile = make_profile()
    r = recipe_from_plain_english(text, profile, "in.csv", "out.csv")
    # Expect at least two conditional steps
    cond_steps = [s for s in r.cleaning_steps if s.action == 'conditional']
    assert len(cond_steps) >= 2
    # each conditional step should include a 'condition' dict and 'action_text'
    for s in cond_steps:
        assert s.params is not None
        assert 'condition' in s.params
        assert s.params.get('action_text')
