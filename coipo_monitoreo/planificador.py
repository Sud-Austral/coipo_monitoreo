"""Corre los chequeos periodicamente dentro del proceso, para el modo contenedor.

En el modo recomendado (systemd o cron) esto no existe: el planificador es el sistema
operativo, que es mas confiable que cualquier bucle propio. Este modulo es para cuando
el monitoreo corre como contenedor y no hay un timer afuera que lo despierte.
"""

from __future__ import annotations

import logging
import threading
import time

from .almacenamiento import Almacen
from .config import Config, ErrorConfig, cargar
from .notificadores import construir
from .runner import correr

log = logging.getLogger("coipo_monitoreo")


class Planificador(threading.Thread):
    def __init__(self, ruta_config: str, intervalo_min: float = 15.0):
        super().__init__(name="planificador", daemon=True)
        self.ruta_config = ruta_config
        self.intervalo = max(60.0, intervalo_min * 60)
        self._parar = threading.Event()
        #: Se marca al terminar la primera corrida. Sirve para esperarla sin dormir a
        #: ciegas, tanto en las pruebas como en cualquier arranque.
        self.primera_lista = threading.Event()
        self.cfg: Config = cargar(ruta_config)
        self.notificadores = construir(self.cfg.alertas.canales)
        self.corridas = 0
        self.ultimo_error: str | None = None

    def detener(self, esperar: float = 30.0) -> None:
        """Corta el bucle y espera a que termine la corrida en curso.

        El join no es un detalle: sin el, al apagar el contenedor el hilo sigue
        escribiendo en la base mientras el proceso se cierra. Docker da 10 segundos
        antes del SIGKILL, tiempo de sobra para una corrida que dura menos de uno.
        """
        self._parar.set()
        if self.is_alive():
            self.join(timeout=esperar)

    def run(self) -> None:
        log.info("planificador iniciado: una corrida cada %.0f min",
                 self.intervalo / 60)
        while not self._parar.is_set():
            inicio = time.monotonic()
            self._una_vez()
            # Se descuenta lo que tardo la corrida para que el intervalo sea real y no
            # se vaya corriendo hacia adelante en cada vuelta.
            espera = max(5.0, self.intervalo - (time.monotonic() - inicio))
            if self._parar.wait(espera):
                break
        log.info("planificador detenido")

    def _una_vez(self) -> None:
        try:
            self._recargar_config()
            with Almacen(self.cfg.base_datos) as alm:
                correr(self.cfg, alm, self.notificadores)
            self.corridas += 1
            self.ultimo_error = None
        except Exception as e:  # noqa: BLE001 - el bucle no puede morir nunca
            self.ultimo_error = f"{type(e).__name__}: {e}"
            log.exception("fallo la corrida programada")
        finally:
            self.primera_lista.set()

    def _recargar_config(self) -> None:
        """Relee el YAML en cada vuelta: cambiar la lista de dominios no exige reiniciar.

        Si el archivo quedo invalido se conserva el anterior y se avisa. Un YAML mal
        editado no puede dejar el monitoreo apagado en silencio.
        """
        try:
            nueva = cargar(self.ruta_config)
        except ErrorConfig as e:
            log.error("la configuracion quedo invalida, se sigue usando la anterior: %s", e)
            return
        canales_antes = self.cfg.alertas.canales
        self.cfg = nueva
        if nueva.alertas.canales != canales_antes:
            self.notificadores = construir(nueva.alertas.canales)
            log.info("canales de alerta actualizados: %s", ", ".join(nueva.alertas.canales))
