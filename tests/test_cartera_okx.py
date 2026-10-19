"""Sincronización de operaciones spot de OKX (API de solo lectura).

Sin red: el HTTP se inyecta (`get=` o parcheando `okx.http_get`). Las claves de
prueba son falsas y no se imprimen en ninguna aserción.
"""
import datetime
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import dashboard as D
from cartera import okx

HDR = {D.CSRF_HEADER: "1"}
EVIL = "https://evil.example"

# 2026-10-07 23:30 UTC = 2026-10-08 01:30 en Madrid: la fecha debe ser la local.
TS = 1791415800000
FIRMA_VECTOR = "PODfCpxGQwSoMgqmdt9ABeO6gTV95cM6CTqSc0vTV3M="
CFG = {"key": "clave-falsa", "secret": "secreto-de-prueba", "passphrase": "frase-falsa"}


def _leg(trade_id, ccy, bal, fee="0", px="10000", inst="BTC-EUR", ts=TS, sz="0.01", tipo="30"):
    return {"billId": f"{trade_id}-{ccy}", "tradeId": trade_id, "type": tipo,
            "instId": inst, "instType": "SPOT", "ccy": ccy, "balChg": bal,
            "sz": sz, "px": px, "fee": fee, "ts": str(ts), "execType": "T"}


def _compra(trade_id="T1", base_fee="0", quote_fee="-0.5", base_bal="0.01", quote_bal="-100.5"):
    return [_leg(trade_id, "BTC", base_bal, fee=base_fee),
            _leg(trade_id, "EUR", quote_bal, fee=quote_fee, sz="100")]


def _venta(trade_id="T2"):
    return [_leg(trade_id, "BTC", "-0.01"),
            _leg(trade_id, "EUR", "99.5", fee="-0.5", sz="100")]


def _fake_get(paginas_archive=None, paginas_recientes=None):
    """Devuelve un `get` que responde por endpoint y registra las llamadas."""
    llamadas = []

    def get(path, headers):
        llamadas.append(path)
        if "bills-archive" in path:
            data = (paginas_archive or [[]]).pop(0) if paginas_archive else []
        else:
            data = (paginas_recientes or [[]]).pop(0) if paginas_recientes else []
        return {"code": "0", "msg": "", "data": data}

    get.llamadas = llamadas
    return get


# ── firma y cabeceras ─────────────────────────────────────────────────────

def test_firma_coincide_con_vector_conocido():
    """Vector calculado de antemano: base64(HMAC-SHA256(secret, ts+method+path))."""
    firma = okx.firmar("secreto-de-prueba", "2026-10-08T10:00:00.000Z", "GET",
                       "/api/v5/account/bills?instType=SPOT&limit=100")
    assert firma == FIRMA_VECTOR


def test_timestamp_iso_utc_con_milisegundos_y_z():
    ahora = datetime.datetime(2026, 10, 8, 10, 0, 0, 123456, tzinfo=datetime.UTC)
    assert okx._timestamp(ahora) == "2026-10-08T10:00:00.123Z"


def test_cabeceras_incluyen_auth_y_user_agent_de_navegador():
    cab = okx.cabeceras(CFG, "GET", "/api/v5/account/bills?instType=SPOT",
                        timestamp="2026-10-08T10:00:00.000Z")
    assert cab["OK-ACCESS-KEY"] == CFG["key"]
    assert cab["OK-ACCESS-PASSPHRASE"] == CFG["passphrase"]
    assert cab["OK-ACCESS-TIMESTAMP"] == "2026-10-08T10:00:00.000Z"
    assert cab["OK-ACCESS-SIGN"]
    assert cab["User-Agent"] == "Mozilla/5.0"
    assert cab["Content-Type"] == "application/json"


# ── configuración ─────────────────────────────────────────────────────────

def test_lee_env_con_comillas_comentarios_y_export(tmp_path):
    ruta = tmp_path / ".env"
    ruta.write_text(
        "# comentario\n"
        'OKX_API_KEY="clave-falsa"\n'
        "OKX_API_SECRET='secreto-de-prueba'\n"
        "export OKX_API_PASSPHRASE=frase-falsa # nota\n"
        "\n"
        "OTRA=1\n", encoding="utf-8")
    cfg, faltan = okx.leer_config(str(ruta))
    assert faltan == []
    assert cfg == CFG


