"""Console-free administrator entry point with visible startup errors."""
from pathlib import Path
import sys
import traceback


def launch():
    try:
        from wuwa_helper.startup import ELEVATED_FLAG, ensure_admin
        relaunched = sys.argv[1:2] == [ELEVATED_FLAG]
        if relaunched:
            del sys.argv[1]
        if not ensure_admin(relaunched=relaunched):
            return
        from wuwa_helper.main import main
        main()
    except Exception as exc:
        log = Path(__file__).resolve().parent / 'startup.log'
        try:
            with log.open('a', encoding='utf-8') as stream:
                stream.write(traceback.format_exc() + '\n')
            detail = f'錯誤紀錄：{log}'
        except OSError:
            detail = '無法寫入錯誤紀錄。'
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f'寶箱助手無法啟動：{exc}\n\n{detail}',
            'Wuwa Tappo 寶箱助手', 0x10,
        )
        raise SystemExit(1)


if __name__ == '__main__':
    launch()
