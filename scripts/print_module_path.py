import sys
import os
# ensure project root is importable when running the script from anywhere
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import fbroom.workflow_rules as w
print(w.__file__)
