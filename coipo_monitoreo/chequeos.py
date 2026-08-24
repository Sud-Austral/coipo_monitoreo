"""Los tres niveles de chequeo, separados a proposito.

Desde el navegador, "no se puede acceder al sitio" puede ser un nombre que no resuelve,
un proxy que no contesta o una aplicacion que devuelve una pagina vacia. Son problemas
distintos, de equipos distintos. Este modulo los mide por separado y no colapsa el
resultado en un booleano.

Detalle importante de infraestructura: los dominios se consultan por IP mandando la
cabecera Host y el SNI explicito, no por nombre. Las VMs de aplicacion usan el DNS
publico, asi que desde ellas los nombres resuelven a la IP publica, que no pueden
alcanzar. Consultando 172.31.2.100 con Host: dominio se obtiene exactamente la misma
respuesta que tendria un usuario interno.
"""

from __future__ import annotations

import datetime as dt
import socket
import ssl
import time

import dns.exception
import dns.rdatatype
import dns.resolver
import httpx
from cryptography import x509

from .config import Config, DominioEfectivo
from .modelos import Estado, Resultado, ResultadoDNS, info, sello

#: Tope de lectura del cuerpo. No necesitamos la pagina entera para decidir si la
#: respuesta es real; solo que no este vacia y que contenga lo que debe contener.
LIMITE_CUERPO = 256 * 1024


# --------------------------------------------------------------------------- DNS


def resolver_dns(nombre: str, resolver_cfg, alias: str, timeout: float) -> ResultadoDNS:
    """Nivel 1: el nombre resuelve?"""
    t0 = time.perf_counter()

    if resolver_cfg.estatico is not None:
        ip = resolver_cfg.estatico.get(nombre)
        ms = (time.perf_counter() - t0) * 1000
        if ip is None:
            return ResultadoDNS(alias, [], error="NXDOMAIN", ms=ms)
        return ResultadoDNS(alias, [], ips=[ip], ms=ms)

    r = dns.resolver.Resolver(configure=False)
    r.nameservers = list(resolver_cfg.servidores)
    r.timeout = timeout
    r.lifetime = timeout
    try:
        resp = r.resolve(nombre, dns.rdatatype.A)
        ips = sorted(x.address for x in resp)
        return ResultadoDNS(alias, r.nameservers, ips=ips,
                            ms=(time.perf_counter() - t0) * 1000)
    except dns.resolver.NXDOMAIN:
        err = "NXDOMAIN"
    except dns.resolver.NoAnswer:
        err = "SIN_REGISTRO_A"
    except dns.resolver.NoNameservers:
        err = "RESOLVER_NO_RESPONDE"
    except (dns.exception.Timeout, dns.resolver.LifetimeTimeout):
        err = "TIMEOUT"
    except Exception as e:  # noqa: BLE001 - cualquier fallo del resolver es informativo
        err = f"ERROR:{type(e).__name__}"
    return ResultadoDNS(alias, r.nameservers, error=err,
                        ms=(time.perf_counter() - t0) * 1000)


def _estado_dns(rd: ResultadoDNS, esperadas: list[str]) -> tuple[Estado | None, str]:
    if rd.error == "NXDOMAIN":
        return Estado.DNS_NXDOMAIN, f"{rd.resolver}: el nombre no existe (NXDOMAIN)"
    if rd.error == "SIN_REGISTRO_A":
        return Estado.DNS_SIN_RESPUESTA, f"{rd.resolver}: la zona existe pero no hay registro A"
    if rd.error in ("TIMEOUT", "RESOLVER_NO_RESPONDE"):
        srv = ", ".join(rd.servidores) or "?"
        return Estado.DNS_TIMEOUT, f"{rd.resolver}: el servidor DNS {srv} no respondio"
    if rd.error:
        return Estado.DNS_TIMEOUT, f"{rd.resolver}: {rd.error}"
    if esperadas and not set(rd.ips) & set(esperadas):
        return (
            Estado.DNS_IP_INESPERADA,
            f"{rd.resolver}: resuelve a {', '.join(rd.ips)} y se esperaba "
            f"{', '.join(esperadas)}",
        )
    return None, f"{rd.resolver}: {', '.join(rd.ips)}"


# -------------------------------------------------------------------- transporte


