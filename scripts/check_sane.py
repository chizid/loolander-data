#!/usr/bin/env python3
"""
Refuse to publish a dataset that looks broken.

A half-finished Geofabrik mirror, a changed osmium flag or a silently skipped
continent all produce a run that succeeds and quietly halves the data. The app
would keep working and just stop finding toilets in whole countries, which is
the worst kind of failure because nobody notices. So compare the new dataset
against what is currently live and stop if it lost too much.

Exit 0 to publish, 1 to abort.
"""

import argparse
import gzip
import io
import json
import sys
import urllib.error
import urllib.request

UA = "LooLander-data/1.0 (+https://loolander.com)"


def read_local(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def read_published(url):
    """The live manifest, or None if nothing is published yet."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sys.stderr.write("could not read the live manifest (%s)\n" % exc)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True, help="path to the new manifest.json")
    ap.add_argument("--live", default=None, help="URL of the published manifest.json")
    ap.add_argument("--min-count", type=int, default=100_000,
                    help="absolute floor for a worldwide run (default 100000)")
    ap.add_argument("--max-drop", type=float, default=0.20,
                    help="fail if the count fell by more than this fraction (default 0.20)")
    args = ap.parse_args()

    new = read_local(args.new)
    count = new.get("count", 0)
    cells = len(new.get("cells") or {})
    print("new dataset: %d toilets in %d cells, generated %s"
          % (count, cells, new.get("generated")))

    if count < args.min_count:
        print("FAIL: only %d toilets, expected at least %d. A region probably "
              "failed to download." % (count, args.min_count), file=sys.stderr)
        return 1

    if not args.live:
        print("no live dataset to compare against - publishing")
        return 0

    old = read_published(args.live)
    if old is None:
        print("nothing published yet - publishing")
        return 0

    before = old.get("count", 0)
    if before <= 0:
        print("live manifest has no count - publishing")
        return 0

    change = (count - before) / float(before)
    print("live dataset: %d toilets (generated %s)" % (before, old.get("generated")))
    print("change: %+.1f%%" % (change * 100))

    if change < -args.max_drop:
        print("FAIL: the new dataset lost %.1f%% of its toilets. Not publishing. "
              "Check the extract log for a region that failed."
              % (-change * 100), file=sys.stderr)
        return 1

    print("sane - publishing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
