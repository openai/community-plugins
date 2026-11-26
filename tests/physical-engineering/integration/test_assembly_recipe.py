"""Exercise the shared CLI with an edited non-aircraft design and an ECAD attachment."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[3] / 'plugins/physical-engineering'
CLI = PACKAGE / 'scripts/engineering.py'


class EditableAssemblyTest(unittest.TestCase):
    def test_custom_board_assembly_rebuilds_from_delivery(self):
        runtime = os.environ.get('PE_RUNTIME', sys.prefix)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = root / 'edited design'
            output = root / 'complete'
            rebuilt = root / 'rebuild'
            env = {**os.environ, 'PE_RUNTIME': runtime}

            def call(*args):
                result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            call(sys.executable, CLI, 'assembly-init', design)
            config = json.loads((design / 'assembly.json').read_text())
            config['id'] = 'custom-desk-instrument'
            config['display']['title'] = 'Desk instrument'
            config['bom']['title'] = 'Desk instrument'
            (design / 'assembly.json').write_text(json.dumps(config))
            board = design / 'boards/interface-revision.txt'
            board.parent.mkdir(exist_ok=True)
            board.write_text('Enclosure interface drawing, revision B\n')
            with (design / 'boards.md').open('a') as notes:
                notes.write('\n[Interface revision](boards/interface-revision.txt)\n')
            receipt = call(sys.executable, CLI, 'assembly-build', design, output)
            self.assertEqual(receipt['design'], 'custom-desk-instrument')
            self.assertIn('Desk instrument', (output / 'viewer.html').read_text())
            self.assertEqual((output / 'boards/interface-revision.txt').read_bytes(), board.read_bytes())
            result = call('sh', output / 'rebuild.sh', rebuilt)
            self.assertEqual(result['reproduction']['status'], 'pass')
            self.assertEqual(call(sys.executable, CLI, 'assembly-verify', rebuilt)['checks'], result['checks'])

            with (design / 'boards.md').open('a') as notes:
                notes.write('\n[Missing drawing](boards/missing.pdf)\n')
            failed = subprocess.run([sys.executable, CLI, 'assembly-build', design, root / 'incomplete'],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=240)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('boards/missing.pdf', failed.stderr)
            self.assertFalse((root / 'incomplete').exists())
