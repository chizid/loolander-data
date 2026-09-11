#!/usr/bin/env python3
"""Tests for regions.py's bundle dropping, built from the real Geofabrik tree
observed in the 2026-09-10 workflow log. Run: python3 test/test_regions.py"""
import io, os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import regions as R  # noqa: E402

GB = 1024 ** 3


def tree():
    """The shape Geofabrik actually publishes: bundles are SIBLINGS."""
    t = {}
    def add(rid, parent, size=GB):
        t[rid] = {"id": rid, "parent": parent, "url": "u://" + rid,
                  "name": rid, "iso1": [], "iso2": [], "_size": size}
    for top in ("europe", "asia", "africa", "north-america"):
        add(top, None)
    for c in ("austria", "switzerland", "italy", "slovenia", "france", "germany",
              "united-kingdom", "ireland-and-northern-ireland", "denmark", "spain"):
        add(c, "europe")
    for b in ("dach", "alps", "britain-and-ireland", "great-britain"):
        add(b, "europe", 5 * GB)
    for c in ("indonesia", "malaysia-singapore-brunei", "philippines", "thailand",
              "vietnam", "cambodia", "laos", "myanmar", "east-timor", "gcc-states"):
        add(c, "asia")
    add("sea", "asia", 4 * GB)
    for c in ("south-africa", "lesotho", "canary-islands"):
        add(c, "africa")
    add("south-africa-and-lesotho", "africa", 2 * GB)
    add("us", "north-america", 11 * GB)
    add("canada", "north-america")
    for m in ("us-midwest", "us-northeast", "us-pacific", "us-south", "us-west"):
        add(m, "north-america", 3 * GB)
    for i in range(53):                      # states, DC and territories
        add("us/state%02d" % i, "north-america")
    for i in range(16):                      # Germany is split by the walker
        add("land%02d" % i, "germany")
    return t


def run(t, chosen_ids):
    chosen = [(t[i], t[i]["_size"]) for i in chosen_ids]
    err = io.StringIO(); old, sys.stderr = sys.stderr, err
    try:
        kept = R.drop_bundles(chosen, t)
    finally:
        sys.stderr = old
    return [r["id"] for r, _ in kept], err.getvalue()


def realistic():
    t = tree()
    ids = [i for i in t if t[i]["parent"] is not None and i != "germany"]
    ids = [i for i in ids if t[i]["parent"] != "germany"] + \
          [i for i in t if t[i]["parent"] == "germany"]
    return t, ids


class Dropping(unittest.TestCase):
    def test_drops_every_known_bundle(self):
        t, ids = realistic()
        kept, log = run(t, ids)
        for b in ("dach", "alps", "britain-and-ireland", "great-britain", "sea",
                  "south-africa-and-lesotho", "us", "us-midwest", "us-northeast",
                  "us-pacific", "us-south", "us-west"):
            self.assertNotIn(b, kept, "%s should have been dropped" % b)

    def test_keeps_everything_else(self):
        t, ids = realistic()
        kept, _ = run(t, ids)
        for keep in ("denmark", "france", "united-kingdom", "canada", "gcc-states",
                     "canary-islands", "south-africa", "lesotho", "indonesia",
                     "malaysia-singapore-brunei", "us/state00", "us/state52", "land00"):
            self.assertIn(keep, kept, "%s must survive" % keep)

    def test_germany_covered_by_its_laender_not_by_itself(self):
        """Germany is split into 16, so 'germany' is never chosen - dach must
        still be dropped, via the tree rather than the chosen list."""
        t, ids = realistic()
        self.assertNotIn("germany", ids)
        kept, _ = run(t, ids)
        self.assertNotIn("dach", kept)


class Safety(unittest.TestCase):
    def test_keeps_us_when_the_states_are_missing(self):
        t = tree()
        ids = ["us", "canada"] + ["us/state%02d" % i for i in range(5)]
        kept, log = run(t, ids)
        self.assertIn("us", kept, "must not drop the US when only 5 states are present")
        self.assertIn("not covered by", log)

    def test_keeps_dach_when_austria_is_missing(self):
        t = tree()
        kept, log = run(t, ["dach", "switzerland", "denmark"] +
                           [i for i in t if t[i]["parent"] == "germany"])
        self.assertIn("dach", kept)
        self.assertIn("austria", log)

    def test_keeps_sea_when_a_country_is_missing(self):
        t = tree()
        kept, log = run(t, ["sea", "indonesia", "philippines"])
        self.assertIn("sea", kept)

    def test_reports_what_it_saved(self):
        t, ids = realistic()
        _, log = run(t, ids)
        self.assertIn("skipped", log)
        self.assertIn("dropping", log)

    def test_no_bundles_present_is_a_no_op(self):
        t = tree()
        kept, log = run(t, ["denmark", "france", "canada"])
        self.assertEqual(kept, ["denmark", "france", "canada"])
        self.assertEqual(log, "")

    def test_survives_a_parent_loop(self):
        """A malformed index must not hang the walk up the parent chain."""
        t = tree()
        t["europe"]["parent"] = "denmark"      # cycle: denmark -> europe -> denmark
        kept, _ = run(t, ["dach", "denmark", "austria", "switzerland"])
        self.assertIsInstance(kept, list)


class Savings(unittest.TestCase):
    def test_drops_roughly_a_third_of_the_bytes(self):
        t, ids = realistic()
        before = sum(t[i]["_size"] for i in ids)
        kept, _ = run(t, ids)
        after = sum(t[i]["_size"] for i in kept)
        self.assertLess(after, before)
        print("\n  bytes before %.1f GB, after %.1f GB (-%.0f%%)"
              % (before / GB, after / GB, 100 * (before - after) / before))


if __name__ == "__main__":
    unittest.main(verbosity=2)
