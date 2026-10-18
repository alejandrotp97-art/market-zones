"""`/api/cartera/ciclo`: sirve la foto del ciclo; la recalcula sólo si hace falta.

La foto es la misma para todas las instancias (es macro de EE. UU., no dato de
ninguna cartera) y se recalcula como mucho una vez a la vez: el candado evita
que tres pestañas abiertas lancen tres descargas de 20 series.

Recalcular dentro de una petición es el último recurso (el timer semanal es el
camino normal) y puede tardar minutos si la red está caída. Por eso, tras un
fallo, durante `RETRY_AFTER_S` se sirve la foto vieja sin volver a intentarlo:
sin esa espera, cada carga de página repetía la descarga entera.
"""
from __future__ import annotations

import logging
import threading
import time

from flask import Blueprint, jsonify

from . import build as B

bp = Blueprint("ciclo", __name__)
log = logging.getLogger(__name__)
# La foto es SEMANAL (timer: domingo 07:30 hora de Madrid). Ocho días = la
# semana más uno de margen si el domingo falla; con menos, el panel la
# recalcularía a mitad de semana y el calendario semanal no serviría de nada.
MAX_AGE_S = 8 * 24 * 3600
# Una foto hecha con alguna copia de disco (la red falló al hacerla) se da por
# buena menos tiempo: se reintenta a las 6 h en vez de esperar una semana.
STALE_SOURCES_MAX_AGE_S = 6 * 3600
RETRY_AFTER_S = 10 * 60
_lock = threading.Lock()
_last_fail = 0.0


def _fresh(snap: dict | None) -> bool:
    if snap is None:
        return False
    age = time.time() - float(snap.get("generated_at", 0))
    stale_inputs = bool((snap.get("sources") or {}).get("stale"))
    return age < (STALE_SOURCES_MAX_AGE_S if stale_inputs else MAX_AGE_S)


@bp.route("/api/cartera/ciclo")
def api_ciclo():
    global _last_fail
    snap = B.read_snapshot()
    if not _fresh(snap):
        backing_off = time.time() - _last_fail < RETRY_AFTER_S
        if not backing_off and _lock.acquire(blocking=False):
            try:
                snap = B.refresh()
            except Exception:               # frontera de red y disco: nunca tumbar la página
                _last_fail = time.time()
                # El detalle (rutas del servidor, URLs internas) va al journal,
                # no a quien abre la página.
                log.exception("no se pudo recalcular la foto del ciclo")
                if snap is None:
                    return jsonify({"error": "No pude calcular el ciclo ahora mismo; "
                                    "lo reintento en unos minutos."}), 502
                snap = {**snap, "stale": True}
            finally:
                _lock.release()
        elif snap is None:
            return jsonify({"error": "Calculando el ciclo; vuelve a cargar en unos segundos."}), 503
        else:
            snap = {**snap, "stale": True}
    resp = jsonify(snap)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp
