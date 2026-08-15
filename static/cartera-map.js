/* Exposición geográfica — coropleta de la cartera con transparencia.

   SVG y no canvas, apartándose a propósito de la gráfica de evolución de esta
   misma página. Saber qué país hay bajo el cursor en canvas obliga a resolver
   punto-en-polígono a mano o a mantener un buffer de color-índice; en SVG el
   hover, el foco y el teclado salen del propio DOM. La serie temporal es otro
   problema y por eso sigue en canvas. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  // El ojo de la cabecera marca el <body> y este módulo lo lee de ahí: es otro
  // fichero con su propio ámbito, y una bandera en el DOM es la única fuente
  // que ambos comparten sin acoplarse. Los PORCENTAJES no se tapan — un reparto
  // por países no dice cuánto dinero hay.
  const MASK = "•••";
  const hidden = () => document.body.classList.contains("amounts-hidden");
  const fmt = (x, d) => Number(x).toLocaleString("es-ES",
    { minimumFractionDigits: d, maximumFractionDigits: d });
  const money = (x, d = 2) => (x == null ? "—" : hidden() ? MASK : fmt(x, d));
  const eur = (x, d = 2) => (x == null ? "—" : money(x, d) + " €");
  const pct = (x) => (x == null ? "—" : fmt(x, x < 1 ? 2 : 1) + "%");

  // ── Rampa secuencial de un solo tono (azul, el mismo familiar de --accent).
  // Magnitud continua: un único tono claro→oscuro, nunca un arcoíris.
  const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
  // En oscuro el ancla se invierte: cerca de cero retrocede hacia el fondo y el
  // máximo es el escalón más luminoso. Voltear la rampa clara sin más dejaría el
  // valor más alto casi fundido con el panel.
  const isLight = () => window.matchMedia("(prefers-color-scheme: light)").matches;
  const ramp = () => (isLight() ? RAMP : RAMP.slice().reverse());

  const hex2rgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16),
    parseInt(h.slice(5, 7), 16)];
  function shade(t) {
    // t ya viene comprimido; aquí sólo se interpola ENTRE escalones documentados,
    // así que ningún color pintado sale de la rampa validada.
    const R = ramp();
    const x = Math.max(0, Math.min(1, t)) * (R.length - 1);
    const i = Math.min(R.length - 2, Math.floor(x)), f = x - i;
    const a = hex2rgb(R[i]), b = hex2rgb(R[i + 1]);
    return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
  }
  // Raíz cuadrada: con EE.UU. en ~68% del tramo de bolsa, una escala lineal deja
  // Japón, Alemania y Taiwán indistinguibles del vacío. La leyenda publica los %
  // reales, así que la compresión ayuda a leer sin falsear la cifra.
  const compress = (v, max) => (max > 0 ? Math.sqrt(v / max) : 0);

  // ── Proyección Natural Earth (Šavrič 2011): polinomio cerrado, sin librería.
  function project(lon, lat) {
    const l = lon * Math.PI / 180, p = lat * Math.PI / 180;
    const p2 = p * p, p4 = p2 * p2;
    const x = l * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4)));
    const y = p * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)));
    return [x * 100, -y * 100];          // y invertida: en SVG crece hacia abajo
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  let GEO = null, DATA = null, clase = "all", nodes = new Map(), busy = false;
  // Comprar algo nuevo dispara la búsqueda de su desglose en el servidor, que
  // tarda unos segundos. El mapa vuelve a preguntar mientras el propio backend
  // diga que sigue buscando, con tope: un instrumento que no se puede resolver
  // no puede dejar la página consultando para siempre.
  const RETRY_MS = 6000, MAX_RETRIES = 20;
  let retries = 0, retryTimer = null;

  async function geometry() {
    if (GEO) return GEO;
    // La URL la pone la plantilla con `asset()`, así que lleva la marca de
    // versión: regenerar la geometría invalida la caché del navegador sola.
    const src = $("wmap").dataset.geo || "/static/world-110m.json";
    const raw = await (await fetch(src)).json();
    const shapes = raw.shapes || {};
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    const paths = {};
    for (const [iso, polys] of Object.entries(shapes)) {
      let d = "";
      for (const rings of polys) {
        for (const ring of rings) {
          for (let i = 0; i < ring.length; i++) {
            const [x, y] = project(ring[i][0], ring[i][1]);
            if (x < minX) minX = x; if (x > maxX) maxX = x;
            if (y < minY) minY = y; if (y > maxY) maxY = y;
            d += (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
          }
          d += "Z";
        }
      }
      if (d) paths[iso] = d;
    }
    GEO = { paths, box: [minX, minY, maxX - minX, maxY - minY] };
    return GEO;
  }

  function buildSvg(g) {
    const svg = $("wmap");
    svg.setAttribute("viewBox", g.box.map((v) => v.toFixed(1)).join(" "));
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    // Dos capas con la MISMA geometría: debajo una silueta gemela que solo existe
    // para proyectar la sombra (va rellena del color del fondo, así que no se ve:
    // lo único que asoma es la sombra alrededor de la costa), y encima los países
    // de verdad, SIN filtro — se dibujan como vectores y las fronteras quedan
    // nítidas. Filtrar la capa visible sería rasterizarla y emborronarla.
    const shadow = document.createElementNS(SVG_NS, "g");
    shadow.setAttribute("class", "land-shadow");
    shadow.setAttribute("filter", "url(#wrelief)");
    shadow.setAttribute("aria-hidden", "true");
    const land = document.createElementNS(SVG_NS, "g");
    land.setAttribute("class", "land");
    nodes = new Map();
    for (const [iso, d] of Object.entries(g.paths)) {
      const s = document.createElementNS(SVG_NS, "path");
      s.setAttribute("d", d);
      shadow.appendChild(s);
      const p = document.createElementNS(SVG_NS, "path");
      p.setAttribute("d", d);
      p.setAttribute("class", "zero");
      p.dataset.iso = iso;
      land.appendChild(p);
      nodes.set(iso, p);
    }
    svg.appendChild(shadow);
    svg.appendChild(land);
    svg.addEventListener("mousemove", onMove);
    svg.addEventListener("mouseleave", () => hideTip());
    svg.addEventListener("focusin", (e) => { if (e.target.dataset && e.target.dataset.iso) showTipFor(e.target.dataset.iso, null); });
    svg.addEventListener("focusout", () => hideTip());
  }

  // ── pintura ───────────────────────────────────────────────────────────────
  function paint() {
    if (!DATA || !nodes.size) return;
    const byIso = new Map(DATA.countries.map((c) => [c.iso2, c]));
    const max = DATA.countries.length ? DATA.countries[0].pct : 0;
    for (const [iso, node] of nodes) {
      const c = byIso.get(iso);
      if (!c) {
        node.setAttribute("class", "zero");
        node.removeAttribute("fill");
        node.removeAttribute("tabindex");
        node.replaceChildren();
        continue;
      }
      node.setAttribute("class", "has");
      node.setAttribute("fill", shade(compress(c.pct, max)));
      // Sólo los países CON exposición entran en el orden de tabulación: 174
      // paradas de tabulador para llegar a veinte países útiles no es accesible,
      // es un laberinto.
      node.setAttribute("tabindex", "0");
      let t = node.querySelector("title");
      if (!t) { t = document.createElementNS(SVG_NS, "title"); node.appendChild(t); }
      t.textContent = `${c.name}: ${pct(c.pct)} · ${eur(c.eur)}`;
    }
    legend(max);
    rank(byIso, max);
    notes();
  }

  function legend(max) {
    const R = ramp();
    const stops = R.map((c, i) => `${c} ${(i / (R.length - 1) * 100).toFixed(1)}%`).join(",");
    // Las marcas van donde caen en la BARRA (posición t), etiquetadas con el
    // valor real que representan (t²·max). Que la mitad de la barra sea un
    // cuarto del máximo es justo lo que hay que poder ver.
    const ticks = [0, 0.5, 1].map((t) => {
      const v = t * t * max;
      return `<span style="left:${(t * 100).toFixed(0)}%">${pct(v)}</span>`;
    }).join("");
    $("map-legend").innerHTML =
      `<span class="nozero"><i class="swatch"></i>sin exposición</span>` +
      `<span class="scale">` +
      `<span class="bar" style="background:linear-gradient(90deg,${stops})"></span>` +
      `<span class="ticks">${ticks}</span></span>`;
  }

  function rank(byIso, max) {
    const rows = DATA.countries.map((c) => {
      const drawable = nodes.has(c.iso2);
      return `<tr class="${drawable ? "" : "dim"}" data-iso="${esc(c.iso2)}">
        <td><i class="sw" style="background:${shade(compress(c.pct, max))}"></i>${esc(c.name)}${drawable ? "" : " *"}</td>
        <td class="num">${pct(c.pct)}</td><td class="num">${money(c.eur, 0)}</td></tr>`;
    }).join("");
    $("geo-rank-body").innerHTML = rows ||
      `<tr><td colspan="3" class="mut">Sin exposición que mapear.</td></tr>`;
  }

  function notes() {
    const d = DATA, out = [];
    const share = (v) => d.total_eur > 0 ? ` (${pct(v / d.total_eur * 100)} de lo valorado)` : "";
    if (d.other_eur > 0.005)
      out.push(`<div><b>${eur(d.other_eur)}</b> en países que la fuente agrupa como
        «otros» y no desglosa${share(d.other_eur)}. No se reparte entre los de arriba.</div>`);
    if (d.no_geography_eur > 0.005)
      out.push(`<div><b>${eur(d.no_geography_eur)}</b> sin país: oro físico${share(d.no_geography_eur)}.</div>`);
    // El aviso de búsqueda se cuelga de `seeding`, que es lo que el servidor
    // sabe, y no de `unmapped`: un instrumento que además está sin valorar cae
    // en `excluded` y se quedaba sin avisar de que su desglose venía en camino.
    const seeking = new Set(d.seeding || []);
    for (const u of d.unmapped)
      if (!seeking.has(u.ticker))
        out.push(`<div class="flag">${esc(u.name)}: <b>${eur(u.eur)}</b> fuera del mapa — ${esc(u.why)}.</div>`);
    for (const x of d.excluded)
      if (!seeking.has(x.ticker))
        out.push(`<div class="flag">${esc(x.ticker)} fuera del mapa: ${esc(x.why)}.</div>`);
    for (const tk of seeking) {
      const hit = d.unmapped.find((u) => u.ticker === tk);
      out.push(`<div>Buscando el desglose por países de <b>${esc(hit ? hit.name : tk)}</b>${
        hit ? ` (${eur(hit.eur)})` : ""}… el mapa se actualiza solo en cuanto llegue.</div>`);
    }
    // Países que existen en los datos pero no en la geometría a 1:110m. Se dicen
    // en voz alta: sin esto, su peso parecería cero en vez de no dibujable.
    const missing = d.countries.filter((c) => !nodes.has(c.iso2));
    if (missing.length)
      out.push(`<div>* ${missing.map((c) => esc(c.name)).join(", ")}: sin contorno propio a
        esta escala. Cuentan en la lista y en los totales, pero no se pintan.</div>`);
    $("geo-notes").innerHTML = out.join("");
  }

  function sources() {
    const s = DATA.sources || [];
    const items = s.map((x) => {
      const via = x.proxy ? ` vía <b>${esc(x.proxy)}</b>${x.proxy_note ? ` — ${esc(x.proxy_note)}` : ""}`
        : (x.proxy_note ? ` — ${esc(x.proxy_note)}` : "");
      const link = x.source ? ` <a href="${esc(x.source)}" target="_blank" rel="noopener">ficha</a>` : "";
      return `<div>${esc(x.name)}${via}${link}</div>`;
    }).join("");
    $("geo-src").innerHTML =
      `Pesos por transparencia a fecha <b>${esc(DATA.as_of || "—")}</b>, top-10 países por
       instrumento (fuente justETF). Los fondos no cotizados toman los pesos de un ETF
       del mismo índice: es una aproximación, no la cartera exacta del fondo.
       Contornos: Natural Earth 1:110m, dominio público.
       <details><summary style="cursor:pointer">Ver referencia por instrumento</summary>${items}</details>`;
  }

  // ── tooltip ───────────────────────────────────────────────────────────────
  function hideTip() {
    $("mtip").hidden = true;
    for (const n of nodes.values()) n.classList.remove("sel");
    for (const tr of $("geo-rank-body").querySelectorAll("tr.sel")) tr.classList.remove("sel");
  }

  function showTipFor(iso, ev) {
    const c = (DATA && DATA.countries.find((x) => x.iso2 === iso)) || null;
    const tip = $("mtip");
    if (!c) { hideTip(); return; }
    const who = c.contributors.map((k) =>
      `<div class="tr"><span>${esc(k.name)}</span><b>${eur(k.eur, 0)}</b></div>`).join("");
    tip.innerHTML = `<div class="td">${esc(c.name)}</div>
      <div class="tr"><span>Peso</span><b>${pct(c.pct)}</b></div>
      <div class="tr"><span>Valor</span><b>${eur(c.eur, 0)}</b></div>
      <div class="who">${who}</div>`;
    tip.hidden = false;
    const host = tip.parentElement.getBoundingClientRect();
    // `node` es null para un país con peso pero sin contorno a 1:110m (Jersey,
    // Hong Kong). Se sigue pudiendo señalar desde la lista, así que el tooltip
    // cae al centro del mapa en vez de reventar.
    const node = nodes.get(iso) || null;
    if (ev) {
      const x = ev.clientX - host.left, y = ev.clientY - host.top;
      tip.style.left = Math.max(0, Math.min(host.width - tip.offsetWidth, x + 14)) + "px";
      tip.style.top = Math.max(0, y - tip.offsetHeight - 12) + "px";
    } else if (node) {
      const b = node.getBoundingClientRect();
      tip.style.left = Math.max(0, Math.min(host.width - tip.offsetWidth, b.left - host.left)) + "px";
      tip.style.top = Math.max(0, b.top - host.top - tip.offsetHeight - 8) + "px";
    } else {
      tip.style.left = Math.max(0, (host.width - tip.offsetWidth) / 2) + "px";
      tip.style.top = "8px";
    }
    for (const [k, n] of nodes) n.classList.toggle("sel", k === iso);
    for (const tr of $("geo-rank-body").querySelectorAll("tr[data-iso]"))
      tr.classList.toggle("sel", tr.dataset.iso === iso);
  }

  function onMove(ev) {
    const t = ev.target;
    // classList, no getAttribute: al señalar desde la lista el país lleva además
    // la clase `sel`, y comparar la cadena entera lo daría por no-país.
    if (!t || !t.dataset || !t.dataset.iso || !t.classList.contains("has")) { hideTip(); return; }
    showTipFor(t.dataset.iso, ev);
  }

  // ── carga ─────────────────────────────────────────────────────────────────
  async function load(fresh = true) {
    if (busy) return;
    // Un cambio de filtro o de cartera empieza de cero; un reintento no, o el
    // tope no llegaría nunca.
    if (fresh) { retries = 0; }
    clearTimeout(retryTimer);
    busy = true;
    try {
      const g = await geometry();
      if (!nodes.size) buildSvg(g);
      const url = "/api/cartera/geo" + (clase === "all" ? "" : "?clase=" + encodeURIComponent(clase));
      const r = await fetch(url);
      if (!r.ok) throw new Error(await r.text());
      DATA = await r.json();
      paint();
      sources();
      if ((DATA.seeding || []).length && retries < MAX_RETRIES) {
        retries++;
        retryTimer = setTimeout(() => load(false), RETRY_MS);
      }
    } catch (e) {
      $("geo-notes").innerHTML = `<div class="flag">No se pudo cargar el mapa: ${esc(e.message || e)}</div>`;
    } finally { busy = false; }
  }

  $("geo-class").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-clase]");
    if (!b || b.classList.contains("on")) return;
    for (const x of $("geo-class").children) x.classList.toggle("on", x === b);
    clase = b.dataset.clase;
    load();
  });

  $("geo-rank-body").addEventListener("mouseover", (ev) => {
    const tr = ev.target.closest("tr[data-iso]");
    if (tr) showTipFor(tr.dataset.iso, null);
  });
  $("geo-rank-body").addEventListener("mouseleave", hideTip);

  // Repintar al cambiar el tema: la rampa se elige en JS, así que un cambio de
  // claro a oscuro no lo arregla el CSS solo.
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => paint());

  // La cartera cambia cuando se añade, importa o borra un movimiento. cartera.js
  // avisa; el mapa no repregunta por su cuenta.
  document.addEventListener("cartera:changed", load);
  // El ojo no cambia los datos, solo cómo se ven: repintar basta.
  document.addEventListener("cartera:display", () => paint());
  load();
})();
