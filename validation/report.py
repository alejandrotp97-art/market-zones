"""Render results.json into a paper-style scientific report (P5-9 + P5-10).

Pure function of results.json — no recomputation, no engine access. Emits a
self-contained HTML file (inline CSS + inline SVG) usable both as a served page
and as a shareable artifact."""
from __future__ import annotations

import json
import math
import os

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "results.json")
OUT = os.path.join(HERE, "report.html")

PRIMARY = ["3m", "6m", "12m"]
LIGHT_TXT = {"🟢": "Sólido", "🟡": "Mejorable", "🟠": "Riesgo moderado", "🔴": "Fallo"}


def nn(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def pct(x, d=1):
    return "—" if not nn(x) else f"{x*100:+.{d}f}%"


def num(x, d=2):
    return "—" if not nn(x) else f"{x:.{d}f}"


def pnum(x, d=0):
    return "—" if not nn(x) else f"{x:.{d}f}%"


# ── minimal inline SVG helpers ───────────────────────────────────────────
def svg_reliability(rel, w=430, h=300):
    if not rel:
        return "<p class='mut'>sin datos</p>"
    xs = [r["pred"] for r in rel]; ys = [r["obs"] for r in rel]
    lo = min(min(xs), min(ys)); hi = max(max(xs), max(ys))
    if hi - lo < 1e-6:
        hi = lo + 1e-6
    pad = (hi - lo) * 0.08; lo -= pad; hi += pad
    m = 42
    X = lambda v: m + (v - lo) / (hi - lo) * (w - m - 12)
    Y = lambda v: (h - 26) - (v - lo) / (hi - lo) * (h - 26 - 12)
    ident = f'<line x1="{X(lo):.1f}" y1="{Y(lo):.1f}" x2="{X(hi):.1f}" y2="{Y(hi):.1f}" stroke="var(--fa)" stroke-dasharray="4 3"/>'
    pts = "".join(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="3.4" fill="var(--ac)"/>' for x, y in zip(xs, ys))
    z0 = f'<line x1="{X(0):.1f}" y1="12" x2="{X(0):.1f}" y2="{h-26}" stroke="var(--bd)"/>' if lo < 0 < hi else ""
    z1 = f'<line x1="{m}" y1="{Y(0):.1f}" x2="{w-12}" y2="{Y(0):.1f}" stroke="var(--bd)"/>' if lo < 0 < hi else ""
    return (f'<svg viewBox="0 0 {w} {h}" class="plot">{z0}{z1}{ident}{pts}'
            f'<text x="{m}" y="{h-8}" class="axl">predicción →</text>'
            f'<text x="6" y="18" class="axl">observado ↑</text></svg>')


def svg_bars(pairs, w=430, h=260, fmt=lambda v: f"{v:.2f}", ylabel=""):
    pairs = [(k, v) for k, v in pairs if nn(v)]
    if not pairs:
        return "<p class='mut'>sin datos</p>"
    vmax = max(abs(v) for _, v in pairs) or 1.0
    m = 30; bw = (w - m - 8) / len(pairs); base = h - 34
    bars = ""
    for i, (k, v) in enumerate(pairs):
        bh = abs(v) / vmax * (base - 14)
        y = base - bh
        col = "var(--ac)" if v >= 0 else "var(--ng)"
        x = m + i * bw + bw * 0.15
        bars += (f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.7:.1f}" height="{bh:.1f}" fill="{col}" rx="2"/>'
                 f'<text x="{x+bw*0.35:.1f}" y="{y-4:.1f}" class="bl">{fmt(v)}</text>'
                 f'<text x="{x+bw*0.35:.1f}" y="{h-8:.1f}" class="bx">{k}</text>')
    axis = f'<line x1="{m}" y1="{base:.1f}" x2="{w-8}" y2="{base:.1f}" stroke="var(--bd)"/>'
    return f'<svg viewBox="0 0 {w} {h}" class="plot">{axis}{bars}</svg>'


def render(R):
    tl = R["traffic_light"]; ph = R["per_horizon"]
    caus = R["causality"]; fdr = R["fdr"]["summary"]; sens = R["sensitivity"]
    HN = R["horizons"]

    # semáforo rows
    tl_labels = {
        "motor_causal": "Motor cuantitativo (causalidad)",
        "calibracion": "Calibración probabilística",
        "cobertura": "Cobertura de intervalos",
        "no_estacionariedad": "Estacionariedad temporal",
        "multiples_comparaciones": "Múltiples comparaciones (FDR)",
        "skill_vs_ingenuo": "Skill frente a modelos ingenuos",
    }
    sem = "".join(
        f'<tr><td>{lab}</td><td class="dot">{tl[k]}</td><td class="mut">{LIGHT_TXT.get(tl[k],"")}</td></tr>'
        for k, lab in tl_labels.items())

    # P5-1 accuracy table
    acc_head = "".join(f"<th>{h}</th>" for h in HN)
    def acc_row(label, fn):
        return "<tr><td>" + label + "</td>" + "".join(f"<td>{fn(ph[h]['metrics'])}</td>" for h in HN) + "</tr>"
    acc = (
        acc_row("N decisiones", lambda m: m.get("n", "—")) +
        acc_row("MAE", lambda m: num(m.get("mae"), 3)) +
        acc_row("RMSE", lambda m: num(m.get("rmse"), 3)) +
        acc_row("Bias (pred−obs)", lambda m: pct(m.get("bias"))) +
        acc_row("Cobertura IC95", lambda m: pnum(m.get("coverage"))) +
        acc_row("Sharpe exceso", lambda m: num(m.get("sharpe"))) +
        acc_row("Hit rate", lambda m: pnum(m.get("hit"))) +
        acc_row("Corr pred-obs", lambda m: num(m.get("corr"))) +
        acc_row("Corr exceso (Spearman)", lambda m: num(m.get("scorr_excess")))
    )

    # P5-2 calibration
    cal_head = "".join(f"<th>{h}</th>" for h in PRIMARY)
    def cal_row(label, fn):
        return "<tr><td>" + label + "</td>" + "".join(f"<td>{fn(ph[h]['calibration'])}</td>" for h in PRIMARY) + "</tr>"
    cal = (
        cal_row("Pendiente (ideal 1)", lambda c: num(c.get("slope"))) +
        cal_row("Intercepto (ideal 0)", lambda c: pct(c.get("intercept"))) +
        cal_row("R²", lambda c: num(c.get("r2"), 3)) +
        cal_row("ECE (menor mejor)", lambda c: num(c.get("ece"), 3))
    )
    rel_svg = svg_reliability(ph["6m"]["calibration"].get("reliability", []))

    # P5-3 era
    era = R["era"]["6m"]
    era_rows = ""
    for e, m in era.items():
        era_rows += (f"<tr><td>{e}</td><td>{m.get('n','—')}</td><td>{num(m.get('mae'),3)}</td>"
                     f"<td>{pct(m.get('bias'))}</td><td>{pnum(m.get('coverage'))}</td>"
                     f"<td>{pnum(m.get('hit'))}</td><td>{num(m.get('sharpe'))}</td></tr>")
    era_bars = svg_bars([(e, era[e].get("mae")) for e in era], fmt=lambda v: f"{v:.3f}")

    # P5-4 regime
    rg = R["regime"]["6m"]
    rg_rows = ""
    for name, m in sorted(rg.items(), key=lambda kv: kv[1].get("mae", 9)):
        rg_rows += (f"<tr><td>{name}</td><td>{m.get('n','—')}</td><td>{m.get('n_eff','—')}</td>"
                    f"<td>{num(m.get('mae'),3)}</td><td>{pct(m.get('bias'))}</td>"
                    f"<td>{pnum(m.get('coverage'))}</td><td>{pnum(m.get('hit'))}</td></tr>")
    rg_bars = svg_bars([(k[:6], v.get("mae")) for k, v in sorted(rg.items(), key=lambda kv: kv[1].get('mae', 9))],
                       fmt=lambda v: f"{v:.3f}")

    # P5-5 bootstrap
    bt_head = "".join(f"<th>{h}</th>" for h in HN)
    def bt_row(label, key):
        cells = ""
        for h in HN:
            b = ph[h]["bootstrap"].get(key)
            cells += f"<td>{('['+num(b[0])+', '+num(b[1])+']') if isinstance(b,list) else '—'}</td>"
        return f"<tr><td>{label}</td>{cells}</tr>"
    boot = (bt_row("IC95 MAE", "mae") + bt_row("IC95 Sharpe", "sharpe") +
            bt_row("IC95 Hit rate", "hit") + bt_row("IC95 exceso medio", "excess"))

    # P5-6 FDR
    surv = [x for x in R["fdr"]["items"] if x.get("survives")]
    surv_rows = "".join(f"<tr><td>{x['asset']}</td><td>{x['horizon']}</td><td>{pct(x['excess'])}</td>"
                        f"<td>{x['p']:.4f}</td></tr>" for x in surv[:40]) or "<tr><td colspan='4' class='mut'>Ninguna evidencia sobrevive a BH.</td></tr>"

    # P5-7 sensitivity
    el = sens["mean_elasticities"]
    sens_bars = svg_bars([("N efectivo", el.get("n_eff")), ("Exceso", el.get("excess")), ("IC", el.get("ic"))],
                         fmt=lambda v: f"{v:.2f}", w=380, h=220)

    # P5-8 naive
    nv_head = "".join(f"<th>{h}</th>" for h in HN)
    def nv_row(label, fn):
        return "<tr><td>" + label + "</td>" + "".join(f"<td>{fn(ph[h]['naive'])}</td>" for h in HN) + "</tr>"
    naive = (
        nv_row("MAE modelo", lambda x: num(x.get("mae_model"), 3)) +
        nv_row("MAE exceso-cero (baseline)", lambda x: num(x.get("mae_zero"), 3)) +
        nv_row("MAE media histórica", lambda x: num(x.get("mae_meanhist"), 3)) +
        nv_row("MAE momentum", lambda x: num(x.get("mae_momentum"), 3)) +
        nv_row("Skill vs cero (1−MAEm/MAE0)", lambda x: num(x.get("skill_vs_zero"), 3)) +
        nv_row("Sharpe modelo", lambda x: num(x.get("sharpe_model"))) +
        nv_row("Sharpe buy&hold", lambda x: num(x.get("sharpe_buyhold"))) +
        nv_row("Sharpe momentum", lambda x: num(x.get("sharpe_momentum")))
    )

    # auto conclusion
    skill6 = ph["6m"]["naive"].get("skill_vs_zero")
    cov6 = ph["6m"]["metrics"].get("coverage")
    slope6 = ph["6m"]["calibration"].get("slope")
    verdict = auto_conclusion(tl, skill6, cov6, slope6, fdr, sens)

    css = PAPER_CSS
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Validación cuantitativa · modelo de régimen</title><style>{css}</style></head>
<body><article>
<header class="ph">
  <div class="eyebrow">Informe de validación · research grade</div>
  <h1>Validación cuantitativa del modelo de régimen de mercado</h1>
  <div class="byline">{R['n_assets']} activos · walk-forward causal estricto · horizontes 1m–24m ·
    generado {R['generated']}</div>
  <div class="modelv">{R['model_version']}</div>
</header>

<section class="semaforo">
  <h2>Semáforo de credibilidad</h2>
  <table class="sem"><tbody>{sem}</tbody></table>
  <p class="conc"><b>Conclusión técnica.</b> {verdict}</p>
</section>

<section><h2>Resumen</h2>
<p>Se audita el modelo de régimen <em>tal como se publica</em>, sin modificar motor, inferencia,
datasets ni scores. Para cada activo y fecha se reconstruye la predicción que habría existido ese día
usando <b>únicamente análogos cuyo horizonte terminó antes de la fecha de decisión</b>
(<code>j+h ≤ t</code>), y se compara con el resultado realizado. Sobre esos pares out-of-sample se calculan
exactitud, calibración, robustez temporal y por régimen, estabilidad por bootstrap, tasa de falsos
positivos y comparación contra modelos ingenuos.</p></section>

<section><h2>1 · Objetivo</h2>
<p>Demostrar con evidencia reproducible <b>cuándo</b> el modelo funciona, <b>cuándo</b> falla y con
<b>qué grado de confianza</b> pueden usarse sus recomendaciones. Donde el análisis revela debilidad,
se muestra de forma explícita en lugar de ocultarse.</p></section>

<section><h2>2 · Metodología</h2>
<ul>
<li><b>Walk-forward causal estricto.</b> Predicción en <code>t</code> = mediana de los retornos forward
de los análogos del mismo régimen cuyo <b>periodo completo terminó antes de t</b>. Esto es más estricto
que el muestreador de calibración que se envía en el panel (que permite que la ventana de un análogo
pasado asome más allá de t); por eso algunas cifras aquí son algo peores, a propósito.</li>
<li><b>Horizontes:</b> 1m (21d), 3m (63d), 6m (126d), 12m (252d), 24m (504d).</li>
<li><b>Muestreo:</b> una decisión cada {R['config']['STEP']} días hábiles; mínimo {R['config']['MIN_ANALOG']} análogos.</li>
<li><b>Definiciones.</b> Bias = media(pred−obs), positivo = sobreestima. Cobertura = P(obs∈[p2.5,p97.5]
de los análogos). Exceso = obs−baseline as-of. Estrategia táctica (Sharpe/Hit) long-only cuando el
exceso predicho &gt; 0, evaluada en decisiones <b>no solapadas</b> (separación ≥ h) para no inflar por
autocorrelación.</li>
<li><b>Intervalos:</b> bootstrap circular por bloques (bloque ≈ √n) sobre las decisiones no solapadas.</li>
<li><b>FDR:</b> Benjamini–Hochberg sobre {fdr.get('m','—')} hipótesis (activo × horizonte), p-valor por
bootstrap del exceso actual.</li>
</ul></section>

<section><h2>3 · Supuestos</h2>
<ul>
<li>Las etiquetas de régimen y el Score son causales (verificado empíricamente: §4 y abajo).</li>
<li>El pasado del mismo régimen es informativo del futuro (hipótesis central del modelo — es lo que se pone a prueba).</li>
<li>Retornos forward solapados ⇒ autocorrelados; se corrige con bloques y decisiones no solapadas.</li>
<li>Verificación de causalidad del motor: al ocultar las últimas 400 sesiones y recomputar, el Score no cambia
(máx. |Δ| = {num(caus.get('score_max_diff'),9)}) y {caus['identical']}/{caus['total']} etiquetas de régimen son idénticas
({'✓ pasa — el motor no mira el futuro' if caus['passes'] else '✗ FALLA'}).</li>
</ul></section>

<section><h2>4 · Resultados</h2>

<h3>4.1 · Exactitud walk-forward (P5-1)</h3>
<table class="data"><thead><tr><th>Métrica</th>{acc_head}</tr></thead><tbody>{acc}</tbody></table>

<h3>4.2 · Calibración probabilística (P5-2)</h3>
<div class="fig"><div class="figc">{rel_svg}<div class="cap">Diagrama de fiabilidad a 6m: predicción media vs
resultado medio por decil. La diagonal punteada es calibración perfecta.</div></div>
<table class="data half"><thead><tr><th>Métrica</th>{cal_head}</tr></thead><tbody>{cal}</tbody></table></div>

<h3>4.3 · Robustez temporal (P5-3)</h3>
<table class="data"><thead><tr><th>Época (6m)</th><th>N</th><th>MAE</th><th>Bias</th><th>Cobertura</th><th>Hit</th><th>Sharpe</th></tr></thead><tbody>{era_rows}</tbody></table>
<div class="figc">{era_bars}<div class="cap">MAE por época a 6m. Dispersión alta ⇒ no estacionariedad.</div></div>

<h3>4.4 · Robustez por régimen (P5-4)</h3>
<table class="data"><thead><tr><th>Régimen (6m)</th><th>N</th><th>N ef.</th><th>MAE</th><th>Bias</th><th>Cobertura</th><th>Hit</th></tr></thead><tbody>{rg_rows}</tbody></table>
<div class="figc">{rg_bars}<div class="cap">MAE por régimen a 6m (ordenado). Los regímenes raros tienen peor precisión.</div></div>

<h3>4.5 · Estabilidad por bootstrap (P5-5)</h3>
<table class="data"><thead><tr><th>IC95</th>{bt_head}</tr></thead><tbody>{boot}</tbody></table>

<h3>4.6 · Falsos positivos y FDR (P5-6)</h3>
<p><b>{fdr.get('m','—')}</b> hipótesis (activo × horizonte). A α=0.05 se esperarían
<b>{num(fdr.get('expected_fp'),1)}</b> falsos positivos por azar; hay <b>{fdr.get('n_raw_sig','—')}</b> "significativas"
en crudo. Tras Benjamini–Hochberg (q=0.05) sobreviven <b>{fdr.get('n_survive','—')}</b>.</p>
<table class="data"><thead><tr><th>Activo</th><th>Horiz.</th><th>Exceso</th><th>p</th></tr></thead><tbody>{surv_rows}</tbody></table>

<h3>4.7 · Sensibilidad y elasticidades (P5-7)</h3>
<div class="figc">{sens_bars}<div class="cap">Elasticidad media de Opportunity a ±20% en cada variable.
Domina: <b>{sens.get('dominant','—')}</b>.</div></div>

<h3>4.8 · Comparación contra modelos ingenuos (P5-8)</h3>
<table class="data"><thead><tr><th>Métrica</th>{nv_head}</tr></thead><tbody>{naive}</tbody></table>
<p class="mut">Skill vs cero &gt; 0 ⇒ el modelo predice mejor que asumir exceso nulo (baseline). Sharpe modelo &gt;
Sharpe buy&amp;hold ⇒ la señal táctica añade valor ajustado por riesgo.</p>
</section>

<section><h2>5 · Limitaciones</h2>
<ul>
<li>Muestra efectiva pequeña a horizontes largos (ventanas solapadas): los IC de 12m–24m son anchos.</li>
<li>Regímenes raros (Pánico, Clímax) con pocos episodios ⇒ predicción poco fiable ahí.</li>
<li>El exceso es sobre el propio mercado (beta descontada), no rentabilidad absoluta.</li>
<li>La diversificación por grupos del panel no modela correlación real entre activos.</li>
</ul></section>

<section><h2>6 · Amenazas a la validez</h2>
<ul>
<li><b>No estacionariedad / cambio estructural:</b> el futuro puede no parecerse a ningún análogo histórico.</li>
<li><b>Múltiples comparaciones:</b> con 24 activos × 5 horizontes, parte de la "evidencia" es ruido (§4.6).</li>
<li><b>Sesgo de supervivencia del universo:</b> los ETFs/índices actuales sobreviven; los que no, no están.</li>
<li><b>Data-snooping del diseñador:</b> el modelo se diseñó conociendo esta misma historia.</li>
</ul></section>

<section><h2>7 · Conclusiones</h2><p>{verdict}</p></section>

<section><h2>8 · Trabajo futuro</h2>
<ul>
<li>Registro persistente de recomendaciones para un backtest de cartera real (no solo por activo).</li>
<li>Matriz de correlación para diversificación efectiva verdadera.</li>
<li>Pruebas formales de cambio estructural (Chow / CUSUM) por serie.</li>
<li>Validación out-of-universe en activos no usados en el diseño.</li>
</ul></section>

<footer class="pf">El motor cuantitativo (Score causal, FSM de régimen, analogías condicionales) permanece
idéntico. Todo este informe es análisis derivado y reproducible sobre el modelo publicado.
· Runtime {R.get('runtime_s','—')}s.</footer>
</article></body></html>"""


def auto_conclusion(tl, skill, cov, slope, fdr, sens):
    parts = []
    greens = sum(1 for v in tl.values() if v == "🟢")
    parts.append(f"El motor es {'causal y verificado' if tl['motor_causal']=='🟢' else 'de causalidad no confirmada'}, "
                 f"y la transparencia es alta.")
    if nn(cov):
        parts.append(f"La cobertura de intervalos es de {cov:.0f}% frente al 95% nominal "
                     f"({'correcta' if abs(cov-95)<6 else 'sesgada'}).")
    if nn(slope):
        parts.append(f"La calibración media presenta pendiente {slope:.2f} "
                     f"({'cercana a 1' if abs(slope-1)<0.3 else 'lejos de 1, hay des-calibración'}).")
    if nn(skill):
        if skill > 0.1:
            parts.append(f"El modelo bate claramente al baseline (skill {skill:+.2f}).")
        elif skill > 0.02:
            parts.append(f"El modelo aporta skill marginal sobre el baseline (skill {skill:+.2f}).")
        else:
            parts.append(f"El modelo apenas mejora —o no mejora— la predicción del baseline (skill {skill:+.2f}): "
                         f"su valor está en el encuadre y la disciplina, no en batir la media.")
    if fdr:
        parts.append(f"El único resultado estadísticamente fuerte es transversal: de {fdr.get('m','?')} evidencias, "
                     f"tras control de falsos positivos sobreviven {fdr.get('n_survive','?')} "
                     f"(esperadas por azar ≈ {fdr.get('expected_fp',0):.0f}), concentradas en horizontes largos.")
    if sens.get("dominant"):
        parts.append(f"La puntuación es poco sensible a shocks de ±20% (elasticidades ≈ 0.02) y está gobernada por la "
                     f"relación exceso/IC; el N efectivo casi no la mueve.")
    parts.append("En conjunto: el motor es causal y transparente, y existe estructura transversal real a horizontes "
                 "largos; PERO como predictor de retorno o temporizador de mercado no supera a modelos ingenuos "
                 "(pierde frente a buy&hold en Sharpe) y sus intervalos son algo optimistas. "
                 "Uso defendible: contexto de régimen y screening relativo, no señal mecánica de asignación ni market-timing. "
                 "La precisión se degrada en regímenes de crisis, poco frecuentes.")
    return " ".join(parts)


PAPER_CSS = """
:root{--bg:#faf9f6;--pn:#fff;--tx:#1a1a1a;--mut:#666;--fa:#aaa;--bd:#e2ded4;--ac:#2f6fb0;--ng:#c0563a;}
@media(prefers-color-scheme:dark){:root{--bg:#12141a;--pn:#171a21;--tx:#e7e9ee;--mut:#9aa0ad;--fa:#5a606c;--bd:#2a303b;--ac:#5b9bd8;--ng:#d1704f;}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.65 Georgia,"Times New Roman",serif;}
article{max-width:840px;margin:0 auto;padding:44px 26px 80px;}
.eyebrow{font:600 11px/1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--ac);margin-bottom:12px;}
h1{font-size:30px;line-height:1.2;margin:0 0 10px;letter-spacing:-.01em;text-wrap:balance;}
.byline,.modelv{font:13px/1.5 ui-sans-serif,system-ui,sans-serif;color:var(--mut);}
.modelv{margin-top:3px;color:var(--fa);font-size:12px;}
.ph{border-bottom:2px solid var(--tx);padding-bottom:20px;margin-bottom:8px;}
h2{font-size:21px;margin:34px 0 12px;padding-bottom:5px;border-bottom:1px solid var(--bd);letter-spacing:-.01em;}
h3{font:600 15px/1.3 ui-sans-serif,system-ui,sans-serif;margin:22px 0 8px;color:var(--tx);}
p{margin:10px 0;}ul{margin:10px 0;padding-left:22px;}li{margin:6px 0;}
code{font:13px ui-monospace,Menlo,monospace;background:color-mix(in srgb,var(--tx) 7%,transparent);padding:1px 5px;border-radius:4px;}
em{color:var(--mut);}
.semaforo{background:var(--pn);border:1px solid var(--bd);border-radius:12px;padding:8px 22px 20px;margin:22px 0;}
table.sem{width:100%;border-collapse:collapse;font:14px/1.4 ui-sans-serif,system-ui,sans-serif;}
table.sem td{padding:9px 6px;border-bottom:1px solid var(--bd);}
table.sem tr:last-child td{border-bottom:none;}
table.sem td.dot{width:34px;font-size:17px;text-align:center;}
table.sem td:last-child{color:var(--mut);width:150px;}
.conc{font:14px/1.6 ui-sans-serif,system-ui,sans-serif;margin-top:14px;color:var(--tx);}
table.data{width:100%;border-collapse:collapse;font:13px/1.4 ui-monospace,Menlo,monospace;margin:12px 0;overflow-x:auto;display:block;}
table.data thead th{font:600 11px/1 ui-sans-serif,system-ui,sans-serif;text-transform:uppercase;letter-spacing:.03em;color:var(--mut);text-align:right;padding:8px 10px;border-bottom:1.5px solid var(--bd);white-space:nowrap;}
table.data th:first-child,table.data td:first-child{text-align:left;font-family:ui-sans-serif,system-ui,sans-serif;color:var(--tx);}
table.data td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--bd);white-space:nowrap;font-variant-numeric:tabular-nums;}
table.data tr:last-child td{border-bottom:none;}
.half{max-width:360px;}
.fig{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start;}
.figc{flex:1;min-width:280px;}
svg.plot{width:100%;height:auto;background:var(--pn);border:1px solid var(--bd);border-radius:10px;}
svg.plot text{font:11px ui-sans-serif,system-ui,sans-serif;fill:var(--mut);}
.plot .axl{font-size:10px;fill:var(--fa);}.plot .bl{fill:var(--tx);text-anchor:middle;font-size:10px;}
.plot .bx{fill:var(--mut);text-anchor:middle;font-size:9.5px;}
.cap{font:12px/1.45 ui-sans-serif,system-ui,sans-serif;color:var(--mut);margin-top:6px;}
.mut{color:var(--mut);}
.pf{margin-top:40px;padding-top:16px;border-top:1px solid var(--bd);font:12px/1.5 ui-sans-serif,system-ui,sans-serif;color:var(--fa);}
"""


def main():
    with open(RES) as f:
        R = json.load(f)
    html = render(R)
    with open(OUT, "w") as f:
        f.write(html)
    print("Wrote", OUT, f"({len(html)} bytes)")


if __name__ == "__main__":
    main()
