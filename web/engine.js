// Q400 takeoff performance engine: JavaScript port of q400perf/calc.py and q400perf/speedbook.py.
// Kept rule-for-rule identical to the Python; tests/test_web_parity.py compares the two.
(function (root) {
  "use strict";

  class PerfError extends Error {}

  // Compact data (built by web/build.py):
  //  runway: {i, c, r, e, el, t: [table]}
  //  table:  {b, c, f, w, q: [high, low], s, rows: [[oat, torque, climbKg, cell...]]}
  //  cell:   [kg, limit, maxV1] | [kg, limit, v1, vr, v2] | 0 (blank)

  const CONFIGS = {
    "normal": null,
    "gear-extended": "GEAR EXTENDED",
    "ground-spoilers-inop": "SPLRS INOP",
    "nws-inop": "NW STEER INOP",
    "anti-skid-inop": "ANTI-SKID INOP",
  };

  function selectTables(rw, surface, bleeds, power, config, flaps, wind) {
    surface = surface.toUpperCase(); bleeds = bleeds.toUpperCase(); power = power.toUpperCase();
    let want;
    if (wind < -10) {
      if (!(surface === "DRY" && bleeds === "OFF" && power === "NTOP" && config === "normal"))
        throw new PerfError("Tailwind above 10 kt: data only for dry runway, bleeds off, NTOP, normal configuration.");
      want = (t) => t.c === "NTOP DRY - TW > 10KTS";
    } else if (config === "normal") {
      if (bleeds === "ON" && power === "MTOP") throw new PerfError("MTOP data is only published for bleeds off.");
      const cond = `${surface} - ${power}`;
      want = (t) => t.c === cond && t.b === bleeds;
    } else {
      if (bleeds !== "OFF" || power !== "NTOP")
        throw new PerfError("Non-normal configuration data is only published for bleeds off, NTOP.");
      if (config === "anti-skid-inop" && surface === "WET")
        throw new PerfError("Anti-skid inop data is only published for a dry runway.");
      const key = CONFIGS[config];
      want = (t) => t.c.startsWith(surface + " /") && t.c.endsWith(key);
    }
    const tables = rw.t.filter((t) => want(t) && (flaps == null || t.f === flaps));
    if (!tables.length) {
      const have = [...new Set(rw.t.filter(want).map((t) => t.f))].sort((a, b) => a - b);
      throw new PerfError(`No ${surface.toLowerCase()} / bleeds ${bleeds.toLowerCase()} / ${power} / ${config} data for ${rw.i} ${rw.r}` +
        (have.length ? ` (flaps available: ${have.join(", ")})` : "") + ".");
    }
    return tables;
  }

  function bracket(values, x) {
    if (x <= values[0]) return [0, 0, 0];
    for (let i = 0; i < values.length - 1; i++) {
      if (values[i] <= x && x <= values[i + 1]) {
        if (values[i] === x) return [i, i, 0];
        if (values[i + 1] === x) return [i + 1, i + 1, 0];
        return [i, i + 1, (x - values[i]) / (values[i + 1] - values[i])];
      }
    }
    throw new PerfError(`${x} is above the chart range (max ${values[values.length - 1]}); extrapolation is not allowed.`);
  }

  const lerp = (a, b, f) => a + (b - a) * f;
  const SPEED_KEYS = ["max_v1", "v1", "vr", "v2"];

  function cellObj(c) {
    if (!c) return null;
    return c.length === 3 ? { mrtw: c[0], limit: c[1], max_v1: c[2] } : { mrtw: c[0], limit: c[1], v1: c[2], vr: c[3], v2: c[4] };
  }

  function mix(a, b, f) {
    const out = { mrtw: lerp(a.mrtw, b.mrtw, f), limit: a.mrtw <= b.mrtw ? a.limit : b.limit };
    for (const k of SPEED_KEYS) if (k in a) out[k] = lerp(a[k], b[k], f);
    return out;
  }

  function cellAtWind(row, winds, w) {
    const [i, j, f] = bracket(winds, w);
    const a = cellObj(row[3 + i]), b = cellObj(row[3 + j]);
    if (!a || !b) return null;
    return mix(a, b, f);
  }

  function effectiveWind(table, wind, headwindCredit) {
    const winds = table.w;
    const w = headwindCredit ? wind : Math.min(wind, 0);
    if (Math.min(...winds) < -10) {
      if (wind < -20) throw new PerfError("Tailwind above 20 kt: no data.");
      return Math.max(Math.min(w, winds[winds.length - 1]), winds[0]);
    }
    if (w < winds[0]) throw new PerfError(`Tailwind ${-wind} kt exceeds the ${-winds[0]} kt column.`);
    return Math.min(w, winds[winds.length - 1]);
  }

  function qnhAdjustment(table, qnh, highCredit) {
    const [high, low] = table.q;
    if (qnh < 1013 && low) return low * (1013 - qnh);
    if (qnh > 1013 && highCredit && high) return high * (qnh - 1013);
    return 0;
  }

  function sortedRows(table) {
    return table.rows.slice().sort((a, b) => a[0] - b[0]);
  }

  function rowValues(table, oat, w) {
    const rows = sortedRows(table);
    const oats = rows.map((r) => r[0]);
    const [i, j, f] = bracket(oats, oat);
    const a = cellAtWind(rows[i], table.w, w), b = cellAtWind(rows[j], table.w, w);
    if (!a || !b) throw new PerfError(`No chart data at OAT ${oat} °C for this wind (blank in the handbook).`);
    const out = mix(a, b, f);
    if (rows[i][1] != null && rows[j][1] != null) out.torque = lerp(rows[i][1], rows[j][1], f);
    if (rows[i][2] != null && rows[j][2] != null) out.climb = lerp(rows[i][2], rows[j][2], f);
    if (oat < oats[0]) out.note = `OAT below lowest chart temperature; ${oats[0]} °C row used.`;
    return out;
  }

  function calculate(rw, table, oat, o) {
    const qnh = o.qnh ?? 1013, wind = o.wind ?? 0, hw = o.headwindCredit ?? true, hq = o.highQnhCredit ?? false;
    const w = effectiveWind(table, wind, hw);
    const v = rowValues(table, oat, w);
    const adj = qnhAdjustment(table, qnh, hq);
    const res = {
      runway: rw, table, oat, wind, qnh, tow: o.tow ?? null,
      mrtw: Math.floor(v.mrtw + adj), limit: v.limit, qnhAdj: Math.round(adj),
      maxV1: null, torque: null, climb: null, v1: null, vr: null, v2: null,
      vfri: null, vclimb: null, assumedTemp: null, speedsSource: null, notes: [],
    };
    if (v.note) res.notes.push(v.note);
    if (w !== wind) res.notes.push(`Wind credited as ${w > 0 ? "+" : ""}${w} kt.`);
    if (v.torque != null) res.torque = Math.round(v.torque * 10) / 10;
    if (v.climb != null) res.climb = Math.trunc(v.climb);
    if ("max_v1" in v) res.maxV1 = Math.floor(v.max_v1);
    else {
      res.vr = Math.ceil(v.vr); res.v2 = Math.ceil(v.v2);
      res.v1 = Math.min(Math.floor(v.v1), res.vr);
      res.speedsSource = "Runway analysis, Supplement 3 (tailwind 10-20 kt)";
    }
    if (res.tow != null && res.tow > res.mrtw) res.notes.push(`TOW ${Math.round(res.tow)} kg exceeds MRTW ${res.mrtw} kg.`);
    if (o.card) {
      const [c1, cr, c2] = o.card;
      res.vr = cr; res.v2 = c2; res.speedsSource = "Entered speeds";
      if (res.maxV1 != null && c1 > res.maxV1) {
        res.notes.push(`V1 ${c1} reduced to MAX V1 ${res.maxV1} (VR and V2 unchanged).`);
        res.v1 = res.maxV1;
      } else res.v1 = c1;
    }
    return res;
  }

  function assumedTemperature(rw, table, oat, tow, o) {
    if (table.b === "ON" || table.c.includes("MTOP"))
      throw new PerfError("Bleeds ON and MTOP data are not valid for reduced power.");
    const w = effectiveWind(table, o.wind ?? 0, o.headwindCredit ?? true);
    const adj = qnhAdjustment(table, o.qnh ?? 1013, o.highQnhCredit ?? false);
    let best = null;
    for (const row of sortedRows(table)) {
      if (row[0] <= oat) continue;
      const c = cellAtWind(row, table.w, w);
      if (!c || c.mrtw + adj < tow) continue;
      if (o.minTorque != null && row[1] != null && row[1] < o.minTorque) continue;
      best = row[0];
    }
    if (best == null) return null;
    const res = calculate(rw, table, best, { ...o, tow, card: null });
    res.assumedTemp = best;
    res.oat = oat;
    return res;
  }

  // ---- Speed book ----
  const ALTS = [0, 2000, 4000, 6000, 8000, 10000];
  const TEMPS = [-54, 0, 10, 20, 30, 40, 50];

  function pressureAltitude(elev, qnh) {
    return elev + 145442.16 * (1 - Math.pow(qnh / 1013.25, 0.190263));
  }

  function additive(table, alt, temp) {
    alt = Math.max(alt, ALTS[0]);
    temp = Math.max(temp, TEMPS[0]);
    if (alt > ALTS[ALTS.length - 1]) throw new PerfError(`Pressure altitude ${Math.round(alt)} ft is above the speed book range (10,000 ft).`);
    if (temp > TEMPS[TEMPS.length - 1]) throw new PerfError(`Temperature ${temp} °C is above the speed book range (50 °C).`);
    const [i, j, fa] = bracket(ALTS, alt);
    const [k, m, ft] = bracket(TEMPS, temp);
    const lo = table[i][k] + (table[i][m] - table[i][k]) * ft;
    const hi = table[j][k] + (table[j][m] - table[j][k]) * ft;
    return Math.ceil(lo + (hi - lo) * fa - 1e-9);
  }

  function takeoffSpeeds(book, tow, flaps, pa, powerTemp, oat, maxV1) {
    const weights = Object.keys(book).map(Number);
    const pageW = Math.floor(tow / 500) * 500;
    if (pageW < Math.min(...weights)) throw new PerfError(`TOW ${Math.round(tow)} kg is below the speed book range (${Math.min(...weights)} kg).`);
    if (pageW > Math.max(...weights)) throw new PerfError(`TOW ${Math.round(tow)} kg is above the speed book range (${Math.max(...weights)} kg page).`);
    if (![5, 10, 15].includes(flaps)) throw new PerfError("The speed book covers takeoff flaps 5, 10 and 15.");
    const page = book[pageW], s = page.flaps[flaps], notes = [];
    const add = additive(page.v1vr_add, pa, powerTemp);
    let vr = s.v1vr + add;
    if (s.v1vr_red && vr < page.min_v1vr[flaps]) {
      notes.push(`V1/VR ${vr} raised to minimum V1/VR ${page.min_v1vr[flaps]}.`);
      vr = page.min_v1vr[flaps];
    }
    let v2 = s.v2, v2Add = 0;
    if (s.v2_red) { v2Add = additive(page.min_v2_add, pa, oat); v2 += v2Add; }
    if (v2 < vr) { notes.push(`V2 ${v2} raised to VR ${vr}.`); v2 = vr; }
    let v1 = vr;
    if (maxV1 != null && maxV1 < v1) { notes.push(`V1 ${v1} limited to runway analysis MAX V1 ${maxV1} (VR and V2 unchanged).`); v1 = maxV1; }
    return { page: pageW, flaps, v1, vr, v2, vfri: s.vfri, vclimb: page.vclimb, v1vrAdd: add, v2Add, notes };
  }

  function applySpeedbook(book, r) {
    if (r.tow == null || r.speedsSource != null) return;
    const pa = pressureAltitude(r.runway.el, r.qnh);
    const powerTemp = r.assumedTemp != null ? r.assumedTemp : r.oat;
    const sp = takeoffSpeeds(book, r.tow, r.table.f, pa, powerTemp, r.oat, r.maxV1);
    Object.assign(r, { v1: sp.v1, vr: sp.vr, v2: sp.v2, vfri: sp.vfri, vclimb: sp.vclimb, pa, book: sp });
    r.speedsSource = `Speed book ${sp.page.toLocaleString("en-US")} kg page · PA ${Math.round(pa)} ft · V1/VR +${sp.v1vrAdd}` +
      (sp.v2Add ? ` · min V2 +${sp.v2Add}` : "");
    r.notes.push(...sp.notes);
  }

  // One complete run, as the CLI does for each matching table.
  function run(data, rw, inp) {
    const tables = selectTables(rw, inp.surface, inp.bleeds, inp.power, inp.config, inp.flaps ?? null, inp.wind ?? 0);
    return tables.map((t) => {
      let r;
      if (inp.assumed) {
        if (inp.tow == null) throw new PerfError("Reduced power needs the takeoff weight.");
        r = assumedTemperature(rw, t, inp.oat, inp.tow, inp);
        if (!r) throw new PerfError("No assumed temperature available for this weight: use full power.");
      } else {
        r = calculate(rw, t, inp.oat, inp);
      }
      applySpeedbook(data.book, r);
      return r;
    });
  }

  const api = { PerfError, CONFIGS, selectTables, calculate, assumedTemperature, pressureAltitude, takeoffSpeeds, applySpeedbook, run };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Q400 = api;
})(typeof window !== "undefined" ? window : globalThis);