def test_sin_fichero_o_sin_claves_no_esta_configurado(tmp_path):
    cfg, faltan = okx.leer_config(str(tmp_path / "no-existe.env"))
    assert cfg is None
    assert faltan == ["OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE"]

    ruta = tmp_path / ".env"
    ruta.write_text("OKX_API_KEY=solo-una\n", encoding="utf-8")
    cfg, faltan = okx.leer_config(str(ruta))
    assert cfg is None
    assert faltan == ["OKX_API_SECRET", "OKX_API_PASSPHRASE"]


def test_ruta_env_viene_de_la_variable_con_defecto(monkeypatch):
    monkeypatch.delenv("CARTERA_OKX_ENV", raising=False)
    assert okx.ruta_env() == "/home/alex/oficina/.env"
    monkeypatch.setenv("CARTERA_OKX_ENV", "/tmp/otro.env")
    assert okx.ruta_env() == "/tmp/otro.env"


# ── mapeo de patas a movimientos ──────────────────────────────────────────

def test_compra_se_mapea_a_un_movimiento():
    movs, omit = okx.movimientos_desde_bills(_compra())
    assert omit == []
    assert len(movs) == 1
    m = movs[0]
    assert m["side"] == "buy"
    assert m["quantity"] == pytest.approx(0.01)
    assert m["price"] == pytest.approx(10000)
    assert m["fee"] == pytest.approx(0.5)
    assert m["ticker"] == "BTC-EUR"
    assert m["kind"] == "Cripto"
    assert m["note"] == "OKX tradeId T1"
    # source_id usa el billId de la pata base (siempre presente), no el tradeId.
    assert m["source_id"] == "okx:BTC-EUR:T1-BTC"


def test_venta_se_mapea_a_sell():
    movs, omit = okx.movimientos_desde_bills(_venta())
    assert omit == []
    assert movs[0]["side"] == "sell"
    assert movs[0]["quantity"] == pytest.approx(0.01)
    assert movs[0]["fee"] == pytest.approx(0.5)


def test_comision_en_la_base_se_convierte_a_euros_y_no_resta_de_la_cantidad():
    # Compra 0.01 BTC pagando 0.00001 BTC de comisión: la cantidad es 0.01 y la
    # comisión son 0.00001 * 10000 = 0.1 EUR.
    rows = _compra(base_bal="0.00999", base_fee="-0.00001", quote_fee="0", quote_bal="-100")
    movs, omit = okx.movimientos_desde_bills(rows)
    assert omit == []
    assert movs[0]["quantity"] == pytest.approx(0.01)
    assert movs[0]["fee"] == pytest.approx(0.1)


def test_la_fecha_es_la_local_de_madrid():
    # 23:30 UTC del 7 ya es el 8 en Madrid.
    movs, _ = okx.movimientos_desde_bills(_compra())
    assert movs[0]["date"] == "2026-10-08"


def test_pata_suelta_se_omite_y_se_cuenta_con_motivo():
    rows = [_leg("T9", "BTC", "0.01")]
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit == [("T9", "sin pareja completa de patas")]


def test_cantidad_cero_se_omite():
    rows = _compra(base_bal="0", quote_bal="0", quote_fee="0")
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit[0][1] == "cantidad cero"


def test_signos_incoherentes_se_omiten():
    # Base sube y la contraparte también: no es una compra.
    rows = _compra(base_bal="0.01", quote_bal="100.5", quote_fee="0.5")
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit[0][1] == "signos incoherentes entre patas"


def test_importe_que_no_cuadra_con_el_precio_se_omite():
    # 0.01 BTC a 10000 deberían ser 100 EUR; el extracto dice 150.
    rows = _compra(quote_bal="-150.5", quote_fee="-0.5")
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit[0][1] == "precio no cuadra con el importe"


def test_dato_no_numerico_se_omite():
    rows = _compra()
    rows[0]["balChg"] = "n/a"
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit[0][1] == "dato no numérico"


def test_filas_que_no_son_operacion_spot_se_ignoran_o_se_omiten():
    # type "0" (otro tipo de apunte) no es una operación: se ignora sin contar.
    movs, omit = okx.movimientos_desde_bills([_leg("X", "BTC", "1", tipo="0")])
    assert movs == [] and omit == []
    # Una operación que no es spot se cuenta como omitida.
    rows = _compra()
    for r in rows:
        r["instType"] = "FUTURES"
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit[0][1] == "no es spot"


