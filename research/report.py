"""Render research/results.json into research/report.html.

Answers exactly one question: is there sufficient reproducible OOS evidence to
justify building Regime Engine v2? Figures embedded as base64 (self-contained)."""
from __future__ import annotations

import base64
import json
import os

HERE = os.path.dirname(__file__)
FIG = os.path.join(HERE, "figures")
VARIANTS = ["A_discrete", "euclid_knn", "C_kernel", "B_mahalanobis"]
VLAB = {"A_discrete": "A · régimen discreto (actual)", "euclid_knn": "Continuo · k-NN euclídeo",
        "C_kernel": "C · kernel gaussiano (difuso)", "B_mahalanobis": "B · Mahalanobis"}


def n(x, d=3):
    return "—" if x is None else (f"{x:.{d}f}" if isinstance(x, (int, float)) else str(x))


def pc(x, d=1):
    return "—" if x is None else f"{x*100:+.{d}f}%"


def img(name):
    p = os.path.join(FIG, name)
    if not os.path.exists(p):
        return ""
    b = base64.b64encode(open(p, "rb").read()).decode()
    return f'<img alt="{name}" src="data:image/png;base64,{b}">'


def yn(b):
    return '<span class="pass">✓</span>' if b else '<span class="fail">✗</span>'


def render(R):
    dec = R["decision"]; e10 = R["e10"]
    build = dec["build_v2"]
    verdict_cls = "yes" if build else "no"
    verdict_txt = "SÍ hay evidencia para construir v2" if build else "NO hay evidencia para construir v2"

    crit = R["criteria"]
    crit_rows = "".join(f"<tr><td>{k}</td><td class='n'>{v}</td></tr>" for k, v in crit.items())

    # E10 table
    hdr = "".join(f"<th>{VLAB[v]}</th>" for v in VARIANTS)
    def row(label, fn):
        return "<tr><td>" + label + "</td>" + "".join(f"<td class='n'>{fn(v)}</td>" for v in VARIANTS) + "</tr>"
    M = lambda v: R["variants"][v]["metrics"]
    G = lambda v: R["variants"][v]["gates"]
    tbl = (
        row("Rank IC medio", lambda v: n(M(v)["rank_ic"], 4)) +
        row("IC t-stat", lambda v: n(M(v)["ic_tstat"], 2)) +
        row("IC épocas positivas (de 4)", lambda v: str(M(v)["ic_eras_positive"])) +
        row("Kendall τ", lambda v: n(M(v)["kendall"], 4)) +
        row("Sharpe long-short (neto)", lambda v: n(M(v)["ls_sharpe"], 2)) +
        row("Alpha top vs EW (anual)", lambda v: pc(M(v)["top_alpha"])) +
        row("Turnover medio", lambda v: pc(M(v)["turnover"], 0)) +
        row("Brier skill", lambda v: n(M(v)["brier_skill"], 3)) +
        row("ECE", lambda v: n(M(v)["ece"], 3)) +
        row("Cobertura conformal", lambda v: pc(M(v)["cov_conformal"], 0)) +
        row("Cobertura bootstrap", lambda v: pc(M(v)["cov_bootstrap"], 0)) +
        "<tr class='gaterow'><td>Gate ranking</td>" + "".join(f"<td class='n'>{yn(G(v)['rank_ic'])}</td>" for v in VARIANTS) + "</tr>" +
        "<tr class='gaterow'><td>Gate cartera</td>" + "".join(f"<td class='n'>{yn(G(v)['portfolio'])}</td>" for v in VARIANTS) + "</tr>" +
        "<tr class='gaterow'><td>Gate probabilidad</td>" + "".join(f"<td class='n'>{yn(G(v)['probability'])}</td>" for v in VARIANTS) + "</tr>" +
        "<tr class='gaterow'><td>Gate cobertura</td>" + "".join(f"<td class='n'>{yn(G(v)['coverage'])}</td>" for v in VARIANTS) + "</tr>" +
        "<tr class='scorerow'><td><b>¿Apto para v2?</b></td>" + "".join(f"<td class='n'>{yn(G(v)['v2_worthy'])}</td>" for v in VARIANTS) + "</tr>" +
        "<tr class='scorerow'><td><b>Puntuación E10</b></td>" + "".join(f"<td class='n'><b>{n(R['variants'][v]['score'],3)}</b></td>" for v in VARIANTS) + "</tr>"
    )

    # portfolio detail of best-by-score
    bs = e10["best_by_score"]; pf = R["variants"][bs]["portfolio"]
    pf_rows = ""
    if pf.get("top"):
        for lab, key in [("Top 20%", "top"), ("Equal-weight", "ew"), ("Bottom 20%", "bottom"), ("Long-short", "ls")]:
            s = pf.get(key, {})
            pf_rows += (f"<tr><td>{lab}</td><td class='n'>{pc(s.get('cagr'))}</td><td class='n'>{n(s.get('sharpe'),2)}</td>"
                        f"<td class='n'>{n(s.get('sortino'),2)}</td><td class='n'>{pc(s.get('maxdd'))}</td>"
                        f"<td class='n'>{n(s.get('calmar'),2)}</td></tr>")

    # E7/E8
    perm = R.get("e7_permutation_importance", {}); abla = R.get("e8_ablation", {})
    imp_rows = "".join(f"<tr><td>{k}</td><td class='n'>{n(perm[k],4)}</td><td class='n'>{n(abla.get(k),4)}</td></tr>"
                       for k in perm)
    rvc = R.get("e8_regime_vs_continuous", {})

    # E9 crisis
    cr = R.get("e9_crisis", {})
    cr_rows = ""
    for name, vs in cr.items():
        for v in ("A_discrete", "euclid_knn"):
            d = vs.get(v, {})
            cr_rows += (f"<tr><td>{name}</td><td>{v}</td><td class='n'>{n(d.get('rank_ic'),3)}</td>"
                        f"<td class='n'>{n(d.get('ls_sharpe'),2)}</td><td class='n'>{pc(d.get('cov_conformal'),0)}</td>"
                        f"<td class='n'>{n(d.get('brier_skill'),3)}</td><td class='n'>{d.get('n','—')}</td></tr>")

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Regime v2 — decisión experimental</title><style>{CSS}</style></head><body><article>
<header class="hd">
  <div class="eyebrow">RESEARCH SANDBOX · P7 · resultados out-of-sample reproducibles</div>
  <h1>¿Existe evidencia suficiente para justificar una Regime Engine v2?</h1>
  <div class="byline">{R['n_assets']} activos · panel transversal no balanceado · {R['n_months']} fechas mensuales ·
    walk-forward causal estricto · generado {R['generated']}</div>
