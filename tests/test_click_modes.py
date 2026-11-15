"""Exercise mode selection and dispatch without producing desktop input."""
import json
from pathlib import Path
import queue
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wuwa_helper.detector import Match
from wuwa_helper.main import Engine


class ClickModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'runtime.json'
        self.target = SimpleNamespace(hwnd=100, pid=200, rect=(-2000, 0, 0, 1200))
        self.api = SimpleNamespace(
            ensure_input_access=Mock(),
            owns_point=Mock(return_value=True),
            idle_seconds=Mock(return_value=5),
            held_keys=Mock(return_value=False),
            click=Mock(return_value=False),
            click_background=Mock(return_value=False),
            targets=Mock(return_value=[self.target]),
        )
        self.monitor = SimpleNamespace(
            running=True,
            error=None,
            snapshot=Mock(return_value={'total': 0, 'keyboard': 0, 'mouse': 0}),
        )

    def make_engine(self):
        return Engine(queue.Queue(), detector=Mock(), api=self.api,
                      grab=Mock(), reader=Mock(), monitor=self.monitor,
                      state_path=self.path)

    def ready_engine(self, mode='foreground'):
        engine = self.make_engine()
        engine.click_mode = mode
        engine.active.set()
        engine.game_count = 2500
        engine.count_base = 2500
        engine.counter_read_at = time.monotonic()
        engine.observe_counter = Mock()
        chest = Match(200, 200, 48, 44, .99)
        engine.find_chests = Mock(return_value=[chest])
        wait = patch.object(engine.closed, 'wait', return_value=False)
        wait.start()
        self.addCleanup(wait.stop)
        return engine, chest

    def test_fresh_install_keeps_foreground_default(self):
        self.assertEqual(self.make_engine().click_mode, 'foreground')

    def test_old_state_keeps_settings_and_claim_progress(self):
        cooling = time.time() + 120
        saved = dict(key='F22', boxes=2, rate=3, sent=125,
                     auto_keys=False, auto_claim=True, round_count=1,
                     round_deadline=time.time() + 30, last_claim=time.time(),
                     claim_pending=True, next_scan=cooling)
        self.path.write_text(json.dumps(saved), encoding='utf-8')
        engine = self.make_engine()
        self.assertEqual(engine.click_mode, 'foreground')
        self.assertEqual((engine.key, engine.boxes, engine.rate, engine.sent),
                         ('F22', 2, 3, 125))
        self.assertFalse(engine.auto_keys)
        self.assertTrue(engine.auto_claim)
        self.assertEqual(engine.round_count, 1)
        self.assertTrue(engine.claim_pending)
        self.assertEqual(engine.next_scan, cooling)
        self.assertFalse(engine.active.is_set())

    def test_background_selection_survives_restart(self):
        engine = self.make_engine()
        engine.request_settings('F22', 1, 4, 'background')
        engine.apply_commands()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['click_mode'],
                         'background')
        reloaded = self.make_engine()
        self.assertEqual((reloaded.click_mode, reloaded.key, reloaded.boxes, reloaded.rate),
                         ('background', 'F22', 1, 4))
        self.assertFalse(reloaded.active.is_set())

    def test_invalid_saved_mode_falls_back_without_losing_progress(self):
        cooling = time.time() + 120
        self.path.write_text(json.dumps(dict(click_mode='unsupported', key='F20',
                            boxes=2, rate=6, round_count=1, next_scan=cooling,
                            claim_pending=True)), encoding='utf-8')
        engine = self.make_engine()
        self.assertEqual(engine.click_mode, 'foreground')
        self.assertEqual((engine.key, engine.rate, engine.round_count), ('F20', 6, 1))
        self.assertEqual(engine.next_scan, cooling)
        self.assertTrue(engine.claim_pending)

    def test_invalid_mode_request_does_not_pause_or_queue_changes(self):
        engine = self.make_engine()
        engine.active.set()
        revision = engine._settings_revision
        for invalid in ('unsupported', '', 7, [], True):
            with self.subTest(mode=invalid):
                with self.assertRaises(ValueError):
                    engine.request_settings('F22', 1, 4, invalid)
                self.assertTrue(engine.active.is_set())
                self.assertTrue(engine.commands.empty())
                self.assertEqual(engine._settings_revision, revision)
                self.assertEqual((engine.click_mode, engine.key, engine.boxes, engine.rate),
                                 ('foreground', 'F13', 2, 5))
        self.api.click.assert_not_called()
        self.api.click_background.assert_not_called()

    def test_omitting_mode_retains_current_selection(self):
        engine = self.make_engine()
        engine.click_mode = 'background'
        engine.request_settings('F20', 2, 7)
        engine.apply_commands()
        self.assertEqual(engine.click_mode, 'background')

    def test_mode_switch_keeps_running_and_preserves_claim_state(self):
        engine = self.make_engine()
        engine.active.set()
        engine.auto_keys = False
        engine.auto_claim = True
        engine.count_base = 2345
        engine.sent = 125
        self.monitor.snapshot.return_value = {'total': 10, 'keyboard': 8, 'mouse': 2}
        engine.round_count = 1
        engine.round_deadline = time.time() + 30
        engine.last_claim = time.time()
        engine.next_scan = time.time() + 300
        engine.claim_pending = True
        engine.next_read = time.monotonic() + 25
        next_read = engine.next_read
        previous = (engine.round_count, engine.round_deadline, engine.last_claim,
                    engine.next_scan, engine.claim_pending, engine.auto_keys, engine.auto_claim)
        engine.request_settings(engine.key, engine.boxes, engine.rate, 'background')
        self.assertTrue(engine.active.is_set())
        engine.apply_commands()
        self.assertTrue(engine.active.is_set())
        self.assertEqual(engine.click_mode, 'background')
        self.assertEqual(engine.next_read, next_read)
        self.assertEqual(engine.next_check, 0)
        self.assertEqual(engine.estimated_count, 2355)
        self.assertEqual(engine.sent, 125)
        self.assertEqual((engine.round_count, engine.round_deadline, engine.last_claim,
                          engine.next_scan, engine.claim_pending, engine.auto_keys,
                          engine.auto_claim), previous)

    def test_foreground_uses_original_click_backend(self):
        engine, chest = self.ready_engine()
        self.assertFalse(engine.claim_step(self.target))
        self.api.click.assert_called_once()
        self.api.click_background.assert_not_called()
        target, x, y, allowed = self.api.click.call_args.args
        self.assertIs(target, self.target)
        self.assertEqual((x, y), (-2000 + chest.center[0], chest.center[1]))
        self.assertTrue(allowed())

    def test_foreground_still_waits_for_user_idle(self):
        engine, _ = self.ready_engine()
        self.api.idle_seconds.return_value = 0
        self.assertTrue(engine.claim_step(self.target))
        self.api.click.assert_not_called()
        self.api.click_background.assert_not_called()

    def test_background_dispatches_while_user_is_active(self):
        engine, _ = self.ready_engine('background')
        self.api.idle_seconds.return_value = 0
        self.api.held_keys.return_value = True
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_called_once()
        self.api.click.assert_not_called()
        self.api.idle_seconds.assert_not_called()
        self.api.held_keys.assert_not_called()
        self.assertEqual(engine.count, 0)
        self.assertFalse(engine.claim_pending)
        self.assertEqual(engine.next_scan, 0)

    def test_background_still_requires_chest_to_be_exposed(self):
        engine, _ = self.ready_engine('background')
        self.api.owns_point.return_value = False
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_not_called()
        self.api.click.assert_not_called()

    def test_background_still_requires_estimated_counter_threshold(self):
        engine, _ = self.ready_engine('background')
        engine.game_count = 100
        engine.count_base = 100
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_not_called()
        self.api.click.assert_not_called()

    def test_background_permission_denial_never_calls_either_backend(self):
        engine, _ = self.ready_engine('background')
        self.api.ensure_input_access.side_effect = RuntimeError('權限不足')
        self.assertFalse(engine.claim_step(self.target))
        self.assertTrue(engine.claim_blocked)
        self.api.click_background.assert_not_called()
        self.api.click.assert_not_called()

    def test_background_error_does_not_fall_back_or_record_a_claim(self):
        engine, _ = self.ready_engine('background')
        self.api.click_background.side_effect = RuntimeError('背景點擊失敗')
        with self.assertRaisesRegex(RuntimeError, '背景點擊失敗'):
            engine.claim_step(self.target)
        self.api.click.assert_not_called()
        self.assertEqual(engine.count, 0)
        self.assertEqual(engine.round_count, 0)
        self.assertTrue(engine.claim_pending)
        self.assertGreater(engine.next_scan, time.time())
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertTrue(saved['claim_pending'])
        self.assertEqual(saved['next_scan'], engine.next_scan)
        self.assertFalse(engine.in_flight)

    def test_background_pending_marker_is_persisted_before_any_input(self):
        engine, _ = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertTrue(saved['claim_pending'])
            self.assertGreater(saved['next_scan'], time.time())
            self.assertEqual(saved['round_count'], 0)
            return False

        self.api.click_background.side_effect = attempt
        self.assertFalse(engine.claim_step(self.target))
        self.assertFalse(engine.claim_pending)
        self.assertEqual(engine.next_scan, 0)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertFalse(saved['claim_pending'])
        self.assertEqual(saved['next_scan'], 0)

    def test_background_save_failure_prevents_input(self):
        engine, _ = self.ready_engine('background')
        with patch.object(engine, 'save', side_effect=PermissionError('state is locked')):
            with self.assertRaises(PermissionError):
                engine.claim_step(self.target)
        self.api.click_background.assert_not_called()
        self.api.click.assert_not_called()
        self.assertEqual(engine.count, 0)
        self.assertEqual(engine.round_count, 0)
        self.assertFalse(engine.in_flight)

    def test_background_false_restores_previous_pending_markers(self):
        engine, _ = self.ready_engine('background')
        engine.next_scan = time.time() - 10
        engine.claim_pending = True
        previous = engine.next_scan, engine.claim_pending
        self.assertFalse(engine.claim_step(self.target))
        self.assertEqual((engine.next_scan, engine.claim_pending), previous)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual((saved['next_scan'], saved['claim_pending']), previous)
        self.api.click.assert_not_called()

    def test_background_message_delivery_alone_is_not_a_claim(self):
        engine, _ = self.ready_engine('background')
        self.api.click_background.return_value = True
        self.assertTrue(engine.claim_step(self.target))
        self.assertEqual(engine.count, 0)
        self.assertEqual(engine.round_count, 0)
        self.assertTrue(engine.claim_blocked)
        self.assertTrue(engine.claim_pending)
        self.assertGreater(engine.next_scan, time.time())
        self.api.click.assert_not_called()

    def test_background_disappearance_confirms_without_fresh_ocr(self):
        engine, chest = self.ready_engine('background')
        self.api.click_background.return_value = True
        engine.find_chests.side_effect = [[chest], [chest], []]
        self.assertTrue(engine.claim_step(self.target))
        self.assertEqual((engine.count, engine.round_count), (1, 1))
        self.assertFalse(engine.claim_pending)
        self.assertFalse(engine.claim_blocked)
        self.assertEqual(engine.estimated_count, 1500)
        engine.observe_counter.assert_called_once_with(self.target)
        self.api.click.assert_not_called()

    def test_foreground_retains_existing_visual_confirmation(self):
        engine, chest = self.ready_engine()
        self.api.click.return_value = True
        engine.find_chests.side_effect = [[chest], [chest], []]
        self.assertTrue(engine.claim_step(self.target))
        self.assertEqual((engine.count, engine.round_count), (1, 1))
        self.assertFalse(engine.claim_pending)
        self.assertEqual(engine.estimated_count, 1500)
        self.api.click_background.assert_not_called()

    def test_input_count_drives_eligibility_before_first_ocr_success(self):
        engine, _ = self.ready_engine('background')
        engine.game_count = None
        engine.count_base = 0
        self.monitor.snapshot.return_value = {'total': 2000, 'keyboard': 1800, 'mouse': 200}
        self.assertEqual(engine.estimated_count, 2000)
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_called_once()
        self.api.click.assert_not_called()
        engine.observe_counter.assert_called_once_with(self.target)

    def test_successful_periodic_ocr_reanchors_input_estimate(self):
        engine = self.make_engine()
        self.assertEqual(engine.estimated_count, 0)
        self.monitor.snapshot.return_value = {'total': 250, 'keyboard': 200, 'mouse': 50}
        engine.counter_capture = Mock(return_value=SimpleNamespace(value=7304))
        engine.observe_counter(self.target, force=True)
        self.assertEqual((engine.game_count, engine.estimated_count), (7304, 7304))
        self.monitor.snapshot.return_value = {'total': 275, 'keyboard': 225, 'mouse': 50}
        self.assertEqual(engine.estimated_count, 7329)
        engine.counter_capture.return_value = SimpleNamespace(value=7200)
        engine.observe_counter(self.target, force=True)
        self.assertEqual((engine.game_count, engine.estimated_count), (7200, 7200))
        self.monitor.snapshot.return_value = {'total': 280, 'keyboard': 230, 'mouse': 50}
        self.assertEqual(engine.estimated_count, 7205)

    def test_ocr_failure_preserves_last_calibration_and_new_inputs(self):
        engine = self.make_engine()
        engine.counter_capture = Mock(return_value=SimpleNamespace(value=2300))
        engine.observe_counter(self.target, force=True)
        self.monitor.snapshot.return_value = {'total': 50, 'keyboard': 45, 'mouse': 5}
        engine.counter_capture.return_value = None
        engine.observe_counter(self.target, force=True)
        self.assertEqual((engine.game_count, engine.estimated_count), (2300, 2350))
        self.assertEqual(engine.read_failures, 1)
        self.monitor.snapshot.return_value = {'total': 70, 'keyboard': 65, 'mouse': 5}
        engine.counter_capture.side_effect = OSError('OCR temporarily unavailable')
        engine.observe_counter(self.target, force=True)
        self.assertEqual((engine.game_count, engine.estimated_count), (2300, 2370))
        self.assertTrue(engine.count_available)

    def test_confirmed_claim_debits_once_and_retains_concurrent_inputs(self):
        engine, chest = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            self.monitor.snapshot.return_value = {'total': 11, 'keyboard': 10, 'mouse': 1}
            return True

        def find(target, verification=False):
            if verification:
                self.monitor.snapshot.return_value = {'total': 19, 'keyboard': 15, 'mouse': 4}
                return []
            return [chest]

        self.api.click_background.side_effect = attempt
        engine.find_chests.side_effect = find
        self.assertTrue(engine.claim_step(self.target))
        self.assertEqual(engine.estimated_count, 1519)
        self.assertEqual((engine.count, engine.round_count), (1, 1))
        engine.step(send_keys=False)
        self.api.click_background.assert_called_once()
        self.assertEqual(engine.estimated_count, 1519)
        self.monitor.snapshot.return_value = {'total': 24, 'keyboard': 20, 'mouse': 4}
        self.assertEqual(engine.estimated_count, 1524)

    def test_unconfirmed_claim_does_not_debit_estimate(self):
        engine, _ = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            self.monitor.snapshot.return_value = {'total': 11, 'keyboard': 10, 'mouse': 1}
            return True

        self.api.click_background.side_effect = attempt
        self.assertTrue(engine.claim_step(self.target))
        self.assertEqual(engine.estimated_count, 2511)
        self.assertEqual((engine.count, engine.round_count), (0, 0))
        self.assertTrue(engine.claim_pending)

    def test_stopped_input_monitor_does_not_authorize_frozen_estimate(self):
        engine, _ = self.ready_engine('background')
        self.monitor.running = False
        engine.counter_read_at = time.monotonic() - 6
        self.monitor.snapshot.return_value = {'total': 10000, 'keyboard': 9000, 'mouse': 1000}
        self.assertFalse(engine.count_available)
        self.assertFalse(engine.claim_step(self.target))
        self.assertEqual(engine.estimated_count, 2500)
        self.api.click_background.assert_not_called()
        self.api.click.assert_not_called()

    def test_failed_input_monitor_can_use_fresh_ocr_calibration(self):
        engine, _ = self.ready_engine('background')
        self.monitor.error = OSError('hook failed')
        self.assertTrue(engine.count_available)
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_called_once()
        self.assertEqual(engine.estimated_count, 2500)

    def test_failed_ocr_disables_fallback_when_input_monitor_is_unhealthy(self):
        engine, _ = self.ready_engine('background')
        self.monitor.error = OSError('hook failed')
        engine.read_failures = 1
        self.assertFalse(engine.count_available)
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_not_called()
        self.api.click.assert_not_called()

    def test_settings_request_cancels_an_in_flight_callback(self):
        engine, _ = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            self.assertTrue(allowed())
            engine.request_settings(engine.key, engine.boxes, engine.rate, 'foreground')
            self.assertFalse(allowed())
            return False

        self.api.click_background.side_effect = attempt
        self.assertFalse(engine.claim_step(self.target))
        self.assertTrue(engine.active.is_set())
        self.api.click.assert_not_called()
        self.assertFalse(engine.claim_pending)

    def test_background_pause_after_down_keeps_pending_claim(self):
        engine, _ = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            self.assertTrue(allowed())
            engine.pause()
            self.assertFalse(allowed())
            # The backend queued down and cleaned up up, so this is an attempt.
            return True

        self.api.click_background.side_effect = attempt
        self.assertTrue(engine.claim_step(self.target))
        self.assertFalse(engine.active.is_set())
        self.assertTrue(engine.claim_pending)
        self.assertGreater(engine.next_scan, time.time())
        self.assertEqual(engine.count, 0)
        self.api.click.assert_not_called()

    def test_callback_rejects_changed_mode_while_engine_stays_running(self):
        engine, _ = self.ready_engine('foreground')

        def attempt(target, x, y, allowed):
            engine.request_settings(engine.key, engine.boxes, engine.rate, 'background')
            engine.apply_commands()
            self.assertTrue(engine.active.is_set())
            self.assertEqual(engine.click_mode, 'background')
            self.assertFalse(allowed())
            return False

        self.api.click.side_effect = attempt
        self.assertFalse(engine.claim_step(self.target))
        self.api.click_background.assert_not_called()
        self.assertFalse(engine.claim_pending)

    def test_same_mode_settings_request_cancels_old_click_before_applying(self):
        engine, _ = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            revision = engine._settings_revision
            engine.request_settings('F22', engine.boxes, 7, 'background')
            self.assertEqual(engine._settings_revision, revision + 1)
            self.assertEqual(engine.click_mode, 'background')
            self.assertTrue(engine.active.is_set())
            self.assertFalse(allowed())
            return False

        self.api.click_background.side_effect = attempt
        self.assertFalse(engine.claim_step(self.target))
        self.assertFalse(engine.claim_pending)
        self.assertTrue(engine.active.is_set())
        self.api.click.assert_not_called()

    def test_queued_settings_block_new_claim_until_fields_are_applied(self):
        engine, _ = self.ready_engine('foreground')
        engine.request_settings('F22', engine.boxes, engine.rate, 'background')
        self.assertFalse(engine.claim_step(self.target))
        self.api.click.assert_not_called()
        self.api.click_background.assert_not_called()
        self.assertTrue(engine.active.is_set())
        engine.apply_commands()
        self.assertFalse(engine.claim_step(self.target))
        self.assertEqual(engine.click_mode, 'background')
        self.api.click_background.assert_called_once()
        self.api.click.assert_not_called()

    def test_switching_away_and_back_does_not_revive_old_callback(self):
        engine, _ = self.ready_engine('background')

        def attempt(target, x, y, allowed):
            revision = engine._settings_revision
            engine.request_settings(engine.key, engine.boxes, engine.rate, 'foreground')
            engine.request_settings(engine.key, engine.boxes, engine.rate, 'background')
            engine.apply_commands()
            self.assertEqual(engine._settings_revision, revision + 2)
            self.assertEqual(engine.click_mode, 'background')
            self.assertTrue(engine.active.is_set())
            self.assertFalse(allowed())
            return False

        self.api.click_background.side_effect = attempt
        self.assertFalse(engine.claim_step(self.target))
        self.assertFalse(engine.claim_pending)
        self.api.click.assert_not_called()

    def test_auto_settings_preserve_short_test_and_use_new_key_on_next_pulse(self):
        engine = self.make_engine()
        engine.active.set()
        engine.test_remaining, engine.test_sent = 13, 7
        engine.auto_keys, engine.auto_claim = False, True
        self.api.tap_key = Mock(return_value=True)
        engine.request_settings('F20', 2, 7, 'background')
        self.assertTrue(engine.active.is_set())
        self.assertEqual((engine.test_remaining, engine.test_sent), (13, 7))
        engine.apply_commands()
        self.assertTrue(engine.active.is_set())
        self.assertEqual((engine.test_remaining, engine.test_sent), (13, 7))
        self.assertEqual((engine.auto_keys, engine.auto_claim), (False, True))
        self.assertEqual((engine.key, engine.rate, engine.click_mode), ('F20', 7, 'background'))
        engine.short_test_step()
        self.assertEqual((engine.test_remaining, engine.test_sent), (12, 8))
        self.assertEqual(self.api.tap_key.call_args.args[0], 'F20')

    def test_auto_settings_keep_paused_engine_paused(self):
        engine = self.make_engine()
        engine.request_settings('F22', 1, 4, 'background')
        self.assertFalse(engine.active.is_set())
        engine.apply_commands()
        self.assertFalse(engine.active.is_set())
        self.assertEqual((engine.key, engine.boxes, engine.rate, engine.click_mode),
                         ('F22', 1, 4, 'background'))


if __name__ == '__main__':
    unittest.main()
