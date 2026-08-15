/* Panel de Régimen — dependency-free Canvas. */
(() => {
  "use strict";
  const REGIME_COLORS = {
    "Pánico": "#8e1b13", "Capitulación": "#cf5b3a", "Recuperación": "#4a9e8f",
    "Alcista sano": "#3fae6b", "Sobrecalentamiento": "#d99a2b", "Clímax": "#b3261e",
    "Distribución": "#b07a3a", "Corrección": "#c96a5e", "Lateral": "#7c828e",
  };
  const HORIZONS = ["1s", "1m", "3m", "6m", "12m"];
  const AXES = [["level", "Nivel"], ["vol", "Volatilidad"], ["cycle", "Ciclo"], ["instability", "Inestab."]];
  const colorForScore = (s) => `hsl(${140 * (1 - Math.max(0, Math.min(100, s)) / 100)},68%,55%)`;
  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim() || v;
  const fmtP = (p) => (p >= 1000 ? p.toLocaleString("es", { maximumFractionDigits: 0 }) : p.toLocaleString("es", { maximumFractionDigits: 2 }));
  const fmtPct = (x) => (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
  const fmtDate = (t) => new Date(t).toISOString().slice(0, 10);

  const state = { data: null, symbol: null };
  let reqSeq = 0;

  const el = (id) => document.getElementById(id);
  function setStatus(m) {
    const s = el("status");
    if (m == null) { s.hidden = true; s.style.display = "none"; return; }
    s.textContent = m; s.hidden = false; s.style.display = "flex";
  }
  function size(c) {
    const dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const ctx = c.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  // ── data ────────────────────────────────────────────
  async function load(symbol) {
    symbol = (symbol || "").trim(); if (!symbol) return;
    const my = ++reqSeq; setStatus(`Cargando ${symbol}…`);
    try {
      const r = await fetch(`/api/regime?symbol=${encodeURIComponent(symbol)}`);
      const d = await r.json();
      if (my !== reqSeq) return;
      if (d.error) { setStatus(d.error); return; }
      setStatus(null); state.data = d; state.symbol = d.symbol;
      el("asof").textContent = d.as_of; renderAll();
    } catch (e) { if (my === reqSeq) setStatus("Error: " + e.message); }
  }

  // ── header ──────────────────────────────────────────
  function renderHeader() {
    const s = state.data.summary;
    el("score-num").textContent = s.score != null ? s.score.toFixed(1) : "—";
    el("score-num").style.color = s.score != null ? colorForScore(s.score) : "var(--text)";
    el("gauge-mark").style.left = (s.score || 0) + "%";
    const col = REGIME_COLORS[s.regime] || "var(--muted)";
    el("regime-dot").style.background = col; el("regime-name").textContent = s.regime || "—";
    el("regime-name").style.color = col;
    el("regime-meta").textContent = `${s.trend_up ? "↑ tendencia alcista" : "↓ tendencia bajista/plana"} · hace ${s.dwell} días`;
    // axes bars
    el("axes").innerHTML = AXES.map(([k, name]) => {
      const v = s[k]; const w = v == null ? 0 : v;
      return `<div class="axis-row"><span class="an">${name}</span>` +
        `<span class="axis-bar"><i style="width:${w}%"></i></span>` +
        `<span class="av">${v == null ? "—" : v.toFixed(0)}</span></div>`;
    }).join("");
    // confidence: from n_analogs (log-scaled) + flag
    const n = s.n_analogs || 0;
    const pct = Math.max(0, Math.min(100, Math.round(100 * Math.log10(Math.max(1, n)) / Math.log10(1500))));
    el("conf-num").textContent = s.low_confidence ? "Baja" : (pct >= 70 ? "Alta" : "Media");
    el("conf-num").style.color = s.low_confidence ? "#cf5b3a" : (pct >= 70 ? "#3fae6b" : "#d99a2b");
    el("conf-meta").textContent = `${n} análogos históricos`;
  }

  // ── main chart (price log + score, offscreen buffer for hover) ──
  const buf = document.createElement("canvas"); let SC = null;
  const M = { L: 40, R: 50, T: 8, B: 20 };
  function drawChart() {
    const c = el("chart"), { ctx, w, h } = size(c);
    const v = state.data.series; if (v.length < 2) return;
    buf.width = c.width; buf.height = c.height;
    const g = buf.getContext("2d"); g.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
    g.clearRect(0, 0, w, h);
    const t0 = v[0].t, t1 = v[v.length - 1].t;
    const px = v.map(p => p.close).filter(x => x > 0);
    const lMin = Math.log10(Math.min(...px)) - .02, lMax = Math.log10(Math.max(...px)) + .02;
    const pb = h - M.B, pt = M.T;
    const X = t => M.L + (t - t0) / (t1 - t0) * (w - M.L - M.R);
    const Ys = s => pb - s / 100 * (pb - pt);
    const Yp = p => pb - (Math.log10(p) - lMin) / (lMax - lMin) * (pb - pt);
    SC = { X, Ys, Yp, t0, t1, w, pb, pt, lMin, lMax };
    // grid
    g.font = "11px sans-serif"; g.textBaseline = "middle"; g.fillStyle = css("--faint");
    for (const s of [0, 20, 40, 60, 80, 100]) {
      const y = Ys(s); g.strokeStyle = "rgba(140,140,150,.10)"; g.setLineDash(s % 100 ? [3, 4] : []);
      g.beginPath(); g.moveTo(M.L, y); g.lineTo(w - M.R, y); g.stroke(); g.setLineDash([]);
      g.textAlign = "right"; g.fillText(s, M.L - 5, y);
    }
    g.textAlign = "left";
    for (let i = 0; i <= 4; i++) { const lp = lMin + i / 4 * (lMax - lMin); g.fillText(fmtP(10 ** lp), w - M.R + 5, Yp(10 ** lp)); }
    g.textAlign = "center"; g.textBaseline = "top";
    for (let i = 0; i <= 5; i++) { const t = t0 + i / 5 * (t1 - t0); g.fillText(fmtDate(t).slice(0, 4), X(t), h - M.B + 4); }
    // price dotted
    g.strokeStyle = css("--muted"); g.globalAlpha = .55; g.setLineDash([2, 3]); g.beginPath();
    v.forEach((p, i) => { const x = X(p.t), y = Yp(p.close); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
    // score line coloured
    g.lineWidth = 1.7; g.lineJoin = "round";
    let i = 1; while (i < v.length) { const key = Math.round(v[i - 1].score); g.strokeStyle = colorForScore(v[i - 1].score);
      g.beginPath(); g.moveTo(X(v[i - 1].t), Ys(v[i - 1].score)); g.lineTo(X(v[i].t), Ys(v[i].score)); i++;
      while (i < v.length && Math.round(v[i - 1].score) === key) { g.lineTo(X(v[i].t), Ys(v[i].score)); i++; } g.stroke(); }
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, c.width, c.height); ctx.drawImage(buf, 0, 0);
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
  }
  function blit() { const c = el("chart"), ctx = c.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, c.width, c.height); ctx.drawImage(buf, 0, 0);
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0); }
  el("chart").addEventListener("mousemove", e => {
    if (!SC) return; const v = state.data.series; const rect = e.target.getBoundingClientRect(); const mx = e.clientX - rect.left;
    const tx = SC.t0 + (mx - M.L) / (SC.w - M.L - M.R) * (SC.t1 - SC.t0);
    let lo = 0, hi = v.length - 1; while (lo < hi) { const m = (lo + hi) >> 1; v[m].t < tx ? lo = m + 1 : hi = m; }
    const p = v[lo]; blit(); const ctx = el("chart").getContext("2d"); const x = SC.X(p.t);
    ctx.strokeStyle = "rgba(150,150,160,.35)"; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(x, SC.pt); ctx.lineTo(x, SC.pb); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = colorForScore(p.score); ctx.beginPath(); ctx.arc(x, SC.Ys(p.score), 3.5, 0, 7); ctx.fill();
    const tip = el("tip");
    tip.innerHTML = `<div class="td">${fmtDate(p.t)}</div>` +
      `<div class="tr"><span>Precio</span><b>${fmtP(p.close)}</b></div>` +
      `<div class="tr"><span>Score</span><b style="color:${colorForScore(p.score)}">${p.score.toFixed(1)}</b></div>` +
      `<div class="tr"><span>Régimen</span><b style="color:${REGIME_COLORS[p.regime] || ""}">${p.regime || ""}</b></div>`;
    tip.hidden = false; tip.style.left = Math.min(mx + 12, el("chart").clientWidth - tip.offsetWidth - 6) + "px"; tip.style.top = SC.pt + 4 + "px";
  });
  el("chart").addEventListener("mouseleave", () => { el("tip").hidden = true; blit(); });

  // ── regime ribbon ───────────────────────────────────
  function drawRibbon() {
    const c = el("ribbon"), { ctx, w, h } = size(c); const v = state.data.series; if (v.length < 2) return;
    const t0 = v[0].t, t1 = v[v.length - 1].t; const X = t => (t - t0) / (t1 - t0) * w;
    ctx.clearRect(0, 0, w, h);
    for (let i = 1; i < v.length; i++) {
      ctx.fillStyle = REGIME_COLORS[v[i].regime] || "#444";
      ctx.fillRect(X(v[i - 1].t), 0, X(v[i].t) - X(v[i - 1].t) + 1, h);
    }
    // legend: distinct regimes present
    const present = [...new Set(v.map(p => p.regime))].filter(Boolean);
    el("ribbon-legend").innerHTML = present.map(r =>
      `<span style="color:${REGIME_COLORS[r]}">■</span> ${r}`).join("&nbsp;&nbsp;");
  }

  // ── phase map (level × vol) ─────────────────────────
  function drawPhase() {
    const c = el("phase"), { ctx, w, h } = size(c); const ph = state.data.phase; if (!ph.length) return;
    const m = 30, pb = h - m, pl = m + 6, pw = w - pl - 10, phh = pb - 10;
    ctx.clearRect(0, 0, w, h);
    const X = lv => pl + lv / 100 * pw, Y = vo => 10 + (100 - vo) / 100 * phh;  // vol high = top
    ctx.strokeStyle = "rgba(140,140,150,.12)"; ctx.fillStyle = css("--faint"); ctx.font = "10px " + "monospace";
    for (const g of [33, 67]) { ctx.beginPath(); ctx.moveTo(X(g), 10); ctx.lineTo(X(g), pb); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(pl, Y(g)); ctx.lineTo(pl + pw, Y(g)); ctx.stroke(); }
    ctx.textAlign = "center"; ctx.fillText("barato", X(16), pb + 14); ctx.fillText("caro", X(84), pb + 14);
    ctx.save(); ctx.translate(pl - 16, (10 + pb) / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText("← calma   estrés →", 0, 0); ctx.restore();
    // trail
    ph.forEach((p, i) => { const a = 0.15 + 0.85 * i / ph.length;
      ctx.fillStyle = `rgba(120,140,180,${a * .5})`; ctx.beginPath(); ctx.arc(X(p.level), Y(p.vol), 2.2, 0, 7); ctx.fill(); });
    const last = ph[ph.length - 1]; const col = REGIME_COLORS[state.data.summary.regime] || "#4a90d9";
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(X(last.level), Y(last.vol), 6, 0, 7); ctx.fill();
    ctx.strokeStyle = css("--bg"); ctx.lineWidth = 2; ctx.stroke();
  }

  // ── fan chart (scenarios) ───────────────────────────
  function drawFan() {
    const c = el("fan"), { ctx, w, h } = size(c); const sc = state.data.scenarios;
    const hz = HORIZONS.filter(k => sc[k]); ctx.clearRect(0, 0, w, h);
    if (!hz.length) { ctx.fillStyle = css("--faint"); ctx.fillText("sin datos", 20, 20); return; }
    let lo = 0, hi = 0; hz.forEach(k => { lo = Math.min(lo, sc[k].p10); hi = Math.max(hi, sc[k].p90); });
    lo -= .01; hi += .01; const m = 44, pb = h - 22, pt = 12;
    const X = i => m + i / (hz.length - 1 || 1) * (w - m - 16);
    const Y = r => pb - (r - lo) / (hi - lo) * (pb - pt);
    // y grid at nice %s
    ctx.font = "11px monospace"; ctx.fillStyle = css("--faint"); ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const step = (hi - lo) > .4 ? .1 : .05;
    for (let r = Math.ceil(lo / step) * step; r < hi; r += step) { const y = Y(r);
      ctx.strokeStyle = Math.abs(r) < 1e-9 ? "rgba(150,150,160,.5)" : "rgba(140,140,150,.12)";
      ctx.beginPath(); ctx.moveTo(m, y); ctx.lineTo(w - 16, y); ctx.stroke();
      ctx.fillStyle = css("--faint"); ctx.fillText((r * 100).toFixed(0) + "%", m - 5, y); }
    // bands
    ctx.fillStyle = "rgba(74,144,217,.14)"; ctx.beginPath();
    hz.forEach((k, i) => { const x = X(i), y = Y(sc[k].p90); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    for (let i = hz.length - 1; i >= 0; i--) ctx.lineTo(X(i), Y(sc[hz[i]].p10)); ctx.closePath(); ctx.fill();
    // CI box + median line
    ctx.strokeStyle = "#4a90d9"; ctx.lineWidth = 2; ctx.beginPath();
    hz.forEach((k, i) => { const x = X(i), y = Y(sc[k].median); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
    hz.forEach((k, i) => { const x = X(i);
      ctx.strokeStyle = "#4a90d9"; ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(x, Y(sc[k].ci_lo)); ctx.lineTo(x, Y(sc[k].ci_hi)); ctx.stroke();
      ctx.fillStyle = css("--text"); ctx.beginPath(); ctx.arc(x, Y(sc[k].median), 3, 0, 7); ctx.fill();
      ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = css("--muted");
      ctx.fillText(k, x, pb + 5); ctx.fillStyle = css("--text");
      ctx.fillText(fmtPct(sc[k].median), x, pt - 2 < Y(sc[k].median) - 14 ? Y(sc[k].median) - 14 : pt); });
    const anyN = sc[hz[0]].n; el("fan-n").textContent = `mediana · banda p10–p90 · IC95 · n=${anyN}`;
  }

  function renderAll() { renderHeader(); drawChart(); drawRibbon(); drawPhase(); drawFan(); }

  // ── controls + resize ───────────────────────────────
  const go = () => { const t = el("ticker").value.trim(); load(t || el("asset").value); };
  el("load").addEventListener("click", go);
  el("asset").addEventListener("change", () => { el("ticker").value = ""; go(); });
  el("ticker").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  let rt; new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => state.data && renderAll(), 120); })
    .observe(document.querySelector(".wrap"));

  load(el("asset").value);
})();
