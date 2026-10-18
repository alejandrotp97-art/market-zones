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

  // ── La onda: CLI − 100 en el tiempo, como la señal de una radio ─────────
  // Por encima de 0 la economía va sobre su tendencia; cada cresta es un pico
  // del ciclo y cada valle un fondo. El color de cada tramo es la fase que se
  // leyó ese mes, y debajo va la misma fase como franja, con el mismo eje X.
  const RANGES = [["5A", 60], ["10A", 120], ["Todo", 0]];

  function runsOf(h) {
    const runs = [];
    h.forEach((x, i) => {
      const last = runs[runs.length - 1];
      if (last && last.phase === x.phase) { last.to = x.month; last.b = i; }
      else runs.push({ phase: x.phase, from: x.month, to: x.month, a: i, b: i });
    });
    return runs;
  }

  function wave(d) {
    const box = el("div", { class: "ciclo-wide ciclo-wave" });
    const all = d.history.filter((x) => x.level != null && x.phase);
    let months = 120;
    try {
      const saved = localStorage.getItem("ciclo-rango");
      if (saved !== null && RANGES.some(([, m]) => String(m) === saved)) months = +saved;
    } catch (e) { /* sin almacenamiento: se queda en 10 años */ }
    const seg = el("span", { class: "seg mini", role: "group", "aria-label": "Periodo de la onda" });
    const host = el("div", { class: "ciclo-wave-host" });
    const tip = el("div", { class: "tip", hidden: "" });
    box.append(el("div", { class: "ciclo-h ciclo-wave-h" },
      el("span", {}, "La onda del ciclo · CLI OCDE EE. UU. frente a su tendencia"), seg), host);

    function draw() {
      const pts = months ? all.slice(-months) : all;
      if (pts.length < 2) { host.replaceChildren(el("p", { class: "mut small" }, "Sin historia suficiente.")); return; }
      const W = Math.max(280, Math.floor(host.clientWidth || 600)), H = 286;
      const L = 38, R = W - 14, T = 18, B = 232, BAND = 10, BY = B + 10;
      // Escala por el percentil 95, no por el extremo: un desplome como el del
      // COVID (−7) aplastaba el resto de la onda hasta dejarla casi plana. Lo
      // que se sale se recorta en el borde y se rotula con su valor real.
      const devs = pts.map((p) => Math.abs(p.level - 100)).sort((a, b) => a - b);
      const amp = Math.max(1, devs[Math.floor((devs.length - 1) * 0.95)] * 1.25);
      const clip = (lv) => 100 + Math.max(-amp, Math.min(amp, lv - 100));
      const n = pts.length, dx = (R - L) / (n - 1);
      const X = (i) => L + i * dx;
      const Y = (lv) => T + (1 - (clip(lv) - 100 + amp) / (2 * amp)) * (B - T);
      const s = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
        "aria-label": `Onda del ciclo: hoy ${phaseLabel(d, d.phase)}; la tabla de tramos lo da en texto` });
      const step = amp <= 1.5 ? 0.5 : amp <= 3.5 ? 1 : 2;
      for (let v = -Math.floor(amp / step) * step; v <= amp; v += step) {
        const y = Y(100 + v);
        s.append(svg("line", { class: v === 0 ? "trend" : "grid", x1: L, x2: R, y1: y, y2: y }),
          svg("text", { class: "ylbl", x: L - 6, y: y + 3.5, "text-anchor": "end" },
            v === 0 ? "100" : (v > 0 ? "+" : "−") + num(Math.abs(v), step < 1 ? 1 : 0)));
      }
      const y0 = Y(100);
      for (const r of runsOf(pts)) {
        // Cada tramo se une con el primer punto del siguiente: la onda no se corta.
        const idx = [];
        for (let i = r.a; i <= Math.min(r.b + 1, n - 1); i++) idx.push(i);
        const line = idx.map((i) => `${X(i).toFixed(1)},${Y(pts[i].level).toFixed(1)}`).join(" ");
        s.append(svg("polygon", { class: "fill", fill: color(r.phase),
          points: `${X(idx[0]).toFixed(1)},${y0.toFixed(1)} ${line} ${X(idx[idx.length - 1]).toFixed(1)},${y0.toFixed(1)}` }),
        svg("polyline", { class: "wave", stroke: color(r.phase), points: line }),
        svg("rect", { x: (X(r.a) - dx / 2).toFixed(1), y: BY, height: BAND,
          width: Math.max(1, (r.b - r.a + 1) * dx).toFixed(1), fill: color(r.phase) }));
      }
      // Rótulo de cada tramo recortado, en su punto más extremo.
      let i0 = 0;
      while (i0 < n) {
        if (Math.abs(pts[i0].level - 100) <= amp) { i0++; continue; }
        let j = i0, k = i0;
        while (j + 1 < n && Math.abs(pts[j + 1].level - 100) > amp) { j++; if (Math.abs(pts[j].level - 100) > Math.abs(pts[k].level - 100)) k = j; }
        const below = pts[k].level < 100;
        s.append(svg("text", { class: "clip-lbl", x: X(k), y: below ? B + 2 - 4 : T + 9, "text-anchor": X(k) < L + 60 ? "start" : "middle" },
          `${below ? "▼" : "▲"} ${signed(pts[k].level - 100, 1)} ${mes(pts[k].month + "-01")}`));
        i0 = j + 1;
      }
      const span = n / 12, every = span <= 6 ? 1 : span <= 12 ? 2 : 5;
      pts.forEach((p, i) => {
        if (p.month.endsWith("-01") && +p.month.slice(0, 4) % every === 0) {
          s.append(svg("line", { class: "tk", x1: X(i), x2: X(i), y1: BY + BAND, y2: BY + BAND + 4 }),
            svg("text", { class: "xlbl", x: X(i), y: BY + BAND + 16, "text-anchor": "middle" }, p.month.slice(0, 4)));
        }
      });
      const last = pts[n - 1];
      s.append(svg("circle", { class: "now", cx: X(n - 1), cy: Y(last.level), r: 5, fill: color(last.phase) }),
        svg("text", { class: "pt-lbl", x: X(n - 1) - 9, y: Y(last.level) - 9, "text-anchor": "end" }, "Hoy"));
      const cross = svg("line", { class: "cross", x1: 0, x2: 0, y1: T, y2: BY + BAND, visibility: "hidden" });
      const dot = svg("circle", { class: "now", r: 4, visibility: "hidden" });
      const hit = svg("rect", { class: "hit", x: L - dx / 2, y: T, width: R - L + dx, height: BY + BAND - T });
      s.append(cross, dot, hit);
      hit.addEventListener("mousemove", (e) => {
        const box2 = s.getBoundingClientRect();
        const i = Math.max(0, Math.min(n - 1, Math.round((e.clientX - box2.left - L) / dx)));
        const p = pts[i];
        for (const a of ["x1", "x2"]) cross.setAttribute(a, X(i));
        dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(p.level)); dot.setAttribute("fill", color(p.phase));
        cross.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
        tipAt(host, tip, e, [mes(p.month + "-01"), ["Fase", phaseLabel(d, p.phase)],
          ["CLI", num(p.level)], ["Sobre la tendencia", signed(p.level - 100)],
          ["Impulso", signed(p.momentum, 3) + " pts/mes"]]);
      });
      hit.addEventListener("mouseleave", () => {
        tip.hidden = true; cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden");
      });
      host.replaceChildren(s, tip);
    }

    for (const [label, m] of RANGES) {
      const b = el("button", { type: "button", class: m === months ? "on" : "" }, label);
      b.addEventListener("click", () => {
        months = m;
        for (const x of seg.children) x.classList.toggle("on", x === b);
        try { localStorage.setItem("ciclo-rango", String(m)); } catch (e) { /* sin almacenamiento */ }
        draw();
      });
      seg.append(b);
    }

    const leg = el("div", { class: "chart-legend" });
    for (const p of d.phases) leg.append(el("span", {}, el("i", { style: `background:${color(p.key)}` }), p.label));
    const tb = el("table", {});
    for (const r of runsOf(all).reverse()) {
      tb.append(el("tr", {}, el("td", {}, `${mes(r.from + "-01")} → ${mes(r.to + "-01")}`),
        el("td", {}, el("i", { class: "sw", style: `background:${color(r.phase)}` }), phaseLabel(d, r.phase))));
    }
    box.append(leg, el("p", { class: "mut small" },
      "Por encima de la línea de 100 la economía crece más que su tendencia; por debajo, menos. " +
      "Debajo y cayendo es Recesión mientras no haya estrés grave; con él, Crisis."),
    el("details", { class: "ciclo-runs" }, el("summary", {}, `Ver los ${tb.children.length} tramos en tabla`), tb));

    // Se dibuja a la anchura real (el texto del SVG no se deforma) y se
    // redibuja si la tarjeta cambia de ancho.
    let lastW = 0;
    const ro = new ResizeObserver(() => {
      const w = Math.floor(host.clientWidth);
      if (w && w !== lastW) { lastW = w; draw(); }
    });
    ro.observe(host);
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
    const box = el("div", { class: "ciclo-pillars" });
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
      " · se actualiza cada domingo" +
      (d.stale ? " · copia anterior" : "");
    const grid = el("div", { class: "ciclo-grid" });
    grid.append(wave(d), reading(d), pillars(d), newsBlock(d), foot(d));
    host.replaceChildren(grid);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
