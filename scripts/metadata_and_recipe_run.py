import requests
import json
import traceback

paths = [
    "samples/test_data/generated_house_data_01.csv",
    "samples/test_data/generated_house_data_02.csv",
]
base = "http://127.0.0.1:3009"


def find_suggestion(obj):
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for k in ("text", "suggestion", "description", "plain_text", "instruction"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
        for v in obj.values():
            res = find_suggestion(v)
            if res:
                return res
    if isinstance(obj, list):
        for item in obj:
            res = find_suggestion(item)
            if res:
                return res
    return None


def try_post(url, payload, timeout=15):
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return r.text
    except Exception as e:
        print(f"Request to {url} failed: {e}")
        traceback.print_exc()
        return None


if __name__ == '__main__':
    for path in paths:
        print("\n" + "=" * 80)
        print("PATH:", path)

        meta = try_post(f"{base}/metadata", {"path": path}, timeout=15)

        # try to extract raw suggestions
        raw_suggestions = None
        suggestion_text = None
        if isinstance(meta, dict):
            if 'suggestions' in meta:
                raw_suggestions = meta['suggestions']
            else:
                # look for common keys
                for key in ('hints', 'tips', 'recommendations', 'metadata', 'suggestion'):
                    if key in meta:
                        raw_suggestions = meta[key]
                        break
            # attempt to derive a first plain-language suggestion
            if raw_suggestions:
                suggestion_text = find_suggestion(raw_suggestions)
            if not suggestion_text:
                suggestion_text = find_suggestion(meta)
        else:
            # meta was not dict (maybe text); include it
            raw_suggestions = meta
            suggestion_text = None

        print("Raw metadata suggestions:")
        try:
            print(json.dumps(raw_suggestions if raw_suggestions is not None else meta, indent=2, ensure_ascii=False))
        except Exception:
            print(repr(raw_suggestions))

        if suggestion_text:
            print("Extracted suggestion text:", suggestion_text)
            recipe_resp = try_post(f"{base}/debug/recipe_from_text",
                                   {"source_path": path, "instruction": suggestion_text},
                                   timeout=30)
            print("Recipe+preview response:")
            try:
                print(json.dumps(recipe_resp, indent=2, ensure_ascii=False))
            except Exception:
                print(repr(recipe_resp))
        else:
            print("No suggestion text found; skipping /debug/recipe_from_text POST.")

    print("\nCompleted script.")
