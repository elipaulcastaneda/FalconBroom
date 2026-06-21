import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from fbroom.workflow_rules import recipe_from_plain_english

def make_profile():
    return {
        "host_name": {"dtype": "utf8", "nulls": 0, "unique": 100},
        "host_since": {"dtype": "utf8", "nulls": 0, "unique": 100},
    }

phrases = [
    "Switch values in columns host_name and host_since in rows where the value of host_name is numerical or a date and where the value of host_since is text (except for dates)",
    "Swap host_name with host_since when host_name is numeric and host_since is text except dates",
    "Exchange between host_name and host_since where host_name contains numeric values and host_since is text",
    "Flip host_name and host_since for rows where host_name is numerical",
    "host_name and host_since should be swapped where host_name looks like a number",
]

for p in phrases:
    r = recipe_from_plain_english(p, make_profile(), "in.csv", "out.csv")
    actions = [s.action for s in r.cleaning_steps]
    print("PHRASE:", p)
    print("ACTIONS:", actions)
    for s in r.cleaning_steps:
        print("STEP:", s)
    print("---")
