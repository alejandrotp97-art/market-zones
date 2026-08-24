/* Diario y libro de movimientos — el REGISTRO de la cartera, en su página.

   Fichero aparte de cartera.js y no un trozo suyo con condicionales: cargar el
   módulo de la cartera aquí traería la gráfica, el mapa, las zonas y una docena
   de llamadas que esta pantalla no usa, y todas ellas tendrían que aprender a
   no romperse cuando su hueco en el HTML no existe.

   El precio de separarlos son los formateadores de abajo, repetidos. Es el
   mismo trato que ya hace cartera-map.js: ámbitos distintos, y el <body> como
   única verdad compartida. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));

  // El ojo de la cabecera marca el <body> y los formateadores lo leen de ahí.
  // Se enmascara AQUÍ y no celda a celda, por el mismo motivo que en la
  // cartera: lo que se añada mañana nace ya cubierto.
  const MASK = "•••";
  const hidden = () => document.body.classList.contains("amounts-hidden");
  const fmt = (x, d) => Number(x).toLocaleString("es-ES",
    { minimumFractionDigits: d, maximumFractionDigits: d });
  const money = (x, d = 2) => (x == null ? "—" : hidden() ? MASK : fmt(x, d));
  // Las participaciones se tapan igual que el dinero: los precios son públicos,
  // así que cantidad × precio reconstruye el patrimonio que acabas de esconder.
  const qty = (x) => (x == null ? "—" : hidden() ? MASK
    : Number(x).toLocaleString("es-ES", { maximumFractionDigits: 6 }));
  const status = (t) => { const s = $("status"); if (!t) { s.hidden = true; return; } s.textContent = t; s.hidden = false; };

  const CSRF = { "X-Market-Zones": "1" };
  const send = (url, opts = {}) =>
    fetch(url, { ...opts, headers: { ...(opts.headers || {}), ...CSRF } });

  const KINDCOL = { ETF: "#4a90d9", ETC: "#b0873f", Fondo: "#8a63d2", "Acción": "#3fae6b", "Índice": "#d99a2b", Cripto: "#e0952b", Divisa: "#7c828e", Futuro: "#7c828e" };
  const kbadge = (k) => k ? `<span class="kbadge" style="background:${KINDCOL[k] || "#7c828e"}">${esc(k)}</span>` : "";
  const SIDE_ES = { buy: "Compra", sell: "Venta", div: "Dividendo" };
  const assetCell = (ticker, name, kind) => name
    ? `<span class="a-name">${esc(name)}</span> ${kbadge(kind)}<div class="a-sym">${esc(ticker)}</div>`
    : `<span class="a-name a-mono">${esc(ticker)}</span> ${kbadge(kind)}`;

  let P = null;                             // último payload, para repintar sin pedirlo

  // ══ libro ═════════════════════════════════════════════════════════════
  function render(p) {
    if (!p || p.error) { status(""); if (p && p.error) alert(p.error); return; }
    P = p;
    const movs = p.movements || [];
    $("n-mov").textContent = (p.summary || {}).n_movements ?? movs.length;
    $("mov-body").innerHTML = movs.map((m) => `
      <tr>
        <td>${m.date ? esc(m.date) : "—"}</td><td>${assetCell(m.ticker, m.name, m.kind)}</td>
        <td><span class="side ${esc(m.side)}">${esc(SIDE_ES[m.side] || "Compra")}</span></td>
        <td class="num">${qty(m.quantity)}</td><td class="num">${money(m.price, 4)}</td>
        <td class="num">${money(m.fee)}</td><td class="note">${esc(m.note || "")}</td>
        <td class="num"><button class="edit" data-id="${esc(m.id)}" title="corregir en la cartera">✎</button></td>
        <td class="num"><button class="del" data-id="${esc(m.id)}" title="eliminar">✕</button></td>
      </tr>`).join("") || `<tr><td colspan="9" class="mut" style="padding:16px">Aún no hay movimientos.
        Se añaden desde <a href="/cartera" class="sublink">Mi Cartera</a>.</td></tr>`;
    $("mov-body").querySelectorAll(".del").forEach((b) =>
      b.addEventListener("click", () => del(b.dataset.id)));
    // Corregir vive en el formulario de alta, que está en la otra pantalla. Se
    // manda el id por la URL en vez de duplicar aquí el formulario entero: un
    // segundo formulario de alta sería un segundo sitio donde arreglar cada
    // cosa que cambie de los movimientos.
    $("mov-body").querySelectorAll(".edit").forEach((b) =>
      b.addEventListener("click", () => {
        location.href = "/cartera?editar=" + encodeURIComponent(b.dataset.id);
      }));
  }

  async function del(id) {
    status("Eliminando…");
    let d = null;
    try { d = await (await send("/api/cartera/" + id, { method: "DELETE" })).json(); }
    catch (e) { status("Error al eliminar"); return; }
    render(d);
    status("");
    // El diario cuenta los movimientos: borrar uno lo cambia, y dejarlo con el
    // borrado dentro sería enseñar dos verdades en la misma pantalla.
    loadDiario();
  }

  $("clear").addEventListener("click", async () => {
    if (!confirm("¿Borrar TODOS los movimientos? Esto no se puede deshacer.")) return;
    status("Borrando…");
    let d = null;
    try { d = await (await send("/api/cartera/clear", { method: "POST" })).json(); }
    catch (e) { status("Error al borrar"); return; }
    render(d);
    status("");
    loadDiario();
  });

  // ══ diario ════════════════════════════════════════════════════════════
  const DIARIO_ICO = { mov: "•", high: "▲", dd: "▼", rec: "↩" };
  async function loadDiario() {
    const box = $("diario");
    let d;
    try { d = await (await fetch("/api/cartera/diario")).json(); }
    catch (e) { box.innerHTML = `<div class="mut">No pude construirlo.</div>`; return; }
    const ev = (d.events || []);
    if (!ev.length) {
      box.innerHTML = `<div class="mut">Todavía no hay nada que contar. Aparecerán
        aquí tus movimientos y los hitos de la cartera: máximos nuevos, la peor
        caída y su recuperación.</div>`;
      return;
    }
    box.innerHTML = `<div class="diario">${ev.map((e) => `
      <div class="dia-fila ${esc(e.kind)}">
        <span class="dia-f">${esc(e.date)}</span>
        <span class="dia-i">${DIARIO_ICO[e.kind] || "•"}</span>
        <span class="dia-t">${esc(e.title)}</span>
        <span class="dia-d mut">${esc(e.detail || "")}</span>
      </div>`).join("")}</div>
      <p class="mut small">Sólo hechos tuyos y de tu serie de rendimiento. Ni
        noticias ni pronósticos: un diario que interpreta deja de ser un diario.</p>`;
  }

  // ══ ojo: ocultar los importes ═════════════════════════════════════════
  // La MISMA clave de localStorage que la cartera: es una sola preferencia de
  // pantalla, y verla respetada en un sitio y no en el otro sería peor que no
  // tenerla.
  const eyeBtn = $("eye");
  function applyHidden(on, repaint) {
    document.body.classList.toggle("amounts-hidden", on);
    const lbl = on ? "Mostrar importes" : "Ocultar importes";
    eyeBtn.setAttribute("aria-pressed", on ? "true" : "false");
    eyeBtn.title = lbl;
    eyeBtn.querySelector(".eye-lbl").textContent = lbl;
    if (repaint && P) render(P);            // mismos datos, otro aspecto
  }
  eyeBtn.addEventListener("click", () => {
    const on = !hidden();
    try { localStorage.setItem("cartera-hide", on ? "1" : "0"); } catch (e) { /* sin persistencia */ }
    applyHidden(on, true);
  });
  // Antes del primer pintado: nada de enseñar los importes y taparlos un
  // instante después, que es justo lo que se quería evitar.
  applyHidden((() => { try { return localStorage.getItem("cartera-hide") === "1"; } catch (e) { return false; } })(), false);

  async function load() {
    status("Cargando…");
    // El diario no depende del libro, así que las dos van a la vez en vez de
    // una detrás de otra.
    const dia = loadDiario();
    try { render(await (await fetch("/api/cartera")).json()); }
    catch (e) { status("Error al cargar"); return; }
    await dia;
    status("");
  }
  load();
})();
