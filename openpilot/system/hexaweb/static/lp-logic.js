// LizPilot: what the phone panel decides, apart from the page so it can be tested without a phone
// (system/hexaweb/tests/test_panel_logic.py runs it in node). Nothing here touches the page or the network.
// Texts come back in Portuguese, marked with T() for the catalogs; the page translates them with LP.t / LP.tf.
(function (root) {
  "use strict";
  const T = (s) => s;

  const STALE_MS = 3500;  // live data is asked every second: older than this is not "now"
  const BUMP_MIN = 5, BUMP_MAX = 60;  // radars.BUMP_SPEEDS
  const ACC_MIN = 30;  // [log] in 12 routes the Haval ACC was never set below 30 km/h
  const LAT_MS = 2000;  // "steering only" has to last this long, moving, before the band says so

  // The band at the top while driving. Never a claim the data can't back:
  //   none  gray   no data, data too old, car off
  //   fail  red    critical alert
  //   warn  amber  alert asking for the driver
  //   op    green  openpilot steering
  //   lat   green  openpilot steering, the Haval ACC not in control for 2 s while moving: the speed is the driver's
  //   you   blue   the driver steering
  // l: /api/live · link: { online, age, now } in ms · latSince: when "steering only" started, from the last call
  // [log] 3 routes: with openpilot engaged and acc_active false the car is mostly stopped behind another one, held by
  // the ACC (state 2); moving, mostly ≤ 3 s of foot on the gas. Stopped, nothing is said about the ACC.
  function band(l, link, latSince) {
    const fresh = !!(link && link.online && link.age <= STALE_MS);
    if (!l || !fresh) {
      return { cls: "none", who: T("sem dados"), sub: link && link.online ? T("dado atrasado: não dá para dizer quem dirige") : T("sem conexão com o comma"),
               why: null, acc: null, stale: true, latSince: null };
    }
    if (!l.driving) return { cls: "none", who: T("carro desligado"), sub: "", why: null, acc: null, stale: false, latSince: null };
    const steer = !!l.steering, moving = l.v > 0;
    const paused = steer && moving && l.acc_active === false;
    const since = paused ? (latSince == null ? link.now : latSince) : null;
    const lat = paused && link.now - since >= LAT_MS;
    const acc = l.acc_active === true && l.acc_set > 0 ? l.acc_set : null;  // the set speed means something only with the ACC in control
    const status = l.alert_status;  // normal, userPrompt, critical; missing on an older comma
    if (status === "critical") return { cls: "fail", who: T("assuma o volante"), sub: "", why: null, acc, stale: false, latSince: since };  // the alert says why
    let who;
    if (!steer) who = T("você dirigindo");
    else if (lat) who = T("só direção · a velocidade é sua");
    else if (l.driver) who = T("você e openpilot na direção");
    else if (moving && l.acc_active === true) who = T("openpilot dirigindo");
    else who = T("openpilot na direção");  // stopped, or the ACC state not known or too short to say
    let cls = steer ? (lat ? "lat" : "op") : "you";
    if (status === "userPrompt" || (status == null && l.alert)) cls = "warn";
    const why = !l.engaged && l.why && l.why.length ? l.why : null;
    const took = !steer ? null : l.brake ? T("você no freio") : l.gas ? T("você no acelerador") : l.driver ? T("você no volante") : null;
    const sub = why ? T("não engata: {motivos}") : took || (lat ? T("ACC desligado") : steer ? T("volante ativo") : T("openpilot desligado"));
    return { cls, who, sub, why, acc, stale: false, latSince: since };
  }

  // With the car off the comma can't see the panda, the cameras nor the calibration: it only says what it checked.
  // ok: true checked and fine · false checked and not fine · null information, not a check
  function commaChecks(online, h) {
    if (!online) return { cls: "att", title: T("Sem dados do comma"), sub: "", items: [] };
    if (!h || !h.device) return { cls: "", title: T("Conferindo o comma…"), sub: "", items: [] };
    const d = h.device, n = h.learned || {}, items = [];
    if (h.down) items.push(h.down.length ? { ok: false, text: T("Processo parado: {nomes}"), vars: { nomes: h.down.join(", ") } } : { ok: true, text: T("Processos rodando") });
    if (d.temp !== undefined) {
      const ok = !["overheated", "critical"].includes(d.thermal);  // deviceState.thermalStatus
      items.push({ ok, text: ok ? T("Comma {graus} °C") : T("Comma quente: {graus} °C"), vars: { graus: d.temp } });
    }
    if (d.free !== undefined) items.push({ ok: d.free >= 10, text: d.free >= 10 ? T("Disco {livre}% livre") : T("Disco quase cheio: {livre}% livre"), vars: { livre: d.free } });
    if (h.commit) items.push({ ok: null, text: T("Versão {versao}"), vars: { versao: h.commit } });
    // learned only shows up after a trip since the comma started: missing is not a failure
    items.push(n.torque ? { ok: null, text: T("Aprendizado {pct}%"), vars: { pct: n.torque.percent } } : { ok: null, text: T("Aprendizado aparece depois de dirigir") });
    const bad = items.some((i) => i.ok === false);
    return { cls: bad ? "att" : "ok", title: bad ? T("Atenção no comma") : T("Comma ok"),
             sub: T("Conferido agora: processos, temperatura e disco. Câmeras, panda e calibração só com o carro ligado."), items };
  }

  // Centimeters only with both lines seen: one weak line makes the middle a guess.
  function laneText(ln) {
    if (!ln) return null;
    if (!ln.left_ok && !ln.right_ok) return { text: T("Linhas da faixa pouco visíveis") };
    if (!ln.left_ok || !ln.right_ok) return { text: ln.left_ok ? T("Só a linha da esquerda está visível") : T("Só a linha da direita está visível") };
    const left = Math.abs(ln.left), right = Math.abs(ln.right), off = (left - right) / 2;  // > 0: right of the middle
    const cm = Math.round(Math.abs(off) * 100);
    if (cm < 5) return { text: T("No centro da faixa") };
    return { text: off > 0 ? T("Na faixa · {cm} cm à direita") : T("Na faixa · {cm} cm à esquerda"), vars: { cm } };
  }

  // When a change takes effect, per option.
  function applies(o) { return o.restart_needed ? T("Vale na próxima partida do carro.") : T("Vale no próximo engate."); }

  // Speeds offered in the point editor. A bump keeps the speed it was passed at and asks for the one to use.
  function speedChoices(kind, passed, limit) {
    const out = [{ v: null, label: T("Não sei") }];
    if (kind !== "lombada") {
      for (let v = 20; v <= 120; v += 10) out.push({ v, label: String(v) });
      return out;
    }
    const keep = passed >= BUMP_MIN && passed <= BUMP_MAX ? passed : null;
    if (keep) out.push({ v: keep, label: T("Manter {n}"), vars: { n: keep } });
    const speeds = [...new Set([10, 15, 20, 25, 30, 40, 50, limit].filter((v) => v >= BUMP_MIN && v <= BUMP_MAX))].sort((a, b) => a - b);
    for (const v of speeds) if (v !== keep) out.push({ v, label: String(v) });
    return out;
  }

  function keptSpeed(kind, passed) { return kind === "lombada" && passed >= BUMP_MIN && passed <= BUMP_MAX ? passed : null; }

  // What the comma will do at a bump, by the chosen speed. Information only: it never blocks Salvar, and it never
  // says the car will slow down by itself where nothing does.
  // ctx: { alert: RadarAlert on, slowdown: BumpSlowdown on, long: AlphaLongitudinalEnabled, calibrated: LONG_CALIBRATED }
  function bumpMessage(speed, ctx) {
    if (!speed) return "";
    if (!ctx.alert) return T("Com o aviso de radar e lombada desligado, o comma não avisa nem reduz. Ligue em Ajustes.");
    if (speed >= ACC_MIN) return ctx.slowdown ? T("Em teste: o comma toca o − do ACC até essa velocidade.") : T("Hoje só avisa. Ligue reduzir na lombada em Ajustes.");
    if (!ctx.long) return T("O ACC da Haval não vai abaixo de 30 km/h. Aqui o comma só avisa. Para reduzir, é preciso o longitudinal do comma.");
    if (!ctx.calibrated) return T("O longitudinal do comma ainda não foi testado nem calibrado neste carro. Aqui o comma só avisa.");
    return T("Aqui o comma só avisa.");  // the text for a calibrated longitudinal comes with the calibration
  }

  // lizpilot.local: one address for the panel, whatever network the comma is on (mdns.py on the comma).
  // A phone keeps the panel (and its pairing) by address, so the fixed one is offered once, and taken before pairing.
  //   fixed  already there · move  not paired yet: go there before typing the code · offer  paired here: suggest it
  //   ip     the name doesn't answer from this phone (some Android, some networks): stay by address
  const FIXED_HOST = "lizpilot.local";
  function addressPlan(host, reachable, paired) {
    if (String(host || "").toLowerCase() === FIXED_HOST) return "fixed";
    if (!reachable) return "ip";
    return paired ? "offer" : "move";
  }

  // Who had the wheel in a drive (timeline.trip_summary): openpilot alone, openpilot with your hand, you alone, all
  // while moving; stopped is apart. Light, never a score: helping when needed is right, so it's never "less is better".
  const MIN_S = 60;
  function minutes(x) { return Math.round((x || 0) / MIN_S); }
  function duration(min) { return min >= 60 ? { text: T("{h} h {m} min"), vars: { h: Math.floor(min / 60), m: min % 60 } } : { text: T("{m} min"), vars: { m: min } }; }
  function tripSplit(s) {
    if (!s || !(s.moving_s > 0)) return null;
    const pct = (x) => 100 * (x || 0) / s.moving_s;
    const parts = [
      { key: "op", label: T("openpilot sozinho"), min: minutes(s.op_s), km: s.op_km || 0, pct: pct(s.op_s) },
      { key: "help", label: T("openpilot com a sua mão"), min: minutes(s.help_s), km: s.help_km || 0, pct: pct(s.help_s) },
      { key: "you", label: T("você sozinho"), min: minutes(s.you_s), km: s.you_km || 0, pct: pct(s.you_s) },
    ];
    const op = (s.op_s || 0) >= 30;
    return {
      parts, movingMin: minutes(s.moving_s), stopMin: minutes(s.stop_s), km: s.km || 0,
      big: duration(op ? parts[0].min : parts[2].min),
      line: op ? T("de volante por conta do openpilot") : T("você dirigiu nesta viagem"),
      sub: { text: s.stop_s >= 30 ? T("Você ajudou em {ajudou} min e dirigiu {dirigiu} min. Parado no trânsito: {parado} min.") : T("Você ajudou em {ajudou} min e dirigiu {dirigiu} min."),
             vars: { ajudou: parts[1].min, dirigiu: parts[2].min, parado: minutes(s.stop_s) } },
      short: { text: T("{op} de {andando} min com o openpilot"), vars: { op: parts[0].min, andando: minutes(s.moving_s) } },
      accPct: s.acc_s > 0 ? Math.round(pct(s.acc_s)) : null,  // older drives don't have it
    };
  }

  // The last 7 days added up: a sum, never a streak (a streak would reward not putting the hand on the wheel).
  function weekTotal(trips, nowS) {
    const recent = (trips || []).filter((t) => t.summary && t.mtime >= nowS - 7 * 86400);
    if (!recent.length) return null;
    const op = recent.reduce((a, t) => a + (t.summary.op_s || 0), 0), km = recent.reduce((a, t) => a + (t.summary.km || 0), 0);
    return { op: duration(minutes(op)), km, trips: recent.length };
  }

  // Every drive with a summary since sinceS (0: all of them). Sums and rates, never a score or a streak.
  // Disengagements and alerts per 100 km only from 1 km on, so one short drive doesn't look like a trend.
  function totals(trips, sinceS) {
    const ts = (trips || []).filter((t) => t.summary && t.mtime >= (sinceS || 0));
    if (!ts.length) return null;
    const sum = (k) => ts.reduce((a, t) => a + (t.summary[k] || 0), 0);
    const km = sum("km"), moving = sum("moving_s");
    return { trips: ts.length, km, op: duration(minutes(sum("op_s"))), opPct: moving > 0 ? Math.round(100 * sum("op_s") / moving) : null,
             off: sum("off"), alerts: sum("alerts"), per100: km >= 1 ? { off: 100 * sum("off") / km, alerts: 100 * sum("alerts") / km } : null };
  }
  function monthStart(nowS) { const d = new Date(nowS * 1000); return new Date(d.getFullYear(), d.getMonth(), 1).getTime() / 1000; }
  // The installed version of each drive (its git commit), newest first. Drives summarized before this have none.
  function versionOf(t) { return (t && t.summary && t.summary.commit) || ""; }
  function versions(trips) {
    const seen = [];
    for (const t of trips || []) { const v = versionOf(t); if (v && !seen.includes(v)) seen.push(v); }
    return seen;
  }

  // ----- the whole history on Início (owner, 02/11 01:53): search, filters, days, hidden drives and the map
  // Turned on in the garage and off again is not a drive: out of the lists unless asked for. [hipótese] the limits
  const IDLE_KM = 0.3, IDLE_MOVING_S = 60;
  function idle(t) { const s = t && t.summary; return !!s && (s.km || 0) < IDLE_KM && (s.moving_s || 0) < IDLE_MOVING_S; }
  function fold(s) { return String(s == null ? "" : s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function tripStart(t) { return t.mtime - t.minutes * 60; }
  // marks within the drive, a minute of slack on each side (the clock of a mark vs the log's files)
  function tripMarks(t, marks) { const a = tripStart(t) - 60, b = t.mtime + 60; return (marks || []).filter((m) => m.t >= a && m.t <= b); }
  function midnight(s) { const d = new Date(s * 1000); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }
  function daysAgo(s, nowS) { return Math.round((midnight(nowS) - midnight(s)) / 86400000); }
  function dayKey(s) { const d = new Date(s * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
  // What a drive is found by: its day and times in the panel's language, the version, its marks' reasons and notes.
  // marks: [{t, text}] already translated; words: {today, yesterday} likewise
  function tripWords(t, marks, locale, nowS, words) {
    const d = new Date(tripStart(t) * 1000), end = new Date(t.mtime * 1000);
    const hm = { hour: "2-digit", minute: "2-digit" };
    const out = [d.toLocaleDateString(locale, { weekday: "long" }), d.toLocaleDateString(locale, { month: "long" }),
                 d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit" }), d.toLocaleDateString(locale, { day: "numeric", month: "numeric" }),
                 d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric" }), d.toLocaleTimeString(locale, hm), end.toLocaleTimeString(locale, hm)];
    const ago = daysAgo(tripStart(t), nowS);
    if (ago === 0 && words && words.today) out.push(words.today);
    if (ago === 1 && words && words.yesterday) out.push(words.yesterday);
    const s = t.summary || {};
    if (s.commit) out.push(s.commit);
    if (s.version) out.push(s.version);
    for (const m of tripMarks(t, marks)) out.push(m.text || "");
    return fold(out.join(" "));
  }
  const HISTORY_FILTERS = [["todas", T("Todas")], ["marcas", T("Com marcas")], ["desengates", T("Com desengates")], ["alertas", T("Com alertas")]];
  // The drives to list, newest first. Every word of the search has to be found; a filter narrows further.
  // o: { query, filter, marks: [{t, text}], showHidden, locale, nowS, words }
  // Hidden by the owner and idle drives stay out unless showHidden; the counts say how many each filter would show.
  function history(trips, o) {
    o = o || {};
    const terms = fold(o.query).split(/\s+/).filter(Boolean);
    const all = trips || [];
    const out_ = all.filter((t) => t.hidden || idle(t));
    const pool = o.showHidden ? all : all.filter((t) => !t.hidden && !idle(t));
    const found = terms.length ? pool.filter((t) => { const w = tripWords(t, o.marks, o.locale, o.nowS, o.words); return terms.every((x) => w.includes(x)); }) : pool;
    const test = { todas: () => true, marcas: (t) => tripMarks(t, o.marks).length > 0,
                   desengates: (t) => !!(t.summary && t.summary.off), alertas: (t) => !!(t.summary && t.summary.alerts) };
    const counts = {};
    for (const [k] of HISTORY_FILTERS) counts[k] = found.filter(test[k]).length;
    const f = test[o.filter] ? o.filter : "todas";
    return { list: found.filter(test[f]), counts, filter: f, hidden: out_.filter((t) => t.hidden).length, idle: out_.filter((t) => !t.hidden && idle(t)).length };
  }
  // Drives grouped by the day they started, with the day's km and minutes from the drives that have a summary.
  function byDay(list) {
    const days = [];
    for (const t of list || []) {
      const k = dayKey(tripStart(t));
      let d = days[days.length - 1];
      if (!d || d.key !== k) { d = { key: k, start: tripStart(t), trips: [], km: 0, minutes: 0 }; days.push(d); }
      d.trips.push(t); d.km += (t.summary && t.summary.km) || 0; d.minutes += t.minutes;
    }
    return days;
  }

  // A drive's numbers, as a trip computer shows them. Averages only from 1 km and 1 min on; older summaries lack v_max and run_*.
  // run: the longest stretch without an intervention: a pedal or a push of half a second ends it, a hand resting on the wheel
  // doesn't. A measure of openpilot in that drive, never a record to beat (no streaks: weekTotal).
  function tripNumbers(s) {
    if (!s || !(s.moving_s > 0)) return null;
    const km = s.km || 0;
    return { km, movingMin: minutes(s.moving_s), stopMin: minutes(s.stop_s),
             avg: km >= 1 && s.moving_s >= 60 ? Math.round(km / (s.moving_s / 3600)) : null,
             vmax: s.v_max > 0 ? Math.round(s.v_max) : null,
             runKm: s.run_km > 0 ? s.run_km : null, runMin: s.run_km > 0 ? minutes(s.run_s) : null };
  }
  // Week by week, oldest first, for the trend: km, openpilot's share of the time moving, and the rates per 100 km.
  // Weeks start on Monday; a week without drives shows as empty, so a gap reads as a gap.
  function weekly(trips, nowS, n) {
    const monday = (s) => { const d = new Date(midnight(s)); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.getTime() / 1000; };
    const weeks = [];
    let start = monday(nowS);
    for (let i = 0; i < (n || 8); i++) {
      weeks.unshift({ start, trips: 0, km: 0, moving_s: 0, op_s: 0, off: 0, alerts: 0 });
      const d = new Date(start * 1000); d.setDate(d.getDate() - 7); start = d.getTime() / 1000;
    }
    for (const t of trips || []) {
      if (!t.summary || t.hidden || idle(t)) continue;
      const w = weeks.find((x, i) => tripStart(t) >= x.start && (i === weeks.length - 1 || tripStart(t) < weeks[i + 1].start));
      if (!w) continue;
      w.trips += 1;
      for (const k of ["km", "moving_s", "op_s", "off", "alerts"]) w[k] += t.summary[k] || 0;
    }
    return weeks.map((w) => ({ start: w.start, trips: w.trips, km: w.km, opPct: w.moving_s > 0 ? Math.round(100 * w.op_s / w.moving_s) : null,
                               off100: w.km >= 1 ? 100 * w.off / w.km : null, alerts100: w.km >= 1 ? 100 * w.alerts / w.km : null }));
  }

  // The map: Web Mercator, 256 px tiles (OpenStreetMap). points: [[lat, lon, who, s], ...] from /api/trip/path
  const TILE = 256, MAP_ZMIN = 3, MAP_ZMAX = 17;
  function mercator(lat, lon, z) {
    const n = TILE * Math.pow(2, z), s = Math.sin(lat * Math.PI / 180);
    return [(lon + 180) / 360 * n, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n];
  }
  // The zoom where the whole path fills w × h with pad on every side: z is the tiles' whole zoom, size their size in pixels
  // (256 to 512, the rest of the zoom), xy the path in the box's pixels.
  function mapFit(points, w, h, pad) {
    if (!points || !points.length || w <= 2 * pad || h <= 2 * pad) return null;
    const w0 = points.map((p) => mercator(p[0], p[1], 0));
    const xs = w0.map((p) => p[0]), ys = w0.map((p) => p[1]);
    const bw = Math.max(...xs) - Math.min(...xs), bh = Math.max(...ys) - Math.min(...ys);
    const room = Math.min(bw > 0 ? (w - 2 * pad) / bw : Infinity, bh > 0 ? (h - 2 * pad) / bh : Infinity);
    const zf = Math.max(MAP_ZMIN, Math.min(MAP_ZMAX, Math.log2(room))), z = Math.floor(zf), k = Math.pow(2, zf);
    const ox = (Math.min(...xs) + bw / 2) * k - w / 2, oy = (Math.min(...ys) + bh / 2) * k - h / 2;
    return { z, size: TILE * Math.pow(2, zf - z), ox, oy, xy: w0.map(([x, y]) => [x * k - ox, y * k - oy]) };
  }
  // The tiles under the box: z/x/y and where each goes, in the box's pixels
  function mapTiles(fit, w, h) {
    if (!fit) return [];
    const n = Math.pow(2, fit.z), sz = fit.size, out = [];
    for (let ty = Math.floor(fit.oy / sz); ty * sz < fit.oy + h; ty++) {
      if (ty < 0 || ty >= n) continue;
      for (let tx = Math.floor(fit.ox / sz); tx * sz < fit.ox + w; tx++) {
        out.push({ z: fit.z, x: ((tx % n) + n) % n, y: ty, size: sz, left: tx * sz - fit.ox, top: ty * sz - fit.oy });
      }
    }
    return out;
  }
  // The path point closest in time to s seconds from the start of the drive
  function nearestAt(points, s) {
    let best = -1, gap = Infinity;
    (points || []).forEach((p, i) => { const g = Math.abs(p[3] - s); if (g < gap) { gap = g; best = i; } });
    return best;
  }

  // Shadow mode (selfdrive/hexa/shadow.py), added up over the last 7 days: what openpilot's longitudinal would have done
  // while the Haval ACC drove. Null until a drive has it (openpilot engaged with the ACC driving).
  function weekShadow(trips, nowS) {
    const recent = (trips || []).filter((t) => t.summary && t.mtime >= nowS - 7 * 86400 && (t.summary.sh_on_s || 0) > 0);
    if (!recent.length) return null;
    const sum = (k) => recent.reduce((a, t) => a + (t.summary[k] || 0), 0);
    const gapT = sum("sh_gap_t");
    return { hard: sum("sh_hard"), earlier: sum("sh_earlier"), weaker: sum("sh_weaker"), fcw: sum("sh_fcw"),
             gap: gapT > 0 ? sum("sh_gap_s") / gapT : null, minutes: Math.round(sum("sh_on_s") / 60), trips: recent.length };
  }
  // What the car went through in the last 7 days (selfdrive/hexa/sensors.py): maxima, minima and rates, never a score.
  function weekCar(trips, nowS) {
    const recent = (trips || []).filter((t) => t.summary && t.mtime >= nowS - 7 * 86400 && (t.summary.se_grade_up || t.summary.se_v12_min));
    if (!recent.length) return null;
    const S = (k) => recent.reduce((a, t) => a + (t.summary[k] || 0), 0);
    const M = (k) => { const v = recent.map((t) => t.summary[k] || 0).filter((x) => x > 0); return v.length ? Math.max(...v) : null; };
    const mins = recent.map((t) => t.summary.se_v12_min || 0).filter((x) => x > 0);
    const wheelT = S("se_wheel_t");
    return { up: M("se_grade_up"), down: M("se_grade_down"), roll: M("se_roll"), decel: M("se_decel"), lat: M("se_lat"),
             v12: mins.length ? Math.min(...mins) : null, wheels: wheelT > 30 ? S("se_wheel_s") / wheelT : null };
  }

  // openpilot's three following distances (longitudinal_mpc_lib/long_mpc.py:71-79), in seconds and in meters at a speed
  const PERSONALITIES = [[T("perto"), 1.25], [T("padrão"), 1.45], [T("folgada"), 1.75]];
  function gapMeters(gapS, kmh) { return Math.round(gapS * kmh / 3.6); }

  // With the car moving, the phone is for the passenger. Asked once per trip, before Marcar or a point; never parked,
  // never stored on the phone or the comma. Stopping in traffic doesn't ask again; turning the car off does.
  function passenger(gate, car) {
    const ok = !!(gate && gate.ok) && !!car.on;
    return { ok, ask: !!car.on && !!car.moving && !ok };
  }

  root.LPL = { FIXED_HOST, addressPlan, tripSplit, weekTotal, totals, monthStart, versionOf, versions, idle, fold, tripStart, tripMarks, daysAgo, dayKey,
               tripWords, HISTORY_FILTERS, history, byDay, tripNumbers, weekly, TILE, mercator, mapFit, mapTiles, nearestAt, weekShadow, weekCar, PERSONALITIES, gapMeters, duration, STALE_MS, LAT_MS, BUMP_MIN, BUMP_MAX, ACC_MIN, band, commaChecks, laneText, applies, speedChoices, keptSpeed, bumpMessage, passenger };
  if (typeof module === "object" && module.exports) module.exports = root.LPL;
})(typeof window === "object" ? window : globalThis);
