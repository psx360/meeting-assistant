import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"gpiod": mock.Mock()}):
        spec.loader.exec_module(module)
    return module


class DeviceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = load("oled_dashboard_test", "oled-dashboard.py")
        cls.controller = load("recorder_controller_test", "recorder-controller.py")

    def test_display_line_supports_horizontal_and_vertical_coordinates(self):
        display = self.dashboard.Display.__new__(self.dashboard.Display)
        display.buf = bytearray(1024)
        display.line(2, 3, 5, 3)
        display.line(8, 1, 4)
        self.assertTrue(all(display.buf[(3 // 8) * 128 + x] & (1 << 3) for x in range(2, 6)))
        self.assertTrue(all(display.buf[(y // 8) * 128 + 8] & (1 << (y & 7)) for y in range(1, 5)))

    def test_dismissed_qr_stays_hidden_when_recording_exit_rewrites_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "meeting-display.json"
            dismissed = Path(directory) / "meeting-display-dismissed"
            value = {
                "meeting_id": "meeting-1",
                "join_url": "https://example.test/m/meeting-1",
                "phase": "recording",
                "qr_until": 0,
            }
            state.write_text(json.dumps(value), encoding="utf-8")
            self.controller.DISPLAY_STATE_FILE = str(state)
            self.controller.DISPLAY_DISMISSED_FILE = str(dismissed)
            self.dashboard.MEETING_DISPLAY_FILE = str(state)
            self.dashboard.MEETING_DISPLAY_DISMISSED_FILE = str(dismissed)

            self.assertTrue(self.controller.dismiss_meeting_qr())
            value.update(phase="stopped", qr_until=4_102_444_800)
            state.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(self.dashboard.meeting_display(), {})

            value.update(meeting_id="meeting-2")
            state.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(self.dashboard.meeting_display()["meeting_id"], "meeting-2")


if __name__ == "__main__":
    unittest.main()
