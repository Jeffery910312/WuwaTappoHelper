"""Language switches keep UI placeholders and operational diagnostics intact."""
import string
import unittest

from wuwa_helper import i18n


class TranslationTests(unittest.TestCase):
    def test_ui_languages_have_matching_keys_and_format_fields(self):
        self.assertEqual(i18n.LANGUAGES, ('zh-TW', 'en'))
        self.assertEqual(set(i18n._TEXT['zh-TW']), set(i18n._TEXT['en']))
        formatter = string.Formatter()
        for key in i18n._TEXT['zh-TW']:
            fields = lambda text: {field for _, field, _, _ in formatter.parse(text) if field}
            with self.subTest(key=key):
                self.assertEqual(fields(i18n._TEXT['zh-TW'][key]), fields(i18n._TEXT['en'][key]))

    def test_visible_numbers_and_timers_format_in_both_languages(self):
        self.assertEqual(i18n.tr('zh-TW', 'goal', count=2000), '領箱門檻 2,000 次')
        self.assertEqual(i18n.tr('en', 'goal', count=2000), 'Claim threshold 2,000')
        self.assertEqual(i18n.tr('en', 'cooldown', timer='29:36'), 'Cooldown 29:36')
        self.assertEqual(i18n.tr('en', 'status_test', sent=3, total=20), 'Test 3 / 20')
        self.assertEqual(i18n.tr('en', 'confirmed', count=2), 'Chests confirmed: 2')

    def test_switching_language_does_not_change_persisted_mode_values(self):
        self.assertEqual(i18n.tr('zh-TW', 'foreground'), '前台')
        self.assertEqual(i18n.tr('en', 'foreground'), 'Foreground')
        self.assertEqual(i18n.tr('en', 'background'), 'Background')
        self.assertEqual(i18n.tr('zh-TW', 'app_title'), 'WuwaTappo 助手')
        self.assertEqual(i18n.tr('en', 'app_title'), 'WuwaTappo Helper')
        self.assertEqual(i18n.tr('zh-TW', 'click_info'), '前台模式會搶滑鼠，背景模式異常時再切換')
        self.assertIn('background mode is not working', i18n.tr('en', 'click_info'))

    def test_old_locale_unknown_key_and_missing_format_values_do_not_crash(self):
        self.assertEqual(i18n.tr('unknown', 'settings'), '設定')
        self.assertEqual(i18n.tr('en', 'future_key'), 'future_key')
        self.assertEqual(i18n.tr('en', 'goal'), 'Claim threshold {count:,}')

    def test_chinese_operation_message_stays_exactly_as_logged(self):
        text = '已送出背景寶箱點擊 (-1870, 584)，正在確認畫面。'
        self.assertEqual(i18n.translate_message('zh-TW', text), text)

    def test_nested_permission_error_keeps_actionable_uac_instructions(self):
        text = ('Wuwa Tappo 的權限高於助手，寶箱點擊會被 Windows 阻擋，自動領箱已暫停。'
                '請關閉助手，再執行 Start.bat 並允許管理員權限。 自動按鍵依開關持續。')
        translated = i18n.translate_message('en', '已暫停：' + text)
        self.assertTrue(translated.startswith('Paused: Wuwa Tappo has higher privileges'))
        self.assertIn('Windows blocks chest clicks', translated)
        self.assertIn('Start.bat', translated)
        self.assertIn('administrator access', translated)
        self.assertTrue(translated.endswith('Auto keypress follows its switch.'))

    def test_background_error_code_and_negative_screen_coordinates_are_preserved(self):
        text = 'Windows 拒絕背景滑鼠訊息（錯誤 0x80070005），自動領箱已暫停。'
        translated = i18n.translate_message('en', text)
        self.assertIn('0x80070005', translated)
        self.assertIn('auto claim is paused', translated)
        self.assertEqual(i18n.translate_message('en', '已送出背景寶箱點擊 (-1870, -84)，正在確認畫面。'),
                         'Background chest click sent to (-1870, -84). Verifying the screen.')

    def test_dynamic_settings_and_feature_states_are_translated(self):
        translated = i18n.translate_message('en', '設定已自動儲存：F22，2 個寶箱，每秒最多 10 次，背景點擊。')
        self.assertIn('F22', translated)
        self.assertIn('2 chests', translated)
        self.assertIn('10 keys per second', translated)
        self.assertIn('background clicks', translated)
        self.assertEqual(i18n.translate_message('en', '自動按鍵：關；自動領箱：開。'),
                         'Auto keypress: off; auto claim: on.')

    def test_unknown_raw_error_details_survive_language_switch(self):
        raw = '[WinError 5] Access denied: D:\\資料\\runtime.tmp -> runtime.json'
        self.assertEqual(i18n.translate_message('en', raw), raw)
        self.assertEqual(i18n.translate_message('en', '已暫停：' + raw), 'Paused: ' + raw)

    def test_unconfirmed_claim_retains_no_fallback_and_cooldown_warning(self):
        text = '背景訊息已送出，但寶箱仍在，領取未確認；保留冷卻，不會自動改用前台滑鼠。'
        translated = i18n.translate_message('en', text)
        self.assertIn('unconfirmed', translated)
        self.assertIn('cooldown is retained', translated)
        self.assertIn('no automatic foreground fallback', translated)


if __name__ == '__main__':
    unittest.main()
