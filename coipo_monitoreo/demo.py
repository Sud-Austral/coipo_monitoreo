"""Demostracion autocontenida: levanta un servidor HTTPS falso en 127.0.0.1 que
reproduce las fallas reales observadas en produccion, y corre el monitoreo contra el.

Sirve para dos cosas:
  1. Verificar el comportamiento completo sin depender de la red ni de que algo este
     efectivamente caido en produccion.
  2. Mostrar el punto que mas importa y que no se ve en una sola corrida: que las
     alertas salen SOLO cuando algo cambia.

Los mismos escenarios son los que usan las pruebas automatizadas.
"""

from __future__ import annotations

import datetime as dt
import http.server
import socket
import ssl
import tempfile
import threading
from pathlib import Path

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import config as cfgmod
from .almacenamiento import Almacen
from .modelos import Severidad
from .notificadores import Notificacion, Notificador
from .reporte import consola, tabla_estado, tabla_historial, tabla_resultados
from .runner import correr

#: Comportamiento inicial de cada host del servidor falso. Cada uno reproduce una
#: falla concreta vista en produccion el 12 de agosto de 2026.
GUION = {
    "sano.demo.local": "ok",            # responde bien
    "login.demo.local": "redirige",     # 303 a login: es correcto, no es una falla
    "vacio.demo.local": "vacio",        # 200 con 0 bytes -> catch-all sin vhost
    "corta.demo.local": "corta",        # TLS ok y corta sin responder -> upstream muerto
    "error.demo.local": "error",        # 500
    "sintexto.demo.local": "otro",      # responde, pero no lo que corresponde
}

PAGINA = (
    "<!doctype html><html><head><title>Sistema de prueba</title></head>"
    "<body><h1>Sistema de prueba</h1><p>Contenido real de la aplicacion, con "
    "suficientes bytes como para no confundirse con una respuesta vacia.</p>"
    "</body></html>"
).encode()

OTRA_PAGINA = (
    "<!doctype html><html><head><title>Bienvenido</title></head><body>"
    "<h1>Pagina por defecto del servidor web</h1><p>Esta no es la aplicacion "
    "esperada, pero devuelve bytes y un codigo 200.</p></body></html>"
).encode()


# --------------------------------------------------------------- servidor falso


