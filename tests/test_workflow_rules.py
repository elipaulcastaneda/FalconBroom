import pytest
from fbroom.workflow_rules import recipe_from_plain_english


def make_profile():
    return {
        "host_name": {"dtype": "utf8", "nulls": 0, "unique": 100},
        "host_since": {"dtype": "utf8", "nulls": 0, "unique": 100},
    }


def has_action(recipe, action):
    return any(getattr(s, "action", None) == action for s in recipe.cleaning_steps)


def assert_has_swap(recipe):
    if not has_action(recipe, "swap_by_types"):
        pytest.fail(f"Expected swap_by_types in recipe steps, got: {[s.action for s in recipe.cleaning_steps]}")


def test_various_swap_phrases_detected():
    profile = make_profile()
    phrases = [
        "Switch values in columns host_name and host_since in rows where the value of host_name is numerical or a date and where the value of host_since is text (except for dates)",
        "Swap host_name with host_since when host_name is numeric and host_since is text except dates",
        "Exchange between host_name and host_since where host_name contains numeric values and host_since is text",
        "Flip host_name and host_since for rows where host_name is numerical",
        "host_name and host_since should be swapped where host_name looks like a number",
    ]
    # additional variants
    phrases += [
        "Swap the values between host_name and host_since",
        "Swap contents of host_name and host_since",
        "Transpose host_name and host_since when host_name looks numeric",
        "Invert host_name and host_since where host_name is numeric",
        "Swap values of host_name with host_since",
    ]
    for p in phrases:
        r = recipe_from_plain_english(p, profile, "in.csv", "out.csv")
        assert_has_swap(r)


def test_put_phrase_detects_move_or_swap():
    profile = make_profile()
    text = "Put all numerical values of the host_name column in the host_since column"
    r = recipe_from_plain_english(text, profile, "in.csv", "out.csv")
    actions = [getattr(s, "action", None) for s in r.cleaning_steps]
    assert "swap_by_types" in actions or "move_by_type" in actions
    assert "impute" not in actions
