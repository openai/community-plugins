"""Shared replay takes are data; their paths must not become publication targets."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
import render_replay as replay


class ReplayArtifactPaths(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.take = self.root / "take"
        self.take.mkdir()
        self.relative = "artifacts/0123456789abcdef.json"
        self.artifact = self.take / self.relative
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b'{"reviewed": true}\n')
        self.output = self.root / "published" / "render"

    def seal(self, relative=None, published=False):
        relative = self.relative if relative is None else relative
        events = [{"t": 0, "kind": "capture_started"}]
        for index, scene in enumerate(replay.SCENES):
            start = 1 + index * 100
            events.extend([
                {"t": start, "kind": "phase_started", "scene": scene, "argv": ["codex", "exec"]},
                {"t": start + 1, "kind": "operator_input", "scene": scene, "text": f"Fixture {scene}"},
            ])
            if index == 0:
                observed = {"t": start + 2, "kind": "artifact_observed", "scene": scene,
                            "label": "Synthetic path fixture", "artifact": relative,
                            "sha256": replay.sha(self.artifact)}
                if published:
                    observed["published_sha256"] = observed["sha256"]
                    observed["sha256"] = "f" * 64
                events.append(observed)
            events.append({"t": start + 3, "kind": "phase_finished", "scene": scene, "returncode": 0})
        events.append({"t": 600, "kind": "capture_finished"})
        (self.take / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
        (self.take / "metadata.json").write_text('{"fixture": true}\n')
        complete = {"duration_seconds": 600, "events_sha256": replay.sha(self.take / "events.jsonl"),
                    "metadata_sha256": replay.sha(self.take / "metadata.json")}
        (self.take / "capture-complete.json").write_text(json.dumps(complete))

    def publish(self, encode=None):
        def fixture_encode(take, video, *args, **kwargs):
            video.write_bytes(b"synthetic encoding fixture")
            if encode:
                encode(take, video)

        command = ["render_replay.py", "--take", str(self.take), "--output", str(self.output),
                   "--ffmpeg", "fixture-ffmpeg", "--ffprobe", "fixture-ffprobe"]
        probe = types.SimpleNamespace(stdout=json.dumps({"format": {}, "streams": []}))
        with mock.patch.object(sys, "argv", command), \
                mock.patch.object(replay, "encode", side_effect=fixture_encode) as encoder, \
                mock.patch.object(replay.subprocess, "run", return_value=probe), \
                contextlib.redirect_stdout(io.StringIO()):
            replay.main()
        return encoder

    def test_load_accepts_recorded_paths_and_published_hash_without_rewriting_events(self):
        for published in (False, True):
            with self.subTest(published=published):
                self.seal(published=published)
                before = (self.take / "events.jsonl").read_bytes()
                _, events = replay.load_take(self.take)
                observed = next(event for event in events if event["kind"] == "artifact_observed")
                self.assertEqual(observed["artifact"], self.relative)
                self.assertEqual((self.take / "events.jsonl").read_bytes(), before)

    def test_load_rejects_alternate_path_representations(self):
        unsafe = [
            "../take/" + self.relative,
            "artifacts/../" + self.relative,
            "./" + self.relative,
            self.relative.replace("/", "//"),
            "artifacts/./0123456789abcdef.json",
            str(self.artifact),
            "C:/take/artifacts/payload.json",
            "C:artifacts/payload.json",
            "artifacts/C:payload.json",
            "//server/share/artifacts/payload.json",
            "\\\\server\\share\\artifacts\\payload.json",
            "artifacts\\payload.json",
            "artifacts/payload\0.json",
            "artifacts/",
            "artifacts",
            "",
        ]
        for relative in unsafe:
            with self.subTest(relative=relative):
                self.seal(relative)
                with self.assertRaisesRegex(RuntimeError, "artifact path"):
                    replay.load_take(self.take)

    def test_load_rejects_paths_outside_the_artifact_namespace(self):
        for relative in ("transcript.md", "media-manifest.json", "private/raw.json", "prompts/01-A.txt"):
            with self.subTest(relative=relative):
                target = self.take / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.artifact.read_bytes())
                self.seal(relative)
                with self.assertRaisesRegex(RuntimeError, "artifact path"):
                    replay.load_take(self.take)

    def test_load_rejects_file_and_directory_symlinks_inside_or_outside_take(self):
        for outside in (False, True):
            for directory in (False, True):
                with self.subTest(outside=outside, directory=directory):
                    target_root = self.root if outside else self.take
                    target = target_root / f"target-{outside}-{directory}"
                    link = self.take / "artifacts" / f"link-{outside}-{directory}"
                    if directory:
                        target.mkdir()
                        (target / "payload.json").write_bytes(self.artifact.read_bytes())
                        link.symlink_to(target, target_is_directory=True)
                        relative = str(link.relative_to(self.take)) + "/payload.json"
                    else:
                        target.write_bytes(self.artifact.read_bytes())
                        link.symlink_to(target)
                        relative = str(link.relative_to(self.take))
                    self.seal(relative)
                    with self.assertRaisesRegex(RuntimeError, "artifact path"):
                        replay.load_take(self.take)

    def test_load_still_rejects_changed_artifact_event_and_metadata_bytes(self):
        for name, error in ((self.relative, "artifact bytes"), ("events.jsonl", "Recorded events"),
                            ("metadata.json", "Recorded metadata")):
            with self.subTest(name=name):
                self.seal(published=True)
                target = self.take / name
                before = target.read_bytes()
                target.write_bytes(before + b" ")
                with self.assertRaisesRegex(RuntimeError, error):
                    replay.load_take(self.take)
                target.write_bytes(before)

    def test_publication_rejects_reentering_source_path_before_any_output(self):
        payload = self.take / "payload.json"
        payload.write_bytes(self.artifact.read_bytes())
        sentinel = self.output.parent / "take" / "payload.json"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"preserve sibling")
        self.seal("../take/payload.json")
        encoder = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "artifact path"):
            self.publish(encoder)
        encoder.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertEqual(sentinel.read_bytes(), b"preserve sibling")

    def test_publication_copies_only_validated_artifacts_and_preserves_journal_bytes(self):
        self.seal(published=True)
        events = (self.take / "events.jsonl").read_bytes()
        (self.take / "artifacts" / "unobserved.json").write_text("unobserved fixture")
        encoder = self.publish()
        self.assertEqual(encoder.call_count, 2)
        self.assertEqual((self.output / self.relative).read_bytes(), self.artifact.read_bytes())
        self.assertEqual((self.output / "events.jsonl").read_bytes(), events)
        self.assertEqual((self.take / "events.jsonl").read_bytes(), events)
        self.assertFalse((self.output / "artifacts" / "unobserved.json").exists())
        manifest = json.loads((self.output / "media-manifest.json").read_text())
        self.assertEqual(manifest["files"][self.relative], replay.sha(self.artifact))
        self.assertTrue((self.output / "prompts" / "01-A.txt").is_file())

    def test_publication_preserves_nested_canonical_artifact_names(self):
        original = self.artifact.read_bytes()
        self.relative = "artifacts/nested/0123456789abcdef.json"
        self.artifact = self.take / self.relative
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(original)
        self.seal()
        self.publish()
        self.assertEqual((self.output / self.relative).read_bytes(), original)

    def test_publication_rechecks_destination_symlink_containment(self):
        self.seal()
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / self.artifact.name
        sentinel.write_bytes(b"preserve outside")

        def replace_destination(take, video):
            link = self.output / "artifacts"
            if not link.is_symlink():
                link.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(RuntimeError, "artifact path"):
            self.publish(replace_destination)
        self.assertEqual(sentinel.read_bytes(), b"preserve outside")

    def test_publication_rechecks_source_symlink_containment(self):
        self.seal()
        outside = self.root / "outside.json"
        outside.write_bytes(self.artifact.read_bytes())

        def replace_source(take, video):
            if not self.artifact.is_symlink():
                self.artifact.unlink()
                self.artifact.symlink_to(outside)

        with self.assertRaisesRegex(RuntimeError, "artifact path"):
            self.publish(replace_source)
        self.assertFalse((self.output / self.relative).exists())

    def test_both_image_layouts_check_artifact_paths_before_opening_images(self):
        state = {"heading": "Fixture", "body": "Fixture", "status": "Fixture",
                 "image": {"artifact": "../take/" + self.relative}}
        image = mock.Mock()
        image.open.side_effect = AssertionError("Unsafe image reached the decoder")
        pil = types.SimpleNamespace(Image=image, ImageDraw=mock.Mock())
        for renderer in (replay.render_frame, replay.render_cad_frame):
            with self.subTest(renderer=renderer.__name__), \
                    mock.patch.dict(sys.modules, {"PIL": pil}), \
                    mock.patch.object(replay, "ft", return_value=None), \
                    mock.patch.object(replay, "lines_for", return_value=[]):
                replay.IMAGE_CACHE.clear()
                with self.assertRaisesRegex(RuntimeError, "artifact path"):
                    renderer(self.take, state, 1, 1, 600, False)
        image.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
