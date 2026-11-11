"""Actual CAD-kernel tests, including faults that volume-only checks miss."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
PACKAGE=ROOT/"plugins"/"physical-engineering"
sys.path.insert(0,str(PACKAGE/"scripts"))
import verify_geometry as verifier
import build123d as bd


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads((PACKAGE/"fixtures/drone-module/requirements.json").read_text())
        spec=importlib.util.spec_from_file_location("fixture_model",PACKAGE/"fixtures/drone-module/model.py")
        cls.model=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.model)
        # The master also exercises cad run. Here direct model bodies isolate
        # the verifier's CAD behavior without reusing its measured facts.
        cls.models={}
        for name,battery,pitch in (("A","A",100),("B","B",100),("A110","A",110),("transfer","B",110)):
            req=copy.deepcopy(cls.fixture)
            req["battery"]["selected"]=battery
            req["frame"]["pitch_mm"][0]=pitch
            cls.model.design=lambda req=req:req
            parts={"tray":cls.model.tray.function(),"bridge":cls.model.bridge.function()}
            cls.models[name]=(req,parts)
        cls.temp=tempfile.TemporaryDirectory(prefix="physical-engineering-geometry-")
        cls.directory=Path(cls.temp.name)
        for name,(_,parts) in cls.models.items():
            out=cls.directory/name;out.mkdir()
            for label,part in parts.items():
                bd.export_step(part,out/f"{label}.step")
                bd.export_stl(part,out/f"{label}.stl",tolerance=.01)
            bd.export_step(bd.Compound(children=list(parts.values()),label="module"),out/"assembly.step")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_by_id(self,report,name):
        return next(c for c in report["checks"] if c["id"]==name)

    def shape(self,name):
        return bd.Compound(children=list(self.models[name][1].values()),label="module")

    def test_baseline_reads_real_step_and_prints(self):
        req,_=self.models["A"]
        report=verifier.verify(self.directory/"A/assembly.step",req)
        self.assertEqual(report["status"],"pass",report)
        self.assertAlmostEqual(report["measurements"]["cavity_width_mm"],36.5)
        self.assertEqual(report["measurements"]["lateral_clearance_mm"],[.75,.75])
        self.assertGreater(report["measurements"]["polymer_mass_g"],0)
        self.assertLess(report["measurements"]["polymer_mass_g"],43.5879669473,
                        "Refined baseline should use less solid polymer than the original block fixture")
        prints=verifier.verify_prints(self.directory/"A/assembly.step",
            {p:self.directory/f"A/{p}.stl" for p in ("tray","bridge")},
            {p:self.directory/f"A/{p}.step" for p in ("tray","bridge")})
        self.assertEqual(prints["status"],"pass",prints)

    def test_wider_battery_fails_preserved_old_geometry(self):
        req,_=self.models["B"]
        report=verifier.verify(self.directory/"A/assembly.step",req)
        self.assertEqual(report["status"],"fail",report)
        self.assertEqual(report["measurements"]["lateral_clearance_mm"],[-1.75,-1.75])
        self.assertEqual(self.check_by_id(report,"battery_interference")["status"],"fail")
        self.assertEqual(self.check_by_id(report,"insertion_interference")["status"],"fail")
        self.assertEqual(self.check_by_id(report,"frame_interface")["status"],"pass")

    def test_revised_geometry_and_transfer_pass(self):
        for name in ("B","A110","transfer"):
            req,_=self.models[name]
            report=verifier.verify(self.directory/name/"assembly.step",req)
            self.assertEqual(report["status"],"pass",report)
            self.assertEqual(report["measurements"]["frame_pitch_mm"],req["frame"]["pitch_mm"])
            self.assertEqual(report["measurements"]["board_pitch_mm"],[30.,30.])

    def test_rounded_profiles_are_not_misclassified_as_bores(self):
        for name in self.models:
            report=verifier.verify(self.directory/name/"assembly.step",self.models[name][0])
            for label,part in self.models[name][1].items():
                cylinders=[face for face in part.faces() if face.geom_type==bd.GeomType.CYLINDER]
                self.assertGreater(len(cylinders),8,"Refined parts include cylindrical rounds/posts")
                self.assertEqual(len(report["measurements"]["parts"][label]["holes"]),8)

    def test_interrupted_required_bore_still_fails(self):
        tray=self.models["A"][1]["tray"]
        # A blind cylindrical pocket must not be accepted as a through mount.
        blind=tray+bd.Pos(50,30,.75)*bd.Cylinder(1.6,1.5)
        blind.label="tray"
        result=verifier.verify_shape(bd.Compound(children=[blind,self.models["A"][1]["bridge"]]),self.fixture)
        self.assertEqual(self.check_by_id(result,"frame_interface")["status"],"fail")

    def test_thin_membranes_fail_every_drone_attachment_interface(self):
        # Each cap is thinner than the 0.05 mm surface/span tolerance and was
        # accepted by the old matcher. Through-path openness is a separate test.
        cases = (("tray", "frame_interface", (50,30,.01), (0,3)),
                 ("bridge", "board_interface", (15,15,30.01), (30,33)),
                 ("tray", "tray_attachment_interface", (18,28,.01), (0,3)),
                 ("bridge", "bridge_attachment_interface", (18,28,3.01), (3,33)))
        for label, ident, center, span in cases:
            with self.subTest(interface=ident):
                parts = dict(self.models["A"][1])
                part = parts[label] + bd.Pos(*center) * bd.Cylinder(1.6,.02)
                part.label = label
                parts[label] = part
                matched,_ = verifier._hole_match(verifier._cylinders(part), [center[:2]], 3.2, span, .05)
                self.assertTrue(matched, "Regression must meet the old surface/span tolerance")
                report = verifier.verify_shape(bd.Compound(children=list(parts.values())), self.fixture)
                result = self.check_by_id(report, ident)
                self.assertEqual(result["status"], "fail", report)
                self.assertGreater(max(probe["overlap_mm3"] for probe in result["interior_probes"]), .1)

    def test_retention_guides_are_open_with_connected_end_material(self):
        for name in ("A","B","A110","transfer"):
            req,parts=self.models[name]
            tray=verifier.read_parts(self.directory/name/"assembly.step")["tray"]
            width=req["battery"]["envelopes_mm"][req["battery"]["selected"]][1]
            for x in (-34,34):
                for sign in (-1,1):
                    y=sign*((width+1.5)/2+5)
                    # A nominal 12x2 webbing gauge passes through each actual
                    # exported slot, while the end bar remains solid and joined.
                    gauge=bd.Pos(x,y,1.5)*bd.Box(12,2,5)
                    self.assertAlmostEqual(verifier._overlap(tray,gauge),0,places=5)
                    end=bd.Pos(x+7.5,y,1.5)*bd.Box(1,1.5,3)
                    self.assertAlmostEqual(verifier._overlap(tray,end),end.volume,places=5)
            self.assertEqual(len(tray.solids()),1)

    def test_excessive_clearance_is_not_accepted(self):
        report=verifier.verify(self.directory/"B/assembly.step",self.fixture)
        self.assertEqual(report["status"],"fail")
        self.assertEqual(report["measurements"]["lateral_clearance_mm"],[3.25,3.25])

    def shifted_tray(self):
        tray=self.models["A"][1]["tray"]
        # Fill one original hole and cut the same diameter one millimetre away.
        shifted=tray+bd.Pos(50,30,1.5)*bd.Cylinder(1.6,3)
        shifted-=bd.Pos(51,30,1.5)*bd.Cylinder(1.6,5)
        shifted.label="tray"
        return shifted

    def test_shifted_hole_fails_even_with_equal_volume(self):
        shifted=self.shifted_tray()
        self.assertAlmostEqual(shifted.volume,self.models["A"][1]["tray"].volume,places=5)
        report=verifier.verify_shape(bd.Compound(children=[shifted,self.models["A"][1]["bridge"]]),self.fixture)
        self.assertEqual(self.check_by_id(report,"frame_interface")["status"],"fail")

    def test_thin_declared_wall_fails_and_perforated_face_is_unknown(self):
        tray=self.models["A"][1]["tray"]
        thin=tray-bd.Pos(0,-19.75,6.5)*bd.Box(106,1,7)
        thin.label="tray"
        report=verifier.verify_shape(bd.Compound(children=[thin,self.models["A"][1]["bridge"]]),self.fixture)
        self.assertEqual(self.check_by_id(report,"declared_planar_walls")["status"],"fail",report)
        self.assertAlmostEqual(report["measurements"]["wall_thickness_mm"]["tray_left_rail"],1)
        # A surviving bounding box is insufficient to claim planar coverage.
        perforated=tray-bd.Pos(0,-19.25,6.5)*bd.Box(2,4,2)
        perforated.label="tray"
        report=verifier.verify_shape(bd.Compound(children=[perforated,self.models["A"][1]["bridge"]]),self.fixture)
        self.assertEqual(report["status"],"unknown",report)

    def test_nested_locations_accumulate_once(self):
        transform=bd.Pos(9,12,7)*bd.Rot(0,0,30)
        children=[p.located(transform.inverse()*p.location) for p in self.models["A"][1].values()]
        nested=bd.Compound(children=children,label="subassembly")
        nested.location=transform
        report=verifier.verify_shape(bd.Compound(children=[nested],label="root"),self.fixture)
        self.assertEqual(report["status"],"pass",report)
        misplaced=bd.Compound(children=list(self.models["A"][1].values()),label="subassembly")
        misplaced.location=transform
        failed=verifier.verify_shape(bd.Compound(children=[misplaced]),self.fixture)
        self.assertNotEqual(failed["status"],"pass")
        self.assertEqual(self.check_by_id(failed,"frame_interface")["status"],"fail")

    def test_wrong_units_and_scaled_geometry_fail(self):
        req=copy.deepcopy(self.fixture);req["units"]="inch"
        report=verifier.verify(self.directory/"A/assembly.step",req)
        self.assertEqual(report["status"],"fail")
        scaled=[]
        for label,part in self.models["A"][1].items():
            item=part.scale(25.4);item.label=label;scaled.append(item)
        report=verifier.verify_shape(bd.Compound(children=scaled),self.fixture)
        self.assertNotEqual(report["status"],"pass")
        self.assertEqual(self.check_by_id(report,"frame_interface")["status"],"fail")

    def test_stale_part_step_fails_consistency(self):
        report=verifier.verify_prints(self.directory/"B/assembly.step",
            {p:self.directory/f"B/{p}.stl" for p in ("tray","bridge")},
            {p:self.directory/f"A/{p}.step" for p in ("tray","bridge")})
        self.assertEqual(report["status"],"fail",report)
        self.assertEqual(self.check_by_id(report,"tray_step_consistency")["status"],"fail")

    def test_shifted_hole_stl_fails_surface_readback(self):
        file=self.directory/"shifted.stl"
        bd.export_stl(self.shifted_tray(),file,tolerance=.01)
        report=verifier.verify_print(file,self.models["A"][1]["tray"])
        self.assertEqual(self.check_by_id(report,"mesh_bounds")["status"],"pass")
        self.assertEqual(self.check_by_id(report,"mesh_volume")["status"],"pass")
        self.assertEqual(self.check_by_id(report,"mesh_surface")["status"],"fail")

    def test_mesh_caps_fail_every_drone_attachment_interface(self):
        cases = (("tray", (50, 30, .0005)), ("bridge", (15, 15, 30.0005)),
                 ("tray", (18, 28, 2.9995)), ("bridge", (18, 28, 17.137)))
        references = verifier.read_parts(self.directory / "A/assembly.step")
        for label, center in cases:
            with self.subTest(part=label, center=center):
                capped = self.models["A"][1][label] + bd.Pos(*center) * bd.Cylinder(1.6, .001)
                path = self.directory / "capped.stl"
                bd.export_stl(capped, path, tolerance=.01)
                report = verifier.verify_print(path, references[label])
                for ident in ("mesh_topology", "mesh_bounds", "mesh_volume"):
                    self.assertEqual(self.check_by_id(report, ident)["status"], "pass", report)
                surface = self.check_by_id(report, "mesh_surface")
                self.assertEqual(surface["status"], "fail", report)
                self.assertGreater(surface["actual"]["bore_coverage"]["intruding_triangles"], 0)

    def test_corrupt_mesh_does_not_pass(self):
        file=self.directory/"corrupt.stl"
        file.write_bytes((self.directory/"A/tray.stl").read_bytes()[:-50])
        self.assertEqual(verifier.verify_print(file,self.models["A"][1]["tray"])["status"],"unknown")

    def test_missing_labels_and_nonfinite_input_are_unknown(self):
        self.assertEqual(verifier.verify_shape(bd.Compound(children=[self.models["A"][1]["tray"]]),self.fixture)["status"],"unknown")
        req=copy.deepcopy(self.fixture);req["frame"]["pitch_mm"][0]=float("nan")
        self.assertEqual(verifier.verify_shape(self.shape("A"),req)["status"],"unknown")
        req=copy.deepcopy(self.fixture);req["envelope_max_mm"]=[]
        self.assertEqual(verifier.verify_shape(self.shape("A"),req)["status"],"unknown")


if __name__=="__main__":
    unittest.main()