def test_mismo_tradeid_en_dos_instrumentos_no_se_mezcla():
    rows = [
        *_compra(trade_id="7"),
        _leg("7", "ETH", "0.1", px="3000", inst="ETH-EUR"),
        _leg("7", "EUR", "-300", fee="0", inst="ETH-EUR", px="3000", sz="300")]
    movs, omit = okx.movimientos_desde_bills(rows)
    assert omit == []
    assert sorted(m["ticker"] for m in movs) == ["BTC-EUR", "ETH-EUR"]


# ── apuntes reales: sin tradeId, emparejados por fillTime ─────────────────
# Forma real del historial: tradeId y ordId vacíos, fillTime en las dos patas,
# ts distinto de fillTime y billId único por apunte. Números sintéticos.

FILL_1 = 1791415800000
FILL_2 = 1791416400000


def _leg_real(fill, ccy, bal, bill_id, px="10000", fee="0", inst="BTC-EUR", ts_extra=0):
    return {"billId": bill_id, "tradeId": "", "ordId": "", "type": "30",
            "instId": inst, "instType": "SPOT", "ccy": ccy, "balChg": bal,
            "fillTime": str(fill), "px": px, "fee": fee,
            "ts": str(fill + ts_extra), "execType": "T"}


def _par_real(fill, bill_base, base_bal="0.01", quote_bal="-100", px="10000",
              inst="BTC-EUR", base_fee="0", quote_fee="0"):
    # Los dos apuntes llevan ts distintos entre sí y del fillTime: no sirven para agrupar.
    return [_leg_real(fill, "BTC", base_bal, bill_base, px=px, fee=base_fee, inst=inst, ts_extra=3),
            _leg_real(fill, "EUR", quote_bal, bill_base + "-q", px=px, fee=quote_fee,
                      inst=inst, ts_extra=9)]


def test_sin_tradeid_se_agrupa_por_filltime():
    rows = [*_par_real(FILL_1, "B1"), *_par_real(FILL_2, "B2", base_bal="-0.01", quote_bal="100")]
    movs, omit = okx.movimientos_desde_bills(rows)
    assert omit == []
    assert len(movs) == 2
    assert sorted(m["side"] for m in movs) == ["buy", "sell"]


def test_compra_sin_tradeid_es_buy_y_base_positiva():
    movs, omit = okx.movimientos_desde_bills(_par_real(FILL_1, "B1", base_bal="0.02", quote_bal="-200"))
    assert omit == []
    assert movs[0]["side"] == "buy"
    assert movs[0]["quantity"] == pytest.approx(0.02)


def test_venta_sin_tradeid_es_sell_y_base_negativa():
    movs, omit = okx.movimientos_desde_bills(_par_real(FILL_1, "B1", base_bal="-0.02", quote_bal="200"))
    assert omit == []
    assert movs[0]["side"] == "sell"


def test_source_id_usa_billid_de_la_pata_base_y_es_unico():
    rows = [*_par_real(FILL_1, "B1"), *_par_real(FILL_2, "B2")]
    movs, _ = okx.movimientos_desde_bills(rows)
    ids = [m["source_id"] for m in movs]
    assert sorted(ids) == ["okx:BTC-EUR:B1", "okx:BTC-EUR:B2"]
    assert len(set(ids)) == len(ids)


def test_sin_tradeid_la_nota_lleva_el_filltime():
    movs, _ = okx.movimientos_desde_bills(_par_real(FILL_1, "B1"))
    assert movs[0]["note"] == f"OKX fillTime {FILL_1}"


def test_con_tradeid_la_nota_sigue_llevando_el_tradeid():
    movs, _ = okx.movimientos_desde_bills(_compra(trade_id="T1"))
    assert movs[0]["note"] == "OKX tradeId T1"


def test_pata_suelta_sin_tradeid_se_omite_con_motivo():
    rows = [_leg_real(FILL_1, "BTC", "0.01", "B1")]
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit == [(str(FILL_1), "sin pareja completa de patas")]


def test_dos_patas_base_en_el_mismo_filltime_se_omiten():
    rows = [_leg_real(FILL_1, "BTC", "0.01", "B1"), _leg_real(FILL_1, "BTC", "0.02", "B2"),
            _leg_real(FILL_1, "EUR", "-300", "B1-q")]
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit == [(str(FILL_1), "sin pareja completa de patas")]


