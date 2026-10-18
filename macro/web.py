"""`/api/cartera/ciclo`: sirve la foto del ciclo; la recalcula sólo si hace falta.

La foto es la misma para todas las instancias (es macro de EE. UU., no dato de
ninguna cartera) y se recalcula como mucho una vez a la vez: el candado evita
que tres pestañas abiertas lancen tres descargas de 20 series.
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify

from . import build as B

bp = Blueprint("ciclo", __name__)
# La foto es SEMANAL (timer: domingo 07:30 hora de Madrid). Ocho días = la
# semana más uno de margen si el domingo falla; con menos, el panel la
# recalcularía a mitad de semana y el calendario semanal no serviría de nada.
MAX_AGE_S = 8 * 24 * 3600
_lock = threading.Lock()


@bp.route("/api/cartera/ciclo")
def api_ciclo():
    snap = B.read_snapshot()
    fresh = snap is not None and time.time() - float(snap.get("generated_at", 0)) < MAX_AGE_S
    if not fresh:
        if _lock.acquire(blocking=False):
            try:
                snap = B.refresh()
            except Exception as e:          # frontera de red: nunca tumbar la página
                if snap is None:
                    return jsonify({"error": f"No pude calcular el ciclo: {e}"}), 502
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
