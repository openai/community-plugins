"""Verify a non-drone recipe, including incorrect placements, BOM counts and machining features."""
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import build123d as bd

PLUGIN = Path(__file__).resolve().parents[3] / "plugins/physical-engineering"
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))
import verify_assembly


class AssemblyInspection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="assembly-inspection-")
        cls.root = Path(cls.temp.name)
        cls.output = cls.root / "delivery"
        cls.recipe = PLUGIN / "fixtures/sensor-enclosure"
        subprocess.run([sys.executable, "-B", str(cls.recipe / "geometry/build.py"), str(cls.output)], check=True)
        shutil.copyfile(cls.recipe / "catalog/parts.csv", cls.output / "bom.csv")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_declared_installation_and_measured_clearance(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = json.loads((self.recipe / "assembly.json").read_text())
            config["inspection"]["method"] = "declared"
            (folder / "assembly.json").write_text(json.dumps(config))
            result = verify_assembly.verify(self.output, folder)
        self.assertEqual(result["status"], "pass", result)
        gap = next(check for check in result["checks"] if check["id"] == "gap-board-to-lid")
        self.assertAlmostEqual(gap["actual"]["measured_mm"], 6.2)

    def test_wrong_bom_and_placement_are_separate_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            delivery = folder / "delivery"
            shutil.copytree(self.output, delivery)
            config = json.loads((self.recipe / "assembly.json").read_text())
            config["inspection"]["parts"]["PC01"][0]["center_mm"][0] += 1
            (folder / "assembly.json").write_text(json.dumps(config))
            with (delivery / "bom.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            next(row for row in rows if row["ID"] == "LS01")["Qty"] = "3"
            with (delivery / "bom.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            report = verify_assembly.verify(delivery, folder)
        self.assertEqual(report["status"], "fail", report)
        failures = [c["id"] for c in report["checks"] if c["status"] == "fail"]
        self.assertEqual(set(failures), {"installed-bom", "placement-sensor_board"})

    def test_equal_volume_and_bounds_do_not_hide_misplaced_component_bore(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cut-plate.step"
            slab = bd.Box(12, 12, 2)
            installed = slab - bd.Pos(-2.5, 0, 0) * bd.Cylinder(1.5, 4)
            wrong = slab - bd.Pos(2.5, 0, 0) * bd.Cylinder(1.5, 4)
            bd.export_step(wrong, path)
            results = []
            verify_assembly.check_component(path, installed, lambda *row: results.append(row))
            self.assertFalse(results[0][1])
            self.assertAlmostEqual(results[0][2]["bounds_error_mm"], 0)
            self.assertAlmostEqual(results[0][2]["volume_difference_mm3"], 0)
            self.assertGreater(results[0][2]["symmetric_difference_mm3"], 25)


if __name__ == "__main__":
    unittest.main()
