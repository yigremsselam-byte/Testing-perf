"""Takeoff speeds (V1, VR, V2, VFRI, VCLIMB) from the DHC-8-402 Speed Book.

Follows the book's "Takeoff Speeds Instructions" (see data/speedbook.txt). Additive tables are
interpolated linearly in pressure altitude and temperature and rounded UP. Below 0 ft pressure
altitude the 0 ft row is used, below -54 °C the -54 °C column; above 10,000 ft or 50 °C is an
error (no extrapolation).
"""

import math
import os
from dataclasses import dataclass, field

from .calc import PerfError, _bracket

SPEEDBOOK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "speedbook.txt")
ALTS = [0, 2000, 4000, 6000, 8000, 10000]   # ascending; the book lists 10,000 first
TEMPS = [-54, 0, 10, 20, 30, 40, 50]
FLAPS = (5, 10, 15)


def load_speedbook(path=SPEEDBOOK_FILE):
    pages, cur = {}, None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kind, rest = line[0], line[2:]
            if kind == "W":
                w, f5, f10, f15, colour, climb, mins = (x.strip() for x in rest.split("|"))
                cur = pages[int(w)] = {
                    "flaps": {},
                    "vclimb": int(climb),
                    "min_v1vr": dict(zip(FLAPS, [None if m == "-" else int(m) for m in mins.split()])),
                }
                for fl, sp, col in zip(FLAPS, (f5, f10, f15), colour.split()):
                    v1vr, v2, vfri = (int(x) for x in sp.split())
                    cur["flaps"][fl] = {"v1vr": v1vr, "v2": v2, "vfri": vfri,
                                        "v1vr_red": col[0] == "R", "v2_red": col[1] == "R"}
            elif kind in "AM":
                rows = [[int(x) for x in r.split()] for r in rest.split("/")]
                cur["v1vr_add" if kind == "A" else "min_v2_add"] = rows[::-1]  # store 0 ft first
    return pages


def pressure_altitude(elevation_ft, qnh_hpa):
    return elevation_ft + 145442.16 * (1 - (qnh_hpa / 1013.25) ** 0.190263)


def _additive(table, alt, temp):
    alt = max(alt, ALTS[0])
    temp = max(temp, TEMPS[0])
    if alt > ALTS[-1]:
        raise PerfError(f"pressure altitude {alt:.0f} ft above speed book range (10,000 ft)")
    if temp > TEMPS[-1]:
        raise PerfError(f"temperature {temp:g} °C above speed book range (50 °C)")
    i, j, fa = _bracket(ALTS, alt)
    k, m, ft = _bracket(TEMPS, temp)
    lo = table[i][k] + (table[i][m] - table[i][k]) * ft
    hi = table[j][k] + (table[j][m] - table[j][k]) * ft
    return math.ceil(lo + (hi - lo) * fa - 1e-9)


@dataclass
class Speeds:
    weight_page: int
    flaps: int
    v1: int
    vr: int
    v2: int
    vfri: int
    vclimb: int
    v1vr_additive: int
    v2_additive: int = 0
    notes: list = field(default_factory=list)


def takeoff_speeds(book, tow_kg, flaps, press_alt_ft, power_temp, oat, max_v1=None):
    """power_temp: temperature at which power is set (assumed temperature for reduced power);
    oat: actual OAT (for the minimum V2 additive)."""
    page_w = int(tow_kg // 500 * 500)
    if page_w < min(book):
        raise PerfError(f"TOW {tow_kg:.0f} kg below speed book range ({min(book)} kg)")
    if page_w > max(book):
        raise PerfError(f"TOW {tow_kg:.0f} kg above speed book range ({max(book)} kg page)")
    if flaps not in FLAPS:
        raise PerfError("speed book covers takeoff flaps 5, 10 and 15")
    page = book[page_w]
    s = page["flaps"][flaps]
    notes = []

    add = _additive(page["v1vr_add"], press_alt_ft, power_temp)
    vr = s["v1vr"] + add
    if s["v1vr_red"] and vr < page["min_v1vr"][flaps]:
        notes.append(f"V1/VR {vr} raised to minimum V1/VR {page['min_v1vr'][flaps]}")
        vr = page["min_v1vr"][flaps]

    v2, v2_add = s["v2"], 0
    if s["v2_red"]:
        v2_add = _additive(page["min_v2_add"], press_alt_ft, oat)
        v2 += v2_add
    if v2 < vr:
        notes.append(f"V2 {v2} raised to VR {vr}")
        v2 = vr

    v1 = vr
    if max_v1 is not None and max_v1 < v1:
        notes.append(f"V1 {v1} limited to runway analysis MAX V1 {max_v1} (VR and V2 unchanged)")
        v1 = max_v1
    return Speeds(page_w, flaps, v1, vr, v2, s["vfri"], page["vclimb"], add, v2_add, notes)
