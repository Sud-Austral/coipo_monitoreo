"""Interfaz HTTP para el modo contenedor.

Existe por dos razones concretas, ninguna de las cuales es "hacia falta un tablero":

  1. El pipeline de despliegue hace un smoke test contra GET /health en APP_PORT. Sin
     ese endpoint, el despliegue falla y la aplicacion no se puede desplegar por el
     camino estandar.
  2. Adentro de un contenedor no hay un timer de systemd que despierte al proceso, asi
     que el planificador vive dentro de este mismo proceso.

Decision importante: **/health responde 200 aunque haya dominios caidos.** Informa la
salud del MONITOREO, no la de lo monitoreado. Si devolviera error cuando una aplicacion
se cae, el despliegue fallaria justo cuando el monitoreo esta haciendo bien su trabajo,
y Docker reiniciaria el contenedor en un bucle. Para saber si el parque esta sano estan
/estado y la portada.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__
from .almacenamiento import Almacen
from .config import cargar
from .modelos import a_local, hace
from .planificador import Planificador
from .reporte import construir_html
from .runner import preparar_log

log = logging.getLogger("coipo_monitoreo")

RUTA_CONFIG = os.environ.get("COIPO_CONFIG", "config/dominios.yml")
INTERVALO_MIN = float(os.environ.get("COIPO_INTERVALO_MIN", "15"))


@asynccontextmanager
async def ciclo(app: FastAPI):
    cfg = cargar(RUTA_CONFIG)
    preparar_log(cfg.archivo_log, verboso=False)
    log.info("coipo_monitoreo %s iniciando en modo contenedor", __version__)

    app.state.base_datos = cfg.base_datos
    app.state.plan = Planificador(RUTA_CONFIG, INTERVALO_MIN)
    app.state.plan.start()
    try:
        yield
    finally:
        app.state.plan.detener()


app = FastAPI(
    title="coipo_monitoreo",
    version=__version__,
    description="Monitoreo de disponibilidad de las aplicaciones web de CONAF.",
    lifespan=ciclo,
)


def _filas(consulta) -> list[dict]:
    return [dict(f) for f in consulta]


def _ultima_corrida(alm: Almacen) -> sqlite3.Row | None:
    return alm.ultima_corrida()


@app.get("/health", tags=["operacion"])
def health():
    """Salud del monitoreo. Siempre 200 si el proceso responde.

    Es lo que consulta el smoke test del despliegue y el healthcheck de Docker.
    """
    plan: Planificador = app.state.plan
    datos = {
        "status": "ok",
        "servicio": "coipo_monitoreo",
        "version": __version__,
        "planificador_vivo": plan.is_alive(),
        "corridas_realizadas": plan.corridas,
        "intervalo_min": INTERVALO_MIN,
    }
    if plan.ultimo_error:
        datos["ultimo_error"] = plan.ultimo_error
    return datos


@app.get("/listo", tags=["operacion"])
def listo():
    """Disponibilidad real del monitoreo: 503 si hace rato que no logra chequear.

    Separado de /health a proposito. Si el smoke test del despliegue usara este, un
    contenedor recien levantado que todavia no completo su primera corrida haria
    fallar el despliegue.
    """
    with Almacen(app.state.base_datos) as alm:
        ult = _ultima_corrida(alm)
    if ult is None:
        return JSONResponse(
            {"listo": False, "motivo": "todavia no se completo ninguna corrida"},
            status_code=503,
        )
    from .modelos import ahora, desde_sello

    minutos = (ahora() - desde_sello(ult["ts"])).total_seconds() / 60
    vencido = minutos > INTERVALO_MIN * 2 + 5
    return JSONResponse(
        {
            "listo": not vencido,
            "ultima_corrida": a_local(ult["ts"]),
            "antiguedad_min": round(minutos, 1),
            "motivo": "la ultima corrida es demasiado vieja" if vencido else "al dia",
        },
        status_code=503 if vencido else 200,
    )


@app.get("/estado", tags=["consulta"])
def estado():
    """Estado vigente de cada dominio y desde cuando."""
    with Almacen(app.state.base_datos) as alm:
        filas = _filas(alm.estados())
        ult = _ultima_corrida(alm)
    for f in filas:
        f["desde_local"] = a_local(f["desde"])
        f["antiguedad"] = hace(f["desde"])
    return {
        "ultima_corrida": a_local(ult["ts"]) if ult else None,
        "total": len(filas),
        "ok": sum(1 for f in filas if f["severidad"] == "OK"),
        "avisos": sum(1 for f in filas if f["severidad"] == "AVISO"),
        "caidos": sum(1 for f in filas if f["severidad"] == "CAIDO"),
        "dominios": filas,
    }


@app.get("/eventos", tags=["consulta"])
def eventos(dias: int = Query(30, ge=1, le=3650),
            dominio: str | None = None,
            limite: int = Query(200, ge=1, le=2000)):
    """Cambios de estado registrados: la respuesta a 'desde cuando esta caido'."""
    with Almacen(app.state.base_datos) as alm:
        filas = _filas(alm.eventos(dominio=dominio, dias=dias, limite=limite))
    for f in filas:
        f["cuando_local"] = a_local(f["ts"])
    return {"dias": dias, "dominio": dominio, "total": len(filas), "eventos": filas}


@app.get("/", response_class=HTMLResponse, tags=["consulta"])
def portada(dias: int = Query(7, ge=1, le=365)):
    """El mismo reporte que genera 'coipo_monitoreo reporte', servido desde memoria."""
    with Almacen(app.state.base_datos) as alm:
        return HTMLResponse(construir_html(alm, dias=dias))
