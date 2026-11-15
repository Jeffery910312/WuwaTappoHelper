"""Keep input accounting aligned to capture, before potentially slow OCR."""
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wuwa_helper.main import Engine
from wuwa_helper.counter_reader import CounterReading


class CalibrationTimingTests(unittest.TestCase):
    def test_capture_wait_and_ocr_inputs_are_not_counted_twice(self):
        total = [100]
        monitor = SimpleNamespace(running=True, error=None,
            snapshot=lambda: dict(total=total[0], keyboard=total[0], mouse=0))
        def grab(rect):
            total[0] = 110
            return object()
        def read(image):
            total[0] = 130
            return CounterReading(1000, (40, 40, 148, 66))
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(queue.Queue(), api=Mock(), detector=Mock(),
                            reader=SimpleNamespace(read=read), grab=grab,
                            monitor=monitor, state_path=Path(directory) / 'runtime.json')
            target = SimpleNamespace(rect=(0, 0, 400, 300))
            with patch('wuwa_helper.main.time.monotonic', side_effect=[100.0, 101.0]):
                engine.observe_counter(target, force=True)
            self.assertEqual(engine.counter_inputs, 110)
            self.assertEqual(engine.game_count, 1000)
            self.assertEqual(engine.estimated_count, 1020)
            self.assertEqual(engine.counter_read_at, 101.0)


if __name__ == '__main__':
    unittest.main()
