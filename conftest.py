"""Guardia de red para la suite.

Con `MZ_NO_NETWORK=1`, cualquier intento de abrir un socket hacia fuera falla.
Es la comprobación de que la suite es HERMÉTICA: si alguien añade mañana un test
que llama a Yahoo de verdad, se vuelve intermitente —falla el día que Yahoo esté
lento, o cuando se corre sin conexión— y contamina al resto sin que se sepa por
qué. Este guardia lo detecta el día que pasa.

POR QUÉ NO CON `iptables`, QUE ES COMO ESTABA
---------------------------------------------
El CI hacía `sudo iptables -P OUTPUT DROP` y repetía la suite. La idea era
correcta y el mecanismo no: esa regla corta TODO el tráfico saliente de la
máquina, incluida la conexión del propio runner con GitHub. El job se quedaba
sin poder reportar y aparecía colgado hasta agotar el tiempo — 45 minutos para
una suite que tarda 27 segundos, y sin un solo mensaje que dijera qué pasaba.

Un guardia dentro de pytest comprueba exactamente la misma propiedad, no
necesita root, no depende del sistema operativo del runner y, cuando salta,
dice el nombre del test y la dirección a la que intentó salir.

El bucle local (`127.0.0.1`, `::1`) se deja pasar: un test que levanta un
servidor de prueba y se habla a sí mismo no está dependiendo de la red.
"""
import os
import socket

import pytest

_BLOQUEADO = (os.environ.get("MZ_NO_NETWORK") or "").strip() == "1"
_LOCAL = {"127.0.0.1", "::1", "localhost", ""}


class RedProhibida(RuntimeError):
    pass


def _es_local(addr):
    host = addr[0] if isinstance(addr, (tuple, list)) and addr else addr
    return str(host) in _LOCAL


def pytest_configure(config):
    if not _BLOQUEADO:
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guard(fn, nombre):
        def envoltorio(self_or_addr, *a, **k):
            addr = a[0] if a and not isinstance(self_or_addr, (tuple, str)) else self_or_addr
            if _es_local(addr if not isinstance(addr, socket.socket) else ""):
                return fn(self_or_addr, *a, **k)
            raise RedProhibida(
                f"la suite intentó salir a la red ({nombre} -> {addr!r}). "
                "Todo lo que sale fuera tiene que estar simulado en el test.")
        return envoltorio

    def connect(self, addr, *a, **k):
        if _es_local(addr):
            return real_connect(self, addr, *a, **k)
        raise RedProhibida(f"la suite intentó conectar a {addr!r}. "
                           "Todo lo que sale fuera tiene que estar simulado.")

    def connect_ex(self, addr, *a, **k):
        if _es_local(addr):
            return real_connect_ex(self, addr, *a, **k)
        raise RedProhibida(f"la suite intentó conectar a {addr!r}.")

    def create_connection(addr, *a, **k):
        if _es_local(addr):
            return real_create(addr, *a, **k)
        raise RedProhibida(f"la suite intentó conectar a {addr!r}.")

    def getaddrinfo(host, *a, **k):
        # Se corta ya en la resolución de nombres: así el mensaje nombra el
        # HOST al que se quería ir y no una IP que no le dice nada a nadie.
        if str(host) in _LOCAL:
            return real_getaddrinfo(host, *a, **k)
        raise RedProhibida(f"la suite intentó resolver «{host}». "
                           "Todo lo que sale fuera tiene que estar simulado.")

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo


@pytest.fixture(scope="session")
def red_bloqueada():
    return _BLOQUEADO
