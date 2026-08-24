"""La interfaz HTTP del modo contenedor.

Lo que se prueba acá no es cosmético: si /health no devuelve 200, el smoke test del
pipeline falla y la aplicación no se puede desplegar.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def cliente(entorno, monkeypatch):
    monkeypatch.setenv("COIPO_CONFIG", str(entorno.ruta))
    monkeypatch.setenv("COIPO_INTERVALO_MIN", "60")   # que no vuelva a correr durante la prueba
    import coipo_monitoreo.web as web

    importlib.reload(web)
    with TestClient(web.app) as c:
        # El planificador corre en su propio hilo: sin esperar la primera corrida, las
        # consultas de estado carrerean con el. En produccion no importa (/health
        # responde igual), pero una prueba que depende del reloj es una prueba inutil.
        assert web.app.state.plan.primera_lista.wait(60), "la primera corrida no termino"
        yield c


def test_health_devuelve_200_aunque_haya_dominios_caidos(cliente):
    """El caso que rompería el despliegue.

    /health informa la salud del MONITOREO, no la del parque. Si devolviera error
    porque una aplicación se cayó, el smoke test fallaría justo cuando el monitoreo
    está haciendo bien su trabajo, y Docker reiniciaría el contenedor en bucle.
    """
    estado = cliente.get("/estado").json()
    assert estado["caidos"] >= 1          # el escenario del demo tiene caídos

    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["planificador_vivo"] is True


def test_el_planificador_corre_solo_al_arrancar(cliente):
    """El smoke test consulta a los 5 segundos: tiene que haber datos ya."""
    assert cliente.get("/health").json()["corridas_realizadas"] >= 1
    assert cliente.get("/listo").status_code == 200


def test_estado_expone_el_parque_completo(cliente):
    d = cliente.get("/estado").json()

    assert d["total"] == 8                       # los 8 dominios del demo
    assert d["ok"] + d["avisos"] + d["caidos"] == d["total"]
    assert {"dominio", "estado", "desde", "desde_local", "antiguedad"} <= set(d["dominios"][0])


def test_eventos_contesta_desde_cuando(cliente):
    d = cliente.get("/eventos?dias=1").json()

    assert d["total"] >= 1
    assert "cuando_local" in d["eventos"][0]
    assert d["eventos"][0]["nuevo"]


def test_la_portada_sirve_el_reporte(cliente):
    r = cliente.get("/")

    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Monitoreo de disponibilidad" in r.text
    assert "Cambios de estado" in r.text
