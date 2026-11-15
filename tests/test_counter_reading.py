"""Counter OCR regressions using local fixtures; no desktop capture or input."""
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from wuwa_helper.counter_reader import CounterReader, parse_number
from wuwa_helper.paths import PROJECT_ROOT


ASSETS = PROJECT_ROOT / 'assets'


class CounterReadingTests(unittest.TestCase):
    def image(self, name='counter_live.png'):
        with Image.open(ASSETS / name) as image:
            return image.convert('RGB')

    def test_original_fixtures_keep_exact_values(self):
        reader = CounterReader()
        for name, expected in [('counter_example.png', 7304),
                               ('counter_live.png', 1719),
                               ('counter_dark.png', 2027)]:
            with self.subTest(name=name):
                reading = reader.read(self.image(name))
                self.assertIsNotNone(reading)
                self.assertEqual(reading.value, expected)

    def test_shallow_red_sprite_pixels_do_not_become_letter_strokes(self):
        image = self.image()
        # A pet's red feet intrude just above and along the glyph tops. Masking
        # removes the red pixels without guessing or filling missing strokes.
        ImageDraw.Draw(image).rectangle((64, 13, 79, 17), fill=(184, 0, 28))
        reading = CounterReader().read(image)
        self.assertIsNotNone(reading)
        self.assertEqual(reading.value, 1719)

    def test_foreign_cover_over_a_whole_digit_is_not_a_shorter_number(self):
        for color in ((184, 0, 28), (15, 20, 28)):
            with self.subTest(color=color):
                image = self.image()
                ImageDraw.Draw(image).rectangle((29, 16, 43, 35), fill=color)
                ocr = Mock(return_value='719')
                self.assertIsNone(CounterReader(ocr=ocr).read(image))
                ocr.assert_not_called()

    def test_fully_covered_counter_is_rejected_before_ocr(self):
        for color in ((15, 20, 28), (252, 248, 236)):
            with self.subTest(color=color):
                image = self.image()
                ImageDraw.Draw(image).rectangle((15, 6, 175, 44), fill=color)
                ocr = Mock(return_value='1719')
                self.assertIsNone(CounterReader(ocr=ocr).read(image))
                ocr.assert_not_called()

    def test_two_valid_variants_must_still_agree(self):
        ocr = Mock(side_effect=['1719', '1718'])
        self.assertIsNone(CounterReader(ocr=ocr).read(self.image()))
        self.assertEqual(ocr.call_count, 2)

    def test_letter_contamination_is_not_guessed_as_digits(self):
        ocr = Mock(return_value='17ft61')
        self.assertIsNone(CounterReader(ocr=ocr).read(self.image()))
        self.assertIsNone(parse_number('17ft61'))
        self.assertIsNone(parse_number('I719'))


if __name__ == '__main__':
    unittest.main()
