"""Cold installed-package use without drone source, demo files or repository imports."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'plugins/physical-engineering'
RUNTIME = Path(os.environ.get('PE_RUNTIME', sys.prefix)).resolve()


class MechanicalWorkflow(unittest.TestCase):
    def test_custom_requirement_change_and_delivered_rebuild_from_cold_package(self):
        with tempfile.TemporaryDirectory(prefix='pe-mechanical-cold-') as folder:
            root = Path(folder)
            installed = root / 'plugin'
            shutil.copytree(PACKAGE, installed)
            script = installed / 'scripts/engineering.py'
            study = root / 'instrument-study'
            requirements = json.loads((installed / 'fixtures/sensor-plate/requirements.json').read_text())
            requirements['part_name'] = 'instrument_mount'
            for check in requirements['checks']:
                if check['type'] == 'dimensions':
                    check['size_mm'] = [88, 56, 4]
                elif check['type'] == 'through_holes':
                    check['centers_xy_mm'] = [[x, y] for x in (-33, 33) for y in (-18, 18)]
                elif check['type'] == 'keepout':
                    check['center_mm'] = [51, 0, 3]
            requirement_file = root / 'accepted-requirements.json'
            requirement_file.write_text(json.dumps(requirements))

            def call(*args, expected=0, executable=script):
                environment = os.environ.copy()
                environment.pop('PYTHONPATH', None)
                environment['PYTHONDONTWRITEBYTECODE'] = '1'
                result = subprocess.run([sys.executable, '-B', str(executable), *map(str, args),
                                         '--runtime', str(RUNTIME)], cwd=root, env=environment,
                                        capture_output=True, text=True, timeout=600)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                return json.loads(result.stdout or result.stderr)

            call('init', study, '--profile', 'mechanical-part', '--requirements', requirement_file,
                 '--name', 'Instrument mounting part')
            baseline = call('build', study)
            self.assertEqual(baseline['status'], 'pass', baseline)
            baseline_checks = json.loads((Path(baseline['run']) / 'checks.json').read_text())
            self.assertEqual(set(baseline_checks['measurements']['parts']), {'instrument_mount'})
            initial_source = (study / 'src/design.json').read_bytes()
            changed = copy.deepcopy(requirements)
            for check in changed['checks']:
                if check['type'] == 'through_holes':
                    check['centers_xy_mm'] = [[x, y] for x in (-35, 35) for y in (-18, 18)]
            requirement_file.write_text(json.dumps(changed))
            revision = call('change', study, '--requirements', requirement_file, '--reason', 'New instrument bolt pattern')
            self.assertEqual(revision['requirement_revision'], 'R002')
            self.assertEqual((study / 'src/design.json').read_bytes(), initial_source)
            failed = call('verify', study, expected=2)
            checks = json.loads((Path(failed['run']) / 'checks.json').read_text())
            self.assertEqual(checks['status'], 'fail')
            self.assertEqual(next(item for item in checks['checks'] if item['id'] == 'mounts')['status'], 'fail')
            call('handoff', study, expected=1)
            corrected = call('build', study)
            self.assertEqual(corrected['status'], 'pass')
            bundle = Path(call('handoff', study)['handoff'])
            for name in ('assembly.step', 'instrument_mount.step', 'instrument_mount.stl'):
                self.assertTrue((bundle / 'exports' / name).is_file())
            self.assertFalse((bundle / 'exports/module-drawing.pdf').exists())
            self.assertIn('instrument_mount', (bundle / 'bom.csv').read_text())
            self.assertIn('solid_material_mass_g', (bundle / 'bom.csv').read_text())
            self.assertNotIn('solid_polymer_mass_g', (bundle / 'bom.csv').read_text())
            self.assertNotIn('M3 bridge', (bundle / 'bom.csv').read_text())
            receipt = json.loads((bundle / 'manifest.json').read_text())
            self.assertEqual(receipt['fixture'], 'mechanical-part')
            self.assertEqual(receipt['requirement_revision'], 'R002')
            reproduced = root / 'independent-state'
            result = call('reproduce', bundle, reproduced,
                          executable=bundle / 'runtime/scripts/engineering.py')
            self.assertEqual(result['status'], 'pass', result)
            self.assertEqual(result['differences'], [])
            self.assertEqual(result['geometry_comparison']['status'], 'pass')
            native = json.loads((reproduced / result['geometry_comparison']['file']).read_text())
            self.assertEqual(set(native['parts']), {'instrument_mount'})
            self.assertLessEqual(native['parts']['instrument_mount']['symmetric_difference_mm3'], .001)
            self.assertTrue(result['independent_cad_state'])
            state = json.loads((reproduced / 'study.json').read_text())
            self.assertEqual(state['fixture'], 'mechanical-part')
            self.assertEqual(state['requirements_history'][0]['requirements'], changed)
            self.assertTrue(Path(failed['run']).is_dir(), 'Actual failed run must remain preserved')
            # Authored code changes invalidate previously selected output.
            with (study / 'src/model.py').open('a') as source:
                source.write('\n# New authored design revision, requires a fresh build.\n')
            call('handoff', study, expected=1)
            self.assertFalse(list(installed.rglob('__pycache__')), 'Installed package remains immutable')


if __name__ == '__main__':
    unittest.main()
