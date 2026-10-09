"""Q400 (DHC-8-402 PW150A) takeoff V-speed calculator.

Examples::

    python -m q400perf list
    python -m q400perf calc BJR 22 --oat 28 --qnh 1008 --wind -5
    python -m q400perf calc ADD 25R --oat 18 --wind -14            # tailwind table: V1/VR/V2
    python -m q400perf calc DIR 15 --oat 30 --tow 27500 --card 112/115/120
    python -m q400perf calc JIB 27 --oat 30 --tow 25000 --assumed
    python -m q400perf all --oat 25 --qnh 1013 --wind 0 --csv out.csv
    python -m q400perf batch flights.csv
"""

import argparse
import csv
import sys

from .calc import CONFIGS, PerfError, assumed_temperature, calculate, find_runway, load, select_tables
from .speedbook import load_speedbook, pressure_altitude, takeoff_speeds

SPEEDBOOK = load_speedbook()


def apply_speedbook(r):
    """Fill V1/VR/V2 from the speed book for the TOW (unless the table or the user gave speeds)."""
    if r.tow_kg is None or r.speeds_source is not None:
        return
    pa = pressure_altitude(r.runway["meta"]["elevation"], r.qnh)
    power_temp = r.assumed_temp if r.assumed_temp is not None else r.oat
    sp = takeoff_speeds(SPEEDBOOK, r.tow_kg, r.flaps, pa, power_temp, r.oat, r.max_v1)
    r.v1, r.vr, r.v2, r.vfri, r.vclimb = sp.v1, sp.vr, sp.v2, sp.vfri, sp.vclimb
    r.speeds_source = (f"speed book {sp.weight_page} kg page, PA {pa:.0f} ft, "
                       f"V1/VR additive +{sp.v1vr_additive}"
                       + (f", min V2 additive +{sp.v2_additive}" if sp.v2_additive else ""))
    r.notes.extend(sp.notes)


def add_condition_args(p):
    p.add_argument("--oat", type=float, required=True, help="outside air temperature, °C")
    p.add_argument("--qnh", type=float, default=1013.0, help="QNH, hPa (default 1013)")
    p.add_argument("--wind", type=float, default=0.0,
                   help="runway wind component, kt: + headwind, - tailwind (default 0)")
    p.add_argument("--surface", choices=["dry", "wet"], default="dry")
    p.add_argument("--bleeds", choices=["on", "off"], default="off")
    p.add_argument("--power", choices=["ntop", "mtop"], default="ntop")
    p.add_argument("--config", choices=list(CONFIGS), default="normal")
    p.add_argument("--flaps", type=int, choices=[5, 10, 15], help="only this flap setting")
    p.add_argument("--no-headwind-credit", action="store_true", help="do not take credit for headwind")
    p.add_argument("--high-qnh-credit", action="store_true", help="take credit for QNH above 1013")


def fmt_result(r):
    rw = r.runway
    lines = [f"{rw['city']} ({rw['iata']}) RWY {rw['runway']}  —  flaps {r.flaps}, "
             f"bleeds {r.table['bleeds'].lower()}, {r.table['condition']}"]
    cond = f"  OAT {r.oat:g} °C, QNH {r.qnh:g} hPa, wind {r.wind:+g} kt"
    if r.assumed_temp is not None:
        cond += f", ASSUMED TEMP {r.assumed_temp} °C"
    lines.append(cond)
    lines.append(f"  MRTW      {r.mrtw_kg:>6} kg  (limit {r.limit}"
                 + (f", QNH adj {r.qnh_adjustment_kg:+d} kg" if r.qnh_adjustment_kg else "") + ")")
    if r.climb_limit_kg:
        lines.append(f"  Climb lim {r.climb_limit_kg:>6} kg")
    if r.torque is not None:
        lines.append(f"  Torque    {r.torque:>6} %")
    if r.max_v1 is not None:
        lines.append(f"  MAX V1    {r.max_v1:>6} kt")
    if r.vr is not None:
        lines.append(f"  V1 / VR / V2   {r.v1} / {r.vr} / {r.v2} kt   ({r.speeds_source})")
        if r.vfri is not None:
            lines.append(f"  VFRI {r.vfri} kt   VCLIMB (flaps 0) {r.vclimb} kt")
    elif r.max_v1 is not None:
        lines.append("  V1 / VR / V2: give --tow for speed book speeds (V1 not above MAX V1)")
    efp = rw.get("engine_failure")
    if efp and efp["procedure"]:
        lines.append("  EFP: " + rw["efp"] + "  " + "  ".join(f"{k}: {v}" for k, v in efp["procedure"].items()))
    for n in r.notes:
        lines.append(f"  NOTE: {n}")
    lines.append(f"  Source: {r.table['source']}")
    return "\n".join(lines)


