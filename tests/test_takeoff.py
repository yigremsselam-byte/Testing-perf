"""Tests against values read directly from the handbook pages.

Run: python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from q400perf.calc import PerfError, assumed_temperature, calculate, find_runway, load, select_tables  # noqa: E402

RUNWAYS = load()

# Takeoff pages listed in the List of Effective Pages (LEP 1-3, revision 26-09).
LEP_TAKEOFF = """
ADD 07L|ADD 07L/B1|ADD 07L/C1|ADD 07R|ADD 07R/B2|ADD 07R/C2|ADD 07R/M|ADD 25L|ADD 25L/C2|ADD 25L/D|
ADD 25L/N|ADD 25R|ADD 25R/C1|ADD 25R/D|ADD 25R/G|ADD 25R/H|ADD 25R/J|ADD 25R/K|ADD 25R/L|ADD 25R/N|
AMH 03|AMH 21|ASO 11|ASO 11/TWY|ASO 29|AXU 16|AXU 34|BJR 04|BJR 04/LOOP|BJR 22|BIB 04|BIB 22|BBO 05|
BBO 23|BSA 09|BSA 27|DAR 05|DAR 23|DEM 15|DEM 15/INTX|DEM 33|DIR 15|DIR 33|DIR 33/R|JIB 09|JIB 27|
EBB 12|EBB 17|EBB 17/A3|EBB 30|EBB 35|EBB 35/A2|GMB 18|GMB 36|GDE 04|GDE 04-SW1548M|GDE 04-SW700M|
GDE 22|GOM 17|GOM 35|GDQ 17|HGA 06|HGA 06/TWY|HGA 24|HGA 24/TWY|AWA 01|AWA 01/TWY|AWA 01V|
AWA 01V/TWY|AWA 19|AWA 19V|HUE 13|HUE 31|JIJ 03|JIJ 21|JIM 13|JIM 31|BCO 12|BCO 30|JUB 13|JUB 13/B|
JUB 31|JUB 31/D|JUB 31/E|KDX 06|KDX 24|ABK 08|ABK 26|KRT 18|KRT 36|KGL 10|KGL 28|KMU 04|KMU 22|
DSE 17|LLI 10|LLI 28|MAK 04|MAK 22|MQX 11|MQX 29|MGQ 05|MGQ 05/A|MGQ 23|MBA 03|MBA 21|NBO 06|
NBO 24|NEK 06|NEK 24|UYL 04|UYL 22|PZU 16|PZU 34|GOB 15|GOB 33|SZE 13|SZE 31|SHC 15|SHC 33
"""


def table(rw_id, bleeds, condition, flaps):
    rw = next(r for r in RUNWAYS if r["id"] == rw_id)
    return next(t for t in rw["tables"]
                if t["bleeds"] == bleeds and t["condition"] == condition and t["flaps"] == flaps)


def row(t, oat):
    return next(r for r in t["rows"] if r["oat"] == oat)


def c(w, lim, v1):
    return {"mrtw_kg": w, "limit": lim, "max_v1": v1}


class ExtractionTest(unittest.TestCase):
    def test_every_lep_runway_present_in_handbook_order(self):
        expected = [s.strip() for s in LEP_TAKEOFF.replace("\n", "").split("|")]
        self.assertEqual([r["id"] for r in RUNWAYS], expected)

    def test_every_runway_has_engine_failure_procedure_and_footer(self):
        for r in RUNWAYS:
            self.assertIn("engine_failure", r, r["id"])
            for k in ("elevation", "length", "slope"):
                self.assertIn(k, r["meta"], r["id"])

    def test_bjr_04_loop_page1_bleeds_on(self):
        t = table("BJR 04/LOOP", "ON", "DRY - NTOP", 5)
        r = row(t, 26)
        self.assertEqual(r["torque"], 77.8)
        self.assertEqual(r["climb_limit_kg"], 27090)
        self.assertEqual(r["cells"], [c(25300, "^", 127), c(27090, "C", 130), c(27090, "C", 127)])
        self.assertEqual(t["qnh"], {"high": 0, "low": -33})
        w = table("BJR 04/LOOP", "ON", "WET - NTOP", 5)
        self.assertEqual(row(w, 26)["cells"][0], c(25300, "^", 116))

    def test_bjr_04_loop_tailwind_table(self):
        t = table("BJR 04/LOOP", "OFF", "NTOP DRY - TW > 10KTS", 10)
        self.assertEqual(t["winds"], [-20, -17, -14, -11])
        r = row(t, 36)
        self.assertEqual(r["torque"], 72.1)
        self.assertEqual(r["cells"][0], {"mrtw_kg": 24380, "limit": "^", "v1": 115, "vr": 115, "v2": 115})
        self.assertEqual(r["cells"][3], {"mrtw_kg": 26000, "limit": "C", "v1": 119, "vr": 120, "v2": 120})

    def test_page2_blank_cells_and_v1_above_100(self):
        t = table("BJR 04/LOOP", "OFF", "DRY / GEAR EXTENDED", 10)
        self.assertNotIn(36, [r["oat"] for r in t["rows"]])          # blank row
        self.assertEqual(row(t, 26)["cells"], [c(21490, "C", 100)] * 3)  # "2149C 0" -> 100 kt
        self.assertEqual(row(t, 24)["cells"][0], c(21930, "^", 101))
        self.assertEqual(t["qnh"], {"high": 0, "low": -31})
        w = table("BJR 04/LOOP", "OFF", "WET / GEAR EXTENDED", 10)
        self.assertEqual(row(w, 28)["cells"], [None, c(21020, "C", 96), c(21020, "C", 96)])

    def test_gode_shortened_runway(self):
        t = table("GDE 04-SW1548M", "OFF", "WET / NW STEER INOP", 5)
        self.assertEqual(row(t, 28)["cells"], [None, None, c(18340, "F", 97)])
        self.assertEqual(row(t, 0)["cells"], [None, c(19840, "F", 97), c(20910, "F", 99)])
        self.assertEqual(t["qnh"], {"high": 16, "low": -26})
        g = table("GDE 04-SW1548M", "OFF", "DRY / GND SPLRS INOP", 15)
        self.assertEqual(row(g, 24)["cells"], [None, c(19500, "V", 96), c(23020, "F", 98)])
        self.assertEqual(g["qnh"], {"high": 11, "low": -123})

    def test_lalibela_padded_speeds(self):
        t = table("LLI 10", "OFF", "DRY - NTOP", 5)
        self.assertEqual(row(t, 36)["cells"], [c(19630, "^", 109), c(20770, "^", 113), c(21110, "^", 114)])
        w = table("LLI 10", "OFF", "WET - NTOP", 5)
        self.assertEqual(row(w, 36)["cells"][0], c(19630, "^", 98))
        self.assertEqual(row(w, 32)["cells"][0], c(20430, "^", 100))

    def test_axum_flap_variant(self):
        t = table("AXU 16", "ON", "WET - NTOP", 10)
        self.assertEqual(row(t, 4)["cells"][0], c(25990, "F", 107))
        self.assertEqual(row(t, 25)["cells"][0], c(22580, "^", 99))


def calc(rw, oat, **kw):
    sel = {k: kw.pop(k) for k in ("surface", "bleeds", "power", "config", "flaps") if k in kw}
    r = find_runway(RUNWAYS, *rw.split(" ", 1))
    (t,) = select_tables(r, wind=kw.get("wind", 0.0), **sel)
    return calculate(r, t, oat, **kw)


class CalculatorTest(unittest.TestCase):
    def test_exact_chart_point(self):
        r = calc("BJR 04/LOOP", 26, bleeds="on")
        self.assertEqual((r.mrtw_kg, r.limit, r.max_v1, r.torque), (27090, "C", 130, 77.8))
        r = calc("BJR 04/LOOP", 26, bleeds="on", wind=-10)
        self.assertEqual((r.mrtw_kg, r.limit, r.max_v1), (25300, "^", 127))

    def test_interpolation_and_rounding(self):
        # 25 °C, -5 kt: between 24/26 °C and -10T/+0 -> mean of 2579^28, 2763C31, 2530^27, 2709C30
        r = calc("BJR 04/LOOP", 25, bleeds="on", wind=-5)
        self.assertEqual(r.mrtw_kg, (25790 + 27630 + 25300 + 27090) // 4)
        self.assertEqual(r.max_v1, 129)  # (128+131+127+130)/4 = 129.0
        self.assertEqual(r.limit, "^")

    def test_low_qnh_adjustment_kg_per_hpa(self):
        r = calc("BJR 04/LOOP", 26, bleeds="on", qnh=1003)
        self.assertEqual(r.qnh_adjustment_kg, -330)
        self.assertEqual(r.mrtw_kg, 27090 - 330)

    def test_high_qnh_only_with_credit(self):
        g = calc("GDE 04-SW1548M", 0, config="nws-inop", surface="wet", qnh=1020)
        self.assertEqual(g.qnh_adjustment_kg, 0)
        g = calc("GDE 04-SW1548M", 0, config="nws-inop", surface="wet", qnh=1020, high_qnh_credit=True)
        self.assertEqual(g.qnh_adjustment_kg, 16 * 7)

    def test_tailwind_over_10_uses_supplement_3_table(self):
        r = calc("BJR 04/LOOP", 36, wind=-15)
        self.assertEqual(r.flaps, 10)
        self.assertEqual(r.mrtw_kg, int(24950 + (25540 - 24950) * 2 / 3))
        self.assertEqual((r.v1, r.vr, r.v2), (117, 118, 118))  # V1 rounded down, VR/V2 up

    def test_tailwind_over_10_rejected_for_other_configs(self):
        with self.assertRaises(PerfError):
            calc("BJR 04/LOOP", 20, wind=-12, surface="wet")
        with self.assertRaises(PerfError):
            calc("BJR 04/LOOP", 20, wind=-21)

    def test_headwind_capped_at_10_and_optional(self):
        a = calc("BJR 04/LOOP", 26, bleeds="on", wind=25)
        b = calc("BJR 04/LOOP", 26, bleeds="on", wind=10)
        self.assertEqual(a.mrtw_kg, b.mrtw_kg)
        n = calc("BJR 04/LOOP", 26, bleeds="on", wind=10, headwind_credit=False)
        self.assertEqual(n.mrtw_kg, 27090)

    def test_no_extrapolation_above_highest_oat(self):
        with self.assertRaises(PerfError):
            calc("BJR 04/LOOP", 27, bleeds="on")

    def test_cold_oat_uses_lowest_row(self):
        r = calc("BJR 04/LOOP", -45, bleeds="on")
        self.assertEqual(r.mrtw_kg, 29570)
        self.assertTrue(any("lowest chart temperature" in n for n in r.notes))

    def test_blank_cell_is_an_error(self):
        with self.assertRaises(PerfError):
            calc("GDE 04-SW1548M", 28, config="nws-inop", surface="wet")

    def test_card_v1_capped_at_max_v1_vr_v2_unchanged(self):
        # 36 °C, -10T: "1963^98"
        r = calc("LLI 10", 36, surface="wet", wind=-10, card_v1=105, card_vr=110, card_v2=115)
        self.assertEqual((r.max_v1, r.v1, r.vr, r.v2), (98, 98, 110, 115))
        r = calc("LLI 10", 36, card_v1=105, card_vr=110, card_v2=115)
        self.assertEqual(r.v1, 105)

    def test_tow_above_mrtw_flagged(self):
        r = calc("LLI 10", 36, tow_kg=21000)
        self.assertTrue(any("EXCEEDS" in n for n in r.notes))

    def test_assumed_temperature(self):
        rw = find_runway(RUNWAYS, "LLI", "10")
        (t,) = select_tables(rw)
        r = assumed_temperature(rw, t, 25, 21000)
        self.assertEqual((r.assumed_temp, r.max_v1), (34, 114))  # 34 °C +0: 2117^14
        on = select_tables(rw, bleeds="on")[0]
        with self.assertRaises(PerfError):
            assumed_temperature(rw, on, 25, 21000)

    def test_every_runway_computes_at_isa_sea_level_day(self):
        for rw in RUNWAYS:
            for t in rw["tables"]:
                if not t["rows"] or "TW >" in t["condition"]:
                    continue
                oat = min(r["oat"] for r in t["rows"])
                try:
                    res = calculate(rw, t, oat)
                except PerfError:
                    continue  # blank +0 cell at the coldest row
                self.assertTrue(80 <= res.max_v1 <= 150, (rw["id"], t["condition"], res.max_v1))
                self.assertTrue(10000 <= res.mrtw_kg <= 29574, (rw["id"], t["condition"], res.mrtw_kg))

    def test_cli_runs(self):
        out = subprocess.run([sys.executable, "-m", "q400perf", "calc", "BJR", "04/LOOP", "--oat", "26",
                              "--bleeds", "on"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertRegex(out, r"MRTW +27090 kg +\(limit C\)")
        self.assertRegex(out, r"MAX V1 +130 kt")


if __name__ == "__main__":
    unittest.main()
