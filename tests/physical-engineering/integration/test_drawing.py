"""Real PDF parsing/visible-callout contracts; full CAD views run in test_workflow.

These small documents isolate the drawing gate from CAD generation. Their
measured inputs are explicit test values, not claims of new geometry evidence.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'plugins/physical-engineering'
sys.path.insert(0, str(PACKAGE / 'scripts'))
import engineering


class DrawingContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.TemporaryDirectory(prefix='pe-drawing-cache-')
        cls.environment = mock.patch.dict(os.environ, {'MPLCONFIGDIR': cls.cache.name})
        cls.environment.start()
        spec = importlib.util.spec_from_file_location('drawing_under_test', PACKAGE / 'fixtures/drone-module/drawing.py')
        cls.drawing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.drawing)

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.cache.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pe-drawing-contract-')
        self.run = Path(self.temporary.name)
        (self.run / 'exports').mkdir()
        self.pdf = self.run / 'exports/module-drawing.pdf'
        self.report = {
            'status': 'pass',
            'checks': [{'id': name, 'actual': [{'diameter_mm': 3.2}] * 4}
                       for name in ('frame_interface', 'board_interface', 'tray_attachment_interface', 'bridge_attachment_interface')],
            'measurements': {
                'parts': {'tray': {'bounds_mm': [112., 72., 10.], 'volume_mm3': 20571.98},
                          'bridge': {'bounds_mm': [44., 64., 30.], 'volume_mm3': 14298.39}},
                'frame_pitch_mm': [100., 60.], 'board_pitch_mm': [30., 30.],
                'cavity_width_mm': 36.5,
                'wall_thickness_mm': {'tray_floor': 3., 'tray_left_rail': 2., 'tray_right_rail': 2.,
                                      'tray_backstop': 2., 'bridge_deck': 3.},
            },
        }
        self.sidecar()

    def tearDown(self):
        self.temporary.cleanup()

    def sidecar(self, revision='R001'):
        measured = self.report['measurements']
        data = {'revision': revision, 'units': 'mm', 'parts': {},
                'cavity_width_mm': measured['cavity_width_mm'], 'wall_thickness_mm': measured['wall_thickness_mm']}
        for name, interface in (('tray', 'frame'), ('bridge', 'board')):
            data['parts'][name] = {**measured['parts'][name],
                                   'primary_pitch_mm': measured[f'{interface}_pitch_mm'],
                                   'primary_hole_diameter_mm': 3.2, 'attachment_hole_diameter_mm': 3.2}
        (self.run / 'exports/drawing-dimensions.json').write_text(json.dumps(data))

    def blank(self, pages=2, width=420 * 72 / 25.4, height=297 * 72 / 25.4):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=width, height=height)
        writer.write(self.pdf)

    def render(self, revision='R001', report=None):
        self.blank()
        self.drawing.measured_dimension_block(self.pdf, report or self.report, revision)

    def test_visible_selectable_callouts_match_independent_values(self):
        self.render()
        engineering.check_drawing(self.run, 'R001', self.report)
        pages = PdfReader(self.pdf, strict=True).pages
        self.assertIn('CAVITY WIDTH: 36.500 MM', pages[0].extract_text())
        self.assertIn('BOARD PITCH XY: 30.000 X 30.000 MM', pages[1].extract_text())

    def test_fake_pdf_with_header_and_footer_is_not_accepted(self):
        self.pdf.write_bytes(b'%PDF-1.7\n' + b'not a PDF object\n' * 30 + b'%%EOF\n')
        with self.assertRaisesRegex(engineering.Refused, 'cannot be parsed'):
            engineering.check_drawing(self.run, 'R001', self.report)

    def test_blank_pdf_cannot_borrow_sidecar_success(self):
        self.blank()
        with self.assertRaises(engineering.Refused):
            engineering.check_drawing(self.run, 'R001', self.report)

    def test_page_count_and_paper_size_are_checked(self):
        for pages, width, height in ((1, 1190.551, 841.89), (3, 1190.551, 841.89), (2, 612, 792)):
            with self.subTest(pages=pages, width=width):
                self.blank(pages, width, height)
                with self.assertRaises(engineering.Refused):
                    engineering.check_drawing(self.run, 'R001', self.report)

    def test_pdf_revision_must_match_even_with_current_sidecar(self):
        self.render('R001')
        self.sidecar('R002')
        with self.assertRaisesRegex(engineering.Refused, 'identity, revision or units'):
            engineering.check_drawing(self.run, 'R002', self.report)

    def test_stale_cavity_in_pdf_is_detected_independently(self):
        self.render('R002')
        self.report['measurements']['cavity_width_mm'] = 41.5
        self.sidecar('R002')
        with self.assertRaisesRegex(engineering.Refused, 'cavity width'):
            engineering.check_drawing(self.run, 'R002', self.report)

    def test_wrong_hole_callout_and_wall_value_in_pdf_are_rejected(self):
        for failure in ('hole', 'wall'):
            with self.subTest(failure=failure):
                wrong = copy.deepcopy(self.report)
                if failure == 'hole':
                    wrong['checks'][0]['actual'] = [{'diameter_mm': 3.8}] * 4
                else:
                    wrong['measurements']['wall_thickness_mm']['tray_left_rail'] = 1.2
                self.render(report=wrong)
                with self.assertRaises(engineering.Refused):
                    engineering.check_drawing(self.run, 'R001', self.report)

    def test_reversed_part_pages_are_rejected(self):
        self.render()
        reader = PdfReader(self.pdf)
        writer = PdfWriter()
        writer.add_page(reader.pages[1])
        writer.add_page(reader.pages[0])
        swapped = self.pdf.with_name('swapped.pdf')
        writer.write(swapped)
        swapped.replace(self.pdf)
        with self.assertRaisesRegex(engineering.Refused, 'identity, revision or units'):
            engineering.check_drawing(self.run, 'R001', self.report)

    def test_sidecar_still_must_match_geometry(self):
        self.render()
        data = json.loads((self.run / 'exports/drawing-dimensions.json').read_text())
        data['parts']['tray']['primary_hole_diameter_mm'] = 4.0
        (self.run / 'exports/drawing-dimensions.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(engineering.Refused, 'primary_hole_diameter_mm'):
            engineering.check_drawing(self.run, 'R001', self.report)

    def test_stdlib_wrapper_uses_explicit_pinned_runtime(self):
        self.render()
        readback = self.run / 'checks.json'
        readback.write_text(json.dumps(self.report))
        # -S removes site-packages from the caller. The PDF dependency must be
        # loaded only by the explicitly supplied runtime subprocess.
        code = '''import importlib.util, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec('pypdf') is None
import engineering
stages = []
engineering.check_drawing(Path(sys.argv[2]), 'R001',
    json.loads(Path(sys.argv[3]).read_text()), Path(sys.argv[4]), stages)
assert len(stages) == 1 and stages[0]['stage'] == 'pdf_readback'
assert stages[0]['returncode'] == 0
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
                                 str(PACKAGE / 'scripts'), str(self.run),
                                 str(readback), sys.prefix],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pdf_timeout_preserves_stage_diagnostics(self):
        self.render()
        stages = []
        timeout = subprocess.TimeoutExpired('pdf_readback', 30,
                                             output=b'partial output', stderr=b'decoder stalled')
        with mock.patch.object(engineering.subprocess, 'run', side_effect=timeout):
            with self.assertRaisesRegex(engineering.Refused, 'cannot be parsed'):
                engineering.check_drawing(self.run, 'R001', self.report, stages=stages)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]['returncode'], 124)
        self.assertIn('decoder stalled', stages[0]['stderr'])
        self.assertIn('partial output', stages[0]['stdout'])


if __name__ == '__main__':
    unittest.main()
