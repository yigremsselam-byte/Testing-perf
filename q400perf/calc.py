"""Takeoff MAX V1 / MRTW / speed calculation from the extracted runway analysis tables.

Implements the handbook's "Rules for reading takeoff charts" (section 2.2):

1. Extrapolation is not allowed (OAT above the highest row is an error; headwind above
   the +10H column is credited only as 10 kt).
2. Interpolation is allowed between adjacent temperatures.
3. Interpolation is allowed between winds.  Tailwind adjustments are always applied;
   headwind credit is optional (``headwind_credit``).
4. OAT colder than the lowest row uses the lowest row.

QNH: adjustments are applied below 1013 hPa (the handbook only requires them at 1010 hPa
and below, so applying them from 1013 is conservative); high QNH credit is optional.
Adjustments are kg per hPa.

MAX V1 (section 2.2): when the V1 for takeoff exceeds MAX V1, V1 is set equal to MAX V1.
VR and V2 are not changed.  Interpolated MAX V1 is rounded down; interpolated VR/V2 from
the tailwind tables are rounded up.
"""

import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "takeoff.json")

CONFIGS = {
    "normal": None,
    "gear-extended": "GEAR EXTENDED",
    "ground-spoilers-inop": "SPLRS INOP",
    "nws-inop": "NW STEER INOP",
    "anti-skid-inop": "ANTI-SKID INOP",
}

REDUCED_POWER_INVALID = "Bleeds ON and MTOP data are not valid for reduced power"


class PerfError(Exception):
    pass


def load(path=DATA_FILE):
    with open(path) as f:
        return json.load(f)["runways"]


@dataclass
class Result:
    runway: dict
    table: dict
    oat: float
    wind: float
    qnh: float
    mrtw_kg: int
    limit: str
    max_v1: Optional[int] = None
    torque: Optional[float] = None
    climb_limit_kg: Optional[int] = None
    v1: Optional[int] = None
    vr: Optional[int] = None
    v2: Optional[int] = None
    qnh_adjustment_kg: int = 0
    notes: list = field(default_factory=list)
    tow_kg: Optional[float] = None
    assumed_temp: Optional[int] = None
    vfri: Optional[int] = None
    vclimb: Optional[int] = None
    speeds_source: Optional[str] = None

    @property
    def flaps(self):
        return self.table["flaps"]


def find_runway(runways, airport, runway):
    airport = airport.upper()
    cands = [r for r in runways if r["iata"] == airport]
    if not cands:
        raise PerfError(f"airport {airport} not in handbook")
    rw = runway.upper()
    for r in cands:
        if r["runway"].upper() == rw:
            return r
    raise PerfError(f"runway {runway} not found at {airport}; available: "
                    + ", ".join(r["runway"] for r in cands))


def select_tables(rw, surface="dry", bleeds="off", power="ntop", config="normal", flaps=None, wind=0.0):
    surface, bleeds, power = surface.upper(), bleeds.upper(), power.upper()
    if surface not in ("DRY", "WET"):
        raise PerfError("surface must be dry or wet")
    if config not in CONFIGS:
        raise PerfError(f"config must be one of {', '.join(CONFIGS)}")
    tw_table = wind < -10
    if tw_table:
        if (surface, bleeds, power, config) != ("DRY", "OFF", "NTOP", "normal"):
            raise PerfError("tailwind above 10 kt: data only for dry runway, bleeds off, NTOP, normal config")
        want = lambda t: t["condition"] == "NTOP DRY - TW > 10KTS"
    elif config == "normal":
        if bleeds == "ON" and power == "MTOP":
            raise PerfError("MTOP data is only published for bleeds off")
        cond = f"{surface} - {power}"
        want = lambda t: t["condition"] == cond and t["bleeds"] == bleeds
    else:
        if bleeds != "OFF" or power != "NTOP":
            raise PerfError("non-normal configuration data is only published for bleeds off, NTOP")
        if config == "anti-skid-inop" and surface == "WET":
            raise PerfError("anti-skid inop data is only published for a dry runway")
        key = CONFIGS[config]
        want = lambda t: t["condition"].startswith(surface + " /") and t["condition"].endswith(key)
    tables = [t for t in rw["tables"] if want(t) and (flaps is None or t["flaps"] == flaps)]
    if not tables:
        have = sorted({t["flaps"] for t in rw["tables"] if want(t)})
        extra = f" (flaps available: {have})" if have else ""
        raise PerfError(f"no {surface.lower()} / bleeds {bleeds.lower()} / {power} / {config} data for "
                        f"{rw['id']}{extra}")
    return tables


def _bracket(values, x):
    """Indices (i, j, frac) of the two entries of ascending `values` around x."""
    if x <= values[0]:
        return 0, 0, 0.0
    for i in range(len(values) - 1):
        if values[i] <= x <= values[i + 1]:
            if values[i] == x:
                return i, i, 0.0
            if values[i + 1] == x:
                return i + 1, i + 1, 0.0
            return i, i + 1, (x - values[i]) / (values[i + 1] - values[i])
    raise PerfError(f"{x} is above the chart range (max {values[-1]}); extrapolation is not allowed")


def _lerp(a, b, f):
    return a + (b - a) * f


def _cell_at_wind(row, winds, w):
    i, j, f = _bracket(winds, w)
    a, b = row["cells"][i], row["cells"][j]
    if a is None or b is None:
        return None
    out = {"mrtw_kg": _lerp(a["mrtw_kg"], b["mrtw_kg"], f),
           "limit": a["limit"] if a["mrtw_kg"] <= b["mrtw_kg"] else b["limit"]}
    for k in ("max_v1", "v1", "vr", "v2"):
        if k in a:
            out[k] = _lerp(a[k], b[k], f)
    return out


