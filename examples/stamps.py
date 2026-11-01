"""Check that every delivered item has a completion stamp. Usage: python stamps.py FILE.json [--buggy]

FILE.json is a list of items like {"id": 1, "stamp": "2026-11-01T10:00:00Z"}. Exit 0 if every item is stamped,
1 otherwise. With --buggy, the check reads a field that does not exist ("stamped_at") and treats a missing
field as fine, the kind of bug that makes a gate pass forever.
"""
import json
import sys

items = json.load(open(sys.argv[1], encoding="utf-8"))
field, lenient = ("stamped_at", True) if "--buggy" in sys.argv else ("stamp", False)
missing = [it["id"] for it in items if not it.get(field, "assumed" if lenient else None)]
if missing:
    print(f"{len(missing)} item(s) without a stamp: {missing}")
    sys.exit(1)
print(f"all {len(items)} items stamped")
