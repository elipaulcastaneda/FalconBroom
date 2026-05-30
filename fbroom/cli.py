"""Simple CLI for local usage of the FalconBroom prototype."""
import argparse
import json
from .engine import Cleaner
from .recipe_schema import Recipe


def main():
    parser = argparse.ArgumentParser(prog="fbroom")
    sub = parser.add_subparsers(dest="cmd")
    p_profile = sub.add_parser("profile")
    p_profile.add_argument("--path", required=True)
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--recipe", required=True)
    args = parser.parse_args()
    c = Cleaner()
    if args.cmd == "profile":
        print(json.dumps(c.profile(args.path), indent=2))
    elif args.cmd == "apply":
        with open(args.recipe) as f:
            r = Recipe.parse_raw(f.read())
        res = c.apply_recipe_from_spec(r)
        print(res)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