def _effective_wind(table, wind, headwind_credit):
    winds = table["winds"]
    w = wind if headwind_credit else min(wind, 0.0)
    if min(winds) < -10:  # tailwind table: columns -20T..-11T
        if wind < -20:
            raise PerfError("tailwind above 20 kt: no data")
        return max(min(w, winds[-1]), winds[0])
    if w < winds[0]:
        raise PerfError(f"tailwind {-wind:g} kt exceeds the {-winds[0]} kt column")
    return min(w, winds[-1])  # headwind above +10H: credit 10 kt only


def _qnh_adjustment(table, qnh, high_qnh_credit):
    q = table["qnh"]
    if qnh < 1013 and q["low"]:
        return q["low"] * (1013 - qnh)
    if qnh > 1013 and high_qnh_credit and q["high"]:
        return q["high"] * (qnh - 1013)
    return 0.0


def _row_values(table, oat, w):
    rows = sorted(table["rows"], key=lambda r: r["oat"])
    oats = [r["oat"] for r in rows]
    i, j, f = _bracket(oats, oat)
    a, b = _cell_at_wind(rows[i], table["winds"], w), _cell_at_wind(rows[j], table["winds"], w)
    if a is None or b is None:
        raise PerfError(f"no chart data at OAT {oat:g} for this wind (blank in handbook)")
    out = {"mrtw_kg": _lerp(a["mrtw_kg"], b["mrtw_kg"], f),
           "limit": a["limit"] if a["mrtw_kg"] <= b["mrtw_kg"] else b["limit"]}
    for k in ("max_v1", "v1", "vr", "v2"):
        if k in a:
            out[k] = _lerp(a[k], b[k], f)
    for k in ("torque", "climb_limit_kg"):
        if rows[i][k] is not None and rows[j][k] is not None:
            out[k] = _lerp(rows[i][k], rows[j][k], f)
    if oat < oats[0]:
        out["note"] = f"OAT below lowest chart temperature; {oats[0]} °C row used"
    return out


def calculate(rw, table, oat, qnh=1013.0, wind=0.0, headwind_credit=True, high_qnh_credit=False,
              tow_kg=None, card_v1=None, card_vr=None, card_v2=None):
    w = _effective_wind(table, wind, headwind_credit)
    v = _row_values(table, oat, w)
    adj = _qnh_adjustment(table, qnh, high_qnh_credit)
    res = Result(runway=rw, table=table, oat=oat, wind=wind, qnh=qnh,
                 mrtw_kg=int(math.floor(v["mrtw_kg"] + adj)), limit=v["limit"],
                 qnh_adjustment_kg=int(round(adj)), tow_kg=tow_kg)
    if "note" in v:
        res.notes.append(v["note"])
    if w != wind:
        res.notes.append(f"wind credited as {w:+g} kt")
    if "torque" in v:
        res.torque = round(v["torque"], 1)
    if "climb_limit_kg" in v:
        res.climb_limit_kg = int(v["climb_limit_kg"])
    if "max_v1" in v:
        res.max_v1 = int(math.floor(v["max_v1"]))
    else:  # tailwind 10-20 kt table: V1/VR/V2 published (Supplement 3)
        res.vr = int(math.ceil(v["vr"]))
        res.v2 = int(math.ceil(v["v2"]))
        res.v1 = min(int(math.floor(v["v1"])), res.vr)
        res.speeds_source = "runway analysis, Supplement 3 (tailwind 10-20 kt)"
    if tow_kg is not None and tow_kg > res.mrtw_kg:
        res.notes.append(f"TOW {tow_kg:.0f} kg EXCEEDS MRTW {res.mrtw_kg} kg")
    if card_vr is not None:
        res.vr, res.v2 = card_vr, card_v2
        res.speeds_source = "entered speed card"
    if card_v1 is not None:
        if res.max_v1 is not None and card_v1 > res.max_v1:
            res.notes.append(f"V1 {card_v1} reduced to MAX V1 {res.max_v1} (VR and V2 unchanged)")
            res.v1 = res.max_v1
        else:
            res.v1 = card_v1
    return res


def assumed_temperature(rw, table, oat, tow_kg, qnh=1013.0, wind=0.0, headwind_credit=True,
                        high_qnh_credit=False, min_torque=None):
    """Reduced power by assumed temperature (handbook section 2.2 "Reduced Power/Thrust").

    Returns the highest chart temperature, above the actual OAT, whose (wind and QNH
    corrected) weight is >= TOW and whose torque is >= ``min_torque`` when published.
    """
    if table["bleeds"] == "ON" or "MTOP" in table["condition"]:
        raise PerfError(REDUCED_POWER_INVALID)
    w = _effective_wind(table, wind, headwind_credit)
    adj = _qnh_adjustment(table, qnh, high_qnh_credit)
    best = None
    for row in sorted(table["rows"], key=lambda r: r["oat"]):
        if row["oat"] <= oat:
            continue
        c = _cell_at_wind(row, table["winds"], w)
        if c is None or c["mrtw_kg"] + adj < tow_kg:
            continue
        if min_torque is not None and row["torque"] is not None and row["torque"] < min_torque:
            continue
        best = row["oat"]
    if best is None:
        return None
    res = calculate(rw, table, best, qnh, wind, headwind_credit, high_qnh_credit, tow_kg)
    res.assumed_temp = best
    res.oat = oat
    return res