def test_sin_tradeid_ni_filltime_se_omite_con_motivo():
    rows = [dict(r, fillTime="") for r in _par_real(FILL_1, "B1")]
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert all(m == "sin tradeId ni fillTime" for _, m in omit)
    assert len(omit) == 2


def test_sin_billid_no_se_inventa_source_id():
    rows = _par_real(FILL_1, "")
    movs, omit = okx.movimientos_desde_bills(rows)
    assert movs == []
    assert omit[0][1] == "sin billId"


def test_sync_sin_tradeid_repetir_no_anade_nada(db_temporal):
    filas = [*_par_real(FILL_1, "B1"), *_par_real(FILL_2, "B2", base_bal="-0.01", quote_bal="100")]
    with D._cartera_conn() as c:
        primera = okx.sincronizar(c, CFG, get=_fake_get(paginas_archive=[filas]))
    with D._cartera_conn() as c:
        segunda = okx.sincronizar(c, CFG, get=_fake_get(paginas_archive=[filas]))
    assert (primera["added"], primera["skipped"]) == (2, 0)
    assert (segunda["added"], segunda["existing"]) == (0, 2)
    assert _contar(db_temporal) == 2


# ── descarga y paginación ─────────────────────────────────────────────────

def _pagina_de_ids(inicio, n):
    return [{"billId": str(inicio - i), "type": "0", "ccy": "EUR", "instType": "SPOT"}
            for i in range(n)]


def test_paginacion_sigue_con_after_y_se_detiene_en_pagina_corta():
    p1 = _pagina_de_ids(1000, 100)
    p2 = _pagina_de_ids(899, 3)
    get = _fake_get(paginas_archive=[p1, p2])
    rows, truncado = okx._paginar(get, CFG, okx.ENDPOINTS[0])
    assert len(rows) == 103
    assert truncado is False
    assert len(get.llamadas) == 2
    assert "after=901" in get.llamadas[1]          # último billId de la página llena


def test_paginacion_tiene_tope(monkeypatch):
    monkeypatch.setattr(okx, "MAX_PAGES", 3)
    paginas = [_pagina_de_ids(10000 - 100 * k, 100) for k in range(10)]
    get = _fake_get(paginas_archive=paginas)
    rows, truncado = okx._paginar(get, CFG, okx.ENDPOINTS[0])
    assert len(get.llamadas) == 3
    assert truncado is True


def test_paginacion_sin_progreso_se_corta():
    pagina = _pagina_de_ids(1000, 100)
    llamadas = []

    def get(path, headers):
        llamadas.append(path)
        return {"code": "0", "data": pagina}

    okx._paginar(get, CFG, okx.ENDPOINTS[0])
    assert len(llamadas) == 2


def test_archive_y_recientes_se_fusionan_sin_duplicar_billid():
    filas = _compra()
    get = _fake_get(paginas_archive=[filas], paginas_recientes=[filas])
    rows, _ = okx.descargar_bills(CFG, get=get)
    assert len(rows) == 2
    movs, _ = okx.movimientos_desde_bills(rows)
    assert len(movs) == 1
    assert any("bills-archive" in p for p in get.llamadas)
    assert any("/api/v5/account/bills?" in p for p in get.llamadas)


def test_error_de_okx_se_propaga_como_okxerror():
    def get(path, headers):
        return {"code": "50111", "msg": "Invalid API key"}

    with pytest.raises(okx.OkxError):
        okx.descargar_bills(CFG, get=get)


# ── sincronización e idempotencia ─────────────────────────────────────────

@pytest.fixture
def db_temporal(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(D, "CARTERA_DB", path)
    yield path
    os.unlink(path)


def _contar(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM movements").fetchone()[0]
    finally:
        c.close()


def test_migracion_anade_source_id_y_indice_unico(db_temporal):
    with D._cartera_conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(movements)")}
        idx = c.execute("SELECT sql FROM sqlite_master WHERE name='movements_source_id'").fetchone()
    assert "source_id" in cols
    assert idx and "UNIQUE" in idx[0].upper()
    with D._cartera_conn():                      # segunda conexión: no falla
        pass


def test_sync_anade_y_repetir_no_anade_nada(db_temporal):
    filas = _compra() + _venta()
    with D._cartera_conn() as c:
        primera = okx.sincronizar(c, CFG, get=_fake_get(paginas_archive=[filas]))
    with D._cartera_conn() as c:
        segunda = okx.sincronizar(c, CFG, get=_fake_get(paginas_archive=[filas]))
    assert (primera["added"], primera["existing"], primera["skipped"]) == (2, 0, 0)
    assert (segunda["added"], segunda["existing"]) == (0, 2)
    assert _contar(db_temporal) == 2


def test_sync_cuenta_omitidas_por_motivo(db_temporal):
    filas = [*_compra(), _leg("Z", "BTC", "0.01")]
    with D._cartera_conn() as c:
        res = okx.sincronizar(c, CFG, get=_fake_get(paginas_archive=[filas]))
    assert res["added"] == 1
    assert res["skipped"] == 1
    assert res["skipped_reasons"] == {"sin pareja completa de patas": 1}
    assert res["truncated"] is False


def test_movimientos_existentes_siguen_intactos(db_temporal):
    with D._cartera_conn() as c:
        c.execute("INSERT INTO movements(date,ticker,side,quantity,price,fee,note)"
                  " VALUES('2024-01-01','TEST','buy',10,100,0,'manual')")
        okx.sincronizar(c, CFG, get=_fake_get(paginas_archive=[_compra()]))
    c = sqlite3.connect(db_temporal)
    try:
        manual = c.execute("SELECT source_id, quantity FROM movements WHERE note='manual'").fetchone()
    finally:
        c.close()
    assert manual == (None, 10)


# ── endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(db_temporal, tmp_path, monkeypatch):
    D.app.config["TESTING"] = True
    ruta_env = tmp_path / ".env"
    ruta_env.write_text("OKX_API_KEY=clave-falsa\nOKX_API_SECRET=secreto-de-prueba\n"
                        "OKX_API_PASSPHRASE=frase-falsa\n", encoding="utf-8")
    monkeypatch.setenv("CARTERA_OKX_ENV", str(ruta_env))
    return D.app.test_client()


def test_endpoint_exige_cabecera_csrf(cliente):
    r = cliente.post("/api/cartera/okx/sync")
    assert r.status_code == 403


def test_endpoint_rechaza_origen_externo(cliente):
    r = cliente.post("/api/cartera/okx/sync", headers={**HDR, "Origin": EVIL})
    assert r.status_code == 403


def test_endpoint_sin_configuracion_no_toca_la_red(cliente, monkeypatch, tmp_path):
    monkeypatch.setenv("CARTERA_OKX_ENV", str(tmp_path / "no-existe.env"))

    def no_debe_llamarse(path, headers):
        raise AssertionError("no debía consultar OKX sin configuración")

    monkeypatch.setattr(okx, "http_get", no_debe_llamarse)
    r = cliente.post("/api/cartera/okx/sync", headers=HDR)
    assert r.status_code == 400
    assert "OKX_API_KEY" in r.get_json()["error"]


def test_endpoint_sincroniza_y_devuelve_conteos(cliente, db_temporal, monkeypatch):
    get = _fake_get(paginas_archive=[_compra() + _venta()])
    monkeypatch.setattr(okx, "http_get", get)
    r = cliente.post("/api/cartera/okx/sync", headers=HDR)
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["added"] == 2 and datos["existing"] == 0 and datos["skipped"] == 0
    assert _contar(db_temporal) == 2
    texto = r.get_data(as_text=True)
    assert "secreto-de-prueba" not in texto and "clave-falsa" not in texto


def test_endpoint_error_de_okx_responde_502(cliente, monkeypatch):
    def falla(path, headers):
        raise okx.OkxError("sin respuesta de OKX")

    monkeypatch.setattr(okx, "http_get", falla)
    r = cliente.post("/api/cartera/okx/sync", headers=HDR)
    assert r.status_code == 502


# ── línea de órdenes (timer) ──────────────────────────────────────────────
# El timer ejecuta `okx_sync.py`: stdout es SOLO el JSON de conteos; los
# errores van a stderr, sin credenciales, y el código de salida lo dice todo.

def _cli():
    import okx_sync
    return okx_sync


def test_cli_sin_configuracion_sale_con_1_y_no_toca_la_red(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CARTERA_OKX_ENV", str(tmp_path / "no-existe.env"))

    def no_debe_llamarse(path, headers):
        raise AssertionError("no debía consultar OKX sin configuración")

    monkeypatch.setattr(okx, "http_get", no_debe_llamarse)
    assert _cli().main([]) == 1
    salida = capsys.readouterr()
    assert salida.out == ""
    assert "OKX_API_KEY" in salida.err


def test_cli_imprime_solo_los_conteos_y_sale_con_0(db_temporal, tmp_path, monkeypatch, capsys):
    ruta_env = tmp_path / ".env"
    ruta_env.write_text("OKX_API_KEY=clave-falsa\nOKX_API_SECRET=secreto-de-prueba\n"
                        "OKX_API_PASSPHRASE=frase-falsa\n", encoding="utf-8")
    monkeypatch.setenv("CARTERA_OKX_ENV", str(ruta_env))
    monkeypatch.setattr(okx, "http_get", _fake_get(paginas_archive=[_compra() + _venta()]))
    assert _cli().main([]) == 0
    salida = capsys.readouterr()
    datos = json.loads(salida.out)
    assert set(datos) == {"added", "existing", "skipped", "skipped_reasons", "truncated"}
    assert datos["added"] == 2
    assert _contar(db_temporal) == 2
    assert "secreto-de-prueba" not in salida.out + salida.err


def test_cli_error_de_okx_sale_con_1_sin_credenciales(db_temporal, tmp_path, monkeypatch, capsys):
    ruta_env = tmp_path / ".env"
    ruta_env.write_text("OKX_API_KEY=clave-falsa\nOKX_API_SECRET=secreto-de-prueba\n"
                        "OKX_API_PASSPHRASE=frase-falsa\n", encoding="utf-8")
    monkeypatch.setenv("CARTERA_OKX_ENV", str(ruta_env))

    def falla(path, headers):
        raise okx.OkxError("sin respuesta de OKX")

    monkeypatch.setattr(okx, "http_get", falla)
    assert _cli().main([]) == 1
    salida = capsys.readouterr()
    assert salida.out == ""
    assert "sin respuesta de OKX" in salida.err
    assert "secreto-de-prueba" not in salida.err and "clave-falsa" not in salida.err


# ── posiciones de derivados (futuros) ─────────────────────────────────────

def _pos(inst="BTC-USD_UM_XPERP-310404", pos="20", margin="54.6", upl="-0.0096", ccy="USDC",
         lever="3", pos_side="net", ratio="-0.000175", mgn="isolated", imr=""):
    return {"instType": "FUTURES", "instId": inst, "pos": pos, "posSide": pos_side,
            "avgPx": "81900", "markPx": "81895.2", "liqPx": "54984.99", "lever": lever,
            "mgnMode": mgn, "margin": margin, "imr": imr, "upl": upl, "uplRatio": ratio,
            "ccy": ccy, "notionalUsd": "163.79", "cTime": str(TS)}


def test_resumir_posiciones_valora_margen_mas_resultado_en_euros():
    r = okx.resumir_posiciones([_pos(), _pos(inst="LIT-USD_UM_XPERP-310704", margin="54.48525",
                                             upl="0.0267", lever="2", ratio="0.00049")],
                               {"USDC": 0.9})
    btc, lit = r["positions"]
    assert btc["inst_id"] == "BTC-USD_UM_XPERP-310404"
    assert btc["side"] == "largo" and btc["contracts"] == 20 and btc["lever"] == 3
    assert btc["margin"] == 54.6 and btc["upl"] == -0.0096 and btc["ccy"] == "USDC"
    assert btc["value_eur"] == round((54.6 - 0.0096) * 0.9, 2)
    assert btc["upl_eur"] == round(-0.0096 * 0.9, 2)
    assert btc["upl_pct"] == -0.02
    assert btc["opened"] == "2026-10-08 01:30"
    assert lit["lever"] == 2
    assert r["total_eur"] == round((54.6 - 0.0096 + 54.48525 + 0.0267) * 0.9, 2)
    assert r["unvalued"] == []


def test_resumir_posiciones_ignora_cerradas_y_marca_las_sin_cambio():
    r = okx.resumir_posiciones([_pos(pos="0"), _pos(inst="X-USD", ccy="ZZZ")], {"USDC": 0.9})
    assert [p["inst_id"] for p in r["positions"]] == ["X-USD"]
    assert r["positions"][0]["value_eur"] is None
    assert r["unvalued"] == ["X-USD"]
    assert r["total_eur"] == 0


def test_resumir_posiciones_corto_y_margen_cruzado():
    r = okx.resumir_posiciones([_pos(pos="-5", margin="", imr="30", mgn="cross")], {"USDC": 1.0})
    p = r["positions"][0]
    assert p["side"] == "corto" and p["contracts"] == 5
    assert p["margin"] == 30 and p["value_eur"] == round(30 - 0.0096, 2)


def _fake_posiciones(filas, cambio="0.9"):
    llamadas = []

    def get(path, headers):
        llamadas.append(path)
        if path.startswith("/api/v5/account/positions"):
            return {"code": "0", "data": filas}
        if path.startswith("/api/v5/market/ticker"):
            inst = path.split("instId=")[1]
            return {"code": "0", "data": [{"instId": inst, "last": cambio}]}
        raise AssertionError(f"ruta inesperada {path}")
    get.llamadas = llamadas
    return get


def test_endpoint_futuros_devuelve_posiciones_en_euros(cliente, monkeypatch):
    get = _fake_posiciones([_pos()])
    monkeypatch.setattr(okx, "http_get", get)
    r = cliente.get("/api/cartera/futuros")
    assert r.status_code == 200
    d = r.get_json()
    assert d["positions"][0]["inst_id"] == "BTC-USD_UM_XPERP-310404"
    assert d["total_eur"] == round((54.6 - 0.0096) * 0.9, 2)
    assert d["fx"] == {"USDC": 0.9}
    assert "/api/v5/market/ticker?instId=USDC-EUR" in get.llamadas
    texto = r.get_data(as_text=True)
    assert "secreto-de-prueba" not in texto and "clave-falsa" not in texto


def test_endpoint_futuros_sin_configuracion_no_toca_la_red(cliente, monkeypatch, tmp_path):
    monkeypatch.setenv("CARTERA_OKX_ENV", str(tmp_path / "no-existe.env"))

    def no_debe_llamarse(path, headers):
        raise AssertionError("no debía consultar OKX sin configuración")

    monkeypatch.setattr(okx, "http_get", no_debe_llamarse)
    r = cliente.get("/api/cartera/futuros")
    assert r.status_code == 400
    assert "OKX_API_KEY" in r.get_json()["error"]


def test_endpoint_futuros_error_de_okx_responde_502(cliente, monkeypatch):
    def falla(path, headers):
        raise okx.OkxError("sin respuesta de OKX")

    monkeypatch.setattr(okx, "http_get", falla)
    r = cliente.get("/api/cartera/futuros")
    assert r.status_code == 502
    assert "sin respuesta de OKX" in r.get_json()["error"]


def test_endpoint_futuros_sin_cambio_deja_la_posicion_sin_valorar(cliente, monkeypatch):
    def get(path, headers):
        if path.startswith("/api/v5/account/positions"):
            return {"code": "0", "data": [_pos()]}
        return {"code": "51001", "msg": "Instrument ID does not exist", "data": []}

    monkeypatch.setattr(okx, "http_get", get)
    d = cliente.get("/api/cartera/futuros").get_json()
    assert d["unvalued"] == ["BTC-USD_UM_XPERP-310404"] and d["total_eur"] == 0


# ── instancias de otras personas ──────────────────────────────────────────

def test_instancia_con_base_propia_no_hereda_las_claves_del_propietario(monkeypatch):
    monkeypatch.delenv("CARTERA_OKX_ENV", raising=False)
    monkeypatch.setenv("CARTERA_DB", "/tmp/otra-persona/cartera.db")
    cfg, faltan = okx.leer_config()
    assert cfg is None and faltan == list(okx.CLAVES)


def test_instancia_con_base_propia_usa_su_propio_env(monkeypatch, tmp_path):
    propio = tmp_path / "propio.env"
    propio.write_text("OKX_API_KEY=a\nOKX_API_SECRET=b\nOKX_API_PASSPHRASE=c\n", encoding="utf-8")
    monkeypatch.setenv("CARTERA_DB", "/tmp/otra-persona/cartera.db")
    monkeypatch.setenv("CARTERA_OKX_ENV", str(propio))
    assert okx.leer_config()[0] == {"key": "a", "secret": "b", "passphrase": "c"}


def test_instancia_principal_sigue_usando_el_env_por_defecto(monkeypatch):
    monkeypatch.delenv("CARTERA_OKX_ENV", raising=False)
    monkeypatch.delenv("CARTERA_DB", raising=False)
    assert okx.ruta_env() == okx.ENV_DEFAULT
