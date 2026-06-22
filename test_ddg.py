from duckduckgo_search import DDGS
import sys

try:
    with DDGS() as d:
        r = list(d.text("russia news", max_results=3))
    print(f"text: {len(r)} results")
    if r:
        print(r[0].get("title", ""))
    with DDGS() as d:
        r2 = list(d.news("russia news", max_results=3))
    print(f"news: {len(r2)} results")
    if r2:
        print(r2[0].get("title", ""))
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
