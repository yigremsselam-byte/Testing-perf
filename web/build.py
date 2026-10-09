"""Build the single-file iPad web app: dist/q400-takeoff.html

    python web/build.py

Embeds data/takeoff.json (compacted), data/speedbook.txt and web/engine.js into web/app.html,
so the page needs no network connection.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from q400perf.calc import load  # noqa: E402
from q400perf.speedbook import load_speedbook  # noqa: E402


def compact_cell(c):
    if c is None:
        return 0
    if "max_v1" in c:
        return [c["mrtw_kg"], c["limit"], c["max_v1"]]
    return [c["mrtw_kg"], c["limit"], c["v1"], c["vr"], c["v2"]]


def compact_runways(runways):
    out = []
    for r in runways:
        efp = r.get("engine_failure") or {}
        proc = "  ".join(f"{k}: {v}" for k, v in (efp.get("procedure") or {}).items())
        out.append({
            "i": r["iata"], "c": r["city"], "r": r["runway"], "e": r["efp"], "p": proc,
            "el": r["meta"].get("elevation", 0), "len": r["meta"].get("length"), "sl": r["meta"].get("slope"),
            "t": [{
                "b": t["bleeds"], "c": t["condition"], "f": t["flaps"], "w": t["winds"],
                "q": [t["qnh"]["high"], t["qnh"]["low"]], "s": t["source"],
                "rows": [[row["oat"], row["torque"], row["climb_limit_kg"]] + [compact_cell(c) for c in row["cells"]]
                         for row in t["rows"]],
            } for t in r["tables"]],
        })
    return out


def data_bundle():
    return {"runways": compact_runways(load()), "book": load_speedbook()}


def main():
    data = json.dumps(data_bundle(), separators=(",", ":"))
    with open(os.path.join(ROOT, "web", "engine.js")) as f:
        engine = f.read()
    with open(os.path.join(ROOT, "web", "app.html")) as f:
        html = f.read()
    for marker, value in (("/*__ENGINE__*/", engine), ("/*__DATA__*/null", data)):
        if marker not in html:
            raise SystemExit(f"marker {marker} missing from app.html")
        html = html.replace(marker, value)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "q400-takeoff.html")
    with open(out, "w") as f:
        f.write('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n' + html)
    print(f"{out}: {len(html) / 1e6:.2f} MB", file=sys.stderr)
    if len(sys.argv) > 1:  # page body only, for hosts that supply their own document skeleton
        with open(sys.argv[1], "w") as f:
            f.write(html)


if __name__ == "__main__":
    main()
