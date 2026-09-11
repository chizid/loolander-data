#!/usr/bin/env python3
"""
Work out which Geofabrik files to download for full world coverage.

Geofabrik publishes a tree: continents contain countries contain sub-regions.
Downloading a continent covers everything under it, but europe-latest.osm.pbf
is over 30 GB and will not fit on a GitHub runner. So this walks down from the
continents and descends into children until every chosen file is small enough
to handle, giving complete coverage with no overlap.

The region list is read live from Geofabrik's own index, so a new country or a
renamed region does not silently go missing from the app.

Writes one "<id>\t<url>\t<bytes>" line per region to stdout.
No third-party packages. Python 3.9+.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
UA = "LooLander-data/1.0 (monthly toilet extract; +https://loolander.com)"


def get(url, method="GET", timeout=60):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout)


def load_index():
    with get(INDEX_URL) as resp:
        data = json.load(resp)
    features = data.get("features")
    if not isinstance(features, list):
        raise SystemExit("index-v1.json has no 'features' list - Geofabrik changed its format")

    regions = {}
    for feature in features:
        props = (feature or {}).get("properties") or {}
        rid = props.get("id")
        urls = props.get("urls") or {}
        pbf = urls.get("pbf")
        if not rid or not pbf:
            continue
        regions[rid] = {"id": rid, "parent": props.get("parent"), "url": pbf,
                        "name": props.get("name") or rid,
                        "iso1": props.get("iso3166-1:alpha2") or [],
                        "iso2": props.get("iso3166-2") or []}

    if len(regions) < 50:
        raise SystemExit(
            "only %d usable regions in index-v1.json - refusing to run with a "
            "partial world. Check https://download.geofabrik.de/index-v1.json" % len(regions))
    return regions


# Geofabrik publishes convenience bundles - "dach" for Germany-Austria-
# Switzerland, "us" alongside all fifty states - as SIBLINGS of the regions
# they contain, not as parents. Walking down the tree therefore cannot see the
# overlap, and the first full run downloaded 116 GB to extract a world that is
# only about 80 GB. The toilets came out right, because records are
# deduplicated by OSM id later, but it wastes hours and takes bandwidth from a
# service that gives this data away for nothing.
#
# Each bundle lists what must already be in the plan before it is dropped.
# A plain id is satisfied by that region or anything beneath it in the tree.
# An entry like "us/*40" needs at least 40 chosen regions whose id starts with
# "us/". A bundle whose requirements are not met is KEPT - so if Geofabrik
# reorganises, the cost is wasted bandwidth, never a missing country.
BUNDLES = {
    "dach": ["germany", "austria", "switzerland"],
    "alps": ["france", "germany", "austria", "switzerland", "italy", "slovenia"],
    "britain-and-ireland": ["united-kingdom", "ireland-and-northern-ireland"],
    "great-britain": ["united-kingdom"],
    "sea": ["indonesia", "malaysia-singapore-brunei", "philippines", "thailand",
            "vietnam", "cambodia", "laos", "myanmar", "east-timor"],
    "south-africa-and-lesotho": ["south-africa", "lesotho"],
    "us": ["us/*40"],
    "us-midwest": ["us/*40"],
    "us-northeast": ["us/*40"],
    "us-pacific": ["us/*40"],
    "us-south": ["us/*40"],
    "us-west": ["us/*40"],
}


def drop_bundles(chosen, regions):
    """Remove overlapping convenience regions once their contents are covered."""
    chosen_ids = {r["id"] for r, _ in chosen}

    def beneath(ancestor_id):
        """Is anything we chose inside this region?"""
        for rid in chosen_ids:
            seen, cur = 0, regions.get(rid, {}).get("parent")
            while cur and seen < 12:
                if cur == ancestor_id:
                    return True
                cur = regions.get(cur, {}).get("parent")
                seen += 1
        return False

    def met(req):
        if "/*" in req:
            prefix, _, minimum = req.partition("*")
            return sum(1 for rid in chosen_ids if rid.startswith(prefix)) >= int(minimum)
        return req in chosen_ids or beneath(req)

    keep, saved = [], 0
    for region, size in chosen:
        needs = BUNDLES.get(region["id"])
        if not needs:
            keep.append((region, size))
            continue
        missing = [r for r in needs if not met(r)]
        if missing:
            sys.stderr.write("  keeping bundle %s - not covered by %s\n"
                             % (region["id"], ", ".join(missing)))
            keep.append((region, size))
        else:
            saved += size or 0
            sys.stderr.write("  dropping %-24s (%s) - already covered\n"
                             % (region["id"], human(size)))
    if saved:
        sys.stderr.write("skipped %s of overlapping downloads\n" % human(saved))
    return keep


def dump_tree(regions):
    """Print the region tree exactly as Geofabrik describes it.

    The plan came out with overlapping regions - the whole US alongside all
    fifty states, Germany alongside DACH - and the walker cannot be corrected
    without seeing how parent links and country codes are really arranged.
    Written to stderr so it lands in the workflow log.
    """
    children = {}
    for r in regions.values():
        children.setdefault(r["parent"], []).append(r["id"])

    def codes(r):
        bits = []
        if r["iso1"]:
            bits.append("iso1=" + ",".join(r["iso1"]))
        if r["iso2"]:
            bits.append("iso2=" + ",".join(r["iso2"][:4]) + ("..." if len(r["iso2"]) > 4 else ""))
        return " ".join(bits) or "-"

    sys.stderr.write("\n===== GEOFABRIK REGION TREE (%d regions) =====\n" % len(regions))
    sys.stderr.write("%-34s %-22s %5s  %s\n" % ("id", "parent", "kids", "codes"))
    for rid in sorted(regions):
        r = regions[rid]
        sys.stderr.write("%-34s %-22s %5d  %s\n"
                         % (rid, r["parent"] or "(top)", len(children.get(rid, [])), codes(r)))
    sys.stderr.write("===== END REGION TREE =====\n\n")


def size_of(url):
    """Content-Length of a download, or None if the server will not say."""
    try:
        with get(url, method="HEAD", timeout=30) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, ValueError, OSError) as exc:
        sys.stderr.write("  ! HEAD failed for %s (%s)\n" % (url, exc))
        return None


def plan(regions, max_bytes):
    children = {}
    for region in regions.values():
        children.setdefault(region["parent"], []).append(region)

    queue = list(children.get(None, []))
    if not queue:
        raise SystemExit("index has no top-level regions - Geofabrik changed its format")

    chosen, checked = [], 0
    while queue:
        region = queue.pop(0)
        checked += 1
        size = size_of(region["url"])
        kids = children.get(region["id"], [])
        # Unknown size is treated as too big when we have somewhere to descend
        # to, because guessing small is the failure that fills the disk.
        too_big = size is None or size > max_bytes
        if too_big and kids:
            sys.stderr.write("  splitting %-24s (%s) into %d sub-regions\n"
                             % (region["id"], human(size), len(kids)))
            queue.extend(kids)
            continue
        if too_big:
            sys.stderr.write("  ! %s is %s with no sub-regions - taking it anyway\n"
                             % (region["id"], human(size)))
        chosen.append((region, size))
    sys.stderr.write("checked %d regions, chose %d\n" % (checked, len(chosen)))
    return chosen


def human(n):
    if n is None:
        return "unknown size"
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser(description="Choose Geofabrik regions to download.")
    ap.add_argument("--max-gb", type=float, default=3.5,
                    help="split any region larger than this (default 3.5)")
    ap.add_argument("--only", default=None,
                    help="comma-separated region ids for a test run, e.g. denmark")
    args = ap.parse_args()

    regions = load_index()

    if args.only:
        wanted = [r.strip() for r in args.only.split(",") if r.strip()]
        missing = [r for r in wanted if r not in regions]
        if missing:
            raise SystemExit("unknown region id(s): %s" % ", ".join(missing))
        chosen = [(regions[r], size_of(regions[r]["url"])) for r in wanted]
        sys.stderr.write("TEST RUN - %d region(s) only, not the whole world\n" % len(chosen))
    else:
        dump_tree(regions)
        chosen = plan(regions, int(args.max_gb * 1024 ** 3))
        chosen = drop_bundles(chosen, regions)

    total = sum(s for _, s in chosen if s)
    sys.stderr.write("total download: %s across %d files\n" % (human(total), len(chosen)))
    for region, size in chosen:
        print("%s\t%s\t%s" % (region["id"], region["url"], size if size else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
