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
    const sub = why ? T("não engata: {motivos}") : lat ? T("ACC desligado") : steer ? T("volante ativo") : T("openpilot desligado");
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

  // With the car moving, the phone is for the passenger. Asked once per trip, before Marcar or a point; never parked,
  // never stored on the phone or the comma. Stopping in traffic doesn't ask again; turning the car off does.
  function passenger(gate, car) {
    const ok = !!(gate && gate.ok) && !!car.on;
    return { ok, ask: !!car.on && !!car.moving && !ok };
  }

  root.LPL = { STALE_MS, LAT_MS, BUMP_MIN, BUMP_MAX, ACC_MIN, band, commaChecks, laneText, applies, speedChoices, keptSpeed, bumpMessage, passenger };
  if (typeof module === "object" && module.exports) module.exports = root.LPL;
})(typeof window === "object" ? window : globalThis);
