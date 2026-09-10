#!/usr/bin/env python3
"""
Turn osmium's GeoJSON-sequence output into the static tile set the app fetches.

Input  : one GeoJSON Feature per line (osmium export -f geojsonseq), on stdin
         or in files named on the command line.
Output : out/v1/manifest.json          gzipped, lists every populated cell
         out/v1/cells/<lat>/<lon>.json gzipped, one 1-degree cell of toilets

Files on disk hold gzipped BYTES but are named .json, because they are uploaded
with Content-Encoding: gzip. The phone asks for .json and its HTTP client
un-gzips them transparently. Do not rename them to .json.gz.

No third-party packages. Python 3.9+.
"""

import argparse
import gzip
import json
import math
import os
import sys
from datetime import datetime, timezone

CELL_DEG = 1  # one degree of latitude and longitude per cell

# Tags that carry no meaning for a person looking for a toilet. Dropped to keep
# cells small and to stop the detail sheet showing database bookkeeping.
DROP_EXACT = {
    "amenity",          # always "toilets" - implied by the dataset
    "created_by", "source", "source:date", "source_ref", "attribution",
    "note", "fixme", "FIXME", "comment",
}
DROP_PREFIX = (
    "seamark:", "source:", "note:", "ref:", "gnis:", "tiger:", "nhd:",
    "massgis:", "kct_", "osak:", "lacounty:", "it:", "kms:",
)


def wanted_tag(key):
    if key in DROP_EXACT:
        return False
    return not key.startswith(DROP_PREFIX)


def flatten(coords, out):
    """Collect [lon, lat] pairs from any GeoJSON coordinate nesting."""
    if not coords:
        return
    first = coords[0]
    if isinstance(first, (int, float)):
        if len(coords) >= 2:
            out.append((coords[0], coords[1]))
        return
    for part in coords:
        flatten(part, out)


def centroid(geometry):
    """Representative point for any geometry. Ways become their vertex mean.

    Returns (lon, lat) or None if the geometry has no usable coordinates.
    """
    if not isinstance(geometry, dict):
        return None
    if geometry.get("type") == "Point":
        c = geometry.get("coordinates")
        if isinstance(c, list) and len(c) >= 2:
            return (c[0], c[1])
        return None
    pts = []
    flatten(geometry.get("coordinates"), pts)
    if not pts:
        return None
    # Drop the duplicated closing vertex of a ring so it is not double weighted.
    if len(pts) > 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return (
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts),
    )


def normalise_lon(lon):
    """Wrap any longitude into [-180, 180)."""
    lon = (lon + 180.0) % 360.0
    if lon < 0:
        lon += 360.0
    return lon - 180.0


def cell_of(lat, lon):
    """Which 1-degree cell a point belongs to, as (lat_index, lon_index)."""
    lat = max(-90.0, min(90.0, lat))
    lon = normalise_lon(lon)
    y = math.floor(lat / CELL_DEG) * CELL_DEG
    x = math.floor(lon / CELL_DEG) * CELL_DEG
    # A point exactly at the north pole would land in a cell that spans nothing.
    if y >= 90:
        y = 90 - CELL_DEG
    return (int(y), int(x))


def feature_id(feature):
    """osmium writes the id at feature level with -u type_id, and as @type/@id
    in properties with -a type,id. Accept either, so a change in the export
    flags does not silently produce records with no id."""
    fid = feature.get("id")
    if isinstance(fid, str) and fid:
        return fid
    props = feature.get("properties") or {}
    otype = props.get("@type") or props.get("type")
    oid = props.get("@id") or props.get("id")
    if otype and oid is not None:
        return "%s%s" % (str(otype)[0], oid)
    return None


def parse_feature(line):
    """One geojsonseq line -> normalised record, or None if unusable."""
    line = line.strip()
    # RFC 8142 allows a leading record separator byte.
    if line.startswith("\x1e"):
        line = line[1:]
    if not line or line in ("[", "]", ","):
        return None
    try:
        feature = json.loads(line)
    except ValueError:
        return None
    if not isinstance(feature, dict):
        return None

    point = centroid(feature.get("geometry"))
    if point is None:
        return None
    lon, lat = point
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    if not (-90.0 <= lat <= 90.0):
        return None

    fid = feature_id(feature)
    if not fid:
        return None

    props = feature.get("properties") or {}

    # osmium tags-filter also emits the untagged nodes that make up a toilet
    # building, so that ways keep their geometry. Those come through export as
    # bare points. Insisting on the tag we filtered for is what keeps them out.
    if props.get("amenity") != "toilets":
        return None

    tags = {}
    for key, value in props.items():
        if key.startswith("@"):
            continue
        if not wanted_tag(key):
            continue
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        text = text.strip()
        if text:
            tags[key] = text

    return {
        "id": fid,
        "lat": round(lat, 6),
        "lon": round(normalise_lon(lon), 6),
        "tags": tags,
    }


def write_gz(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # mtime=0 so an unchanged cell produces byte-identical output and `aws s3
    # sync` skips re-uploading it.
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(raw)
    return len(raw)


def main():
    ap = argparse.ArgumentParser(description="Build LooLander toilet tiles.")
    ap.add_argument("inputs", nargs="*", help="geojsonseq files (default: stdin)")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--generated", default=None, help="ISO timestamp override (for tests)")
    args = ap.parse_args()

    cells = {}
    seen = set()
    read = kept = 0

    def consume(stream):
        nonlocal read, kept
        for line in stream:
            read += 1
            record = parse_feature(line)
            if record is None:
                continue
            if record["id"] in seen:
                continue  # continent files overlap at borders
            seen.add(record["id"])
            kept += 1
            cells.setdefault(cell_of(record["lat"], record["lon"]), []).append(record)

    if args.inputs:
        for name in args.inputs:
            with open(name, "r", encoding="utf-8", errors="replace") as handle:
                consume(handle)
    else:
        consume(sys.stdin)

    generated = args.generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = os.path.join(args.out, "v1")
    index = {}
    largest = ("", 0)

    for (y, x), records in sorted(cells.items()):
        records.sort(key=lambda r: r["id"])
        key = "%d,%d" % (y, x)
        size = write_gz(
            os.path.join(root, "cells", str(y), "%d.json" % x),
            {"cell": key, "generated": generated, "toilets": records},
        )
        index[key] = len(records)
        if size > largest[1]:
            largest = (key, size)

    write_gz(
        os.path.join(root, "manifest.json"),
        {
            "version": 1,
            "generated": generated,
            "cell_size_deg": CELL_DEG,
            "count": kept,
            "cells": index,
        },
    )

    sys.stderr.write(
        "read %d lines, kept %d toilets, %d cells, largest cell %s at %.0f kB raw\n"
        % (read, kept, len(index), largest[0], largest[1] / 1024.0)
    )
    if kept == 0:
        sys.stderr.write("ERROR: no toilets found - refusing to publish an empty dataset\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
