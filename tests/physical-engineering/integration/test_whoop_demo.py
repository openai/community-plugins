"""Run the demo from an installed-layout copy, then rebuild from its delivery."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[3] / 'plugins/physical-engineering'
RUNTIME = Path(os.environ.get('PE_RUNTIME', sys.prefix)).resolve()


class WhoopDemoTest(unittest.TestCase):
    def test_offline_delivery_and_rebuild(self):
        with tempfile.TemporaryDirectory(prefix='pe-whoop-') as tmp:
            root = Path(tmp)
            plugin = root / 'installed plugin'
            shutil.copytree(PACKAGE, plugin)
            cli = plugin / 'scripts/engineering.py'
            demo = root / 'first run'
            demo.mkdir()  # An explicitly created, empty project directory is supported.
            rebuilt = root / 'second run'
            env = {**os.environ, 'PE_RUNTIME': str(RUNTIME), 'PYTHONDONTWRITEBYTECODE': '1'}

            def invoke(*args, expected=0):
                result = subprocess.run(args, cwd=root, capture_output=True, text=True, env=env, timeout=600)
                self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
                return json.loads(result.stdout or result.stderr)

            first = invoke(sys.executable, cli, 'whoop-demo', demo)
            self.assertGreater(first['checks'], 20)
            source_hash = hashlib.sha256((demo / 'manifest.json').read_bytes()).hexdigest()
            repeat = invoke(sys.executable, cli, 'whoop-demo', demo, expected=1)
            self.assertIn('existing handoff', repeat['reason'])
            (demo / 'print/.DS_Store').write_bytes(b'Finder')
            (demo / '~$bom.xlsx').write_bytes(b'Excel')
            (demo / 'source/design/.DS_Store').write_bytes(b'Finder')
            shutil.rmtree(plugin)  # The delivered package must stand alone after uninstall.
            result = invoke('sh', demo / 'rebuild.sh', rebuilt)
            self.assertEqual(result['reproduction']['baseline_manifest_sha256'], source_hash)
            standalone = rebuilt / 'source/scripts/engineering.py'
            self.assertEqual(invoke(sys.executable, standalone, 'assembly-verify', rebuilt)['checks'], first['checks'])
            (rebuilt / 'components/flight-controller.step').unlink()
            failure = invoke(sys.executable, standalone, 'assembly-verify', rebuilt, expected=1)
            self.assertIn('flight-controller.step', failure['reason'])
