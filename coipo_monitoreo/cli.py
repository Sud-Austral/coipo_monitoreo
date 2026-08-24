"""Interfaz de linea de comandos.

Codigos de salida, pensados para cron y para encadenar con otras herramientas:
  0  todo en servicio
  1  hay al menos un dominio caido
  2  error de configuracion o del propio monitoreo
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from . import config as cfgmod
from .almacenamiento import Almacen
from .modelos import Severidad, a_local, hace
from .notificadores import Notificacion, construir, repartir
from .reporte import consola, escribir_html, tabla_estado, tabla_historial, tabla_resultados
from .runner import correr, preparar_log

CONFIG_POR_DEFECTO = os.environ.get("COIPO_CONFIG", "config/dominios.yml")


def _cargar(args) -> cfgmod.Config:
    try:
        return cfgmod.cargar(args.config)
    except cfgmod.ErrorConfig as e:
        consola.print(f"[red]Error de configuracion:[/]\n{e}")
        raise SystemExit(2) from e


def _abrir(cfg: cfgmod.Config) -> Almacen:
    return Almacen(cfg.base_datos)


# ------------------------------------------------------------------- comandos


def cmd_chequear(args) -> int:
    cfgmod.cargar_env(args.env)
    cfg = _cargar(args)
    preparar_log(cfg.archivo_log, args.verboso)

    notificadores = construir(cfg.alertas.canales) if not args.sin_alertas else []
    with _abrir(cfg) as alm:
        resultados, transiciones, dur = correr(
            cfg, alm, notificadores,
            solo=args.dominio or None,
            silencioso=args.sin_alertas,
            guardar=not args.sin_guardar,
        )

        if args.json:
            print(json.dumps(
                [_a_dict(r) for r in resultados], indent=2, ensure_ascii=False
            ))
        else:
            tabla_resultados(resultados, dur)
            if transiciones:
                consola.print()
                for t in transiciones:
                    marca = ("alerta enviada" if t.notificada
                             else (f"agrupada por {t.supresion}" if t.supresion
                                   else "sin enviar"))
                    consola.print(
                        f"  cambio: {t.dominio}: {t.anterior or '(nuevo)'} -> "
                        f"{t.nuevo.value}  [dim]({marca})[/]"
                    )
            elif not args.sin_guardar:
                consola.print("\n[dim]sin cambios de estado: no se envio ninguna "
                              "alerta (que es exactamente la intencion)[/]")

    return 1 if any(r.severidad is Severidad.CAIDO for r in resultados) else 0


def _a_dict(r) -> dict:
    d = dataclasses.asdict(r)
    d["estado"] = r.estado.value
    d["nivel"] = r.nivel.value
    d["severidad"] = r.severidad.value
    d["dns"] = {k: {"ips": v.ips, "error": v.error} for k, v in r.dns.items()}
    return d


def cmd_estado(args) -> int:
    cfg = _cargar(args)
    with _abrir(cfg) as alm:
        caidos = tabla_estado(alm)
    return 1 if caidos else 0


def cmd_historial(args) -> int:
    cfg = _cargar(args)
    with _abrir(cfg) as alm:
        eventos = alm.eventos(dominio=args.dominio, dias=args.dias, limite=args.limite)
        if args.dominio:
            consola.print(f"[bold]{args.dominio}[/] - cambios de los ultimos "
                          f"{args.dias} dias\n")
            fila = alm.estado(args.dominio)
            if fila:
                consola.print(
                    f"ahora: [bold]{fila['estado']}[/] desde {a_local(fila['desde'])} "
                    f"({hace(fila['desde'])})\n"
                )
        tabla_historial(eventos)
    return 0


def cmd_reporte(args) -> int:
    cfg = _cargar(args)
    with _abrir(cfg) as alm:
        destino = escribir_html(alm, args.salida, dias=args.dias)
    consola.print(f"reporte escrito en [cyan]{destino.resolve()}[/]")
    return 0


def cmd_probar_config(args) -> int:
    cfg = _cargar(args)
    efectivos = cfg.efectivos()
    consola.print(f"[green]configuracion valida[/]  ({cfg.origen})\n")
    consola.print(f"  dominios habilitados : {len(efectivos)} de {len(cfg.dominios)}")
    consola.print(f"  resolvers definidos  : {', '.join(sorted(cfg.resolvers)) or '-'}")
    consola.print(f"  canales de alerta    : {', '.join(cfg.alertas.canales)}")
    consola.print(f"  confirmaciones       : {cfg.alertas.confirmaciones} "
                  f"corridas antes de declarar un cambio")
    consola.print(f"  base de datos        : {cfg.base_datos}")
    consola.print(f"  archivo de log       : {cfg.archivo_log}\n")
    for d in efectivos:
        destino = (f"{d.ip_proxy}:{d.puerto} con Host" if d.via == "proxy"
                   else f"la IP que resuelva:{d.puerto}")
        consola.print(
            f"  - {d.nombre}: consulta {destino}, acepta {d.estados_ok}, "
            f"minimo {d.min_bytes} bytes"
            + (f", debe contener '{d.texto_esperado}'" if d.texto_esperado else "")
        )
    return 0


def cmd_probar_alerta(args) -> int:
    cfgmod.cargar_env(args.env)
    cfg = _cargar(args)
    preparar_log(cfg.archivo_log, True)
    notificadores = construir(cfg.alertas.canales)
    consola.print(f"canales activos: {', '.join(n.nombre for n in notificadores)}")
    ok = repartir(notificadores, Notificacion(
        "[PRUEBA] coipo_monitoreo",
        "Esto es una prueba del canal de alertas de coipo_monitoreo.\n"
        "Si recibiste esto, el canal funciona.\n\n-- \ncoipo_monitoreo",
        "AVISO",
    ))
    consola.print("[green]enviado[/]" if ok else "[red]ningun canal pudo enviar[/]")
    return 0 if ok else 2


def cmd_demo(args) -> int:
    from .demo import ejecutar

    preparar_log(None, args.verboso)
    return ejecutar()


# ---------------------------------------------------------------------- parser


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="coipo_monitoreo",
        description="Monitoreo de disponibilidad de las aplicaciones web de CONAF. "
                    "Distingue fallas de DNS, de proxy y de contenido, porque las "
                    "resuelven equipos distintos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Empeza por:  python -m coipo_monitoreo demo",
    )
    p.add_argument("--config", default=CONFIG_POR_DEFECTO,
                   help=f"ruta del YAML de dominios (por defecto {CONFIG_POR_DEFECTO})")
    p.add_argument("--env", default=".env",
                   help="archivo con las credenciales de alerta (por defecto .env)")
    p.add_argument("--verboso", "-v", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("chequear", help="corre una ronda de chequeos y alerta cambios")
    c.add_argument("--dominio", action="append",
                   help="chequear solo este dominio (se puede repetir)")
    c.add_argument("--sin-alertas", action="store_true",
                   help="no enviar nada, solo mostrar")
    c.add_argument("--sin-guardar", action="store_true",
                   help="no tocar la base de datos (prueba en seco)")
    c.add_argument("--json", action="store_true", help="salida en JSON")
    c.set_defaults(func=cmd_chequear)

    e = sub.add_parser("estado", help="estado vigente de cada dominio y desde cuando")
    e.set_defaults(func=cmd_estado)

    h = sub.add_parser("historial", help="cambios de estado registrados")
    h.add_argument("--dominio")
    h.add_argument("--dias", type=int, default=30)
    h.add_argument("--limite", type=int, default=200)
    h.set_defaults(func=cmd_historial)

    r = sub.add_parser("reporte", help="genera un reporte HTML de un solo archivo")
    r.add_argument("--salida", default="datos/reporte.html")
    r.add_argument("--dias", type=int, default=7)
    r.set_defaults(func=cmd_reporte)

    t = sub.add_parser("probar-config", help="valida el YAML y muestra que se chequeara")
    t.set_defaults(func=cmd_probar_config)

    a = sub.add_parser("probar-alerta", help="manda una alerta de prueba")
    a.set_defaults(func=cmd_probar_alerta)

    d = sub.add_parser("demo", help="demostracion autocontenida, sin tocar la red")
    d.set_defaults(func=cmd_demo)

    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        consola.print("\n[yellow]interrumpido[/]")
        return 2
    except cfgmod.ErrorConfig as e:
        consola.print(f"[red]{e}[/]")
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