def run_one(runways, airport, runway, a, out_rows=None, quiet=False):
    rw = find_runway(runways, airport, runway)
    tables = select_tables(rw, a.surface, a.bleeds, a.power, a.config, a.flaps, a.wind)
    results = []
    for t in tables:
        kw = dict(qnh=a.qnh, wind=a.wind, headwind_credit=not a.no_headwind_credit,
                  high_qnh_credit=a.high_qnh_credit)
        if getattr(a, "assumed", False):
            if a.tow is None:
                raise PerfError("--assumed requires --tow")
            r = assumed_temperature(rw, t, a.oat, a.tow, min_torque=a.min_torque, **kw)
            if r is None:
                raise PerfError("no assumed temperature available: use full power")
        else:
            card = parse_card(getattr(a, "card", None))
            r = calculate(rw, t, a.oat, tow_kg=getattr(a, "tow", None), card_v1=card[0], card_vr=card[1],
                          card_v2=card[2], **kw)
        apply_speedbook(r)
        results.append(r)
    return results


def parse_card(s):
    if not s:
        return (None, None, None)
    try:
        v1, vr, v2 = (int(x) for x in s.split("/"))
    except ValueError:
        raise PerfError("--card must be V1/VR/V2, e.g. 112/115/120")
    if not v1 <= vr <= v2:
        raise PerfError("speed card must satisfy V1 <= VR <= V2")
    return v1, vr, v2


CSV_FIELDS = ["airport", "city", "runway", "flaps", "bleeds", "condition", "oat", "qnh", "wind", "assumed_temp",
              "tow_kg", "mrtw_kg", "limit", "max_v1", "v1", "vr", "v2", "vfri", "vclimb", "torque",
              "climb_limit_kg", "speeds_source", "notes", "source"]


def csv_row(r):
    return {"airport": r.runway["iata"], "city": r.runway["city"], "runway": r.runway["runway"],
            "flaps": r.flaps, "bleeds": r.table["bleeds"], "condition": r.table["condition"], "oat": r.oat,
            "qnh": r.qnh, "wind": r.wind, "assumed_temp": r.assumed_temp, "tow_kg": r.tow_kg,
            "mrtw_kg": r.mrtw_kg, "limit": r.limit, "max_v1": r.max_v1, "v1": r.v1, "vr": r.vr, "v2": r.v2,
            "vfri": r.vfri, "vclimb": r.vclimb, "torque": r.torque, "climb_limit_kg": r.climb_limit_kg,
            "speeds_source": r.speeds_source, "notes": "; ".join(r.notes), "source": r.table["source"]}


def cmd_list(runways, a):
    city = None
    for rw in runways:
        if rw["city"] != city:
            city = rw["city"]
            print(f"\n{rw['iata']}  {city}  (elev {rw['meta'].get('elevation', 0):.0f} ft)")
        flaps = sorted({t["flaps"] for t in rw["tables"]})
        m = rw["meta"]
        print(f"    {rw['runway']:<14} {rw['efp']:<10} length {m.get('length', 0):.0f} m  "
              f"slope {m.get('slope', 0):+.2f}%  flaps {flaps}")


def cmd_calc(runways, a):
    for r in run_one(runways, a.airport, a.runway, a):
        print(fmt_result(r))
        print()


