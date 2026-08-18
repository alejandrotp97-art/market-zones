/* Comité de Inversión — capa de decisión sobre el motor de régimen.
   TODO se deriva del payload de /api/regime por activo (mismo que el screener).
   No recalcula ni toca la inferencia: sólo agrega, pondera y presenta. */
(() => {
  "use strict";

  // Values below originate outside this file (the curated list, the API
  // payload). Escape at the point of insertion: the discipline is what
  // protects the NEXT value someone interpolates here, not just today's.
  const esc = (s) => String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  // ── paleta / etiquetas ────────────────────────────────────────────────
  const POS = "#3fae6b", NEG = "#cf5b3a", FLAT = "#d99a2b";
  const EVID = { pos: { c: POS, t: "positiva" }, flat: { c: FLAT, t: "plana" }, neg: { c: NEG, t: "negativa" } };
  const GC = { "A+": POS, "A": POS, "B": "#8bbf3f", "C": FLAT, "D": NEG };
  const REGIME_COLORS = {
    "Pánico": "#8e1b13", "Capitulación": "#cf5b3a", "Recuperación": "#4a9e8f",
    "Alcista sano": "#3fae6b", "Sobrecalentamiento": "#d99a2b", "Clímax": "#b3261e",
    "Distribución": "#b07a3a", "Corrección": "#c96a5e", "Lateral": "#7c828e",
  };
  const GROUP = {
    "SPY": "Renta variable", "QQQ": "Renta variable", "^RUT": "Renta variable", "URTH": "Renta variable",
    "EEM": "Renta variable", "^GDAXI": "Renta variable", "^STOXX50E": "Renta variable", "^N225": "Renta variable",
    "^KS11": "Renta variable", "^HSCE": "Renta variable",
    "GLD": "Metales", "SLV": "Metales", "BZ=F": "Energía", "TLT": "Bonos",
    "NLR": "Uranio", "URA": "Uranio", "URNM": "Uranio", "CCJ": "Uranio",
    "GDX": "Mineras oro", "GDXJ": "Mineras oro",
    "UNH": "Acciones", "KOS": "Acciones", "HGRAF": "Acciones", "BTC-USD": "Cripto",
  };
  const MODEL_VERSION = "régimen v1 · Score causal (percentil expandido) · 3 ejes equipeso · analogías por régimen";

  // ── P4-7 · explicaciones para gestor no cuantitativo ──────────────────
  const WHY = {
    exec: ["Resumen ejecutivo",
      "Sintetiza en una página la recomendación de asignación, su justificación, la evidencia disponible, los riesgos y qué la invalidaría.",
      "Todo se deriva de las mismas métricas del motor; no hay opinión discrecional.",
      "Limitación: describe el estado actual del régimen, no predice puntos de giro."],
    alloc: ["Asignación de capital",
      "Cada peso = convicción × 18% (tope por activo). Convicción combina Oportunidad por encima de neutral, Robustez y Evidencia a 6m.",
      "Hay un tope del 35% por grupo de activos y un colchón mínimo de efectivo. NO es una optimización matemática: es una heurística transparente y explicable.",
      "Puede inducir a error si varios activos correlacionados caen en grupos distintos: la diversificación real puede ser menor que la aparente."],
    radar: ["Calidad de la decisión",
      "Mide seis dimensiones de la CALIDAD del proceso (robustez, evidencia, consenso, calibración, persistencia, diversificación), no la rentabilidad esperada.",
      "Un radar amplio significa 'la señal es fiable', no 'va a subir'.",
      "Limitación: agrega sobre la cartera propuesta; un activo excelente puede quedar diluido."],
    stress: ["Stress test",
      "Simula qué pasaría con la Oportunidad si la señal se degrada: menos evidencia, menos muestra efectiva, intervalos más anchos o cambio de régimen.",
      "Reaplica la MISMA fórmula de Oportunidad sobre métricas degradadas a mano; no recalcula el motor.",
      "Es un análisis de sensibilidad, no un pronóstico de que eso vaya a ocurrir."],
    timeline: ["Evolución de la decisión",
      "Para cada fecha pasada muestra el exceso ESPERADO en ese momento (as-of, usando sólo datos previos) y, cuando ya hay 3 meses por delante, el exceso OBSERVADO real.",
      "Permite auditar si el proceso habría acertado históricamente.",
      "Limitación: las decisiones de los últimos ~3 meses aún no tienen resultado observado."],
    opp: ["Oportunidad (0-100)",
      "Señal/ruido del exceso por horizonte, penalizada por muestra efectiva baja. Un exceso enorme con N≈2 NO puntúa alto.",
      "50 = neutral. Por encima, hay evidencia de exceso; por debajo, de defecto.",
      "Limitación: mide exceso sobre el mercado, no rentabilidad absoluta."],
    robustness: ["Robustez",
      "Independiente de la magnitud del exceso: mide si la señal es FIABLE (muestra, IC estrecho, signo estable, persistencia del régimen, calibración).",
      "Una señal puede ser muy robusta y a la vez ofrecer poco exceso.",
      "Limitación: no dice la dirección, sólo la fiabilidad."],
  };

  // ── estado ────────────────────────────────────────────────────────────
  const tickers = window.__TICKERS__ || [];
  const NAME = Object.fromEntries(tickers.map(([s, n]) => [s, n]));
  const full = {}, status = {};
  tickers.forEach(([s]) => (status[s] = "loading"));
  let loaded = false;
  document.getElementById("n-assets").textContent = tickers.length;
  document.getElementById("gen-ts").textContent = "generado " + new Date().toLocaleString("es-ES");

  const okSyms = () => tickers.map(([s]) => s).filter((s) => status[s] === "ok");
  const fmtP = (x, d = 1) => (x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

  // ── carga concurrente ─────────────────────────────────────────────────
  let idx = 0, done = 0; const CONC = 5;
  function next() {
    if (idx >= tickers.length) return;
    const [sym] = tickers[idx++];
    fetch(`/api/regime?view=light&symbol=${encodeURIComponent(sym)}`).then((r) => r.json()).then((d) => {
      if (d.error) { status[sym] = "err"; return; }
      full[sym] = d; status[sym] = "ok";
    }).catch(() => { status[sym] = "err"; }).finally(() => {
      done++; document.getElementById("prog").textContent = `${done}/${tickers.length} cargados`;
      renderAggregates();
      if (done === tickers.length && !loaded) { loaded = true; initInteractive(); }
      next();
    });
  }
  for (let k = 0; k < CONC; k++) next();

  // ── fórmula de Oportunidad replicada (para el stress test) ────────────
  function oppFrom(rows) {
    const c = [];
    for (const r of rows) {
      if (r.baseline == null || r.excess == null) continue;
      const ch = (r.ci_hi - r.ci_lo) / 2;
      const snr = ch > 1e-9 ? r.excess / ch : 0;
      c.push(Math.tanh(0.6 * snr) * Math.sqrt(Math.min(1, r.n_eff / 30)));
    }
    if (!c.length) return null;
    return Math.round(clamp(50 + 50 * (c.reduce((a, b) => a + b, 0) / c.length), 0, 100));
  }
  const gradeFrom = (o) => (o >= 82 ? "A+" : o >= 70 ? "A" : o >= 60 ? "B" : o >= 50 ? "C" : "D");
  function recommend(opp, ev) {
    if (ev === "neg") return { t: "Evitar", c: NEG };
    if (opp >= 70 && ev === "pos") return { t: "Sobreponderar", c: POS };
    if (opp >= 60 && ev === "pos") return { t: "Incluir", c: POS };
    if (opp >= 50) return { t: "Vigilar", c: FLAT };
    return { t: "Evitar", c: NEG };
  }
  function consensusPct(d) {
    const evs = Object.values(d.scenarios).map((s) => s.evidence).filter(Boolean);
    if (!evs.length) return 50;
    const pos = evs.filter((e) => e === "pos").length, neg = evs.filter((e) => e === "neg").length;
    const dom = Math.max(pos, neg);
    if (dom === 0) return 40;                       // todos planos
    return clamp(40 + 60 * (dom / evs.length) - (Math.min(pos, neg) > 0 ? 20 : 0), 0, 100);
  }

  // ── P4-1 · asignación de capital ──────────────────────────────────────
  const MAX_SINGLE = 18, GROUP_CAP = 35, MIN_CASH = 5;
  function buildAllocation() {
    const items = [];
    okSyms().forEach((sym) => {
      const d = full[sym], s = d.summary, sc = d.scenarios["6m"] || {};
      if (s.opportunity == null || s.robustness == null) return;
      const ev = sc.evidence;
      const evMult = ev === "pos" ? 1 : ev === "flat" ? 0.35 : 0;
      const edge = Math.max(0, (s.opportunity - 50) / 50);
      const conv = edge * Math.sqrt(s.robustness / 100) * evMult;
      items.push({ sym, name: NAME[sym] || sym, group: GROUP[sym] || "Otros",
        opp: s.opportunity, grade: s.grade, rob: s.robustness, rob_level: s.rob_level,
        ev, conv, base: conv * MAX_SINGLE, capped: false });
    });
    const byG = {};
    items.forEach((it) => (byG[it.group] = byG[it.group] || []).push(it));
    Object.values(byG).forEach((g) => {
      const sum = g.reduce((a, it) => a + it.base, 0);
      if (sum > GROUP_CAP) { const f = GROUP_CAP / sum; g.forEach((it) => { it.base *= f; it.capped = true; }); }
    });
    let total = items.reduce((a, it) => a + it.base, 0);
    if (total > 100 - MIN_CASH) { const f = (100 - MIN_CASH) / total; items.forEach((it) => (it.base *= f)); }
    items.forEach((it) => (it.pct = Math.round(it.base)));
    const investedR = items.reduce((a, it) => a + it.pct, 0);
    const cash = Math.max(0, 100 - investedR);
    items.sort((a, b) => b.pct - a.pct || b.conv - a.conv);
    return { rows: items, cash, invested: 100 - cash };
  }

  // ── P4-2 · salud de la cartera ────────────────────────────────────────
  // Esta función se usa ahora con DOS carteras: la que el comité propone (donde
  // todas las filas vienen del motor y están puntuadas) y la REAL del usuario,
  // que casi nunca lo está entera — un fondo indexado europeo o un ETC de oro no
  // están en el universo del régimen.
  //
  // La concentración (HHI, N efectivo, top1, top3) no necesita ninguna
  // puntuación: son pesos, y se calcula sobre TODO lo que se tiene. Las medias
  // de oportunidad, robustez y evidencia sí, y se calculan sobre el subconjunto
  // puntuado CON SU PROPIO DENOMINADOR, no sobre el total. Repartir entre el
  // total lo que sólo cubre a la mitad daría una robustez media hundida por
  // posiciones que nadie ha medido, que es peor que no dar el número: parece
  // medido. `covered` dice qué parte del capital respalda esas medias.
  function buildHealth(alloc) {
    const held = alloc.rows.filter((r) => r.pct > 0), inv = alloc.invested;
    if (!held.length || inv <= 0)
      return { rating: "Defensiva", score: 0, effN: 0, top1: 0, top3: 0, groups: 0,
        pctGreen: 0, oppMean: 50, robMean: 0, confAgg: 0, cash: alloc.cash, inv: 0,
        covered: 0, empty: true };
    const w = held.map((r) => r.pct / inv);
    const hhi = w.reduce((a, x) => a + x * x, 0), effN = 1 / hhi;
    const top1 = Math.max(...held.map((r) => r.pct)) / inv;
    const top3 = [...held].sort((a, b) => b.pct - a.pct).slice(0, 3).reduce((a, r) => a + r.pct, 0) / inv;
    const groups = new Set(held.map((r) => r.group)).size;

    const scored = held.filter((r) => r.opp != null && r.rob != null);
    const sInv = scored.reduce((a, r) => a + r.pct, 0);
    const covered = sInv / inv;
    const cm = { Alta: 2, Media: 1, Baja: 0 };
    const wmean = (f) => (sInv > 1e-9 ? scored.reduce((a, r) => a + r.pct * f(r), 0) / sInv : null);
    const pctGreen = sInv > 1e-9
      ? scored.filter((r) => r.ev === "pos").reduce((a, r) => a + r.pct, 0) / sInv : 0;
    const oppMean = wmean((r) => r.opp) ?? 50;
    const robMean = wmean((r) => r.rob) ?? 0;
    const confAgg = wmean((r) => cm[(full[r.sym] || {}).summary?.confidence] ?? 1) ?? 1;

    const divScore = Math.min(1, effN / 5);
    const score = 100 * (0.30 * pctGreen + 0.25 * robMean / 100 + 0.20 * divScore
      + 0.15 * Math.max(0, (oppMean - 50) / 50) + 0.10 * confAgg / 2);
    const rating = score >= 75 ? "Excelente" : score >= 60 ? "Buena" : score >= 45 ? "Aceptable" : "Débil";
    return { rating, score: Math.round(score), effN, top1, top3, groups, pctGreen, oppMean,
      robMean, confAgg, hhi, cash: alloc.cash, inv, covered };
  }

  // ── P4-4 · radar de calidad de decisión ───────────────────────────────
  function buildRadar(alloc) {
    const held = alloc.rows.filter((r) => r.pct > 0), inv = alloc.invested;
    const useW = held.length && inv > 0;
    const pool = useW ? held : okSyms().map((s) => ({ sym: s, pct: 1 }));
    const W = useW ? inv : pool.length;
    const wsum = (fn) => pool.reduce((a, r) => a + (useW ? r.pct : 1) * fn(r), 0) / W;
    const rob = wsum((r) => full[r.sym].summary.robustness || 0);
    const green = 100 * wsum((r) => ((full[r.sym].scenarios["6m"] || {}).evidence === "pos" ? 1 : 0));
    const cons = wsum((r) => consensusPct(full[r.sym]));
    const cp = pool.filter((r) => full[r.sym].calibration);
    const calib = cp.length
      ? cp.reduce((a, r) => a + (useW ? r.pct : 1) * full[r.sym].calibration.coverage, 0)
        / (useW ? cp.reduce((a, r) => a + r.pct, 0) : cp.length)
      : 50;
    const pers = wsum((r) => { const dm = full[r.sym].transition.dwell_mean; return dm ? Math.min(100, dm / 40 * 100) : 40; });
    const div = useW ? Math.min(1, 1 / held.map((r) => r.pct / inv).reduce((a, x) => a + x * x, 0) / 5) * 100 : 50;
    return [
      { k: "Robustez", v: rob }, { k: "Evidencia", v: green }, { k: "Consenso", v: cons },
      { k: "Calibración", v: calib }, { k: "Persistencia", v: pers }, { k: "Diversificación", v: div },
    ];
  }

  function expectedByHorizon(alloc) {
    const held = alloc.rows.filter((r) => r.pct > 0), out = {};
    ["3m", "6m", "12m"].forEach((h) => {
      let acc = 0, ws = 0;
      held.forEach((r) => { const sc = full[r.sym].scenarios[h]; if (sc && sc.excess != null) { acc += r.pct * sc.excess; ws += r.pct; } });
      out[h] = ws > 0 ? acc / ws : null;
    });
    return out;
  }

  // ── render agregado (progresivo) ──────────────────────────────────────
  let LAST = null;
  function renderAggregates() {
    if (!okSyms().length) return;
    const alloc = buildAllocation(), health = buildHealth(alloc), radar = buildRadar(alloc), exp = expectedByHorizon(alloc);
    LAST = { alloc, health, radar, exp };
    renderExec(alloc, health, exp);
    renderAlloc(alloc);
    renderHealth(health);
    drawRadar(radar);
    renderAudit();
  }

  // ── P4-3 · resumen ejecutivo ──────────────────────────────────────────
  function renderExec(alloc, h, exp) {
    const held = alloc.rows.filter((r) => r.pct > 0);
    const top = held.slice(0, 4);
    const greenN = okSyms().filter((s) => (full[s].scenarios["6m"] || {}).evidence === "pos").length;
    const negN = okSyms().filter((s) => (full[s].scenarios["6m"] || {}).evidence === "neg").length;
    const risks = buildRisks(alloc, h);
    const inval = buildInvalidation(alloc);
    const recoLine = held.length
      ? top.map((r) => `<b>${r.sym}</b> ${r.pct}%`).join(" · ") + ` · <span class="mut">efectivo ${alloc.cash}%</span>`
      : `<span class="mut">Sin convicción direccional suficiente — ${alloc.cash}% en efectivo.</span>`;
    const expLine = ["3m", "6m", "12m"].map((k) => `${k}: <b style="color:${(exp[k] || 0) >= 0 ? POS : NEG}">${fmtP(exp[k])}</b>`).join(" · ");
    document.getElementById("exec").innerHTML = `
      <div class="exec-grid">
        <div class="ex-block"><div class="ex-h">Qué recomendamos</div><div>${recoLine}</div></div>
        <div class="ex-block"><div class="ex-h">Por qué</div><div>Oportunidad media ponderada
          <b>${Math.round(h.oppMean)}</b>/100, robustez media <b>${Math.round(h.robMean)}</b>,
          ${Math.round(h.pctGreen * 100)}% del capital invertido en evidencia positiva.</div></div>
        <div class="ex-block"><div class="ex-h">Qué evidencia existe</div><div>
          <span style="color:${POS}">${greenN} activos</span> con evidencia positiva a 6m,
          <span style="color:${NEG}">${negN}</span> negativa; el resto indistinguible de 0.
          Calibración media de las posiciones: <b>${radarCoverageLabel(alloc)}</b>.</div></div>
        <div class="ex-block"><div class="ex-h">Qué riesgos vemos</div><ul class="ex-ul">${risks.map((x) => `<li>${x}</li>`).join("") || "<li class='mut'>Sin riesgos estructurales destacables.</li>"}</ul></div>
        <div class="ex-block"><div class="ex-h">Qué invalidaría la tesis</div><ul class="ex-ul">${inval.map((x) => `<li>${x}</li>`).join("")}</ul></div>
        <div class="ex-block"><div class="ex-h">Qué esperamos por horizonte</div><div>${expLine}
          <div class="mut small">Exceso mediano esperado sobre el mercado (ponderado por capital asignado).</div></div></div>
      </div>`;
  }
  function radarCoverageLabel(alloc) {
    const held = alloc.rows.filter((r) => r.pct > 0).filter((r) => full[r.sym].calibration);
    if (!held.length) return "—";
    const inv = held.reduce((a, r) => a + r.pct, 0);
    const c = held.reduce((a, r) => a + r.pct * full[r.sym].calibration.coverage, 0) / inv;
    return Math.round(c) + "% cobertura IC95";
  }
  function buildRisks(alloc, h) {
    const held = alloc.rows.filter((r) => r.pct > 0), out = [];
    if (h.top1 > 0.30) out.push(`Concentración: la mayor posición pesa ${Math.round(h.top1 * 100)}% del capital invertido.`);
    if (h.effN && h.effN < 2.5) out.push(`Baja diversificación efectiva (${h.effN.toFixed(1)} posiciones equivalentes).`);
    const weakRob = held.filter((r) => r.rob < 50);
    if (weakRob.length) out.push(`Robustez baja en ${weakRob.map((r) => r.sym).join(", ")} — señal poco fiable pese al peso asignado.`);
    const lowN = held.filter((r) => { const sc = full[r.sym].scenarios["6m"] || {}; return sc.n_eff != null && sc.n_eff < 3; });
    if (lowN.length) out.push(`Muestra efectiva &lt;3 en ${lowN.map((r) => r.sym).join(", ")} — el exceso puede ser espejismo de muestra.`);
    const over = held.filter((r) => { const d = full[r.sym]; const dm = d.transition.dwell_mean; return dm && d.summary.dwell > 1.6 * dm; });
    if (over.length) out.push(`${over.map((r) => r.sym).join(", ")} llevan mucho más tiempo que su permanencia media en el régimen — mayor riesgo de giro.`);
    return out;
  }
  function buildInvalidation(alloc) {
    const held = alloc.rows.filter((r) => r.pct > 0).slice(0, 3), out = [];
    if (held.length) out.push(`Si la evidencia a 6m de ${held.map((r) => r.sym).join(", ")} pasa a plana o negativa.`);
    const regs = [...new Set(held.map((r) => full[r.sym].summary.regime))];
    if (regs.length) out.push(`Si el régimen dominante (${regs.join(", ")}) cambia — vigilar la permanencia frente a la media histórica.`);
    out.push("Si la muestra efectiva de las posiciones núcleo cae por debajo de 3 o el IC95 se ensancha materialmente.");
    return out;
  }

  // ── P4-1 render ───────────────────────────────────────────────────────
  function renderAlloc(alloc) {
    const held = alloc.rows.filter((r) => r.pct > 0);
    const bar = held.map((r) => `<div class="alloc-seg" style="flex:${r.pct};background:${GC[r.grade] || "#888"}" title="${r.sym} ${r.pct}%"></div>`).join("")
      + `<div class="alloc-seg cash" style="flex:${alloc.cash}" title="Efectivo ${alloc.cash}%"></div>`;
    const rows = held.map((r) => {
      const E = EVID[r.ev] || { c: "var(--muted)", t: "—" };
      const why = r.capped ? " · <span class='mut'>limitado por tope de grupo</span>" : "";
      return `<tr>
        <td><span class="sym">${r.sym}</span> <span class="nm">${r.name}</span></td>
        <td class="num"><b class="pct">${r.pct}%</b></td>
        <td class="expl">Oportunidad <b style="color:${GC[r.grade]}">${r.grade}·${r.opp}</b>,
          robustez ${r.rob_level.toLowerCase()}, evidencia
          <span class="ev-dot" style="background:${E.c}"></span> ${E.t}. Convicción ${(r.conv).toFixed(2)}${why}.</td>
      </tr>`;
    }).join("");
    const cashRow = `<tr class="cash-row">
      <td><span class="sym">EFECTIVO</span></td><td class="num"><b class="pct">${alloc.cash}%</b></td>
      <td class="expl mut">Capital sin convicción direccional suficiente. Sólo ${held.length} de ${okSyms().length}
        activos superan el umbral (evidencia positiva y oportunidad por encima de neutral).</td></tr>`;
    document.getElementById("alloc").innerHTML =
      `<div class="alloc-bar">${bar}</div>
       <div class="scroll"><table class="alloc-tbl">
         <thead><tr><th>Activo</th><th class="num">Peso</th><th>Justificación</th></tr></thead>
         <tbody>${rows}${cashRow}</tbody></table></div>`;
  }

  // ── P4-2 render ───────────────────────────────────────────────────────
  function renderHealth(h) {
    const RC = { Excelente: POS, Buena: "#8bbf3f", Aceptable: FLAT, Débil: NEG, Defensiva: "var(--muted)" };
    document.getElementById("health-rating").innerHTML =
      `<b style="color:${RC[h.rating]};font-size:14px">${h.rating}</b> · ${h.score}/100`;
    const items = h.empty ? [
      ["Estado", "Cartera defensiva — todo en efectivo", ""],
      ["Efectivo", h.cash + "%", ""],
    ] : [
      ["Concentración (top-1)", Math.round(h.top1 * 100) + "%", h.top1 > 0.3 ? "warn" : ""],
      ["Top-3", Math.round(h.top3 * 100) + "%", ""],
      ["Diversificación efectiva", h.effN.toFixed(1) + " posiciones", h.effN < 2.5 ? "warn" : "ok"],
      ["Grupos representados", String(h.groups), ""],
      ["Capital en evidencia verde", Math.round(h.pctGreen * 100) + "%", h.pctGreen >= 0.6 ? "ok" : h.pctGreen < 0.3 ? "warn" : ""],
      ["Oportunidad media", Math.round(h.oppMean) + "/100", ""],
      ["Robustez media", Math.round(h.robMean) + "/100", h.robMean >= 65 ? "ok" : h.robMean < 50 ? "warn" : ""],
      ["Confianza agregada", h.confAgg >= 1.5 ? "Alta" : h.confAgg >= 0.8 ? "Media" : "Baja", ""],
      ["Efectivo", h.cash + "%", ""],
    ];
    document.getElementById("health").innerHTML = items.map(([k, v, c]) =>
      `<div class="hz-row"><span>${k}</span><b class="${c}">${v}</b></div>`).join("");
  }

  // ── P4-4 radar canvas ─────────────────────────────────────────────────
  function drawRadar(axes) {
    const cv = document.getElementById("radar"); if (!cv) return;
    const rect = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = rect.width * dpr; cv.height = rect.height * dpr;
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width, H = rect.height, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 34;
    const N = axes.length, txt = css("--text"), mut = css("--muted"), bd = css("--border"), acc = css("--accent");
    ctx.clearRect(0, 0, W, H);
    const ang = (i) => -Math.PI / 2 + i * 2 * Math.PI / N;
    // rings
    ctx.strokeStyle = bd; ctx.fillStyle = mut; ctx.font = "10px ui-monospace,monospace";
    for (let g = 1; g <= 4; g++) {
      const rr = R * g / 4; ctx.beginPath();
      for (let i = 0; i <= N; i++) { const a = ang(i % N); const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.globalAlpha = 0.5; ctx.stroke(); ctx.globalAlpha = 1;
    }
    // spokes + labels
    axes.forEach((ax, i) => {
      const a = ang(i), x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
      ctx.strokeStyle = bd; ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke(); ctx.globalAlpha = 1;
      const lx = cx + (R + 20) * Math.cos(a), ly = cy + (R + 20) * Math.sin(a);
      ctx.fillStyle = mut; ctx.textAlign = Math.abs(Math.cos(a)) < 0.3 ? "center" : (Math.cos(a) > 0 ? "left" : "right");
      ctx.textBaseline = "middle"; ctx.fillText(ax.k, lx, ly);
    });
    // polygon
    ctx.beginPath();
    axes.forEach((ax, i) => { const a = ang(i), rr = R * clamp(ax.v, 0, 100) / 100; const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.closePath();
    ctx.fillStyle = acc; ctx.globalAlpha = 0.18; ctx.fill(); ctx.globalAlpha = 1;
    ctx.strokeStyle = acc; ctx.lineWidth = 2; ctx.stroke();
    axes.forEach((ax, i) => { const a = ang(i), rr = R * clamp(ax.v, 0, 100) / 100; const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a); ctx.beginPath(); ctx.arc(x, y, 3, 0, 2 * Math.PI); ctx.fillStyle = acc; ctx.fill(); });
    document.getElementById("radar-legend").innerHTML =
      axes.map((a) => `<span><i>${a.k}</i> ${Math.round(a.v)}</span>`).join("");
  }

  // ── P4-8 auditoría ────────────────────────────────────────────────────
  function renderAudit() {
    const head = `<thead><tr><th>Activo</th><th>Datos hasta</th><th class="num">Hist.</th>
      <th>Horiz.</th><th class="num">Baseline 6m</th><th class="num">N bruto 12m</th>
      <th class="num">N ef. 6m</th><th class="num">IC95 6m</th><th class="num">Cobertura</th></tr></thead>`;
    const body = okSyms().map((s) => {
      const d = full[s], sc = d.scenarios["6m"] || {}, a = d.audit;
      const ic = (sc.ci_lo != null && sc.baseline != null) ? `±${(((sc.ci_hi - sc.ci_lo) / 2) * 100).toFixed(1)}%` : "—";
      return `<tr><td><span class="sym">${esc(s)}</span></td><td>${esc(d.as_of)}</td><td class="num">${a.history_len}</td>
        <td>3/6/12m</td><td class="num">${fmtP(sc.baseline)}</td><td class="num">${a.n_raw_12m ?? "—"}</td>
        <td class="num">${sc.n_eff ?? "—"}</td><td class="num">${ic}</td>
        <td class="num">${a.coverage != null ? a.coverage + "%" : "—"}</td></tr>`;
    }).join("");
    document.getElementById("audit-tbl").innerHTML = head + `<tbody>${body}</tbody>`;
    document.getElementById("audit-lims").innerHTML =
      `<div class="lbl">Versión del modelo</div><div class="mut small">${MODEL_VERSION}</div>
       <div class="lbl" style="margin-top:10px">Limitaciones estadísticas vigentes</div>
       <ul>
         <li>Ventanas forward solapadas → la muestra efectiva (N ef.) es mucho menor que la bruta; la Oportunidad ya la penaliza.</li>
         <li>Los intervalos vienen de bootstrap por bloques; suponen que el pasado del régimen informa el futuro.</li>
         <li>Analogías condicionadas por régimen: un régimen con pocas repeticiones históricas da IC anchos.</li>
         <li>El exceso es sobre el propio mercado (beta descontada), no rentabilidad absoluta.</li>
         <li>El resultado observado de las decisiones recientes (&lt;3m) aún no existe.</li>
       </ul>
       <div class="mut small">Generado ${new Date().toLocaleString("es-ES")}.</div>`;
  }

  // ── interactivos (una vez cargado todo) ───────────────────────────────
  function initInteractive() {
    loadMine();                       // la cartera real, ya con la propuesta lista
    const held = LAST.alloc.rows.filter((r) => r.pct > 0);
    const universe = held.length ? held.map((r) => r.sym) : okSyms();
    const def = universe[0];
    // stress select
    const stressSel = `<select id="st-sym" class="mini-sel">${universe.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("")}</select>`;
    document.getElementById("stress").innerHTML = `
      <div class="st-ctrls">
        <label>Activo ${stressSel}</label>
        <label>Evidencia (× exceso) <input id="st-ev" type="range" min="0" max="100" value="100"><span id="st-ev-v">100%</span></label>
        <label>N efectivo (×) <input id="st-n" type="range" min="20" max="100" value="100"><span id="st-n-v">100%</span></label>
        <label>Amplitud IC (×) <input id="st-ic" type="range" min="100" max="250" value="100"><span id="st-ic-v">100%</span></label>
        <label class="chk"><input id="st-reg" type="checkbox"> Simular cambio de régimen</label>
      </div>
      <div id="st-out" class="st-out"></div>`;
    ["st-sym", "st-ev", "st-n", "st-ic", "st-reg"].forEach((id) =>
      document.getElementById(id).addEventListener("input", runStress));
    runStress();
    // timeline
    const tlSel = document.getElementById("tl-sym");
    tlSel.innerHTML = universe.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    tlSel.addEventListener("change", () => drawTimeline(tlSel.value));
    drawTimeline(def);
    window.addEventListener("resize", () => { if (LAST) drawRadar(LAST.radar); drawTimeline(tlSel.value); });
  }

  // ══ Mi cartera REAL ═══════════════════════════════════════════════════
  // Toda la maquinaria de salud de cartera —HHI, N efectivo, concentración,
  // capital en evidencia positiva— ya estaba escrita, y corría sobre una
  // asignación HIPOTÉTICA. La pregunta que nadie podía hacerle era la única que
  // importa cuando hay dinero dentro: ¿y lo que YO tengo, qué nota saca?
  const MINE = { rows: [], loaded: false, err: null, total: 0, unvalued: 0 };

  async function loadMine() {
    let d;
    try { d = await (await fetch("/api/cartera")).json(); }
    catch (e) { MINE.err = "no pude leer la cartera"; return renderMine(); }
    if (d.error) { MINE.err = d.error; return renderMine(); }
    const open = (d.positions || []).filter((r) => r.qty > 1e-9);
    const valued = open.filter((r) => r.market_value != null);
    MINE.unvalued = open.length - valued.length;
    MINE.total = valued.reduce((a, r) => a + r.market_value, 0);
    if (!valued.length) { MINE.loaded = true; return renderMine(); }
    MINE.rows = valued.map((r) => ({
      sym: r.ticker, name: r.name || r.ticker, kind: r.kind || "",
      // El grupo sólo se afirma cuando se sabe. Meter en "Otros" un fondo que
      // es renta variable global inflaría la diversificación aparente con una
      // etiqueta inventada, y la diversificación es justo lo que se mide aquí.
      group: GROUP[r.ticker] || null,
      pct: r.market_value / MINE.total * 100, eur: r.market_value,
      opp: null, rob: null, ev: null,
    })).sort((a, b) => b.pct - a.pct);
    renderMine();                                  // pesos ya, puntuación después

    // El motor de régimen, para MIS símbolos. Muchos no estarán cubiertos (un
    // fondo sin histórico largo, un ETC): eso no es un fallo, es el alcance
    // real del análisis, y se dice en vez de rellenarlo.
    let i = 0;
    const worker = async () => {
      while (i < MINE.rows.length) {
        const row = MINE.rows[i++];
        if (full[row.sym]) { applyScore(row, full[row.sym]); continue; }
        try {
          const p = await (await fetch(`/api/regime?view=light&symbol=${encodeURIComponent(row.sym)}`)).json();
          if (!p.error) { full[row.sym] = p; applyScore(row, p); }
        } catch (e) { /* sin cobertura: la fila se queda sin puntuar */ }
        renderMine();
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    MINE.loaded = true;
    renderMine();
  }

  function applyScore(row, p) {
    const s = p.summary || {}, sc = (p.scenarios || {})["6m"] || {};
    if (s.opportunity == null || s.robustness == null) return;
    row.opp = s.opportunity; row.rob = s.robustness; row.ev = sc.evidence || null;
  }

  const recoTag = (r) => {
    const k = recommend(r.opp, r.ev);
    return `<span style="color:${k.c}">${k.t}</span>`;
  };

  function renderMine() {
    const box = document.getElementById("mine"), leg = document.getElementById("mine-legend");
    if (MINE.err) {
      leg.textContent = "";
      box.innerHTML = `<div class="mut">No hay cartera que medir (${esc(MINE.err)}).</div>`;
      return;
    }
    if (!MINE.rows.length) {
      leg.textContent = "";
      box.innerHTML = `<div class="mut">Sin posiciones abiertas. Añádelas en <a href="/cartera">Cartera</a> y aquí sale su nota.</div>`;
      return;
    }
    const alloc = { rows: MINE.rows, cash: 0, invested: 100 };
    const h = buildHealth(alloc);
    const prop = LAST ? LAST.alloc.rows.filter((r) => r.pct > 0) : [];
    const mineSyms = new Set(MINE.rows.map((r) => r.sym));
    // Las dos listas que convierten esto en una decisión y no en un boletín.
    const missing = prop.filter((r) => !mineSyms.has(r.sym)).slice(0, 5);
    const avoidable = MINE.rows.filter((r) => r.ev === "neg");

    leg.innerHTML = MINE.loaded
      ? `${MINE.rows.length} posiciones · análisis sobre el <b>${Math.round(h.covered * 100)}%</b> del capital`
      : `${MINE.rows.length} posiciones · puntuando…`;

    const bar = MINE.rows.map((r) => {
      const c = r.ev ? EVID[r.ev].c : "#7c828e";
      return `<div class="alloc-seg" style="flex:${r.pct.toFixed(2)};background:${c}"
        title="${esc(r.sym)} ${r.pct.toFixed(1)}%"></div>`;
    }).join("");

    const rows = MINE.rows.map((r) => {
      const cov = r.opp != null;
      return `<tr${cov ? "" : ' class="uncovered"'}>
        <td><span class="sym">${esc(r.sym)}</span> <span class="mut">${esc(r.name)}</span></td>
        <td class="num"><b>${r.pct.toFixed(1)}%</b></td>
        <td class="num">${cov ? r.opp : "—"}</td>
        <td class="num">${cov ? Math.round(r.rob) : "—"}</td>
        <td>${cov
          ? (r.ev ? `<span style="color:${EVID[r.ev].c}">${EVID[r.ev].t}</span>` : "<span class='mut'>—</span>")
          : `<span class="mut" title="El motor de régimen no cubre este instrumento: no tiene histórico suficiente o no cotiza de forma que pueda puntuarse.">fuera del análisis</span>`}</td>
        <td>${cov ? recoTag(r) : ""}</td></tr>`;
    }).join("");

    box.innerHTML = `
      <div class="mine-top">
        <div class="mine-kpi"><div class="ex-h">Nota de la cartera</div>
          <div class="mine-big">${h.rating} <span class="mut">${h.score}/100</span></div>
          <div class="mut small">misma fórmula que la asignación sugerida</div></div>
        <div class="mine-kpi"><div class="ex-h">Concentración</div>
          <div class="mine-big">${h.effN.toFixed(1)} <span class="mut">activos efectivos</span></div>
          <div class="mut small">mayor posición ${Math.round(h.top1 * 100)}% · top 3 ${Math.round(h.top3 * 100)}%</div></div>
        <div class="mine-kpi"><div class="ex-h">Cobertura del análisis</div>
          <div class="mine-big">${Math.round(h.covered * 100)}%</div>
          <div class="mut small">del capital tiene régimen medible</div></div>
      </div>
      <div class="alloc-bar mine-bar">${bar}</div>
      <div class="scroll"><table class="alloc-tbl mine-tbl">
        <thead><tr><th>Posición</th><th class="num">Peso</th><th class="num">Oport.</th>
          <th class="num">Robustez</th><th>Evidencia 6m</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${h.covered < 0.999 ? `<p class="mut small">⚠ Las medias de oportunidad, robustez y evidencia
        se calculan SÓLO sobre el ${Math.round(h.covered * 100)}% del capital que el motor puede puntuar.
        El resto no está medido — que no es lo mismo que estar bien.</p>` : ""}
      ${MINE.unvalued ? `<p class="mut small">⚠ ${MINE.unvalued} posición(es) sin valorar quedan fuera de estos pesos.</p>` : ""}
      ${avoidable.length ? `<p class="mine-flag">Con evidencia NEGATIVA a 6 meses:
        ${avoidable.map((r) => `<b>${esc(r.sym)}</b> (${r.pct.toFixed(1)}%)`).join(" · ")}.</p>` : ""}
      ${missing.length ? `<p class="mut small">El comité sugeriría además:
        ${missing.map((r) => `<b>${esc(r.sym)}</b> ${r.pct}%`).join(" · ")}.
        Sale de la misma heurística de arriba: es una sugerencia, no una orden.</p>` : ""}`;
  }

  // ── P4-6 stress ───────────────────────────────────────────────────────
  function runStress() {
    const sym = document.getElementById("st-sym").value, d = full[sym]; if (!d) return;
    const evM = +document.getElementById("st-ev").value / 100;
    const nM = +document.getElementById("st-n").value / 100;
    const icM = +document.getElementById("st-ic").value / 100;
    const reg = document.getElementById("st-reg").checked;
    document.getElementById("st-ev-v").textContent = Math.round(evM * 100) + "%";
    document.getElementById("st-n-v").textContent = Math.round(nM * 100) + "%";
    document.getElementById("st-ic-v").textContent = Math.round(icM * 100) + "%";
    const baseRows = Object.values(d.scenarios).filter((r) => r.baseline != null && r.excess != null);
    const base = { opp: d.summary.opportunity, grade: d.summary.grade, ev: (d.scenarios["6m"] || {}).evidence };
    // degradar
    const degRows = baseRows.map((r) => {
      const half = (r.ci_hi - r.ci_lo) / 2 * icM;
      const exc = (reg ? 0 : r.excess) * evM;
      return { baseline: r.baseline, excess: exc, n_eff: r.n_eff * nM * (reg ? 0.5 : 1),
        ci_lo: r.baseline + exc - half, ci_hi: r.baseline + exc + half };
    });
    const opp2 = oppFrom(degRows) ?? 50, g2 = gradeFrom(opp2);
    // evidencia degradada del horizonte 6m
    const s6 = degRows.find((_, i) => Object.keys(d.scenarios)[i] === "6m") || degRows[0] || {};
    const ev2 = s6.ci_lo > s6.baseline ? "pos" : s6.ci_hi < s6.baseline ? "neg" : "flat";
    const r1 = recommend(base.opp, base.ev), r2 = recommend(opp2, reg ? "flat" : ev2);
    const arrow = opp2 < base.opp ? "▼" : opp2 > base.opp ? "▲" : "=";
    document.getElementById("st-out").innerHTML = `
      <div class="st-cmp">
        <div class="st-col"><div class="st-lbl">Ahora</div>
          <div class="st-opp" style="color:${GC[base.grade]}">${base.grade}·${base.opp}</div>
          <div class="st-reco" style="color:${r1.c}">${r1.t}</div></div>
        <div class="st-arrow ${opp2 < base.opp ? "down" : ""}">${arrow}</div>
        <div class="st-col"><div class="st-lbl">Degradado</div>
          <div class="st-opp" style="color:${GC[g2]}">${g2}·${opp2}</div>
          <div class="st-reco" style="color:${r2.c}">${r2.t}</div></div>
      </div>
      <p class="mut small">${reg ? "Con cambio de régimen el exceso se lleva a 0 y la muestra a la mitad. " : ""}
        La recomendación ${r1.t === r2.t ? "<b>se mantiene</b>" : `pasaría de <b>${r1.t}</b> a <b style="color:${r2.c}">${r2.t}</b>`}
        bajo este escenario. Simulación sobre métricas derivadas; el motor no se recalcula.</p>`;
  }

  // ── P4-5 timeline de decisiones ───────────────────────────────────────
  function drawTimeline(sym) {
    const d = full[sym]; if (!d) return;
    const cv = document.getElementById("timeline"), rect = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = rect.width * dpr; cv.height = rect.height * dpr;
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width, H = rect.height, pad = { l: 46, r: 14, t: 14, b: 22 };
    const th = d.thesis || [];
    ctx.clearRect(0, 0, W, H);
    if (th.length < 2) { document.getElementById("tl-note").textContent = "Histórico insuficiente."; return; }
    const txt = css("--text"), mut = css("--muted"), bd = css("--border"), acc = css("--accent");
    const t0 = th[0].t, t1 = th[th.length - 1].t;
    const vals = th.flatMap((p) => [p.excess, p.obs].filter((x) => x != null));
    const mx = Math.max(0.02, ...vals.map(Math.abs));
    const X = (t) => pad.l + (t - t0) / (t1 - t0) * (W - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (v + mx) / (2 * mx)) * (H - pad.t - pad.b);
    // bandas de régimen
    for (let i = 0; i < th.length; i++) {
      const x0 = X(th[i].t), x1 = i < th.length - 1 ? X(th[i + 1].t) : x0 + 2;
      ctx.fillStyle = REGIME_COLORS[th[i].regime] || "#888"; ctx.globalAlpha = 0.10;
      ctx.fillRect(x0, pad.t, x1 - x0, H - pad.t - pad.b); ctx.globalAlpha = 1;
    }
    // eje 0
    ctx.strokeStyle = bd; ctx.beginPath(); ctx.moveTo(pad.l, Y(0)); ctx.lineTo(W - pad.r, Y(0)); ctx.stroke();
    ctx.fillStyle = mut; ctx.font = "10px ui-monospace,monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText("0", pad.l - 6, Y(0)); ctx.fillText("+" + (mx * 100).toFixed(0) + "%", pad.l - 6, Y(mx));
    ctx.fillText("-" + (mx * 100).toFixed(0) + "%", pad.l - 6, Y(-mx));
    // línea esperado (as-of)
    ctx.strokeStyle = acc; ctx.lineWidth = 1.6; ctx.beginPath();
    th.forEach((p, i) => { const x = X(p.t), y = Y(p.excess); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
    // observado (donde existe)
    const obs = th.filter((p) => p.obs != null);
    if (obs.length) {
      ctx.beginPath();
      obs.forEach((p, i) => { const x = X(p.t), y = Y(p.obs); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.strokeStyle = mut; ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);
      obs.forEach((p) => { ctx.beginPath(); ctx.arc(X(p.t), Y(p.obs), 2.2, 0, 2 * Math.PI); ctx.fillStyle = p.obs >= 0 ? POS : NEG; ctx.fill(); });
    }
    // puntos de decisión (evidencia as-of)
    th.forEach((p) => { const E = EVID[p.ev] || { c: mut }; ctx.beginPath(); ctx.arc(X(p.t), Y(p.excess), 2.4, 0, 2 * Math.PI); ctx.fillStyle = E.c; ctx.fill(); });
    // fechas
    ctx.fillStyle = mut; ctx.textAlign = "left"; ctx.fillText(new Date(t0).toLocaleDateString("es-ES", { year: "2-digit", month: "short" }), pad.l, H - 8);
    ctx.textAlign = "right"; ctx.fillText(new Date(t1).toLocaleDateString("es-ES", { year: "2-digit", month: "short" }), W - pad.r, H - 8);
    document.getElementById("tl-note").innerHTML =
      `Línea <b style="color:${acc}">azul</b>: exceso esperado en cada momento (as-of, sólo datos previos). ` +
      `Línea gris punteada + puntos: exceso <b>observado</b> realizado a 3m (verde/rojo según signo). ` +
      `Fondo: régimen vigente. Las decisiones de los últimos ~3 meses aún no tienen resultado observado.`;
  }

  // ── P4-9 exportación ──────────────────────────────────────────────────
  function download(name, text, type) {
    const b = new Blob([text], { type }); const u = URL.createObjectURL(b);
    const a = document.createElement("a"); a.href = u; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(u), 1000);
  }
  function exportCSV() {
    const alloc = LAST.alloc, pctBy = Object.fromEntries(alloc.rows.map((r) => [r.sym, r.pct]));
    const head = ["symbol", "name", "regime", "opportunity", "grade", "robustness", "rob_level", "evidence_6m", "excess_6m", "n_eff_6m", "confidence", "allocation_pct"];
    const lines = okSyms().map((s) => {
      const d = full[s], su = d.summary, sc = d.scenarios["6m"] || {};
      return [s, `"${(NAME[s] || "").replace(/"/g, "'")}"`, su.regime, su.opportunity, su.grade, su.robustness, su.rob_level,
        sc.evidence, sc.excess, sc.n_eff, su.confidence, pctBy[s] || 0].join(",");
    });
    lines.push(["CASH", "Efectivo", "", "", "", "", "", "", "", "", "", alloc.cash].join(","));
    download("comite_cartera.csv", head.join(",") + "\n" + lines.join("\n"), "text/csv");
  }
  function exportJSON() {
    const trim = {};
    okSyms().forEach((s) => { const d = full[s]; trim[s] = { symbol: s, name: NAME[s], as_of: d.as_of,
      summary: d.summary, scenarios: d.scenarios, calibration: d.calibration, transition: d.transition, audit: d.audit }; });
    const out = { generated: new Date().toISOString(), model: MODEL_VERSION,
      portfolio: { allocation: LAST.alloc, health: LAST.health, radar: LAST.radar, expected: LAST.exp }, assets: trim };
    download("comite_cartera.json", JSON.stringify(out, null, 2), "application/json");
  }
  function exportAudit() {
    const head = ["symbol", "as_of", "history_len", "baseline_6m", "n_raw_12m", "n_eff_6m", "ci95_half_6m", "coverage", "model_version"];
    const lines = okSyms().map((s) => { const d = full[s], sc = d.scenarios["6m"] || {}, a = d.audit;
      const half = (sc.ci_lo != null) ? ((sc.ci_hi - sc.ci_lo) / 2).toFixed(4) : "";
      return [s, d.as_of, a.history_len, sc.baseline, a.n_raw_12m, sc.n_eff, half, a.coverage, `"${MODEL_VERSION}"`].join(","); });
    download("comite_auditoria.csv", head.join(",") + "\n" + lines.join("\n"), "text/csv");
  }
  function printMode(m) { document.body.dataset.print = m; window.print(); setTimeout(() => delete document.body.dataset.print, 400); }
  document.querySelector(".export-bar").addEventListener("click", (e) => {
    const k = e.target.dataset.exp; if (!k || !LAST) return;
    if (k === "csv") exportCSV(); else if (k === "json") exportJSON(); else if (k === "audit") exportAudit();
    else printMode(k);
  });

  // ── P4-7 popovers de explicación ──────────────────────────────────────
  const pop = document.getElementById("why-pop");
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".why");
    if (!b) { pop.hidden = true; return; }
    e.stopPropagation();
    const w = WHY[b.dataset.why]; if (!w) return;
    pop.innerHTML = `<div class="wp-h">${w[0]}</div>` + w.slice(1).map((p) => `<p>${p}</p>`).join("") +
      `<div class="wp-x">cerrar</div>`;
    pop.hidden = false;
    const r = b.getBoundingClientRect();
    pop.style.top = (window.scrollY + r.bottom + 8) + "px";
    pop.style.left = Math.max(12, Math.min(window.innerWidth - 340, r.left)) + "px";
  });
})();
