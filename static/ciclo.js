// Ciclo económico: reloj de la OCDE, fase, pilares, historial y titulares.
//
// Todo lo que llega del servidor se escribe con textContent y nunca se
// interpreta como HTML: los titulares son texto de terceros.
(() => {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";
  const $ = (id) => document.getElementById(id);
  const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const WHY = {
    auge: "La economía está por encima de su tendencia y sigue acelerando.",
    recesion: "La economía pierde impulso: crece cada vez menos, o ya va por debajo de su tendencia pero sin estrés grave.",
    crisis: "Por debajo de la tendencia, cayendo y con estrés en el crédito, los mercados o el empleo.",
    recuperacion: "Todavía por debajo de la tendencia, pero el impulso ya es positivo.",
  };
  const color = (k) => `var(--ph-${k})`;

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v;
      else if (k === "style") n.setAttribute("style", v);
      else n.setAttribute(k, v);
    }
    for (const c of kids) if (c != null) n.append(c instanceof Node ? c : String(c));
    return n;
  }
  function svg(tag, attrs, text) {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  }
  const num = (x, d = 2) => x == null ? "—" :
    x.toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });
  const signed = (x, d = 2) => x == null ? "—" : (x > 0 ? "+" : x < 0 ? "−" : "") + num(Math.abs(x), d);
  const pct = (x) => x == null ? "—" : Math.round(x * 100) + " %";
  const mes = (s) => s ? `${MESES[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}` : "—";
  const UNIT = { "%": " %", pp: " pp", pts: "", "": "" };

  function tipAt(box, tip, evt, rows) {
    tip.replaceChildren(el("div", { class: "td" }, rows[0]),
      ...rows.slice(1).map(([a, b]) => el("div", { class: "tr" }, el("span", {}, a), el("b", {}, b))));
    tip.hidden = false;
    const r = box.getBoundingClientRect();
    let x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 14;
    if (x + tip.offsetWidth > r.width) x = evt.clientX - r.left - tip.offsetWidth - 14;
    tip.style.left = Math.max(0, x) + "px";
    tip.style.top = y + "px";
  }

  function divBar(score, mini) {
    const b = el("div", { class: "div-bar" + (mini ? " mini" : "") });
    if (score != null) {
      const w = Math.min(1, Math.abs(score)) * 50;
      b.append(el("div", { class: "f " + (score >= 0 ? "p" : "n"),
        style: score >= 0 ? `left:50%;width:${w}%` : `left:${50 - w}%;width:${w}%` }));
    }
    return b;
  }

  function phaseLabel(d, k) {
    const p = d.phases.find((x) => x.key === k);
    return p ? p.label : "—";
  }

  // ── El reloj: x = impulso (pts/mes), y = CLI − 100 ───────────────────────
  function clock(d) {
    const box = el("div", { class: "ciclo-clock" });
    const tip = el("div", { class: "tip", hidden: "" });
    const W = 400, H = 340, L = 34, R = 386, T = 14, B = 300;
    const pts = d.trail.filter((t) => t.level != null);
    const xs = pts.map((t) => Math.abs(t.momentum)).concat([0.15]);
    const ys = pts.map((t) => Math.abs(t.level - 100)).concat([0.8]);
    if (d.clock_g20) { xs.push(Math.abs(d.clock_g20.momentum)); ys.push(Math.abs(d.clock_g20.level - 100)); }
    const mx = Math.max(...xs) * 1.2, my = Math.max(...ys) * 1.2;
    const X = (m) => L + (m + mx) / (2 * mx) * (R - L);
    const Y = (lv) => B - (lv - 100 + my) / (2 * my) * (B - T);
    const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Reloj del ciclo: ${phaseLabel(d, d.phase)}` });
    s.append(svg("line", { class: "ax", x1: L, x2: R, y1: Y(100), y2: Y(100) }),
      svg("line", { class: "ax", x1: X(0), x2: X(0), y1: T, y2: B }));
    const quads = [["recesion", L + 6, T + 14, "start"], ["auge", R - 6, T + 14, "end"],
      ["crisis", L + 6, B - 8, "start"], ["recuperacion", R - 6, B - 8, "end"]];
    // El cuadrado de color va por FUERA del texto (a la izquierda o a la
    // derecha según el lado), así no depende de cuánto mida la palabra.
    for (const [k, x, y, anchor] of quads) {
      const label = k === "crisis" ? "Crisis · con estrés" : phaseLabel(d, k);
      const left = anchor === "start";
      s.append(svg("rect", { class: "q-sw", x: left ? x : x - 9, y: y - 9, width: 9, height: 9, fill: color(k) }),
        svg("text", { class: "q-lbl", x: left ? x + 14 : x - 14, y, "text-anchor": anchor }, label));
    }
    s.append(svg("text", { class: "ax-lbl", x: (L + R) / 2, y: B + 18, "text-anchor": "middle" },
      "← pierde impulso · impulso del CLI (3 meses) · gana impulso →"),
    svg("text", { class: "ax-lbl", x: 12, y: (T + B) / 2, "text-anchor": "middle",
      transform: `rotate(-90 12 ${(T + B) / 2})` }, "← bajo la tendencia · CLI − 100 · sobre ella →"));
    if (pts.length > 1) {
      s.append(svg("polyline", { class: "trail",
        points: pts.map((t) => `${X(t.momentum).toFixed(1)},${Y(t.level).toFixed(1)}`).join(" ") }));
    }
    const hover = [];
    pts.forEach((t, i) => {
      const last = i === pts.length - 1;
      s.append(svg("circle", { class: last ? "dot now" : "dot", cx: X(t.momentum), cy: Y(t.level),
        r: last ? 7 : 4, fill: color(t.phase || "auge") }));
      hover.push([X(t.momentum), Y(t.level), [mes(t.month + "-01"), ["Fase", phaseLabel(d, t.phase)],
        ["CLI", num(t.level)], ["Impulso", signed(t.momentum, 3) + " pts/mes"]]]);
    });
    if (pts.length) {
      const t = pts[pts.length - 1];
      // A la izquierda del punto si está en la mitad derecha, para no pisar
      // la etiqueta de su propio cuadrante.
      const right = X(t.momentum) > (L + R) / 2;
      s.append(svg("text", { class: "pt-lbl", x: X(t.momentum) + (right ? -12 : 12), y: Y(t.level) + 4,
        "text-anchor": right ? "end" : "start" }, "Hoy"));
    }
    if (d.clock_g20) {
      const g = d.clock_g20;
      s.append(svg("circle", { class: "g20", cx: X(g.momentum), cy: Y(g.level), r: 6 }),
        svg("text", { class: "pt-lbl", x: X(g.momentum) + 10, y: Y(g.level) + 14 }, "G20"));
      hover.push([X(g.momentum), Y(g.level), [`G20 · ${mes(g.date)}`, ["Fase (solo reloj)", phaseLabel(d, d.phase_g20)],
        ["CLI", num(g.level)], ["Impulso", signed(g.momentum, 3) + " pts/mes"]]]);
    }
    for (const [x, y, rows] of hover) {
      const hit = svg("circle", { class: "hit", cx: x, cy: y, r: 11 });
      hit.addEventListener("mousemove", (e) => tipAt(box, tip, e, rows));
      hit.addEventListener("mouseleave", () => { tip.hidden = true; });
      s.append(hit);
    }
    box.append(s, tip, el("div", { class: "mut small" },
      `Estela: últimos ${pts.length} meses, cada punto con el color de su fase. Debajo de la tendencia y cayendo ` +
      "es Recesión mientras no haya estrés grave; con él, Crisis. Círculo discontinuo: G20 (solo el reloj; los pilares son de EE. UU.)."));
    return box;
  }

  // ── La lectura: fase, confianza, probabilidades y alarmas ────────────────
  function reading(d) {
    const box = el("div", { class: "ciclo-read" });
    box.append(el("div", { class: "ciclo-phase" },
      el("i", { class: "ph-sw", style: `background:${color(d.phase)}` }),
      el("span", { class: "ph-name" }, phaseLabel(d, d.phase)),
      el("span", { class: "ph-conf", title: "Probabilidad de la fase leída" }, `${pct(d.confidence)} de confianza`)));
    const c = d.clock;
    box.append(el("p", { class: "ciclo-why" }, WHY[d.phase] || "",
      el("br"), `CLI OCDE EE. UU. ${num(c.level)} (${mes(c.date)}), impulso ${signed(c.momentum, 3)} pts/mes`,
      d.phase_g20 ? ` · G20: ${phaseLabel(d, d.phase_g20)}` : ""));
    const probs = el("div", { class: "ciclo-probs", role: "table", "aria-label": "Probabilidad por fase" });
    for (const p of d.phases) {
      const v = d.probs[p.key] || 0;
      probs.append(el("span", { class: "plbl" }, el("i", { style: `background:${color(p.key)}` }), p.label),
        el("div", { class: "track" }, el("div", { class: "bar", style: `width:${(v * 100).toFixed(1)}%;background:${color(p.key)}` })),
        el("b", {}, pct(v)));
    }
    box.append(probs);
    const al = el("ul", { class: "ciclo-alarms" });
    if (!d.alarms.length) al.append(el("li", { class: "ok" }, el("span", { class: "ic" }, "✓"), "Sin alarmas de estrés."));
    for (const a of d.alarms) {
      al.append(el("li", { class: a.severe ? "sev" : "warn", title: a.severe ? "Alarma grave: empuja la fase" : "Aviso: se enseña, no mueve la fase" },
        el("span", { class: "ic" }, a.severe ? "!" : "△"), a.text));
    }
    box.append(al);
    return box;
  }

  // ── Pilares, con el detalle de cada indicador plegado ────────────────────
  function pillars(d) {
    const box = el("div", { class: "ciclo-wide ciclo-pillars" });
    box.append(el("div", { class: "ciclo-h" }, `Pilares · termómetro compuesto ${signed(d.composite)}`,
      el("span", { class: "w", title: "−1: economía en contracción o estresada · +1: en expansión" }, "(de −1 a +1)")));
    for (const p of d.pillars) {
      const det = el("details", {});
      det.append(el("summary", {},
        el("span", { class: "nm" }, p.label, el("span", { class: "w" }, `${Math.round(p.weight * 100)} %`)),
        divBar(p.score), el("span", { class: "sc" }, p.score == null ? "sin dato" : signed(p.score))));
      const tb = el("table", { class: "ciclo-ind" });
      for (const i of p.indicators) {
        tb.append(el("tr", {},
          el("td", {}, i.label, i.note ? el("span", { class: "nt" }, i.note) : null),
          el("td", { class: "v" }, i.value == null ? el("span", { class: "na" }, "sin dato") : num(i.value) + UNIT[i.unit]),
          el("td", { class: "d" }, i.as_of ? i.as_of : ""),
          el("td", { class: "b" }, divBar(i.score, true))));
      }
      det.append(tb);
      box.append(det);
    }
    return box;
  }

  // ── Historial: una franja de meses coloreada por fase, más su tabla ──────
  function strip(d) {
    const box = el("div", { class: "ciclo-wide" });
    const h = d.history.filter((x) => x.phase);
    box.append(el("div", { class: "ciclo-h" }, `Historial desde ${mes(h[0].month + "-01")}`));
    const wrap = el("div", { class: "ciclo-strip" });
    const tip = el("div", { class: "tip", hidden: "" });
    const W = 1000, s = svg("svg", { viewBox: `0 0 ${W} 34`, preserveAspectRatio: "none", role: "img",
      "aria-label": "Fase del ciclo mes a mes; la tabla de abajo la da en texto" });
    const w = W / h.length;
    const years = el("div", { class: "ciclo-years", "aria-hidden": "true" });
    h.forEach((x, i) => {
      s.append(svg("rect", { x: (i * w).toFixed(2), y: 0, width: (w + 0.4).toFixed(2), height: 28, fill: color(x.phase) }));
      if (x.month.endsWith("-01") && +x.month.slice(0, 4) % 5 === 0) {
        s.append(svg("line", { class: "tk", x1: i * w, x2: i * w, y1: 28, y2: 33 }));
        // Los años en HTML: el SVG se estira sin conservar proporción y un texto
        // dentro saldría aplastado en el móvil.
        years.append(el("span", { style: `left:${(i / h.length * 100).toFixed(2)}%` }, x.month.slice(0, 4)));
      }
    });
    s.addEventListener("mousemove", (e) => {
      const r = s.getBoundingClientRect();
      const i = Math.min(h.length - 1, Math.max(0, Math.floor((e.clientX - r.left) / r.width * h.length)));
      tipAt(wrap, tip, e, [mes(h[i].month + "-01"), ["Fase", phaseLabel(d, h[i].phase)],
        ["Compuesto", signed(h[i].composite)]]);
    });
    s.addEventListener("mouseleave", () => { tip.hidden = true; });
    wrap.append(s, years, tip);
    const leg = el("div", { class: "chart-legend" });
    for (const p of d.phases) leg.append(el("span", {}, el("i", { style: `background:${color(p.key)}` }), p.label));
    // La misma información en texto: tramos seguidos de la misma fase.
    const runs = [];
    for (const x of h) {
      const last = runs[runs.length - 1];
      if (last && last.phase === x.phase) last.to = x.month; else runs.push({ phase: x.phase, from: x.month, to: x.month });
    }
    const tb = el("table", {});
    for (const r of runs.slice().reverse()) {
      tb.append(el("tr", {}, el("td", {}, `${mes(r.from + "-01")} → ${mes(r.to + "-01")}`),
        el("td", {}, el("i", { style: `display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;background:${color(r.phase)}` }),
          phaseLabel(d, r.phase))));
    }
    box.append(wrap, leg, el("details", { class: "ciclo-runs" }, el("summary", {}, `Ver los ${runs.length} tramos en tabla`), tb));
    return box;
  }

  function newsBlock(d) {
    const n = d.news;
    const box = el("div", { class: "ciclo-wide" });
    box.append(el("div", { class: "ciclo-h" }, n.score == null
      ? `Titulares (${n.n}): sin palabras de ciclo con las que medir el tono`
      : `Titulares de los últimos 3 días · tono ${signed(n.score)} (${n.pos} a favor / ${n.neg} en contra, sobre ${n.n})`));
    const ul = el("ul", { class: "ciclo-news" });
    for (const h of n.headlines) {
      const safe = /^https?:\/\//.test(h.link);
      const mood = h.mood > 0 ? ["up", "▲"] : h.mood < 0 ? ["dn", "▼"] : ["nt", "·"];
      ul.append(el("li", {}, el("span", { class: "md " + mood[0] }, mood[1]),
        safe ? el("a", { href: h.link, target: "_blank", rel: "noopener noreferrer" }, h.title) : el("span", {}, h.title),
        el("span", { class: "src" }, [h.source, h.date].filter(Boolean).join(" · "))));
    }
    box.append(ul);
    return box;
  }

  function foot(d) {
    const f = el("div", { class: "ciclo-wide ciclo-foot" });
    for (const s of d.substitutes) f.append(el("div", {}, el("b", {}, s.wanted), ` → ${s.used} (${s.why}).`));
    if (d.sources.failed.length) f.append(el("div", { class: "ciclo-stale" }, `Sin respuesta hoy: ${d.sources.failed.join(", ")} (se calculó con el resto).`));
    if (d.sources.stale.length) f.append(el("div", { class: "ciclo-stale" }, `Copia de disco por fallo de red: ${d.sources.stale.join(", ")}.`));
    f.append(el("div", {}, "Fuentes: FRED (St. Louis Fed), OCDE, índices EPU/EMV de Baker, Bloom y Davis, Google News. " +
      "Datos revisados, no en tiempo real; cada dato se usa solo desde su fecha habitual de publicación. " +
      "Es una lectura del estado de la economía de EE. UU., no una recomendación de inversión."));
    return f;
  }

  async function load() {
    const host = $("ciclo"), leg = $("ciclo-legend");
    if (!host || !leg) return;
    let d = null;
    try {
      const r = await fetch("/api/cartera/ciclo");
      d = await r.json();
      if (!r.ok) throw new Error(d.error || r.statusText);
    } catch (e) {
      leg.textContent = "no disponible";
      host.replaceChildren(el("p", { class: "mut small" }, `No pude cargar el ciclo: ${e.message}`));
      return;
    }
    const when = new Date(d.generated_at * 1000);
    leg.textContent = `EE. UU. · dato a ${d.as_of} · calculado ${when.toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" })}` +
      (d.stale ? " · copia anterior" : "");
    const grid = el("div", { class: "ciclo-grid" });
    grid.append(clock(d), reading(d), pillars(d), strip(d), newsBlock(d), foot(d));
    host.replaceChildren(grid);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
