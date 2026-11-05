"""Unit fixtures for timing math only; never used as recorded demonstration data."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_replay import ReplayTimeline, caption, message, states, subtitles, teaching_timeline


def event(time, kind, **fields):
    return {"t": time, "kind": kind, **fields}


def phase(time, scene):
    return event(time, "phase_started", scene=scene, argv=["codex", "exec", "--json", "-"])


def text(time, value):
    return event(time, "cli_output", event={"type": "item.completed",
                 "item": {"type": "agent_message", "text": value}})


class ReplayTimelineTests(unittest.TestCase):
    def setUp(self):
        self.events = [event(0, "capture_started"), phase(300, "A"),
                       event(301, "operator_input", scene="A", text="Prompt A"),
                       event(302, "subprocess_started", scene="A"), text(310, "Real-state fixture"),
                       event(320, "artifact_observed", scene="A", artifact="artifacts/example.png", label="Fixture image"),
                       event(328, "phase_finished", scene="A", returncode=0), phase(900, "B"),
                       event(901, "operator_input", scene="B", text="Prompt B"),
                       event(950, "phase_finished", scene="B", returncode=0),
                       event(1000, "capture_finished")]

    def test_full_replay_is_identity(self):
        timeline = ReplayTimeline([(0, 1000)], 1000)
        for value in (0, 301.25, 400, 900, 1000):
            self.assertEqual(timeline.source_at(value), value)
            self.assertEqual(timeline.video_at(value), value)

    def test_idle_is_trimmed_without_moving_source_actions(self):
        timeline = teaching_timeline(self.events, 1000)
        self.assertEqual(timeline.source_at(0), 300)
        self.assertEqual(timeline.duration, 75)
        self.assertEqual(timeline.video_at(500), timeline.video_at(900))
        self.assertEqual(timeline.source_at(timeline.video_at(900)), 900)
        for item in self.events:
            if item["kind"] not in {"capture_started", "capture_finished"}:
                self.assertAlmostEqual(timeline.source_at(timeline.video_at(item["t"])), item["t"])
        # Each caption/action is still a real boundary; only unchanged tails go.
        for item in self.events:
            if caption(item) and item["kind"] != "capture_finished":
                self.assertFalse(any(row["source_start_seconds"] < item["t"] < row["source_end_seconds"]
                                     for row in timeline.omitted))

    def test_short_source_is_not_padded(self):
        timeline = teaching_timeline([event(0, "capture_started"), phase(5, "A"),
                                      text(6, "One short action"), event(9, "phase_finished", scene="A", returncode=0),
                                      event(10, "capture_finished")], 10)
        self.assertEqual(timeline.duration, 5)
        self.assertEqual(timeline.scale, 1)
        with self.assertRaises(ValueError):
            ReplayTimeline([(0, 1)], 1, scale=2)

    def test_long_activity_compresses_retained_intervals_to_target(self):
        events = [event(0, "capture_started"), phase(100, "A")]
        events += [text(120 + 20*index, f"State {index}") for index in range(70)]
        events += [event(1520, "phase_finished", scene="A", returncode=0), event(1530, "capture_finished")]
        timeline = teaching_timeline(events, 1530)
        self.assertAlmostEqual(timeline.duration, 420)
        self.assertEqual(timeline.active_hold_cap, 10)
        previous_end = 0
        for row in timeline.segments:
            self.assertGreaterEqual(row["source_start_seconds"], previous_end)
            self.assertLessEqual(row["video_end_seconds"]-row["video_start_seconds"],
                                 row["source_end_seconds"]-row["source_start_seconds"])
            previous_end = row["source_end_seconds"]
        for item in events[1:-1]:
            self.assertAlmostEqual(timeline.source_at(timeline.video_at(item["t"])), item["t"])

    def test_caption_timing_uses_the_same_map(self):
        timeline = teaching_timeline(self.events, 1000)
        result = subtitles(self.events, timeline)
        self.assertIn("00:00:20,000", result)  # Actual observed image at source t=320.
        self.assertIn("00:00:36,000", result)  # Phase B follows the trimmed idle gap.
        self.assertNotIn("00:15:00,000", result)

    def test_actual_response_remains_visible_after_cleanup_warning(self):
        warning = event(3, "cli_output", channel="stderr", line="Fixture cleanup warning")
        events = [phase(1, "A"), text(2, "Actual fixture response"), warning,
                  event(4, "phase_finished", scene="A", returncode=0)]
        self.assertEqual(states(events)[-1][1]["body"], "Actual fixture response")
        self.assertEqual(message(warning, complete=True)[1], "Fixture cleanup warning")
        events[-1]["returncode"] = 1
        self.assertEqual(states(events)[-1][1]["body"], "Fixture cleanup warning")


if __name__ == "__main__":
    unittest.main()
