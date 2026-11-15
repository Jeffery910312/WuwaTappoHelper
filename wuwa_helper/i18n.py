"""Runtime UI translations; saved settings and diagnostic details stay stable."""
import re


LANGUAGES = ('zh-TW', 'en')

_TEXT = {
    'zh-TW': {
        'app_title': 'WuwaTappo 助手',
        'primary_count': '主要點擊數',
        'goal': '領箱門檻 {count:,} 次',
        'cooldown': '冷卻 {timer}',
        'cooldown_ready': '冷卻就緒',
        'confirmed': '已確認 {count} 個寶箱',
        'settings': '設定',
        'auto_keys': '自動按鍵',
        'auto_claim': '自動領箱',
        'key': '累積按鍵',
        'rate': '每秒次數',
        'click_mode': '點擊方式',
        'foreground': '前台',
        'background': '背景',
        'start': '開始',
        'pause': '暫停',
        'status_paused': '已暫停',
        'status_disabled': '功能已關閉',
        'status_claim_paused': '領箱已暫停',
        'status_claim_ready': '準備領箱',
        'status_auto_keys': '自動按鍵中',
        'status_wait_input': '等待輸入偵測',
        'status_cooling': '領箱冷卻中',
        'status_wait_claim': '等待領箱',
        'status_test': '測試 {sent} / {total}',
        'theme_to_light': '切換淺色模式',
        'theme_to_dark': '切換深色模式',
        'click_info': '前台模式會搶滑鼠，背景模式異常時再切換',
        'switch_language': 'Switch to English',
        'settings_saved': '設定已儲存。',
        'features_updated': '功能設定已更新。',
        'short_test_running': '正在測試 20 次按鍵。',
    },
    'en': {
        'app_title': 'WuwaTappo Helper',
        'primary_count': 'Click count',
        'goal': 'Claim threshold {count:,}',
        'cooldown': 'Cooldown {timer}',
        'cooldown_ready': 'Cooldown ready',
        'confirmed': 'Chests confirmed: {count}',
        'settings': 'Settings',
        'auto_keys': 'Auto keypress',
        'auto_claim': 'Auto claim',
        'key': 'Accumulation key',
        'rate': 'Keys per second',
        'click_mode': 'Click mode',
        'foreground': 'Foreground',
        'background': 'Background',
        'start': 'Start',
        'pause': 'Pause',
        'status_paused': 'Paused',
        'status_disabled': 'Features off',
        'status_claim_paused': 'Claim paused',
        'status_claim_ready': 'Preparing claim',
        'status_auto_keys': 'Auto keypress running',
        'status_wait_input': 'Waiting for input detection',
        'status_cooling': 'Claim cooldown',
        'status_wait_claim': 'Waiting for chest',
        'status_test': 'Test {sent} / {total}',
        'theme_to_light': 'Switch to light mode',
        'theme_to_dark': 'Switch to dark mode',
        'click_info': 'Foreground mode takes control of your mouse; switch to it if background mode is not working.',
        'switch_language': '切換為繁體中文',
        'settings_saved': 'Settings saved.',
        'features_updated': 'Feature settings updated.',
        'short_test_running': 'Testing 20 keypresses.',
    },
}


def tr(language, key, **values):
    """Translate a UI key, tolerating an old locale or unknown future key."""
    template = _TEXT.get(language, _TEXT['zh-TW']).get(key, key)
    try:
        return template.format(**values)
    except (KeyError, ValueError, TypeError):
        # A display-format error must not stop input cancellation or the GUI.
        return template


