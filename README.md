# Q400 takeoff V-speed calculator

Takeoff V1 / VR / V2, MAX V1, maximum runway takeoff weight (MRTW) and torque for
every airport/runway in the Ethiopian Airlines **DHC-8-402 PW150A Performance
Handbook** (revision 26-09, 29 Sep 2026), with speeds from the **DHC-8-402 Speed
Book** (AeroData, version 1, 1/3/13).

> Planning and cross-check aid only. Always verify against the current
> approved handbook, AFM, and the company's speed cards.

## Where each number comes from

| Item | Source |
|---|---|
| MRTW and limit code | handbook runway analysis, every table |
| **MAX V1** | handbook runway analysis, every table (section 2.2 "MAX V1") |
| Takeoff torque, dry climb limit | handbook, page-1 tables |
| **V1 / VR / V2**, VFRI, VCLIMB for the TOW (give `--tow`) | speed book, following its "Takeoff Speeds Instructions" (below) |
| **V1 / VR / V2** with tailwind 10–20 kt | handbook "NTOP DRY – TW > 10KTS" tables (Supplement 3); these replace the speed book speeds |
| Hand-entered speeds | `--card V1/VR/V2` overrides the speed book; V1 is still capped at MAX V1 |

### Speed book procedure

1. TOW rounded **down** to 500 kg gives the speed book page (18,000–29,500 kg).
2. V1/VR additive at pressure altitude (from runway elevation and QNH) and the temperature at
   which power is set. For reduced power with `--assumed`, that is the assumed temperature.
3. If V1/VR is printed red and the adjusted value is below MINIMUM V1 VR, use the minimum.
4. If V2 is printed red, add the MINIMUM V2 additive at **actual** OAT and pressure altitude.
5. If V2 < VR, then V2 = VR.
6. V1 = lesser of the speed book V1 and the runway analysis MAX V1. VR and V2 are unchanged.

Additive tables are interpolated linearly and rounded **up**. The book has no extrapolation:
above 10,000 ft or 50 °C is an error. Below 0 ft or −54 °C uses the edge row or column.
The speed book warns that its speeds are based on V1/VR = 1, not a balanced field.

## Usage

Python 3.9+, standard library only (the extractor also needs `pdfplumber`).

```sh
python -m q400perf list                                    # all 42 airports / 120 runways
python -m q400perf calc BJR 22 --oat 28 --qnh 1008 --wind -5 --tow 26300
python -m q400perf calc BJR 04/LOOP --oat 26 --bleeds on --tow 25000
python -m q400perf calc ADD 25R --oat 18 --wind -14 --tow 24000   # tailwind >10 kt
python -m q400perf calc DIR 15 --oat 30 --tow 27500 --card 112/115/120
python -m q400perf calc LLI 10 --oat 25 --tow 21000 --assumed     # reduced power
python -m q400perf all --oat 25 --qnh 1010 --wind -3 --tow 25000 --csv all.csv
python -m q400perf batch flights.csv --csv results.csv
```

Condition options: `--surface dry|wet`, `--bleeds on|off`, `--power ntop|mtop`,
`--config normal|gear-extended|ground-spoilers-inop|nws-inop|anti-skid-inop`,
`--flaps 5|10|15`. Wind is the runway component (+ headwind, − tailwind).

`batch` CSV columns: `airport,runway,oat` plus optional
`qnh,wind,surface,bleeds,power,config,flaps,tow,card`.

Example:

```
$ python -m q400perf calc BJR 22 --oat 28 --qnh 1008 --wind -5 --tow 26300
BAHIR DAR (BJR) RWY 22  —  flaps 5, bleeds off, DRY - NTOP
  OAT 28 °C, QNH 1008 hPa, wind -5 kt
  MRTW       27370 kg  (limit ^, QNH adj -160 kg)
  MAX V1       131 kt
  V1 / VR / V2   128 / 128 / 128 kt   (speed book 26000 kg page, PA 6183 ft, V1/VR additive +5)
  VFRI 137 kt   VCLIMB (flaps 0) 148 kt
  EFP: RT H010  REACHING: 7039’  TURN: RT H010  FLAP RETRACT: 7039’
  NOTE: V2 127 raised to VR 128
  Source: 34ae58a0-GoodReader_2.pdf p4
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

### Speed book data

The speed book is a scan with no text layer. `data/speedbook.txt` is a hand transcription of
its 24 takeoff weight pages, in a plain format you can check against the book. The 23,000 kg page
appears twice in the scan. Checks applied:

* The red/black colour of every V1/VR and V2 entry was confirmed from the scan's pixel colours.
* Speeds never decrease with weight, and V1/VR ≤ V2 < VFRI on every page.
* The V1/VR additive table is identical on every page. The minimum V2 additive tables decrease with
  temperature, altitude and weight, and appear exactly on the pages that have a red V2.

## Tests

```sh
python -m unittest discover -s tests
```

Expected values in the tests were read directly from the handbook and speed book page images.
