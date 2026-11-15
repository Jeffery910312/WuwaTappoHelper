"""Preview appearance using sample counts, temporary state and no input hooks."""
import argparse
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wuwa_helper import main as app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--theme', choices=('light', 'dark'), default='light')
    parser.add_argument('--language', choices=('zh-TW', 'en'), default='zh-TW')
    args = parser.parse_args()
    original_engine, original_tk = app.Engine, tk.Tk

    class PreviewRoot(original_tk):
        def title(self, text=None):
            if text is None:
                return super().title()
            return super().title('WuwaTappo Helper · Appearance Preview')

    with tempfile.TemporaryDirectory(prefix='wuwa-appearance-') as temporary:
        class PreviewEngine(original_engine):
            def __init__(self, events, **_options):
                monitor = SimpleNamespace(
                    running=True, error=None, close=lambda: None,
                    snapshot=lambda: dict(total=0, keyboard=0, mouse=0))
                super().__init__(events, api=SimpleNamespace(), detector=object(),
                                 reader=object(), grab=lambda _rect: None,
                                 monitor=monitor, state_path=Path(temporary) / 'runtime.json')
                self.theme = args.theme
                self.language = args.language
                self.auto_keys = False
                self.count_base = 12864
                self.game_count = 12864
                self.count = 2
                self.next_scan = time.time() + 17 * 60 + 42

            def run(self):
                # Settings work, but previews never capture, hook or send input.
                while not self.closed.is_set():
                    self.apply_commands()
                    self.closed.wait(.05)

        try:
            app.Engine, tk.Tk = PreviewEngine, PreviewRoot
            app.gui()
        finally:
            app.Engine, tk.Tk = original_engine, original_tk


if __name__ == '__main__':
    main()
