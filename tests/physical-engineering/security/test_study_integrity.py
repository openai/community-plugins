import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import types

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('engineering_integrity', ROOT / 'plugins/physical-engineering/scripts/engineering.py')
engineering = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engineering)


class StudyIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'study'
        engineering.initialize(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_requirement_change_preserves_original_and_geometry(self):
        original = engineering.hashes(self.root / 'src')
        engineering.change(self.root, battery='B', reason='Wider component')
        study = engineering.load_study(self.root)
        self.assertEqual(study['requirements_history'][0]['requirements']['battery']['selected'], 'A')
        self.assertEqual(engineering.active(study)['requirements']['battery']['selected'], 'B')
        self.assertEqual(engineering.hashes(self.root / 'src'), original)
        self.assertEqual(study['selected_run'], None)

    def test_no_silent_requirement_relaxation(self):
        before = (self.root / 'study.json').read_bytes()
        with self.assertRaises(engineering.Refused):
            engineering.change(self.root, frame_pitch_x=200, reason='Force impossible pitch')
        self.assertEqual((self.root / 'study.json').read_bytes(), before)

    def test_hash_manifest_rejects_escape_and_tamper(self):
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('outside')
        for path in ('../outside.txt', str(outside), 'src/../../outside.txt', 'src\\escape'):
            with self.subTest(path=path), self.assertRaises(engineering.Refused):
                engineering.verify_hashes(self.root, {path: engineering.digest(outside)})
        expected = engineering.hashes(self.root / 'src')
        (self.root / 'src/model.py').write_text('# changed after export')
        with self.assertRaises(engineering.Refused):
            engineering.verify_hashes(self.root / 'src', expected)

    def test_symlinked_artifact_is_refused(self):
        target = Path(self.temp.name) / 'external'
        target.write_text('external')
        (self.root / 'src/external').symlink_to(target)
        with self.assertRaises(engineering.Refused):
            engineering.hashes(self.root / 'src')

    def test_edited_checker_identity_refuses_resume(self):
        state = json.loads((self.root / 'study.json').read_text())
        state['package_hashes']['scripts/verify_geometry.py'] = '0' * 64
        (self.root / 'study.json').write_text(json.dumps(state))
        with self.assertRaises(engineering.Refused):
            engineering.load_study(self.root)

    def test_original_requirement_cannot_be_relaxed_by_edit(self):
        state = json.loads((self.root / 'study.json').read_text())
        state['requirements_history'][0]['requirements']['manufacturing']['minimum_declared_planar_wall_mm'] = .5
        (self.root / 'study.json').write_text(json.dumps(state))
        with self.assertRaises(engineering.Refused):
            engineering.load_study(self.root)

    def test_symlinked_source_directory_is_refused(self):
        (self.root / 'src').rename(self.root / 'original-src')
        outside = Path(self.temp.name) / 'external-src'
        outside.mkdir()
        sentinel = outside / 'design.json'
        sentinel.write_text('unchanged')
        (self.root / 'src').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(engineering.Refused):
            engineering.relative(self.root, 'src/design.json')
        with self.assertRaises(engineering.Refused):
            engineering.hashes(self.root / 'src')
        self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_checker_timeout_cannot_reuse_a_passing_report(self):
        run = self.root / 'run-test'
        run.mkdir()
        def timeout(*args):
            (run / 'checks.json').write_text(json.dumps({'status': 'pass'}))
            return {'returncode': 124, 'stderr': 'timed out'}
        with mock.patch.dict('sys.modules', {'cad_backend': types.SimpleNamespace(run_cad=timeout)}):
            with self.assertRaises(engineering.Refused):
                engineering.run_checks(Path('/unused'), self.root / '.cad', run)
        with mock.patch.dict('sys.modules', {'cad_backend': types.SimpleNamespace(run_cad=mock.Mock())}):
            with self.assertRaises(engineering.Refused):
                engineering.run_checks(Path('/unused'), self.root / '.cad', run)

    def test_reproduced_revision_advances_without_collision(self):
        state = json.loads((self.root / 'study.json').read_text())
        state['requirements_history'][0]['id'] = state['active_requirement'] = 'R002'
        (self.root / 'study.json').write_text(json.dumps(state))
        engineering.change(self.root, frame_pitch_x=110, reason='Transfer after reproduction')
        self.assertEqual(engineering.load_study(self.root)['active_requirement'], 'R003')

    def test_single_writer_and_existing_study_are_preserved(self):
        with engineering.writer(self.root):
            with self.assertRaises(engineering.Refused):
                with engineering.writer(self.root):
                    self.fail('second writer entered')
        with self.assertRaises(engineering.Refused):
            engineering.initialize(self.root)


if __name__ == '__main__':
    unittest.main()