def _certificado(dias_validez: int = 10) -> tuple[str, str]:
    """Certificado autofirmado de vida corta, para que el demo tambien muestre el
    aviso de certificado por vencer."""
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "*.demo.local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "coipo_monitoreo demo"),
    ])
    ahora = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(days=1))
        .not_valid_after(ahora + dt.timedelta(days=dias_validez))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("*.demo.local"), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(clave, hashes.SHA256())
    )
    carpeta = Path(tempfile.mkdtemp(prefix="coipo_demo_cert_"))
    pcert = carpeta / "cert.pem"
    pclave = carpeta / "clave.pem"
    pcert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pclave.write_bytes(
        clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(pcert), str(pclave)


class _Manejador(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    guion: dict[str, str] = {}

    def log_message(self, *_a):  # silencio
        pass

    def handle_one_request(self):
        # El escenario 'corta' cierra el socket a mano; las excepciones que eso provoca
        # al intentar vaciar el buffer son esperadas y no deben ensuciar la salida.
        try:
            super().handle_one_request()
        except (OSError, ValueError):
            self.close_connection = True

    def do_GET(self):  # noqa: N802
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        modo = self.guion.get(host, "vacio")  # sin vhost -> catch-all, igual que nginx

        if modo == "corta":
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return

        if modo == "ok":
            self._responder(200, PAGINA)
        elif modo == "otro":
            self._responder(200, OTRA_PAGINA)
        elif modo == "error":
            self._responder(500, b"error interno")
        elif modo == "redirige":
            self._responder(303, b"", {"Location": "https://iam.demo.local/login"})
        else:  # 'vacio': la firma del catch-all
            self._responder(200, b"")

    def _responder(self, codigo: int, cuerpo: bytes, extra: dict | None = None):
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if cuerpo:
            self.wfile.write(cuerpo)


class ServidorFalso:
    """Servidor HTTPS local que decide que responder segun la cabecera Host."""

    def __init__(self, guion: dict[str, str] | None = None):
        self.guion = dict(guion or GUION)
        cert, clave = _certificado()
        _Manejador.guion = self.guion
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Manejador)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, clave)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.puerto = self.httpd.socket.getsockname()[1]
        self.hilo = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.hilo.start()

    def cambiar(self, host: str, modo: str) -> None:
        self.guion[host] = modo
        _Manejador.guion = self.guion

    def detener(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.detener()


def puerto_cerrado() -> int:
    """Un puerto donde con seguridad no escucha nadie, para provocar PROXY_RECHAZADO."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def escribir_config(carpeta: Path, puerto: int, cerrado: int) -> Path:
    """Genera un YAML igual en forma al de produccion, con el puerto real del servidor."""
    estatico = {h: "127.0.0.1" for h in GUION}
    estatico["caido.demo.local"] = "127.0.0.1"
    # 'nodns.demo.local' se omite a proposito: los nombres ausentes son NXDOMAIN.

    doc = {
        "base_datos": str(carpeta / "monitoreo.db"),
        "archivo_log": str(carpeta / "demo.log"),
        "hilos": 8,
        "predeterminados": {
            "timeout_s": 4,
            "min_bytes": 50,
            "estados_ok": [200],
            "puerto": puerto,
            "ip_proxy": "127.0.0.1",
            "via": "proxy",
            "resolver": "demo",
            "ips_esperadas": ["127.0.0.1"],
        },
        "alertas": {"canales": ["log"], "confirmaciones": 2,
                    "umbral_tormenta": 0.6, "minimo_tormenta": 3},
        "resolvers": {
            "demo": {"estatico": estatico, "descripcion": "resolver simulado del demo"}
        },
        "dominios": [
            {"nombre": "sano.demo.local", "texto_esperado": "Sistema de prueba",
             "equipo": "equipo de la aplicacion", "notas": "camino feliz"},
            {"nombre": "login.demo.local", "estados_ok": [200, 303],
             "notas": "redirige a login: 303 es correcto para este dominio"},
            {"nombre": "vacio.demo.local",
             "notas": "200 con 0 bytes: el caso que un chequeo por codigo HTTP aprueba"},
            {"nombre": "corta.demo.local",
             "notas": "TLS completa y el servidor corta sin responder"},
            {"nombre": "error.demo.local", "notas": "HTTP 500"},
            {"nombre": "sintexto.demo.local", "texto_esperado": "Sistema de prueba",
             "notas": "responde bytes, pero no es la aplicacion esperada"},
            {"nombre": "caido.demo.local", "puerto": cerrado,
             "notas": "nadie escucha en el puerto"},
            {"nombre": "nodns.demo.local", "notas": "no existe en el DNS"},
        ],
    }
    ruta = carpeta / "dominios.demo.yml"
    ruta.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return ruta


# ------------------------------------------------------------------ notificador


class NotificadorMemoria(Notificador):
    """Guarda las alertas en memoria en vez de mandarlas, para poder mostrarlas."""

    nombre = "memoria"

    def __init__(self):
        self.recibidas: list[Notificacion] = []

    def enviar(self, n: Notificacion) -> None:
        self.recibidas.append(n)

    def vaciar(self) -> list[Notificacion]:
        salida, self.recibidas = self.recibidas, []
        return salida


# ------------------------------------------------------------------------ demo


def _titulo(texto: str) -> None:
    consola.print()
    consola.rule(f"[bold]{texto}[/]", style="cyan")


def _mostrar_alertas(buzon: NotificadorMemoria, esperado: str) -> None:
    alertas = buzon.vaciar()
    if not alertas:
        consola.print(f"[green]sin alertas[/]  ({esperado})")
        return
    consola.print(f"[bold]{len(alertas)} alerta(s) enviada(s)[/]  ({esperado})")
    for a in alertas:
        consola.print(f"\n[bold yellow]{a.asunto}[/]")
        for linea in a.cuerpo.splitlines():
            consola.print(f"  [dim]{linea}[/]")


def ejecutar() -> int:
    carpeta = Path(tempfile.mkdtemp(prefix="coipo_demo_"))
    consola.print(
        "[bold]Demostracion de coipo_monitoreo[/]\n"
        "Servidor HTTPS falso en 127.0.0.1 que reproduce las fallas reales de "
        "produccion.\nNo se toca la red ni ningun servidor de CONAF.\n"
    )

    with ServidorFalso() as srv:
        ruta = escribir_config(carpeta, srv.puerto, puerto_cerrado())
        consola.print(f"configuracion generada : [cyan]{ruta}[/]")
        consola.print(f"base de datos          : [cyan]{carpeta / 'monitoreo.db'}[/]")
        consola.print(f"servidor falso         : https://127.0.0.1:{srv.puerto}")

        cfg = cfgmod.cargar(ruta)
        buzon = NotificadorMemoria()
        alm = Almacen(cfg.base_datos)

        def ronda(titulo: str, nota: str, esperado: str):
            _titulo(titulo)
            if nota:
                consola.print(f"[dim]{nota}[/]\n")
            res, _tr, dur = correr(cfg, alm, [buzon])
            tabla_resultados(res, dur)
            consola.print()
            _mostrar_alertas(buzon, esperado)
            return res

        res = ronda(
            "Ronda 1 - primera corrida",
            "Ocho dominios, ocho comportamientos. Fijate que 'vacio' devuelve HTTP 200: "
            "un chequeo que solo mire el codigo lo da por bueno.",
            "inventario inicial: se avisa una vez lo que ya estaba roto",
        )

        ronda(
            "Ronda 2 - nada cambio",
            "Mismo estado que la ronda anterior. Este es el punto central del diseno.",
            "esperado: silencio total. Sigue caido no es una novedad",
        )

        srv.cambiar("sano.demo.local", "corta")
        ronda(
            "Ronda 3 - se cae sano.demo.local",
            "Simula que el contenedor de la aplicacion muere: el proxy acepta TLS y "
            "corta sin responder. Es la falla mas comun en produccion hoy.",
            "esperado: silencio. Un solo dato no confirma un cambio (amortiguacion)",
        )

        ronda(
            "Ronda 4 - se confirma la caida",
            "Segunda observacion consecutiva del mismo cambio.",
            "esperado: UNA alerta, con nivel, responsable y desde cuando",
        )

        srv.cambiar("sano.demo.local", "ok")
        ronda("Ronda 5 - se repara", "Vuelve el contenedor.",
              "esperado: silencio, todavia sin confirmar")

        ronda("Ronda 6 - se confirma la recuperacion", "",
              "esperado: UNA alerta de recuperacion")

        cfg.resolvers["demo"].estatico = {}
        ronda(
            "Ronda 7 - se cae el DNS entero",
            "Ahora ningun nombre resuelve. Sin proteccion, esto serian ocho alertas.",
            "esperado: silencio, pendiente de confirmar",
        )
        ronda(
            "Ronda 8 - guarda anti-tormenta",
            "Ocho de ocho dominios fallan en el mismo nivel y en la misma corrida.",
            "esperado: UNA sola alerta agregada, no ocho",
        )

        _titulo("Estado actual")
        tabla_estado(alm)

        _titulo("Historial de cambios: la tabla que contesta 'desde cuando'")
        tabla_historial(alm.eventos(dias=1, limite=40))

        alm.cerrar()

    consola.print(
        f"\n[bold green]Listo.[/] Los archivos quedaron en [cyan]{carpeta}[/]\n"
        "La base se puede abrir con: "
        f"[cyan]sqlite3 \"{carpeta / 'monitoreo.db'}\" \"select * from eventos\"[/]"
    )
    return 0
