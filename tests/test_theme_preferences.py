"""Appearance preferences round-trip without changing automation state."""
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from wuwa_helper.main import Engine


class ThemePreferencesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'runtime.json'
        self.api = SimpleNamespace(click=Mock(), click_background=Mock(), tap_key=Mock())

    def engine(self):
        return Engine(queue.Queue(), api=self.api, detector=Mock(), reader=Mock(),
                      grab=Mock(), state_path=self.path,
                      monitor=SimpleNamespace(snapshot=lambda: dict(total=0, keyboard=0, mouse=0)))

    def test_old_or_invalid_appearance_preserves_existing_settings(self):
        for value in (None, 'invalid', [], {}, 42):
            with self.subTest(theme=value):
                self.path.write_text(json.dumps(dict(theme=value, key='F22', boxes=2,
                                                     rate=1, auto_keys=False)), encoding='utf-8')
                engine = self.engine()
                self.assertEqual(engine.theme, 'light')
                self.assertEqual((engine.key, engine.rate, engine.auto_keys), ('F22', 1, False))

    def test_switching_themes_persists_without_changing_automation(self):
        engine = self.engine()
        engine.active.set()
        engine.count_base, engine.round_count = 4500, 1
        engine.next_scan, engine.claim_pending = 100, True
        revision = engine._settings_revision
        engine.request_theme('dark')
        self.assertTrue(engine.active.is_set())
        self.assertEqual(engine.theme, 'dark')
        engine.apply_commands()
        self.assertEqual(engine._settings_revision, revision)
        self.assertEqual((engine.count_base, engine.round_count, engine.next_scan,
                          engine.claim_pending), (4500, 1, 100, True))
        self.assertEqual(self.engine().theme, 'dark')
        engine.request_settings(engine.key, engine.boxes, engine.rate, 'background')
        engine.apply_commands()
        self.assertEqual(self.engine().theme, 'dark')
        engine.request_theme('light')
        engine.apply_commands()
        self.assertEqual(self.engine().theme, 'light')
        self.api.click.assert_not_called()
        self.api.click_background.assert_not_called()
        self.api.tap_key.assert_not_called()

    def test_invalid_request_changes_nothing(self):
        engine = self.engine()
        for value in (None, 'invalid', [], 42):
            with self.subTest(theme=value):
                with self.assertRaises(ValueError):
                    engine.request_theme(value)
                self.assertEqual(engine.theme, 'light')
                self.assertTrue(engine.commands.empty())
                self.assertFalse(engine.active.is_set())

    def test_language_migration_and_validation_preserve_theme(self):
        for value in (None, 'fr', [], 42):
            with self.subTest(language=value):
                self.path.write_text(json.dumps(dict(language=value, theme='dark', key='F22')),
                                     encoding='utf-8')
                engine = self.engine()
                self.assertEqual((engine.language, engine.theme, engine.key), ('zh-TW', 'dark', 'F22'))
                with self.assertRaises(ValueError):
                    engine.request_language(value)
                self.assertTrue(engine.commands.empty())

    def test_language_and_theme_round_trip_together_without_resetting_progress(self):
        engine = self.engine()
        engine.active.set()
        engine.count_base, engine.round_count = 3456, 1
        revision = engine._settings_revision
        engine.request_language('en')
        engine.request_theme('dark')
        engine.apply_commands()
        self.assertTrue(engine.active.is_set())
        self.assertEqual((engine.count_base, engine.round_count, engine._settings_revision),
                         (3456, 1, revision))
        self.assertEqual((self.engine().language, self.engine().theme), ('en', 'dark'))
        engine.request_language('zh-TW')
        engine.apply_commands()
        self.assertEqual((self.engine().language, self.engine().theme), ('zh-TW', 'dark'))
        self.api.click.assert_not_called()
        self.api.click_background.assert_not_called()


if __name__ == '__main__':
    unittest.main()
