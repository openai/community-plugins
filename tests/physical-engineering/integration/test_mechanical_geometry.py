"""Independent measurements for user-defined parts, not the drone demo."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "plugins/physical-engineering"
sys.path.insert(0, str(PACKAGE / "scripts"))
import verify_mechanical as verifier
import build123d as bd


class MechanicalGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = PACKAGE / "fixtures/sensor-plate"
        cls.requirements = json.loads((fixture / "requirements.json").read_text())
        cls.changed = json.loads((fixture / "transfer-requirements.json").read_text())
        spec = importlib.util.spec_from_file_location("sensor_plate_model", fixture / "model.py")
        cls.model = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.model)
        cls.model.design = lambda: cls.requirements
        cls.plate = cls.model.component.function()
        cls.temp = tempfile.TemporaryDirectory(prefix="mechanical-part-geometry-")
        cls.output = Path(cls.temp.name)
        bd.export_step(cls.plate, cls.output / "assembly.step")
        bd.export_step(cls.plate, cls.output / "sensor_plate.step")
        bd.export_stl(cls.plate, cls.output / "sensor_plate.stl", tolerance=.01)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def check(report, ident):
        return next(item for item in report["checks"] if item["id"] == ident)

    def test_real_step_and_mesh_pass(self):
        report = verifier.verify(self.output / "assembly.step", self.requirements)
        self.assertEqual(report["status"], "pass", report)
        self.assertEqual({item["id"] for item in report["checks"]}, verifier.expected_check_ids(self.requirements))
        self.assertAlmostEqual(report["measurements"]["parts"]["sensor_plate"]["dimensions_mm"][0], 80)
        mesh = verifier.verify_prints(self.output / "assembly.step", self.output, self.requirements)
        self.assertEqual(mesh["status"], "pass", mesh)
        self.assertEqual({item["id"] for item in mesh["checks"]},
                         {f"sensor_plate_{suffix}" for suffix in verifier.PRINT_SUFFIXES})

    def test_changed_pitch_fails_old_part_and_passes_revised_part(self):
        old = verifier.verify(self.output / "assembly.step", self.changed)
        self.assertEqual(old["status"], "fail", old)
        self.assertEqual(self.check(old, "mounts")["status"], "fail")
        self.model.design = lambda: self.changed
        revised = self.model.component.function()
        self.assertEqual(verifier.verify_shape(revised, self.changed)["status"], "pass")

    def test_custom_non_plate_geometry_and_name_are_supported(self):
        # This ring is independently authored, not produced by either starter.
        req = {"schema_version": 1, "profile_id": "mechanical-part-v1", "units": "mm",
               "part_name": "instrument_spacer", "envelope_max_mm": [40, 40, 20],
               "density_g_per_cm3": 2.7, "mass_max_g": 100,
               "checks": [{"id": "overall_size", "type": "dimensions", "size_mm": [30, 30, 8],
                           "center_mm": [0, 0, 4], "tolerance_mm": .02},
                          {"id": "central_bore", "type": "through_holes", "centers_xy_mm": [[0, 0]],
                           "diameter_mm": 10, "z_span_mm": [0, 8], "tolerance_mm": .02},
                          {"id": "solid_volume", "type": "volume", "min_mm3": 5000, "max_mm3": 5100}]}
        part = bd.Pos(0, 0, 4) * (bd.Cylinder(15, 8) - bd.Cylinder(5, 10))
        part.label = req["part_name"]
        report = verifier.verify_shape(part, req)
        self.assertEqual(report["status"], "pass", report)
        self.assertAlmostEqual(report["measurements"]["material_mass_g"], part.volume * 2.7 / 1000)

    def test_shifted_equal_volume_hole_fails(self):
        part = self.plate + bd.Pos(30, 15, 2) * bd.Cylinder(1.6, 4)
        part -= bd.Pos(31, 15, 2) * bd.Cylinder(1.6, 6)
        part.label = "sensor_plate"
        self.assertAlmostEqual(part.volume, self.plate.volume)
        report = verifier.verify_shape(part, self.requirements)
        self.assertEqual(self.check(report, "overall_size")["status"], "pass")
        self.assertEqual(self.check(report, "mounts")["status"], "fail")

    def test_blind_hole_is_not_a_through_hole(self):
        part = self.plate + bd.Pos(30, 15, .5) * bd.Cylinder(1.6, 1)
        part.label = "sensor_plate"
        report = verifier.verify_shape(part, self.requirements)
        self.assertEqual(self.check(report, "mounts")["status"], "fail", report)

    def test_thin_end_membranes_fail_even_inside_dimension_tolerance(self):
        for center in (.01, 3.99):
            with self.subTest(cap_center_z=center):
                part = self.plate + bd.Pos(30, 15, center) * bd.Cylinder(1.6, .02)
                part.label = "sensor_plate"
                holes = verifier.geometry._cylinders(part)
                check = self.requirements["checks"][1]
                matched, _ = verifier.geometry._hole_match(holes, check["centers_xy_mm"],
                    check["diameter_mm"], check["z_span_mm"], check["tolerance_mm"])
                self.assertTrue(matched, "This regression must fit the old surface/span tolerance")
                report = verifier.verify_shape(part, self.requirements)
                result = self.check(report, "mounts")
                self.assertEqual(result["status"], "fail", report)
                self.assertGreater(max(probe["overlap_mm3"] for probe in result["actual"]["interior_probes"]), .1)

    def test_thin_middle_obstruction_is_not_a_through_hole(self):
        part = self.plate + bd.Pos(30, 15, 2) * bd.Cylinder(1.6, .02)
        part.label = "sensor_plate"
        report = verifier.verify_shape(part, self.requirements)
        self.assertEqual(self.check(report, "mounts")["status"], "fail", report)

    def test_position_tolerance_cannot_hide_caps_beyond_nominal_span(self):
        for offset, cap_center in ((.04,4.03),(-.04,-.03)):
            with self.subTest(offset_z=offset):
                part = self.plate.moved(bd.Pos(0,0,offset))
                part += bd.Pos(30,15,cap_center) * bd.Cylinder(1.6,.02)
                part.label = "sensor_plate"
                holes = verifier.geometry._cylinders(part)
                check = self.requirements["checks"][1]
                matched,_ = verifier.geometry._hole_match(holes,check["centers_xy_mm"],
                    check["diameter_mm"],check["z_span_mm"],check["tolerance_mm"])
                self.assertTrue(matched, "Shifted cap must satisfy nominal dimensional tolerance")
                report = verifier.verify_shape(part,self.requirements)
                self.assertEqual(self.check(report,"overall_size")["status"],"pass")
                result = self.check(report,"mounts")
                self.assertEqual(result["status"],"fail",report)
                self.assertGreater(max(probe["overlap_mm3"] for probe in result["actual"]["interior_probes"]),.1)

    def test_blind_depth_cannot_be_declared_as_a_through_span(self):
        req = copy.deepcopy(self.requirements)
        req["checks"][1]["z_span_mm"] = [1, 4]
        with self.assertRaisesRegex(ValueError, "full declared Z extent"):
            verifier.validate_requirements(req)

    def test_extra_bore_fails_declared_feature_count(self):
        part = self.plate - bd.Pos(0, 0, 2) * bd.Cylinder(1.6, 6)
        part.label = "sensor_plate"
        report = verifier.verify_shape(part, self.requirements)
        self.assertEqual(self.check(report, "mounts")["status"], "pass")
        self.assertEqual(self.check(report, "hole_feature_count")["status"], "fail")

    def test_wrong_size_or_position_fail(self):
        for part in (self.plate.scale(1.05), self.plate.moved(bd.Pos(2, 0, 0))):
            part.label = "sensor_plate"
            report = verifier.verify_shape(part, self.requirements)
            self.assertEqual(self.check(report, "overall_size")["status"], "fail", report)

    def test_nested_assembly_placement_is_accumulated(self):
        transform = bd.Pos(10, 20, 30) * bd.Rot(0, 0, 25)
        child = self.plate.located(transform.inverse() * self.plate.location)
        assembly = bd.Compound(children=[child], label="transform")
        assembly.location = transform
        report = verifier.verify_shape(assembly, self.requirements)
        self.assertEqual(report["status"], "pass", report)

    def test_keepout_overlap_and_mass_limit_fail(self):
        req = copy.deepcopy(self.requirements)
        req["checks"][2]["center_mm"] = [0, 0, 2]
        req["mass_max_g"] = 1
        report = verifier.verify_shape(self.plate, req)
        self.assertEqual(self.check(report, "cable_access")["status"], "fail")
        self.assertEqual(self.check(report, "mass")["status"], "fail")

    def test_disconnected_solids_never_pass(self):
        shape = bd.Compound([self.plate, bd.Pos(150, 0, 0) * bd.Box(2, 2, 2)])
        shape.label = "sensor_plate"
        report = verifier.verify_shape(shape, self.requirements)
        self.assertNotEqual(report["status"], "pass", report)

    def test_wrong_leaf_name_or_duplicate_leaves_are_unknown(self):
        shape = self.plate.moved(bd.Pos())
        shape.label = "someone_else"
        self.assertEqual(verifier.verify_shape(shape, self.requirements)["status"], "unknown")
        assembly = bd.Compound(children=[self.plate, self.plate.moved(bd.Pos(0, 0, 10))])
        self.assertEqual(verifier.verify_shape(assembly, self.requirements)["status"], "unknown")

    def test_strict_schema_rejects_unsupported_or_nonfinite_inputs(self):
        mutations = [lambda r: r.update(units="inch"), lambda r: r.update(part_name="../../escape"),
                     lambda r: r.update(density_g_per_cm3=float("nan")),
                     lambda r: r.update(mass_max_g=True), lambda r: r.update(extra="ignored"),
                     lambda r: r.update(checks=[]), lambda r: r["checks"][0].update(type="feA"),
                     lambda r: r["checks"][0].update(tolerance_mm=5),
                     lambda r: r["checks"][0].update(ignore=True),
                     lambda r: r["checks"][1].update(centers_xy_mm=[[0, 0], [0, 0]]),
                     lambda r: r["checks"][2].update(max_overlap_mm3=100)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                req = copy.deepcopy(self.requirements)
                mutate(req)
                with self.assertRaises(ValueError):
                    verifier.validate_requirements(req)
                self.assertEqual(verifier.verify_shape(self.plate, req)["status"], "unknown")

    def test_multiple_groups_cannot_reuse_one_bore(self):
        req = copy.deepcopy(self.requirements)
        duplicate = copy.deepcopy(req["checks"][1])
        duplicate["id"] = "duplicate_mounts"
        duplicate["centers_xy_mm"] = [[30.075, 15]]
        req["checks"].append(duplicate)
        with self.assertRaises(ValueError):
            verifier.validate_requirements(req)

    def test_missing_and_corrupted_exports_never_pass(self):
        self.assertEqual(verifier.verify(self.output / "missing.step", self.requirements)["status"], "unknown")
        with tempfile.TemporaryDirectory(prefix="mechanical-corrupt-") as folder:
            root = Path(folder)
            (root / "sensor_plate.stl").write_bytes((self.output / "sensor_plate.stl").read_bytes()[:-50])
            (root / "sensor_plate.step").write_bytes((self.output / "sensor_plate.step").read_bytes())
            report = verifier.verify_prints(self.output / "assembly.step", root, self.requirements)
            self.assertEqual(report["status"], "unknown", report)

    def test_stale_part_step_and_shifted_hole_stl_fail(self):
        part = self.plate + bd.Pos(30, 15, 2) * bd.Cylinder(1.6, 4)
        part -= bd.Pos(31, 15, 2) * bd.Cylinder(1.6, 6)
        part.label = "sensor_plate"
        with tempfile.TemporaryDirectory(prefix="mechanical-stale-") as folder:
            root = Path(folder)
            bd.export_step(part, root / "sensor_plate.step")
            bd.export_stl(part, root / "sensor_plate.stl", tolerance=.01)
            report = verifier.verify_prints(self.output / "assembly.step", root, self.requirements)
            self.assertEqual(report["status"], "fail", report)
            self.assertEqual(self.check(report, "sensor_plate_step_consistency")["status"], "fail")
            self.assertEqual(self.check(report, "sensor_plate_mesh_surface")["status"], "fail")


if __name__ == "__main__":
    unittest.main()
