"""RSI / ATR / 格式化工具的单元测试（不连网、不需要密钥）。
运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rsi_bot_v3 import (
    BTC_DUST_QTY,
    _btc_has_position,
    _tp_sl_qty_s,
    calc_atr,
    calc_rsi_ema,
    calc_rsi_wilder,
    floor_qty,
    fmt_price,
    fmt_qty,
)


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


class TestBtcPosition(unittest.TestCase):
    def test_dust_treated_as_no_position(self):
        self.assertFalse(_btc_has_position(0.0))
        self.assertFalse(_btc_has_position(0.0004))
        self.assertFalse(_btc_has_position(-0.0004))
        self.assertFalse(_btc_has_position(BTC_DUST_QTY))

    def test_real_position_detected(self):
        self.assertTrue(_btc_has_position(0.001))
        self.assertTrue(_btc_has_position(-0.05))


class TestTpSlQty(unittest.TestCase):
    def test_partial_fill_uses_held(self):
        # 市价单部分成交：止盈按实际持仓挂单，避免 -2022 拒单
        self.assertEqual(_tp_sl_qty_s(2.5, 2.4, 2), "2.40")

    def test_full_fill_uses_requested(self):
        self.assertEqual(_tp_sl_qty_s(2.5, 2.5, 2), "2.50")

    def test_held_unknown_falls_back_to_requested(self):
        # 持仓查询失败（held=0）：回退到请求数量，保持旧行为
        self.assertEqual(_tp_sl_qty_s(2.5, 0.0, 2), "2.50")

    def test_held_above_requested_uses_requested(self):
        # 反手残留余量导致持仓略大于请求：仍按请求数量
        self.assertEqual(_tp_sl_qty_s(2.5, 2.5078, 2), "2.50")

    def test_zero_precision(self):
        self.assertEqual(_tp_sl_qty_s(8000.7, 8000.0, 0), "8000")


if __name__ == "__main__":
    unittest.main()