</header>

<section class="verdict {verdict_cls}">
  <div class="vk">Decisión</div>
  <div class="vt">{verdict_txt}</div>
  <p>{dec['rationale']}</p>
  <div class="vmeta">Variantes que superan TODOS los gates pre-registrados:
    <b>{', '.join(e10['passers']) if e10['passers'] else 'ninguna'}</b> ·
    mejor por puntuación E10: <b>{VLAB[bs]}</b></div>
</section>

<section><h2>Criterios de éxito · pre-registrados (antes de ver resultados)</h2>
<p>Estos umbrales se fijaron por escrito en <code>research/criteria.py</code> antes de ejecutar nada. Una
variante que no los supera queda descartada. Si ninguna supera ranking + cartera, la conclusión válida es
<b>no construir v2</b>.</p>
<table class="mini"><tbody>{crit_rows}</tbody></table></section>

<section><h2>E10 · Selección objetiva de variantes</h2>
<table class="data"><thead><tr><th>Métrica</th>{hdr}</tr></thead><tbody>{tbl}</tbody></table>
<div class="fig">{img('e1_rank_ic.png')}{img('e4_coverage.png')}</div></section>

<section><h2>E1 · Rank IC — ¿se pueden ordenar los activos?</h2>
<p>Correlación de Spearman transversal entre la señal (exceso predicho) y el retorno observado a 1 mes, en cada
rebalanceo. Umbral pre-registrado: IC ≥ {crit['RANK_IC_MIN']}, t-stat ≥ {crit['IC_TSTAT_MIN']}, positivo en ≥
{crit['IC_ERA_POSITIVE_MIN']} de 4 épocas. Ver tabla E10 y figura de barras.</p></section>

