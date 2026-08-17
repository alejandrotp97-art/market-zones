/* Market-zone panel: a dependency-free Canvas chart.
 * Score line coloured by value (green=cheap -> red=expensive) on a linear
 * 0-100 axis; price as a dotted line on a log axis. Range buttons only zoom
 * the loaded series (the score is computed once, server-side). */
(() => {
  "use strict";

  const cv = document.getElementById("chart");
  const ctx = cv.getContext("2d");
  const buf = document.createElement("canvas");   // offscreen base render
  const bctx = buf.getContext("2d");
  const tip = document.getElementById("tip");
  const statusEl = document.getElementById("status");
  const host = document.querySelector(".chart-host");
  const tgtCard = document.getElementById("target");
  const tgtStatus = document.getElementById("target-status");
  const tgtGrid = document.getElementById("target-grid");
  const tgtChart = document.getElementById("target-chart");
  const tgtBuy = document.getElementById("tgt-buy");
  const tgtSell = document.getElementById("tgt-sell");
  const titleEl = document.querySelector(".titles h1");
  const assetSel = document.getElementById("asset");
  // symbol -> curated display name, read off the dropdown options once. A
  // searched ticker that isn't curated falls back to its own symbol.
  const NAMES = {};
  if (assetSel) for (const o of assetSel.options) NAMES[o.value.toUpperCase()] = o.textContent;

  const M = { L: 46, R: 54, T: 10, B: 26 };
  const GRID = [0, 20, 40, 60, 80, 100];

  const state = { full: [], view: [], range: "all", asof: "—", tf: "daily",
                  volW: 0.10, hi: 70, mid: 45, symbol: null, summary: null, model: null };
  const STATIC = !!window.__STATIC__;

  // Conviction is derived on the client from the raw `climax` + the live
  // thresholds, so moving the sliders re-labels instantly (no server round-trip).
  const EXTREME = new Set(["Capitulación", "Acumulación", "Precaución", "Euforia"]);
  function convLabel(climax, zoneName) {
    if (climax == null || !EXTREME.has(zoneName)) return null;
    if (climax >= state.hi) return "Clímax confirmado";
    if (climax >= state.mid) return "Parcial";
    return "Sin confirmar";
  }

  // Cheap -> expensive colour ramp (green 140° -> red 0°).
  const colorForScore = (s) => {
    const c = Math.max(0, Math.min(100, s));
    return `hsl(${140 * (1 - c / 100)}, 68%, 55%)`;
  };

  const fmtPrice = (p) =>
    p >= 1000 ? p.toLocaleString("es", { maximumFractionDigits: 0 })
              : p.toLocaleString("es", { maximumFractionDigits: 2 });
  const fmtDate = (t) => new Date(t).toISOString().slice(0, 10);

  // ── data ────────────────────────────────────────────
  // A monotonically increasing token guards against races: if the user fires
  // a newer load while an older one is still in flight, only the newest
  // request is allowed to touch the UI. This is what stops the "Cargando…"
  // overlay from getting stuck when two loads overlap.
  let reqSeq = 0;
  async function load(symbol) {
    if (STATIC) { apply(window.__ZONES__); return; }
    symbol = (symbol || "").trim();
    if (!symbol) return;
    const my = ++reqSeq;
    setStatus(`Cargando ${symbol}…`);
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 25000);
    try {
      const url = `/api/zones?symbol=${encodeURIComponent(symbol)}&vol_w=${state.volW}&tf=${state.tf}`;
      const r = await fetch(url, { signal: ctl.signal });
      const d = await r.json();
      if (my !== reqSeq) return;                 // superseded by a newer load
      if (d.error) { setStatus(d.error); return; }
      apply(d);                                  // apply() clears the overlay
    } catch (e) {
      if (my === reqSeq) setStatus(e.name === "AbortError" ? `Timeout cargando ${symbol}` : "Error de red: " + e.message);
    } finally {
      clearTimeout(timer);
    }
  }

  // Reflect the LOADED symbol everywhere the boot value was frozen: the page
  // title and the dropdown. A searched ticker outside the curated list is kept
  // in a single reusable "buscado" option so the selector never lies about what
  // is on screen.
  function reflectAsset(sym, apiName) {
    sym = (sym || "").trim();
    if (!sym) return;
    const key = sym.toUpperCase();
    // Curated symbols keep their hand-written name; a searched ticker gets the
    // full company name from the server (Yahoo), and only then falls back to the
    // bare symbol.
    const name = NAMES[key] || (apiName || "").trim() || sym;
    if (titleEl) titleEl.textContent = name;
    document.title = name + " · Zonas de Mercado";
    if (!assetSel) return;
    let opt = [...assetSel.options].find((o) => o.value.toUpperCase() === key);
    if (!opt) {
      opt = assetSel.querySelector("option[data-dyn]");
      if (!opt) {
        opt = document.createElement("option");
        opt.dataset.dyn = "1";
        assetSel.insertBefore(opt, assetSel.firstChild);
      }
      opt.value = sym;
      opt.textContent = name;
    }
    assetSel.value = opt.value;
  }

  function apply(d) {
    setStatus(null);
    state.full = d.series || [];
    state.asof = d.as_of || "—";
    state.summary = d.summary || null;
    state.symbol = d.symbol || state.symbol;
    state.model = d.model || state.model;
    reflectAsset(state.symbol, d.name);
    document.getElementById("asof").textContent = state.asof;
    updateVerdict(d.summary);
    renderComposition();
    applyRange();
    if (STATIC) return;
    loadTarget(state.symbol);   // target follows STATE.tf (weekly re-inverts on W-SUN bars)
  }

  // ── target price ("precio objetivo por zona") ───────
  // Its own endpoint: the inversion re-runs the engine dozens of times, so it
  // must never make the main chart wait. A monotonic token drops stale answers.
  let tgtSeq = 0;
  async function loadTarget(symbol) {
    symbol = (symbol || "").trim();
    if (!symbol) return;
    tgtCard.hidden = false;
    // [hidden] loses to `.target-status{display:flex}` / `.target-grid{display:grid}`
    // — the same stylesheet-beats-attribute trap as the load pill — so toggle the
    // inline display directly.
    tgtGrid.style.display = "none";
    tgtStatus.style.display = "flex";
    tgtStatus.className = "target-status";
    tgtStatus.textContent = "Calculando nivel…";
    const my = ++tgtSeq;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 30000);
    try {
      const url = `/api/target?symbol=${encodeURIComponent(symbol)}&vol_w=${state.volW}&tf=${state.tf}`;
      const r = await fetch(url, { signal: ctl.signal });
      const d = await r.json();
      if (my !== tgtSeq) return;                 // superseded by a newer load
      if (d.error) return tgtFail(d.error);
      if (!d.target) return tgtFail("Histórico insuficiente para calcular un objetivo.");
      renderTarget(d.target);
    } catch (e) {
      if (my === tgtSeq) tgtFail(e.name === "AbortError" ? "El cálculo tardó demasiado, reintentá." : "Error de red.");
    } finally {
      clearTimeout(timer);
    }
  }
  function tgtFail(msg) {
    tgtGrid.style.display = "none";
    tgtStatus.style.display = "flex";
    tgtStatus.className = "target-status err";
    tgtStatus.textContent = msg;
  }
  function renderTarget(t) {
    tgtStatus.style.display = "none";
    tgtGrid.style.display = "grid";
    tgtChart.innerHTML = svgCurve(t);
    renderSide(tgtBuy, t.buy, t.ccy, "tgt-buy", t.conf, t.vol);
    renderSide(tgtSell, t.sell, t.ccy, "tgt-sell", t.conf, t.vol);
  }
  function money(ccy, v) {
    return v == null ? "—" : ccy + Math.round(v).toLocaleString("es");
  }
  const mrow = (k, v) => `<div class="m-row"><span>${k}</span><b>${v}</b></div>`;
  const sPct = (p) => p == null ? ""
    : (Math.abs(p) < 0.1 ? "≈0 %"
      : (p >= 0 ? "+" : "−") + Math.abs(p).toFixed(1).replace(".", ",") + " %");
  function renderSide(el, s, ccy, cls, conf, vol) {
    const dir = s.u <= 50 ? "COMPRA → Capitulación" : "VENTA → Euforia";
    const pct = s.pct != null ? sPct(s.pct) + " vs hoy" : "";
    const m2 = s.m2.price == null ? "ninguna palanca sola llega"
      : `${money(ccy, s.m2.price)} · ${s.m2.lever}`;
    const m3 = s.m3 == null ? "sin muestra en zona"
      : `${money(ccy, s.m3.mid)} <span class="band">[${Math.round(s.m3.lo)}–${Math.round(s.m3.hi)}]</span><i class="n">n=${s.m3.n}</i>`;
    // Banda: el consenso es la ENTRADA; `band` es el borde típico hacia dentro.
    const confChip = conf
      ? `<span class="tgt-conf conf-${conf}">${conf}${vol != null ? ` · vol ${Math.round(vol)}%` : ""}</span>`
      : "";
    const bandRow = s.band == null ? ""
      : `<div class="tgt-band">${s.u <= 50 ? "fondo" : "techo"} típico `
        + `<b>${money(ccy, s.band)}</b> <span class="tgt-bpct">${sPct(s.band_pct)}</span></div>`;
    el.className = "tgt " + cls;
    el.innerHTML =
      `<div class="tgt-title">${dir} · score ${s.u}${confChip}</div>` +
      `<div class="tgt-cons">${money(ccy, s.consensus)}<span class="tgt-pct">${pct}</span></div>` +
      bandRow +
      `<div class="tgt-methods">${mrow("M1 exacta", money(ccy, s.m1))}${mrow("M2 palanca · diagnóstico", m2)}${mrow("M3 histórico", m3)}</div>`;
  }
  function svgCurve(t) {
    const VBW = 560, VBH = 210, pad = { l: 8, r: 8, t: 22, b: 16 };
    const c = t.curve || [];
    if (c.length < 2) return "";
    const xs = c.map((p) => p[0]);
    const xmin = Math.min(...xs), xmax = Math.max(...xs);
    const X = (p) => pad.l + (p - xmin) / (xmax - xmin || 1) * (VBW - pad.l - pad.r);
    const Y = (v) => (VBH - pad.b) - (v / 100) * (VBH - pad.b - pad.t);
    // Una sola familia de color por lado. Antes el punto del objetivo era azul /
    // naranja mientras su franja era verde / roja: dos colores para una misma
    // cosa, que se leía como si fueran dos anotaciones distintas.
    const BUY_C = "#2f9e68", SELL_C = "#c0454a";
    const bands = [[0, 20, "#dbe6f3"], [20, 40, "#e8eef6"], [40, 60, "#f0efec"], [60, 80, "#f4e9dd"], [80, 100, "#f1dcc7"]];
    let g = `<svg viewBox="0 0 ${VBW} ${VBH}" class="tsvg" font-family="sans-serif">`;
    for (const [lo, hi, col] of bands)
      g += `<rect x="0" y="${Y(hi).toFixed(1)}" width="${VBW}" height="${(Y(lo) - Y(hi)).toFixed(1)}" fill="${col}"/>`;
    // Banda de incertidumbre: franja de precio entre la entrada (consenso) y el
    // borde típico. Debajo de la curva; se recorta al rango visible del eje.
    const clampX = (x) => Math.max(pad.l, Math.min(VBW - pad.r, x));
    const shade = (a, b, col) => {
      if (a == null || b == null) return "";
      const x0 = clampX(X(a)), x1 = clampX(X(b)), w = Math.abs(x1 - x0);
      return w < 0.6 ? "" : `<rect x="${Math.min(x0, x1).toFixed(1)}" y="${pad.t}" `
        + `width="${w.toFixed(1)}" height="${(VBH - pad.b - pad.t).toFixed(1)}" fill="${col}"/>`;
    };
    g += shade(t.buy.band, t.buy.consensus, "rgba(70,201,138,.15)")
       + shade(t.sell.consensus, t.sell.band, "rgba(229,87,92,.15)");
    const pts = c.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    g += `<polyline points="${pts}" fill="none" stroke="#3a4a63" stroke-width="2.2" stroke-linejoin="round"/>`;
    // Score sobre la curva por interpolación. El consenso es la MEDIANA de M1 y
    // M3, así que NO cae en el borde de zona — ahí sólo cae M1, que es su raíz
    // exacta. Sin interpolar, el punto del consenso flotaría fuera de la línea.
    const scoreAt = (p) => {
      if (p <= c[0][0]) return c[0][1];
      const last = c[c.length - 1];
      if (p >= last[0]) return last[1];
      for (let i = 1; i < c.length; i++) {
        if (p <= c[i][0]) {
          const [x0, y0] = c[i - 1], [x1, y1] = c[i];
          return x1 === x0 ? y1 : y0 + (p - x0) / (x1 - x0) * (y1 - y0);
        }
      }
      return last[1];
    };
    // Ancho REAL del texto: estimarlo por nº de caracteres descuadraba los
    // rótulos y los sacaba del lienzo. El SVG hereda font-family="sans-serif".
    const measure = (txt, px, wt) => {
      const ctx = svgCurve._ctx
        || (svgCurve._ctx = document.createElement("canvas").getContext("2d"));
      ctx.font = `${wt} ${px}px sans-serif`;
      return ctx.measureText(txt).width;
    };
    // Línea firme = entrada (consenso). Difuminada = borde típico, no es un tope
    // duro. Mismo clamp que la sombra: si no, con la banda fuera de rango la
    // franja se dibujaba y su borde no, dejando una sombra sin cierre.
    const vline = (price, col, soft) => {
      if (price == null) return "";
      const x = clampX(X(price));
      return `<line x1="${x.toFixed(1)}" y1="${pad.t}" x2="${x.toFixed(1)}" y2="${(VBH - pad.b).toFixed(1)}" `
        + `stroke="${col}" stroke-width="1.2" `
        + (soft ? 'stroke-dasharray="3 3" opacity=".5"' : 'opacity=".6"') + "/>";
    };
    // Cota acotada: flechas de extremo a extremo de la franja + el rango en
    // cifras. La franja de compra suele medir <30 px, donde el rótulo no entra:
    // en ese caso sale al lado con una guía en vez de comerse el dibujo.
    const caliper = (band, cons, yy, col) => {
      if (band == null || cons == null) return "";
      const lo = Math.min(band, cons), hi = Math.max(band, cons);
      const x0 = clampX(X(lo)), x1 = clampX(X(hi)), w = x1 - x0;
      if (w < 0.6) return "";
      const txt = `${money(t.ccy, lo)} – ${money(t.ccy, hi)}`;
      const tt = measure(txt, 11.5, 700);
      let s = `<line x1="${x0.toFixed(1)}" y1="${yy}" x2="${x1.toFixed(1)}" y2="${yy}" stroke="${col}" stroke-width="1.2"/>`
        + `<path d="M${(x0 + 5.5).toFixed(1)} ${yy - 3.4}L${x0.toFixed(1)} ${yy}L${(x0 + 5.5).toFixed(1)} ${yy + 3.4}Z" fill="${col}"/>`
        + `<path d="M${(x1 - 5.5).toFixed(1)} ${yy - 3.4}L${x1.toFixed(1)} ${yy}L${(x1 - 5.5).toFixed(1)} ${yy + 3.4}Z" fill="${col}"/>`;
      let tx;
      if (w > tt + 16) {
        tx = (x0 + x1) / 2 - tt / 2;
      } else {
        tx = Math.min(VBW - 6 - tt, x1 + 9);
        s += `<line x1="${x1.toFixed(1)}" y1="${yy}" x2="${(tx - 5).toFixed(1)}" y2="${yy}" stroke="${col}" stroke-width="1" opacity=".5"/>`;
      }
      tx = Math.max(6, tx);
      return s + `<rect x="${(tx - 5).toFixed(1)}" y="${yy - 9}" width="${(tt + 10).toFixed(1)}" height="18" rx="4" fill="#f6f6f3" opacity=".93"/>`
        + `<text x="${tx.toFixed(1)}" y="${(yy + 4).toFixed(1)}" fill="${col}" font-size="11.5" font-weight="700">${txt}</text>`;
    };
    const hx = X(t.price), hy = Y(t.score);
    const hoyBox = [hx - 5, hy - 6, hx + 9 + measure("hoy", 12, 700), hy + 8];
    // PRINCIPAL: el consenso, que es el titular de la tarjeta. Antes el punto
    // gordo era M1, o sea el número que NO titula.
    const primary = (price, col, place) => {
      if (price == null) return "";
      const x = X(price), y = Y(scoreAt(price)), txt = money(t.ccy, price);
      const w = measure(txt, 13.5, 700);
      // En compra el punto cae abajo a la izquierda pegado a la curva: el único
      // hueco es arriba-izquierda, y hay que toparlo o se sale del lienzo.
      const up = place === "upleft";
      const tx = up ? Math.max(4 + w, x - 8)
        : Math.max(w / 2 + 4, Math.min(VBW - 4 - w / 2, x));
      let ty = Math.max(12, Math.min(VBH - 4, y + (up ? -20 : -10)));
      // "hoy" y el objetivo caen casi encima cuando el precio ya está pegado a
      // la zona (SPY cotiza a 775 con la venta en 779). Si los rótulos chocan,
      // el del objetivo sube; en la de compra ya va arriba, así que baja.
      const x0 = up ? tx - w : tx - w / 2;
      for (let i = 0; i < 3; i++) {
        const hit = x0 < hoyBox[2] && x0 + w > hoyBox[0]
          && ty - 11 < hoyBox[3] && ty + 3 > hoyBox[1];
        if (!hit) break;
        ty = Math.max(12, ty - 17);   // siempre hacia arriba: es el lado libre
      }
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.6" fill="${col}" stroke="#fff" stroke-width="1.6"/>`
        + `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" fill="${col}" font-size="13.5" font-weight="700" `
        + `text-anchor="${up ? "end" : "middle"}">${txt}</text>`;
    };
    // SECUNDARIO: M1 baja a anillo hueco sin cifra. El valor exacto sigue en la
    // tarjeta, así que no se pierde el dato y la zona de compra deja de
    // amontonar tres números en 40 px.
    const secondary = (price) => {
      if (price == null) return "";
      const x = X(price), y = Y(scoreAt(price));
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.1" fill="#f6f6f3" stroke="#7c8798" stroke-width="1.6"/>`;
    };
    g += vline(t.buy.band, BUY_C, true) + vline(t.buy.consensus, BUY_C, false)
       + vline(t.sell.consensus, SELL_C, false) + vline(t.sell.band, SELL_C, true);
    g += caliper(t.buy.band, t.buy.consensus, 40, BUY_C)
       + caliper(t.sell.band, t.sell.consensus, 174, SELL_C);
    g += secondary(t.buy.m1) + secondary(t.sell.m1);
    g += primary(t.buy.consensus, BUY_C, "upleft")
       + primary(t.sell.consensus, SELL_C, "up");
    g += `<circle cx="${hx.toFixed(1)}" cy="${hy.toFixed(1)}" r="5" fill="#26303f" stroke="#fff" stroke-width="2"/>` +
      `<text x="${(hx + 9).toFixed(1)}" y="${(hy + 4).toFixed(1)}" fill="#26303f" font-size="12" font-weight="700">hoy</text>`;
    return g + "</svg>";
  }

  // ── score composition ("cómo se arma el score") ─────
  const COMPONENTS = [
    { key: "stretch",    name: "Stretch",     wkey: "stretch" },
    { key: "rsi",        name: "RSI(14)",     wkey: "rsi" },
    { key: "drawdown",   name: "Drawdown",    wkey: "drawdown" },
    { key: "trend_dev",  name: "TrendDev",    wkey: "trend_dev" },
    { key: "volatility", name: "Volatilidad", wkey: "vol" },
  ];
  // Same weight rule as the engine, mirrored client-side so the panel updates
  // live with the volatility-weight slider.
  function weightsFor(model, w) {
    if (model === "reduced") return { stretch: 0.60, rsi: 0.40 };
    const s = 1 - w;
    return { stretch: 0.30 * s, rsi: 0.20 * s, drawdown: 0.25 * s, trend_dev: 0.25 * s, vol: w };
  }
  // Largest-remainder rounding so the displayed integer percents sum to 100.
  function roundPercents(fracs) {
    const raw = fracs.map((f) => f * 100);
    const out = raw.map(Math.floor);
    let rem = Math.round(100 - out.reduce((a, b) => a + b, 0));
    raw.map((v, i) => [v - out[i], i]).sort((a, b) => b[0] - a[0])
       .slice(0, Math.max(0, rem)).forEach(([, i]) => out[i]++);
    return out;
  }
  function renderComposition() {
    const el = document.getElementById("compo");
    const s = state.summary;
    if (!s || !state.model || state.model === "none") { el.innerHTML = ""; return; }
    const W = weightsFor(state.model, state.volW);
    const rows = COMPONENTS
      .filter((c) => W[c.wkey] != null && s[c.key] != null)
      .map((c) => ({ name: c.name, val: s[c.key], weight: W[c.wkey], contrib: W[c.wkey] * s[c.key] }));
    const total = rows.reduce((a, r) => a + r.contrib, 0);
    const maxc = Math.max(...rows.map((r) => r.contrib), 1e-9);
    const pcts = roundPercents(rows.map((r) => r.weight));
    const head =
      `<div class="compo-head"><span>Cómo se arma el score</span>` +
      `<span>bruto <b>${total.toFixed(1)}</b> · zona EMA(7) <b>${s.score != null ? s.score.toFixed(1) : "—"}</b></span></div>`;
    const body = rows.map((r, i) => {
      const col = colorForScore(r.val);
      return `<div class="compo-row">` +
        `<span class="compo-dot" style="background:${col}"></span>` +
        `<span class="compo-name">${r.name}</span>` +
        `<span class="compo-w">${pcts[i]}%</span>` +
        `<span class="compo-bar"><i style="width:${(r.contrib / maxc * 100).toFixed(1)}%;background:${col}"></i></span>` +
        `<span class="compo-val">valor ${r.val.toFixed(0)} → aporta <b>${r.contrib.toFixed(1)}</b></span>` +
        `</div>`;
    }).join("");
    el.innerHTML = head + body;
  }

  const convClass = (lab) =>
    lab === "Clímax confirmado" ? "chip-alta" : lab === "Parcial" ? "chip-media" : "chip-baja";

  function updateVerdict(s) {
    if (!s) return;
    const dot = document.getElementById("zone-dot");
    dot.style.background = s.score != null ? colorForScore(s.score) : "var(--muted)";
    document.getElementById("zone-name").textContent = s.zone || "—";
    document.getElementById("verdict").textContent = s.verdict || "—";
    const lab = convLabel(s.climax, s.zone);
    const chip = document.getElementById("conviction");
    if (lab) {
      chip.textContent = lab;
      chip.className = "chip " + convClass(lab);
      chip.hidden = false; chip.style.display = "inline-flex";
    } else {
      chip.hidden = true; chip.style.display = "none";
    }
  }

  function applyRange() {
    const f = state.full;
    if (!f.length) { state.view = []; draw(); return; }
    if (state.range === "all") { state.view = f; }
    else {
      const lastT = f[f.length - 1].t;
      const cut = lastT - Number(state.range) * 365.25 * 864e5;
      state.view = f.filter((p) => p.t >= cut);
    }
    draw();
  }

  // ── scales ──────────────────────────────────────────
  let SC = null;
  function scales(w, h, v) {
    const t0 = v[0].t, t1 = v[v.length - 1].t;
    const closes = v.map((p) => p.close).filter((x) => x != null && x > 0);
    const pMin = Math.min(...closes), pMax = Math.max(...closes);
    const lMin = Math.log10(pMin) - 0.02, lMax = Math.log10(pMax) + 0.02;
    const pb = h - M.B, pt = M.T;
    return {
      t0, t1, pMin, pMax, lMin, lMax, pb, pt,
      x: (t) => M.L + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * (w - M.L - M.R),
      ys: (s) => pb - (s / 100) * (pb - pt),
      yp: (p) => pb - (Math.log10(p) - lMin) / (lMax - lMin || 1) * (pb - pt),
    };
  }

  // ── render ──────────────────────────────────────────
  function sizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const w = host.clientWidth, h = host.clientHeight;
    for (const [c, cx] of [[cv, ctx], [buf, bctx]]) {
      c.width = w * dpr; c.height = h * dpr;
      c.style.width = w + "px"; c.style.height = h + "px";
      cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    return { w, h };
  }

  function draw() {
    const { w, h } = sizeCanvas();
    bctx.clearRect(0, 0, w, h);
    const v = state.view;
    if (v.length < 2) { blit(); return; }
    const s = scales(w, h, v);
    SC = { ...s, w, h };
    const g = bctx;

    // horizontal score gridlines + left labels
    g.font = "11px sans-serif"; g.textBaseline = "middle";
    for (const gv of GRID) {
      const y = s.ys(gv);
      g.strokeStyle = "rgba(255,255,255,0.06)"; g.lineWidth = 1;
      g.setLineDash(gv === 0 || gv === 100 ? [] : [3, 4]);
      g.beginPath(); g.moveTo(M.L, y); g.lineTo(w - M.R, y); g.stroke();
      g.setLineDash([]);
      g.fillStyle = "#6b7080"; g.textAlign = "right";
      g.fillText(String(gv), M.L - 6, y);
    }

    // right price axis labels (log)
    g.fillStyle = "#6b7080"; g.textAlign = "left";
    for (let i = 0; i <= 4; i++) {
      const lp = s.lMin + (i / 4) * (s.lMax - s.lMin);
      const p = Math.pow(10, lp);
      g.fillText(fmtPrice(p), w - M.R + 6, s.yp(p));
    }

    // x date labels
    g.textAlign = "center"; g.textBaseline = "top"; g.fillStyle = "#6b7080";
    const spanYears = (s.t1 - s.t0) / (365.25 * 864e5);
    for (let i = 0; i <= 5; i++) {
      const t = s.t0 + (i / 5) * (s.t1 - s.t0);
      const lab = spanYears > 6 ? fmtDate(t).slice(0, 4) : fmtDate(t).slice(0, 7);
      g.fillText(lab, s.x(t), h - M.B + 6);
    }

    // price line (dotted)
    g.strokeStyle = "rgba(215,218,226,0.75)"; g.lineWidth = 1; g.setLineDash([2, 3]);
    g.beginPath();
    let started = false;
    for (const p of v) {
      if (p.close == null || p.close <= 0) continue;
      const x = s.x(p.t), y = s.yp(p.close);
      started ? g.lineTo(x, y) : g.moveTo(x, y); started = true;
    }
    g.stroke(); g.setLineDash([]);

    // score line: colour quantized to the integer score so smooth stretches
    // become ONE path instead of thousands of 1-segment strokes (big win over
    // 20+ years of daily data).
    g.lineWidth = 1.8; g.lineJoin = "round"; g.lineCap = "round";
    let i = 1;
    while (i < v.length) {
      const key = Math.round(v[i - 1].score);
      g.strokeStyle = colorForScore(v[i - 1].score);
      g.beginPath();
      g.moveTo(s.x(v[i - 1].t), s.ys(v[i - 1].score));
      g.lineTo(s.x(v[i].t), s.ys(v[i].score));
      i++;
      while (i < v.length && Math.round(v[i - 1].score) === key) {
        g.lineTo(s.x(v[i].t), s.ys(v[i].score));
        i++;
      }
      g.stroke();
    }
    blit();
  }

  function blit() {
    const { width, height } = cv;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(buf, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // ── hover ───────────────────────────────────────────
  function nearest(mx) {
    const v = state.view; if (!v.length || !SC) return -1;
    let lo = 0, hi = v.length - 1;
    const tx = SC.t0 + (mx - M.L) / (SC.w - M.L - M.R) * (SC.t1 - SC.t0);
    while (lo < hi) { const m = (lo + hi) >> 1; (v[m].t < tx) ? lo = m + 1 : hi = m; }
    if (lo > 0 && Math.abs(v[lo - 1].t - tx) < Math.abs(v[lo].t - tx)) lo--;
    return lo;
  }

  cv.addEventListener("mousemove", (e) => {
    if (!SC) return;
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const i = nearest(mx);
    if (i < 0) return;
    const p = state.view[i];
    blit();
    const x = SC.x(p.t);
    ctx.strokeStyle = "rgba(255,255,255,0.25)"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, SC.pt); ctx.lineTo(x, SC.pb); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = colorForScore(p.score);
    ctx.beginPath(); ctx.arc(x, SC.ys(p.score), 3.5, 0, 7); ctx.fill();

    const comp = (k) => (p[k] == null ? "—" : p[k].toFixed(0));
    const conv = convLabel(p.climax, p.zone);
    tip.innerHTML =
      `<div class="t-date">${fmtDate(p.t)}</div>` +
      `<div class="t-row"><span>Precio</span><b>${p.close == null ? "—" : fmtPrice(p.close)}</b></div>` +
      `<div class="t-row"><span>Score</span><b style="color:${colorForScore(p.score)}">${p.score.toFixed(1)} · ${p.zone || ""}</b></div>` +
      `<div class="t-sep"></div>` +
      `<div class="t-row"><span>Stretch</span><b>${comp("stretch")}</b></div>` +
      `<div class="t-row"><span>RSI(14)</span><b>${comp("rsi")}</b></div>` +
      `<div class="t-row"><span>Drawdown</span><b>${comp("drawdown")}</b></div>` +
      `<div class="t-row"><span>TrendDev</span><b>${comp("trend_dev")}</b></div>` +
      `<div class="t-row"><span>Volatilidad</span><b>${comp("volatility")}</b></div>` +
      (conv ? `<div class="t-sep"></div><div class="t-row"><span>Convicción</span><b>${conv}</b></div>` : "");
    tip.hidden = false;
    const tw = tip.offsetWidth, hostW = host.clientWidth;
    tip.style.left = Math.min(mx + 14, hostW - tw - 6) + "px";
    tip.style.top = (SC.pt + 6) + "px";
  });
  cv.addEventListener("mouseleave", () => { tip.hidden = true; blit(); });

  function setStatus(msg) {
    // Toggle inline display directly: an inline style beats any stylesheet
    // rule, so `.status { display:flex }` can never keep the pill visible.
    // (The `hidden` attribute alone loses to the class selector — that was the
    // bug that kept "Cargando…" stuck over an already-rendered chart.)
    if (msg == null) { statusEl.hidden = true; statusEl.style.display = "none"; return; }
    statusEl.textContent = msg;                 // spinner is a ::before, unaffected
    statusEl.hidden = false; statusEl.style.display = "flex";
  }

  // ── controls ────────────────────────────────────────
  document.getElementById("ranges").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    document.querySelectorAll("#ranges button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.range = b.dataset.range; applyRange();
  });

  // Timeframe toggle: unlike the range filter this changes the SCORE, so it
  // refetches (weekly bars are scored server-side with weekly-horizon windows).
  document.getElementById("tf").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || STATIC) return;
    if (b.dataset.tf === state.tf) return;             // already on this timeframe
    document.querySelectorAll("#tf button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.tf = b.dataset.tf;
    load(curSymbol());
  });

  if (STATIC) {
    ["asset", "ticker", "load"].forEach((id) => { const el = document.getElementById(id); if (el) el.disabled = true; });
    document.querySelectorAll("#tf button").forEach((x) => { x.disabled = true; });
  } else {
    const go = () => {
      const t = document.getElementById("ticker").value.trim();
      load(t || document.getElementById("asset").value);
    };
    document.getElementById("load").addEventListener("click", go);
    document.getElementById("asset").addEventListener("change", () => {
      document.getElementById("ticker").value = ""; go();
    });
    document.getElementById("ticker").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }

  // ── tuner (live tuning) ─────────────────────────────
  const vwEl = document.getElementById("vw"), hiEl = document.getElementById("hi"), midEl = document.getElementById("mid");
  const vwOut = document.getElementById("vw-out"), hiOut = document.getElementById("hi-out"), midOut = document.getElementById("mid-out");
  let vwTimer = null;
  const curSymbol = () => state.symbol || document.getElementById("asset").value;
  function syncTuner() {
    vwOut.textContent = Number(vwEl.value).toFixed(2);
    hiOut.textContent = hiEl.value; midOut.textContent = midEl.value;
  }
  // Thresholds: pure re-label, instant (chip now, tooltip on next hover).
  hiEl.addEventListener("input", () => { state.hi = +hiEl.value; hiOut.textContent = hiEl.value; updateVerdict(state.summary); });
  midEl.addEventListener("input", () => { state.mid = +midEl.value; midOut.textContent = midEl.value; updateVerdict(state.summary); });
  // Volatility weight: changes the score -> debounced server recompute.
  vwEl.addEventListener("input", () => {
    state.volW = +vwEl.value; vwOut.textContent = Number(vwEl.value).toFixed(2);
    renderComposition();                       // instant weight/contribution update
    if (STATIC) return;                        // score is baked in offline snapshots
    clearTimeout(vwTimer);
    vwTimer = setTimeout(() => load(curSymbol()), 300);
  });
  document.getElementById("tuner-reset").addEventListener("click", () => {
    const changedW = state.volW !== 0.10;
    vwEl.value = "0.10"; hiEl.value = "70"; midEl.value = "45";
    state.volW = 0.10; state.hi = 70; state.mid = 45;
    syncTuner(); updateVerdict(state.summary); renderComposition();
    if (!STATIC && changedW) load(curSymbol());
  });
  if (STATIC) vwEl.disabled = true;            // thresholds stay live; weight cannot

  new ResizeObserver(() => draw()).observe(host);

  // ── boot ────────────────────────────────────────────
  if (STATIC) apply(window.__ZONES__);
  else load(document.getElementById("asset").value);
})();
