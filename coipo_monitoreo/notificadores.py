"""Canales de salida de las alertas.

El canal 'log' esta siempre activo y no necesita configuracion: aunque no haya relay
SMTP, el monitoreo deja constancia. Los canales que necesitan credenciales las leen del
entorno (o de un .env), nunca del YAML, porque el YAML se versiona en git.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger("coipo_monitoreo")


@dataclass
class Notificacion:
    asunto: str
    cuerpo: str
    severidad: str = "CAIDO"


class Notificador:
    nombre = "base"

    def disponible(self) -> tuple[bool, str]:
        return True, ""

    def enviar(self, n: Notificacion) -> None:  # pragma: no cover - interfaz
        raise NotImplementedError


class NotificadorLog(Notificador):
    nombre = "log"

    def enviar(self, n: Notificacion) -> None:
        nivel = logging.ERROR if n.severidad == "CAIDO" else logging.WARNING
        log.log(nivel, "ALERTA %s | %s\n%s", n.severidad, n.asunto, n.cuerpo)


class NotificadorEmail(Notificador):
    nombre = "email"

    def __init__(self) -> None:
        self.host = os.environ.get("SMTP_HOST", "")
        self.puerto = int(os.environ.get("SMTP_PUERTO", "25") or 25)
        self.usuario = os.environ.get("SMTP_USUARIO", "")
        self.clave = os.environ.get("SMTP_CLAVE", "")
        self.tls = os.environ.get("SMTP_TLS", "0") in ("1", "true", "si", "yes")
        self.de = os.environ.get("ALERTA_DE", "monitoreo@conaf.cl")
        self.para = [x.strip() for x in os.environ.get("ALERTA_PARA", "").split(",") if x.strip()]

    def disponible(self) -> tuple[bool, str]:
        if not self.host:
            return False, "falta SMTP_HOST en el entorno"
        if not self.para:
            return False, "falta ALERTA_PARA en el entorno"
        return True, ""

    def enviar(self, n: Notificacion) -> None:
        msg = EmailMessage()
        msg["Subject"] = n.asunto
        msg["From"] = self.de
        msg["To"] = ", ".join(self.para)
        msg.set_content(n.cuerpo)
        with smtplib.SMTP(self.host, self.puerto, timeout=20) as s:
            if self.tls:
                s.starttls()
            if self.usuario:
                s.login(self.usuario, self.clave)
            s.send_message(msg)
        log.info("alerta enviada por correo a %s", ", ".join(self.para))


class NotificadorWebhook(Notificador):
    nombre = "webhook"

    def __init__(self) -> None:
        self.url = os.environ.get("WEBHOOK_URL", "")

    def disponible(self) -> tuple[bool, str]:
        if not self.url:
            return False, "falta WEBHOOK_URL en el entorno"
        return True, ""

    def enviar(self, n: Notificacion) -> None:
        import httpx

        payload = {"text": f"{n.asunto}\n\n{n.cuerpo}",
                   "asunto": n.asunto, "cuerpo": n.cuerpo, "severidad": n.severidad}
        with httpx.Client(timeout=15, verify=False) as cli:
            r = cli.post(self.url, json=payload)
            r.raise_for_status()
        log.info("alerta enviada por webhook")


DISPONIBLES = {
    "log": NotificadorLog,
    "email": NotificadorEmail,
    "webhook": NotificadorWebhook,
}


def construir(canales: list[str]) -> list[Notificador]:
    """Instancia los canales pedidos y descarta con aviso los que no estan configurados.

    Un canal mal configurado nunca debe impedir que el monitoreo corra: se avisa y se
    sigue con los demas.
    """
    salida: list[Notificador] = []
    for c in canales:
        clase = DISPONIBLES.get(c)
        if clase is None:
            log.warning("canal de alerta desconocido, se ignora: %s", c)
            continue
        inst = clase()
        ok, motivo = inst.disponible()
        if not ok:
            log.warning("canal '%s' no configurado (%s): se omite", c, motivo)
            continue
        salida.append(inst)
    if not salida:
        salida.append(NotificadorLog())
    return salida


def repartir(notificadores: list[Notificador], n: Notificacion) -> bool:
    """Manda la notificacion por todos los canales. Devuelve si alguno lo logro."""
    algo = False
    for nt in notificadores:
        try:
            nt.enviar(n)
            algo = True
        except Exception as e:  # noqa: BLE001 - un canal caido no puede tumbar la corrida
            log.error("fallo el canal '%s': %s: %s", nt.nombre, type(e).__name__, e)
    return algo
