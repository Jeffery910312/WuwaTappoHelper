"""One-shot capture boundary tests; no desktop capture or input is produced."""
from types import SimpleNamespace
import gc
import weakref
import unittest
from unittest.mock import Mock, patch

import numpy as np

from wuwa_helper.window_capture import CaptureError, WindowGrabber


class WindowCaptureTests(unittest.TestCase):
    def setUp(self):
        self.target = SimpleNamespace(hwnd=0x123456789, pid=321, rect=(-10, -6, -6, -3))
        self.pixels = np.full((3, 4, 4), (7, 8, 9, 255), dtype=np.uint8)
        self.validator = Mock(return_value=True)
        self.instances = []
        self.action = lambda capture: capture.frame_handler(self.frame(), Mock())
        self.stop_error = None

    def frame(self, pixels=None, **changes):
        values = dict(frame_buffer=self.pixels if pixels is None else pixels, width=4, height=3)
        values.update(changes)
        return SimpleNamespace(**values)

    def factory(self, **options):
        capture = SimpleNamespace(options=options, capture=object(),
                                  control=SimpleNamespace(stop=Mock(side_effect=self.stop_error)))
        def start():
            self.action(capture)
            return capture.control
        capture.start_free_threaded = start
        self.instances.append(capture)
        return capture

    def grabber(self, **changes):
        options = dict(factory=self.factory, validator=self.validator, timeout=.001)
        options.update(changes)
        return WindowGrabber(**options)

    def assert_stopped(self):
        capture = self.instances[-1]
        capture.control.stop.assert_called_once_with()
        self.assertIsNone(capture.frame_handler)
        self.assertIsNone(capture.closed_handler)
        self.assertIsNone(capture.capture)

    def test_exact_hwnd_uses_only_compatible_capture_options(self):
        image = self.grabber().grab(self.target, self.target.rect)
        self.assertEqual(self.instances[0].options, dict(window_hwnd=0x123456789, cursor_capture=False))
        self.assertEqual((image.mode, image.size), ('RGB', (4, 3)))
        self.assertEqual(self.validator.call_count, 2)
        self.assert_stopped()

    def test_negative_screen_roi_color_order_alpha_and_pixel_ownership(self):
        self.pixels[1, 1] = (99, 88, 77, 0)
        self.pixels[1, 2] = (10, 20, 30, 128)
        image = self.grabber().grab(self.target, (-9, -5, -7, -3))
        self.assertEqual(image.size, (2, 2))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(image.getpixel((1, 0)), (30, 20, 10))
        self.assertEqual(image.getpixel((1, 1)), (9, 8, 7))
        self.pixels[:] = 0
        self.assertEqual(image.getpixel((1, 1)), (9, 8, 7))
        self.assert_stopped()

    def test_each_request_creates_a_fresh_session_and_frame(self):
        grabber = self.grabber()
        first = grabber.grab(self.target, self.target.rect)
        self.pixels[:] = (1, 2, 3, 255)
        second = grabber.grab(self.target, self.target.rect)
        self.assertEqual(first.getpixel((0, 0)), (9, 8, 7))
        self.assertEqual(second.getpixel((0, 0)), (3, 2, 1))
        self.assertEqual(len(self.instances), 2)
        for capture in self.instances:
            capture.control.stop.assert_called_once()

    def test_timeout_never_returns_an_old_image_and_stops(self):
        self.action = lambda capture: None
        with self.assertRaisesRegex(CaptureError, 'Timed out'):
            self.grabber().grab(self.target, self.target.rect)
        self.assert_stopped()

    def test_closed_before_frame_is_failure_and_cannot_be_overwritten(self):
        def close_first(capture):
            capture.closed_handler()
            capture.frame_handler(self.frame(), Mock())
        self.action = close_first
        with self.assertRaisesRegex(CaptureError, 'closed before'):
            self.grabber().grab(self.target, self.target.rect)
        self.assert_stopped()

    def test_normal_stop_close_notification_does_not_replace_success(self):
        def frame_then_close(capture):
            capture.frame_handler(self.frame(), Mock())
            capture.closed_handler()
        self.action = frame_then_close
        image = self.grabber().grab(self.target, self.target.rect)
        self.assertEqual(image.getpixel((0, 0)), (9, 8, 7))
        self.assert_stopped()

    def test_changed_frame_dimensions_or_format_fail_without_fallback(self):
        frames = [self.frame(np.zeros((4, 4, 4), dtype=np.uint8)),
                  self.frame(np.zeros((3, 4, 3), dtype=np.uint8)),
                  self.frame(np.zeros((3, 4, 4), dtype=np.float32)),
                  self.frame(width=5)]
        for frame in frames:
            with self.subTest(shape=frame.frame_buffer.shape, width=frame.width):
                self.action = lambda capture: capture.frame_handler(frame, Mock())
                with self.assertRaisesRegex(CaptureError, 'dimensions'):
                    self.grabber().grab(self.target, self.target.rect)
                self.assert_stopped()

    def test_callback_errors_surface_and_control_stops(self):
        class BrokenFrame:
            @property
            def frame_buffer(self):
                raise OSError('GPU frame failed')
        self.action = lambda capture: capture.frame_handler(BrokenFrame(), Mock())
        with self.assertRaisesRegex(CaptureError, 'GPU frame failed'):
            self.grabber().grab(self.target, self.target.rect)
        self.assert_stopped()

    def test_invalid_hidden_or_minimized_target_does_not_start_a_session(self):
        # False includes a changed identity, hidden or minimized target. Ordinary
        # occlusion is intentionally not checked by the native validator.
        self.validator.return_value = False
        with self.assertRaisesRegex(CaptureError, 'window changed'):
            self.grabber().grab(self.target, self.target.rect)
        self.assertFalse(self.instances)

    def test_target_changing_during_capture_rejects_result_and_stops(self):
        self.validator.side_effect = [True, False]
        with self.assertRaisesRegex(CaptureError, 'window changed'):
            self.grabber().grab(self.target, self.target.rect)
        self.assert_stopped()

    def test_mutated_target_geometry_is_rejected(self):
        def changed(capture):
            capture.frame_handler(self.frame(), Mock())
            self.target.rect = (0, 0, 4, 3)
        self.action = changed
        with self.assertRaisesRegex(CaptureError, 'window changed'):
            self.grabber().grab(self.target, (-10, -6, -6, -3))
        self.assert_stopped()

    def test_outside_or_invalid_roi_does_not_start_capture(self):
        for rect in [(-11, -6, -6, -3), (-10, -6, -5, -3), (-9, -5, -9, -3),
                     (-9.0, -5, -7, -3), (True, 0, 1, 1)]:
            with self.subTest(rect=rect):
                with self.assertRaises(CaptureError):
                    self.grabber().grab(self.target, rect)
        self.assertFalse(self.instances)
        self.validator.assert_not_called()

    def test_missing_dependency_is_explicit(self):
        with patch.dict('sys.modules', {'windows_capture': None}):
            with self.assertRaisesRegex(CaptureError, 'Setup.bat'):
                self.grabber(factory=None).grab(self.target, self.target.rect)

    def test_startup_failure_is_explicit(self):
        factory = Mock(side_effect=OSError('D3D unavailable'))
        with self.assertRaisesRegex(CaptureError, 'D3D unavailable'):
            self.grabber(factory=factory).grab(self.target, self.target.rect)

    def test_stop_failure_is_not_hidden_after_success(self):
        self.stop_error = OSError('stop failed')
        with self.assertRaisesRegex(CaptureError, 'Unable to stop'):
            self.grabber().grab(self.target, self.target.rect)
        self.assert_stopped()

    def test_stop_failure_does_not_mask_timeout(self):
        self.stop_error = OSError('stop failed')
        self.action = lambda capture: None
        with self.assertRaisesRegex(CaptureError, 'Timed out'):
            self.grabber().grab(self.target, self.target.rect)
        self.assert_stopped()

    def test_native_callback_cycle_is_released_without_waiting_for_gc(self):
        references = []
        frame = self.frame()
        class CaptureWithNativeCycle:
            def __init__(inner, **options):
                # The native PyO3 config holds these bound methods but is not
                # traversed by Python GC. Emulate the ownership cycle here.
                inner.capture = SimpleNamespace(frame=inner.on_frame, closed=inner.on_closed)
                references.append(weakref.ref(inner))
            def on_frame(inner):
                pass
            def on_closed(inner):
                pass
            def start_free_threaded(inner):
                inner.frame_handler(frame, None)
                def stop():
                    # Detaching callbacks or the native config before joining
                    # the capture thread would break in-flight callbacks.
                    self.assertIsNotNone(inner.capture)
                    self.assertIsNotNone(inner.frame_handler)
                    self.assertIsNotNone(inner.closed_handler)
                return SimpleNamespace(stop=stop)
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            grabber = self.grabber(factory=CaptureWithNativeCycle)
            for _ in range(5):
                image = grabber.grab(self.target, self.target.rect)
                self.assertEqual(image.getpixel((0, 0)), (9, 8, 7))
                self.assertIsNone(references[-1]())
        finally:
            if was_enabled:
                gc.enable()

    def test_native_configuration_is_released_when_start_fails(self):
        def startup_failure(capture):
            raise OSError('session could not start')
        self.action = startup_failure
        with self.assertRaisesRegex(CaptureError, 'session could not start'):
            self.grabber().grab(self.target, self.target.rect)
        capture = self.instances[-1]
        capture.control.stop.assert_not_called()
        self.assertIsNone(capture.frame_handler)
        self.assertIsNone(capture.closed_handler)
        self.assertIsNone(capture.capture)

    def test_invalid_timeout_is_rejected(self):
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    self.grabber(timeout=timeout)


if __name__ == '__main__':
    unittest.main()
