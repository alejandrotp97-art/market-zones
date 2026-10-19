#!/usr/bin/env python3
"""Sincroniza las operaciones spot de OKX con `cartera.db` (solo lectura en OKX).

Lo ejecuta el timer `market-zones-okx-sync`, pero también sirve a mano:

    python3 okx_sync.py

Salida: en éxito, SOLO el JSON de conteos por stdout. En fallo, el motivo por
stderr, nunca credenciales ni importes.

Códigos de salida:  0 sincronizado · 1 sin configurar o error de OKX.
"""
from __future__ import annotations

import json
import sys

from cartera import okx


def main(argv=None) -> int:
    # El mismo camino que el panel: `_cartera_conn` aplica las migraciones y
    # fija los permisos del fichero. Importar `dashboard` no arranca nada: el
    # servidor sólo se levanta en su `__main__`.
    import dashboard as D

    cfg, faltan = okx.leer_config()
    if cfg is None:
        print("OKX no configurado. Faltan: " + ", ".join(faltan), file=sys.stderr)
        return 1
    try:
        with D._cartera_conn() as c:
            resultado = okx.sincronizar(c, cfg)
    except okx.OkxError as e:
        print(f"No pude leer OKX: {e}", file=sys.stderr)
        return 1
    print(json.dumps(resultado))
    return 0


if __name__ == "__main__":
    sys.exit(main())
