"""Profile routing must preserve acceptance history and complete handoff packages."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import types

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'plugins/physical-engineering/scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('engineering_profiles', SCRIPTS / 'engineering.py')
engineering = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engineering)


class ProfileIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'study'
        engineering.initialize(self.root, profile='mechanical-part')

    def tearDown(self):
        self.temp.cleanup()

    def test_arbitrary_valid_requirements_are_preserved_without_mutating_source(self):
        before = engineering.hashes(self.root / 'src')
        state = engineering.load_study(self.root)
        original = copy.deepcopy(engineering.active(state)['requirements'])
        changed = copy.deepcopy(original)
        changed['part_name'] = 'custom_instrument'
        changed['mass_max_g'] = 42.5
        engineering.change(self.root, requirements=changed, reason='Accepted new instrument brief')
        state = engineering.load_study(self.root)
        self.assertEqual(state['requirements_history'][0]['requirements'], original)
        self.assertEqual(engineering.active(state)['requirements'], changed)
        self.assertEqual(engineering.hashes(self.root / 'src'), before)
        self.assertEqual(state['active_requirement'], 'R002')
        self.assertEqual(state['selected_run'], None)

    def test_casual_edit_of_preserved_revision_is_refused(self):
        state = engineering.read_json(self.root / 'study.json')
        state['requirements_history'][0]['requirements']['mass_max_g'] = 200
        engineering.write_json(self.root / 'study.json', state)
        with self.assertRaisesRegex(engineering.Refused, 'Preserved requirement revision'):
            engineering.load_study(self.root)

    def test_profile_specific_changes_and_malformed_requirements_leave_state_unchanged(self):
        before = (self.root / 'study.json').read_bytes()
        original = engineering.active(engineering.load_study(self.root))['requirements']
        invalid = copy.deepcopy(original)
        invalid['checks'][0]['type'] = 'user_certifies_success'
        for kwargs in ({'battery': 'B'}, {'requirements': []}, {'requirements': invalid},
                       {'requirements': original, 'battery': 'B'}, {'requirements': original}):
            with self.subTest(kwargs=kwargs), self.assertRaises((engineering.Refused, ValueError)):
                engineering.change(self.root, reason='Invalid change', **kwargs)
            self.assertEqual((self.root / 'study.json').read_bytes(), before)
        drone = Path(self.temp.name) / 'drone'
        engineering.initialize(drone)
        with self.assertRaises(engineering.Refused):
            engineering.change(drone, requirements=original, reason='Wrong profile')

    def test_invalid_initial_requirements_do_not_create_study(self):
        destination = Path(self.temp.name) / 'invalid'
        with self.assertRaises(engineering.Refused):
            engineering.initialize(destination, requirements=[], profile='mechanical-part')
        self.assertFalse(destination.exists())

    def test_incomplete_passing_report_cannot_authorize_handoff(self):
        run = self.root / 'run-test'
        run.mkdir()
        req = engineering.active(engineering.load_study(self.root))['requirements']
        engineering.write_json(run / 'requirements.json', req)
        def incomplete(*args):
            engineering.write_json(run / 'checks.json', {'status': 'pass', 'checks': []})
            return {'returncode': 0}
        with mock.patch.dict('sys.modules', {'cad_backend': types.SimpleNamespace(run_cad=incomplete)}):
            with self.assertRaisesRegex(engineering.Refused, 'lacks required'):
                engineering.run_checks(Path('/unused'), self.root / '.cad', run, profile='mechanical-part')

    def test_interrupted_handoff_preparation_preserves_current_package(self):
        current = self.root / 'handoff'
        current.mkdir()
        (current / 'sentinel').write_text('last complete handoff')
        state = engineering.load_study(self.root)
        manifest = {'status': 'pass', 'requirement_revision': 'R001', 'tool_versions': {},
                    'package_hashes': state['package_hashes']}
        run = self.root / 'mock-run'
        run.mkdir()
        with mock.patch.object(engineering, 'selected', return_value=(run, manifest)), \
             mock.patch.object(engineering, 'check_source'), \
             mock.patch.object(engineering, 'execute_run', return_value=(run, manifest)), \
             mock.patch.object(engineering, 'copy_tree', side_effect=OSError('copy interrupted')):
            with self.assertRaisesRegex(OSError, 'copy interrupted'):
                engineering.handoff(self.root, Path('/unused'))
        self.assertEqual((current / 'sentinel').read_text(), 'last complete handoff')
        self.assertEqual([path.name for path in current.iterdir()], ['sentinel'])

    def test_failed_handoff_promotion_restores_previous_complete_package(self):
        current = self.root / 'handoff'
        current.mkdir()
        (current / 'sentinel').write_text('last complete handoff')
        state = engineering.load_study(self.root)
        manifest = {'status': 'pass', 'requirement_revision': 'R001', 'tool_versions': {},
                    'package_hashes': state['package_hashes']}
        run = self.root / 'mock-run'
        run.mkdir()
        for folder in ('src', 'exports'):
            (run / folder).mkdir()
            (run / folder / 'source.txt').write_text('fixture')
        for name in ('requirements.json', 'checks.json', 'report.md', 'bom.csv'):
            (run / name).write_text('{}')
        rename = Path.rename
        def interrupt(source, destination):
            if source.name.startswith('.handoff-') and Path(destination).resolve() == current.resolve():
                raise OSError('promotion interrupted')
            return rename(source, destination)
        with mock.patch.object(engineering, 'selected', return_value=(run, manifest)), \
             mock.patch.object(engineering, 'check_source'), \
             mock.patch.object(engineering, 'execute_run', return_value=(run, manifest)), \
             mock.patch.object(Path, 'rename', interrupt):
            with self.assertRaisesRegex(OSError, 'promotion interrupted'):
                engineering.handoff(self.root, Path('/unused'))
        self.assertEqual((current / 'sentinel').read_text(), 'last complete handoff')
        self.assertFalse(list(self.root.glob('handoff-previous-*')))


if __name__ == '__main__':
    unittest.main()
