"""Carga y validacion de la configuracion.

La lista de dominios es un archivo YAML, no codigo: agregar un dominio no debe
requerir tocar el programa ni volver a desplegar nada.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ErrorConfig(Exception):
    """Configuracion invalida. Se reporta con el archivo y el campo exactos."""


class Resolver(BaseModel):
    """Un servidor DNS a consultar, o un mapa fijo para pruebas."""

    model_config = {"extra": "forbid"}

    servidores: list[str] = Field(default_factory=list)
    #: Mapa nombre -> IP. Los nombres ausentes se comportan como NXDOMAIN.
    #: Sirve para las pruebas y el demo, que no deben depender de la red.
    estatico: dict[str, str] | None = None
    descripcion: str = ""

    @model_validator(mode="after")
    def _uno_u_otro(self):
        if not self.servidores and self.estatico is None:
            raise ValueError("un resolver necesita 'servidores' o 'estatico'")
        return self


class Alertas(BaseModel):
    model_config = {"extra": "forbid"}

    canales: list[Literal["log", "email", "webhook"]] = Field(default_factory=lambda: ["log"])
    #: Corridas consecutivas con el mismo resultado antes de dar por cierto un cambio.
    #: Con 2 y frecuencia de 15 min, un pico aislado no despierta a nadie.
    confirmaciones: int = 2
    #: Si esta fraccion de los dominios falla en el MISMO nivel en la misma corrida,
    #: se manda una sola alerta agregada. Catorce correos simultaneos no son catorce
    #: incidentes: son un problema de red o del propio monitoreo.
    umbral_tormenta: float = 0.6
    minimo_tormenta: int = 3
    #: Dias restantes del certificado a partir de los cuales se avisa.
    cert_avisar_dias: int = 30
    #: No repetir el aviso de certificado antes de estos dias.
    cert_repetir_cada_dias: int = 7

    @field_validator("confirmaciones")
    @classmethod
    def _min_uno(cls, v: int) -> int:
        if v < 1:
            raise ValueError("confirmaciones debe ser >= 1")
        return v


class Predeterminados(BaseModel):
    model_config = {"extra": "forbid"}

    timeout_s: float = 6.0
    min_bytes: int = 300
    estados_ok: list[int] = Field(default_factory=lambda: [200])
    esquema: Literal["https", "http"] = "https"
    puerto: int = 443
    ruta: str = "/"
    #: 'proxy'   -> conectar a ip_proxy mandando cabecera Host y SNI (recomendado)
    #: 'resuelto'-> conectar a la IP que devolvio el DNS
    via: Literal["proxy", "resuelto"] = "proxy"
    ip_proxy: str | None = None
    resolver: str = "interno"
    resolvers_extra: list[str] = Field(default_factory=list)
    ips_esperadas: list[str] = Field(default_factory=list)
    seguir_redirecciones: bool = False
    #: El certificado del proxy no valida contra la IP: por defecto no se valida, y el
    #: vencimiento se reporta aparte en vez de tumbar el chequeo.
    verificar_tls: bool = False
    equipo: str = ""


class Dominio(BaseModel):
    model_config = {"extra": "forbid"}

    nombre: str
    habilitado: bool = True
    notas: str = ""

    timeout_s: float | None = None
    min_bytes: int | None = None
    estados_ok: list[int] | None = None
    esquema: Literal["https", "http"] | None = None
    puerto: int | None = None
    ruta: str | None = None
    via: Literal["proxy", "resuelto"] | None = None
    ip_proxy: str | None = None
    resolver: str | None = None
    resolvers_extra: list[str] | None = None
    ips_esperadas: list[str] | None = None
    seguir_redirecciones: bool | None = None
    verificar_tls: bool | None = None
    equipo: str | None = None
    texto_esperado: str | None = None

    @field_validator("nombre")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("el nombre del dominio no puede estar vacio")
        if "/" in v or ":" in v:
            raise ValueError("poner solo el nombre del host, sin esquema ni puerto")
        return v


class DominioEfectivo(BaseModel):
    """Un dominio con los predeterminados ya aplicados. Es lo que consume el chequeo."""

    nombre: str
    notas: str
    timeout_s: float
    min_bytes: int
    estados_ok: list[int]
    esquema: str
    puerto: int
    ruta: str
    via: str
    ip_proxy: str | None
    resolver: str
    resolvers_extra: list[str]
    ips_esperadas: list[str]
    seguir_redirecciones: bool
    verificar_tls: bool
    equipo: str
    texto_esperado: str | None


class Config(BaseModel):
    model_config = {"extra": "forbid"}

    predeterminados: Predeterminados = Field(default_factory=Predeterminados)
    alertas: Alertas = Field(default_factory=Alertas)
    resolvers: dict[str, Resolver] = Field(default_factory=dict)
    dominios: list[Dominio] = Field(default_factory=list)
    base_datos: str = "datos/monitoreo.db"
    archivo_log: str = "datos/coipo_monitoreo.log"
    #: Dias de historial de chequeos que se conservan. Los eventos no se borran nunca:
    #: son los que contestan "desde cuando esta caido" meses despues.
    retencion_dias: int = 90
    hilos: int = 8

    #: Ruta del archivo del que se cargo, para mensajes de error utiles.
    origen: str = Field(default="", exclude=True)

    @model_validator(mode="after")
    def _coherencia(self):
        nombres = [d.nombre for d in self.dominios]
        repetidos = {n for n in nombres if nombres.count(n) > 1}
        if repetidos:
            raise ValueError(f"dominios repetidos: {', '.join(sorted(repetidos))}")

        usados = set()
        for d in self.dominios:
            usados.add(d.resolver or self.predeterminados.resolver)
            usados.update(d.resolvers_extra or self.predeterminados.resolvers_extra)
        faltan = usados - set(self.resolvers)
        if faltan:
            raise ValueError(
                f"resolvers usados por algun dominio pero no definidos: "
                f"{', '.join(sorted(faltan))}"
            )

        for d in self.dominios:
            via = d.via or self.predeterminados.via
            ip = d.ip_proxy or self.predeterminados.ip_proxy
            if via == "proxy" and not ip:
                raise ValueError(
                    f"'{d.nombre}' usa via=proxy pero no hay 'ip_proxy' ni en el dominio "
                    f"ni en predeterminados"
                )
        return self

    def efectivos(self, solo: list[str] | None = None) -> list[DominioEfectivo]:
        """Aplica los predeterminados y devuelve los dominios listos para chequear."""
        p = self.predeterminados
        salida: list[DominioEfectivo] = []
        for d in self.dominios:
            if not d.habilitado:
                continue
            if solo and d.nombre not in solo:
                continue
            salida.append(
                DominioEfectivo(
                    nombre=d.nombre,
                    notas=d.notas,
                    timeout_s=_o(d.timeout_s, p.timeout_s),
                    min_bytes=_o(d.min_bytes, p.min_bytes),
                    estados_ok=_o(d.estados_ok, p.estados_ok),
                    esquema=_o(d.esquema, p.esquema),
                    puerto=_o(d.puerto, p.puerto),
                    ruta=_o(d.ruta, p.ruta),
                    via=_o(d.via, p.via),
                    ip_proxy=_o(d.ip_proxy, p.ip_proxy),
                    resolver=_o(d.resolver, p.resolver),
                    resolvers_extra=_o(d.resolvers_extra, p.resolvers_extra),
                    ips_esperadas=_o(d.ips_esperadas, p.ips_esperadas),
                    seguir_redirecciones=_o(d.seguir_redirecciones, p.seguir_redirecciones),
                    verificar_tls=_o(d.verificar_tls, p.verificar_tls),
                    equipo=_o(d.equipo, p.equipo),
                    texto_esperado=d.texto_esperado,
                )
            )
        return salida


def _o(valor, defecto):
    return defecto if valor is None else valor


def cargar(ruta: str | Path) -> Config:
    """Lee el YAML y devuelve la configuracion validada."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorConfig(f"no existe el archivo de configuracion: {ruta}")
    try:
        crudo = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ErrorConfig(f"{ruta}: YAML invalido\n{e}") from e
    if not isinstance(crudo, dict):
        raise ErrorConfig(f"{ruta}: se esperaba un mapa en la raiz del archivo")
    try:
        cfg = Config(**crudo)
    except ValidationError as e:
        detalle = "\n".join(
            f"  - {'.'.join(str(x) for x in err['loc']) or '(raiz)'}: {err['msg']}"
            for err in e.errors()
        )
        raise ErrorConfig(f"{ruta}: configuracion invalida\n{detalle}") from e
    cfg.origen = str(ruta)

    # Las rutas relativas del YAML se resuelven contra la carpeta del propio YAML, no
    # contra el directorio actual: cron y systemd arrancan desde cualquier lado y no
    # queremos que la base de datos aparezca en un lugar distinto segun quien lo llame.
    #
    # El entorno gana sobre el YAML. Lo necesita el contenedor: la imagen trae el YAML
    # con las rutas del repositorio, pero adentro la base tiene que vivir en el volumen
    # montado y no en la carpeta de la aplicacion. Sin esto habria que mantener dos
    # copias del mismo archivo de configuracion.
    base = ruta.resolve().parent
    cfg.base_datos = str(_absoluta(
        os.environ.get("COIPO_BASE_DATOS") or cfg.base_datos, base))
    cfg.archivo_log = str(_absoluta(
        os.environ.get("COIPO_ARCHIVO_LOG") or cfg.archivo_log, base))
    return cfg


def _absoluta(valor: str, base: Path) -> Path:
    p = Path(valor)
    return p if p.is_absolute() else (base / p).resolve()


def cargar_env(ruta: str | Path = ".env") -> None:
    """Carga variables de un .env sin dependencias externas.

    Las credenciales (SMTP, webhook) viven en el entorno, nunca en el YAML: el YAML se
    versiona en git y las credenciales no.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        os.environ.setdefault(clave, valor)
