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
        surface = self.check(mesh, "sensor_plate_mesh_surface")["actual"]
        self.assertEqual(surface["reverse_coverage"]["checked_points"], surface["reverse_coverage"]["total_points"])
        self.assertEqual(surface["bore_coverage"]["checked_bores"], 4)

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

    def test_each_filled_bore_stl_fails_even_when_step_and_volume_match(self):
        reference = bd.import_step(self.output / "assembly.step")
        for x, y in self.requirements["checks"][1]["centers_xy_mm"]:
            with self.subTest(center=(x, y)):
                filled = self.plate + bd.Pos(x, y, 2) * bd.Cylinder(1.6, 4)
                path = self.output / "filled-bore.stl"
                bd.export_stl(filled, path, tolerance=.01)
                report = verifier.geometry.verify_print(path, reference)
                for ident in ("mesh_topology", "mesh_bounds", "mesh_volume"):
                    self.assertEqual(self.check(report, ident)["status"], "pass", report)
                self.assertEqual(self.check(report, "mesh_surface")["status"], "fail", report)

    def test_mesh_caps_and_partial_webs_are_not_excused_by_tessellation_tolerance(self):
        reference = bd.import_step(self.output / "assembly.step")
        obstructions = [(self.plate, bd.Pos(30, 15, z) * bd.Cylinder(1.6, .001))
                        for z in (.0005, 2.137, 3.9995)]
        # Shift the solid within export tolerance so an end cap just beyond
        # the reference span remains attached to a closed manifold solid.
        obstructions.append((self.plate.moved(bd.Pos(0, 0, .001)),
                             bd.Pos(30, 15, 4.0005) * bd.Cylinder(1.6, .001)))
        # A narrow web does not cover the bore centerline. Sampling only the
        # axis, bore wall or a few Z slices cannot establish an open bore.
        obstructions.append((self.plate, bd.Pos(30, 16, 2.137) * bd.Box(3.2, .15, .001)))
        for index, (base, obstruction) in enumerate(obstructions):
            with self.subTest(obstruction=index):
                path = self.output / "obstructed-bore.stl"
                bd.export_stl(base + obstruction, path, tolerance=.01)
                report = verifier.geometry.verify_print(path, reference)
                for ident in ("mesh_topology", "mesh_bounds", "mesh_volume"):
                    self.assertEqual(self.check(report, ident)["status"], "pass", report)
                surface = self.check(report, "mesh_surface")
                self.assertEqual(surface["status"], "fail", report)
                self.assertGreater(surface["actual"]["bore_coverage"]["intruding_triangles"], 0)

    def test_missing_small_non_bore_feature_fails_reverse_coverage(self):
        base = bd.Box(80, 50, 4) + bd.Pos(0, 0, 2.5) * bd.Box(2, 2, 1)
        reference = base + bd.Pos(20, 0, 2.25) * bd.Box(.3, .3, .5)
        path = self.output / "missing-small-feature.stl"
        bd.export_stl(base, path, tolerance=.01)
        report = verifier.geometry.verify_print(path, reference)
        for ident in ("mesh_topology", "mesh_bounds", "mesh_volume"):
            self.assertEqual(self.check(report, ident)["status"], "pass", report)
        surface = self.check(report, "mesh_surface")
        self.assertEqual(surface["status"], "fail", report)
        self.assertIsNotNone(surface["actual"]["reverse_coverage"]["uncovered_point_mm"])

    def test_curved_rotated_ring_and_blind_pocket_meshes_pass(self):
        ring = bd.Cylinder(15, 8) - bd.Cylinder(5, 10)
        ring = ring.fillet(.4, ring.edges())
        blind = bd.Box(20, 20, 8) - bd.Pos(0, 0, 3) * bd.Cylinder(3, 6)
        for name, original in (("rounded-ring", ring), ("blind-pocket", blind)):
            with self.subTest(shape=name):
                part = original.moved(bd.Pos(19, -8, 3) * bd.Rot(21, 33, 17))
                path = self.output / f"{name}.stl"
                step = self.output / f"{name}.step"
                bd.export_step(part, step)
                bd.export_stl(part, path, tolerance=.01)
                report = verifier.geometry.verify_print(path, bd.import_step(step))
                self.assertEqual(report["status"], "pass", report)
                bores = self.check(report, "mesh_surface")["actual"]["bore_coverage"]
                if name == "rounded-ring":
                    self.assertEqual(bores["checked_bores"], 1)
                else:
                    self.assertEqual(bores["skipped_nonempty_spans"], 1)

    def test_rotated_ring_mesh_obstruction_fails(self):
        ring = bd.Cylinder(15, 8) - bd.Cylinder(5, 10)
        transform = bd.Pos(19, -8, 3) * bd.Rot(21, 33, 17)
        blocked = ring + bd.Pos(0, 0, .137) * bd.Cylinder(5, .001)
        path = self.output / "blocked-rotated-ring.stl"
        bd.export_stl(blocked.moved(transform), path, tolerance=.01)
        report = verifier.geometry.verify_print(path, ring.moved(transform))
        for ident in ("mesh_topology", "mesh_bounds", "mesh_volume"):
            self.assertEqual(self.check(report, ident)["status"], "pass", report)
        surface = self.check(report, "mesh_surface")
        self.assertEqual(surface["status"], "fail", report)
        self.assertGreater(surface["actual"]["bore_coverage"]["intruding_triangles"], 0)

    def test_coarse_cli_tessellation_passes_the_same_surface_coverage(self):
        # The CAD CLI has a coarser angular tessellation than build123d's
        # default export. Exercise its actual writer against the fine checker
        # reference mesh, without weakening either linear tolerance.
        from cad.engine.export import translate
        path = self.output / "coarse-cli.stl"
        translate(self.output / "assembly.step", None, path, tolerance=.02)
        report = verifier.geometry.verify_print(path, bd.import_step(self.output / "assembly.step"))
        self.assertEqual(report["status"], "pass", report)
        reverse = self.check(report, "mesh_surface")["actual"]["reverse_coverage"]
        self.assertEqual(reverse["checked_points"], reverse["total_points"])
        self.assertGreater(reverse["candidate_triangle_queries"], 0)

    def test_candidate_triangle_distances_cover_faces_edges_and_degeneracy(self):
        import numpy as np
        triangle = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        points = np.array([[.2, .2, 1], [.5, -1, 1], [1, 1, 0], [-1, -1, 0], [.5, 1, 1]], dtype=float)
        triangles = np.array([[triangle]]*4 + [[[[0, 0, 0], [1, 0, 0], [2, 0, 0]]]], dtype=float)
        actual = verifier.geometry._candidate_triangle_distances_squared(points, triangles)
        np.testing.assert_allclose(actual[:, 0], [1, 2, .5, 2, 2], rtol=0, atol=1e-12)

    def test_nearby_centroids_cannot_replace_complete_triangle_search(self):
        # Eight nearby centroids hide the large triangle containing the point.
        # Candidate rejection must fall through to the complete AABB search.
        point = (0., 0., 0.)
        triangle = ((-100., -1., 0.), (100., -1., 0.), (0., 100., 0.))
        distractors = [((-.1, -.1, z), (.1, -.1, z), (0., .1, z))
                       for z in range(1, 9)]
        triangles = [triangle, *distractors]
        self.assertFalse(verifier.geometry._nearby_triangle_witnesses([point], triangles, .02)[0])
        self.assertTrue(verifier.geometry._mesh_covers(point, verifier.geometry._triangle_tree(triangles), .02**2))


if __name__ == "__main__":
    unittest.main()
