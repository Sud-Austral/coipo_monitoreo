"""Orquestacion de una corrida completa."""

from __future__ import annotations

import logging
import logging.handlers
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .alertas import Motor, Transicion
from .almacenamiento import Almacen
from .chequeos import chequear_dominio
from .config import Config
from .modelos import Estado, Resultado, Severidad, sello
from .notificadores import Notificador

log = logging.getLogger("coipo_monitoreo")


def preparar_log(archivo: str | Path | None, verboso: bool = False) -> None:
    log.setLevel(logging.DEBUG if verboso else logging.INFO)
    log.handlers.clear()

    consola = logging.StreamHandler()
    consola.setLevel(logging.DEBUG if verboso else logging.WARNING)
    consola.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    log.addHandler(consola)

    if archivo:
        Path(archivo).parent.mkdir(parents=True, exist_ok=True)
        arch = logging.handlers.RotatingFileHandler(
            archivo, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        arch.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")
        )
        log.addHandler(arch)


def correr(cfg: Config, alm: Almacen, notificadores: list[Notificador],
           solo: list[str] | None = None, silencioso: bool = False,
           guardar: bool = True) -> tuple[list[Resultado], list[Transicion], float]:
    """Chequea todos los dominios habilitados, guarda y alerta lo que cambio."""
    dominios = cfg.efectivos(solo)
    if not dominios:
        log.warning("no hay dominios habilitados para chequear")
        return [], [], 0.0

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(cfg.hilos, len(dominios)))) as pool:
        resultados = list(pool.map(lambda d: _seguro(d, cfg), dominios))
    duracion = time.perf_counter() - t0

    resultados.sort(key=lambda r: (r.severidad is Severidad.OK, r.dominio))

    if not guardar:
        return resultados, [], duracion

    for r in resultados:
        alm.guardar_chequeo(r)

    ok = sum(1 for r in resultados if r.severidad is Severidad.OK)
    avisos = sum(1 for r in resultados if r.severidad is Severidad.AVISO)
    caidos = sum(1 for r in resultados if r.severidad is Severidad.CAIDO)
    alm.guardar_corrida(duracion, len(resultados), ok, avisos, caidos)

    # Solo se olvidan dominios cuando se chequeo el parque completo; con --dominio
    # estariamos borrando el historial de estado de todos los demas.
    if solo is None:
        alm.olvidar([d.nombre for d in cfg.efectivos()])

    motor = Motor(alm, cfg.alertas, notificadores, silencioso=silencioso)
    transiciones = motor.procesar(resultados)

    if cfg.retencion_dias > 0:
        borrados = alm.purgar(cfg.retencion_dias)
        if borrados:
            log.info("purgados %d chequeos con mas de %d dias",
                     borrados, cfg.retencion_dias)
    alm.commit()

    log.info("corrida: %d dominios en %.1f s -> %d ok, %d avisos, %d caidos, "
             "%d cambios de estado",
             len(resultados), duracion, ok, avisos, caidos, len(transiciones))
    return resultados, transiciones, duracion


def _seguro(d, cfg: Config) -> Resultado:
    """Un dominio que revienta el chequeo no puede tumbar la corrida entera."""
    try:
        return chequear_dominio(d, cfg)
    except Exception as e:  # noqa: BLE001
        log.exception("fallo el chequeo de %s", d.nombre)
        return Resultado(
            dominio=d.nombre, ts=sello(), estado=Estado.ERROR_MONITOR,
            detalle=f"{type(e).__name__}: {e}", equipo=d.equipo,
        )
