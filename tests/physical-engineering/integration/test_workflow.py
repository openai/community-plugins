"""Real CLI, CAD exports, revision failure, handoff and independent-state rebuild."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'plugins/physical-engineering/scripts/engineering.py'
RUNTIME = Path(os.environ.get('PE_RUNTIME', sys.prefix)).resolve()


class Workflow(unittest.TestCase):
    def test_requirement_change_and_delivered_reproduction(self):
        with tempfile.TemporaryDirectory(prefix='pe-workflow-') as folder:
            study = Path(folder) / 'study'

            def call(*args, expected=0, script=SCRIPT):
                result = subprocess.run([sys.executable, str(script), *map(str, args), '--runtime', str(RUNTIME)], capture_output=True, text=True, timeout=600)
                self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
                return json.loads(result.stdout or result.stderr)

            call('init', study)
            baseline = call('build', study)
            baseline_checks = json.loads((Path(baseline['run']) / 'checks.json').read_text())
            self.assertEqual(baseline_checks['status'], 'pass')
            call('change', study, '--battery', 'B', '--reason', 'Wider battery request')
            failed = call('verify', study, expected=2)
            failed_checks = json.loads((Path(failed['run']) / 'checks.json').read_text())
            self.assertEqual(failed_checks['status'], 'fail')
            for clearance in failed_checks['measurements']['lateral_clearance_mm']:
                self.assertAlmostEqual(clearance, -1.75, places=5)
            call('handoff', study, expected=1)
            corrected = call('build', study)
            self.assertEqual(corrected['status'], 'pass')
            result = call('handoff', study)
            bundle = Path(result['handoff'])
            for name in ('assembly.step', 'tray.stl', 'bridge.stl'):
                self.assertTrue((bundle / 'exports' / name).is_file())
            receipt = json.loads((bundle / 'manifest.json').read_text())
            self.assertEqual(receipt['requirement_revision'], 'R002')
            drawing = json.loads((bundle / 'exports/drawing-dimensions.json').read_text())
            self.assertEqual(drawing['revision'], 'R002')
            self.assertAlmostEqual(drawing['cavity_width_mm'], 41.5)
            rebuilt = call('reproduce', bundle, Path(folder) / 'reproduced', script=bundle / 'runtime/scripts/engineering.py')
            self.assertEqual(rebuilt['status'], 'pass', rebuilt)
            self.assertTrue(rebuilt['independent_cad_state'])
            self.assertEqual(rebuilt['geometry_comparison']['status'], 'pass')
            native = json.loads((Path(folder) / 'reproduced' / rebuilt['geometry_comparison']['file']).read_text())
            self.assertEqual(set(native['parts']), {'tray', 'bridge'})
            self.assertTrue(all(part['symmetric_difference_mm3'] <= .001 for part in native['parts'].values()))
            resumed_change = call('change', Path(folder) / 'reproduced', '--frame-pitch-x', '110', '--reason', 'Transfer after reproduction', script=bundle / 'runtime/scripts/engineering.py')
            self.assertEqual(resumed_change['requirement_revision'], 'R003')
            transfer_failure = call('verify', Path(folder) / 'reproduced', expected=2, script=bundle / 'runtime/scripts/engineering.py')
            self.assertEqual(transfer_failure['status'], 'fail')
            # A stale export cannot be handed off after editing the editable source.
            source = study / 'src/model.py'
            source.write_text(source.read_text() + '\n# Source changed after export\n')
            call('handoff', study, expected=1)
            call('change', study, '--frame-pitch-x', '110', '--reason', 'Transfer exercise')
            transfer = call('build', study)
            measurements = json.loads((Path(transfer['run']) / 'checks.json').read_text())['measurements']
            self.assertEqual(measurements['frame_pitch_mm'], [110.0, 60.0])
            self.assertTrue(Path(failed['run']).is_dir(), 'Failed evidence must remain')


if __name__ == '__main__':
    unittest.main()
