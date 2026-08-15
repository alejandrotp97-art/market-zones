/* Panel de Régimen — dependency-free Canvas. */
(() => {
  "use strict";
  // Values below originate outside this file (the curated list, the API
  // payload). Escape at the point of insertion: the discipline is what
  // protects the NEXT value someone interpolates here, not just today's.
  const esc = (s) => String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const REGIME_COLORS = {
    "Pánico": "#8e1b13", "Capitulación": "#cf5b3a", "Recuperación": "#4a9e8f",
    "Alcista sano": "#3fae6b", "Sobrecalentamiento": "#d99a2b", "Clímax": "#b3261e",
    "Distribución": "#b07a3a", "Corrección": "#c96a5e", "Lateral": "#7c828e",
  };
  const HORIZONS = ["1s", "1m", "3m", "6m", "12m"];
  const AXES = [["vol", "Volatilidad"], ["cycle", "Ciclo"], ["instability", "Inestab."]];  // "Nivel" = Score, eliminado (P0-6)
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
    el("regime-meta").innerHTML =
      `${s.trend_up ? "↑ tendencia alcista" : "↓ tendencia bajista/plana"} · hace ${s.dwell} días` +
      `<div class="reg-pct">${s.regime_pct != null ? s.regime_pct.toFixed(1) : "—"}% del histórico en este régimen</div>`;
    // axes bars
    el("axes").innerHTML = AXES.map(([k, name]) => {
      const v = s[k]; const w = v == null ? 0 : v;
      return `<div class="axis-row"><span class="an">${name}</span>` +
        `<span class="axis-bar"><i style="width:${w}%"></i></span>` +
        `<span class="av">${v == null ? "—" : v.toFixed(0)}</span></div>`;
    }).join("");
    // confidence: effective N + CI width + distinguishability (backend), never raw N/log(N)
    const conf = s.confidence || "—";
    const cc = { Alta: "#3fae6b", Media: "#d99a2b", Baja: "#cf5b3a" }[conf] || "var(--muted)";
    el("conf-num").textContent = conf; el("conf-num").style.color = cc;
    el("conf-meta").innerHTML = (s.conf_drivers || []).map((d) => `<div>${esc(d)}</div>`).join("");
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
      `<div class="tr"><span>Régimen</span><b style="color:${REGIME_COLORS[p.regime] || ""}">${esc(p.regime)}</b></div>`;
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
    // legend con % del histórico (distribución temporal cuantificada, P0-4)
    const dist = state.data.regime_dist || {};
    const present = [...new Set(v.map(p => p.regime))].filter(Boolean)
      .sort((a, b) => (dist[b] || 0) - (dist[a] || 0));
    el("ribbon-legend").innerHTML = present.map(r =>
      `<span style="color:${REGIME_COLORS[r]}">■</span> ${r} ${dist[r] != null ? dist[r].toFixed(0) + "%" : ""}`).join("&nbsp;&nbsp;");
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

  // ── fan chart: EXCESO sobre el baseline del mercado (P0-1) ──
  function drawFan() {
    const c = el("fan"), { ctx, w, h } = size(c); const sc = state.data.scenarios;
    const hz = HORIZONS.filter(k => sc[k] && sc[k].excess != null); ctx.clearRect(0, 0, w, h);
    if (!hz.length) { ctx.fillStyle = css("--faint"); ctx.fillText("sin métricas suficientes (N / IC)", 20, 20); return; }
    const ex = k => sc[k].excess, exlo = k => sc[k].p10 - sc[k].baseline, exhi = k => sc[k].p90 - sc[k].baseline,
          cilo = k => sc[k].ci_lo - sc[k].baseline, cihi = k => sc[k].ci_hi - sc[k].baseline;
    let lo = 0, hi = 0; hz.forEach(k => { lo = Math.min(lo, exlo(k), cilo(k)); hi = Math.max(hi, exhi(k), cihi(k)); });
    lo -= .005; hi += .005; const m = 42, pb = h - 20, pt = 14;
    const X = i => m + i / (hz.length - 1 || 1) * (w - m - 16);
    const Y = r => pb - (r - lo) / (hi - lo) * (pb - pt);
    ctx.font = "11px monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const step = (hi - lo) > .2 ? .05 : .02;
    for (let r = Math.ceil(lo / step) * step; r < hi; r += step) { if (Math.abs(r) < 1e-9) continue;
      const y = Y(r); ctx.strokeStyle = "rgba(140,140,150,.12)"; ctx.beginPath(); ctx.moveTo(m, y); ctx.lineTo(w - 16, y); ctx.stroke();
      ctx.fillStyle = css("--faint"); ctx.fillText((r >= 0 ? "+" : "") + (r * 100).toFixed(0) + "%", m - 5, y); }
    // baseline = línea 0 destacada (P0-1)
    const y0 = Y(0); ctx.strokeStyle = css("--muted"); ctx.lineWidth = 1.5; ctx.setLineDash([5, 3]);
    ctx.beginPath(); ctx.moveTo(m, y0); ctx.lineTo(w - 16, y0); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = css("--faint"); ctx.textAlign = "left"; ctx.fillText("baseline del mercado (0)", m + 4, y0 - 8);
    // banda de exceso p10–p90
    ctx.fillStyle = "rgba(74,144,217,.13)"; ctx.beginPath();
    hz.forEach((k, i) => { const x = X(i), y = Y(exhi(k)); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    for (let i = hz.length - 1; i >= 0; i--) ctx.lineTo(X(i), Y(exlo(hz[i]))); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = "#6b91c9"; ctx.lineWidth = 1.5; ctx.beginPath();
    hz.forEach((k, i) => { const x = X(i), y = Y(ex(k)); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
    // IC + punto (verde = exceso positivo, rojo = negativo)
    hz.forEach((k, i) => { const x = X(i); const col = ex(k) >= 0 ? "#3fae6b" : "#cf5b3a";
      ctx.strokeStyle = "#4a90d9"; ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(x, Y(cilo(k))); ctx.lineTo(x, Y(cihi(k))); ctx.stroke();
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, Y(ex(k)), 4, 0, 7); ctx.fill();
      ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = css("--muted"); ctx.fillText(k, x, pb + 4);
      ctx.fillStyle = col; ctx.fillText(fmtPct(ex(k)), x, Math.max(pt, Y(ex(k)) - 15)); });
    el("fan-n").textContent = "exceso vs. baseline · IC95 · verde = aporta, rojo = peor que el mercado";
  }

  // tabla obligatoria: ningún pronóstico sin exceso + IC + N efectivo (P0-3)
  function renderFanTable() {
    const sc = state.data.scenarios;
    const hz = HORIZONS.filter(k => sc[k] && sc[k].excess != null);
    el("fan-table").innerHTML =
      `<table class="ft"><thead><tr><th>Horiz.</th><th>Esperado</th><th>Baseline</th><th>Exceso</th><th>IC95 exceso</th><th>N&nbsp;ef.</th><th>Ev.</th></tr></thead><tbody>` +
      hz.map(k => { const s = sc[k]; const e = s.excess, lo = s.ci_lo - s.baseline, hi = s.ci_hi - s.baseline;
        const col = e >= 0 ? "var(--pos)" : "var(--neg)"; const E = EVID[s.evidence] || { c: "var(--muted)", t: "—" };
        return `<tr><td>${k}</td><td>${fmtPct(s.median)}</td><td class="mut">${fmtPct(s.baseline)}</td>` +
          `<td style="color:${col};font-weight:700">${fmtPct(e)}</td>` +
          `<td class="mut">[${fmtPct(lo)}, ${fmtPct(hi)}]</td><td>${s.n_eff}</td>` +
          `<td><span class="ev-dot" style="background:${E.c}" title="${E.t}"></span></td></tr>`;
      }).join("") + `</tbody></table>`;
  }

  // ── composición del score (pesos equiponderados) ───
  function renderComposition() {
    const s = state.data.summary;
    const W = 1 / 3;   // 3 entradas equiponderadas; inestabilidad/tendencia = FSM
    const rows = [
      { name: "Extensión (mayer)", val: s.extension },
      { name: "Volatilidad (inv.)", val: s.vol == null ? null : 100 - s.vol },
      { name: "Ciclo (drawdown)", val: s.cycle },
    ].filter((r) => r.val != null);
    const contribs = rows.map((r) => r.val * W);
    const total = contribs.reduce((a, b) => a + b, 0);
    const maxc = Math.max(...contribs, 1e-9);
    el("compo-total").textContent = `score ${total.toFixed(1)} = media de ${rows.length} · peso ${(W * 100).toFixed(1)}% c/u`;
    el("compo").innerHTML = rows.map((r) => {
      const c = r.val * W, col = colorForScore(r.val);
      return `<div class="compo-row"><span class="compo-dot" style="background:${col}"></span>` +
        `<span class="compo-name">${esc(r.name)}</span><span class="compo-w">33.3%</span>` +
        `<span class="compo-bar"><i style="width:${(c / maxc * 100).toFixed(1)}%;background:${col}"></i></span>` +
        `<span class="compo-val">valor ${r.val.toFixed(0)} → aporta <b>${c.toFixed(1)}</b></span></div>`;
    }).join("");
  }

  // ── P1: evidencia / veredicto / consistencia / transición / calibración ──
  const REF = "6m";
  const EVID = {
    pos:  { c: "#3fae6b", t: "Evidencia positiva",     dot: "🟢" },
    flat: { c: "#d99a2b", t: "Indistinguible de cero",  dot: "🟡" },
    neg:  { c: "#cf5b3a", t: "Evidencia negativa",      dot: "🔴" },
  };

  function renderHeadline() {
    const sc = state.data.scenarios, sm = state.data.summary;
    const r = sc[REF] || sc[Object.keys(sc)[0]];
    const ev = r ? r.evidence : null; const E = EVID[ev] || { c: "var(--muted)", t: "—", dot: "⚪" };
    // P2-1: grade / oportunidad / robustez
    const gc = { "A+": "#3fae6b", "A": "#3fae6b", "B": "#8bbf3f", "C": "#d99a2b", "D": "#cf5b3a" }[sm.grade] || "var(--muted)";
    el("grade").textContent = sm.grade || "—"; el("grade").style.color = gc; el("grade").style.borderColor = gc;
    el("opp-num").textContent = sm.opportunity != null ? sm.opportunity : "—";
    el("rob-level").textContent = sm.rob_level || "—";
    const badge = el("verdict-badge");
    badge.innerHTML = `<span class="ev-dot" style="background:${E.c};margin-right:8px;vertical-align:middle"></span>${E.t}`;
    badge.style.color = E.c; badge.style.borderColor = E.c;
    let txt = `Históricamente este régimen ha aparecido el <b>${sm.regime_pct != null ? sm.regime_pct.toFixed(1) : "—"}%</b> del tiempo. `;
    if (r && r.excess != null) {
      const lo = r.ci_lo - r.baseline, hi = r.ci_hi - r.baseline;
      txt += `A <b>${REF}</b> el exceso esperado sobre el mercado es <b style="color:${r.excess >= 0 ? "var(--pos)" : "var(--neg)"}">${fmtPct(r.excess)}</b> ` +
        `(IC95 [${fmtPct(lo)}, ${fmtPct(hi)}], N efectivo ${r.n_eff}). ` +
        (ev === "flat"
          ? `El IC95 <b>incluye el cero</b>: no hay evidencia estadística de que este régimen aporte ventaja frente a permanecer invertido en el índice.`
          : ev === "pos"
            ? `El IC95 del exceso está <b>por encima de cero</b>: hay evidencia de que aporta rendimiento sobre el mercado.`
            : `El IC95 del exceso está <b>por debajo de cero</b>: hay evidencia de que rinde por debajo del mercado.`);
    }
    el("verdict-text").innerHTML = txt;
    renderConsistency();
  }

  function renderConsistency() {
    const sc = state.data.scenarios; const hz = HORIZONS.filter(k => sc[k] && sc[k].excess != null);
    if (hz.length < 2) { el("v-consistency").innerHTML = ""; return; }
    const exs = hz.map(k => sc[k].excess);
    const first = exs[0], last = exs[exs.length - 1];
    const trend = last > first + 0.005 ? "↗ mejora con el horizonte" : last < first - 0.005 ? "↘ empeora con el horizonte" : "→ estable entre horizontes";
    const inv = new Set(exs.map(e => e >= 0)).size > 1;
    const mn = Math.min(...exs, 0), mx = Math.max(...exs, 0), W = 82, H = 22;
    const pts = exs.map((e, i) => `${(i / (exs.length - 1) * W).toFixed(1)},${(H - (e - mn) / (mx - mn || 1) * H).toFixed(1)}`).join(" ");
    const y0 = (H - (0 - mn) / (mx - mn || 1) * H).toFixed(1);
    el("v-consistency").innerHTML =
      `<span class="cons-lbl">Exceso × horizonte</span>` +
      `<svg width="${W}" height="${H}" class="spark"><line x1="0" y1="${y0}" x2="${W}" y2="${y0}" class="spark0"/><polyline points="${pts}" class="sparkl"/></svg>` +
      `<span class="cons-i">${trend}</span>` +
      (inv ? `<span class="cons-i warn">⚠ inversión de signo</span>` : `<span class="cons-i">signo consistente</span>`);
  }

  function renderTransition() {
    const t = state.data.transition || {};
    el("trans-dwell").textContent = t.dwell_mean != null ? `permanencia media ${t.dwell_mean} días · n=${t.n}` : "";
    if (!t.next || !t.next.length) { el("transition").innerHTML = `<p class="mut small">Sin transiciones registradas.</p>`; return; }
    const max = Math.max(...t.next.map(x => x.pct), 1);
    el("transition").innerHTML = t.next.map(x =>
      `<div class="tr-row"><span class="tr-dot" style="background:${REGIME_COLORS[x.regime] || "#888"}"></span>` +
      `<span class="tr-name">${esc(x.regime)}</span>` +
      `<span class="tr-bar"><i style="width:${(x.pct / max * 100).toFixed(0)}%;background:${REGIME_COLORS[x.regime] || "#888"}"></i></span>` +
      `<span class="tr-pct">${x.pct.toFixed(0)}%</span></div>`).join("");
  }

  function renderCalibration() {
    const c = state.data.calibration;
    if (!c) { el("calibration").innerHTML = `<p class="mut small">Muestra insuficiente para calibrar.</p>`; el("calib-h").textContent = ""; return; }
    el("calib-h").textContent = `horizonte ${c.horizon} · n=${c.n}`;
    const covOk = c.coverage >= 90 && c.coverage <= 99;
    const rows = [
      ["Predicción media (exceso)", fmtPct(c.pred_mean), ""],
      ["Resultado medio observado", fmtPct(c.obs_mean), ""],
      ["Error absoluto medio", fmtPct(c.mae), ""],
      ["Sesgo (obs − pred)", fmtPct(c.bias), Math.abs(c.bias) < 0.01 ? "ok" : "warn"],
      ["Cobertura IC95%", c.coverage + "%", covOk ? "ok" : "warn"],
    ];
    el("calibration").innerHTML = `<div class="cal">` + rows.map(([k, v, st]) =>
      `<div class="cal-row"><span>${k}</span><b class="${st}">${v}</b></div>`).join("") + `</div>` +
      `<p class="mut small">Backtest causal: cada predicción usa solo análogos pasados del mismo régimen.</p>`;
  }

  // ── P2-2: robustez de la señal (independiente del exceso) ──
  function renderRobustness() {
    const sm = state.data.summary, sc = state.data.scenarios, t = state.data.transition, c = state.data.calibration;
    const lc = { "Muy robusta": "#3fae6b", "Robusta": "#8bbf3f", "Moderada": "#d99a2b", "Débil": "#cf5b3a", "Muy débil": "#8e1b13" }[sm.rob_level] || "var(--muted)";
    el("rob-h").textContent = `${sm.robustness}/100 · independiente del exceso`;
    const hz = HORIZONS.filter(k => sc[k] && sc[k].excess != null);
    const neffs = hz.map(k => sc[k].n_eff), widths = hz.map(k => sc[k].ci_hi - sc[k].ci_lo);
    const mNeff = neffs.reduce((a, b) => a + b, 0) / (neffs.length || 1);
    const mW = widths.reduce((a, b) => a + b, 0) / (widths.length || 1);
    const stable = new Set(hz.map(k => sc[k].excess >= 0)).size <= 1;
    const rows = [
      ["N efectivo medio", mNeff.toFixed(1), mNeff >= 20 ? "ok" : mNeff >= 8 ? "" : "warn"],
      ["IC medio", "±" + (mW / 2 * 100).toFixed(1) + "%", mW < 0.10 ? "ok" : "warn"],
      ["Signo entre horizontes", stable ? "consistente" : "inconsistente", stable ? "ok" : "warn"],
      ["Persistencia régimen", t.dwell_mean != null ? t.dwell_mean + " días" : "—", (t.dwell_mean || 0) >= 30 ? "ok" : ""],
      ["Calibración", c ? c.coverage + "%" : "—", c && c.coverage >= 85 ? "ok" : "warn"],
    ];
    el("robustness").innerHTML = `<div class="rob-big" style="color:${lc}">${sm.rob_level || "—"}</div>` +
      `<div class="cal">` + rows.map(([k, v, st]) => `<div class="cal-row"><span>${k}</span><b class="${st}">${v}</b></div>`).join("") + `</div>`;
  }

  // ── P2-3: consenso entre horizontes ──
  function renderConsensus() {
    const sc = state.data.scenarios; const hz = HORIZONS.filter(k => sc[k] && sc[k].evidence);
    const evs = hz.map(k => sc[k].evidence); const hasP = evs.includes("pos"), hasN = evs.includes("neg");
    let v, vc;
    if (hasP && hasN) { v = "Señales contradictorias"; vc = "#cf5b3a"; }
    else if (evs.every(e => e === "pos")) { v = "Consenso fuerte (positivo)"; vc = "#3fae6b"; }
    else if (evs.every(e => e === "neg")) { v = "Consenso fuerte (negativo)"; vc = "#cf5b3a"; }
    else if (hasP) { v = "Consenso parcial (positivo)"; vc = "#8bbf3f"; }
    else if (hasN) { v = "Consenso parcial (negativo)"; vc = "#d99a2b"; }
    else { v = "Sin señal (neutral en todos)"; vc = "#d99a2b"; }
    el("consensus").innerHTML = hz.map(k => { const E = EVID[sc[k].evidence];
      return `<div class="cons-row"><span class="cons-h">${k}</span><span class="ev-dot" style="background:${E.c}"></span><span class="mut small">${E.t}</span></div>`; }).join("") +
      `<div class="cons-verdict" style="color:${vc}">${v}</div>`;
  }

  // ── P2-4: qué invalidaría la señal ──
  function renderInvalidation() {
    const sc = state.data.scenarios, sm = state.data.summary, t = state.data.transition;
    const r = sc[REF] || {}; const ev = r.evidence; const nx = (t.next && t.next[0]) ? `${esc(t.next[0].regime)} ${t.next[0].pct.toFixed(0)}%` : "—";
    const it = [`Si el régimen deja de ser <b>${esc(sm.regime)}</b> — la tesis se recalcula (próximo probable: ${nx}).`];
    if (ev === "pos") it.push(`Si el <b>IC del exceso empieza a cruzar cero</b> — desaparece la evidencia.`);
    else if (ev === "neg") it.push(`Si el IC del exceso vuelve a <b>incluir el cero</b> — deja de ser señal negativa.`);
    else it.push(`Para que HAYA señal: el <b>IC del exceso a ${REF} tendría que dejar de incluir el cero</b> (hoy [${fmtPct(r.ci_lo - r.baseline)}, ${fmtPct(r.ci_hi - r.baseline)}]).`);
    it.push(`Si el <b>N efectivo cae por debajo de ~10</b> a horizontes largos — la confianza pasa a Baja.`);
    el("invalidation").innerHTML = `<ul class="inval">` + it.map(x => `<li>${x}</li>`).join("") + `</ul>`;
  }

  // ── P2-6: evolución de la tesis ──
  function drawThesis() {
    const c = el("thesis"), { ctx, w, h } = size(c); const th = state.data.thesis || [];
    ctx.clearRect(0, 0, w, h); if (th.length < 2) { ctx.fillStyle = css("--faint"); ctx.fillText("histórico insuficiente", 20, 20); return; }
    const t0 = th[0].t, t1 = th[th.length - 1].t; const exs = th.map(p => p.excess);
    let mn = Math.min(...exs, 0) - .005, mx = Math.max(...exs, 0) + .005; const m = 42, pb = h - 18, pt = 10;
    const X = t => m + (t - t0) / (t1 - t0) * (w - m - 12), Y = e => pb - (e - mn) / (mx - mn) * (pb - pt);
    for (let i = 1; i < th.length; i++) { ctx.fillStyle = (REGIME_COLORS[th[i].regime] || "#888") + "22";
      ctx.fillRect(X(th[i - 1].t), pt, X(th[i].t) - X(th[i - 1].t) + 1, pb - pt); }
    ctx.strokeStyle = css("--muted"); ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(m, Y(0)); ctx.lineTo(w - 12, Y(0)); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = css("--faint"); ctx.font = "10px monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText("0%", m - 4, Y(0)); ctx.fillText((mx * 100).toFixed(0) + "%", m - 4, Y(mx)); ctx.fillText((mn * 100).toFixed(0) + "%", m - 4, Y(mn));
    ctx.strokeStyle = css("--accent"); ctx.lineWidth = 1.5; ctx.beginPath();
    th.forEach((p, i) => { const x = X(p.t), y = Y(p.excess); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
    th.forEach(p => { const E = EVID[p.ev]; ctx.fillStyle = E ? E.c : "#888"; ctx.beginPath(); ctx.arc(X(p.t), Y(p.excess), 2, 0, 7); ctx.fill(); });
    ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = css("--faint");
    for (let i = 0; i <= 4; i++) { const t = t0 + i / 4 * (t1 - t0); ctx.fillText(new Date(t).toISOString().slice(0, 7), X(t), pb + 4); }
    el("thesis-h").textContent = "línea = exceso · fondo = régimen · puntos = evidencia";
  }

  // ── P2-7: transparencia ──
  function renderAudit() {
    const d = state.data, a = d.audit, sc = d.scenarios;
    const rows = [
      ["Horizontes", Object.keys(sc).join(" · ")],
      ["Tamaño histórico", a.history_len + " sesiones"],
      ["N bruto (12m)", a.n_raw_12m != null ? a.n_raw_12m : "—"],
      ["N efectivo (12m)", sc["12m"] ? sc["12m"].n_eff : "—"],
      ["Baseline (6m)", sc["6m"] ? fmtPct(sc["6m"].baseline) : "—"],
      ["Cobertura IC95 (calib.)", a.coverage != null ? a.coverage + "% (nominal 95%)" : "—"],
      ["Última actualización", d.as_of],
    ];
    const lims = [
      "Calibración a un solo horizonte (3m), muestreada.",
      "N efectivo ≈ N/horizonte (aproximación conservadora del solape).",
      "Baseline = buy-&-hold del propio activo (no cash ni peers).",
      "Sin corrección por multiplicidad (activos × horizontes).",
      "Sin ponderación por recencia (no-estacionariedad).",
    ];
    el("audit").innerHTML = `<div class="audit-grid">` +
      rows.map(([k, v]) => `<div class="au-k">${k}</div><div class="au-v">${v}</div>`).join("") + `</div>` +
      `<div class="au-lims"><span class="lbl">Limitaciones conocidas</span><ul>` + lims.map(l => `<li>${l}</li>`).join("") + `</ul></div>`;
  }

  function renderAll() {
    renderHeader(); renderHeadline(); renderRobustness(); renderConsensus(); renderInvalidation();
    drawChart(); drawRibbon(); drawPhase(); drawFan(); renderFanTable();
    renderTransition(); renderCalibration(); drawThesis(); renderAudit(); renderComposition();
  }

  // ── buscador + resize ───────────────────────────────
  const go = () => {
    const t = el("search").value.trim();
    if (t && t.toUpperCase() !== (state.symbol || "").toUpperCase()) load(t);
  };
  el("load").addEventListener("click", go);
  el("search").addEventListener("change", go);   // dispara al elegir del datalist o al salir
  el("search").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  let rt; new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => state.data && renderAll(), 120); })
    .observe(document.querySelector(".wrap"));

  load(el("search").value || "SPY");
})();
