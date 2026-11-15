"""Wuwa Tappo accumulator and chest clicker. F5 toggles start/pause."""
import argparse
import math
import json
import logging
from pathlib import Path
import queue
import threading
import time

from PIL import Image

from detector import Detector

BASE = Path(__file__).resolve().parent
COOLDOWN = 30 * 60
KEYS = tuple(f'F{i}' for i in range(13, 25))


class Engine:
    def __init__(self, events, detector=None, api=None, grab=None, state_path=None, reader=None, monitor=None):
        if api is None:
            import windows_api as api
        self.api = api
        self.detector = detector or Detector()
        # Hover transparency changes the gold icon's color. Verification must
        # still see that icon instead of treating a color change as a claim.
        self.verifier = detector or Detector(color_error=None)
        if reader is None:
            from counter_reader import CounterReader
            reader = CounterReader()
        self.reader = reader
        if grab is None:
            from capture import grab_region
            grab = grab_region
        self.grab = grab
        if monitor is None:
            from input_monitor import InputMonitor
            monitor = InputMonitor()
        self.monitor = monitor
        self.counter_inputs = self.monitor.snapshot()['total']
        self.counter_region = None
        self.region_target = None
        self.events = events
        self.commands = queue.Queue()
        self.active = threading.Event()
        self.closed = threading.Event()
        self.state_path = state_path or BASE / 'runtime.json'
        self._save_lock = threading.Lock()
        self._input_lock = threading.RLock()
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
        if self.game_count is None:
            return None
        return self.game_count + max(0, self.monitor.snapshot()['total'] - self.counter_inputs)

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
            from detector import Match
            return [Match(m.x+left, m.y+top, m.width, m.height, m.score) for m in matches]
        return matcher.find(self.grab(target.rect))

    def observe_counter(self, target=None, force=False):
        now = time.monotonic()
        if not force and now < self.next_read:
            return
        self.next_read = now + (30 if not getattr(self.monitor, 'error', None) else .8)
        if target is None:
            windows = self.api.targets()
            if len(windows) != 1:
                self.game_count = None
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
            self.game_count = None
            if self.allowed():
                self.emit('暫時讀不到計數器，領箱將自動重試；自動按鍵依開關持續。')
            return
        value = reading.value
        if self.game_count is not None and value != self.game_count:
            self.stalled_keys = 0
        self.game_count = value
        self.counter_inputs = inputs
        self.counter_read_at = now
        if self.read_failures and self.allowed():
            self.emit('計數辨識已恢復，自動接續。')
        self.read_failures = 0

    @staticmethod
    def validate(key, boxes, rate):
        if key not in KEYS or type(boxes) is not int or boxes not in (1, 2):
            raise ValueError('請選 F13～F24，以及 1 或 2 個寶箱。')
        if type(rate) is not int or not 1 <= rate <= 10:
            raise ValueError('每秒次數必須是 1～10 的整數。')

    def request_settings(self, key, boxes, rate):
        self.validate(key, boxes, rate)
        self.pause()
        self.commands.put(('settings', key, boxes, rate))

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
            if command == 'features':
                self.save()
                self.emit(f'自動按鍵：{"開" if self.auto_keys else "關"}；自動領箱：{"開" if self.auto_claim else "關"}。F5 總開關保持原狀。')
                continue
            self.pause()
            self.test_remaining = 0
            if command == 'settings':
                self.key, self.boxes, self.rate = values
                self.save()
                self.emit('設定已儲存，按 F5 開始。以 Wuwa Tappo 畫面計數判斷領取條件。')
            elif command == 'test':
                self.test_remaining, self.test_sent = 20, 0
                self.active.set()
                self.emit('短測試：只送出 20 次按鍵後自動暫停，不領寶箱。')
            elif command == 'reset':
                if self.round_count or self.claim_pending or self.next_scan > time.time():
                    self.emit('已有領取進度或仍在冷卻，保留現有進度。')
                else:
                    self.sent = 0
                    self.save()
                    self.emit('累積診斷已重設；畫面計數仍以 Wuwa Tappo 為準。')

    def save(self):
        # Windows file scanners/readers can briefly prevent atomic replacement.
        # Serialize writers and retain the previous complete state on failure.
        with self._save_lock:
            saved_sent = self.sent
            payload = json.dumps(dict(next_scan=self.next_scan, key=self.key,
                                boxes=self.boxes, rate=self.rate, sent=saved_sent,
                                auto_keys=self.auto_keys, auto_claim=self.auto_claim,
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
        return self.allowed() and self.auto_claim and not self.claim_blocked

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
                self.emit(f'短測試完成：已送出 {self.test_sent} 次 {self.key}，已自動暫停。請確認遊戲計數。')

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
                self.claim_issue = '第二個寶箱仍未確認，領箱暫停並保留進度；切換領箱開關或 F5 重試。自動按鍵依開關持續。'
                self.emit(self.claim_issue)
                return False
        self.observe_counter(target)
        if (not self.claims_allowed() or self.game_count is None
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
                self.emit(f'遊戲計數已達 {self.game_count}，等待可點擊寶箱；自動按鍵依開關持續。')
            return False
        # Avoid repeated OCR once the total is high but no chest is present.
        # Estimates only trigger verification; actual OCR must authorize input.
        if now-self.counter_read_at > 1 or self.monitor.snapshot()['total'] != self.counter_inputs:
            self.observe_counter(target, force=True)
        if not self.claims_allowed() or self.game_count is None or self.game_count < self.required_count:
            self.claim_waiting = False
            return False
        self.claim_waiting = True
        if self.api.idle_seconds() < 1.2 or self.api.held_keys():
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
            # The callback observes the current feature switch even mid-click.
            if not self.api.click(target, x, y, self.claims_allowed):
                self.claim_waiting = False
                return False
            self.next_scan = time.time() + COOLDOWN
            self.claim_pending = True
            self.save()
            self.last_point = (x, y)
            self.emit(f'已送出寶箱點擊 ({x}, {y})，正在確認畫面。')
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
                self.emit('點擊後寶箱仍在，結果未確認；保留冷卻，自動按鍵依開關持續。')
                return True
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
    import windows_api as api
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title('Wuwa Tappo 寶箱助手')
    root.geometry('760x730')
    root.minsize(740, 710)
    root.configure(bg='#f9f2e7')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TFrame', background='#f9f2e7')
    style.configure('TLabel', background='#f9f2e7', foreground='#573b2a', font=('Microsoft JhengHei UI', 11))
    style.configure('TButton', font=('Microsoft JhengHei UI', 11), padding=9)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Wuwa Tappo 寶箱助手', font=('Microsoft JhengHei UI', 20, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='自動按鍵與自動領箱獨立控制；F5 為總開關').pack(anchor='w', pady=(6, 18))
    events = queue.Queue()
    engine = Engine(events, api=api)
    hotkeys = api.Hotkeys()
    status = tk.StringVar(value='已暫停')
    countdown = tk.StringVar(value='')
    counter = tk.StringVar(value='本次已確認消失的寶箱：0')
    detail = tk.StringVar(value='選好設定並套用，再按 F5 或「開始」。')
    progress = tk.StringVar()
    estimate_label = tk.StringVar()
    switches = ttk.Frame(frame)
    switches.pack(fill='x', pady=(0, 12))
    auto_keys = tk.BooleanVar(value=engine.auto_keys)
    auto_claim = tk.BooleanVar(value=engine.auto_claim)

    def features():
        engine.request_features(auto_keys.get(), auto_claim.get())

    ttk.Checkbutton(switches, text='自動按鍵（持續執行）', variable=auto_keys, command=features).pack(side='left', padx=(0, 20))
    ttk.Checkbutton(switches, text='自動領取寶箱', variable=auto_claim, command=features).pack(side='left')
    settings = ttk.Frame(frame)
    settings.pack(fill='x', pady=(0, 14))
    key = tk.StringVar(value=engine.key)
    boxes = tk.StringVar(value=str(engine.boxes))
    rate = tk.StringVar(value=str(engine.rate))
    for column, (label, variable, choices) in enumerate((('累積按鍵', key, KEYS), ('寶箱數', boxes, (1, 2)), ('每秒次數', rate, tuple(range(1, 11))))):
        ttk.Label(settings, text=label).grid(row=0, column=column, sticky='w', padx=(0, 14))
        ttk.Combobox(settings, textvariable=variable, values=choices, state='readonly', width=10).grid(row=1, column=column, padx=(0, 14), pady=6)

    def apply():
        try:
            engine.request_settings(key.get(), int(boxes.get()), int(rate.get()))
        except ValueError as exc:
            detail.set(str(exc))

    ttk.Button(settings, text='套用並暫停', command=apply).grid(row=1, column=3)
    ttk.Label(frame, textvariable=status, font=('Microsoft JhengHei UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(frame, textvariable=progress).pack(anchor='w', pady=5)
    ttk.Label(frame, textvariable=estimate_label).pack(anchor='w', pady=5)
    bar = ttk.Progressbar(frame, maximum=engine.goal)
    bar.pack(fill='x', pady=5)
    ttk.Label(frame, textvariable=countdown).pack(anchor='w', pady=5)
    ttk.Label(frame, textvariable=counter).pack(anchor='w', pady=5)
    ttk.Label(frame, textvariable=detail, wraplength=580).pack(anchor='w', pady=(10, 15))
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x')
    toggle = ttk.Button(buttons, text='開始（F5）', command=engine.toggle)
    toggle.pack(side='left', padx=(0, 10))
    ttk.Button(buttons, text='測試 20 次', command=engine.request_test).pack(side='left', padx=(0, 10))
    ttk.Label(frame, text='自動按鍵持續執行，達標與領箱冷卻期間也會繼續。\n實際可領箱時會短暫停止按鍵，等待閒置後領取。\n關閉自動按鍵可只靠日常操作累積；F5 控制全部開始／暫停。', wraplength=620).pack(anchor='w', pady=(18, 0))
    if len(hotkeys.registered) != 1:
        ttk.Label(frame, text='快捷鍵被占用：請使用開始／暫停按鈕。').pack(anchor='w')
    worker = threading.Thread(target=engine.run, name='chest-worker', daemon=True)
    worker.start()

    def fit_content():
        root.update_idletasks()
        width = max(root.winfo_width(), root.winfo_reqwidth())
        height = max(root.winfo_height(), root.winfo_reqheight())
        if width > root.winfo_width() or height > root.winfo_height():
            root.geometry(f'{width}x{height}')

    detail.trace_add('write', lambda *_: root.after_idle(fit_content))

    def tick():
        for ident in hotkeys.poll():
            if ident == 1:
                engine.toggle()
        while not events.empty():
            detail.set(events.get_nowait())
        if (engine.auto_claim and engine.claim_blocked and engine.claim_issue
                and detail.get() != engine.claim_issue):
            detail.set(engine.claim_issue)
        running = engine.active.is_set()
        if not engine.auto_keys and not engine.auto_claim:
            phase = '兩項功能皆關閉'
        elif engine.auto_claim and engine.claim_blocked:
            phase = '自動按鍵中／領箱已暫停' if engine.auto_keys else '領箱已暫停，請查看提示'
        elif engine.claim_waiting:
            phase = '暫停自動按鍵，準備領箱'
        elif engine.auto_keys:
            phase = '自動按鍵中' + ('／領箱冷卻中' if engine.next_scan > time.time() else '')
        elif engine.game_count is None:
            phase = '等待計數辨識（自動重試）'
        else:
            phase = '領箱冷卻中' if engine.next_scan > time.time() else '等待／領取寶箱'
        if engine.test_remaining:
            phase = f'短測試 {engine.test_sent} / 20'
        status.set(phase if running else '已暫停')
        toggle.configure(text='暫停（F5）' if running else '開始（F5）')
        value = '辨識中／不可見' if engine.game_count is None else str(engine.game_count)
        progress.set(f'Wuwa Tappo 辨識值：{value}　｜　門檻：{engine.required_count} 次')
        estimate = engine.estimated_count
        stats = engine.monitor.snapshot()
        estimate_label.set(f'點擊數：{estimate if estimate is not None else "待辨識"}　｜　偵測鍵盤 {stats["keyboard"]}／滑鼠 {stats["mouse"]}')
        bar.configure(maximum=max(1, engine.required_count), value=min(estimate or 0, engine.required_count))
        remaining = max(0, int(engine.next_scan-time.time()+.999))
        countdown.set(f'冷卻剩餘 {remaining//60:02d}:{remaining%60:02d}' if remaining else '冷卻結束，可檢查寶箱')
        counter.set(f'已確認消失：{engine.count} 個寶箱　｜　目標：{engine.boxes} 個（累積鍵 {engine.key}）')
        root.after(100, tick)

    def close():
        engine.pause()
        engine.closed.set()
        hotkeys.close()
        # A worker may still be matching; closing prevents any later click.
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    root.update_idletasks()
    root.geometry(f'{max(760, root.winfo_reqwidth())}x{max(730, root.winfo_reqheight())}')
    tick()
    root.mainloop()
    worker.join(timeout=5)
    engine.monitor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detect-image', type=Path, help='Offline detection; never sends input.')
    args = parser.parse_args()
    if not args.detect_image:
        from startup import ensure_admin
        if not ensure_admin():
            return
    logging.basicConfig(filename=BASE/'clicker.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    if args.detect_image:
        for match in Detector().find(Image.open(args.detect_image)):
            print(match)
    else:
        import windows_api as api
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
