"""Deteccion de cambios de estado y despacho de alertas.

Regla central: se alerta cuando algo CAMBIA, nunca en cada corrida. Un monitoreo que
manda un correo cada 15 minutos se ignora en dos dias, y despues no sirve para nada.

Tres mecanismos evitan el ruido:
  1. Solo transiciones confirmadas. "Sigue caido" no genera nada.
  2. Amortiguacion de rebote: un cambio necesita N corridas consecutivas para darse por
     cierto, asi que un timeout aislado no despierta a nadie.
  3. Guarda anti-tormenta: si medio parque falla en el mismo nivel a la vez, eso no son
     catorce incidentes, es un problema de red o del propio monitoreo. Se manda una
     sola alerta agregada.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from .almacenamiento import Almacen
from .config import Alertas as CfgAlertas
from .modelos import Estado, Resultado, Severidad, a_local, hace, info, sello
from .notificadores import Notificacion, Notificador, repartir

log = logging.getLogger("coipo_monitoreo")


@dataclass
class Transicion:
    dominio: str
    anterior: str | None
    nuevo: Estado
    desde: str
    detalle: str
    responsable: str
    hallazgos: list[str] = field(default_factory=list)
    id_evento: int = 0
    notificada: bool = False
    supresion: str | None = None

    @property
    def recuperacion(self) -> bool:
        return info(self.nuevo).severidad is Severidad.OK


class Motor:
    def __init__(self, alm: Almacen, cfg: CfgAlertas, notificadores: list[Notificador],
                 silencioso: bool = False):
        self.alm = alm
        self.cfg = cfg
        self.notificadores = notificadores
        self.silencioso = silencioso

    # ------------------------------------------------------------------ transicion

    def procesar(self, resultados: list[Resultado]) -> list[Transicion]:
        transiciones = [t for r in resultados if (t := self._evaluar(r))]
        suprimidos = self._detectar_tormenta(transiciones, len(resultados))

        for t in transiciones:
            i = info(t.nuevo)
            t.supresion = "tormenta" if i.nivel.value in suprimidos and not t.recuperacion else None
            t.id_evento = self.alm.registrar_evento(
                t.dominio, t.anterior, t.nuevo, t.detalle,
                t.responsable or i.responsable, t.desde,
                notificado=False, supresion=t.supresion,
            )

        if suprimidos:
            self._alertar_tormenta(transiciones, suprimidos, len(resultados))

        for t in transiciones:
            if t.supresion:
                continue
            asunto, cuerpo = _texto(t)
            if self._despachar(asunto, cuerpo, info(t.nuevo).severidad.value):
                t.notificada = True
                self.alm.marcar_notificado(t.id_evento)

        self._avisar_certificados(resultados)
        self.alm.commit()
        return transiciones

    def _evaluar(self, r: Resultado) -> Transicion | None:
        fila = self.alm.estado(r.dominio)
        nuevo = r.estado

        # Dominio nuevo: se registra de inmediato. No hay rebote posible porque no hay
        # estado anterior, y en la primera corrida uno quiere el inventario completo de
        # lo que esta roto, no esperar media hora.
        if fila is None:
            self.alm.escribir_estado(
                r.dominio, nuevo, r.ts, r.ts, r.detalle, r.responsable, None, None, 0
            )
            if r.ok:
                return None
            return Transicion(r.dominio, None, nuevo, r.ts, r.detalle,
                              r.responsable, list(r.hallazgos))

        if fila["estado"] == nuevo.value:
            # Sin cambio: se refresca la ultima revision y se descarta cualquier
            # candidato a medio confirmar.
            self.alm.escribir_estado(
                r.dominio, nuevo, fila["desde"], r.ts, r.detalle, r.responsable,
                None, None, 0
            )
            return None

        # Hay un cambio. Cuenta cuantas corridas seguidas lleva viendose igual.
        if fila["candidato"] == nuevo.value:
            reps = int(fila["candidato_reps"]) + 1
            desde = fila["candidato_desde"] or r.ts
        else:
            reps = 1
            desde = r.ts

        if reps < self.cfg.confirmaciones:
            log.info(
                "%s: %s -> %s pendiente de confirmar (%d/%d)",
                r.dominio, fila["estado"], nuevo.value, reps, self.cfg.confirmaciones,
            )
            self.alm.escribir_estado(
                r.dominio, Estado(fila["estado"]), fila["desde"], r.ts, fila["detalle"],
                r.responsable, nuevo.value, desde, reps
            )
            return None

        # Confirmado. 'desde' es la PRIMERA observacion del estado nuevo, no el momento
        # de la confirmacion: es la fecha que se le muestra a quien tiene que arreglarlo.
        self.alm.escribir_estado(
            r.dominio, nuevo, desde, r.ts, r.detalle, r.responsable, None, None, 0
        )
        return Transicion(r.dominio, fila["estado"], nuevo, desde, r.detalle,
                          r.responsable, list(r.hallazgos))

    # -------------------------------------------------------------- anti-tormenta

    def _detectar_tormenta(self, transiciones: list[Transicion], total: int) -> set[str]:
        por_nivel: dict[str, int] = defaultdict(int)
        for t in transiciones:
            i = info(t.nuevo)
            if i.severidad is not Severidad.OK:
                por_nivel[i.nivel.value] += 1
        return {
            nivel for nivel, n in por_nivel.items()
            if n >= self.cfg.minimo_tormenta and total and n / total >= self.cfg.umbral_tormenta
        }

    def _alertar_tormenta(self, transiciones: list[Transicion], niveles: set[str],
                          total: int) -> None:
        afectados = [t for t in transiciones
                     if info(t.nuevo).nivel.value in niveles and not t.recuperacion]
        lista = "\n".join(f"  - {t.dominio}: {t.nuevo.value}" for t in afectados)
        nivel_txt = ", ".join(sorted(niveles))
        asunto = (f"[CAIDO] falla masiva en nivel {nivel_txt}: "
                  f"{len(afectados)} de {total} dominios")
        cuerpo = (
            f"{len(afectados)} de {total} dominios cambiaron a estado de falla en el "
            f"mismo nivel ({nivel_txt}) en la misma corrida.\n\n"
            "Eso casi nunca son N incidentes separados: es un problema de red, del "
            "resolver DNS, del proxy, o del host donde corre el monitoreo. Se manda una "
            "sola alerta en vez de una por dominio.\n\n"
            f"Dominios afectados:\n{lista}\n\n"
            "Primero verificar el monitoreo y la red desde el host que chequea, antes "
            "de escalar a los equipos de cada aplicacion.\n\n"
            "-- \ncoipo_monitoreo"
        )
        log.error("guarda anti-tormenta activada: %d/%d en nivel %s",
                  len(afectados), total, nivel_txt)
        if self._despachar(asunto, cuerpo, "CAIDO"):
            for t in afectados:
                self.alm.marcar_notificado(t.id_evento)
                t.notificada = True

    # -------------------------------------------------------------- certificados

    def _avisar_certificados(self, resultados: list[Resultado]) -> None:
        """El vencimiento del certificado se reporta aparte y nunca marca un dominio
        como caido: un certificado que vence en 20 dias no es una interrupcion.

        Se agrupa por certificado y no por dominio. El proxy sirve los catorce dominios
        con un unico comodin *.conaf.cl: avisar por dominio serian catorce correos que
        dicen exactamente lo mismo.
        """
        por_cert: dict[str, list[Resultado]] = defaultdict(list)
        for r in resultados:
            if r.cert_dias is not None and r.cert_dias <= self.cfg.cert_avisar_dias:
                por_cert[r.cert_asunto or "(certificado sin identificar)"].append(r)

        for asunto_cert, afectados in por_cert.items():
            dias = min(r.cert_dias for r in afectados)  # type: ignore[type-var]
            if not self.alm.aviso_cert_pendiente(asunto_cert, self.cfg.cert_repetir_cada_dias):
                continue
            vencido = dias < 0
            asunto = (f"[AVISO] certificado {'vencido' if vencido else 'por vencer'} "
                      f"({dias} dias, {len(afectados)} dominio(s))")
            lista = "\n".join(f"  - {r.dominio}" for r in sorted(afectados,
                                                                 key=lambda x: x.dominio))
            cuerpo = (
                f"Certificado:    {asunto_cert}\n"
                f"Dias restantes: {dias}\n\n"
                f"Dominios servidos por este certificado:\n{lista}\n\n"
                "Los sitios siguen respondiendo: esto es un aviso preventivo, no una "
                "interrupcion.\nResponsable: Administrador del proxy.\n\n"
                "-- \ncoipo_monitoreo"
            )
            if self._despachar(asunto, cuerpo, "AVISO"):
                self.alm.anotar_aviso_cert(asunto_cert, dias)

    # -------------------------------------------------------------------- despacho

    def _despachar(self, asunto: str, cuerpo: str, severidad: str) -> bool:
        if self.silencioso:
            log.info("(modo sin alertas) se habria enviado: %s", asunto)
            return False
        return repartir(self.notificadores, Notificacion(asunto, cuerpo, severidad))


def _texto(t: Transicion) -> tuple[str, str]:
    """Arma la alerta. Tiene que decir QUE fallo, DESDE CUANDO y A QUIEN pedirselo."""
    i = info(t.nuevo)
    if t.recuperacion:
        asunto = f"[OK] {t.dominio} se recupero"
    else:
        asunto = f"[{i.severidad.value}] {t.dominio} - {t.nuevo.value}"

    anterior = t.anterior or "(primera observacion)"
    lineas = [
        f"Dominio:      {t.dominio}",
        f"Estado:       {t.nuevo.value}  (antes: {anterior})",
        f"Nivel:        {i.nivel.value} - {i.resumen}",
        f"Desde:        {a_local(t.desde)}  ({hace(t.desde)})",
        f"Responsable:  {t.responsable or i.responsable}",
        "",
        "Que se midio:",
        f"  {t.detalle}",
    ]
    if not t.recuperacion:
        lineas += ["", "Que hay que pedir:", f"  {i.accion}"]
    if t.hallazgos:
        lineas += ["", "Ademas se detecto (problemas independientes del anterior):"]
        lineas += [f"  - {h}" for h in t.hallazgos]
    lineas += [
        "",
        "-- ",
        f"coipo_monitoreo  |  historial: python -m coipo_monitoreo historial "
        f"--dominio {t.dominio}",
    ]
    return asunto, "\n".join(lineas)


def marca_ahora() -> str:
    return sello()
