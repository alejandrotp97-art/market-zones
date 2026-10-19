"""Operaciones spot de OKX, leídas con una API key de SOLO LECTURA.

Solo se usan dos endpoints GET de historial de facturación. No hay permisos de
trading ni de retirada, y el código no tiene ninguna ruta que los escriba.

Cada operación llega como dos apuntes (uno por divisa) enlazados por `tradeId`
o, si éste viene vacío (como en el histórico real), por `fillTime`.
Se convierte en UN movimiento solo si las dos patas cuadran; lo que no cuadra se
omite y se cuenta con su motivo. Nunca se adivina un importe.

Las claves se leen en cada llamada desde un fichero de entorno (`CARTERA_OKX_ENV`,
por defecto `/home/alex/oficina/.env`) y no se copian a ningún sitio.

Paginación (verificada en la documentación v5): los resultados vienen del más
nuevo al más antiguo; `after=<billId>` devuelve los apuntes ANTERIORES a ese id;
`limit` máximo 100. Se pide la página siguiente solo si la actual vino llena.
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from cartera.parsing import instrument_kind

HOST = "https://eea.okx.com"
ENV_DEFAULT = "/home/alex/oficina/.env"
CLAVES = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")
# El de archivo primero: es el que trae el histórico. El de «recientes» solo
# cubre los últimos días, así que se fusiona por billId, nunca se suma.
ENDPOINTS = ("/api/v5/account/bills-archive", "/api/v5/account/bills")
PAGE_LIMIT = 100
MAX_PAGES = 200            # tope duro: 20 000 apuntes por endpoint
TIMEOUT_S = 20
TZ_MADRID = ZoneInfo("Europe/Madrid")
KIND_CRIPTO = "Cripto"     # el mismo valor que pone Yahoo para CRYPTOCURRENCY
TOLERANCIA_PRECIO = 0.005  # 0.5 %: redondeo del importe frente a qty * px


class OkxError(Exception):
    """Fallo al hablar con OKX. El mensaje nunca contiene credenciales."""


# ── configuración ─────────────────────────────────────────────────────────

def ruta_env() -> str:
    """Archivo de claves. Una instancia con base propia (`CARTERA_DB`, la cartera de
    otra persona) nunca hereda las del propietario: sin su `CARTERA_OKX_ENV`, no hay
    claves, y ni la sincronización ni los futuros leen la cuenta de otro."""
    propio = os.environ.get("CARTERA_OKX_ENV")
    if propio:
        return propio
    return "" if os.environ.get("CARTERA_DB") else ENV_DEFAULT


def leer_env(path: str) -> dict:
    """KEY=VALUE por línea. Ignora vacías y comentarios; quita comillas y `export`."""
    try:
        with open(path, encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return {}
    env = {}
    for linea in texto.splitlines():
        s = linea.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        clave, _, valor = s.partition("=")
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        else:
            valor = valor.split(" #", 1)[0].strip()
        env[clave.strip()] = valor
    return env


def leer_config(path: str | None = None) -> tuple[dict | None, list[str]]:
    """(config, faltan). Sin las tres claves no hay config y se dice cuáles faltan."""
    env = leer_env(path or ruta_env())
    faltan = [k for k in CLAVES if not env.get(k)]
    if faltan:
        return None, faltan
    return ({"key": env["OKX_API_KEY"], "secret": env["OKX_API_SECRET"],
             "passphrase": env["OKX_API_PASSPHRASE"]}, [])


# ── firma y HTTP ──────────────────────────────────────────────────────────

def firmar(secret: str, timestamp: str, method: str, request_path: str) -> str:
    """base64(HMAC-SHA256(secret, timestamp + método + ruta-con-query))."""
    mensaje = (timestamp + method.upper() + request_path).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), mensaje, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _timestamp(ahora: datetime.datetime | None = None) -> str:
    """ISO-8601 UTC con milisegundos y sufijo Z, como exige OKX."""
    ahora = ahora or datetime.datetime.now(datetime.UTC)
    return ahora.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ahora.microsecond // 1000:03d}Z"


def cabeceras(cfg: dict, method: str, path: str, timestamp: str | None = None) -> dict:
    ts = timestamp or _timestamp()
    return {
        "OK-ACCESS-KEY": cfg["key"],
        "OK-ACCESS-SIGN": firmar(cfg["secret"], ts, method, path),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": cfg["passphrase"],
        "Content-Type": "application/json",
        # Sin un User-Agent de navegador, Cloudflare responde 403 (código 1010).
        "User-Agent": "Mozilla/5.0",
    }


def http_get(path: str, headers: dict) -> dict:
    """GET real contra OKX. Devuelve el JSON decodificado tal cual."""
    req = urllib.request.Request(HOST + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:      # antes que URLError: es su subclase
        raise OkxError(f"HTTP {e.code} de OKX") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OkxError("sin respuesta de OKX") from None
    except ValueError:
        raise OkxError("respuesta de OKX no es JSON") from None


def _datos(get, cfg: dict, path: str) -> list:
    resp = get(path, cabeceras(cfg, "GET", path))
    if str(resp.get("code")) != "0":
        raise OkxError(f"OKX devolvió código {resp.get('code')}: {resp.get('msg', '')}")
    return resp.get("data") or []


def _paginar(get, cfg: dict, endpoint: str) -> tuple[list, bool]:
    """Todas las páginas de un endpoint. Devuelve (filas, truncado)."""
    filas: list = []
    despues = None
    for _ in range(MAX_PAGES):
        q = {"instType": "SPOT", "limit": str(PAGE_LIMIT)}
        if despues:
            q["after"] = despues
        pagina = _datos(get, cfg, endpoint + "?" + urllib.parse.urlencode(q))
        filas.extend(pagina)
        if len(pagina) < PAGE_LIMIT:
            return filas, False
        ultimo = str(pagina[-1].get("billId") or "")
        if not ultimo or ultimo == despues:  # sin avance: no insistir
            return filas, False
        despues = ultimo
    return filas, True                        # tope alcanzado con páginas aún llenas


def descargar_bills(cfg: dict, get=None) -> tuple[list, bool]:
    """Apuntes de ambos endpoints, sin duplicar billId. Devuelve (filas, truncado)."""
    get = get or http_get
    vistos: dict = {}
    truncado = False
    for endpoint in ENDPOINTS:
        filas, trunc = _paginar(get, cfg, endpoint)
        truncado = truncado or trunc
        for r in filas:
            clave = r.get("billId") or ("sin-id", len(vistos))
            vistos.setdefault(clave, r)
    return list(vistos.values()), truncado


# ── apuntes → movimientos ─────────────────────────────────────────────────

def _num(valor, por_defecto=None):
    if valor is None or valor == "":
        return por_defecto
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _movimiento(inst: str, trade_id: str, fill_time: str, patas: list):
    """(movimiento, None) si las dos patas cuadran; (None, motivo) si no.

    `trade_id` y `fill_time` solo sirven para la nota: el identificador del
    movimiento es el billId de la pata base, que siempre viene informado.
    """
    if any(str(p.get("instType") or "SPOT") != "SPOT" for p in patas):
        return None, "no es spot"
    if len(patas) != 2 or inst.count("-") != 1:
        return None, "sin pareja completa de patas"
    base, quote = inst.split("-", 1)
    if not base or not quote:
        return None, "sin pareja completa de patas"
    b = [p for p in patas if p.get("ccy") == base]
    q = [p for p in patas if p.get("ccy") == quote]
    if len(b) != 1 or len(q) != 1:
        return None, "sin pareja completa de patas"
    b, q = b[0], q[0]

    bill_base = str(b.get("billId") or "").strip()
    if not bill_base:
        return None, "sin billId"

    bal_b, bal_q = _num(b.get("balChg")), _num(q.get("balChg"))
    fee_b, fee_q = _num(b.get("fee"), 0.0), _num(q.get("fee"), 0.0)
    px = _num(b.get("px")) or _num(q.get("px"))
    if None in (bal_b, bal_q, fee_b, fee_q, px):
        return None, "dato no numérico"
    if px <= 0:
        return None, "precio no válido"

    # balChg incluye la comisión (con signo negativo cuando se cobra); se quita
    # para quedarse con lo ejecutado.
    cantidad = abs(bal_b - fee_b)
    if cantidad <= 0:
        return None, "cantidad cero"
    importe = bal_q - fee_q
    if abs(importe) <= 0:
        return None, "cantidad cero"

    lado = "buy" if bal_b > 0 else "sell"
    if (lado == "buy") != (bal_q < 0):
        return None, "signos incoherentes entre patas"
    if abs(abs(importe) - cantidad * px) > TOLERANCIA_PRECIO * abs(importe):
        return None, "precio no cuadra con el importe"

    # Comisión expresada en la divisa cotizada: la de la pata EUR tal cual; la de
    # la pata base se convierte al precio de la operación.
    comision = -fee_q - fee_b * px

    ts = _num(b.get("ts")) or _num(q.get("ts"))
    if not ts:
        return None, "sin fecha"
    fecha = datetime.datetime.fromtimestamp(ts / 1000, TZ_MADRID).date().isoformat()

    return {
        "date": fecha, "ticker": inst, "name": "", "kind": instrument_kind(inst, KIND_CRIPTO),
        "side": lado, "quantity": cantidad, "price": px, "fee": comision,
        "note": (f"OKX tradeId {trade_id}" if trade_id
                 else f"OKX fillTime {fill_time}"),
        "source_id": f"okx:{inst}:{bill_base}",
    }, None


def movimientos_desde_bills(rows: list) -> tuple[list, list]:
    """Agrupa las patas de cada operación y mapea. Devuelve (movimientos, omitidas).

    Clave de agrupación: (instId, tradeId) si hay tradeId; si no, (instId, fillTime).
    `omitidas` es una lista de (referencia, motivo). Los apuntes que no son operación
    (type distinto de "30") se ignoran sin contarlos.
    """
    grupos: dict = {}
    omitidas: list = []
    for r in rows:
        if str(r.get("type")) != "30":
            continue
        tid = str(r.get("tradeId") or "").strip()
        fill = str(r.get("fillTime") or "").strip()
        inst = str(r.get("instId") or "").strip()
        if not inst:
            omitidas.append((tid or fill or "?", "sin instId"))
            continue
        if not tid and not fill:
            omitidas.append(("?", "sin tradeId ni fillTime"))
            continue
        clave = (inst, tid, "" if tid else fill)
        grupos.setdefault(clave, []).append(r)

    movs = []
    for (inst, tid, fill), patas in grupos.items():
        mov, motivo = _movimiento(inst, tid, fill, patas)
        if mov:
            movs.append(mov)
        else:
            omitidas.append((tid or fill, motivo))
    return movs, omitidas


# ── sincronización ────────────────────────────────────────────────────────

def sincronizar(c, cfg: dict, get=None) -> dict:
    """Descarga, mapea e inserta. Idempotente: `source_id` es único.

    Devuelve {added, existing, skipped, skipped_reasons, truncated}. No incluye
    importes ni credenciales.
    """
    rows, truncado = descargar_bills(cfg, get=get)
    movs, omitidas = movimientos_desde_bills(rows)
    anadidas = existentes = 0
    for m in movs:
        cur = c.execute(
            "INSERT OR IGNORE INTO movements(date,ticker,name,kind,side,quantity,"
            "price,fee,note,source_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (m["date"], m["ticker"], m["name"], m["kind"], m["side"], m["quantity"],
             m["price"], m["fee"], m["note"], m["source_id"]))
        if cur.rowcount:
            anadidas += 1
        else:
            existentes += 1
    motivos: dict = {}
    for _tid, motivo in omitidas:
        motivos[motivo] = motivos.get(motivo, 0) + 1
    return {"added": anadidas, "existing": existentes, "skipped": len(omitidas),
            "skipped_reasons": motivos, "truncated": truncado}


# ── posiciones de derivados ───────────────────────────────────────────────
# Un futuro no es poseer el activo: es un margen depositado más un resultado aún
# no cobrado. Por eso no entra en `movements` (cantidad x precio) y se lee en
# vivo, sin guardarlo: lo que vale ahora es lo que dice OKX ahora.

RUTA_POSICIONES = "/api/v5/account/positions"


def _cambio_eur(get, cfg: dict, moneda: str):
    """EUR por unidad de `moneda` (último del contado <moneda>-EUR de OKX). None si no hay par."""
    if moneda == "EUR":
        return 1.0
    try:
        filas = _datos(get, cfg, "/api/v5/market/ticker?instId=" + urllib.parse.quote(f"{moneda}-EUR"))
    except OkxError:
        return None
    ultimo = _num((filas[0] if filas else {}).get("last"))
    return ultimo if ultimo and ultimo > 0 else None


def resumir_posiciones(filas: list, cambios: dict) -> dict:
    """Posiciones abiertas con su valor en EUR = (margen + resultado no realizado) x cambio.

    Una posición cuya moneda de margen no tiene cambio queda sin valorar: se lista,
    pero no suma al total, y se nombra en `unvalued`.
    """
    posiciones, sin_valorar, total = [], [], 0.0
    for r in filas:
        pos = _num(r.get("pos"), 0.0) or 0.0
        if abs(pos) < 1e-12:
            continue
        lado = r.get("posSide")
        if lado not in ("long", "short"):
            lado = "long" if pos > 0 else "short"
        moneda = str(r.get("ccy") or "")
        # En margen aislado OKX da `margin`; en cruzado viene vacío y el que cuenta es `imr`.
        margen = _num(r.get("margin")) if r.get("margin") not in (None, "") else _num(r.get("imr"))
        resultado = _num(r.get("upl"), 0.0)
        fx = cambios.get(moneda)
        valor = (margen + resultado) * fx if (fx and margen is not None) else None
        ratio = _num(r.get("uplRatio"))
        apertura = _num(r.get("cTime"))
        inst = str(r.get("instId") or "")
        posiciones.append({
            "inst_id": inst, "inst_type": r.get("instType"),
            "side": "largo" if lado == "long" else "corto",
            "contracts": abs(pos), "avg_px": _num(r.get("avgPx")), "mark_px": _num(r.get("markPx")),
            "liq_px": _num(r.get("liqPx")), "lever": _num(r.get("lever")), "mgn_mode": r.get("mgnMode"),
            "ccy": moneda, "margin": margen, "upl": resultado,
            "upl_pct": round(ratio * 100, 2) if ratio is not None else None,
            "notional_usd": _num(r.get("notionalUsd")),
            "upl_eur": round(resultado * fx, 2) if fx else None,
            "value_eur": round(valor, 2) if valor is not None else None,
            "opened": (datetime.datetime.fromtimestamp(apertura / 1000, TZ_MADRID).strftime("%Y-%m-%d %H:%M")
                       if apertura else None),
        })
        if valor is None:
            sin_valorar.append(inst)
        else:
            total += valor
    return {"positions": posiciones, "total_eur": round(total, 2), "unvalued": sin_valorar}


def posiciones_abiertas(cfg: dict, get=None) -> dict:
    """Lee las posiciones abiertas de OKX (solo lectura) y las valora en EUR."""
    get = get or http_get
    filas = _datos(get, cfg, RUTA_POSICIONES)
    monedas = sorted({str(r.get("ccy") or "") for r in filas if (_num(r.get("pos"), 0.0) or 0.0) != 0})
    cambios = {m: fx for m in monedas if (fx := _cambio_eur(get, cfg, m)) is not None}
    return {**resumir_posiciones(filas, cambios), "fx": cambios}
