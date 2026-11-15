"""Read the visible Wuwa Tappo counter using a local Windows OCR engine."""
import asyncio
from dataclasses import dataclass
import re
import threading
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageOps

from .detector import Detector
from .paths import PROJECT_ROOT


@dataclass(frozen=True)
class CounterReading:
    value: int
    rect: tuple[int, int, int, int]


def parse_number(text):
    """Accept digits and conventional separators, never guess letters as digits."""
    clean = unicodedata.normalize('NFKC', text).strip()
    if not clean or not re.fullmatch(r'[0-9][0-9,\s]*', clean):
        return None
    digits = re.sub(r'[,\s]', '', clean)
    if not 1 <= len(digits) <= 7:
        return None
    return int(digits)


class WindowsOCR:
    """Each calling thread owns its own WinRT engine and apartment."""
    def __init__(self):
        self.local = threading.local()

    def __call__(self, image):
        from winrt.runtime import ApartmentType, init_apartment
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        if not hasattr(self.local, 'engine'):
            try:
                init_apartment(ApartmentType.MULTI_THREADED)
            except OSError as exc:
                # An existing STA (for example a UI thread) can also use OCR.
                if getattr(exc, 'winerror', 0) & 0xffffffff != 0x80010106:
                    raise
            self.local.engine = (OcrEngine.try_create_from_language(Language('en-US'))
                                 or OcrEngine.try_create_from_user_profile_languages())
            if self.local.engine is None:
                raise RuntimeError('找不到 Windows OCR 語言，請安裝 Windows 英文或中文 OCR 語言功能。')
        rgba = image.convert('RGBA')
        writer = DataWriter()
        try:
            writer.write_bytes(rgba.tobytes('raw', 'BGRA'))
            buffer = writer.detach_buffer()
            bitmap = SoftwareBitmap.create_copy_from_buffer(
                buffer, BitmapPixelFormat.BGRA8, rgba.width, rgba.height, BitmapAlphaMode.IGNORE)
        finally:
            writer.close()

        async def recognize():
            result = await asyncio.wait_for(self.local.engine.recognize_async(bitmap), timeout=3)
            return result.text

        try:
            return asyncio.run(recognize())
        finally:
            bitmap.close()


class CounterReader:
    """Locate the star/menu anchor, then require two OCR variants to agree."""
    def __init__(self, ocr=None, locator=None):
        self.ocr = ocr if ocr is not None else WindowsOCR()
        self.locator = locator if locator is not None else Detector(
            PROJECT_ROOT / 'assets/counter_anchor.png', threshold=.90,
            color_error=None)

    def read(self, image):
        image = image.convert('RGB')
        anchors = self.locator.find(image)
        if len(anchors) != 1:
            return None
        anchor = anchors[0]
        sx, sy = anchor.width / 51, anchor.height / 34
        # Reference anchor starts at (1604, 484); digits occupy the pill to its left.
        rect = (round(anchor.x - 116*sx), round(anchor.y + 4*sy),
                round(anchor.x - 8*sx), round(anchor.y + 30*sy))
        if rect[0] < 0 or rect[1] < 0 or rect[2] > image.width or rect[3] > image.height:
            return None
        crop = image.crop(rect)
        rgb = np.asarray(crop)
        # Counter background is pale cream; reject a covered/transparent panel.
        pale = (rgb[:, :, 0] > 205) & (rgb[:, :, 1] > 200) & (rgb[:, :, 2] > 175)
        if pale.mean() < .55:
            return None
        # Pet sprites can protrude into the pill above the digits. Their red or
        # gray pixels must not become letter strokes during grayscale OCR. Keep
        # only the counter's brown ink, including its darker antialiased edges;
        # this removes noise, never reconstructs a covered part of a digit.
        channels = rgb.astype(np.int16)
        red, green, blue = channels[:, :, 0], channels[:, :, 1], channels[:, :, 2]
        brown = ((red-green > 12) & (green-blue > 8) & (green > 60)
                 & (blue < 190) & (red < 225) & (green > red*.38))
        cleaned = np.full_like(rgb, 255)
        cleaned[brown] = rgb[brown]
        cleaned = Image.fromarray(cleaned)
        # Locate the ink rather than scaling the entire mostly-empty pill. The
        # Windows recognizer can return no text for large, sparse numeric lines.
        # Drop components touching the ROI edge: those are rounded pill edges or
        # background pixels, not digits inside the counter.
        gray_pixels = np.asarray(cleaned.convert('L'))
        ink = (gray_pixels < 190).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
        letters = []
        for index in range(1, count):
            x, y, width, height, area = stats[index]
            if (x > 0 and y > 0 and x+width < crop.width and y+height < crop.height
                    and height >= max(5, crop.height*.20) and area >= 5):
                letters.append((x, y, x+width, y+height))
        if not letters:
            return None
        left, top = min(b[0] for b in letters), min(b[1] for b in letters)
        right, bottom = max(b[2] for b in letters), max(b[3] for b in letters)
        ink_height = bottom-top
        # Large foreign marks through the digit body are an occlusion, not
        # harmless sprite pixels. Do not silently read the remaining digits as
        # a shorter number after removing a covered glyph.
        foreign = ((np.asarray(crop.convert('L')) < 190) & ~brown).astype(np.uint8)
        foreign[:top, :] = 0
        foreign[bottom:, :] = 0
        components, _, marks, _ = cv2.connectedComponentsWithStats(foreign, 8)
        for index in range(1, components):
            x, y, width, height, area = marks[index]
            if (x > 0 and x+width < crop.width and height >= ink_height*.6
                    and width >= max(2, ink_height*.15) and area >= ink_height):
                return None
        margin = max(2, round(ink_height*.12))
        tight = cleaned.crop((max(0, left-margin), max(0, top-margin),
                          min(crop.width, right+margin), min(crop.height, bottom+margin)))
        gray = ImageOps.autocontrast(tight.convert('L'))
        binary = Image.fromarray(cv2.threshold(np.asarray(tight.convert('L')), 0, 255,
                                               cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
        # A moderate glyph height with generous padding avoids Windows OCR's
        # empty result for otherwise clean, dense six-digit numeric lines.
        scale = 32 / ink_height
        values = []
        for variant in (gray, binary):
            enlarged = variant.resize((max(16, round(variant.width*scale)),
                                       max(16, round(variant.height*scale))),
                                      Image.Resampling.LANCZOS)
            padded = ImageOps.expand(enlarged, border=24, fill='white')
            value = parse_number(self.ocr(padded))
            if value is None:
                return None
            values.append(value)
        if values[0] != values[1]:
            return None
        return CounterReading(values[0], rect)
