"""Native rebuild identity catches shape changes hidden by equal summary metrics."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'plugins/physical-engineering/scripts'))
import compare_geometry as comparison
import engineering
import verify_mechanical
import build123d as bd


class GeometryComparison(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pe-shape-identity-')
        self.root = Path(self.temp.name)
        self.a = self.root / 'delivered.step'
        self.b = self.root / 'rebuilt.step'
        self.req = {'schema_version': 1, 'profile_id': 'mechanical-part-v1', 'units': 'mm',
                    'part_name': 'mount', 'envelope_max_mm': [80, 50, 4],
                    'density_g_per_cm3': 1.25, 'mass_max_g': 30,
                    'checks': [{'id': 'size', 'type': 'dimensions', 'size_mm': [80, 50, 4],
                                'center_mm': [0, 0, 2], 'tolerance_mm': .05}]}

    def tearDown(self):
        self.temp.cleanup()

    def part(self, notch_x):
        part = bd.Pos(0, 0, 2) * bd.Box(80, 50, 4)
        part -= bd.Pos(notch_x, 25, 2) * bd.Box(10, 10, 6)
        part.label = 'mount'
        return part

    def export(self, a, b):
        bd.export_step(a, self.a)
        bd.export_step(b, self.b)

    def test_equal_volume_bounds_and_summaries_do_not_hide_shifted_notch(self):
        self.export(self.part(-15), self.part(15))
        a = verify_mechanical.verify(self.a, self.req)
        b = verify_mechanical.verify(self.b, self.req)
        self.assertEqual(a['status'], 'pass', a)
        self.assertEqual(b['status'], 'pass', b)
        self.assertEqual(engineering.compare_values(a['measurements'], b['measurements']), [])
        report = comparison.compare_steps(self.a, self.b, ['mount'])
        self.assertEqual(report['status'], 'fail', report)
        self.assertAlmostEqual(report['parts']['mount']['symmetric_difference_mm3'], 400, places=5)
        self.assertAlmostEqual(report['parts']['mount']['max_bounds_drift_mm'], 0)
        self.assertEqual(report['inputs']['delivered'], {'file': 'delivered.step', 'sha256': comparison.digest(self.a)})
        self.assertEqual(report['inputs']['rebuilt'], {'file': 'rebuilt.step', 'sha256': comparison.digest(self.b)})

    def test_actual_step_roundtrip_passes_native_comparison(self):
        bd.export_step(self.part(-15), self.a)
        bd.export_step(bd.import_step(self.a), self.b)
        report = comparison.compare_steps(self.a, self.b, ['mount'])
        self.assertEqual(report['status'], 'pass', report)
        self.assertLessEqual(report['parts']['mount']['symmetric_difference_mm3'], .001)
        self.assertIn('world_location', report['parts']['mount']['delivered'])

    def test_part_names_and_duplicate_leaves_are_required(self):
        wrong = self.part(-15)
        wrong.label = 'other'
        self.export(self.part(-15), wrong)
        self.assertEqual(comparison.compare_steps(self.a, self.b, ['mount'])['status'], 'unknown')
        duplicate = bd.Compound(children=[self.part(-15), self.part(-15).moved(bd.Pos(0, 0, 10))])
        bd.export_step(duplicate, self.b)
        self.assertEqual(comparison.compare_steps(self.a, self.b, ['mount'])['status'], 'unknown')

    def test_world_placements_are_accumulated_and_displacement_fails(self):
        part = self.part(-15)
        transform = bd.Pos(12, 13, 14) * bd.Rot(0, 0, 25)
        nested = bd.Compound(children=[part.located(transform.inverse() * part.location)], label='group')
        nested.location = transform
        self.export(part, nested)
        self.assertEqual(comparison.compare_steps(self.a, self.b, ['mount'])['status'], 'pass')
        bd.export_step(part.moved(bd.Pos(2, 0, 0)), self.b)
        self.assertEqual(comparison.compare_steps(self.a, self.b, ['mount'])['status'], 'fail')

    def test_reproduction_gate_rejects_native_difference_even_when_summaries_match(self):
        # Run the actual pinned-runtime comparator, while substituting only the
        # expensive build stage with two already exported counterexample solids.
        bundle = self.root / 'bundle'
        (bundle / 'exports').mkdir(parents=True)
        (bundle / 'src').mkdir()
        (bundle / 'src/model.py').write_text('# test source; build stage supplied below\n')
        bd.export_step(self.part(-15), bundle / 'exports/assembly.step')
        accepted = verify_mechanical.verify(bundle / 'exports/assembly.step', self.req)
        engineering.write_json(bundle / 'requirements.json', {**self.req, 'revision': 'R001'})
        engineering.write_json(bundle / 'checks.json', accepted)
        receipt = {'package_hashes': engineering.package_hashes(), 'fixture': 'mechanical-part',
                   'requirement_revision': 'R001', 'selected_run': 'delivered-run',
                   'sha256': engineering.hashes(bundle)}
        engineering.write_json(bundle / 'manifest.json', receipt)
        destination = self.root / 'reproduced'

        def build(root, runtime, refresh_design):
            run = root / 'runs/run-20261106T000000Z-01234567'
            (run / 'exports').mkdir(parents=True)
            bd.export_step(self.part(15), run / 'exports/assembly.step')
            engineering.write_json(run / 'checks.json', verify_mechanical.verify(run / 'exports/assembly.step', self.req))
            manifest = {'status': 'pass', 'sha256': engineering.hashes(run), 'tool_versions': {}}
            engineering.write_json(run / 'manifest.json', manifest)
            return run, manifest

        with mock.patch.object(engineering, 'execute_run', side_effect=build):
            result = engineering.reproduce(bundle, destination, Path(sys.prefix))
        self.assertEqual(result['differences'], [])
        self.assertEqual(result['status'], 'fail', result)
        self.assertEqual(result['geometry_comparison']['status'], 'fail', result)
        receipt = engineering.read_json(destination / 'geometry-comparison.json')
        self.assertAlmostEqual(receipt['parts']['mount']['symmetric_difference_mm3'], 400, places=5)
        self.assertEqual(result['geometry_comparison']['sha256'], comparison.digest(destination / 'geometry-comparison.json'))


if __name__ == '__main__':
    unittest.main()
