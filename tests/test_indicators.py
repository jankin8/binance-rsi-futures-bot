"""RSI / ATR / 格式化工具的单元测试（不连网、不需要密钥）。
运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsi_bot_v3 import calc_atr, calc_rsi_ema, calc_rsi_wilder, floor_qty, fmt_price, fmt_qty


class TestRSI(unittest.TestCase):
    def test_wilder_all_up_is_100(self):
        closes = [float(i) for i in range(1, 40)]
        self.assertEqual(calc_rsi_wilder(closes), 100.0)

    def test_wilder_all_down_is_0(self):
        closes = [float(100 - i) for i in range(40)]
        self.assertEqual(calc_rsi_wilder(closes), 0.0)

    def test_wilder_too_short(self):
        self.assertIsNone(calc_rsi_wilder([1.0, 2.0, 3.0]))

    def test_ema_all_up_near_100(self):
        closes = [float(i) for i in range(1, 60)]
        self.assertGreater(calc_rsi_ema(closes), 99.0)


class TestATR(unittest.TestCase):
    def test_atr_constant_range(self):
        closes = [float(100 + i) for i in range(30)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        self.assertAlmostEqual(calc_atr(highs, lows, closes), 2.0)

    def test_atr_too_short(self):
        self.assertIsNone(calc_atr([1, 2], [1, 2], [1, 2]))


class TestFormat(unittest.TestCase):
    def test_floor_qty_never_rounds_up(self):
        self.assertEqual(floor_qty(1.999, 2), 1.99)
        self.assertEqual(floor_qty(0.1239, 3), 0.123)

    def test_fmt(self):
        self.assertEqual(fmt_qty(12.0, 0), "12")
        self.assertEqual(fmt_qty(1.239, 2), "1.23")
        self.assertEqual(fmt_price(100.126, 2), "100.13")


if __name__ == "__main__":
    unittest.main()
