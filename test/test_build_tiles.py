#!/usr/bin/env python3
"""Tests for build_tiles.py. Run: python3 test/test_build_tiles.py"""
import gzip, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_tiles as bt  # noqa: E402


def feat(fid, lon, lat, **tags):
    return json.dumps({
        "type": "Feature", "id": fid,
        "properties": dict({"amenity": "toilets"}, **tags),
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    })


class Centroid(unittest.TestCase):
    def test_point(self):
        self.assertEqual(bt.centroid({"type": "Point", "coordinates": [12.5, 55.6]}), (12.5, 55.6))

    def test_point_with_elevation(self):
        self.assertEqual(bt.centroid({"type": "Point", "coordinates": [12.5, 55.6, 30]}), (12.5, 55.6))

    def test_closed_way_ignores_duplicate_last_vertex(self):
        ring = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
        self.assertEqual(bt.centroid({"type": "Polygon", "coordinates": [ring]}), (1.0, 1.0))

    def test_linestring(self):
        g = {"type": "LineString", "coordinates": [[0, 0], [4, 8]]}
        self.assertEqual(bt.centroid(g), (2.0, 4.0))

    def test_multipolygon(self):
        g = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]]}
        self.assertEqual(bt.centroid(g), (1.0, 1.0))

    def test_empty_and_junk(self):
        for g in ({"type": "Polygon", "coordinates": []}, {}, None, {"type": "Point", "coordinates": [1]}):
            self.assertIsNone(bt.centroid(g))


class Longitude(unittest.TestCase):
    def test_wraps(self):
        self.assertAlmostEqual(bt.normalise_lon(181), -179)
        self.assertAlmostEqual(bt.normalise_lon(-181), 179)
        self.assertAlmostEqual(bt.normalise_lon(360), 0)
        self.assertAlmostEqual(bt.normalise_lon(-180), -180)
    def test_leaves_normal_values_alone(self):
        for v in (-179.9, -1, 0, 12.5683, 179.9):
            self.assertAlmostEqual(bt.normalise_lon(v), v)


class Cells(unittest.TestCase):
    def test_copenhagen(self):
        self.assertEqual(bt.cell_of(55.6761, 12.5683), (55, 12))
    def test_negative_rounds_down_not_toward_zero(self):
        self.assertEqual(bt.cell_of(-0.5, -0.5), (-1, -1))
        self.assertEqual(bt.cell_of(-33.9249, 18.4241), (-34, 18))
    def test_exact_boundary_belongs_to_higher_cell(self):
        self.assertEqual(bt.cell_of(55.0, 12.0), (55, 12))
    def test_poles_do_not_produce_a_cell_that_spans_nothing(self):
        self.assertEqual(bt.cell_of(90.0, 0.0), (89, 0))
        self.assertEqual(bt.cell_of(-90.0, 0.0), (-90, 0))
    def test_antimeridian(self):
        self.assertEqual(bt.cell_of(-16.9, 179.99), (-17, 179))
        self.assertEqual(bt.cell_of(-16.9, -179.99), (-17, -180))
    def test_out_of_range_latitude_is_clamped(self):
        self.assertEqual(bt.cell_of(120.0, 0.0)[0], 89)


class Tags(unittest.TestCase):
    def test_amenity_is_dropped_but_useful_tags_kept(self):
        r = bt.parse_feature(feat("n1", 12.5, 55.6, fee="yes", charge="5 DKK", wheelchair="yes"))
        self.assertNotIn("amenity", r["tags"])
        self.assertEqual(r["tags"], {"fee": "yes", "charge": "5 DKK", "wheelchair": "yes"})

    def test_noise_prefixes_dropped(self):
        r = bt.parse_feature(feat("n1", 12.5, 55.6, **{
            "seamark:type": "buoy", "source:date": "2009", "tiger:cfcc": "A41",
            "created_by": "JOSM", "note": "check this", "opening_hours": "24/7"}))
        self.assertEqual(r["tags"], {"opening_hours": "24/7"})

    def test_blank_and_null_values_dropped(self):
        line = json.dumps({"type": "Feature", "id": "n1",
                           "properties": {"amenity": "toilets", "fee": "  ", "charge": None, "wheelchair": "yes"},
                           "geometry": {"type": "Point", "coordinates": [12.5, 55.6]}})
        self.assertEqual(bt.parse_feature(line)["tags"], {"wheelchair": "yes"})

    def test_non_string_values_survive_as_text(self):
        line = json.dumps({"type": "Feature", "id": "n1", "properties": {"amenity": "toilets", "level": 2},
                           "geometry": {"type": "Point", "coordinates": [12.5, 55.6]}})
        self.assertEqual(bt.parse_feature(line)["tags"]["level"], "2")

    def test_unicode_preserved(self):
        r = bt.parse_feature(feat("n1", 12.5, 55.6, name="Toilet ved Nørreport"))
        self.assertEqual(r["tags"]["name"], "Toilet ved Nørreport")


