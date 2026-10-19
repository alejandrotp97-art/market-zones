/* Orden de las tarjetas de /cartera, a gusto de quien la mira.
 *
 * Cada tarjeta se arrastra por su asa (⠿) dentro de su columna; con el asa
 * enfocada, ↑ y ↓ la mueven de una en una. El orden se guarda en este
 * navegador como lista de claves (el `id` de la tarjeta o su `data-card`): es
 * una preferencia de pantalla, como ocultar importes, no un dato de la cartera.
 *
 * Arrastre con eventos de puntero y no con el drag&drop de HTML: éste no anda
 * con el dedo en buena parte de los móviles, y además chocaría con la zona de
 * «arrastra un archivo aquí» de importar, que escucha esos mismos eventos.
 *
 * Sólo dentro de la columna: el raíl mide 400 px y una gráfica ancha metida
 * ahí no se redibuja a su nuevo tamaño.
 */
(() => {
  "use strict";
  const KEY = "cartera-orden";
  const shell = document.querySelector(".shell");
  if (!shell) return;
  const cols = [...shell.children].filter(c => c.matches(".col-main, .rail"));
  const colName = c => (c.classList.contains("rail") ? "rail" : "main");
  const keyOf = e => e.id || e.dataset.card || "";
  const items = c => [...c.children].filter(e => e.matches(".card, .hero") && keyOf(e));
  const visible = c => items(c).filter(e => !e.hidden);
  const defaults = {};
  cols.forEach(c => { defaults[colName(c)] = items(c).map(keyOf); });

  function read() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; }
  }
  function write(v) {
    try {
      if (v) localStorage.setItem(KEY, JSON.stringify(v)); else localStorage.removeItem(KEY);
    } catch (e) { /* sin almacenamiento: el orden dura lo que la pestaña */ }
  }

  // El orden guardado, sin claves que ya no existen; una tarjeta nueva (que no
  // estaba cuando se guardó) entra detrás de la que la precede por defecto.
  function merge(saved, def) {
    const out = (Array.isArray(saved) ? saved : [])
      .filter((k, i, a) => def.includes(k) && a.indexOf(k) === i);
    def.forEach((k, i) => {
      if (out.includes(k)) return;
      const prev = def.slice(0, i).reverse().find(p => out.includes(p));
      out.splice(prev ? out.indexOf(prev) + 1 : 0, 0, k);
    });
    return out;
  }

  function apply(order) {
    cols.forEach(c => {
      const byKey = new Map(items(c).map(e => [keyOf(e), e]));
      merge(order && order[colName(c)], defaults[colName(c)]).forEach(k => c.appendChild(byKey.get(k)));
    });
  }

  const reset = document.createElement("div");
  reset.className = "orden-reset";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "link";
  resetBtn.textContent = "Restablecer el orden de las tarjetas";
  reset.append(resetBtn);
  shell.after(reset);

  function isCustom(v) {
    return cols.some(c => v[colName(c)].join() !== defaults[colName(c)].join());
  }
  function save() {
    const v = {};
    cols.forEach(c => { v[colName(c)] = items(c).map(keyOf); });
    const custom = isCustom(v);
    write(custom ? v : null);
    reset.hidden = !custom;
  }
  resetBtn.addEventListener("click", () => { apply(null); save(); });

  function addGrip(card) {
    const g = document.createElement("span");
    const title = (card.querySelector(".card-h > span, b")?.textContent || "").trim();
    g.className = "grip";
    g.textContent = "⠿";
    g.tabIndex = 0;
    g.setAttribute("role", "button");
    g.setAttribute("aria-label", title ? `Mover la tarjeta «${title}»` : "Mover la tarjeta");
    g.title = "Arrastra para cambiarla de sitio (o ↑ ↓ con el teclado)";
    const head = card.querySelector(":scope > .card-h > span:first-child");
    if (head) head.prepend(g);
    else { card.classList.add("has-grip"); card.append(g); }
  }

  let drag = null;

  // Mientras se arrastra, la tarjeta se coloca delante de la primera hermana
  // visible cuya mitad queda por debajo del puntero.
  function place() {
    const { card, col, y } = drag;
    const others = visible(col).filter(x => x !== card);
    const before = others.find(x => {
      const r = x.getBoundingClientRect();
      return y < r.top + r.height / 2;
    });
    if (before) {
      if (card.nextElementSibling !== before) col.insertBefore(card, before);
    } else if (others.length && others[others.length - 1].nextElementSibling !== card) {
      others[others.length - 1].after(card);
    }
  }

  // Cerca del borde de la ventana la página se desplaza sola, para poder
  // llevar una tarjeta de abajo arriba sin soltarla.
  function tick() {
    if (!drag) return;
    const edge = 70, dy = drag.y < edge ? -14 : drag.y > innerHeight - edge ? 14 : 0;
    if (dy) { scrollBy(0, dy); place(); }
    requestAnimationFrame(tick);
  }

  function end() {
    if (!drag) return;
    drag.card.classList.remove("dragging");
    document.body.classList.remove("ordenando");
    drag = null;
    save();
  }

  document.addEventListener("pointerdown", e => {
    const g = e.target.closest(".grip");
    if (!g || e.button > 0) return;
    const card = g.closest(".card, .hero");
    if (!card || !cols.includes(card.parentElement)) return;
    e.preventDefault();
    g.setPointerCapture?.(e.pointerId);
    drag = { card, col: card.parentElement, y: e.clientY };
    card.classList.add("dragging");
    document.body.classList.add("ordenando");
    requestAnimationFrame(tick);
  });
  document.addEventListener("pointermove", e => {
    if (!drag) return;
    drag.y = e.clientY;
    place();
  });
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);

  // El asa del enlace «Diario y movimientos» vive dentro del enlace: pulsarla
  // no debe navegar.
  document.addEventListener("click", e => {
    if (e.target.closest(".grip")) e.preventDefault();
  }, true);

  document.addEventListener("keydown", e => {
    const g = e.target.closest?.(".grip");
    if (!g || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    const card = g.closest(".card, .hero"), col = card.parentElement;
    const vis = visible(col), i = vis.indexOf(card);
    const j = e.key === "ArrowUp" ? i - 1 : i + 1;
    e.preventDefault();
    if (j < 0 || j >= vis.length) return;
    if (j < i) col.insertBefore(card, vis[j]); else vis[j].after(card);
    g.focus();
    card.scrollIntoView({ block: "nearest" });
    save();
  });

  cols.forEach(c => items(c).forEach(addGrip));
  apply(read());
  save();
})();
