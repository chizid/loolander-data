#!/usr/bin/env python3
"""Write a human summary of a run into the GitHub Actions job summary."""
import gzip, json, os, sys

path = sys.argv[1] if len(sys.argv) > 1 else "out/v1/manifest.json"
lines = ["### Toilet data refresh", ""]
try:
    m = json.load(gzip.open(path, "rt", encoding="utf-8"))
    cells = m.get("cells") or {}
    lines.append("- **%d toilets** in **%d cells**" % (m.get("count", 0), len(cells)))
    lines.append("- generated `%s`" % m.get("generated"))
    busiest = sorted(cells.items(), key=lambda kv: -kv[1])[:5]
    if busiest:
        lines.append("- busiest cells: " + ", ".join("`%s` (%d)" % kv for kv in busiest))
except (OSError, ValueError) as exc:
    lines.append("No dataset was produced (%s)." % exc)

out = os.environ.get("GITHUB_STEP_SUMMARY")
text = "\n".join(lines) + "\n"
if out:
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(text)
print(text)
