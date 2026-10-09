"""The iPad web app's JavaScript engine must give exactly the Python results.

Runs both on randomly generated conditions across every runway. Skipped when node is missing.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))

from build import data_bundle  # noqa: E402
from q400perf.__main__ import run_one  # noqa: E402
from q400perf.calc import CONFIGS, PerfError, load  # noqa: E402

FIELDS = ["mrtw_kg", "limit", "max_v1", "climb_limit_kg", "v1", "vr", "v2", "vfri", "vclimb", "assumed_temp"]
JS_FIELDS = ["mrtw", "limit", "maxV1", "climb", "v1", "vr", "v2", "vfri", "vclimb", "assumedTemp"]

NODE_SCRIPT = r"""
const fs = require("fs");
const Q = require(process.argv[2]);
const data = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const cases = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const out = cases.map((c) => {
  const rw = data.runways.find((r) => r.i === c.airport && r.r === c.runway);
  try {
    return Q.run(data, rw, c).map((r) => ({
      mrtw: r.mrtw, limit: r.limit, maxV1: r.maxV1, climb: r.climb, v1: r.v1, vr: r.vr, v2: r.v2,
      vfri: r.vfri, vclimb: r.vclimb, assumedTemp: r.assumedTemp, torque: r.torque }));
  } catch (e) {
    if (e instanceof Q.PerfError) return "error";
    throw e;
  }
});
process.stdout.write(JSON.stringify(out));
"""


def make_cases(n, seed=7):
    rnd = random.Random(seed)
    runways = load()
    cases = []
    for _ in range(n):
        rw = rnd.choice(runways)
        tow = rnd.choice([None, round(rnd.uniform(17800, 30000))])
        cases.append({
            "airport": rw["iata"], "runway": rw["runway"],
            "oat": rnd.choice([rnd.randint(-35, 50), round(rnd.uniform(-35, 50), 1)]),
            "qnh": rnd.choice([1013, rnd.randint(985, 1035)]),
            "wind": rnd.choice([0, rnd.randint(-22, 25), round(rnd.uniform(-20, 15), 1)]),
            "surface": rnd.choice(["dry", "wet"]), "bleeds": rnd.choice(["off", "off", "on"]),
            "power": rnd.choice(["ntop", "ntop", "mtop"]),
            "config": rnd.choice(["normal"] * 4 + list(CONFIGS)),
            "flaps": None, "tow": tow, "assumed": tow is not None and rnd.random() < 0.25,
            "headwindCredit": rnd.random() < 0.8, "highQnhCredit": rnd.random() < 0.2,
            "minTorque": None,
        })
    return runways, cases


def python_results(runways, cases):
    out = []
    for c in cases:
        a = argparse.Namespace(oat=c["oat"], qnh=c["qnh"], wind=c["wind"], surface=c["surface"], bleeds=c["bleeds"],
                               power=c["power"], config=c["config"], flaps=c["flaps"], tow=c["tow"], card=None,
                               assumed=c["assumed"], min_torque=None, no_headwind_credit=not c["headwindCredit"],
                               high_qnh_credit=c["highQnhCredit"])
        try:
            res = run_one(runways, c["airport"], c["runway"], a)
        except PerfError:
            out.append("error")
            continue
        out.append([{**{f: getattr(r, f) for f in FIELDS}, "torque": r.torque} for r in res])
    return out


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WebParityTest(unittest.TestCase):
    def test_js_engine_matches_python(self):
        runways, cases = make_cases(4000)
        expected = python_results(runways, cases)
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for name, obj in (("data", data_bundle()), ("cases", cases)):
                paths[name] = os.path.join(d, name + ".json")
                with open(paths[name], "w") as f:
                    json.dump(obj, f)
            script = os.path.join(d, "run.js")
            with open(script, "w") as f:
                f.write(NODE_SCRIPT)
            out = subprocess.run(["node", script, os.path.join(ROOT, "web", "engine.js"), paths["data"],
                                  paths["cases"]], capture_output=True, text=True, check=True).stdout
        got = json.loads(out)
        n_ok = n_err = 0
        for c, e, g in zip(cases, expected, got):
            if e == "error" or g == "error":
                self.assertEqual(e, g, c)
                n_err += 1
                continue
            self.assertEqual(len(e), len(g), c)
            for er, gr in zip(e, g):
                for pf, jf in zip(FIELDS, JS_FIELDS):
                    self.assertEqual(er[pf], gr[jf], (pf, c))
                if er["torque"] is not None:
                    self.assertAlmostEqual(er["torque"], gr["torque"], delta=0.11, msg=c)
                n_ok += 1
        print(f"\n  compared {n_ok} results, {n_err} rejected inputs", file=sys.stderr)
        self.assertGreater(n_ok, 1500)   # plenty of successful results compared
        self.assertGreater(n_err, 100)   # and plenty of rejected inputs


if __name__ == "__main__":
    unittest.main()