def chequear_tls(ip: str, puerto: int, sni: str, timeout: float, verificar: bool):
    """Nivel 2: el equipo destino acepta TCP y completa el handshake TLS?

    Devuelve (estado_o_None, detalle, ms, info_cert). El certificado se lee siempre,
    aunque no se valide: el vencimiento se reporta por separado y nunca marca el
    dominio como caido por si solo.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if verificar:
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_default_certs()
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    t0 = time.perf_counter()
    try:
        with socket.create_connection((ip, puerto), timeout) as s:
            with ctx.wrap_socket(s, server_hostname=sni) as t:
                der = t.getpeercert(binary_form=True)
                version = t.version()
        ms = (time.perf_counter() - t0) * 1000
    except ConnectionRefusedError:
        return Estado.PROXY_RECHAZADO, f"{ip}:{puerto} rechazo la conexion TCP", \
            (time.perf_counter() - t0) * 1000, None
    except (socket.timeout, TimeoutError):
        return Estado.PROXY_TIMEOUT, f"{ip}:{puerto} no respondio en {timeout:g} s", \
            (time.perf_counter() - t0) * 1000, None
    except ssl.SSLError as e:
        return Estado.TLS_FALLA, f"handshake TLS fallo contra {ip}:{puerto}: {e.reason or e}", \
            (time.perf_counter() - t0) * 1000, None
    except socket.gaierror as e:
        return Estado.PROXY_RECHAZADO, f"no se pudo resolver el destino {ip}: {e}", \
            (time.perf_counter() - t0) * 1000, None
    except OSError as e:
        return Estado.PROXY_RECHAZADO, f"{ip}:{puerto} inalcanzable: {e}", \
            (time.perf_counter() - t0) * 1000, None

    return None, f"TLS {version} contra {ip}:{puerto}", ms, _leer_cert(der, sni)


def _leer_cert(der: bytes | None, dominio: str) -> dict | None:
    if not der:
        return None
    try:
        c = x509.load_der_x509_certificate(der)
    except Exception:  # noqa: BLE001
        return None
    try:
        vence = c.not_valid_after_utc
    except AttributeError:  # cryptography < 42
        vence = c.not_valid_after.replace(tzinfo=dt.timezone.utc)
    try:
        nombres = c.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        nombres = []
    return {
        "asunto": c.subject.rfc4514_string(),
        "nombres": nombres,
        "vence": vence.date().isoformat(),
        "dias": (vence - dt.datetime.now(dt.timezone.utc)).days,
        "cubre": _cubre(nombres, dominio),
    }


def _cubre(nombres: list[str], dominio: str) -> bool:
    for n in nombres:
        n = n.lower()
        if n == dominio:
            return True
        if n.startswith("*.") and dominio.endswith(n[1:]) and \
                dominio.count(".") == n.count("."):
            return True
    return False


# ---------------------------------------------------------------------- contenido


def chequear_http(d: DominioEfectivo, ip: str):
    """Niveles 2b y 3: contesta HTTP, y lo que contesta es real?

    El caso que motiva mirar el tamano del cuerpo: un catch-all de nginx devuelve
    HTTP 200 con 0 bytes cuando el dominio llega al proxy pero no tiene vhost. Un
    chequeo que solo mire el codigo HTTP da ese caso por bueno.
    """
    url = f"{d.esquema}://{ip}:{d.puerto}{d.ruta}"
    cabeceras = {
        "Host": d.nombre,
        "User-Agent": "coipo-monitoreo/1.0 (+monitoreo de disponibilidad CONAF)",
        "Accept": "*/*",
    }
    ext = {"sni_hostname": d.nombre} if d.esquema == "https" else {}

    t0 = time.perf_counter()
    try:
        with httpx.Client(
            verify=d.verificar_tls,
            timeout=d.timeout_s,
            follow_redirects=d.seguir_redirecciones,
        ) as cli:
            with cli.stream("GET", url, headers=cabeceras, extensions=ext) as r:
                cuerpo = bytearray()
                for trozo in r.iter_bytes():
                    cuerpo += trozo
                    if len(cuerpo) >= LIMITE_CUERPO:
                        break
                ms = (time.perf_counter() - t0) * 1000
                codigo = r.status_code
                destino = r.headers.get("location", "")
    except httpx.RemoteProtocolError:
        return (Estado.UPSTREAM_SIN_RESPUESTA,
                f"TLS completa contra {ip}:{d.puerto} pero el servidor cerro la conexion "
                f"sin enviar respuesta HTTP",
                (time.perf_counter() - t0) * 1000, None, None)
    except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
        return (Estado.HTTP_TIMEOUT,
                f"la conexion abrio pero no llego respuesta en {d.timeout_s:g} s",
                (time.perf_counter() - t0) * 1000, None, None)
    except httpx.ConnectTimeout:
        return (Estado.PROXY_TIMEOUT, f"{ip}:{d.puerto} no respondio la conexion",
                (time.perf_counter() - t0) * 1000, None, None)
    except httpx.ConnectError as e:
        return (Estado.PROXY_RECHAZADO, f"no se pudo conectar a {ip}:{d.puerto}: {e}",
                (time.perf_counter() - t0) * 1000, None, None)
    except Exception as e:  # noqa: BLE001
        return (Estado.ERROR_MONITOR, f"{type(e).__name__}: {e}",
                (time.perf_counter() - t0) * 1000, None, None)

    n = len(cuerpo)

    if codigo >= 500:
        return Estado.HTTP_5XX, f"HTTP {codigo} ({n} bytes)", ms, codigo, n
    if codigo not in d.estados_ok:
        return (Estado.HTTP_ESTADO_INESPERADO,
                f"HTTP {codigo}, aceptados {d.estados_ok} ({n} bytes)", ms, codigo, n)

    # Una redireccion aceptada se valida por el codigo y el destino, no por el cuerpo:
    # un 303 a la pagina de login legitimamente no trae nada. Sin esta excepcion, todo
    # dominio que redirige a login se reportaria como contenido vacio.
    if 300 <= codigo < 400 and not d.seguir_redirecciones:
        return None, f"HTTP {codigo} hacia {destino or '(sin Location)'}", ms, codigo, n

    if n < d.min_bytes:
        extra = " - firma del catch-all: el dominio llega al proxy pero no tiene vhost" \
            if n == 0 else ""
        return (Estado.CONTENIDO_VACIO,
                f"HTTP {codigo} con {n} bytes, minimo esperado {d.min_bytes}{extra}",
                ms, codigo, n)
    if d.texto_esperado:
        texto = cuerpo.decode("utf-8", errors="replace")
        if d.texto_esperado.lower() not in texto.lower():
            return (Estado.CONTENIDO_SIN_TEXTO,
                    f"HTTP {codigo} con {n} bytes pero no aparece "
                    f"'{d.texto_esperado}'", ms, codigo, n)
    return None, f"HTTP {codigo} con {n} bytes", ms, codigo, n


# ----------------------------------------------------------------- orquestacion


def chequear_dominio(d: DominioEfectivo, cfg: Config) -> Resultado:
    """Corre los tres niveles y arma el resultado.

    El estado final es la PRIMERA falla en orden de capa (DNS gana sobre proxy, proxy
    sobre contenido), porque es la que hay que arreglar primero. Las fallas de las capas
    siguientes no se descartan: van en 'hallazgos'. Eso existe por un caso real:
    archivo.conaf.cl no resuelve en DNS y ademas el proxy no tiene vhost para el.
    Reportar solo la primera falla haria que, tras arreglar el DNS, el sitio siguiera
    en blanco sin explicacion.
    """
    r = Resultado(dominio=d.nombre, ts=sello(), equipo=d.equipo)
    fallas: list[tuple[Estado, str]] = []

    # --- Nivel 1: DNS
    principal = resolver_dns(d.nombre, cfg.resolvers[d.resolver], d.resolver, d.timeout_s)
    r.dns[d.resolver] = principal
    r.ms_dns = principal.ms
    r.ip_resuelta = principal.ip

    est, det = _estado_dns(principal, d.ips_esperadas)
    if est:
        fallas.append((est, det))

    for alias in d.resolvers_extra:
        if alias == d.resolver:
            continue
        extra = resolver_dns(d.nombre, cfg.resolvers[alias], alias, d.timeout_s)
        r.dns[alias] = extra
        e2, d2 = _estado_dns(extra, [])
        if e2:
            r.hallazgos.append(f"{info(e2).nivel.value}: {d2}")

    # --- A donde nos conectamos
    if d.via == "proxy":
        ip = d.ip_proxy
    else:
        ip = principal.ip
        if not ip:
            # Sin DNS y sin IP fija no hay nada mas que medir.
            r.estado, r.detalle = fallas[0]
            return r
    r.ip_consultada = ip

    # --- Nivel 2: TCP + TLS + certificado
    if d.esquema == "https":
        est, det, r.ms_tls, cert = chequear_tls(
            ip, d.puerto, d.nombre, d.timeout_s, d.verificar_tls
        )
        if cert:
            r.cert_dias = cert["dias"]
            r.cert_asunto = cert["asunto"]
            if not cert["cubre"]:
                r.hallazgos.append(
                    f"certificado: no cubre {d.nombre} (cubre {', '.join(cert['nombres']) or '?'})"
                )
        if est:
            fallas.append((est, det))
            r.estado, r.detalle = fallas[0]
            _anexar(r, fallas)
            return r

    # --- Niveles 2b y 3: HTTP y contenido
    est, det, r.ms_http, r.http_estado, r.bytes = chequear_http(d, ip)
    if est:
        fallas.append((est, det))

    if fallas:
        r.estado, r.detalle = fallas[0]
        _anexar(r, fallas)
    else:
        r.estado = Estado.OK
        r.detalle = det
    return r


def _anexar(r: Resultado, fallas: list[tuple[Estado, str]]) -> None:
    """Las fallas de capas posteriores a la que define el estado van como hallazgos."""
    for est, det in fallas[1:]:
        r.hallazgos.append(f"{info(est).nivel.value}: {det}")