def cmd_all(runways, a):
    rows = []
    print(f"OAT {a.oat:g} °C  QNH {a.qnh:g} hPa  wind {a.wind:+g} kt  {a.surface}  bleeds {a.bleeds}  "
          f"{a.power}  {a.config}\n")
    print(f"{'APT':<4} {'RWY':<14} {'FLP':>3} {'MRTW kg':>8} {'LIM':>3} {'MAXV1':>5} {'V1/VR/V2':>11} {'TRQ':>5}")
    for rw in runways:
        try:
            res = run_one(runways, rw["iata"], rw["runway"], a)
        except PerfError as e:
            print(f"{rw['iata']:<4} {rw['runway']:<14}  -- {e}")
            continue
        for r in res:
            sp = f"{r.v1}/{r.vr}/{r.v2}" if r.vr is not None else ""
            over = "  TOW > MRTW" if r.tow_kg is not None and r.tow_kg > r.mrtw_kg else ""
            print(f"{rw['iata']:<4} {rw['runway']:<14} {r.flaps:>3} {r.mrtw_kg:>8} {r.limit:>3} "
                  f"{r.max_v1 if r.max_v1 else '':>5} {sp:>11} {r.torque if r.torque else '':>5}{over}")
            rows.append(csv_row(r))
    if a.csv:
        write_csv(a.csv, rows)


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)} rows written to {path}", file=sys.stderr)


def cmd_batch(runways, a):
    """CSV columns: airport,runway,oat[,qnh,wind,surface,bleeds,power,config,flaps,tow,card]"""
    rows = []
    with open(a.input) as f:
        for n, rec in enumerate(csv.DictReader(f), 2):
            ns = argparse.Namespace(
                oat=float(rec["oat"]), qnh=float(rec.get("qnh") or 1013), wind=float(rec.get("wind") or 0),
                surface=rec.get("surface") or "dry", bleeds=rec.get("bleeds") or "off",
                power=rec.get("power") or "ntop", config=rec.get("config") or "normal",
                flaps=int(rec["flaps"]) if rec.get("flaps") else None,
                tow=float(rec["tow"]) if rec.get("tow") else None, card=rec.get("card") or None,
                no_headwind_credit=a.no_headwind_credit, high_qnh_credit=a.high_qnh_credit,
                assumed=False, min_torque=None)
            try:
                for r in run_one(runways, rec["airport"], rec["runway"], ns):
                    print(fmt_result(r) + "\n")
                    rows.append(csv_row(r))
            except PerfError as e:
                print(f"line {n}: {rec['airport']} {rec['runway']}: {e}\n")
    if a.csv:
        write_csv(a.csv, rows)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="q400perf", description=__doc__.split("\n")[0])
    ap.add_argument("--data", help="extracted handbook JSON (default data/takeoff.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list airports and runways in the handbook")

    p = sub.add_parser("calc", help="MRTW, MAX V1 and V-speeds for one runway")
    p.add_argument("airport", help="IATA code, e.g. ADD")
    p.add_argument("runway", help="runway as in the handbook, e.g. 25R, 07L/B1, 04-SW700M")
    add_condition_args(p)
    p.add_argument("--tow", type=float, help="actual takeoff weight, kg: gives speed book V1/VR/V2")
    p.add_argument("--card", help="override: V1/VR/V2 entered by hand; V1 is capped at MAX V1")
    p.add_argument("--assumed", action="store_true", help="find assumed temperature for reduced power")
    p.add_argument("--min-torque", type=float, help="AFM minimum torque for reduced power, %%")

    p = sub.add_parser("all", help="every airport/runway in the handbook for one set of conditions")
    add_condition_args(p)
    p.add_argument("--tow", type=float, help="takeoff weight, kg: adds speed book V1/VR/V2")
    p.add_argument("--csv", help="also write results to this CSV file")

    p = sub.add_parser("batch", help="conditions per runway from a CSV file")
    p.add_argument("input")
    p.add_argument("--csv", help="also write results to this CSV file")
    p.add_argument("--no-headwind-credit", action="store_true")
    p.add_argument("--high-qnh-credit", action="store_true")

    a = ap.parse_args(argv)
    runways = load(a.data) if a.data else load()
    try:
        {"list": cmd_list, "calc": cmd_calc, "all": cmd_all, "batch": cmd_batch}[a.cmd](runways, a)
    except PerfError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
