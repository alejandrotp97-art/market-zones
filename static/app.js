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

  const M = { L: 46, R: 54, T: 10, B: 26 };
  const GRID = [0, 20, 40, 60, 80, 100];

  const state = { full: [], view: [], range: "all", asof: "—",
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
      const url = `/api/zones?symbol=${encodeURIComponent(symbol)}&vol_w=${state.volW}`;
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

  function apply(d) {
    setStatus(null);
    state.full = d.series || [];
    state.asof = d.as_of || "—";
    state.summary = d.summary || null;
    state.symbol = d.symbol || state.symbol;
    state.model = d.model || state.model;
    document.getElementById("asof").textContent = state.asof;
    updateVerdict(d.summary);
    renderComposition();
    applyRange();
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

  if (STATIC) {
    ["asset", "ticker", "load"].forEach((id) => { const el = document.getElementById(id); if (el) el.disabled = true; });
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
