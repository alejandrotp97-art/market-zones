/* Screener multiactivo — reusa /api/regime por activo (backend sin cambios).
   Ranking por Opportunity Score (P2-1) + comparación de dos activos (P2-5). */
(() => {
  "use strict";
  // Values below originate outside this file (the curated list, the API
  // payload). Escape at the point of insertion: the discipline is what
  // protects the NEXT value someone interpolates here, not just today's.
  const esc = (s) => String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const REF = "6m";
  const REGIME_COLORS = {
    "Pánico": "#8e1b13", "Capitulación": "#cf5b3a", "Recuperación": "#4a9e8f",
    "Alcista sano": "#3fae6b", "Sobrecalentamiento": "#d99a2b", "Clímax": "#b3261e",
    "Distribución": "#b07a3a", "Corrección": "#c96a5e", "Lateral": "#7c828e",
  };
  const EVID = { pos: { c: "#3fae6b", t: "positiva" }, flat: { c: "#d99a2b", t: "neutral" }, neg: { c: "#cf5b3a", t: "negativa" } };
  const GC = { "A+": "#3fae6b", "A": "#3fae6b", "B": "#8bbf3f", "C": "#d99a2b", "D": "#cf5b3a" };
  const fmtPct = (x) => (x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%");

  const tickers = window.__TICKERS__ || [];
  const rows = {}, full = {};       // full = payload completo (para comparación)
  const sel = [];                   // símbolos seleccionados (máx 2)
  let sortK = "opportunity", sortDir = -1;
  const body = document.getElementById("scr-body");
  document.getElementById("n-assets").textContent = tickers.length;
  tickers.forEach(([sym, name]) => (rows[sym] = { symbol: sym, name, status: "loading" }));
  render();

  let idx = 0, done = 0; const CONC = 5;
  function next() {
    if (idx >= tickers.length) return;
    const [sym] = tickers[idx++];
    fetch(`/api/regime?view=light&symbol=${encodeURIComponent(sym)}`).then((r) => r.json()).then((d) => {
      if (d.error) { rows[sym].status = "err"; return; }
      full[sym] = d;
      const s = d.summary, sc = d.scenarios[REF] || {};
      rows[sym] = { symbol: sym, name: rows[sym].name, status: "ok", regime: s.regime, score: s.score,
        excess: sc.excess, confidence: s.confidence, evidence: sc.evidence, n_eff: sc.n_eff,
        opportunity: s.opportunity, grade: s.grade, robustness: s.robustness };
    }).catch(() => { rows[sym].status = "err"; }).finally(() => {
      done++; document.getElementById("prog").textContent = `${done}/${tickers.length} cargados`;
      render(); next();
    });
  }
  for (let k = 0; k < CONC; k++) next();

  function render() {
    const arr = tickers.map(([s]) => rows[s]);
    const ok = arr.filter((r) => r.status === "ok"), rest = arr.filter((r) => r.status !== "ok");
    ok.sort((a, b) => { const va = a[sortK], vb = b[sortK];
      if (va == null) return 1; if (vb == null) return -1;
      return typeof va === "string" ? sortDir * va.localeCompare(vb) : sortDir * (va - vb); });
    body.innerHTML = ok.concat(rest).map(rowHTML).join("");
    body.querySelectorAll("tr[data-sym]").forEach((tr) => tr.addEventListener("click", () => toggle(tr.dataset.sym)));
  }

  function rowHTML(r) {
    const on = sel.includes(r.symbol) ? " sel" : "";
    if (r.status === "loading") return `<tr data-sym="${esc(r.symbol)}" class="row${on}"><td><span class="sym">${esc(r.symbol)}</span></td><td class="loading" colspan="8">cargando…</td></tr>`;
    if (r.status === "err") return `<tr data-sym="${esc(r.symbol)}" class="row${on}"><td><span class="sym">${esc(r.symbol)}</span></td><td class="err" colspan="8">no disponible</td></tr>`;
    const rc = REGIME_COLORS[r.regime] || "#888", E = EVID[r.evidence] || { c: "var(--muted)", t: "—" };
    const exc = r.excess != null ? `<span style="color:${r.excess >= 0 ? "#3fae6b" : "#cf5b3a"};font-weight:600">${fmtPct(r.excess)}</span>` : "—";
    const gc = GC[r.grade] || "var(--muted)";
    return `<tr data-sym="${esc(r.symbol)}" class="row${on}"><td><span class="sym">${esc(r.symbol)}</span> <span class="nm">${esc(r.name)}</span></td>` +
      `<td class="num"><span class="gr" style="color:${gc};border-color:${gc}">${r.grade || "—"}</span> <b>${r.opportunity != null ? r.opportunity : "—"}</b></td>` +
      `<td><span class="rdot" style="background:${rc}"></span>${esc(r.regime) || "—"}</td>` +
      `<td class="num">${r.score != null ? r.score.toFixed(1) : "—"}</td>` +
      `<td class="num">${exc}</td>` +
      `<td class="conf-${r.confidence}">${r.confidence || "—"}</td>` +
      `<td class="num">${r.robustness != null ? r.robustness : "—"}</td>` +
      `<td><span class="evdot" style="background:${E.c}" title="${E.t}"></span></td>` +
      `<td class="num">${r.n_eff != null ? r.n_eff : "—"}</td></tr>`;
  }

  function toggle(sym) {
    if (!full[sym]) return;
    const i = sel.indexOf(sym);
    if (i >= 0) sel.splice(i, 1);
    else { sel.push(sym); if (sel.length > 2) sel.shift(); }
    render(); renderCompare();
  }
  document.getElementById("cmp-clear").addEventListener("click", () => { sel.length = 0; render(); renderCompare(); });

  function renderCompare() {
    const sec = document.getElementById("compare-sec");
    if (sel.length < 2) { sec.hidden = true; return; }
    sec.hidden = false;
    const [A, B] = sel.map((s) => full[s]);
    const g = (d, fn) => { try { return fn(d); } catch { return "—"; } };
    const refx = (d) => (d.scenarios[REF] || {});
    const cmp = [
      ["Régimen", (d) => d.summary.regime],
      ["Oportunidad", (d) => `${d.summary.grade} · ${d.summary.opportunity}`],
      ["Robustez", (d) => d.summary.rob_level],
      ["Score", (d) => d.summary.score.toFixed(1)],
      ["Exceso 6m", (d) => fmtPct(refx(d).excess)],
      ["Baseline 6m", (d) => fmtPct(refx(d).baseline)],
      ["IC95 exceso 6m", (d) => `[${fmtPct(refx(d).ci_lo - refx(d).baseline)}, ${fmtPct(refx(d).ci_hi - refx(d).baseline)}]`],
      ["Evidencia 6m", (d) => (EVID[refx(d).evidence] || { t: "—" }).t],
      ["N efectivo 6m", (d) => refx(d).n_eff],
      ["Confianza", (d) => d.summary.confidence],
      ["Calibración (cobertura)", (d) => d.calibration ? d.calibration.coverage + "%" : "—"],
      ["Próximo régimen probable", (d) => (d.transition.next && d.transition.next[0]) ? `${esc(d.transition.next[0].regime)} ${d.transition.next[0].pct.toFixed(0)}%` : "—"],
      ["Permanencia media", (d) => d.transition.dwell_mean != null ? d.transition.dwell_mean + " días" : "—"],
    ];
    // ganador por oportunidad
    const win = A.summary.opportunity >= B.summary.opportunity ? 0 : 1;
    document.getElementById("compare").innerHTML =
      `<table class="cmp"><thead><tr><th></th>` +
      [A, B].map((d, i) => `<th class="${i === win ? "win" : ""}">${esc(d.symbol)}${i === win ? " ★" : ""}</th>`).join("") + `</tr></thead><tbody>` +
      cmp.map(([k, fn]) => `<tr><td class="ck">${k}</td>` + [A, B].map((d) => `<td>${g(d, fn)}</td>`).join("") + `</tr>`).join("") +
      `</tbody></table>` +
      `<p class="mut small">★ = mayor Opportunity Score. La decisión relativa: elegí el de mejor evidencia + N efectivo, no el de mayor exceso bruto.</p>`;
  }

  document.querySelectorAll("#scr th").forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k;
    if (sortK === k) sortDir *= -1; else { sortK = k; sortDir = (k === "name" || k === "regime" || k === "confidence") ? 1 : -1; }
    render();
  }));
})();