<section><h2>E2 · Cartera por ranking — el experimento decisivo</h2>
<p>Carteras top 20% / equal-weight / bottom 20%, rebalanceo mensual, neto de {crit['COST_BPS']} bps por pierna.
Mejor variante por puntuación: <b>{VLAB[bs]}</b>.</p>
<table class="data"><thead><tr><th>Cartera</th><th class="n">CAGR</th><th class="n">Sharpe</th><th class="n">Sortino</th><th class="n">Max DD</th><th class="n">Calmar</th></tr></thead><tbody>{pf_rows or '<tr><td colspan=6 class=mut>sin datos suficientes</td></tr>'}</tbody></table>
<div class="fig">{img('e2_equity.png')}</div></section>

<section><h2>E3 · Probabilidad de batir el baseline</h2>
<p>Salida P(exceso&gt;0) frente al resultado observado. Umbral: Brier skill &gt; 0 y ECE ≤ {crit['ECE_MAX']}.</p>
<div class="fig">{img('e3_reliability.png')}</div></section>

<section><h2>E4 · Conformal vs bootstrap — cobertura</h2>
<p>El bootstrap actual subcubre (P5: 75–84%). La predicción conforme recalibra a la cobertura nominal. Umbral:
cobertura conformal ∈ [{crit['COVERAGE_MIN']}, {crit['COVERAGE_MAX']}]. Ver figura de cobertura arriba.</p></section>

<section><h2>E5 · Analog search &nbsp;·&nbsp; E6 · Estados (discreto vs difuso vs continuo)</h2>
<p>Las cuatro columnas de E10 <em>son</em> la comparación E5/E6. Discretización vs continuo:
Rank IC discreto = <b>{n(rvc.get('discrete_ic'),4)}</b> · continuo = <b>{n(rvc.get('continuous_ic'),4)}</b>.
{'El continuo NO recupera señal que la discretización destruyera.' if (rvc.get('continuous_ic') or 0) <= (rvc.get('discrete_ic') or 0) + 0.01 else 'El continuo mejora sobre el discreto.'}</p></section>

<section><h2>E7 · Importancia de variables &nbsp;·&nbsp; E8 · Ablation</h2>
<p>Caída de Rank IC al permutar (E7) o eliminar (E8) cada eje, sobre la variante continua. Un valor ≈ 0 significa
que el eje no aporta ventaja transversal.</p>
<table class="data"><thead><tr><th>Eje</th><th class="n">Caída IC permutación</th><th class="n">Caída IC ablación</th></tr></thead><tbody>{imp_rows}</tbody></table></section>

<section><h2>E9 · Crisis (2008 · 2020 · 2022) — donde P5 fallaba</h2>
<table class="data"><thead><tr><th>Crisis</th><th>Variante</th><th class="n">Rank IC</th><th class="n">LS Sharpe</th><th class="n">Cob. conf.</th><th class="n">Brier skill</th><th class="n">N</th></tr></thead><tbody>{cr_rows}</tbody></table></section>

<section><h2>Decisión final</h2>
<p class="{'okc' if build else 'noc'}"><b>{verdict_txt}.</b> {dec['rationale']}</p>
<p class="mut small">Regla de oro aplicada: ninguna idea pasa a producción sin superar este laboratorio con
validación out-of-sample. {'' if build else 'Que ninguna variante lo supere es un resultado científico válido: evita invertir en una dirección que los datos no respaldan.'}</p></section>