_MESSAGES = {
    '設定已儲存。': 'Settings saved.',
    '功能設定已更新。': 'Feature settings updated.',
    '正在測試 20 次按鍵。': 'Testing 20 keypresses.',
    '等待唯一的 Wuwa Tappo 視窗；恢復後自動接續。': 'Waiting for a single Wuwa Tappo window; operation resumes when it returns.',
    'OCR 暫時無法校正，保留輸入估算並稍後重試；寶箱仍依畫面確認。': 'OCR calibration is temporarily unavailable. The input estimate is retained; calibration will retry. Chest claims still require screen confirmation.',
    'OCR 校正已恢復，已更新輸入估算。': 'OCR calibration recovered; the input estimate has been updated.',
    '請選 F13～F24，以及 1 或 2 個寶箱。': 'Choose F13–F24 and one or two chests.',
    '每秒次數必須是 1～10 的整數。': 'Keys per second must be an integer from 1 to 10.',
    '點擊方式請選前台滑鼠或背景訊息。': 'Choose foreground or background click mode.',
    '功能開關必須是布林值。': 'Feature switches must be Boolean values.',
    '主題請選淺色或深色。': 'Choose a light or dark theme.',
    '語言請選繁體中文或英文。': 'Choose Traditional Chinese or English.',
    '語言請選中文或英文。': 'Choose Traditional Chinese or English.',
    '語言請選中文或 English。': 'Choose Traditional Chinese or English.',
    '短測試：只送出 20 次按鍵後自動暫停，不領寶箱。': 'Short test: sends 20 keypresses, then pauses automatically without claiming chests.',
    '已有領取進度或仍在冷卻，保留現有進度。': 'Claim progress or cooldown is already active; existing progress is retained.',
    '按鍵診斷已重設；主要點擊估算與 OCR 校正保留。': 'Keypress diagnostics reset; the click estimate and OCR calibration are retained.',
    '寶箱領取結束，冷卻 30 分鐘；自動按鍵依開關持續。': 'Chest claims finished. A 30-minute cooldown has started; auto keypress follows its switch.',
    '等待修飾鍵／滑鼠按鈕放開，或可用的桌面。': 'Waiting for modifier keys or mouse buttons to be released, or for an available desktop.',
    '找不到唯一桌寵視窗，短測試已暫停。': 'A single companion window could not be found; the short test is paused.',
    '等待唯一的 Wuwa Tappo 桌寵視窗；不送出按鍵。': 'Waiting for a single Wuwa Tappo companion window; no keypresses are sent.',
    '第二個寶箱仍未確認，領箱暫停並保留進度；切換領箱開關或暫停後再開始重試。自動按鍵依開關持續。': 'The second chest is still unconfirmed. Claims are paused and progress is retained. Toggle auto claim, or pause and start again to retry. Auto keypress follows its switch.',
    '第二個寶箱仍未確認，領箱暫停並保留進度；切換領箱開關或 F5 重試。自動按鍵依開關持續。': 'The second chest is still unconfirmed. Claims are paused and progress is retained. Toggle auto claim or use F5 to retry. Auto keypress follows its switch.',
    '暫未看到可點擊的第二個寶箱，保留進度並繼續檢查。': 'The second chest is not clickable yet; progress is retained while checks continue.',
    '可領寶箱已就緒，暫停自動按鍵，等待閒置 1.2 秒。': 'A claimable chest is ready. Auto keypress is paused while waiting for 1.2 seconds of inactivity.',
    '寶箱正在移動，等待位置穩定。': 'The chest is moving; waiting for a stable position.',
    '點擊後視窗位置改變，結果未確認；保留冷卻，自動按鍵依開關持續。': 'The window moved after the click; the result is unconfirmed. Cooldown is retained and auto keypress follows its switch.',
    '背景訊息已送出，但寶箱仍在，領取未確認；保留冷卻，不會自動改用前台滑鼠。': 'The background message was sent, but the chest remains. The claim is unconfirmed; cooldown is retained and there is no automatic foreground fallback.',
    '點擊後寶箱仍在，結果未確認；保留冷卻，自動按鍵依開關持續。': 'The chest remains after the click; the result is unconfirmed. Cooldown is retained and auto keypress follows its switch.',
    '寶箱圖案已消失，接著檢查其他寶箱。': 'The chest icon disappeared; checking the other chests.',
    '無法確認 Wuwa Tappo 的輸入權限，自動領箱已暫停。請確認遊戲仍在執行，再重新開始領箱。': 'Unable to verify Wuwa Tappo input permissions; auto claim is paused. Confirm the game is running, then restart claiming.',
    'Wuwa Tappo 的權限高於助手，寶箱點擊會被 Windows 阻擋，自動領箱已暫停。請關閉助手，再執行 Start.bat 並允許管理員權限。': 'Wuwa Tappo has higher privileges than the helper, so Windows blocks chest clicks and auto claim is paused. Close the helper, run Start.bat again, and allow administrator access.',
    '此專案的 Wuwa Tappo 寶箱助手已在執行。': 'The Wuwa Tappo helper for this project is already running.',
    '累積按鍵請選 F13～F24。': 'Choose an accumulation key from F13–F24.',
    'Windows 拒絕鍵盤輸入，已暫停。': 'Windows rejected keyboard input; operation is paused.',
    '無法取得螢幕座標，已暫停。': 'Unable to obtain screen coordinates; operation is paused.',
    'Windows 拒絕滑鼠輸入，已暫停。': 'Windows rejected mouse input; operation is paused.',
    'Windows 拒絕滑鼠放開事件，已暫停。': 'Windows rejected the mouse-button release event; operation is paused.',
    '無法換算背景點擊座標，自動領箱已暫停。': 'Unable to convert background click coordinates; auto claim is paused.',
    '背景點擊座標超出 Windows 訊息範圍，自動領箱已暫停。': 'Background click coordinates exceed the Windows message range; auto claim is paused.',
    '背景輸入視窗已變更，無法確認點擊結果，自動領箱已暫停。': 'The background input window changed. The click result cannot be confirmed; auto claim is paused.',
    '未取得管理員權限，寶箱助手未啟動。請重新執行 Start.bat 並允許管理員權限。': 'Administrator access was not obtained, so the helper did not start. Run Start.bat again and allow administrator access.',
    '已取消管理員授權，寶箱助手未啟動。需要使用時請重新執行 Start.bat。': 'Administrator authorization was cancelled, so the helper did not start. Run Start.bat again when needed.',
    '寶箱助手已在執行，請使用已開啟的工具視窗。': 'The helper is already running. Use the existing helper window.',
    '找不到 Windows OCR 語言，請安裝 Windows 英文或中文 OCR 語言功能。': 'No Windows OCR language was found. Install Windows English or Chinese OCR language support.',
    '初始化輸入計數逾時。': 'Input counter initialization timed out.',
    '按鍵診斷暫時無法儲存，稍後重試。': 'Keypress diagnostics could not be saved temporarily; saving will retry.',
    'F5 已被占用，請使用開始／暫停按鈕。': 'F5 is already in use. Use the Start/Pause button.',
    '無法讀取程序完整性層級。': 'Unable to read the process integrity level.',
    '程序完整性層級沒有有效的 SID。': 'The process integrity level has no valid SID.',
}

