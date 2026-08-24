"""Entorno de pruebas: un servidor HTTPS falso local, sin tocar la red.

Es el mismo servidor que usa 'python -m coipo_monitoreo demo', asi que lo que prueban
las pruebas es exactamente lo que se ve al ejecutar el demo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coipo_monitoreo.almacenamiento import Almacen
from coipo_monitoreo.config import cargar
from coipo_monitoreo.demo import (
    NotificadorMemoria,
    ServidorFalso,
    escribir_config,
    puerto_cerrado,
)
from coipo_monitoreo.runner import correr


@pytest.fixture
def servidor():
    with ServidorFalso() as s:
        yield s


@pytest.fixture
def entorno(servidor, tmp_path):
    ruta = escribir_config(tmp_path, servidor.puerto, puerto_cerrado())
    cfg = cargar(ruta)
    alm = Almacen(cfg.base_datos)
    buzon = NotificadorMemoria()

    def ronda():
        resultados, transiciones, _ = correr(cfg, alm, [buzon])
        return {r.dominio: r for r in resultados}, transiciones

    yield SimpleNamespace(
        cfg=cfg, alm=alm, buzon=buzon, srv=servidor, ronda=ronda, ruta=ruta
    )
    alm.cerrar()
