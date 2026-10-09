# Q400 takeoff V-speed calculator

Takeoff MAX V1, maximum runway takeoff weight (MRTW), torque and Supplement 3
V1/VR/V2 for every airport/runway in the Ethiopian Airlines **DHC-8-402 PW150A
Performance Handbook** (revision 26-09, 29 Sep 2026). The data comes from the
AeroData takeoff runway analysis pages.

> Planning and cross-check aid only. Always verify against the current
> approved handbook, AFM, and the company's speed cards.

## What the handbook provides, and what it doesn't

| Item | Source |
|---|---|
| MRTW and limit code | handbook, every table |
| **MAX V1** | handbook, every table (section 2.2 "MAX V1") |
| Takeoff torque, dry climb limit | handbook, page-1 tables |
| **V1 / VR / V2** with tailwind 10–20 kt | handbook, "NTOP DRY – TW > 10KTS" tables (Supplement 3) |
| VR / V2 for an actual weight in normal operations | **not in the handbook**: it comes from the AFM/QRH speed card. Pass it with `--card V1/VR/V2` and the program caps V1 at MAX V1 (VR and V2 stay unchanged, as section 2.2 requires). |

## Usage

Python 3.9+, standard library only (the extractor also needs `pdfplumber`).

```sh
python -m q400perf list                                    # all 42 airports / 120 runways
python -m q400perf calc BJR 22 --oat 28 --qnh 1008 --wind -5
python -m q400perf calc BJR 04/LOOP --oat 26 --bleeds on
python -m q400perf calc ADD 25R --oat 18 --wind -14       # tailwind >10 kt -> V1/VR/V2
python -m q400perf calc DIR 15 --oat 30 --tow 27500 --card 112/115/120
python -m q400perf calc LLI 10 --oat 25 --tow 21000 --assumed   # reduced power
python -m q400perf all --oat 25 --qnh 1010 --wind -3 --csv all.csv
python -m q400perf batch flights.csv --csv results.csv
```

Condition options: `--surface dry|wet`, `--bleeds on|off`, `--power ntop|mtop`,
`--config normal|gear-extended|ground-spoilers-inop|nws-inop|anti-skid-inop`,
`--flaps 5|10|15`. Wind is the runway component (+ headwind, − tailwind).

`batch` CSV columns: `airport,runway,oat` plus optional
`qnh,wind,surface,bleeds,power,config,flaps,tow,card`.

Example:

```
$ python -m q400perf calc BJR 04/LOOP --oat 26 --bleeds on
BAHIR DAR (BJR) RWY 04/LOOP  —  flaps 5, bleeds on, DRY - NTOP
  OAT 26 °C, QNH 1013 hPa, wind +0 kt
  MRTW       27090 kg  (limit C)
  Climb lim  27090 kg
  Torque      77.8 %
  MAX V1       130 kt
  ...
```

## Chart-reading rules applied (handbook section 2.2)

* No extrapolation: an OAT above the highest row is an error. An OAT below the lowest row uses the lowest row.
* Linear interpolation between adjacent temperatures and between wind columns.
  Interpolated MAX V1 and V1 are rounded **down**; VR and V2 are rounded **up**.
* Tailwind is always applied. Headwind credit is applied up to the +10H column; turn it off with `--no-headwind-credit`.
* Tailwind over 10 kt uses the Supplement 3 table, which only exists for dry runway, bleeds off, NTOP. Over 20 kt is rejected.
* Low QNH is applied whenever QNH is below 1013. The handbook only requires it at 1010 or below, so this is conservative.
  High QNH credit needs `--high-qnh-credit`. Adjustments are **kg per hPa**: this matches the
  ≈35 kg/hPa sensitivity of the climb-limit column across the handbook's airports.
* A blank cell in the handbook means no data, so the program returns an error rather than guessing.
* Reduced power (`--assumed`) uses the highest chart temperature above OAT whose weight is ≥ TOW, plus an optional `--min-torque`.
  It is refused for bleeds-on and MTOP data, which the handbook marks "not valid for reduced power".
* Anti-ice and MEL/CDL penalties are not in the provided pages, so they are not applied.

## Data extraction

`data/takeoff.json` is generated from the handbook PDFs. The files are passed in
handbook page order: front matter and ADD…BJR 04, then BJR 04/LOOP…JIM 13, then
JIM 31…SHC 33. The landing section is ignored.

```sh
python -m q400perf.extract GoodReader.pdf GoodReader_2.pdf GoodReader_3.pdf GoodReader_4.pdf -o data/takeoff.json
```

The extractor reads character positions, so it handles blank cells and
space-padded speeds (`2077^ 1` = 101 kt). It also checks its own output:

* Every runway in the List of Effective Pages is present, in handbook order (checked by the tests).
* All 76,666 cells match an independent `pdftotext -layout` read of the same pages.
* Each table entry records the PDF and page it came from (`Source:` in the output).

## Tests

```sh
python -m unittest discover -s tests
```

Expected values in the tests were read directly from the handbook page images.