_PATTERNS = (
    (re.compile(r'自動按鍵：(開|關)；自動領箱：(開|關)。(?:F5 總開關保持原狀。)?'),
     lambda m: f'Auto keypress: {"on" if m[1] == "開" else "off"}; auto claim: {"on" if m[2] == "開" else "off"}.'),
    (re.compile(r'設定已自動儲存：(F\d+)，(\d+) 個寶箱，每秒最多 (\d+) 次，(前台|背景)點擊。'),
     lambda m: f'Settings saved: {m[1]}, {m[2]} chests, up to {m[3]} keys per second, {"foreground" if m[4] == "前台" else "background"} clicks.'),
    (re.compile(r'自動按鍵 (F\d+) 執行中；每秒最多 (\d+) 次。'),
     lambda m: f'Auto keypress {m[1]} is running; up to {m[2]} keys per second.'),
    (re.compile(r'短測試完成：共送出 (\d+) 次按鍵，已自動暫停。請確認遊戲計數。'),
     lambda m: f'Short test finished: {m[1]} keypresses sent, then paused automatically. Check the game count.'),
    (re.compile(r'短測試完成：已送出 (\d+) 次 (F\d+)，已自動暫停。請確認遊戲計數。'),
     lambda m: f'Short test finished: {m[1]} {m[2]} keypresses sent, then paused automatically. Check the game count.'),
    (re.compile(r'輸入估算已達 ([\d,]+)，等待可點擊寶箱；自動按鍵依開關持續。'),
     lambda m: f'The input estimate reached {m[1]}; waiting for a clickable chest. Auto keypress follows its switch.'),
    (re.compile(r'已送出(前台|背景)(?:滑鼠|訊息（實驗）)?寶箱點擊 \((-?\d+), (-?\d+)\)，正在確認畫面。'),
     lambda m: f'{"Foreground" if m[1] == "前台" else "Background"} chest click sent to ({m[2]}, {m[3]}). Verifying the screen.'),
    (re.compile(r'Windows 拒絕背景滑鼠訊息（錯誤 (.+)），自動領箱已暫停。'),
     lambda m: f'Windows rejected the background mouse message (error {m[1]}); auto claim is paused.'),
)


def translate_message(language, text):
    """Translate known operation messages without discarding unknown diagnostics."""
    if language != 'en' or not isinstance(text, str):
        return text
    if text in _MESSAGES:
        return _MESSAGES[text]
    if text.startswith('已暫停：'):
        return 'Paused: ' + translate_message(language, text[len('已暫停：'):])
    suffix = ' 自動按鍵依開關持續。'
    if text.endswith(suffix):
        return translate_message(language, text[:-len(suffix)]) + ' Auto keypress follows its switch.'
    for pattern, replacement in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return replacement(match)
    return text
