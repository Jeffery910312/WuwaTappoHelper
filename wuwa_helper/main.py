"""Wuwa Tappo desktop accumulator and chest helper."""
import argparse
import math
import json
import logging
from pathlib import Path
import queue
import threading
import time

from PIL import Image

from .detector import Detector
from .paths import PROJECT_ROOT

BASE = PROJECT_ROOT
COOLDOWN = 30 * 60
KEYS = tuple(f'F{i}' for i in range(13, 25))
CLICK_MODES = {'foreground': '前台', 'background': '背景'}


class Engine:
    def __init__(self, events, detector=None, api=None, grab=None, state_path=None, reader=None, monitor=None):
        if api is None:
            from . import windows_api as api
        self.api = api
        self.detector = detector or Detector()
        # Hover transparency changes the gold icon's color. Verification must
        # still see that icon instead of treating a color change as a claim.
        self.verifier = detector or Detector(color_error=None)
        if reader is None:
            from .counter_reader import CounterReader
            reader = CounterReader()
        self.reader = reader
        if grab is None:
            from .capture import grab_region
            grab = grab_region
        self.grab = grab
        if monitor is None:
            from .input_monitor import InputMonitor
            monitor = InputMonitor()
        self.monitor = monitor
        self.counter_inputs = self.monitor.snapshot()['total']
        # Start conservatively at zero until OCR supplies a calibration. Inputs
        # observed in this session remain usable when a later OCR attempt fails.
        self.count_base = 0
        self.counter_region = None
        self.region_target = None
        self.events = events
        self.commands = queue.Queue()
        self.active = threading.Event()
        self.closed = threading.Event()
        self.state_path = state_path or BASE / 'runtime.json'
        self._save_lock = threading.Lock()
        self._input_lock = threading.RLock()
        self._settings_revision = 0
        self._saved_sent = 0
        self.next_save = 0.0
        self.next_scan = 0.0
        self.count = 0
        self.last_point = None
        self.round_count = 0
        self.round_started = 0.0
        self.round_deadline = 0.0
        self.last_claim = 0.0
        self.claim_pending = False
        self.in_flight = False
        self.key, self.boxes, self.rate = 'F13', 2, 5
        self.click_mode = 'foreground'
        self.theme = 'light'
        self.language = 'zh-TW'
        self.auto_keys = self.auto_claim = True
        self.claim_waiting = self.claim_blocked = False
        self.claim_issue = None
        self.sent = 0
        self.next_pulse = self.next_check = 0.0
        self.last_message = None
        self.test_remaining = self.test_sent = 0
        self.game_count = None
        self.next_read = 0.0
        self.counter_read_at = 0.0
        self.stalled_keys = 0
        self.read_failures = 0
        try:
            saved = json.loads(self.state_path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                return
            key, boxes, rate = saved.get('key', 'F13'), saved.get('boxes', 2), saved.get('rate', 5)
            self.validate(key, boxes, rate)
            auto_keys, auto_claim = saved.get('auto_keys', True), saved.get('auto_claim', True)
            if type(auto_keys) is not bool or type(auto_claim) is not bool:
                return
            sent = saved.get('sent', 0)
            value = float(saved.get('next_scan', 0))
            if type(sent) is not int or not 0 <= sent <= 10_000_000 or not math.isfinite(value):
                return
            self.key, self.boxes, self.rate, self.sent = key, boxes, rate, sent
            self._saved_sent = sent
            self.auto_keys, self.auto_claim = auto_keys, auto_claim
            mode = saved.get('click_mode', 'foreground')
            if isinstance(mode, str) and mode in CLICK_MODES:
                self.click_mode = mode
            theme = saved.get('theme', 'light')
            if isinstance(theme, str) and theme in ('light', 'dark'):
                self.theme = theme
            language = saved.get('language', 'zh-TW')
            if isinstance(language, str) and language in ('zh-TW', 'en'):
                self.language = language
            claimed = saved.get('round_count', 0)
            deadline = float(saved.get('round_deadline', 0))
            last_claim = float(saved.get('last_claim', 0))
            if type(claimed) is int and 0 <= claimed <= boxes and math.isfinite(deadline) and math.isfinite(last_claim):
                self.round_count = claimed
                self.round_deadline = deadline
                self.last_claim = last_claim
            self.claim_pending = saved.get('claim_pending') is True
            if 0 < value <= time.time() + COOLDOWN + 5:
                self.next_scan = value
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def emit(self, message):
        if message != self.last_message:
            self.last_message = message
            self.events.put(message)
            logging.info(message)

    @property
    def goal(self):
        return self.boxes * 1000

    @property
    def required_count(self):
        return max(0, self.boxes - self.round_count) * 1000

    @property
    def estimated_count(self):
        delta = max(0, self.monitor.snapshot()['total'] - self.counter_inputs) if self.monitor_healthy else 0
        return max(0, self.count_base + delta)

    @property
    def monitor_healthy(self):
        return bool(getattr(self.monitor, 'running', True)) and not getattr(self.monitor, 'error', None)

    @property
    def count_available(self):
        return self.monitor_healthy or (self.game_count is not None and not self.read_failures
                                       and time.monotonic() - self.counter_read_at <= 5)

    def debit_claim(self):
        """Account for a visually confirmed claim before any new OCR calibration."""
        total = self.monitor.snapshot()['total']
        delta = max(0, total - self.counter_inputs) if self.monitor_healthy else 0
        self.count_base = max(0, self.count_base + delta - 1000)
        self.counter_inputs = total

    def counter_capture(self, target):
        region = self.counter_region if self.region_target == target.rect else None
        left, top = (region[0], region[1]) if region else (0, 0)
        rect = (target.rect[0]+region[0], target.rect[1]+region[1],
                target.rect[0]+region[2], target.rect[1]+region[3]) if region else target.rect
        reading = self.reader.read(self.grab(rect))
        if reading is None and region:
            # The pet may have moved: relocate inside its own window only.
            reading = self.reader.read(self.grab(target.rect))
            left = top = 0
        if reading is not None and hasattr(reading, 'rect'):
            x1, y1, x2, y2 = reading.rect
            x1, x2, y1, y2 = x1+left, x2+left, y1+top, y2+top
            scale = (x2-x1) / 108
            width, height = target.rect[2]-target.rect[0], target.rect[3]-target.rect[1]
            self.counter_region = (max(0, round(x1-24*scale)), max(0, round(y1-12*scale)),
                                   min(width, round(x2+68*scale)), min(height, round(y2+12*scale)))
            self.region_target = target.rect
        return reading

    def find_chests(self, target, verification=False):
        matcher = self.verifier if verification else self.detector
        region = self.counter_region if self.region_target == target.rect else None
        if region:
            x1, y1, x2, y2 = region
            scale = (x2-x1) / 200
            width, height = target.rect[2]-target.rect[0], target.rect[3]-target.rect[1]
            left, top = max(0, round(x1-70*scale)), max(0, round(y1-180*scale))
            right, bottom = min(width, round(x2+40*scale)), min(height, round(y2+20*scale))
            matches = matcher.find(self.grab((target.rect[0]+left, target.rect[1]+top,
                                                  target.rect[0]+right, target.rect[1]+bottom)))
            from .detector import Match
            return [Match(m.x+left, m.y+top, m.width, m.height, m.score) for m in matches]
        return matcher.find(self.grab(target.rect))

    def observe_counter(self, target=None, force=False):
        now = time.monotonic()
        if not force and now < self.next_read:
            return
        self.next_read = now + (30 if self.monitor_healthy else .8)
        if target is None:
            windows = self.api.targets()
            if len(windows) != 1:
                self.next_read = now + 2
                if self.allowed():
                    self.emit('等待唯一的 Wuwa Tappo 視窗；恢復後自動接續。')
                return
            target = windows[0]
        inputs = self.monitor.snapshot()['total']
        try:
            reading = self.counter_capture(target)
        except Exception:
            reading = None
        if reading is None:
            self.read_failures += 1
            self.next_read = now + min(10, 2 * self.read_failures)
            if self.allowed():
                self.emit('OCR 暫時無法校正，保留輸入估算並稍後重試；寶箱仍依畫面確認。')
            return
        value = reading.value
        if self.game_count is not None and value != self.game_count:
            self.stalled_keys = 0
        self.game_count = value
        self.count_base = value
        self.counter_inputs = inputs
        self.counter_read_at = now
        if self.read_failures and self.allowed():
            self.emit('OCR 校正已恢復，已更新輸入估算。')
        self.read_failures = 0

    @staticmethod
    def validate(key, boxes, rate):
        if key not in KEYS or type(boxes) is not int or boxes not in (1, 2):
            raise ValueError('請選 F13～F24，以及 1 或 2 個寶箱。')
        if type(rate) is not int or not 1 <= rate <= 10:
            raise ValueError('每秒次數必須是 1～10 的整數。')

    def request_settings(self, key, boxes, rate, click_mode=None):
        self.validate(key, boxes, rate)
        mode = self.click_mode if click_mode is None else click_mode
        if not isinstance(mode, str) or mode not in CLICK_MODES:
            raise ValueError('點擊方式請選前台滑鼠或背景訊息。')
        # Invalidate any prepared click immediately, before the worker applies
        # the queued values, while leaving normal execution and tests running.
        self._settings_revision += 1
        self.commands.put(('settings', key, boxes, rate, mode))

    def request_features(self, auto_keys, auto_claim):
        if type(auto_keys) is not bool or type(auto_claim) is not bool:
            raise ValueError('功能開關必須是布林值。')
        was_claiming = self.auto_claim
        # Publish switches immediately, including to an in-flight input callback.
        self.auto_keys, self.auto_claim = auto_keys, auto_claim
        if not auto_claim:
            self.claim_waiting = False
        elif not was_claiming:
            self.claim_blocked = False
            self.claim_issue = None
            self.round_deadline = 0
            self.next_read = self.next_check = 0
        self.commands.put(('features',))

    def request_theme(self, theme):
        if not isinstance(theme, str) or theme not in ('light', 'dark'):
            raise ValueError('主題請選淺色或深色。')
        self.theme = theme
        self.commands.put(('theme',))

    def request_language(self, language):
        if not isinstance(language, str) or language not in ('zh-TW', 'en'):
            raise ValueError('語言請選中文或 English。')
        self.language = language
        self.commands.put(('language',))

    def request_test(self):
        self.pause()
        self.commands.put(('test',))

    def request_reset(self):
        self.pause()
        self.commands.put(('reset',))

    def apply_commands(self):
        # A completed key must be accounted before replacing short-test state.
        with self._input_lock:
            self._apply_commands()

    def _apply_commands(self):
        while not self.commands.empty():
            command, *values = self.commands.get_nowait()
            if command in ('theme', 'language'):
                self.save()
                continue
            if command == 'features':
                self.save()
                self.emit(f'自動按鍵：{"開" if self.auto_keys else "關"}；自動領箱：{"開" if self.auto_claim else "關"}。')
                continue
            if command == 'settings':
                self.key, self.boxes, self.rate, self.click_mode = values
                self.claim_waiting = False
                self.claim_blocked = False
                self.claim_issue = None
                self.next_check = 0
                self.save()
                self.emit(f'設定已自動儲存：{self.key}，{self.boxes} 個寶箱，每秒最多 {self.rate} 次，{CLICK_MODES[self.click_mode]}點擊。')
                continue
            self.pause()
            self.test_remaining = 0
            if command == 'test':
                self.test_remaining, self.test_sent = 20, 0
                self.active.set()
                self.emit('短測試：只送出 20 次按鍵後自動暫停，不領寶箱。')
            elif command == 'reset':
                if self.round_count or self.claim_pending or self.next_scan > time.time():
                    self.emit('已有領取進度或仍在冷卻，保留現有進度。')
                else:
                    self.sent = 0
                    self.save()
                    self.emit('按鍵診斷已重設；主要點擊估算與 OCR 校正保留。')

    def save(self):
        # Windows file scanners/readers can briefly prevent atomic replacement.
        # Serialize writers and retain the previous complete state on failure.
        with self._save_lock:
            saved_sent = self.sent
            payload = json.dumps(dict(next_scan=self.next_scan, key=self.key,
                                boxes=self.boxes, rate=self.rate, sent=saved_sent,
                                auto_keys=self.auto_keys, auto_claim=self.auto_claim,
                                click_mode=self.click_mode,
                                theme=self.theme,
                                language=self.language,
                                round_count=self.round_count, round_deadline=self.round_deadline,
                                last_claim=self.last_claim, claim_pending=self.claim_pending))
            temporary = self.state_path.with_suffix('.tmp')
            for attempt in range(4):
                try:
                    temporary.write_text(payload, encoding='utf-8')
                    temporary.replace(self.state_path)
                    self._saved_sent = saved_sent
                    return
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(.02 * 2**attempt)

    def save_diagnostics(self, force=False):
        if self.sent == self._saved_sent or (not force and time.monotonic() < self.next_save):
            return
        self.next_save = time.monotonic() + 2
        try:
            self.save()
        except OSError:
            # Diagnostic key totals do not authorize claims. Retry them later;
            # claim/cooldown writes still call save() directly and fail closed.
            logging.warning('按鍵診斷暫時無法儲存，稍後重試。', exc_info=True)

    def allowed(self):
        return self.active.is_set() and not self.closed.is_set()

    def keys_allowed(self):
        return self.allowed() and self.auto_keys and not (self.auto_claim and self.claim_waiting)

    def claims_allowed(self):
        # A selection may already be queued when claim_step starts. Do not
        # authorize a click using old fields before the worker applies it.
        return (self.allowed() and self.auto_claim and not self.claim_blocked
                and self.commands.empty())

    def pause(self):
        self.active.clear()
        self.claim_waiting = False
        self.save_diagnostics(force=True)

    def toggle(self):
        if self.active.is_set():
            self.pause()
        elif self.commands.empty():
            if self.round_count and self.round_deadline < time.time():
                self.round_deadline = 0
            self.active.set()
            self.claim_blocked = False
            self.claim_issue = None
            self.next_read = 0

    def finish_round(self):
        self.next_scan = max(time.time(), self.last_claim) + COOLDOWN
        self.sent = 0
        self.round_count = 0
        self.round_started = 0
        self.round_deadline = 0
        self.claim_pending = False
        self.claim_waiting = False
        self.save()
        self.emit('寶箱領取結束，冷卻 30 分鐘；自動按鍵依開關持續。')

    def pulse_keys(self):
        """The independent key loop never stops just because credit is sufficient."""
        if not self.keys_allowed() or time.monotonic() < self.next_pulse:
            return
        self.next_pulse = time.monotonic() + 1 / self.rate
        if self.api.tap_key(self.key, self.keys_allowed):
            self.sent += 1
            self.stalled_keys += 1
            self.emit(f'自動按鍵 {self.key} 執行中；每秒最多 {self.rate} 次。')
        else:
            self.emit('等待修飾鍵／滑鼠按鈕放開，或可用的桌面。')

    def short_test_step(self):
        if self.test_remaining <= 0 or not self.allowed():
            return
        if time.monotonic() < self.next_pulse:
            return
        self.next_pulse = time.monotonic() + 1 / self.rate
        if len(self.api.targets()) != 1:
            self.pause()
            self.emit('找不到唯一桌寵視窗，短測試已暫停。')
            return
        if self.api.tap_key(self.key, self.allowed):
            self.test_remaining -= 1
            self.test_sent += 1
            if not self.test_remaining:
                self.pause()
                self.emit(f'短測試完成：共送出 {self.test_sent} 次按鍵，已自動暫停。請確認遊戲計數。')

    def key_step(self):
        with self._input_lock:
            self._key_step()

    def _key_step(self):
        if not self.allowed():
            return
        if self.test_remaining:
            self.short_test_step()
            return
        if not self.keys_allowed() or time.monotonic() < self.next_pulse:
            return
        if len(self.api.targets()) != 1:
            self.next_pulse = time.monotonic() + 1
            self.emit('等待唯一的 Wuwa Tappo 桌寵視窗；不送出按鍵。')
            return
        self.pulse_keys()

    def step(self, send_keys=True):
        if not self.allowed():
            return
        if self.test_remaining:
            if send_keys:
                self.short_test_step()
            return
        if not self.auto_keys and not self.auto_claim:
            self.claim_waiting = False
            return
        if self.next_scan and time.time() >= self.next_scan:
            self.next_scan = 0
            self.sent = self.round_count = 0
            self.round_started = self.round_deadline = 0
            self.claim_pending = False
            self.save()
        now = time.monotonic()
        cooling = time.time() < self.next_scan
        claim_due = self.auto_claim and not self.claim_blocked and not cooling and now >= self.next_check
        key_due = send_keys and self.keys_allowed() and now >= self.next_pulse
        if not claim_due and not key_due:
            return
        windows = self.api.targets()
        if len(windows) != 1:
            self.next_pulse = self.next_check = now + 1
            self.claim_waiting = False
            self.emit('等待唯一的 Wuwa Tappo 桌寵視窗；不送出按鍵。')
            return
        hold_keys = self.claim_waiting and self.auto_claim and not cooling
        if claim_due:
            hold_keys = self.claim_step(windows[0])
        if send_keys and not hold_keys:
            self.pulse_keys()

    def claim_step(self, target):
        """Return True while a visible, eligible chest needs keyboard silence."""
        click_mode = self.click_mode
        settings_revision = self._settings_revision
        if not self.claims_allowed():
            self.claim_waiting = False
            return False
        try:
            self.api.ensure_input_access(target)
        except RuntimeError as exc:
            self.claim_waiting = False
            self.claim_blocked = True
            self.claim_issue = f'{exc} 自動按鍵依開關持續。'
            self.emit(self.claim_issue)
            return False
        now = time.monotonic()
        self.next_check = now + 3
        if self.round_count >= self.boxes:
            self.finish_round()
            return False
        if self.round_count:
            if not self.round_deadline:
                self.round_deadline = time.time() + 45
                self.save()
            if time.time() >= self.round_deadline:
                self.claim_blocked = True
                self.claim_waiting = False
                self.claim_issue = '第二個寶箱仍未確認，領箱暫停並保留進度；切換領箱開關或暫停後再開始重試。自動按鍵依開關持續。'
                self.emit(self.claim_issue)
                return False
        self.observe_counter(target)
        if (not self.claims_allowed() or not self.count_available
                or self.estimated_count < self.required_count):
            self.claim_waiting = False
            return False
        matches = self.find_chests(target)
        if not self.claims_allowed():
            self.claim_waiting = False
            return False
        visible = [m for m in matches if self.api.owns_point(target,
                   target.rect[0]+m.center[0], target.rect[1]+m.center[1])]
        if not visible:
            self.claim_waiting = False
            if self.round_count:
                self.emit('暫未看到可點擊的第二個寶箱，保留進度並繼續檢查。')
            else:
                self.emit(f'輸入估算已達 {self.estimated_count}，等待可點擊寶箱；自動按鍵依開關持續。')
            return False
        # Input counts drive eligibility; OCR is periodic calibration, not a
        # required read before each click. The exposed ready chest is required.
        if not self.claims_allowed() or not self.count_available or self.estimated_count < self.required_count:
            self.claim_waiting = False
            return False
        self.claim_waiting = True
        if click_mode == 'foreground' and (self.api.idle_seconds() < 1.2 or self.api.held_keys()):
            self.emit('可領寶箱已就緒，暫停自動按鍵，等待閒置 1.2 秒。')
            return True
        match = visible[0]
        if self.closed.wait(.25) or not self.claims_allowed():
            self.claim_waiting = False
            return False
        fresh = self.find_chests(target)
        near = [m for m in fresh if abs(m.center[0]-match.center[0]) <= 5
                and abs(m.center[1]-match.center[1]) <= 5]
        if not near:
            self.claim_waiting = False
            self.emit('寶箱正在移動，等待位置穩定。')
            return False
        match = near[0]
        x, y = target.rect[0]+match.center[0], target.rect[1]+match.center[1]
        self.in_flight = True
        try:
            # A queued selection invalidates this prepared click before its
            # values are applied, including a mode changed away and back.
            allowed = lambda: (self.claims_allowed() and self.click_mode == click_mode
                               and self._settings_revision == settings_revision)
            click = self.api.click_background if click_mode == 'background' else self.api.click
            previous_pending, previous_scan = self.claim_pending, self.next_scan
            if click_mode == 'background':
                # A queued down can take effect even if pause, process exit or
                # release delivery fails. Persist uncertainty before posting.
                self.next_scan = time.time() + COOLDOWN
                self.claim_pending = True
                self.save()
            if not click(target, x, y, allowed):
                if click_mode == 'background':
                    # False guarantees no button-down was queued.
                    self.claim_pending, self.next_scan = previous_pending, previous_scan
                    self.save()
                self.claim_waiting = False
                return False
            self.next_scan = time.time() + COOLDOWN
            self.claim_pending = True
            self.save()
            self.last_point = (x, y)
            self.emit(f'已送出{CLICK_MODES[click_mode]}寶箱點擊 ({x}, {y})，正在確認畫面。')
            if self.closed.wait(2):
                self.claim_waiting = False
                return True
            current = self.api.targets()
            if len(current) != 1 or current[0] != target:
                self.claim_waiting = False
                self.emit('點擊後視窗位置改變，結果未確認；保留冷卻，自動按鍵依開關持續。')
                return True
            after = self.find_chests(target, verification=True)
            remains = any(abs(m.center[0]-match.center[0]) < match.width*.6
                          and abs(m.center[1]-match.center[1]) < match.height*.6 for m in after)
            if remains and len(after) >= len(fresh):
                self.claim_waiting = False
                if click_mode == 'background':
                    self.claim_blocked = True
                    self.claim_issue = '背景訊息已送出，但寶箱仍在，領取未確認；保留冷卻，不會自動改用前台滑鼠。'
                    self.emit(self.claim_issue)
                else:
                    self.emit('點擊後寶箱仍在，結果未確認；保留冷卻，自動按鍵依開關持續。')
                return True
            self.debit_claim()
            self.count += 1
            self.round_count += 1
            self.claim_pending = False
            self.last_claim = time.time()
            if not self.round_started:
                self.round_started = time.monotonic()
            self.round_deadline = time.time() + 45
            self.next_read = 0
            self.emit('寶箱圖案已消失，接著檢查其他寶箱。')
            if self.round_count < self.boxes:
                self.next_scan = 0
                self.claim_waiting = bool(after) and self.auto_claim
                self.save()
            else:
                self.finish_round()
            return True
        finally:
            self.in_flight = False

    def run(self):
        keyboard = threading.Thread(target=self.run_keys, name='key-worker', daemon=True)
        keyboard.start()
        try:
            while not self.closed.is_set():
                try:
                    self.apply_commands()
                    if not self.test_remaining:
                        self.observe_counter()
                    self.step(send_keys=False)
                    self.save_diagnostics()
                except Exception as exc:
                    self.pause()
                    logging.exception('Worker stopped')
                    self.emit(f'已暫停：{exc}')
                self.closed.wait(.02)
        finally:
            self.pause()
            keyboard.join(timeout=2)

    def run_keys(self):
        while not self.closed.is_set():
            try:
                self.key_step()
            except Exception as exc:
                self.pause()
                logging.exception('Keyboard worker stopped')
                self.emit(f'已暫停：{exc}')
            self.closed.wait(.01)


def gui():
    # DPI awareness must be set before Tk creates any windows.
    from . import windows_api as api
    from .themes import UI_FONT, configure_theme, style_combobox_popup
    from .window_style import apply_window_style, get_frame_handle, set_app_identity
    from .i18n import tr, translate_message
    import tkinter as tk
    from tkinter import ttk

    ui_font = UI_FONT
    set_app_identity()
    root = tk.Tk()
    root.iconbitmap(default=str(BASE / 'assets' / 'app-icon.ico'))
    root.title('WuwaTappo Helper')
    root.geometry('800x640')
    root.minsize(760, 600)
    events = queue.Queue()
    engine = Engine(events, api=api)
    style = ttk.Style(root)
    style.theme_use('clam')
    palette = configure_theme(root, style, engine.theme)
    language = tk.StringVar(value=engine.language)
    localized_widgets = []

    def localized_label(parent, key, **options):
        widget = ttk.Label(parent, text=tr(language.get(), key), **options)
        localized_widgets.append((widget, key))
        return widget

    frame = ttk.Frame(root, style='App.TFrame', padding=(24, 20))
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)
    header = ttk.Frame(frame, style='App.TFrame')
    header.grid(row=0, column=0, sticky='ew', pady=(0, 16))
    header.columnconfigure(0, weight=1)
    localized_label(header, 'app_title', style='Title.App.TLabel').grid(row=0, column=0, sticky='w')
    status = tk.StringVar(value=tr(language.get(), 'status_paused'))
    badge = ttk.Label(header, textvariable=status, style='Idle.Badge.TLabel')
    badge.grid(row=0, column=1, sticky='e', padx=(0, 16))
    dark_mode = tk.BooleanVar(value=engine.theme == 'dark')
    theme_button = ttk.Button(header, text='☀' if dark_mode.get() else '☾', width=2,
                              takefocus=True, command=lambda: theme_changed(), style='Theme.TButton')
    theme_button.grid(row=0, column=2, sticky='e')
    language_button = ttk.Button(header, text='EN' if language.get() == 'zh-TW' else '中', width=3,
                                 takefocus=True, command=lambda: language_changed(), style='Language.TButton')
    language_button.grid(row=0, column=3, sticky='e', padx=(6, 0))
    count_value = tk.StringVar(value='0')
    goal = tk.StringVar()
    cooldown = tk.StringVar()
    confirmed = tk.StringVar()
    detail = tk.StringVar(value='')
    last_detail = ''
    card_borders = []
    combos = []

    def card(row, bottom=14):
        border = tk.Frame(frame, background=palette['card'], highlightthickness=1,
                          highlightbackground=palette['line'], borderwidth=0)
        border.grid(row=row, column=0, sticky='nsew', pady=(0, bottom))
        card_borders.append(border)
        body = ttk.Frame(border, style='Card.TFrame', padding=(20, 16))
        body.pack(fill='both', expand=True)
        return body

    metric = card(1)
    metric.columnconfigure(0, weight=1)
    metric.columnconfigure(1, weight=1)
    metric.rowconfigure(1, weight=1)
    localized_label(metric, 'primary_count', style='Heading.Card.TLabel').grid(row=0, column=0, sticky='w')
    ttk.Label(metric, textvariable=goal, style='Muted.Card.TLabel').grid(row=0, column=1, sticky='e')
    ttk.Label(metric, textvariable=count_value, style='Count.Card.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 4))
    bar = ttk.Progressbar(metric, maximum=engine.goal, style='Count.Horizontal.TProgressbar')
    bar.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 14))
    ttk.Label(metric, textvariable=cooldown, style='Muted.Card.TLabel').grid(row=3, column=0, sticky='w')
    ttk.Label(metric, textvariable=confirmed, style='Muted.Card.TLabel').grid(row=3, column=1, sticky='e')

    settings = card(2, bottom=0)
    settings.columnconfigure(0, weight=1)
    settings_header = ttk.Frame(settings, style='Card.TFrame')
    settings_header.grid(row=0, column=0, sticky='ew', pady=(0, 12))
    settings_header.columnconfigure(0, weight=1)
    localized_label(settings_header, 'settings', style='Heading.Card.TLabel').grid(row=0, column=0, sticky='w')
    switches = ttk.Frame(settings_header, style='Card.TFrame')
    switches.grid(row=0, column=1, sticky='e')
    auto_keys = tk.BooleanVar(value=engine.auto_keys)
    auto_claim = tk.BooleanVar(value=engine.auto_claim)

    def features():
        engine.request_features(auto_keys.get(), auto_claim.get())

    for key_name, variable, padding in (('auto_keys', auto_keys, (0, 18)), ('auto_claim', auto_claim, 0)):
        check = ttk.Checkbutton(switches, text=tr(language.get(), key_name), variable=variable,
                                command=features, style='Card.TCheckbutton')
        check.pack(side='left', padx=padding)
        localized_widgets.append((check, key_name))
    fields = ttk.Frame(settings, style='Card.TFrame')
    fields.grid(row=1, column=0, sticky='ew')
    key = tk.StringVar(value=engine.key)
    rate = tk.StringVar(value=str(engine.rate))
    click_mode = tk.StringVar(value=engine.click_mode)

    def settings_changed(_event=None):
        try:
            engine.request_settings(key.get(), engine.boxes, int(rate.get()), click_mode.get())
        except ValueError as exc:
            show_message(str(exc))

    for column, (label_key, variable, choices) in enumerate((('key', key, KEYS), ('rate', rate, tuple(range(1, 11))))):
        fields.columnconfigure(column, weight=1, uniform='settings')
        padding = (0, 16) if column == 0 else (0, 0)
        localized_label(fields, label_key, style='Muted.Card.TLabel').grid(row=0, column=column, sticky='w', padx=padding)
        combo = ttk.Combobox(fields, textvariable=variable, values=choices, state='readonly',
                             font=ui_font, style='Card.TCombobox', width=8)
        combo.grid(row=1, column=column, sticky='ew', padx=padding, pady=(5, 0))
        combo.configure(postcommand=lambda target=combo: style_combobox_popup(target, palette))
        combos.append(combo)
        combo.bind('<<ComboboxSelected>>', settings_changed)
    modes = ttk.Frame(settings, style='Card.TFrame')
    modes.grid(row=2, column=0, sticky='ew', pady=(16, 0))
    localized_label(modes, 'click_mode', style='Muted.Card.TLabel').pack(side='left', padx=(0, 14))
    segment = ttk.Frame(modes, style='Card.TFrame')
    segment.pack(side='left')
    for mode in CLICK_MODES:
        radio = ttk.Radiobutton(segment, text=tr(language.get(), mode), value=mode, variable=click_mode,
                                command=settings_changed, style='Mode.Toolbutton')
        radio.pack(side='left')
        localized_widgets.append((radio, mode))
    info_button = ttk.Button(modes, text='ⓘ', width=2, takefocus=True, style='Info.TButton')
    info_button.pack(side='left', padx=(8, 0))
    info_tooltip = tk.Frame(root, background=palette['quiet'], highlightthickness=1,
                            highlightbackground=palette['line'], borderwidth=0)
    tooltip_text = tk.StringVar()
    ttk.Label(info_tooltip, textvariable=tooltip_text, style='Tooltip.TLabel').pack()

    def hide_info(_event=None):
        info_tooltip.place_forget()

    def show_tooltip(button, text):
        tooltip_text.set(text)
        info_tooltip.update_idletasks()
        width, height = info_tooltip.winfo_reqwidth(), info_tooltip.winfo_reqheight()
        # The tooltip is a child overlay: all positions are relative to the
        # root, including when the application lives on a negative-X monitor.
        x = button.winfo_rootx() - root.winfo_rootx()
        y = button.winfo_rooty() - root.winfo_rooty() + button.winfo_height() + 7
        x = max(12, min(x, root.winfo_width() - width - 12))
        if y + height > root.winfo_height() - 12:
            y = max(12, y - button.winfo_height() - height - 14)
        info_tooltip.place(x=x, y=y)
        info_tooltip.lift()

    def show_info(_event=None):
        show_tooltip(info_button, tr(language.get(), 'click_info'))

    def show_theme_info(_event=None):
        show_tooltip(theme_button, tr(language.get(), 'theme_to_light' if dark_mode.get() else 'theme_to_dark'))

    def show_language_info(_event=None):
        show_tooltip(language_button, tr(language.get(), 'switch_language'))

    def leave_info(event):
        if root.focus_get() is not event.widget:
            hide_info()

    info_button.configure(command=show_info)
    info_button.bind('<Enter>', show_info)
    info_button.bind('<Leave>', leave_info)
    info_button.bind('<FocusIn>', show_info)
    info_button.bind('<FocusOut>', hide_info)
    info_button.bind('<Escape>', hide_info)
    theme_button.bind('<Enter>', show_theme_info)
    theme_button.bind('<Leave>', leave_info)
    theme_button.bind('<FocusIn>', show_theme_info)
    theme_button.bind('<FocusOut>', hide_info)
    theme_button.bind('<Escape>', hide_info)
    language_button.bind('<Enter>', lambda event: show_language_info())
    language_button.bind('<Leave>', leave_info)
    language_button.bind('<FocusIn>', lambda event: show_language_info())
    language_button.bind('<FocusOut>', hide_info)
    language_button.bind('<Escape>', hide_info)

    message_label = ttk.Label(frame, textvariable=detail, style='Detail.App.TLabel', wraplength=700)
    message_label.grid(row=3, column=0, sticky='ew', pady=(10, 12))
    buttons = ttk.Frame(frame, style='App.TFrame')
    buttons.grid(row=4, column=0, sticky='ew')
    buttons.columnconfigure(0, weight=1)
    toggle = ttk.Button(buttons, text=tr(language.get(), 'start'), command=engine.toggle, style='Primary.TButton')
    toggle.grid(row=0, column=0, sticky='ew')

    def refresh_window_style():
        hwnd = get_frame_handle(root.winfo_id())
        if hwnd:
            apply_window_style(hwnd, dark=dark_mode.get(), caption_color=palette['page'],
                               border_color=palette['line'], text_color=palette['text'])

    def apply_theme(name):
        nonlocal palette
        hide_info()
        dark_mode.set(name == 'dark')
        theme_button.configure(text='☀' if dark_mode.get() else '☾')
        palette = configure_theme(root, style, name)
        for border in card_borders:
            border.configure(background=palette['card'], highlightbackground=palette['line'])
        for combo in combos:
            style_combobox_popup(combo, palette)
        info_tooltip.configure(background=palette['quiet'], highlightbackground=palette['line'])
        root.after_idle(refresh_window_style)

    def theme_changed():
        name = 'light' if dark_mode.get() else 'dark'
        apply_theme(name)
        engine.request_theme(name)

    def language_changed():
        code = 'en' if language.get() == 'zh-TW' else 'zh-TW'
        language.set(code)
        hide_info()
        for widget, key_name in localized_widgets:
            widget.configure(text=tr(code, key_name))
        language_button.configure(text='EN' if code == 'zh-TW' else '中')
        engine.request_language(code)
        detail.set(translate_message(code, last_detail))
        render_state()
        root.after_idle(fit_content)

    def mapped(event):
        if event.widget is root:
            root.after_idle(refresh_window_style)

    root.bind('<Map>', mapped, add='+')
    worker = threading.Thread(target=engine.run, name='chest-worker', daemon=True)
    worker.start()

    def resize_message(event):
        message_label.configure(wraplength=max(360, event.width))

    message_label.bind('<Configure>', resize_message)

    def fit_content():
        root.update_idletasks()
        width = max(root.winfo_width(), root.winfo_reqwidth())
        height = max(root.winfo_height(), root.winfo_reqheight())
        if width > root.winfo_width() or height > root.winfo_height():
            root.geometry(f'{width}x{height}')

    detail.trace_add('write', lambda *_: root.after_idle(fit_content))

    def show_message(message):
        nonlocal last_detail
        # Routine OCR/key diagnostics remain in the log. The main panel shows
        # short operation results and actionable failures only.
        if message.startswith(('OCR ', '自動按鍵 ', '等待修飾鍵／滑鼠按鈕放開')):
            return
        if message.startswith('設定已自動儲存'):
            message = '設定已儲存。'
        elif message.startswith('自動按鍵：'):
            message = '功能設定已更新。'
        elif message.startswith('短測試：'):
            message = '正在測試 20 次按鍵。'
        last_detail = message
        detail.set(translate_message(language.get(), message))

    def render_state():
        code = language.get()
        running = engine.active.is_set()
        if not running:
            phase, badge_style = tr(code, 'status_paused'), 'Idle'
        elif not engine.auto_keys and not engine.auto_claim:
            phase, badge_style = tr(code, 'status_disabled'), 'Idle'
        elif engine.auto_claim and engine.claim_blocked:
            phase, badge_style = tr(code, 'status_claim_paused'), 'Alert'
        elif engine.claim_waiting:
            phase, badge_style = tr(code, 'status_claim_ready'), 'Running'
        elif engine.auto_keys:
            phase, badge_style = tr(code, 'status_auto_keys'), 'Running'
        elif not engine.count_available:
            phase, badge_style = tr(code, 'status_wait_input'), 'Alert'
        else:
            phase = tr(code, 'status_cooling' if engine.next_scan > time.time() else 'status_wait_claim')
            badge_style = 'Running'
        if running and engine.test_remaining:
            phase, badge_style = tr(code, 'status_test', sent=engine.test_sent, total=20), 'Running'
        status.set(phase)
        badge.configure(style=f'{badge_style}.Badge.TLabel')
        toggle.configure(text=tr(code, 'pause' if running else 'start'))
        estimate = engine.estimated_count
        count_value.set(f'{estimate:,}')
        goal.set(tr(code, 'goal', count=engine.required_count))
        bar.configure(maximum=max(1, engine.required_count), value=min(estimate or 0, engine.required_count))
        remaining = max(0, int(engine.next_scan-time.time()+.999))
        cooldown.set(tr(code, 'cooldown', timer=f'{remaining//60:02d}:{remaining%60:02d}')
                     if remaining else tr(code, 'cooldown_ready'))
        confirmed.set(tr(code, 'confirmed', count=engine.count))
        alert = engine.claim_issue or last_detail.startswith('已暫停：')
        message_label.configure(style='Alert.Detail.App.TLabel' if alert else 'Detail.App.TLabel')

    def tick():
        while not events.empty():
            show_message(events.get_nowait())
        if (engine.auto_claim and engine.claim_blocked and engine.claim_issue
                and last_detail != engine.claim_issue):
            show_message(engine.claim_issue)
        render_state()
        root.after(100, tick)

    def close():
        hide_info()
        engine.pause()
        engine.closed.set()
        try:
            # Appearance changes are published before queueing. Flush them if
            # OCR is still finishing while the user closes the window.
            engine.save()
        except OSError:
            logging.warning('無法在結束時儲存介面偏好。', exc_info=True)
        # A worker may still be matching; closing prevents any later click.
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    root.update_idletasks()
    root.geometry(f'{max(800, root.winfo_reqwidth())}x{max(640, root.winfo_reqheight())}')
    apply_theme(engine.theme)
    tick()
    root.mainloop()
    worker.join(timeout=5)
    engine.monitor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detect-image', type=Path, help='Offline detection; never sends input.')
    args = parser.parse_args()
    if not args.detect_image:
        from .startup import ensure_admin
        if not ensure_admin():
            return
    logging.basicConfig(filename=BASE/'clicker.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    if args.detect_image:
        for match in Detector().find(Image.open(args.detect_image)):
            print(match)
    else:
        from . import windows_api as api
        lock = api.InstanceLock(BASE)
        if not lock.acquire():
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, '寶箱助手已在執行，請使用已開啟的工具視窗。',
                'Wuwa Tappo 寶箱助手', 0x40)
            return
        try:
            gui()
        finally:
            lock.close()


if __name__ == '__main__':
    main()
