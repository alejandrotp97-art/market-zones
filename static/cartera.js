/* Mi Cartera — libro de movimientos + posiciones valoradas.
   Persistencia server-side (SQLite); importación CSV/Excel en el backend. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const POS = "#3fae6b", NEG = "#cf5b3a";
  // ── modo discreto ────────────────────────────────────────────────────────
  // El ojo tapa los importes en pantalla y nada más: no toca la base de datos,
  // no deja de pedir precios, no cambia un solo número. Se enmascara AQUÍ, en
  // los formateadores por los que pasa todo importe, en vez de ir tapando cada
  // hueco de la plantilla — así lo que se añada mañana nace ya cubierto.
  // La bandera vive en el <body> para que el módulo del mapa, que es otro
  // fichero con su propio ámbito, lea exactamente la misma verdad.
  const MASK = "•••";
  const hidden = () => document.body.classList.contains("amounts-hidden");
  const fmt = (x, d) => Number(x).toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d });
  const money = (x, d = 2) => (x == null ? "—" : hidden() ? MASK : fmt(x, d));
  const eur = (x, d = 2) => (x == null ? "—" : money(x, d) + " €");
  const nat = (x, ccy, d = 2) => (x == null ? "—" : money(x, d) + (ccy ? " " + ccy : ""));
  // Las participaciones se tapan igual que el dinero: los precios son públicos,
  // así que cantidad × precio reconstruye el patrimonio que acabas de esconder.
  const qty = (x) => (x == null ? "—" : hidden() ? MASK
    : Number(x).toLocaleString("es-ES", { maximumFractionDigits: 6 }));
  const signed = (x, suf = "") => x == null ? "—" :
    `<span style="color:${x >= 0 ? POS : NEG};font-weight:600">${x >= 0 ? "+" : ""}${money(x)}${suf}</span>`;
  const status = (t) => { const s = $("status"); if (!t) { s.hidden = true; return; } s.textContent = t; s.hidden = false; };
  const cssv = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
  // Eje Y: en modo discreto se queda SIN etiquetas, no con "•••" repetido seis
  // veces. Las líneas de rejilla siguen ahí, así que la forma de la curva —que
  // no es un importe— se lee igual.
  const kfmt = (v) => hidden() ? ""
    : Math.abs(v) >= 1000 ? (v / 1000).toLocaleString("es-ES", { maximumFractionDigits: 1 }) + "k"
      : Number(v).toLocaleString("es-ES", { maximumFractionDigits: 0 });
  // Every state-changing call carries this header. It is not CORS-safelisted,
  // so a cross-site fetch() trying to forge one triggers a preflight the server
  // never answers, and an HTML form cannot set headers at all.
  const CSRF = { "X-Market-Zones": "1" };
  const send = (url, opts = {}) =>
    fetch(url, { ...opts, headers: { ...(opts.headers || {}), ...CSRF } });

  // Every field below reaches the DOM from a CSV the user uploaded, so it is
  // untrusted markup until escaped. Quotes included: `kind` lands inside an
  // attribute-adjacent template, and a stray `<` from a broker export ("Compra
  // < 100 uds") is enough to eat the rest of the table.
  const esc = (s) => String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  // ETC va aparte del resto: es el tipo del oro de la cartera y sin entrada
  // propia caía al gris genérico, indistinguible de "tipo desconocido".
  const KINDCOL = { ETF: "#4a90d9", ETC: "#b0873f", Fondo: "#8a63d2", "Acción": "#3fae6b", "Índice": "#d99a2b", Cripto: "#e0952b", Divisa: "#7c828e", Futuro: "#7c828e" };
  const kbadge = (k) => k ? `<span class="kbadge" style="background:${KINDCOL[k] || "#7c828e"}">${esc(k)}</span>` : "";
  const SIDE_ES = { buy: "Compra", sell: "Venta", div: "Dividendo" };

  // Celda "Activo", compartida por posiciones y movimientos: el NOMBRE arriba
  // en negrita con su etiqueta, el ISIN/ticker debajo. Sin nombre, el ticker
  // pasa arriba en vez de dejar un titular vacío con subtítulo.
  const assetCell = (ticker, name, kind, extra = "") => name
    ? `<span class="a-name">${esc(name)}</span> ${kbadge(kind)}${extra}<div class="a-sym">${esc(ticker)}</div>`
    : `<span class="a-name a-mono">${esc(ticker)}</span> ${kbadge(kind)}${extra}`;
  // La misma escala del Panel de Zonas: 0 = barato (verde), 100 = caro (rojo).
  // Copiada a propósito y no importada — cartera.js y app.js no se cargan nunca
  // en la misma página, y un módulo compartido para una línea costaría más
  // mantenerlo que tenerlo dos veces.
  const colorForScore = (s) => `hsl(${140 * (1 - Math.max(0, Math.min(100, s)) / 100)}, 68%, 55%)`;

  // Estado que NO viene del payload de la cartera: las zonas llegan por su
  // propia ruta (son lentas) y el criterio de coste lo elige quien mira.
  let ZONES = {}, ZONE_TRIES = 0, REALMODE = "avg", ESTADO = null, APORT = null, PERF = null;

  // La temporalidad la comparten la gráfica y la sección de rentabilidad: son
  // la misma pregunta mirada de dos formas, y verlas contestar a rangos
  // distintos a la vez es peor que no poder cambiar el rango.
  const RANGO = { r: "all", from: "", to: "" };
  const rangoQS = () => RANGO.r === "custom"
    ? `from=${encodeURIComponent(RANGO.from)}&to=${encodeURIComponent(RANGO.to)}`
    : `range=${encodeURIComponent(RANGO.r)}`;

  function zoneCell(ticker) {
    const z = ZONES[(ticker || "").toUpperCase()];
    if (!z) return `<span class="mut">…</span>`;
    if (z.error) return `<span class="mut small" title="${esc(z.error)}">—</span>`;
    const c = colorForScore(z.score);
    return `<span class="zchip" style="border-color:${c};color:${c}" `
      + `title="Score ${z.score} · modelo ${esc(z.model || "")} · lleva ${z.dwell} día(s) en esta zona · datos a ${esc(z.date || "")}">`
      + `${esc(z.zone)}</span>`;
  }

  function paintZones() {
    document.querySelectorAll("[data-zone-for]").forEach((el) => {
      el.innerHTML = zoneCell(el.dataset.zoneFor);
    });
  }

  // Las zonas se piden DESPUÉS de pintar la tabla, y por partes. Con la caché
  // fría cada instrumento es una descarga de 25 años, así que el servidor
  // devuelve lo que le da tiempo y deja el resto en `pending`; aquí se vuelve a
  // llamar hasta que no queda nada. El límite de intentos existe para que un
  // instrumento que falla siempre no deje al navegador llamando para siempre.
  async function loadZones(first = true) {
    if (first) ZONE_TRIES = 0;
    if (ZONE_TRIES++ > 6) return;
    let d;
    try { d = await (await fetch("/api/cartera/zonas")).json(); } catch (e) { return; }
    if (d.error) return;
    ZONES = { ...ZONES, ...d.zones };
    paintZones();
    if ((d.pending || []).length) setTimeout(() => loadZones(false), 400);
  }

  let CH = null, sel = null, P = null, EDITING = null;

  async function load() {
    status("Cargando…");
    // The history endpoint does not depend on the positions payload, so both
    // round trips run at once instead of one after the other.
    const hist = loadHistory();
    try { render(await (await fetch("/api/cartera")).json(), false); }
    catch (e) { status("Error al cargar"); return; }
    await hist;
    status("");
  }

  // `dataChanged` es falso cuando solo repintamos lo mismo con otro aspecto (el
  // ojo): el mapa no tiene por qué volver a pedir nada para tapar sus cifras.
  function render(p, reloadHistory = true, dataChanged = true) {
    P = p;                                  // último payload, para repintar sin pedirlo otra vez
    const s = p.summary || {};
    // El mapa de exposición geográfica vive en su propio módulo y se entera por
    // aquí: un evento en vez de una llamada directa, para que ninguna de las dos
    // piezas necesite existir para que la otra funcione.
    document.dispatchEvent(new CustomEvent(dataChanged ? "cartera:changed" : "cartera:display"));
    $("n-mov").textContent = s.n_movements ?? 0;
    $("s-inv").textContent = eur(s.invested);
    $("s-unreal").innerHTML = signed(s.unreal, " €");
    $("s-unrealpct").textContent = s.unreal_pct != null ? (s.unreal_pct >= 0 ? "+" : "") + s.unreal_pct + "%" : "";
    paintRealized(s);
    $("s-income").innerHTML = signed(s.income, " €");
    $("s-incomenote").textContent = s.n_dividends
      ? `${s.n_dividends} cobro${s.n_dividends === 1 ? "" : "s"}`
      : "sin dividendos apuntados";
    // posiciones
    const open = (p.positions || []).filter((r) => r.qty > 1e-9);
    // Una posición cerrada que sólo dejó dividendos también es historia de esta
    // cartera: filtrarla por el realizado a secas la borraba de la tabla.
    const closed = (p.positions || []).filter((r) => r.qty <= 1e-9
      && (Math.abs(r.realized || 0) > 1e-9 || Math.abs(r.income || 0) > 1e-9));
    const ccyNote = (s.currencies && s.currencies.length > 1) ? ` · ${s.currencies.join("/")} → EUR en tiempo real` : "";
    // The totals cover only what could be valued. Never let the header imply
    // it covers everything when it does not.
    const nUnv = (s.unvalued || []).length;
    const notes = [`${open.length} abiertas`];
    if (closed.length) notes.push(`${closed.length} cerradas`);
    if (nUnv) notes.push(`⚠ ${nUnv} sin valorar (${s.unvalued.map((u) => u.ticker + ": " + u.why).join(", ")}) — fuera de los totales`);
    if ((s.oversold || []).length) notes.push(`⚠ ventas de más en ${s.oversold.map((o) => o.ticker).join(", ")}`);
    if (s.n_undated) notes.push(`⚠ ${s.n_undated} movimientos sin fecha`);
    $("pos-legend").textContent = notes.join(" · ") + ccyNote;
    $("pos-legend").classList.toggle("has-warn", nUnv > 0 || (s.oversold || []).length > 0 || s.n_undated > 0);
    $("pos-body").innerHTML = open.concat(closed).map((r) => {
      const cl = r.qty <= 1e-9;
      return `<tr class="${cl ? "closed" : ""}">
        <td>${assetCell(r.ticker, r.name, r.kind,
          (r.why ? ` <span class="warn" title="No puedo expresarla en EUR: ${esc(r.why)}">⚠</span>` : "")
          + (r.oversold ? ` <span class="warn" title="Hay ${qty(r.oversold)} vendidas de más: falta una compra en los movimientos">⚠ ventas de más</span>` : "")
          + (cl ? ' <span class="mut small">cerrada</span>' : ""))}</td>
        <td data-zone-for="${esc(r.ticker)}">${cl ? "" : zoneCell(r.ticker)}</td>
        <td class="num">${cl || r.weight == null ? "—" : `<span class="wbar" style="--w:${r.weight}%">${r.weight}%</span>`}</td>
        <td class="num">${cl ? "—" : qty(r.qty)}</td>
        <td class="num">${nat(r.avg_cost, r.ccy, 4)}</td>
        <td class="num">${cl ? "—" : eur(r.invested)}</td>
        <td class="num">${nat(r.last, r.ccy, 4)}</td>
        <td class="num">${eur(r.market_value)}</td>
        <td class="num">${cl ? "—" : signed(r.unreal, " €")}</td>
        <td class="num">${r.unreal_pct != null ? `<span style="color:${r.unreal_pct >= 0 ? POS : NEG}">${r.unreal_pct >= 0 ? "+" : ""}${r.unreal_pct}%</span>` : "—"}</td>
        <td class="num">${r.income ? signed(r.income, " €") : "—"}</td>
        <td class="num">${signed(REALMODE === "fifo" ? r.realized_fifo : r.realized, " €")}</td></tr>`;
    }).join("") || `<tr><td colspan="12" class="mut" style="padding:16px">Sin posiciones todavía. Añade un movimiento o importa un archivo.</td></tr>`;
    // movimientos
    $("mov-body").innerHTML = (p.movements || []).map((m) => `
      <tr class="${EDITING == m.id ? "editing" : ""}">
        <td>${m.date ? esc(m.date) : "—"}</td><td>${assetCell(m.ticker, m.name, m.kind)}</td>
        <td><span class="side ${esc(m.side)}">${esc(SIDE_ES[m.side] || "Compra")}</span></td>
        <td class="num">${qty(m.quantity)}</td><td class="num">${money(m.price, 4)}</td>
        <td class="num">${money(m.fee)}</td><td class="note">${esc(m.note || "")}</td>
        <td class="num"><button class="edit" data-id="${esc(m.id)}" title="corregir">✎</button></td>
        <td class="num"><button class="del" data-id="${esc(m.id)}" title="eliminar">✕</button></td>
      </tr>`).join("") || `<tr><td colspan="9" class="mut" style="padding:16px">Aún no hay movimientos.</td></tr>`;
    $("mov-body").querySelectorAll(".del").forEach((b) =>
      b.addEventListener("click", () => del(b.dataset.id)));
    $("mov-body").querySelectorAll(".edit").forEach((b) =>
      b.addEventListener("click", () => startEdit((p.movements || []).find((m) => String(m.id) === b.dataset.id))));
    if (p.import) importMsg(p.import);
    // Las zonas van por su cuenta y DESPUÉS: son la parte lenta, y la tabla no
    // tiene por qué esperarlas para enseñar el dinero. Sólo se vuelven a pedir
    // cuando los datos han cambiado; repintar por el ojo no toca la red.
    renderFiscForm(p);
    renderCostes(p);
    renderFx(p);
    renderContrib(p);
    if (dataChanged) loadRebal();
    if (dataChanged) { loadZones(); loadPerf(); loadCorr(); loadEstado(); loadAport(); loadCcy(); }
    else paintZones();
    if (reloadHistory) loadHistory();     // skipped on first load: already in flight
  }

  // El realizado y la advertencia que lo acompaña. Los dos criterios coinciden
  // salvo que haya ventas PARCIALES —cerrar entera una posición consume todos
  // los lotes con cualquiera de los dos—, así que cuando divergen la diferencia
  // no es un matiz: es lo que separa lo que enseña la cartera de lo que hay que
  // declarar. Enseñar sólo uno de los dos y callarse el otro era esconderlo.
  function paintRealized(s) {
    const v = REALMODE === "fifo" ? s.realized_fifo : s.realized;
    $("s-real").innerHTML = signed(v, " €");
    const gap = (s.realized_fifo != null && s.realized != null)
      ? s.realized_fifo - s.realized : null;
    const note = $("s-realnote");
    if (gap == null || Math.abs(gap) < 0.01) {
      note.textContent = REALMODE === "fifo" ? "FIFO · criterio fiscal" : "coste medio";
      note.classList.remove("has-warn");
    } else {
      const otro = REALMODE === "fifo" ? s.realized : s.realized_fifo;
      note.innerHTML = `${REALMODE === "fifo" ? "FIFO" : "coste medio"} · `
        + `con el otro criterio: <b>${eur(otro)}</b>`;
      note.classList.add("has-warn");
    }
  }

  $("real-mode").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mode]");
    if (!b || b.dataset.mode === REALMODE) return;
    REALMODE = b.dataset.mode;
    $("real-mode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    if (P) render(P, false, false);
  });

  // ══ estado de mi cartera ══════════════════════════════════════════════
  // El bloque que se lee en diez segundos. Junta lo que si no habría que ir a
  // buscar a cinco sitios, y NO repite ninguna cifra del desglose de abajo:
  // dos números iguales en la misma pantalla obligan a comprobar si dicen lo
  // mismo, que es justo el trabajo que este bloque existe para ahorrar.
  async function loadEstado() {
    const leg = $("estado-legend"), box = $("estado");
    let d;
    try { d = await (await fetch("/api/cartera/estado")).json(); }
    catch (e) { leg.textContent = ""; box.innerHTML = `<div class="mut">No pude calcularlo.</div>`; return; }
    if (d.error) { leg.textContent = ""; box.innerHTML = `<div class="mut">${esc(d.error)}</div>`; return; }
    if (!d.n_positions) {
      leg.textContent = "";
      box.innerHTML = `<div class="mut">Sin posiciones abiertas todavía.</div>`;
      $("atencion-card").hidden = true; renderPlan(d); return;
    }
    ESTADO = d;
    const cob = d.coverage || {};
    leg.innerHTML = `${d.n_positions} posiciones · análisis sobre el <b>${
      cob.analisis == null ? "—" : cob.analisis + "%"}</b> del patrimonio`;

    const dd = d.drawdown || {};
    const tile = (lbl, val, sub, color) => `<div class="et">
      <div class="et-l">${lbl}</div>
      <div class="et-v"${color ? ` style="color:${color}"` : ""}>${val}</div>
      <div class="et-s">${sub || ""}</div></div>`;

    box.innerHTML = `<div class="estado-grid">
      ${tile("Tengo", eur(d.value), `${d.n_positions} posiciones abiertas`)}
      ${tile("He puesto", eur(d.contributed), "aportado menos retirado")}
      ${tile("He ganado", signed(d.result, " €"),
             d.twr == null ? "" : `${pct(d.twr, 1)} desde el principio`,
             (d.result || 0) >= 0 ? POS : NEG)}
      ${tile("Este año", d.ytd == null ? "—" : pct(d.ytd, 1),
             d.ytd_from ? `desde ${esc(d.ytd_from)}` : "", (d.ytd || 0) >= 0 ? POS : NEG)}
      ${tile(`Frente a ${esc(d.benchmark_ticker)}`,
             d.vs_benchmark == null ? "—" : pct(d.vs_benchmark, 1),
             "sin dividendos en ninguno de los dos",
             (d.vs_benchmark || 0) >= 0 ? POS : NEG)}
      ${tile("Peor caída", dd.max == null ? "—" : pct(dd.max, 1),
             dd.at_high ? "ahora, en máximos" : (dd.current == null ? "" : `ahora ${pct(dd.current, 1)}`),
             NEG)}
      ${tile("Cobertura", cob.analisis == null ? "—" : cob.analisis + "%",
             cob.analisis != null && cob.analisis < 100
               ? "el resto no está medido" : "todo el patrimonio",
             cob.analisis != null && cob.analisis < 90 ? "#cf8b3a" : "")}
      ${(() => {
        // La casilla cuenta TODO lo que hay debajo. Enseñar sólo los graves
        // ponía un "0" encima de una lista de cuatro avisos, y eso obliga a
        // parar a averiguar cuál de los dos números miente.
        const n = (d.attention || []).length, g = d.n_attention || 0;
        return tile("Requiere mirada", String(n),
          n === 0 ? "nada pendiente" : g ? `${g} de ellos, importantes` : "ninguno urgente",
          g ? "#cf8b3a" : (n ? "" : POS));
      })()}
    </div>
    ${d.twr != null && !d.annualizable ? `<p class="mut small">Menos de un año de
      historia: no se anualiza ninguna cifra, porque convertir una racha corta
      en una tasa anual es inventarse un dato.</p>` : ""}`;

    renderAtencion(d);
    renderPlan(d);
  }

  // ══ qué merece tu atención ════════════════════════════════════════════
  // Un aviso describe un HECHO. En cuanto dijera qué hacer estaría prometiendo
  // algo que este panel tiene medido que no sabe: aquí el buy & hold le gana a
  // cualquier regla de entrada y salida que se ha probado.
  function renderAtencion(d) {
    const box = $("atencion"), card = $("atencion-card");
    const av = d.attention || [];
    card.hidden = !av.length;
    if (!av.length) return;
    box.innerHTML = `<div class="avisos">${av.map((a) => `
      <div class="aviso ${esc(a.level)}">
        <div class="av-t">${esc(a.title)}</div>
        <div class="av-s">${esc(a.scope)}</div>
        <div class="av-w">${esc(a.why)}</div>
        ${a.missing ? `<div class="av-m">Falta: ${esc(a.missing)}</div>` : ""}
        ${a.key.startsWith("split:") ? splitBotones(a.key) : ""}
      </div>`).join("")}</div>
      <p class="mut small">Ninguno de estos avisos dice qué comprar ni qué vender.
        Describen el estado de tus datos y la distancia respecto a lo que TÚ has
        declarado; qué hacer con eso depende de cosas que este panel no sabe.</p>`;
  }

  // El único aviso con botones, porque es el único que el panel puede arreglar
  // solo. Las dos salidas son distintas y se nombran distinto: «ya lo tenía en
  // cuenta» sólo silencia, «ajustar» REESCRIBE movimientos. Llamar a las dos
  // «aceptar» sería invitar a pulsar la que reescribe sin querer.
  function splitBotones(key) {
    const [, tk, fecha] = key.split(":");
    return `<div class="av-acts">
      <button type="button" class="sp-apply" data-tk="${esc(tk)}" data-d="${esc(fecha)}">Ajustar mis cantidades</button>
      <button type="button" class="sp-ack" data-tk="${esc(tk)}" data-d="${esc(fecha)}">Ya lo tenía en cuenta</button>
    </div>`;
  }

  async function resolverSplit(tk, fecha, accion) {
    if (accion === "apply" && !confirm(
        `Se van a REESCRIBIR los movimientos de ${tk} anteriores al ${fecha}: ` +
        `las cantidades se multiplican y los precios se dividen por el factor del split. ` +
        `El coste total no cambia. Se guarda una copia de seguridad antes.\n\n¿Sigo?`)) return;
    const r = await send("/api/cartera/splits", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: tk, date: fecha, action: accion }) });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    if (d.split_applied) status(`Ajustados ${d.split_applied.n} movimiento(s) · ${d.split_applied.backup}`);
    render(d);
  }

  // ══ aportaciones ══════════════════════════════════════════════════════
  async function loadAport() {
    const box = $("aport"), leg = $("aport-legend");
    let d;
    try { d = await (await fetch("/api/cartera/aportaciones")).json(); }
    catch (e) { box.innerHTML = `<div class="mut">No pude calcularlo.</div>`; return; }
    if (d.error || d.empty || !(d.rows || []).length) {
      leg.textContent = "";
      box.innerHTML = `<div class="mut">Aún no hay movimientos con fecha que agregar por meses.</div>`;
      return;
    }
    APORT = d;
    const s = d.stats;
    leg.textContent = `${s.months} meses · ${s.n_months_with_in} con aportación`;
    const filas = d.rows.slice(-36);
    const max = Math.max(...filas.map((r) => Math.max(r.in, r.out)), 1);
    const barras = filas.map((r) => `<span class="ap-col"
        title="${esc(r.month)} · entra ${eur(r.in)}${r.out ? " · sale " + eur(r.out) : ""}${r.div ? " · dividendos " + eur(r.div) : ""}">
        <i class="ap-in" style="height:${(r.in / max * 100).toFixed(1)}%"></i>
        ${r.out ? `<i class="ap-out" style="height:${(r.out / max * 100).toFixed(1)}%"></i>` : ""}
      </span>`).join("");

    box.innerHTML = `
      <div class="ap-kpis">
        <div><div class="ex-h">Aportado en total</div><div class="ap-big">${eur(s.total_in)}</div>
          <div class="mut small">sin restar lo que salió por ventas${
            s.total_out ? " — arriba, en «He puesto», sí está restado" : ""}</div></div>
        <div><div class="ex-h">Media al mes</div><div class="ap-big">${eur(s.avg_month)}</div>
          <div class="mut small">repartido entre los ${s.months} meses transcurridos, no sólo entre los que aportaste</div></div>
        <div><div class="ex-h">Última aportación</div>
          <div class="ap-big">${s.last_month ? esc(s.last_month) : "—"}</div>
          <div class="mut small">${s.months_since === 0 ? "este mes"
            : s.months_since == null ? "" : `hace ${s.months_since} mes(es)`}</div></div>
      </div>
      <div class="ap-chart">${barras}</div>
      <div class="ap-axis"><span>${esc(filas[0].month)}</span>
        <span class="ap-leg"><i class="ap-in"></i>entra <i class="ap-out"></i>sale</span>
        <span>${esc(filas[filas.length - 1].month)}</span></div>
      ${s.total_out ? `<div class="vs-row"><span>Retirado por ventas</span><b>${eur(s.total_out)}</b></div>` : ""}
      ${s.total_div ? `<div class="vs-row"><span>Dividendos cobrados</span><b>${eur(s.total_div)}</b></div>` : ""}
      <p class="mut small">Mide el dinero que se despliega en <b>títulos</b>, no lo que
        entra en la cuenta del bróker: este panel ve compras, no transferencias. Un mes
        con dinero parado en efectivo aparece aquí como un mes sin aportar.</p>`;
  }

  // ══ objetivo propio ═══════════════════════════════════════════════════
  function renderPlan(d) {
    const box = $("plan"), g = d.goal;
    const campo = (id, lbl, val, ph) => `<label>${lbl}
      <input type="number" id="${id}" step="any" min="0" value="${val == null ? "" : val}" placeholder="${ph}"></label>`;
    const form = `<form class="plan-form" id="plan-form">
      ${campo("g-capital", "Capital objetivo (€)", g && g.capital, "100000")}
      ${campo("g-monthly", "Aportación mensual (€)", g && g.monthly, "500")}
      ${campo("g-horizon", "Horizonte (años)", g && g.horizon_years, "15")}
      <button type="submit" class="primary-mini">Guardar</button>
    </form>`;

    if (!g || g.capital == null) {
      box.innerHTML = `<p class="mut">Declara a dónde quieres llegar y el panel te
        dice cuánto llevas del camino. <b>No proyecta ninguna fecha de llegada</b>:
        eso exigiría suponer una rentabilidad futura, y este panel tiene medido que
        no sabe pronosticar.</p>${form}`;
    } else {
      const p = Math.max(0, Math.min(100, g.pct || 0));
      box.innerHTML = `
        <div class="ex-h">Capital objetivo</div>
        <div class="ap-big">${g.pct == null ? "—" : g.pct + "%"}
          <span class="mut" style="font-size:14px">de ${eur(g.capital)}</span></div>
        <div class="plan-bar"><i style="width:${p}%"></i></div>
        <div class="mut small">Te faltan ${eur(g.missing)}.
          ${g.horizon_years ? `Horizonte declarado: ${g.horizon_years} años.` : ""}</div>
        ${g.plan_pct != null ? `<div class="vs-row" style="margin-top:12px">
            <span>Aportado en 12 meses</span><b>${eur(g.real_12m)}</b></div>
          <div class="vs-row"><span>Previsto en 12 meses</span><b>${eur(g.plan_12m)}</b></div>
          <div class="vs-row vs-tot"><span>Cumplimiento</span>
            <b style="color:${g.plan_pct >= 100 ? POS : g.plan_pct >= 80 ? "" : NEG}">${g.plan_pct}%</b></div>` : ""}
        <p class="mut small">Progreso y desviación, nada más. Una fecha de llegada
          sería una predicción disfrazada de aritmética.</p>${form}`;
    }

    $("plan-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { capital: $("g-capital").value, monthly: $("g-monthly").value,
                     horizon_years: $("g-horizon").value };
      const r = await send("/api/cartera/plan", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const dd = await r.json();
      if (dd.error) { alert(dd.error); return; }
      ESTADO = dd; renderPlan(dd); renderAtencion(dd);
    });
  }

  // ══ rentabilidad ══════════════════════════════════════════════════════
  // Un «+89%» sobre una cartera con aportaciones repartidas no se puede
  // comparar con nada. Aquí van las dos cifras que sí, y la diferencia entre
  // ellas, que es lo más informativo de toda la sección.
  const pct = (x, d = 2) => x == null ? "—"
    : (x >= 0 ? "+" : "") + (x * 100).toLocaleString("es-ES",
        { minimumFractionDigits: d, maximumFractionDigits: d }) + "%";
  const pcol = (x) => x == null ? "" : `color:${x >= 0 ? POS : NEG}`;

  async function loadPerf() {
    const leg = $("perf-legend"), box = $("perf");
    let d;
    const bench = ($("bench").value || "SPY").trim().toUpperCase();
    try { d = await (await fetch(`/api/cartera/rendimiento?benchmark=${encodeURIComponent(bench)}&${rangoQS()}`)).json(); }
    catch (e) { leg.textContent = ""; box.innerHTML = `<div class="mut">No pude calcularla.</div>`; return; }
    if (d.error || d.empty || !d.twr || d.twr.total == null) {
      leg.textContent = "";
      box.innerHTML = `<div class="mut">Aún no hay historia suficiente para medir una rentabilidad.</div>`;
      return;
    }
    PERF = d;
    const t = d.twr, anual = d.annualizable;
    leg.innerHTML = `${t.days} días · ${t.periods} sesiones`
      + (t.skipped ? ` · <b class="warn-txt">${t.skipped} excluidas</b>` : "");

    // La diferencia TWR-TIR ES el efecto del propio calendario de aportaciones.
    // Es el único sitio de la aplicación que le pone número a una decisión de
    // quien invierte en vez de a una del mercado.
    const gap = (anual && d.tir != null && t.annualized != null) ? d.tir - t.annualized : null;
    const timing = gap == null ? "" : gap >= 0
      ? `Tus aportaciones <b style="color:${POS}">sumaron ${pct(gap, 1)}</b> anual: entraste, de media, en buenos momentos.`
      : `Tus aportaciones <b style="color:${NEG}">restaron ${pct(-gap, 1)}</b> anual: los mismos activos habrían rendido más entrando de otra forma.`;

    const bt = d.benchmark_twr;
    const vsb = (bt && bt.total != null)
      ? d.twr_price_only.total - bt.total : null;

    const anos = t.by_year.map((y) => `<tr>
      <td>${y.year}${y.partial ? ` <span class="mut small" title="Tramo medido: ${esc(y.from)} → ${esc(y.to)}">parcial</span>` : ""}</td>
      <td class="num" style="${pcol(y.ret)}">${pct(y.ret, 1)}</td>
      <td class="bar-cell"><i class="ybar ${y.ret >= 0 ? "up" : "dn"}"
        style="--w:${Math.min(100, Math.abs(y.ret) * 100 * 2).toFixed(1)}%"></i></td></tr>`).join("");

    box.innerHTML = `
      <div class="perf-grid">
        <div class="perf-kpi">
          <div class="ex-h">TWR${anual ? " anualizado" : " (total)"}</div>
          <div class="perf-big" style="${pcol(anual ? t.annualized : t.total)}">${pct(anual ? t.annualized : t.total, 2)}</div>
          <div class="mut small">Qué tal lo han hecho los activos que elegiste.
            No la mueve <b>cuándo</b> aportaste, y por eso es la única comparable con un índice.</div>
        </div>
        <div class="perf-kpi">
          <div class="ex-h">TIR${anual ? " (anual)" : ""}</div>
          <div class="perf-big" style="${pcol(d.tir)}">${anual ? pct(d.tir, 2) : "—"}</div>
          <div class="mut small">${anual
            ? "Qué te has llevado tú. Sí cuenta cuándo entró cada euro."
            : "Menos de un año de historia: anualizar una racha corta la convertiría en una tasa que no existe."}</div>
        </div>
        <div class="perf-kpi">
          <div class="ex-h">Acumulado</div>
          <div class="perf-big" style="${pcol(t.total)}">${pct(t.total, 2)}</div>
          <div class="mut small">Desde el primer movimiento.
            ${anual ? "" : "Todavía sin anualizar."}</div>
        </div>
      </div>
      ${timing ? `<p class="perf-note">${timing}</p>` : ""}
      ${riesgoBloque(d)}
      <div class="perf-two">
        <div>
          <div class="ex-h">Por año</div>
          <table class="tbl yr-tbl"><tbody>${anos}</tbody></table>
        </div>
        <div>
          <div class="ex-h">Frente a ${esc(d.benchmark_ticker)}</div>
          ${bt && bt.total != null ? `
            <div class="vs-row"><span>Tu cartera</span><b style="${pcol(d.twr_price_only.total)}">${pct(d.twr_price_only.total, 2)}</b></div>
            <div class="vs-row"><span>${esc(d.benchmark_ticker)}</span><b style="${pcol(bt.total)}">${pct(bt.total, 2)}</b></div>
            <div class="vs-row vs-tot"><span>Diferencia</span><b style="${pcol(vsb)}">${pct(vsb, 2)}</b></div>
            <p class="mut small">Comparación <b>limpia</b>: aquí tu cartera va SIN sus dividendos,
              porque la serie del índice tampoco lleva los suyos. Enfrentar un total return
              contra un price return te regalaría la rentabilidad por dividendo del índice.</p>`
            : `<div class="mut">Sin serie del índice para comparar.</div>`}
        </div>
      </div>
      ${(d.excluded || []).length ? `<p class="mut small">⚠ Fuera de este cálculo:
        ${d.excluded.map(esc).join(", ")} — sin serie o sin tipo de cambio.</p>` : ""}`;
  }

  // La caída va SIEMPRE sobre el índice de rendimiento y nunca sobre los euros.
  // El valor en euros sube cuando se aporta, y aportar no es recuperarse: una
  // transferencia puede devolver la cifra a su máximo anterior y hacer pasar
  // por «recuperada» una caída de la que el mercado no ha vuelto.
  function riesgoBloque(d) {
    const dd = d.drawdown;
    if (!dd) return "";
    const rec = dd.recovered
      ? `recuperada el <b>${esc(dd.recovered)}</b>${dd.days_to_recover != null ? ` — tardó ${dd.days_to_recover} días` : ""}`
      : `<b class="warn-txt">todavía sin recuperar</b>`;
    return `
      <div class="perf-grid" style="margin-top:16px">
        <div class="perf-kpi"><div class="ex-h">Caída máxima</div>
          <div class="perf-big" style="color:${NEG}">${pct(dd.max, 1)}</div>
          <div class="mut small">De <b>${esc(dd.peak || "—")}</b> a <b>${esc(dd.trough || "—")}</b>; ${rec}.
            Medida sobre lo que valía 1 € invertido, no sobre el saldo: aportar no es recuperarse.
            ${enEuros(dd.max)}</div></div>
        <div class="perf-kpi"><div class="ex-h">Ahora mismo</div>
          <div class="perf-big" style="${dd.at_high ? `color:${POS}` : `color:${NEG}`}">${
            dd.at_high ? "en máximos" : pct(dd.current, 1)}</div>
          <div class="mut small">${dd.at_high
            ? "Estás en el mejor momento de la serie."
            : "Por debajo de tu mejor momento. Es la caída que importa hoy, no la de 2020."}</div></div>
        <div class="perf-kpi"><div class="ex-h">Volatilidad · Sharpe</div>
          <div class="perf-big">${d.volatility != null ? pct(d.volatility, 1) : "—"}
            <span class="mut" style="font-size:15px">${d.sharpe != null ? " · " + d.sharpe.toFixed(2) : ""}</span></div>
          <div class="mut small">${d.sharpe != null
            ? `Rentabilidad por unidad de riesgo, con tipo sin riesgo <b>0%</b> — un Sharpe sin decir contra qué se calcula no se compara con nada.`
            : "Hace falta un año de historia para el Sharpe."}</div></div>
      </div>`;
  }

  // ══ quién ha puesto el dinero ═════════════════════════════════════════
  // Ni el peso ni el porcentaje de subida contestan esto. Una posición del 5%
  // que se dobló ha hecho más dinero que una del 40% que subió un 2%, y hasta
  // ahora eso no se deducía de ninguna columna de la tabla.
  function renderContrib(p) {
    const box = $("contrib");
    const rows = (p.positions || [])
      .filter((r) => r.contribution != null && Math.abs(r.contribution) > 0.005)
      .sort((a, b) => b.contribution - a.contribution);
    if (!rows.length) { box.innerHTML = `<div class="mut">Todavía no hay resultado que repartir.</div>`; return; }
    const max = Math.max(...rows.map((r) => Math.abs(r.contribution)));
    const total = rows.reduce((a, r) => a + r.contribution, 0);
    box.innerHTML = `
      <div class="cbars">${rows.map((r) => {
        const w = (Math.abs(r.contribution) / max * 100).toFixed(1);
        const pos = r.contribution >= 0;
        return `<div class="cbar-row">
          <span class="cbar-lab" title="${esc(r.name || r.ticker)}">${esc(r.ticker)}</span>
          <span class="cbar-track"><i class="cbar ${pos ? "up" : "dn"}" style="width:${w}%"></i></span>
          <span class="cbar-val" style="color:${pos ? POS : NEG}">${signed(r.contribution, " €")}</span>
        </div>`; }).join("")}</div>
      <div class="vs-row vs-tot"><span>Total</span><b style="color:${total >= 0 ? POS : NEG}">${signed(total, " €")}</b></div>
      <p class="mut small">No realizado + realizado + dividendos de cada posición.
        Es la respuesta a «¿quién me está haciendo el dinero?», que no es la misma
        pregunta que «¿qué pesa más?» ni que «¿qué ha subido más por ciento?».</p>`;
  }

  // ══ rebalanceo ════════════════════════════════════════════════════════
  async function loadRebal() {
    const box = $("rebal");
    const cash = Number($("rb-cash") ? $("rb-cash").value : 0) || 0;
    let d;
    try { d = await (await fetch("/api/cartera/rebalanceo?cash=" + cash)).json(); }
    catch (e) { box.innerHTML = `<div class="mut">No pude calcularlo.</div>`; return; }
    const filas = d.rows || [];
    const compras = d.buys || {};
    const drift = filas.map((r) => `<tr>
      <td><span class="sym">${esc(r.ticker)}</span></td>
      <td class="num">${r.now_pct}%</td>
      <td class="num mut">${r.target_pct}%</td>
      <td class="num" style="color:${Math.abs(r.drift_pp) < 1 ? "" : (r.drift_pp > 0 ? NEG : POS)}">${
        (r.drift_pp >= 0 ? "+" : "") + r.drift_pp}pp</td>
      <td class="num">${compras[r.ticker] ? `<b style="color:${POS}">${eur(compras[r.ticker])}</b>` : "—"}</td></tr>`).join("");

    box.innerHTML = `
      <div class="rb-bar">
        <label>Voy a aportar <input type="number" id="rb-cash" min="0" step="50" value="${cash || ""}" placeholder="500"> €</label>
        <button type="button" id="rb-go" class="primary-mini">Calcular</button>
      </div>
      ${!filas.length ? `<p class="mut">Escribe un peso objetivo (%) en la tabla de abajo y aquí sale
        cuánto te desvías y qué comprar para corregirlo.</p>` : `
        <div class="ex-h">Qué desvío tienes</div>
        <div class="scroll"><table class="tbl">
          <thead><tr><th>Activo</th><th class="num">Ahora</th><th class="num">Objetivo</th>
            <th class="num">Desvío</th><th class="num">Comprar</th></tr></thead>
          <tbody>${drift}</tbody></table></div>
        ${Object.keys(compras).length ? `<p class="perf-note">Repartiendo <b>${eur(d.cash)}</b> así,
          te acercas al objetivo <b>sin vender nada</b>. En España cada venta con plusvalía
          es un hecho imponible: pagar impuestos hoy para cuadrar unos decimales de peso
          destruye más de lo que corrige.</p>` : ""}
        ${d.targets_sum && Math.abs(d.targets_sum - 100) > 0.5 ? `<p class="mut small">
          Tus objetivos suman ${d.targets_sum}%. Se usan normalizados, así que la proporción
          entre ellos es la que manda.</p>` : ""}
        ${(d.untargeted || []).length ? `<p class="mut small">Sin objetivo asignado:
          ${d.untargeted.map(esc).join(", ")} — no entran en el reparto, y no se cuentan como cero:
          que nadie haya decidido su peso no significa que deban desaparecer.</p>` : ""}`}
      <div class="ex-h" style="margin-top:18px">Tus pesos objetivo</div>
      <div class="scroll"><table class="tbl">
        <thead><tr><th>Activo</th><th class="num">Peso hoy</th><th class="num">Objetivo %</th></tr></thead>
        <tbody>${((P && P.positions) || []).filter((r) => r.qty > 1e-9).map((r) => `<tr>
          <td>${assetCell(r.ticker, r.name, r.kind)}</td>
          <td class="num">${r.weight != null ? r.weight + "%" : "—"}</td>
          <td class="num"><input class="tgt-in" type="number" step="1" min="0" max="100"
            data-ticker="${esc(r.ticker)}" value="${r.target == null ? "" : r.target}" placeholder="—"></td>
        </tr>`).join("")}</tbody></table></div>`;

    $("rb-go").addEventListener("click", loadRebal);
    box.querySelectorAll(".tgt-in").forEach((el) => el.addEventListener("change", async () => {
      const r = await send("/api/cartera/objetivo", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: el.dataset.ticker, target: el.value }) });
      const dd = await r.json();
      if (dd.error) { alert(dd.error); return; }
      P = dd; loadRebal();
    }));
  }

  // ══ si vendo… ═════════════════════════════════════════════════════════
  // La pantalla separa LO EXACTO de LO ESTIMADO con una línea, y no es un
  // adorno: el coste FIFO y el resultado salen del libro y se pueden
  // comprobar; el impuesto depende de una ley que cambia y de cosas que este
  // panel no ve. Presentarlos juntos convertiría una estimación en un dato.
  const FISC = { ticker: null, qty: "", gains: "", losses: "" };

  function renderFiscForm(p) {
    const abiertas = (p.positions || []).filter((r) => r.qty > 1e-9 && r.valued);
    if (!abiertas.length) {
      $("fisc").innerHTML = `<div class="mut">Sin posiciones valorables que simular.</div>`;
      return;
    }
    if (!FISC.ticker || !abiertas.some((r) => r.ticker === FISC.ticker))
      FISC.ticker = abiertas[0].ticker;
    const sel = abiertas.map((r) =>
      `<option value="${esc(r.ticker)}"${r.ticker === FISC.ticker ? " selected" : ""}>${
        esc(r.name || r.ticker)} · ${qty(r.qty)}</option>`).join("");
    $("fisc").innerHTML = `
      <div class="fi-bar">
        <label>Instrumento <select id="fi-tk">${sel}</select></label>
        <label>Vendo <input type="number" id="fi-qty" step="any" min="0"
          value="${FISC.qty}" placeholder="todo"> títulos</label>
        <button type="button" id="fi-go" class="primary-mini">Calcular</button>
      </div>
      <div id="fi-out" class="mut">Elige cuántos títulos y pulsa calcular.</div>`;
    $("fi-tk").addEventListener("change", () => { FISC.ticker = $("fi-tk").value; runFisc(); });
    $("fi-go").addEventListener("click", () => { FISC.qty = $("fi-qty").value; runFisc(); });
  }

  async function runFisc() {
    const out = $("fi-out");
    if (!out) return;
    out.innerHTML = `<span class="mut">Calculando…</span>`;
    const qs = new URLSearchParams({ ticker: FISC.ticker });
    if (FISC.qty !== "") qs.set("qty", FISC.qty);
    if (FISC.gains !== "") qs.set("other_gains", FISC.gains);
    if (FISC.losses !== "") qs.set("pending_losses", FISC.losses);
    let d;
    try { d = await (await fetch("/api/cartera/simular-venta?" + qs)).json(); }
    catch (e) { out.innerHTML = `<span class="mut">No pude calcularlo.</span>`; return; }
    if (d.error) { out.innerHTML = `<span class="mut">${esc(d.error)}</span>`; return; }

    const gana = d.result >= 0;
    const lotes = d.lots.map((l) => `<tr>
      <td>${esc(l.date || "—")}${l.partial ? ' <span class="mut small">(parte)</span>' : ""}</td>
      <td class="num">${qty(l.qty)}</td>
      <td class="num">${eur(l.unit_cost, 4)}</td>
      <td class="num">${eur(l.cost)}</td></tr>`).join("");

    out.innerHTML = `
      <div class="fi-two">
        <div class="fi-block">
          <div class="ex-h">Del libro · exacto</div>
          <div class="vs-row"><span>Vendes</span><b>${qty(d.qty)} de ${qty(d.held)}</b></div>
          <div class="vs-row"><span>Ingresas</span><b>${eur(d.proceeds)}</b></div>
          <div class="vs-row"><span>Coste FIFO de esos títulos</span><b>${eur(d.cost_fifo)}</b></div>
          <div class="vs-row vs-tot"><span>${gana ? "Plusvalía" : "Minusvalía"}</span>
            <b style="color:${gana ? POS : NEG}">${signed(d.result, " €")}</b></div>
          <table class="tbl fi-lots"><thead><tr><th>Lote</th><th class="num">Títulos</th>
            <th class="num">Coste ud.</th><th class="num">Coste</th></tr></thead>
            <tbody>${lotes}</tbody></table>
        </div>
        <div class="fi-block fi-est">
          <div class="ex-h">De la ley · estimación</div>
          ${gana ? `
            <div class="vs-row"><span>Ya realizado este año</span>
              <b>${eur(d.other_gains)}${d.other_gains_auto ? ' <span class="mut small">calculado</span>' : ""}</b></div>
            ${d.losses_used ? `<div class="vs-row"><span>Minusvalías aplicadas</span><b>−${eur(d.losses_used)}</b></div>` : ""}
            <div class="vs-row"><span>Base que tributa</span><b>${eur(d.taxable_base)}</b></div>
            <div class="vs-row vs-tot"><span>Impuesto estimado</span>
              <b style="color:${NEG}">${eur(d.tax)}</b>${d.effective_rate != null
                ? ` <span class="mut small">${d.effective_rate}% del resultado</span>` : ""}</div>
            <div class="vs-row"><span>Te quedarían</span><b>${eur(d.net)}</b></div>`
          : `<p class="mut">Una minusvalía no paga impuesto. ${esc(d.loss_note || "")}</p>`}
          <div class="fi-inputs">
            <label>Otras ganancias del año (€)
              <input type="number" id="fi-g" step="any" min="0" value="${FISC.gains}"
                placeholder="${d.other_gains}"></label>
            <label>Minusvalías pendientes (€)
              <input type="number" id="fi-l" step="any" min="0" value="${FISC.losses}"
                placeholder="0"></label>
          </div>
        </div>
      </div>
      ${d.repurchase.length ? `<p class="perf-note" style="border-color:#cf8b3a;background:color-mix(in srgb,#cf8b3a 8%,transparent)">
        ⚠ Has comprado ${esc(d.ticker)} hace ${d.repurchase[0].days} días
        (${esc(d.repurchase[0].date)}). Con una recompra dentro de los dos meses
        anteriores o posteriores, la minusvalía <b>no se puede computar todavía</b>.
        Y hacia delante este panel no sabe si vas a recomprar.</p>` : ""}
      ${d.short ? `<p class="mut small">⚠ Sólo tienes ${qty(d.held)}: se ha simulado sobre eso.</p>` : ""}
      <p class="mut small"><b>Esto no es asesoramiento fiscal.</b> El impuesto sale de
        los tramos estatales del ahorro de ${d.brackets_year} aplicados a lo que este
        panel ve. No conoce el resto de tus rentas del ahorro fuera de esta cartera,
        ni tus minusvalías de ejercicios anteriores —los dos campos de arriba están
        para que se las digas—, ni si tributas en País Vasco o Navarra, que tienen su
        propio régimen.</p>`;

    ["fi-g", "fi-l"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("change", () => {
        FISC.gains = $("fi-g").value; FISC.losses = $("fi-l").value; runFisc();
      });
    });
  }

  // Un porcentaje de caída no duele hasta que se ve en euros. La traducción va
  // sobre el patrimonio de HOY y se dice — no es lo que se perdió entonces, que
  // fue sobre una cartera más pequeña; es lo que ese mismo golpe costaría ahora.
  const enEuros = (frac) => {
    const v = (ESTADO && ESTADO.value) || (P && P.summary && P.summary.market_value);
    if (frac == null || !v) return "";
    return `<b>Sobre tu patrimonio de hoy, un golpe así son ${eur(Math.abs(frac) * v)}.</b>`;
  };

  // Beta y correlación SIEMPRE juntas. Una beta de 1,2 con correlación 0,3 no
  // dice «se mueve un 20% más que el índice»: dice que el índice explica muy
  // poco de lo que hace esta cartera, y que ese 1,2 es casi ruido. Publicar la
  // beta sola invita justo a la lectura equivocada.
  function betaBloque() {
    const d = PERF;
    if (!d || d.beta == null) return "";
    const c = d.beta_corr, flojo = c != null && c < 0.5;
    return `<div class="vs-row" style="margin-top:12px">
        <span>Beta contra ${esc(d.benchmark_ticker)}</span><b>${d.beta}</b></div>
      <div class="vs-row"><span>Correlación con el índice</span><b>${c}</b></div>
      <p class="mut small">${flojo
        ? `Con una correlación de ${c}, el índice explica <b>poco</b> de lo que hace tu cartera:
           esa beta de ${d.beta} describe una relación débil y conviene no leerla como
           «se mueve un ${Math.round(Math.abs(d.beta - 1) * 100)}% distinto que el índice».`
        : `Por cada 1% que se mueve ${esc(d.benchmark_ticker)}, tu cartera se ha movido
           históricamente un ${d.beta}% en la misma dirección. Con correlación ${c},
           la relación es lo bastante estrecha para leerlo así.`}
        Medido sobre ${d.beta_obs} sesiones y sobre el índice de rendimiento, no sobre
        el saldo: si no, el salto del día de una aportación entraría como movimiento
        de mercado.</p>`;
  }

  // ══ de qué divisas dependes ═══════════════════════════════════════════
  // Dos lecturas, porque cada una engaña por su lado si va sola. La de
  // cotización es exacta y dice poco; la económica abre cada fondo y es la que
  // contesta de qué depende el patrimonio de verdad.
  async function loadCcy() {
    const box = $("ccy"), leg = $("ccy-legend");
    let d;
    try { d = await (await fetch("/api/cartera/divisa")).json(); }
    catch (e) { box.innerHTML = `<div class="mut">No pude calcularlo.</div>`; return; }
    if (d.error) { box.innerHTML = `<div class="mut">${esc(d.error)}</div>`; return; }
    const eco = d.economic || {}, q = d.quote || {};
    const barra = (rows, tot) => rows.length ? `<div class="ccy-bar">${rows.map((r, i) => `
      <span class="ccy-seg" style="flex:${r.pct || 0};background:${CCYCOL(i)}"
        title="${esc(r.ccy)} ${r.pct}%"></span>`).join("")}</div>` : "";
    const lista = (rows) => `<div class="ccy-list">${rows.slice(0, 8).map((r, i) => `
      <div class="ccy-row"><i style="background:${CCYCOL(i)}"></i>
        <span class="ccy-n">${esc(r.ccy)}</span>
        <b>${r.pct == null ? "—" : r.pct + "%"}</b>
        <span class="mut">${eur(r.eur)}</span></div>`).join("")}</div>`;

    leg.textContent = eco.coverage_pct != null ? `transparencia sobre el ${eco.coverage_pct}%` : "";
    box.innerHTML = `
      ${(eco.rows || []).length ? `
        <div class="ex-h">Dónde está el negocio · abriendo cada fondo</div>
        ${barra(eco.rows)}${lista(eco.rows)}
        ${eco.unmapped > 0 ? `<p class="mut small">${eur(eco.unmapped)} en países sin moneda
          en la tabla (${eco.unmapped_countries.map(esc).join(", ")}): no se reparten entre
          las demás, porque hacerlo inflaría en proporción todas las divisas conocidas.</p>` : ""}`
      : `<p class="mut">Todavía no hay transparencia por países con la que calcular esto.</p>`}

      <div class="ex-h" style="margin-top:16px">En qué moneda compras y vendes</div>
      ${barra(q.rows)}${lista(q.rows || [])}
      <p class="mut small">Las dos lecturas <b>no dicen lo mismo, y ninguna sobra</b>.
        Un fondo indexado mundial cotizado en euros aparece abajo al 100% en euros y
        arriba lleva dos tercios de dólares: abajo se ve en qué moneda te cobran,
        arriba de qué depende tu dinero.
        La divisa se asigna por el PAÍS del negocio — una empresa alemana que factura
        en dólares sale entera en euros; afinarlo exigiría la cuenta de resultados de
        cada compañía.</p>`;
  }
  const CCYCOL = (i) => ["#4a90d9", "#3fae6b", "#d99a2b", "#8a63d2", "#cf5b3a",
                         "#4a9e8f", "#b0873f", "#7c828e"][i % 8];

  // ══ qué cuesta ════════════════════════════════════════════════════════
  function renderCostes(p) {
    const s = p.summary || {}, box = $("costes");
    const open = (p.positions || []).filter((r) => r.qty > 1e-9);
    const aport = s.invested || 0;
    const filas = open.map((r) => `<tr>
      <td>${assetCell(r.ticker, r.name, r.kind)}</td>
      <td class="num">${r.fees ? eur(r.fees) : "—"}</td>
      <td class="num"><input class="ter-in" type="number" step="0.01" min="0" max="10"
        data-ticker="${esc(r.ticker)}" value="${r.ter == null ? "" : r.ter}"
        placeholder="—" title="Gastos corrientes anuales en %, del folleto (KID/DFI)"></td>
      <td class="num">${r.ter_year != null ? eur(r.ter_year) : `<span class="mut" title="Sin TER declarado: este coste no está contado en ningún total">sin declarar</span>`}</td>
    </tr>`).join("");

    // Lo que el TER se lleva a 20 años del valor de HOY, sin contar crecimiento.
    // Es la cifra que cambia decisiones: el coste que nunca aparece en un
    // extracto porque no se cobra, se descuenta del valor liquidativo.
    const t = (s.ter_pct || 0) / 100;
    const mv = s.market_value || 0;
    const arrastre = (n) => t > 0 ? mv * (1 - Math.pow(1 - t, n)) : null;

    box.innerHTML = `
      <div class="perf-grid">
        <div class="perf-kpi"><div class="ex-h">Comisiones pagadas</div>
          <div class="perf-big">${eur(s.fees)}</div>
          <div class="mut small">${s.n_ops || 0} operaciones${aport > 0 && s.fees
            ? ` · ${(s.fees / aport * 100).toFixed(2)}% de lo aportado` : ""}</div></div>
        <div class="perf-kpi"><div class="ex-h">Retenido en dividendos</div>
          <div class="perf-big">${eur(s.withheld)}</div>
          <div class="mut small">Impuesto a cuenta, no una comisión: parte se recupera al declarar.</div></div>
        <div class="perf-kpi"><div class="ex-h">Gastos corrientes al año</div>
          <div class="perf-big">${s.ter_year ? eur(s.ter_year) : "—"}</div>
          <div class="mut small">${s.ter_coverage
            ? `Sobre el <b>${s.ter_coverage}%</b> del capital: es lo único con TER declarado.`
            : "Escribe el TER de cada fondo abajo y aparece aquí."}</div></div>
      </div>
      ${arrastre(20) ? `<p class="perf-note">A este ritmo, los gastos corrientes se llevan
        <b>${eur(arrastre(10))}</b> en 10 años y <b>${eur(arrastre(20))}</b> en 20,
        sobre el valor de hoy y sin contar lo que crezca.
        <span class="mut">Es el único coste que no verás nunca en un extracto: no se cobra,
        se descuenta del valor liquidativo todos los días.</span></p>` : ""}
      <div class="scroll"><table class="tbl">
        <thead><tr><th>Activo</th><th class="num">Comisiones</th>
          <th class="num">TER %</th><th class="num">Coste anual</th></tr></thead>
        <tbody>${filas || `<tr><td colspan="4" class="mut">Sin posiciones abiertas.</td></tr>`}</tbody>
      </table></div>`;

    box.querySelectorAll(".ter-in").forEach((el) => {
      el.addEventListener("change", async () => {
        const r = await send("/api/cartera/ter", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker: el.dataset.ticker, ter: el.value }) });
        const d = await r.json();
        if (d.error) { alert(d.error); return; }
        render(d, false);
      });
    });
  }

  // ══ el activo y la divisa ═════════════════════════════════════════════
  function renderFx(p) {
    const rows = (p.positions || []).filter((r) => r.split
      && Math.abs(r.split.currency) > 0.005);
    $("fx-card").hidden = !rows.length;
    if (!rows.length) return;
    const tot = rows.reduce((a, r) => ({ asset: a.asset + r.split.asset,
                                         currency: a.currency + r.split.currency }),
                            { asset: 0, currency: 0 });
    $("fxsplit").innerHTML = `
      <div class="scroll"><table class="tbl">
        <thead><tr><th>Activo</th><th class="num">Puso el activo</th>
          <th class="num">Puso la divisa</th><th class="num">Cambio</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td>${assetCell(r.ticker, r.name, r.kind)}</td>
          <td class="num">${signed(r.split.asset, " €")}</td>
          <td class="num">${signed(r.split.currency, " €")}</td>
          <td class="num mut" title="Cambio medio de compra ${r.split.fx_buy.toFixed(4)} → hoy ${r.split.fx_now.toFixed(4)}">${
            (r.split.fx_change_pct >= 0 ? "+" : "") + r.split.fx_change_pct.toFixed(1)}%</td></tr>`).join("")}
          <tr class="tot-row"><td><b>Total</b></td>
            <td class="num">${signed(tot.asset, " €")}</td>
            <td class="num">${signed(tot.currency, " €")}</td><td></td></tr>
        </tbody></table></div>
      <p class="mut small">Los dos sumandos reconstruyen el resultado no realizado
        <b>al céntimo</b>: no es un reparto aproximado. El de la divisa es lo que ha
        puesto (o quitado) el tipo de cambio entre el día de tus compras y hoy.</p>`;
  }

  // ══ diversificación real ══════════════════════════════════════════════
  async function loadCorr() {
    const leg = $("corr-legend"), box = $("corr");
    let d;
    try { d = await (await fetch("/api/cartera/correlacion")).json(); }
    catch (e) { leg.textContent = ""; box.innerHTML = `<div class="mut">No pude medirla.</div>`; return; }
    if (d.error || d.eff_n_corr == null) {
      leg.textContent = "";
      box.innerHTML = `<div class="mut">${esc(d.why || "Hacen falta al menos dos posiciones con histórico en común.")}</div>`;
      return;
    }
    leg.textContent = `${d.obs} sesiones · último año`;
    const n = d.tickers.length;
    // Verde = se mueven al revés (diversifica). Rojo = son la misma apuesta.
    // La DIAGONAL va en gris: un activo correlaciona 1,00 consigo mismo por
    // definición, y pintarla del rojo más intenso de la tabla dirige la vista
    // justo a las únicas seis casillas que no dicen nada.
    const cell = (v, diag) => {
      if (diag) return `<td class="cm diag">${v.toFixed(2)}</td>`;
      const a = Math.min(1, Math.abs(v));
      const c = v >= 0 ? `rgba(207,91,58,${(a * 0.75).toFixed(2)})` : `rgba(63,174,107,${(a * 0.75).toFixed(2)})`;
      return `<td class="cm" style="background:${c}">${v.toFixed(2)}</td>`;
    };
    const mc = d.most_correlated;
    box.innerHTML = `
      <div class="corr-kpis">
        <div><div class="ex-h">Contando líneas</div><div class="perf-big mut">${d.eff_n_weights}</div></div>
        <div class="corr-arrow">→</div>
        <div><div class="ex-h">Apuestas reales</div><div class="perf-big">${d.eff_n_corr}</div></div>
      </div>
      <p class="mut small">Tienes <b>${n}</b> posiciones que se comportan como
        <b>${d.eff_n_corr}</b> apuestas independientes. Contar líneas no es diversificar:
        cinco fondos del mismo índice son una sola apuesta repartida en cinco filas.</p>
      ${betaBloque()}
      ${mc ? `<p class="perf-note">Lo que más se parece: <b>${esc(mc.a)}</b> y <b>${esc(mc.b)}</b>,
        correlación <b>${mc.rho}</b>.${mc.rho > 0.8 ? " A ese nivel, son prácticamente el mismo activo." : ""}</p>` : ""}
      <div class="scroll"><table class="tbl corr-tbl">
        <thead><tr><th></th>${d.tickers.map((t) => `<th class="num">${esc(t.slice(0, 6))}</th>`).join("")}</tr></thead>
        <tbody>${d.tickers.map((t, i) => `<tr><td class="sym">${esc(t.slice(0, 12))}</td>${
          d.matrix[i].map((v, j) => cell(v, i === j)).join("")}</tr>`).join("")}</tbody></table></div>
      ${(d.excluded || []).length ? `<p class="mut small">⚠ Fuera de la matriz:
        ${d.excluded.map((x) => esc(x.ticker) + " (" + esc(x.why) + ")").join(", ")}.</p>` : ""}`;
  }

  // ── evolución de la cartera + benchmark ──────────────────────────────
  async function loadHistory() {
    const b = ($("bench").value || "SPY").trim();
    try { CH = await (await fetch(`/api/cartera/history?benchmark=${encodeURIComponent(b)}&${rangoQS()}`)).json(); }
    catch (e) { CH = null; }
    drawChart();
    const nota = $("rebase-note");
    if (CH && CH.rebased) {
      // Un tramo recortado lleva el índice RESEMBRADO su primer día. Decirlo
      // no es un tecnicismo: sin ello, alguien podría leer la distancia entre
      // las dos líneas como si viniera de su primera compra.
      nota.innerHTML = `Tramo <b>${esc(CH.dates[0] || "")}</b> → <b>${esc(CH.dates[CH.dates.length - 1] || "")}</b>. `
        + `${esc(CH.benchmark_ticker)} se <b>resiembra</b> el primer día con el valor que tenía tu cartera, `
        + `y recibe tus mismos flujos dentro del tramo: las tres líneas parten del mismo punto. `
        + `«Invertido» es el capital que ya tenías más lo aportado dentro.`;
      nota.hidden = false;
    } else nota.hidden = true;
  }

  $("hrange").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-r]");
    if (!b) return;
    const r = b.dataset.r;
    if (r === "custom") {
      $("hcustom").hidden = !$("hcustom").hidden;
      if (!$("hfrom").value && CH && CH.first) $("hfrom").value = CH.first;
      if (!$("hto").value && CH && CH.last) $("hto").value = CH.last;
      return;
    }
    $("hcustom").hidden = true;
    if (r === RANGO.r) return;
    RANGO.r = r;
    $("hrange").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    loadHistory(); loadPerf();
  });

  $("happly").addEventListener("click", () => {
    const f = $("hfrom").value, t = $("hto").value;
    if (!f) return;
    if (t && t < f) { $("hrangenote").textContent = "La fecha final va después de la inicial."; return; }
    $("hrangenote").textContent = "";
    RANGO.r = "custom"; RANGO.from = f; RANGO.to = t;
    $("hrange").querySelectorAll("button").forEach((x) =>
      x.classList.toggle("on", x.dataset.r === "custom"));
    loadHistory(); loadPerf();
  });

  const SERIES = () => [
    { k: "invested", c: cssv("--faint"), dash: [4, 4], lab: "Invertido" },
    ...(CH && CH.benchmark ? [{ k: "benchmark", c: "#d99a2b", dash: [], lab: CH.benchmark_ticker }] : []),
    { k: "portfolio", c: cssv("--accent"), dash: [], lab: "Cartera" },
  ];
  const lastVal = (a) => { if (!a) return null; for (let i = a.length - 1; i >= 0; i--) if (a[i] != null && isFinite(a[i])) return a[i]; return null; };

  function drawChart() {
    const cv = $("pchart"); if (!cv) return;
    const rect = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    cv.width = rect.width * dpr; cv.height = rect.height * dpr;
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width, H = rect.height; ctx.clearRect(0, 0, W, H);
    if (!CH || !CH.dates || CH.dates.length < 2) {
      ctx.fillStyle = cssv("--muted"); ctx.font = "13px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("Añade movimientos para ver la evolución de la cartera.", W / 2, H / 2);
      $("chart-legend").innerHTML = ""; return;
    }
    const pad = { l: 58, r: 12, t: 12, b: 22 }, dates = CH.dates, n = dates.length, series = SERIES();
    let mn = Infinity, mx = -Infinity;
    series.forEach((s) => (CH[s.k] || []).forEach((v) => { if (v != null && isFinite(v)) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }));
    if (!isFinite(mn)) { mn = 0; mx = 1; }
    const pv = (mx - mn) * 0.06 || 1; mn -= pv; mx += pv;
    const X = (i) => pad.l + i / (n - 1) * (W - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (v - mn) / (mx - mn)) * (H - pad.t - pad.b);
    ctx.strokeStyle = cssv("--border"); ctx.fillStyle = cssv("--faint");
    ctx.font = "10px ui-monospace,monospace"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let g = 0; g <= 4; g++) { const v = mn + (mx - mn) * g / 4, y = Y(v);
      ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillText(kfmt(v), pad.l - 6, y); }
    ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; let ly = null;
    for (let i = 0; i < n; i++) { const yr = dates[i].slice(0, 4); if (yr !== ly) { ly = yr; if (i > 0) ctx.fillText(yr, X(i), H - 6); } }
    series.forEach((s) => { const arr = CH[s.k] || []; ctx.strokeStyle = s.c; ctx.lineWidth = s.k === "portfolio" ? 2 : 1.4;
      ctx.setLineDash(s.dash); ctx.beginPath(); let st = false;
      for (let i = 0; i < n; i++) { const v = arr[i]; if (v == null || !isFinite(v)) continue; const x = X(i), y = Y(v);
        if (!st) { ctx.moveTo(x, y); st = true; } else ctx.lineTo(x, y); } ctx.stroke(); ctx.setLineDash([]); });
    CH._geo = { X, Y, pad, W, H, n, series };
    chartLegend();
  }

  function chartLegend() {
    const pvv = lastVal(CH.portfolio), bv = lastVal(CH.benchmark), iv = lastVal(CH.invested);
    const dot = (c) => `<i style="background:${c}"></i>`;
    const parts = [`<span>${dot(cssv("--accent"))}Cartera <b>${eur(pvv)}</b></span>`];
    if (bv != null) parts.push(`<span>${dot("#d99a2b")}${CH.benchmark_ticker} <b>${eur(bv)}</b></span>`);
    parts.push(`<span>${dot(cssv("--faint"))}Invertido <b>${eur(iv)}</b></span>`);
    if (pvv != null && bv != null) { const d = pvv - bv, pct = bv ? d / bv * 100 : 0;
      parts.push(`<span class="out">vs ${CH.benchmark_ticker}: <b style="color:${d >= 0 ? POS : NEG}">${d >= 0 ? "+" : ""}${eur(d)} (${d >= 0 ? "+" : ""}${pct.toFixed(1)}%)</b></span>`); }
    // Cuando faltan series, la gráfica representa MENOS cartera que la tabla de
    // posiciones. Decirlo es obligatorio: si no, el hueco se lee como pérdida.
    if (CH.excluded && CH.excluded.length) {
      parts.push(`<span class="warn" title="Yahoo no publica serie diaria para estos instrumentos: quedan fuera de la gráfica (valor Y coste), no de tus posiciones">`
        + `⚠ sin histórico: <b>${esc(CH.excluded.join(", "))}</b></span>`);
    }
    // Una serie prestada es legítima pero no es la del propio instrumento:
    // quien mira la gráfica tiene derecho a saber de dónde sale cada línea.
    if (CH.proxied && CH.proxied.length) {
      const via = CH.proxied.map((p) => `${esc(p.ticker)} vía ${esc(p.via)}`).join(", ");
      const dev = Math.max(...CH.proxied.map((p) => p.dev || 0));
      parts.push(`<span class="via" title="Estas posiciones no tienen serie diaria propia. Se usa la del mismo instrumento en otra plaza, en la misma divisa (desvío máx. ${(dev * 100).toFixed(2)}%). La valoración de hoy sigue siendo la del instrumento real.">`
        + `↪ histórico: <b>${via}</b></span>`);
    }
    $("chart-legend").innerHTML = parts.join("");
  }

  const cv = $("pchart"), tip = $("ptip");
  cv.addEventListener("mousemove", (e) => {
    if (!CH || !CH._geo) return; const g = CH._geo, r = cv.getBoundingClientRect();
    let i = Math.round((e.clientX - r.left - g.pad.l) / (g.W - g.pad.l - g.pad.r) * (g.n - 1));
    i = Math.max(0, Math.min(g.n - 1, i));
    drawChart(); const ctx = cv.getContext("2d"), px = g.X(i);
    ctx.strokeStyle = cssv("--border"); ctx.beginPath(); ctx.moveTo(px, g.pad.t); ctx.lineTo(px, g.H - g.pad.b); ctx.stroke();
    g.series.forEach((s) => { const v = (CH[s.k] || [])[i]; if (v == null || !isFinite(v)) return;
      ctx.beginPath(); ctx.arc(px, g.Y(v), 3, 0, 7); ctx.fillStyle = s.c; ctx.fill(); });
    const rows = g.series.map((s) => { const v = (CH[s.k] || [])[i]; return v == null ? "" : `<div class="tr"><span>${s.lab}</span><b>${eur(v)}</b></div>`; }).join("");
    tip.innerHTML = `<div class="td">${CH.dates[i]}</div>${rows}`; tip.hidden = false;
    tip.style.left = Math.min(cv.parentElement.clientWidth - 165, Math.max(0, px + 10)) + "px"; tip.style.top = "10px";
  });
  cv.addEventListener("mouseleave", () => { tip.hidden = true; drawChart(); });
  // ── ojo: ocultar los importes ────────────────────────────────────────────
  const eyeBtn = $("eye");
  function applyHidden(on, repaint) {
    document.body.classList.toggle("amounts-hidden", on);
    const lbl = on ? "Mostrar importes" : "Ocultar importes";
    eyeBtn.setAttribute("aria-pressed", on ? "true" : "false");
    eyeBtn.title = lbl;
    eyeBtn.querySelector(".eye-lbl").textContent = lbl;
    if (!repaint) return;
    if (P) render(P, false, false);         // mismos datos, otro aspecto
    drawChart();
  }
  eyeBtn.addEventListener("click", () => {
    const on = !hidden();
    // Es una preferencia de pantalla y se queda en la pantalla: localStorage, no
    // el servidor. Y si el navegador la deniega (modo privado), el ojo funciona
    // igual durante la sesión en vez de romperse.
    try { localStorage.setItem("cartera-hide", on ? "1" : "0"); } catch (e) { /* sin persistencia */ }
    applyHidden(on, true);
  });
  // Se recupera ANTES del primer render: nada de pintar los importes y taparlos
  // un instante después, que es justo lo que se quería evitar.
  applyHidden((() => { try { return localStorage.getItem("cartera-hide") === "1"; } catch (e) { return false; } })(), false);

  // ── "comparar con": buscador con desplegable ─────────────────────────────
  // Los presets son el punto de partida, no el límite: cualquier cotización de
  // Yahoo sirve de referencia. Escribir el símbolo a pelo y pulsar Enter ya
  // funcionaba, pero obligaba a saberlo de memoria — un datalist solo ofrece sus
  // seis opciones fijas y calla ante todo lo demás. El desplegable lo dice.
  const BENCH_PRESETS = [
    { symbol: "SPY", name: "S&P 500", kind: "ETF" },
    { symbol: "QQQ", name: "Nasdaq 100", kind: "ETF" },
    { symbol: "URTH", name: "MSCI World", kind: "ETF" },
    { symbol: "^GDAXI", name: "DAX", kind: "Índice" },
    { symbol: "GLD", name: "Oro", kind: "ETF" },
    { symbol: "^STOXX50E", name: "EuroStoxx 50", kind: "Índice" },
  ];
  const bTk = $("bench"), bRes = $("bench-results"), bWrap = $("bench-wrap");
  let benchT = null, bItems = [], bOn = -1;

  const benchPresets = (q) => {
    const n = q.toLowerCase();
    return BENCH_PRESETS.filter((p) => !n || p.symbol.toLowerCase().includes(n)
      || p.name.toLowerCase().includes(n));
  };

  function benchShow(items, note) {
    bItems = items; bOn = -1;
    bRes.innerHTML = items.length
      // Sin etiqueta va una celda vacía, no ninguna: la fila es una rejilla de
      // tres columnas y quitarle la primera desplaza nombre y símbolo.
      ? items.map((x, i) => `<div class="r-item" data-i="${i}">
          ${kbadge(x.kind || "") || "<span></span>"}<span class="r-name">${esc(x.name || x.symbol)}</span>
          <span class="r-sym">${esc(x.symbol)}${x.exchange ? " · " + esc(x.exchange) : ""}</span></div>`).join("")
      : `<div class="r-empty">${esc(note || "Sin resultados. Escribe el símbolo y pulsa Enter.")}</div>`;
    bRes.hidden = false;
    // `mousedown` y no `click`: el click llega después del blur, y para entonces
    // la lista ya se habría cerrado bajo el cursor.
    bRes.querySelectorAll(".r-item").forEach((el) => el.addEventListener("mousedown", (ev) => {
      ev.preventDefault(); benchPick(items[+el.dataset.i]);
    }));
  }

  function benchPick(x) {
    if (!x) return;
    bTk.value = x.symbol; bRes.hidden = true; bOn = -1;
    loadHistory();
  }

  function benchMove(d) {
    const els = [...bRes.querySelectorAll(".r-item")];
    if (!els.length) return;
    bOn = bOn < 0 ? (d > 0 ? 0 : els.length - 1) : (bOn + d + els.length) % els.length;
    els.forEach((el, i) => el.classList.toggle("on", i === bOn));
    els[bOn].scrollIntoView({ block: "nearest" });
  }

  // Al entrar se ofrecen TODOS los presets, sin filtrar por lo que ya hay dentro:
  // el valor actual es lo que ya está pintado, no una búsqueda. Filtrarlo dejaría
  // la lista con una sola línea — la que ya estás viendo en el gráfico.
  let bFresh = false;
  bTk.addEventListener("focus", () => { bFresh = true; benchShow(BENCH_PRESETS); });
  // Y el texto queda seleccionado, así que escribir lo REEMPLAZA. Sin esto el
  // clic deja el cursor donde caiga y "SPY" + "tsla" sale "SPtslaY". El
  // navegador deshace la selección en el mouseup del propio clic, de ahí que se
  // rehaga ahí y solo la primera vez.
  bTk.addEventListener("mouseup", (e) => { if (bFresh) { e.preventDefault(); bTk.select(); bFresh = false; } });
  bTk.addEventListener("blur", () => { bFresh = false; });
  bTk.addEventListener("input", () => {
    clearTimeout(benchT);
    const q = bTk.value.trim();
    const pre = benchPresets(q);
    benchShow(pre, q.length < 2 ? "Escribe al menos 2 letras" : "Buscando…");
    if (q.length < 2) return;
    benchT = setTimeout(async () => {
      let r;
      try { r = await (await fetch("/api/search?q=" + encodeURIComponent(q))).json(); } catch (e) { return; }
      if (bTk.value.trim() !== q) return;                 // respuesta obsoleta
      const seen = new Set(pre.map((p) => p.symbol.toUpperCase()));
      benchShow(pre.concat((r.results || []).filter((x) => !seen.has(x.symbol.toUpperCase()))));
    }, 260);
  });
  bTk.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); benchMove(e.key === "ArrowDown" ? 1 : -1); return; }
    if (e.key === "Escape") { bRes.hidden = true; return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    // Con una opción marcada manda el desplegable; sin marcar, el texto tal cual
    // sigue valiendo. Quien ya se sabe el ticker no tiene por qué mirar la lista.
    if (!bRes.hidden && bOn >= 0) benchPick(bItems[bOn]);
    else { bRes.hidden = true; loadHistory(); }
  });
  // Salir del campo también aplica lo escrito, pero solo si de verdad cambió:
  // tras un Enter o una selección el gráfico ya es ese, y el blur posterior
  // repetiría la consulta entera para pintar exactamente lo mismo.
  bTk.addEventListener("change", () => {
    bRes.hidden = true;
    const want = (bTk.value.trim() || "SPY").toUpperCase();
    if (!CH || String(CH.benchmark_ticker || "").toUpperCase() !== want) loadHistory();
  });
  window.addEventListener("resize", drawChart);

  function importMsg(imp) {
    const el = $("import-msg");
    let h = `<b style="color:${POS}">✓ ${imp.added} movimientos importados</b> · columnas: ${esc(imp.detected.join(", ")) || "—"}`;
    if (imp.skipped_duplicates) {
      // Re-importing the same file is the easy mistake and it doubles every
      // position. Say what was skipped and how to force it in.
      const ej = (imp.duplicates || []).map((d) => `${esc(d.date)} ${esc(d.ticker)} ${qty(d.quantity)}@${money(d.price, 4)}`).join("; ");
      h += `<div class="warn small">⚠ ${imp.skipped_duplicates} filas omitidas por estar ya guardadas${ej ? ": " + ej : ""}`
        + `${imp.skipped_duplicates > (imp.duplicates || []).length ? " …" : ""}`
        + `<br>Si son compras reales repetidas, vuelve a importar con <code>?duplicates=allow</code>.</div>`;
    }
    if (imp.n_errors) h += `<div class="mut small">${imp.n_errors} filas con problemas: ${esc(imp.errors.join("; "))}</div>`;
    el.innerHTML = h;
    setTimeout(() => { el.innerHTML = ""; }, 20000);
  }

  // ── buscador de instrumentos (ETF / fondo / acción europeos) ──
  const fTk = $("f-ticker"), fRes = $("f-results"), fPick = $("f-picked");
  let searchT = null;
  fTk.addEventListener("input", () => {
    if (sel && fTk.value.trim() !== sel.symbol) { sel = null; fPick.hidden = true; }
    clearTimeout(searchT);
    const q = fTk.value.trim();
    if (q.length < 2) { fRes.hidden = true; return; }
    searchT = setTimeout(() => doSearch(q), 260);
  });
  async function doSearch(q) {
    let r;
    try { r = await (await fetch("/api/search?q=" + encodeURIComponent(q))).json(); } catch (e) { return; }
    if (fTk.value.trim() !== q) return;                 // stale response
    const res = r.results || [];
    if (!res.length) {
      fRes.innerHTML = `<div class="r-empty">Sin resultados. Puedes escribir el símbolo o ISIN directamente.</div>`;
      fRes.hidden = false; return;
    }
    fRes.innerHTML = res.map((x, i) => `<div class="r-item" data-i="${i}">
      ${kbadge(x.kind) || "<span></span>"}<span class="r-name">${esc(x.name)}</span>
      <span class="r-sym">${esc(x.symbol)}${x.exchange ? " · " + esc(x.exchange) : ""}</span></div>`).join("");
    fRes.hidden = false;
    fRes.querySelectorAll(".r-item").forEach((el) => el.addEventListener("click", () => pick(res[+el.dataset.i])));
  }
  // Escribir el símbolo a mano es tan legítimo como elegirlo de la lista, y era
  // justo el camino por el que nadie llegaba a ver la divisa. Al salir del
  // campo, no en cada tecla: el buscador ya se encarga de lo que se teclea.
  fTk.addEventListener("blur", () => {
    const v = fTk.value.trim();
    if (v && (!sel || sel.symbol !== v)) showCcy(v);
    else if (!v) $("f-ccy").hidden = true;
  });

  function pick(x) {
    sel = { symbol: x.symbol, name: x.name, kind: x.kind };
    fTk.value = x.symbol; fRes.hidden = true;
    fPick.innerHTML = `${kbadge(x.kind)} <b>${esc(x.name)}</b>`; fPick.hidden = false;
    showCcy(x.symbol);
  }

  // ── en qué moneda se teclea el precio ─────────────────────────────────
  // El precio siempre ha sido el de la divisa NATIVA del instrumento, y la
  // pantalla no lo decía en ninguna parte. Quien copiaba el importe en euros
  // que le cobró su bróker por una acción estadounidense se metía el error del
  // EURUSD entero en el coste, y el número resultante era perfectamente
  // plausible — que es lo que lo hacía caro.
  async function showCcy(symbol) {
    const box = $("f-ccy");
    box.hidden = true; box.className = "ccy-hint";
    if (!symbol) return;
    let d;
    try { d = await (await fetch("/api/instrumento?symbol=" + encodeURIComponent(symbol))).json(); }
    catch (e) { return; }
    if (fTk.value.trim().toUpperCase() !== symbol.toUpperCase()) return;   // stale
    if (!d.ccy) return;
    box.textContent = "en " + d.ccy;
    box.title = `Este instrumento cotiza en ${d.ccy}. Teclea el precio TAL CUAL viene en la operación, sin convertirlo: la conversión a euros la hace el panel con el cambio del día de la operación.`;
    // GBp cotiza en peniques, no en libras. Es la trampa clásica de una plaza
    // de Londres y se avisa aparte, porque el error no es del 20%: es de 100x.
    if (d.factor && d.factor !== 1) {
      box.textContent = `en ${d.ccy} (¡peniques!)`;
      box.classList.add("warn");
      box.title = `${d.ccy} cotiza en centésimas de ${d.base_ccy}. Un precio de 850 son 8,50 ${d.base_ccy}.`;
    }
    box.hidden = false;
  }

  // ── el formulario cambia de forma según lo que se apunte ──────────────
  // Un dividendo no tiene ni cantidad ni precio: tiene un IMPORTE y una
  // retención. Reusar las mismas casillas con las mismas etiquetas es como se
  // acaba apuntando un cobro de 45 € como una compra de 45 títulos.
  function sideMode() {
    const div = $("f-side").value === "div";
    $("f-qty-lbl").textContent = div ? "Títulos (opcional)" : "Cantidad";
    $("f-price-lbl").textContent = div ? "Importe" : "Precio";
    $("f-fee-lbl").textContent = div ? "Retención" : "Comisión";
    $("f-qty").required = !div;
    $("f-qty").placeholder = div ? "déjalo vacío si sólo sabes el total" : "10";
    $("f-price").placeholder = div ? "45.20" : "450.20";
    $("f-ccy").hidden = div ? true : $("f-ccy").hidden;
  }
  $("f-side").addEventListener("change", sideMode);

  // ── corregir un movimiento en vez de borrarlo ─────────────────────────
  function startEdit(m) {
    if (!m) return;
    EDITING = m.id;
    $("f-date").value = m.date || "";
    fTk.value = m.ticker || "";
    $("f-side").value = m.side || "buy";
    $("f-qty").value = m.quantity ?? "";
    $("f-price").value = m.price ?? "";
    $("f-fee").value = m.fee ?? "";
    $("f-note").value = m.note || "";
    // El instrumento se conserva tal cual salvo que se elija otro: `sel` queda
    // a null y el PATCH sólo manda el ticker, así que el nombre y el tipo
    // guardados no se pisan con los de una búsqueda que nadie hizo.
    sel = null; fPick.hidden = true; fRes.hidden = true;
    sideMode(); showCcy(m.ticker);
    $("f-submit").textContent = "Guardar cambios";
    $("f-cancel").hidden = false;
    document.getElementById("add-form").classList.add("editing");
    $("f-price").focus();
    if (P) render(P, false, false);
  }

  function cancelEdit(repaint = true) {
    EDITING = null;
    ["f-ticker", "f-qty", "f-price", "f-fee", "f-note"].forEach((i) => ($(i).value = ""));
    sel = null; fPick.hidden = true; fRes.hidden = true; $("f-ccy").hidden = true;
    $("f-submit").textContent = "Añadir";
    $("f-cancel").hidden = true;
    document.getElementById("add-form").classList.remove("editing");
    sideMode();
    if (repaint && P) render(P, false, false);
  }
  $("f-cancel").addEventListener("click", () => cancelEdit());
  // Un clic fuera cierra cada lista por su cuenta: son dos buscadores distintos
  // y abrir uno tiene que cerrar el otro, no dejar dos desplegables encendidos.
  document.addEventListener("click", (e) => {
    const w = e.target.closest(".search-wrap");
    if (w !== fTk.closest(".search-wrap")) fRes.hidden = true;
    if (w !== bWrap) bRes.hidden = true;
  });

  // alta manual — y corrección, que es el mismo formulario con otro destino
  $("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { date: $("f-date").value, ticker: fTk.value, side: $("f-side").value,
      quantity: $("f-qty").value, price: $("f-price").value, fee: $("f-fee").value, note: $("f-note").value };
    // Una cantidad vacía sólo es legítima en un dividendo (el servidor la toma
    // como una unidad al importe). En cualquier otro caso mandar "" convertiría
    // la casilla en blanco en un cero, así que se queda fuera del cuerpo.
    if (body.quantity === "" && body.side === "div") delete body.quantity;
    if (sel && sel.symbol === fTk.value.trim()) { body.name = sel.name; body.kind = sel.kind; body.symbol = 1; }
    const editing = EDITING;
    status(editing ? "Guardando cambios…" : "Guardando…");
    const r = await send(editing ? "/api/cartera/" + editing : "/api/cartera",
      { method: editing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.error) { status(""); alert(d.error); return; }
    cancelEdit(false);
    render(d); status("");
  });

  // importar
  const fileInput = $("file"), drop = $("drop");
  $("pick").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => fileInput.files[0] && upload(fileInput.files[0]));
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));

  async function upload(file) {
    status("Importando " + file.name + "…");
    const fd = new FormData(); fd.append("file", file);
    const r = await send("/api/cartera/upload", { method: "POST", body: fd });
    const d = await r.json();
    status("");
    if (d.error) { $("import-msg").innerHTML = `<b style="color:${NEG}">✗ ${d.error}</b>` + (d.errors ? `<div class="mut small">${d.errors.join("; ")}</div>` : ""); return; }
    render(d);
  }

  async function del(id) {
    status("Eliminando…");
    // Si se borra justo el que estaba abierto en el formulario, la edición se
    // queda apuntando a un `id` que ya no existe y el siguiente "Guardar" da un
    // 404 sin que se vea por qué.
    if (EDITING != null && String(EDITING) === String(id)) cancelEdit(false);
    render(await (await send("/api/cartera/" + id, { method: "DELETE" })).json());
    status("");
  }
  $("clear").addEventListener("click", async () => {
    if (!confirm("¿Borrar TODOS los movimientos? Esto no se puede deshacer.")) return;
    render(await (await send("/api/cartera/clear", { method: "POST" })).json());
  });

  // plantilla CSV
  $("tpl").addEventListener("click", (e) => {
    e.preventDefault();
    const csv = "fecha,ticker,tipo,cantidad,precio,comision,nota\n" +
      "2024-01-15,SPY,compra,10,450.20,1.5,ejemplo\n" +
      "2024-06-01,SPY,venta,4,510.00,1.5,toma parcial\n" +
      // En un dividendo la columna «comision» es la RETENCIÓN, y la cantidad
      // puede ir a 1 con el total en «precio» si el extracto no da el importe
      // por título. Va en la plantilla porque un tipo de movimiento que nadie
      // sabe escribir es un tipo de movimiento que nadie apunta.
      "2024-07-10,SPY,dividendo,6,1.75,1.58,retención 15%\n";
    const b = new Blob([csv], { type: "text/csv" }); const u = URL.createObjectURL(b);
    const a = document.createElement("a"); a.href = u; a.download = "plantilla_cartera.csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(u), 1000);
  });

  // fecha por defecto = hoy
  $("f-date").value = new Date().toISOString().slice(0, 10);
  load();
})();