class ReferencedNodes(unittest.TestCase):
    """osmium keeps the untagged nodes of a toilet building so ways have
    geometry. Those must not end up in the dataset as phantom toilets."""

    def test_untagged_referenced_node_is_dropped(self):
        line = json.dumps({"type": "Feature", "id": "n99", "properties": {},
                           "geometry": {"type": "Point", "coordinates": [12.5, 55.6]}})
        self.assertIsNone(bt.parse_feature(line))

    def test_unrelated_amenity_is_dropped(self):
        line = json.dumps({"type": "Feature", "id": "n99",
                           "properties": {"amenity": "cafe", "name": "Not a toilet"},
                           "geometry": {"type": "Point", "coordinates": [12.5, 55.6]}})
        self.assertIsNone(bt.parse_feature(line))

    def test_toilet_way_is_kept(self):
        ring = [[12.0, 55.0], [12.001, 55.0], [12.001, 55.001], [12.0, 55.001], [12.0, 55.0]]
        line = json.dumps({"type": "Feature", "id": "w7",
                           "properties": {"amenity": "toilets", "wheelchair": "yes"},
                           "geometry": {"type": "Polygon", "coordinates": [ring]}})
        r = bt.parse_feature(line)
        self.assertEqual(r["id"], "w7")
        self.assertAlmostEqual(r["lat"], 55.0005, places=5)
        self.assertEqual(r["tags"], {"wheelchair": "yes"})


class Ids(unittest.TestCase):
    def test_feature_level_id(self):
        self.assertEqual(bt.parse_feature(feat("w42", 12.5, 55.6))["id"], "w42")

    def test_falls_back_to_attributes(self):
        line = json.dumps({"type": "Feature", "properties": {"amenity": "toilets", "@type": "way", "@id": 42},
                           "geometry": {"type": "Point", "coordinates": [12.5, 55.6]}})
        self.assertEqual(bt.parse_feature(line)["id"], "w42")

    def test_no_id_is_rejected(self):
        line = json.dumps({"type": "Feature", "properties": {"amenity": "toilets"},
                           "geometry": {"type": "Point", "coordinates": [12.5, 55.6]}})
        self.assertIsNone(bt.parse_feature(line))


class Robustness(unittest.TestCase):
    def test_blank_junk_and_truncated_lines_are_skipped_not_fatal(self):
        for line in ("", "   ", "[", "]", ",", "{oh no", '{"type":"Feature"'):
            self.assertIsNone(bt.parse_feature(line))

    def test_rfc8142_record_separator_is_stripped(self):
        self.assertIsNotNone(bt.parse_feature("\x1e" + feat("n1", 12.5, 55.6)))

    def test_geometry_missing_is_skipped(self):
        self.assertIsNone(bt.parse_feature(json.dumps({"type": "Feature", "id": "n1", "properties": {}})))

    def test_nan_coordinates_rejected(self):
        self.assertIsNone(bt.parse_feature('{"type":"Feature","id":"n1","properties":{},'
                                           '"geometry":{"type":"Point","coordinates":[NaN,55.6]}}'))

    def test_impossible_latitude_rejected(self):
        self.assertIsNone(bt.parse_feature(feat("n1", 12.5, 99.0)))


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_pipeline(self, lines):
        src = os.path.join(self.dir, "in.geojsonseq")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        out = os.path.join(self.dir, "out")
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "build_tiles.py"),
             src, "--out", out, "--generated", "2026-09-01T03:00:00Z"],
            capture_output=True, text=True)
        return proc, out

    def read_gz(self, path):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def test_full_run(self):
        lines = [
            feat("n1", 12.5683, 55.6761, fee="no"),               # Copenhagen
            feat("n2", 12.5900, 55.6800, fee="yes", charge="2 DKK"),
            feat("n3", 2.3522, 48.8566, fee="yes"),               # Paris
            feat("n1", 12.5683, 55.6761, fee="no"),               # duplicate id
            "",                                                    # blank line
            "{broken",                                             # junk
            feat("n4", -0.1276, 51.5074),                          # London, cell 51,-1
        ]
        proc, out = self.run_pipeline(lines)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        man = self.read_gz(os.path.join(out, "v1", "manifest.json"))
        self.assertEqual(man["version"], 1)
        self.assertEqual(man["generated"], "2026-09-01T03:00:00Z")
        self.assertEqual(man["cell_size_deg"], 1)
        self.assertEqual(man["count"], 4, "duplicate must be counted once")
        self.assertEqual(man["cells"], {"48,2": 1, "51,-1": 1, "55,12": 2})

        cell = self.read_gz(os.path.join(out, "v1", "cells", "55", "12.json"))
        self.assertEqual(cell["cell"], "55,12")
        self.assertEqual(cell["generated"], "2026-09-01T03:00:00Z")
        self.assertEqual([t["id"] for t in cell["toilets"]], ["n1", "n2"])
        self.assertAlmostEqual(cell["toilets"][0]["lat"], 55.6761)
        self.assertAlmostEqual(cell["toilets"][0]["lon"], 12.5683)

        # Negative longitude must land in a readable path, not a crash.
        self.assertTrue(os.path.exists(os.path.join(out, "v1", "cells", "51", "-1.json")))

    def test_files_are_gzip_and_named_json(self):
        proc, out = self.run_pipeline([feat("n1", 12.5683, 55.6761)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = os.path.join(out, "v1", "cells", "55", "12.json")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(2), b"\x1f\x8b", "must be gzip bytes despite .json name")

    def test_output_is_byte_stable_so_sync_skips_unchanged_cells(self):
        lines = [feat("n1", 12.5683, 55.6761), feat("n2", 12.59, 55.68)]
        p1, o1 = self.run_pipeline(lines)
        p2, o2 = self.run_pipeline(list(reversed(lines)))
        self.assertEqual(p1.returncode, 0); self.assertEqual(p2.returncode, 0)
        a = open(os.path.join(o1, "v1", "cells", "55", "12.json"), "rb").read()
        b = open(os.path.join(o2, "v1", "cells", "55", "12.json"), "rb").read()
        self.assertEqual(a, b, "same data in a different order must produce identical bytes")

    def test_empty_input_fails_loudly_rather_than_publishing_nothing(self):
        proc, out = self.run_pipeline(["", "{junk"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refusing to publish an empty dataset", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
