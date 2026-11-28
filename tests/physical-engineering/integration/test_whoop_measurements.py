"""Failures that could otherwise make an incomplete demo look ready to show."""
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import build123d as bd

SCRIPTS = Path(__file__).resolve().parents[3] / "plugins/physical-engineering/scripts"
sys.path.insert(0, str(SCRIPTS))
import verify_whoop as verifier


class WhoopExportChecks(unittest.TestCase):
    def test_catalog_alone_cannot_verify_motor_screw_quantity(self):
        parts = {(label,) for label in verifier.COMPONENTS.values()}
        for corner, hand in verifier.CORNERS.items():
            group = f"propulsion_{corner}"
            parts.update({(group, f"motor_{corner}"), (group, f"prop_{corner}_{hand}")})
            for i in (1, 2, 3):
                parts.add((group, f"motor_screws_{corner}", f"M1_4x3_{i}"))
                parts.add(("wiring", f"{corner}_phase_{i}"))
        for i in (1, 2, 3, 4):
            parts.update({("stack_hardware", f"FC_grommet_{i}"), ("stack_hardware", f"ST1_4x6_{i}")})
        for i in (1, 2):
            parts.update({("battery_retention", f"foam_pad_{i}"), ("battery_retention", f"silicone_loop_{i}")})
        for label in ("BT2_mating_plug", "battery_lead_plus", "battery_lead_minus", "ELRS_receiver_antenna",
                      "VTX_coax", "VTX_whip", "camera_harness_1", "camera_harness_2", "camera_harness_3"):
            parts.add(("wiring", label))
        catalog = SCRIPTS.parent / "fixtures/whoop-65/catalog/parts.csv"
        results = []
        record = lambda *fields: results.append(fields)
        verifier.check_bom(catalog, parts, record)
        self.assertTrue(results[-1][1], results[-1][2])
        with tempfile.TemporaryDirectory() as tmp:
            with catalog.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            next(r for r in rows if r["ID"] == "MS01")["Qty"] = "16"
            altered = Path(tmp) / "bom.csv"
            with altered.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            verifier.check_bom(altered, parts, record)
        self.assertFalse(results[-1][1])
        self.assertEqual(results[-1][2]["modeled_aircraft"]["MS01"], {"step": 12, "bom": 16, "unit": "ea"})

    def test_mounting_holes_must_match_distinct_axes(self):
        with self.assertRaisesRegex(ValueError, "same feature"):
            verifier.axis_matches([(0, 0), (.01, 0)], [(0, 0), (20, 0)])

    def test_round_boss_cannot_substitute_for_board_bore(self):
        board = bd.Box(8, 8, 1) - bd.Cylinder(1.5, 3)
        solid_boss = bd.Cylinder(1.5, 1)
        self.assertEqual(len(verifier.cylinder_features(board, 1.5, bore=True)), 1)
        self.assertEqual(verifier.cylinder_features(solid_boss, 1.5, bore=True), [])

    def test_different_closed_mesh_cannot_substitute_for_fit_coupon(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coupon-catch.stl"
            # A genuine manifold STL sitting on the bed still has the wrong dimensions.
            bd.export_stl(bd.Pos(0, 0, 1) * bd.Box(10, 10, 2), path)
            results = []
            verifier.check_print(path, None, lambda *fields: results.append(fields))
            self.assertFalse(results[0][1])
            self.assertEqual(results[0][2]["bad_edges"], 0)

    def test_shifted_hole_with_equal_volume_and_bounds_fails_surface_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.stl"
            block = bd.Pos(0, 0, 1) * bd.Box(12, 12, 2)
            expected = block - bd.Pos(-2, 0, 1) * bd.Cylinder(1, 3)
            shifted = block - bd.Pos(2, 0, 1) * bd.Cylinder(1, 3)
            bd.export_stl(shifted, path)
            results = []
            verifier.check_print(path, expected, lambda *fields: results.append(fields))
            self.assertFalse(results[0][1])
            actual = results[0][2]
            self.assertLess(actual["relative_volume_error"], .02)
            self.assertLess(actual["max_span_error_mm"], .1)
            self.assertIsNotNone(actual["surface"]["unmatched_step_point_mm"])

    def test_glb_reads_vertex_buffer_and_rejects_nonfinite_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "view.glb"
            block = bd.Box(7, 9, 4)
            block.label = "part"
            block.color = "#416788"
            bd.export_gltf(bd.Compound(children=[block], label="assembly"), path, binary=True)
            actual = verifier.glb_bounds(path)[("part",)]
            self.assertEqual((actual[1] - actual[0]).round(4).tolist(), [7, 9, 4])
            data = bytearray(path.read_bytes())
            length, = struct.unpack_from("<I", data, 12)
            doc = json.loads(data[20:20+length])
            prim = doc["meshes"][0]["primitives"][0]
            pos = doc["accessors"][prim["attributes"]["POSITION"]]
            view = doc["bufferViews"][pos["bufferView"]]
            offset = 28 + length + view.get("byteOffset", 0) + pos.get("byteOffset", 0)
            # Keep the document and accessor min/max metadata intact.
            struct.pack_into("<f", data, offset, float("nan"))
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                verifier.glb_bounds(path)


if __name__ == "__main__":
    unittest.main()
