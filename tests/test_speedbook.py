"""Speed book tests. Expected values read from the scanned speed book pages.

Run: python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from q400perf.calc import PerfError  # noqa: E402
from q400perf.speedbook import load_speedbook, pressure_altitude, takeoff_speeds  # noqa: E402

BOOK = load_speedbook()


class SpeedBookDataTest(unittest.TestCase):
    def test_pages(self):
        self.assertEqual(sorted(BOOK), list(range(18000, 30000, 500)))

    def test_page_values_and_colours(self):
        f5 = BOOK[21000]["flaps"][5]
        self.assertEqual((f5["v1vr"], f5["v2"], f5["vfri"], f5["v1vr_red"], f5["v2_red"]), (108, 114, 123, False, True))
        f15 = BOOK[22000]["flaps"][15]
        self.assertEqual((f15["v1vr"], f15["v2"], f15["v1vr_red"], f15["v2_red"]), (100, 105, False, True))
        f10 = BOOK[29500]["flaps"][10]
        self.assertEqual((f10["v1vr"], f10["v2"], f10["vfri"], f10["v2_red"]), (124, 126, 137, False))
        self.assertEqual(BOOK[18000]["min_v1vr"], {5: 108, 10: 104, 15: 100})
        self.assertEqual(BOOK[25500]["vclimb"], 147)

    def test_additive_tables(self):
        a = BOOK[18000]["v1vr_add"]          # stored 0 ft first
        self.assertEqual(a[5], [4, 4, 5, 6, 7, 8, 9])   # 10,000 ft
        self.assertEqual(a[0], [0, 0, 1, 1, 1, 2, 3])   # 0 ft
        self.assertEqual(BOOK[18000]["min_v2_add"][0], [7, 6, 6, 6, 5, 5, 4])
        self.assertEqual(BOOK[21500]["min_v2_add"][0], [2, 2, 2, 1, 1, 1, 0])
        self.assertNotIn("min_v2_add", BOOK[22500])     # no red V2 from 22,500 kg

    def test_monotonic_in_weight(self):
        ws = sorted(BOOK)
        for a, b in zip(ws, ws[1:]):
            for fl in (5, 10, 15):
                for k in ("v1vr", "v2", "vfri"):
                    self.assertLessEqual(BOOK[a]["flaps"][fl][k], BOOK[b]["flaps"][fl][k], (b, fl, k))


class SpeedCalcTest(unittest.TestCase):
    def test_pressure_altitude(self):
        self.assertAlmostEqual(pressure_altitude(0, 1013.25), 0, places=3)
        self.assertAlmostEqual(pressure_altitude(1000, 1003), 1000 + 280, delta=5)

    def test_black_speeds_with_interpolated_additive(self):
        # 26,300 -> 26,000 page, flaps 5: 123/127. PA 6183 ft, 28 °C: additive 4.7 -> +5.
        s = takeoff_speeds(BOOK, 26300, 5, 6183, 28, 28, max_v1=131)
        self.assertEqual((s.weight_page, s.v1, s.vr, s.v2, s.vfri, s.vclimb), (26000, 128, 128, 128, 137, 148))

    def test_red_speeds_minimums_and_v2_additive(self):
        # 18,000 page flaps 15: 91 red / 100 red. Sea level 15 °C: +1 -> 92 < min 100 -> 100.
        # V2 red: min V2 additive at 0 ft, 15 °C = 6 -> 106.
        s = takeoff_speeds(BOOK, 18200, 15, 0, 15, 15)
        self.assertEqual((s.v1, s.vr, s.v2, s.v2_additive), (100, 100, 106, 6))

    def test_assumed_temperature_for_v1vr_actual_oat_for_v2(self):
        # 20,000 page flaps 5: 105 red / 111 red. PA 0.
        s = takeoff_speeds(BOOK, 20000, 5, 0, 40, 10)
        self.assertEqual(s.v1vr_additive, 2)    # 40 °C column at 0 ft
        self.assertEqual(s.v2_additive, 5)      # 10 °C column at 0 ft on 20,000 page
        self.assertEqual((s.vr, s.v2), (108, 116))  # 107 < min 108 -> 108

    def test_v1_limited_by_max_v1(self):
        s = takeoff_speeds(BOOK, 29560, 5, 5337, 25, 25, max_v1=132)
        self.assertEqual((s.v1, s.vr, s.v2), (132, 137, 137))

    def test_limits(self):
        with self.assertRaises(PerfError):
            takeoff_speeds(BOOK, 17900, 5, 0, 15, 15)
        with self.assertRaises(PerfError):
            takeoff_speeds(BOOK, 25000, 5, 10500, 15, 15)
        with self.assertRaises(PerfError):
            takeoff_speeds(BOOK, 25000, 5, 0, 51, 15)
        with self.assertRaises(PerfError):
            takeoff_speeds(BOOK, 25000, 35, 0, 15, 15)

    def test_cli_speeds(self):
        out = subprocess.run([sys.executable, "-m", "q400perf", "calc", "BJR", "22", "--oat", "28", "--qnh", "1008",
                              "--wind", "-5", "--tow", "26300"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout
        self.assertIn("V1 / VR / V2   128 / 128 / 128 kt", out)


if __name__ == "__main__":
    unittest.main()
