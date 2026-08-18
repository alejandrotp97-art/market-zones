"""Dominio de la cartera: lo que un movimiento SIGNIFICA, sin saber que existe
un navegador ni una base de datos.

`zones/` lleva años siendo un núcleo puro con un solo módulo de red, y el panel
se monta encima. El lado de la cartera no tenía nada de eso: las posiciones, el
cambio de divisa, la clasificación de instrumentos y el sniff de CSV vivían
dentro de `dashboard.py`, entre las rutas de Flask y el SQL. Es decir, la mitad
que toca dinero real era justo la que no seguía la arquitectura del proyecto.

Este paquete es esa mitad, ya separada:

    cartera/parsing.py     texto -> dato (números, fechas, compra/venta, ISIN,
                           tipo de instrumento, nombre comercial). Puro.

Lo que NO vive aquí, a propósito: nada que abra un socket, un fichero o una
conexión. Si una función de este paquete necesita un precio o un tipo de
cambio, se lo tienen que pasar.
"""
from .parsing import (CARTERA_EXPORT_COLS, COLSYN, FAR_FUTURE, clean_company_name,
                      csv_num, instrument_kind, looks_like_isin, mov_key,
                      name_from_meta, norm_col, norm_date, norm_side, num,
                      sniff_sep, symbol_isin)

__all__ = ["CARTERA_EXPORT_COLS", "COLSYN", "FAR_FUTURE", "clean_company_name",
           "csv_num", "instrument_kind", "looks_like_isin", "mov_key",
           "name_from_meta", "norm_col", "norm_date", "norm_side", "num",
           "sniff_sep", "symbol_isin"]
