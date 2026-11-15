"""Read-only counter diagnostics; never sends keyboard or mouse input."""
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from wuwa_helper.capture import grab_region
from wuwa_helper.counter_reader import CounterReader, WindowsOCR
from wuwa_helper import windows_api as api
from wuwa_helper.paths import PROJECT_ROOT


def main():
    output = PROJECT_ROOT / 'diagnostics'
    output.mkdir(exist_ok=True)
    targets = api.targets()
    if len(targets) != 1:
        raise RuntimeError(f'Expected one game window, got {len(targets)}')
    target = targets[0]
    image = grab_region(target.rect)
    ocr = WindowsOCR()

    def trace_ocr(variant):
        value = ocr(variant)
        print('OCR', repr(value), 'size', variant.size, flush=True)
        return value

    reader = CounterReader(ocr=trace_ocr)
    anchors = reader.locator.find(image)
    print('target', target, 'anchors', anchors, flush=True)
    for index, anchor in enumerate(anchors):
        sx, sy = anchor.width / 51, anchor.height / 34
        roi = (max(0, round(anchor.x-145*sx)), max(0, round(anchor.y-16*sy)),
               min(image.width, round(anchor.x+65*sx)), min(image.height, round(anchor.y+48*sy)))
        crop = image.crop(roi)
        crop.save(output / f'counter_probe_{index}.png')
        print('local', roi, 'anchors', reader.locator.find(crop), flush=True)
        try:
            print('local_reading', reader.read(crop), flush=True)
        except Exception:
            traceback.print_exc()
    print('full_reading', reader.read(image), flush=True)


if __name__ == '__main__':
    main()