<footer class="pf">Sandbox aislado bajo <code>research/</code>. El motor de producción, el dashboard, el screener,
el comité y la validación P5 permanecen intactos: este laboratorio solo lee datos/features y escribe bajo
<code>research/</code>. Reproducible: <code>python -m research.run</code>.</footer>
</article></body></html>"""


CSS = """
:root{--bg:#f5f6f4;--pn:#fffffe;--tx:#191c1a;--mut:#606763;--fa:#a6aaa6;--bd:#e0e3df;--ac:#2e7d6b;--yes:#2e7d4f;--no:#b23a2e;}
@media(prefers-color-scheme:dark){:root{--bg:#101311;--pn:#171a18;--tx:#e8ebe7;--mut:#969c97;--fa:#5a605b;--bd:#282d29;--ac:#54b39c;--yes:#5bb37e;--no:#d1704f;}}
:root[data-theme="dark"]{--bg:#101311;--pn:#171a18;--tx:#e8ebe7;--mut:#969c97;--fa:#5a605b;--bd:#282d29;--ac:#54b39c;--yes:#5bb37e;--no:#d1704f;}
:root[data-theme="light"]{--bg:#f5f6f4;--pn:#fffffe;--tx:#191c1a;--mut:#606763;--fa:#a6aaa6;--bd:#e0e3df;--ac:#2e7d6b;--yes:#2e7d4f;--no:#b23a2e;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.6 Georgia,serif;}
article{max-width:900px;margin:0 auto;padding:44px 24px 80px;}
.eyebrow{font:600 11px/1 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--ac);}
h1{font-size:29px;line-height:1.22;margin:12px 0 8px;letter-spacing:-.015em;text-wrap:balance;}
.byline{font:13px/1.5 ui-sans-serif,system-ui,sans-serif;color:var(--mut);}
.hd{border-bottom:3px double var(--tx);padding-bottom:18px;}
h2{font:600 13px/1.3 ui-sans-serif,system-ui,sans-serif;letter-spacing:.05em;text-transform:uppercase;color:var(--ac);margin:38px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--bd);}
p{margin:10px 0;}code{font:13px ui-monospace,monospace;background:color-mix(in srgb,var(--tx) 8%,transparent);padding:1px 5px;border-radius:4px;}
.verdict{border-radius:12px;padding:20px 24px;margin:22px 0;border:2px solid;}
.verdict.yes{border-color:var(--yes);background:color-mix(in srgb,var(--yes) 10%,var(--pn));}
.verdict.no{border-color:var(--no);background:color-mix(in srgb,var(--no) 9%,var(--pn));}
.vk{font:600 11px/1 ui-sans-serif,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);}
.vt{font-size:25px;font-weight:700;margin:6px 0 8px;letter-spacing:-.01em;}
.verdict.yes .vt{color:var(--yes);}.verdict.no .vt{color:var(--no);}
.vmeta{font:13px/1.5 ui-sans-serif,sans-serif;color:var(--mut);margin-top:8px;}
table{width:100%;border-collapse:collapse;margin:12px 0;font:13px/1.4 ui-sans-serif,system-ui,sans-serif;display:block;overflow-x:auto;}
table.data th,table.mini th{font:600 10.5px/1.2 ui-sans-serif,sans-serif;text-transform:uppercase;letter-spacing:.03em;color:var(--mut);text-align:left;padding:8px 10px;border-bottom:1.5px solid var(--bd);white-space:nowrap;vertical-align:bottom;}
td{padding:7px 10px;border-bottom:1px solid var(--bd);vertical-align:top;}
td.n,th.n{text-align:right;font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;}
tr:last-child td{border-bottom:none;}
.mini{max-width:420px;}.mini td{font-family:ui-monospace,monospace;}
.gaterow td{background:color-mix(in srgb,var(--tx) 3%,transparent);}
.scorerow td{border-top:1.5px solid var(--bd);}
.pass{color:var(--yes);font-weight:700;}.fail{color:var(--no);font-weight:700;}
.fig{display:flex;flex-wrap:wrap;gap:14px;margin:14px 0;}
.fig img{max-width:100%;flex:1;min-width:280px;border:1px solid var(--bd);border-radius:8px;background:var(--pn);}
.mut{color:var(--mut);}.small{font-size:13px;}
.okc{color:var(--yes);}.noc{color:var(--no);}
.pf{margin-top:40px;padding-top:16px;border-top:1px solid var(--bd);font:12px/1.6 ui-sans-serif,sans-serif;color:var(--fa);}
"""


def main():
    with open(os.path.join(HERE, "results.json")) as f:
        R = json.load(f)
    html = render(R)
    with open(os.path.join(HERE, "report.html"), "w") as f:
        f.write(html)
    print("wrote research/report.html", len(html), "bytes")


if __name__ == "__main__":
    main()
