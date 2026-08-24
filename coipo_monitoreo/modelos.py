"""Estados posibles de un dominio, en que nivel fallan y a quien le corresponde resolverlos.

La razon de ser de este modulo: desde el navegador las tres causas de "no se puede
acceder al sitio" se ven identicas, pero las resuelven equipos distintos. Aca queda
escrito, una sola vez, que significa cada falla y a quien hay que pedirle el arreglo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class Nivel(str, Enum):
    """Capa donde se detecto el problema."""

    DNS = "dns"
    TRANSPORTE = "transporte"
    UPSTREAM = "upstream"
    CONTENIDO = "contenido"
    MONITOR = "monitor"


class Severidad(str, Enum):
    OK = "OK"
    AVISO = "AVISO"
    CAIDO = "CAIDO"


class Estado(str, Enum):
    OK = "OK"

    DNS_NXDOMAIN = "DNS_NXDOMAIN"
    DNS_TIMEOUT = "DNS_TIMEOUT"
    DNS_SIN_RESPUESTA = "DNS_SIN_RESPUESTA"
    DNS_IP_INESPERADA = "DNS_IP_INESPERADA"

    PROXY_RECHAZADO = "PROXY_RECHAZADO"
    PROXY_TIMEOUT = "PROXY_TIMEOUT"
    TLS_FALLA = "TLS_FALLA"

    UPSTREAM_SIN_RESPUESTA = "UPSTREAM_SIN_RESPUESTA"
    HTTP_TIMEOUT = "HTTP_TIMEOUT"

    HTTP_5XX = "HTTP_5XX"
    HTTP_ESTADO_INESPERADO = "HTTP_ESTADO_INESPERADO"
    CONTENIDO_VACIO = "CONTENIDO_VACIO"
    CONTENIDO_SIN_TEXTO = "CONTENIDO_SIN_TEXTO"

    ERROR_MONITOR = "ERROR_MONITOR"


@dataclass(frozen=True)
class InfoEstado:
    nivel: Nivel
    severidad: Severidad
    resumen: str
    responsable: str
    accion: str


#: Quien resuelve cada cosa. Estos textos van tal cual dentro de la alerta, para que
#: el correo diga "pedile esto a esta persona" y no "el sitio no anda".
INFO: dict[Estado, InfoEstado] = {
    Estado.OK: InfoEstado(
        Nivel.CONTENIDO, Severidad.OK,
        "responde y el contenido es valido", "-", "-",
    ),
    Estado.DNS_NXDOMAIN: InfoEstado(
        Nivel.DNS, Severidad.CAIDO,
        "el nombre no existe en el DNS consultado",
        "Informatica (DNS institucional)",
        "Pedir que se restaure el registro A del dominio. Adjuntar la fecha exacta en "
        "que dejo de resolver, que sale de 'coipo_monitoreo historial'.",
    ),
    Estado.DNS_TIMEOUT: InfoEstado(
        Nivel.DNS, Severidad.CAIDO,
        "el servidor DNS no contesto dentro del tiempo limite",
        "Informatica (DNS institucional)",
        "Verificar que el servidor DNS este operativo y sea alcanzable desde el host "
        "que corre el monitoreo. Si fallan TODOS los dominios a la vez, el problema es "
        "el resolver o la red, no los dominios.",
    ),
    Estado.DNS_SIN_RESPUESTA: InfoEstado(
        Nivel.DNS, Severidad.CAIDO,
        "el dominio existe pero no tiene registro A",
        "Informatica (DNS institucional)",
        "Pedir que se agregue el registro A. La zona existe pero el nombre no apunta a "
        "ninguna direccion.",
    ),
    Estado.DNS_IP_INESPERADA: InfoEstado(
        Nivel.DNS, Severidad.AVISO,
        "resuelve, pero a una IP distinta de la declarada",
        "Informatica (DNS institucional)",
        "Confirmar si el cambio de IP fue intencional. Puede ser un repunte de DNS no "
        "avisado, o que el monitoreo este consultando el horizonte equivocado.",
    ),
    Estado.PROXY_RECHAZADO: InfoEstado(
        Nivel.TRANSPORTE, Severidad.CAIDO,
        "el equipo destino rechazo la conexion TCP",
        "Administrador del proxy",
        "Verificar que nginx este corriendo y escuchando en el puerto, y que no haya "
        "un firewall bloqueando desde el host de monitoreo.",
    ),
    Estado.PROXY_TIMEOUT: InfoEstado(
        Nivel.TRANSPORTE, Severidad.CAIDO,
        "el equipo destino no respondio la conexion TCP",
        "Administrador del proxy",
        "Revisar carga del proxy, reglas de firewall y ruteo hacia la IP destino.",
    ),
    Estado.TLS_FALLA: InfoEstado(
        Nivel.TRANSPORTE, Severidad.CAIDO,
        "la conexion TCP abre pero el handshake TLS falla",
        "Administrador del proxy",
        "Revisar el certificado y la configuracion TLS del bloque server. Puede ser un "
        "certificado vencido, mal cargado o una version de TLS no soportada.",
    ),
    Estado.UPSTREAM_SIN_RESPUESTA: InfoEstado(
        Nivel.UPSTREAM, Severidad.CAIDO,
        "TLS completa pero el servidor corta sin enviar respuesta HTTP",
        "Administrador del proxy + equipo de la aplicacion",
        "El proxy acepto la conexion y despues no pudo entregar nada: casi siempre el "
        "upstream (contenedor de la aplicacion) esta caido, o el bloque server apunta a "
        "un upstream que ya no existe. Revisar 'docker ps' en la VM de la aplicacion y "
        "el error.log de nginx.",
    ),
    Estado.HTTP_TIMEOUT: InfoEstado(
        Nivel.UPSTREAM, Severidad.CAIDO,
        "la conexion abre pero la respuesta HTTP nunca llega",
        "Equipo de la aplicacion",
        "La aplicacion acepta la conexion pero no responde: revisar si el proceso esta "
        "trabado, sin conexion a base de datos o sin workers disponibles.",
    ),
    Estado.HTTP_5XX: InfoEstado(
        Nivel.CONTENIDO, Severidad.CAIDO,
        "la aplicacion responde con error de servidor",
        "Equipo de la aplicacion",
        "Revisar los logs de la aplicacion. El proxy y la red estan bien: el error es "
        "de la aplicacion misma.",
    ),
    Estado.HTTP_ESTADO_INESPERADO: InfoEstado(
        Nivel.CONTENIDO, Severidad.CAIDO,
        "el codigo HTTP no esta entre los aceptados para este dominio",
        "Equipo de la aplicacion",
        "Comparar contra 'estados_ok' en la configuracion. Si el cambio es legitimo "
        "(por ejemplo, ahora redirige a login), actualizar la configuracion.",
    ),
    Estado.CONTENIDO_VACIO: InfoEstado(
        Nivel.CONTENIDO, Severidad.CAIDO,
        "responde 200 pero el cuerpo esta vacio o es demasiado chico",
        "Administrador del proxy (falta el vhost) o equipo de la aplicacion",
        "Es la firma tipica del catch-all: el dominio llega al proxy pero no tiene "
        "bloque server propio, y el proxy contesta 200 con 0 bytes. Un monitoreo que "
        "solo mire el codigo HTTP da este caso por bueno. Pedir que se cree el vhost.",
    ),
    Estado.CONTENIDO_SIN_TEXTO: InfoEstado(
        Nivel.CONTENIDO, Severidad.CAIDO,
        "responde con cuerpo, pero falta el texto que debe aparecer",
        "Equipo de la aplicacion",
        "La aplicacion devuelve algo, pero no lo esperado: puede estar sirviendo una "
        "pagina de error, un placeholder o la aplicacion equivocada.",
    ),
    Estado.ERROR_MONITOR: InfoEstado(
        Nivel.MONITOR, Severidad.AVISO,
        "el chequeo mismo fallo",
        "Quien opera el monitoreo",
        "No es una falla del dominio: fallo el monitoreo. Revisar el detalle del error.",
    ),
}


def info(estado: Estado) -> InfoEstado:
    return INFO[estado]


def responsable_de(estado: Estado, equipo: str = "") -> str:
    """Quien tiene que arreglarlo.

    Lo define el NIVEL donde fallo, no el dominio: si iam.conaf.cl deja de resolver,
    el arreglo es de Informatica aunque el dueno de la aplicacion sea otro equipo. El
    equipo declarado en la configuracion se agrega solo cuando la falla es de la
    aplicacion misma, que es cuando sirve saber a cual llamar.
    """
    if estado is Estado.OK:
        return equipo or "-"          # nadie tiene que arreglar nada: se muestra el dueno
    i = info(estado)
    if equipo and i.nivel in (Nivel.CONTENIDO, Nivel.UPSTREAM):
        return f"{i.responsable} ({equipo})"
    return i.responsable


@dataclass
class ResultadoDNS:
    """Lo que devolvio un resolver puntual. Se guarda uno por resolver consultado."""

    resolver: str
    servidores: list[str]
    ips: list[str] = field(default_factory=list)
    error: str | None = None
    ms: float | None = None

    @property
    def ip(self) -> str | None:
        return self.ips[0] if self.ips else None


@dataclass
class Resultado:
    """Resultado completo de un dominio en una corrida."""

    dominio: str
    ts: str
    estado: Estado = Estado.OK
    detalle: str = ""
    #: Dueno de la aplicacion, declarado en la configuracion. No es lo mismo que el
    #: responsable del arreglo: ver responsable_de().
    equipo: str = ""

    ip_resuelta: str | None = None
    ip_consultada: str | None = None
    http_estado: int | None = None
    bytes: int | None = None

    ms_dns: float | None = None
    ms_tls: float | None = None
    ms_http: float | None = None

    cert_dias: int | None = None
    cert_asunto: str | None = None

    #: Problemas encontrados en OTROS niveles ademas del que define el estado.
    #: Existe por un caso real: archivo.conaf.cl no resuelve en DNS *y ademas* el proxy
    #: no tiene vhost para el. Si solo reportaramos la primera falla, arreglar el DNS
    #: dejaria el sitio en blanco y nadie sabria por que.
    hallazgos: list[str] = field(default_factory=list)

    #: Resultado por resolver, para ver el DNS de horizonte partido.
    dns: dict[str, ResultadoDNS] = field(default_factory=dict)

    @property
    def responsable(self) -> str:
        return responsable_de(self.estado, self.equipo)

    @property
    def nivel(self) -> Nivel:
        return info(self.estado).nivel

    @property
    def severidad(self) -> Severidad:
        return info(self.estado).severidad

    @property
    def ok(self) -> bool:
        return self.severidad is Severidad.OK


def ahora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


#: Formato de guardado: UTC, sin zona explicita y con espacio separador. Es el mismo
#: que produce datetime('now') en SQLite, asi que las comparaciones por fecha dentro
#: de las consultas son exactas en vez de una comparacion de texto que casi funciona.
FORMATO = "%Y-%m-%d %H:%M:%S"


def sello() -> str:
    """Marca de tiempo UTC, que es como se guarda todo."""
    return ahora().strftime(FORMATO)


def desde_sello(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, FORMATO).replace(tzinfo=dt.timezone.utc)


def a_local(marca: str) -> str:
    """Convierte una marca UTC guardada a hora local legible, para mostrar."""
    try:
        return desde_sello(marca).astimezone().strftime(FORMATO)
    except (ValueError, TypeError):
        return marca or "-"


def hace(marca: str) -> str:
    """'hace 3 h 20 min' a partir de una marca UTC guardada."""
    try:
        delta = ahora() - desde_sello(marca)
    except (ValueError, TypeError):
        return "?"
    seg = int(delta.total_seconds())
    if seg < 60:
        return f"hace {seg} s"
    if seg < 3600:
        return f"hace {seg // 60} min"
    if seg < 86400:
        return f"hace {seg // 3600} h {(seg % 3600) // 60} min"
    return f"hace {seg // 86400} d {(seg % 86400) // 3600} h"
