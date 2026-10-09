"""Extract takeoff runway analysis tables from the DHC-8-402 Performance Handbook PDFs.

The handbook (AeroData format) gives, for every airport/runway and configuration,
cells of the form ``WWWWcVV`` where:

* ``WWWW`` is the maximum runway takeoff weight (MRTW) in 10 kg,
* ``c``    is the limit code (``^`` obstacle, A, B, C, D, F, T, V),
* ``VV``   is MAX V1 (add 100 when the number shown is below 80).

The "BLEEDS OFF NTOP DRY - TW > 10KTS" tables use ``WWWWcV1-VR-V2`` with 100 added to
each speed.  See handbook section 2.2 "Takeoff Chart Layout" (pages 2.8 - 2.10).

Text is read with character x-coordinates (pdfplumber) because cells may be blank
and speeds are space padded (``2077^ 1`` = 101 kt), which plain text extraction
cannot resolve reliably.

Usage::

    python -m q400perf.extract handbook/*.pdf -o data/takeoff.json
"""

import argparse
import json
import re
import sys

import pdfplumber

CELL_RE = re.compile(r"^(\d{4})([A-Z^])(\d{1,2})$")
TW_CELL_RE = re.compile(r"^(\d{4})([A-Z^])(\d{1,2})-(\d{1,2})-(\d{1,2})$")
WIND_RE = re.compile(r"^([+-]\d+)([TH]?)$")
SIGNED_RE = re.compile(r"^[+-]?\d+$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
TITLE_RE = re.compile(r"^(.*?)\s*-([A-Z]{3})\s+(\S+)\s*(.*)$")

LINE_TOL = 2.0     # points: chars whose tops differ by less than this are one line
CELL_GAP = 5.0     # points: gap that separates two cells (one space ~3.7 pt, two ~7.3 pt)
PHRASE_GAP = 12.0  # points: gap that separates two title phrases on one line


def speed(v):
    """Handbook rule: add 100 when the number shown is less than 80."""
    v = int(v)
    return v + 100 if v < 80 else v


def _join(chars):
    """Join characters, inserting a space wherever there is a visible gap."""
    out = chars[0]["text"]
    for a, b in zip(chars, chars[1:]):
        out += (" " if b["x0"] - a["x1"] > 0.12 * b["size"] else "") + b["text"]
    return out


class Line:
    def __init__(self, chars):
        self.chars = sorted(chars, key=lambda c: c["x0"])
        self.top = min(c["top"] for c in chars)
        self.tokens = self._split(CELL_GAP)

    def _split(self, gap):
        toks, cur = [], []
        for c in self.chars:
            if c["text"].isspace():
                continue
            if cur and c["x0"] - cur[-1]["x1"] > gap:
                toks.append(cur)
                cur = []
            cur.append(c)
        if cur:
            toks.append(cur)
        return [
            {
                "text": _join(t),
                "x0": t[0]["x0"],
                "x1": t[-1]["x1"],
                "xc": (t[0]["x0"] + t[-1]["x1"]) / 2,
                "size": max(ch["size"] for ch in t),
            }
            for t in toks
        ]

    def words(self):
        """Tokens split on any space (for labels)."""
        return self._split(1.0)

    def phrases(self):
        return self._split(PHRASE_GAP)

    @property
    def text(self):
        return " ".join(t["text"] for t in self.tokens)


def page_lines(page):
    chars = [c for c in page.chars if c["text"].strip()]
    chars.sort(key=lambda c: c["top"])
    groups, cur = [], []
    for c in chars:
        if cur and c["top"] - cur[0]["top"] > LINE_TOL:
            groups.append(cur)
            cur = []
        cur.append(c)
    if cur:
        groups.append(cur)
    return [Line(g) for g in groups]


ANY_CELL_RE = re.compile(r"\d{4}[A-Z^] ?\d{1,2}(?:- ?\d{1,2}- ?\d{1,2})?")


def split_cells(line, tokens, source):
    """Split row tokens into cells; adjacent tables can be only one space apart."""
    out = []
    for tok in tokens:
        chars = [c for c in line.chars if tok["x0"] <= c["x0"] and c["x1"] <= tok["x1"] + 0.01]
        text, idx = "", []
        for n, c in enumerate(chars):
            if n and c["x0"] - chars[n - 1]["x1"] > 1.5:
                text += " "
                idx.append(None)
            text += c["text"]
            idx.append(c)
        covered = 0
        for m in ANY_CELL_RE.finditer(text):
            if text[covered:m.start()].strip(" -"):
                raise ValueError(f"{source}: stray text '{text[covered:m.start()]}' in '{text}'")
            covered = m.end()
            cs = [c for c in idx[m.start():m.end()] if c]
            out.append({"text": m.group().replace(" ", ""), "x0": cs[0]["x0"], "x1": cs[-1]["x1"],
                        "xc": (cs[0]["x0"] + cs[-1]["x1"]) / 2})
        if text[covered:].strip(" -"):
            raise ValueError(f"{source}: unparseable text '{text[covered:]}' in '{text}'")
    return out


def is_header(line):
    words = [w["text"] for w in line.words()]
    winds = [w for w in words if WIND_RE.match(w) and w[-1] in "TH" or w == "+0"]
    return len(winds) >= 3 and ("˚C" in words or "°C" in words or "TORQUE" in words)


def nearest(x, items, key):
    return min(items, key=lambda it: abs(key(it) - x))


def parse_title(lines):
    """First line: CITY  -IATA  RUNWAY  ENGINE-FAILURE-PROCEDURE."""
    m = TITLE_RE.match(" ".join(w["text"] for w in lines[0].words()))
    if not m:
        return None
    city, iata, runway, efp = m.groups()
    return {"city": city.strip(), "iata": iata, "runway": runway, "efp": efp.strip()}


def parse_footer(lines):
    meta = {}
    for ln in lines:
        words = [w["text"] for w in ln.words()]
        if words[:1] == ["Elevation"]:
            for k in ("Elevation", "Length", "Shift", "Clearway", "Stopway", "Slope"):
                if k in words:
                    meta[k.lower()] = float(words[words.index(k) + 1])
        if words[:2] == ["Rwy", "Rev."] and words:
            meta["revision"] = " ".join(words[-3:])
    return meta


def parse_efp(lines):
    """Engine failure procedure box (only on the main page)."""
    for i, ln in enumerate(lines):
        if ln.text.startswith("CLIMB AT") and i + 1 < len(lines):
            heads = ln.phrases()
            vals = lines[i + 1]
            out = {}
            for v in vals.phrases():
                h = nearest(v["xc"], heads, lambda t: t["xc"])
                out[h["text"]] = (out.get(h["text"], "") + " " + v["text"]).strip()
            remarks = []
            for r in lines[i + 2:]:
                if r.text.startswith("BLEEDS"):
                    break
                remarks.append(r.text)
            return {"procedure": out, "remarks": " ".join(remarks)}
    return None


def parse_tables(lines, source):
    tables = []
    bleeds = None
    for i, ln in enumerate(lines):
        words = ln.words()
        for j, w in enumerate(words):
            if w["text"] == "BLEEDS" and j + 1 < len(words) and words[j + 1]["text"] in ("ON", "OFF"):
                bleeds = words[j + 1]["text"]
        if not is_header(ln):
            continue

        hwords = ln.words()
        oat_x = nearest(0, hwords, lambda t: t["x0"])["xc"]
        torque = next((w for w in hwords if w["text"] == "TORQUE"), None)
        limit = next((w for w in hwords if w["text"] == "Limit"), None)
        cols = [w for w in hwords if w["text"] == "+0" or (WIND_RE.match(w["text"]) and w["text"][-1] in "TH")]

        # Title line(s) and flaps line(s) above the header, below the previous table.
        above = lines[max(0, i - 6):i]
        title_line = next(
            (l for l in reversed(above) if re.search(r"NTOP|MTOP|GEAR|INOP|TW >", l.text)), None
        )
        if title_line is None:
            raise ValueError(f"{source}: no table title above header '{ln.text}'")
        groups = [p for p in title_line.phrases() if not p["text"].startswith("BLEEDS")]
        # "Flaps" labels and the large flap numbers beside them (which may sit on their own
        # text line because of the bigger font).
        between = [w for l in above[above.index(title_line) + 1:] for w in l.words()]
        numbers = [w for w in between if w["text"] in ("5", "10", "15", "35")]
        flap_labels = []
        for w in between:
            if w["text"] == "Flaps":
                cand = [n for n in numbers if 0 < n["x0"] - w["x1"] < 45]
                n = min(cand, key=lambda n: n["x0"] - w["x1"]) if cand else None
                flap_labels.append({"xc": w["xc"], "flaps": int(n["text"]) if n else None})

        right_edge = limit["x0"] if limit else 1e9
        data_cols = [c for c in cols if c["xc"] < right_edge]
        gtabs = []
        for g in groups:
            gtabs.append({
                "condition": g["text"],
                "bleeds": bleeds,
                "flaps": None,
                "columns": [],
                "rows": {},
                "qnh": {"high": None, "low": None},
                "source": source,
            })
        for c in data_cols:
            g = nearest(c["xc"], list(range(len(groups))), lambda k: groups[k]["xc"])
            gtabs[g]["columns"].append(c)
        for fl in flap_labels:
            g = nearest(fl["xc"], list(range(len(groups))), lambda k: groups[k]["xc"])
            gtabs[g]["flaps"] = fl["flaps"]
        spans = [(min(c["x0"] for c in t["columns"]), max(c["x1"] for c in t["columns"]))
                 if t["columns"] else (1e9, 1e9) for t in gtabs]
        for t in gtabs:
            t["wind_labels"] = [c["text"] for c in t["columns"]]
            t["tw_table"] = "TW >" in t["condition"]

        # Data rows
        k = i + 1
        while k < len(lines):
            row = lines[k]
            toks = row.tokens
            if not toks or row.text.startswith("High QNH") or "QNH" in row.text:
                break
            first = toks[0]
            if not (SIGNED_RE.match(first["text"]) and abs(first["xc"] - oat_x) < 12):
                break
            oat = int(first["text"])
            rest = toks[1:]
            trq = None
            if torque and rest and abs(rest[0]["xc"] - torque["xc"]) < 15 and NUM_RE.match(rest[0]["text"]):
                trq = float(rest[0]["text"])
                rest = rest[1:]
            climb = None
            if limit and rest and abs(rest[-1]["xc"] - limit["xc"]) < 15 and rest[-1]["text"].isdigit():
                climb = int(rest[-1]["text"]) * 10
                rest = rest[:-1]
            for t in gtabs:
                t["rows"][oat] = {"torque": trq, "climb_limit_kg": climb, "cells": [None] * len(t["columns"])}
            for tok in split_cells(row, rest, source):
                txt = tok["text"]
                allcols = [(gi, ci, c) for gi, t in enumerate(gtabs) for ci, c in enumerate(t["columns"])]
                gi, ci, col = nearest(tok["xc"], allcols, lambda a: a[2]["xc"])
                if abs(tok["xc"] - col["xc"]) > 20:
                    raise ValueError(f"{source}: cell '{txt}' at x={tok['xc']:.0f} not under a column")
                t = gtabs[gi]
                m = (TW_CELL_RE if t["tw_table"] else CELL_RE).match(txt)
                if not m:
                    raise ValueError(f"{source}: unparseable cell '{txt}' (OAT {oat}, {t['condition']})")
                cell = {"mrtw_kg": int(m.group(1)) * 10, "limit": m.group(2)}
                if t["tw_table"]:
                    cell.update(v1=speed(m.group(3)), vr=speed(m.group(4)), v2=speed(m.group(5)))
                else:
                    cell["max_v1"] = speed(m.group(3))
                if t["rows"][oat]["cells"][ci] is not None:
                    raise ValueError(f"{source}: two cells in one column at OAT {oat}")
                t["rows"][oat]["cells"][ci] = cell
            k += 1

        # QNH adjustment lines directly below the rows.
        while k < len(lines) and "QNH" in lines[k].text:
            ws = lines[k].words()
            labels = [j for j, w in enumerate(ws) if w["text"] in ("High", "Low")]
            for n, j in enumerate(labels):
                end = labels[n + 1] if n + 1 < len(labels) else len(ws)
                # value sits between "QNH" and "per"; blank means no adjustment published
                seg = ws[j + 2:end]
                seg = seg[:next((n for n, w in enumerate(seg) if w["text"] == "per"), len(seg))]
                vals = [w for w in seg if SIGNED_RE.match(w["text"])]
                key = ws[j]["text"].lower()
                lab_x = ws[j]["xc"]
                if len(labels) == 1:  # one adjustment for the whole section
                    targets = gtabs
                else:  # one pair per table: the label sits at the table's left edge
                    left = [g for g in range(len(gtabs)) if spans[g][0] - 25 <= lab_x]
                    targets = [gtabs[max(left, key=lambda g: spans[g][0])]] if left else []
                for t in targets:
                    t["qnh"][key] = int(vals[0]["text"]) if vals else None
            k += 1

        for t in gtabs:
            # Drop OAT rows with no data at all (blank tables, e.g. NOTAM shortened runways).
            t["rows"] = {o: r for o, r in t["rows"].items() if any(r["cells"])}
            del t["columns"]
            tables.append(t)
    return tables


def wind_value(label):
    """'-10T' -> -10, '+10H' -> 10, '+0' -> 0 (headwind positive)."""
    m = WIND_RE.match(label)
    return int(m.group(1))


def extract(paths):
    runways = {}
    order = []
    for path in paths:
        with pdfplumber.open(path) as pdf:
            for pno, page in enumerate(pdf.pages, 1):
                lines = page_lines(page)
                if not lines:
                    continue
                text = "\n".join(l.text for l in lines)
                if "BLEEDS" not in text or not any(is_header(l) for l in lines):
                    continue
                if "Handbook" in text:
                    continue  # explanatory sample chart (section 2.2), not airport data
                title = parse_title(lines)
                if title is None:
                    raise ValueError(f"{path} p{pno}: takeoff table without a title line")
                src = f"{path.split('/')[-1]} p{pno}"
                key = f"{title['iata']} {title['runway']}"
                rw = runways.get(key)
                if rw is None:
                    rw = runways[key] = {**title, "id": key, "meta": {}, "tables": [], "pages": []}
                    order.append(key)
                rw["pages"].append(src)
                rw["meta"].update(parse_footer(lines))
                efp = parse_efp(lines)
                if efp:
                    rw["engine_failure"] = efp
                for t in parse_tables(lines, src):
                    if not t["rows"]:
                        continue
                    t["winds"] = [wind_value(w) for w in t.pop("wind_labels")]
                    t["rows"] = [dict(oat=o, **r) for o, r in sorted(t["rows"].items(), reverse=True)]
                    dup = [x for x in rw["tables"] if x["condition"] == t["condition"]
                           and x["bleeds"] == t["bleeds"] and x["flaps"] == t["flaps"]]
                    if dup and all(d["rows"] == t["rows"] for d in dup):
                        continue  # identical table repeated on a flap-variant page
                    rw["tables"].append(t)
    return [runways[k] for k in order]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdfs", nargs="+", help="handbook PDFs, in handbook page order")
    ap.add_argument("-o", "--output", default="data/takeoff.json")
    a = ap.parse_args(argv)
    data = extract(a.pdfs)
    with open(a.output, "w") as f:
        json.dump({"aircraft": "DHC-8-402 PW150A", "runways": data}, f, separators=(",", ":"))
    ntab = sum(len(r["tables"]) for r in data)
    print(f"{len(data)} runways, {ntab} tables -> {a.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
