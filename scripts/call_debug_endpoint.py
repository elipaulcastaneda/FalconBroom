import json
import urllib.request

url = 'http://127.0.0.1:3009/debug/recipe_from_text'
payload = {
    "instruction": "Make sure the first letter in any given entry of username are uppercase and the rest of the entry lowercase",
    "source_path": "data/uploads/combined_whitespace_case_33ef9b3b_9712_1db0.csv",
    "output_path": "output_from_text.csv",
}
req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode('utf-8'))
