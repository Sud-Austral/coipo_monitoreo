"""Salida para humanos: tablas en terminal y un reporte HTML de un solo archivo.

El reporte HTML es la respuesta a "y si alguien mas quiere ver el estado" sin montar
un servidor: es un archivo que se abre con doble clic o se adjunta a un correo.
"""

from __future__ import annotations

import html as _html
import sys
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .almacenamiento import Almacen
from .modelos import Resultado, Severidad, a_local, hace, info

# Cuando la salida se redirige a un archivo o a una tuberia, rich asume 80 columnas y
# la evidencia queda cortada justo donde importa. Con 120 el detalle entra completo.
consola = Console(width=None if sys.stdout.isatty() else 120)

COLOR = {"OK": "green", "AVISO": "yellow", "CAIDO": "red"}
ICONO = {"OK": "OK ", "AVISO": "!  ", "CAIDO": "X  "}


def _c(sev: str, texto: str) -> str:
    return f"[{COLOR.get(sev, 'white')}]{texto}[/]"


def tabla_resultados(resultados: list[Resultado], duracion: float = 0.0) -> None:
    t = Table(title=None, header_style="bold", box=None, pad_edge=False, show_lines=False)
    t.add_column("")
    t.add_column("dominio", no_wrap=True)
    t.add_column("estado", no_wrap=True)
    t.add_column("nivel", no_wrap=True)
    t.add_column("evidencia", overflow="fold")
    t.add_column("ms", justify="right", no_wrap=True)

    for r in resultados:
        sev = r.severidad.value
        ms = r.ms_http or r.ms_tls or r.ms_dns or 0
        t.add_row(
            _c(sev, ICONO[sev].strip()),
            r.dominio,
            _c(sev, r.estado.value),
            "-" if r.ok else r.nivel.value,
            r.detalle,
            f"{ms:.0f}",
        )
        for h in r.hallazgos:
            t.add_row("", "", "", "[dim]ademas[/]", f"[dim]{h}[/]", "")

    consola.print(t)

    ok = sum(1 for r in resultados if r.severidad is Severidad.OK)
    av = sum(1 for r in resultados if r.severidad is Severidad.AVISO)
    ca = sum(1 for r in resultados if r.severidad is Severidad.CAIDO)
    resumen = (f"{len(resultados)} dominios en {duracion:.1f} s   "
               f"[green]{ok} ok[/]   [yellow]{av} avisos[/]   [red]{ca} caidos[/]")
    consola.print(resumen)

    # Agrupado por certificado: el proxy sirve todos los dominios con un mismo comodin,
    # asi que una linea por dominio serian catorce lineas diciendo lo mismo.
    porvencer: dict[tuple[str, int], list[str]] = defaultdict(list)
    for r in resultados:
        if r.cert_dias is not None and r.cert_dias <= 30:
            porvencer[(r.cert_asunto or "?", r.cert_dias)].append(r.dominio)
    if porvencer:
        consola.print("")
        for (asunto, dias), dominios in sorted(porvencer.items(), key=lambda x: x[0][1]):
            consola.print(
                f"[yellow]certificado[/] vence en {dias} dias: {asunto}  "
                f"[dim]({len(dominios)} dominio(s))[/]"
            )


def tabla_estado(alm: Almacen) -> int:
    filas = alm.estados()
    if not filas:
        consola.print("[yellow]todavia no hay estado registrado: "
                      "corre 'python -m coipo_monitoreo chequear' primero[/]")
        return 0

    t = Table(header_style="bold", box=None, pad_edge=False)
    t.add_column("")
    t.add_column("dominio", overflow="fold")
    t.add_column("estado")
    t.add_column("desde")
    t.add_column("hace")
    t.add_column("responsable", overflow="fold")

    caidos = 0
    for f in filas:
        sev = f["severidad"]
        if sev == "CAIDO":
            caidos += 1
        pendiente = ""
        if f["candidato"]:
            pendiente = f"  [dim](cambiando a {f['candidato']}, {f['candidato_reps']} obs.)[/]"
        t.add_row(
            _c(sev, ICONO[sev].strip()),
            f["dominio"],
            _c(sev, f["estado"]) + pendiente,
            a_local(f["desde"]),
            hace(f["desde"]),
            (f["responsable"] or info_responsable(f["estado"])),
        )
    consola.print(t)

    ult = alm.ultima_corrida()
    if ult:
        consola.print(
            f"\nultima corrida: {a_local(ult['ts'])} ({hace(ult['ts'])}), "
            f"{ult['total']} dominios en {ult['duracion_s']:.1f} s"
        )
        if "hace" in hace(ult["ts"]) and _minutos(ult["ts"]) > 45:
            consola.print(
                "[yellow]aviso: la ultima corrida es vieja. El monitoreo puede no "
                "estar corriendo (revisar el timer o el cron).[/]"
            )
    return caidos


def _minutos(marca: str) -> float:
    from .modelos import ahora, desde_sello

    try:
        return (ahora() - desde_sello(marca)).total_seconds() / 60
    except Exception:  # noqa: BLE001
        return 0.0


def info_responsable(estado: str) -> str:
    from .modelos import Estado

    try:
        return info(Estado(estado)).responsable
    except ValueError:
        return "-"


def tabla_historial(eventos) -> None:
    if not eventos:
        consola.print("[dim]sin cambios de estado en el periodo consultado[/]")
        return
    t = Table(header_style="bold", box=None, pad_edge=False)
    t.add_column("cuando")
    t.add_column("dominio", overflow="fold")
    t.add_column("cambio", overflow="fold")
    t.add_column("nivel")
    t.add_column("aviso")
    for e in eventos:
        marca = "enviado" if e["notificado"] else (e["supresion"] or "no enviado")
        t.add_row(
            a_local(e["ts"]),
            e["dominio"],
            f"{e['anterior'] or '(nuevo)'} -> " + _c(e["severidad"], e["nuevo"]),
            e["nivel"] or "-",
            marca,
        )
    consola.print(t)


# ------------------------------------------------------------------------- HTML

_CSS = """
:root{--fondo:#ffffff;--texto:#1a1a1a;--suave:#666;--linea:#e3e3e3;--caja:#fafafa;
--ok:#1a7f37;--aviso:#9a6700;--caido:#c1121f;}
@media (prefers-color-scheme:dark){:root{--fondo:#16181c;--texto:#e6e6e6;--suave:#9aa0a6;
--linea:#2c2f36;--caja:#1d2026;--ok:#3fb950;--aviso:#d29922;--caido:#f85149;}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;background:var(--fondo);color:var(--texto);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.envoltorio{max-width:1000px;margin:0 auto}
h1{font-size:1.4rem;margin:0 0 .25rem}
.sub{color:var(--suave);font-size:.85rem;margin-bottom:1.5rem}
.tarjetas{display:flex;gap:.75rem;flex-wrap:wrap;margin-bottom:1.5rem}
.tarjeta{background:var(--caja);border:1px solid var(--linea);border-radius:8px;
padding:.75rem 1rem;min-width:110px}
.tarjeta b{display:block;font-size:1.6rem;line-height:1.1}
.tarjeta span{color:var(--suave);font-size:.78rem;text-transform:uppercase;
letter-spacing:.04em}
.desplaza{overflow-x:auto;border:1px solid var(--linea);border-radius:8px;
margin-bottom:2rem}
table{border-collapse:collapse;width:100%;font-size:.87rem}
th,td{text-align:left;padding:.5rem .75rem;border-bottom:1px solid var(--linea);
white-space:nowrap}
td.libre{white-space:normal}
th{background:var(--caja);font-weight:600;font-size:.78rem;text-transform:uppercase;
letter-spacing:.03em;color:var(--suave)}
tr:last-child td{border-bottom:none}
.OK{color:var(--ok);font-weight:600}
.AVISO{color:var(--aviso);font-weight:600}
.CAIDO{color:var(--caido);font-weight:600}
.pie{color:var(--suave);font-size:.8rem;border-top:1px solid var(--linea);
padding-top:1rem}
"""


def escribir_html(alm: Almacen, ruta: str | Path, dias: int = 7) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(construir_html(alm, dias), encoding="utf-8")
    return ruta


def construir_html(alm: Almacen, dias: int = 7) -> str:
    """Arma el reporte completo como texto.

    Separado de escribir_html porque la interfaz web lo sirve directo desde memoria,
    sin pasar por disco.
    """
    filas = alm.estados()
    eventos = alm.eventos(dias=dias, limite=100)
    disp = {f["dominio"]: f for f in alm.resumen_disponibilidad(dias)}
    ult = alm.ultima_corrida()

    ok = sum(1 for f in filas if f["severidad"] == "OK")
    av = sum(1 for f in filas if f["severidad"] == "AVISO")
    ca = sum(1 for f in filas if f["severidad"] == "CAIDO")

    def esc(x) -> str:
        return _html.escape(str(x if x is not None else "-"))

    cuerpo = [
        "<div class='envoltorio'>",
        "<h1>Monitoreo de disponibilidad &mdash; aplicaciones CONAF</h1>",
        f"<div class='sub'>Generado el {esc(a_local(ult['ts']) if ult else '-')}"
        f" &middot; ultima corrida {esc(hace(ult['ts']) if ult else '-')}</div>",
        "<div class='tarjetas'>",
        f"<div class='tarjeta'><b>{len(filas)}</b><span>dominios</span></div>",
        f"<div class='tarjeta'><b class='OK'>{ok}</b><span>en servicio</span></div>",
        f"<div class='tarjeta'><b class='AVISO'>{av}</b><span>con aviso</span></div>",
        f"<div class='tarjeta'><b class='CAIDO'>{ca}</b><span>caidos</span></div>",
        "</div>",
        "<div class='desplaza'><table><thead><tr>"
        "<th>Dominio</th><th>Estado</th><th>Desde</th><th>Antiguedad</th>"
        f"<th>Disponible {dias}d</th><th>Responsable</th>"
        "</tr></thead><tbody>",
    ]
    for f in filas:
        d = disp.get(f["dominio"])
        pct = f"{100 * d['ok'] / d['total']:.1f}%" if d and d["total"] else "-"
        cuerpo.append(
            f"<tr><td>{esc(f['dominio'])}</td>"
            f"<td class='{esc(f['severidad'])}'>{esc(f['estado'])}</td>"
            f"<td>{esc(a_local(f['desde']))}</td><td>{esc(hace(f['desde']))}</td>"
            f"<td>{pct}</td>"
            f"<td class='libre'>{esc(f['responsable'] or info_responsable(f['estado']))}</td></tr>"
        )
    cuerpo.append("</tbody></table></div>")

    cuerpo.append(f"<h1>Cambios de estado (ultimos {dias} dias)</h1>")
    cuerpo.append("<div class='sub'>Esta es la tabla que contesta &laquo;desde cuando "
                  "esta caido&raquo; con fecha y hora.</div>")
    cuerpo.append("<div class='desplaza'><table><thead><tr><th>Cuando</th>"
                  "<th>Dominio</th><th>Cambio</th><th>Nivel</th><th>Detalle</th>"
                  "</tr></thead><tbody>")
    if not eventos:
        cuerpo.append("<tr><td colspan='5'>Sin cambios registrados.</td></tr>")
    for e in eventos:
        cuerpo.append(
            f"<tr><td>{esc(a_local(e['ts']))}</td><td>{esc(e['dominio'])}</td>"
            f"<td>{esc(e['anterior'] or '(nuevo)')} &rarr; "
            f"<span class='{esc(e['severidad'])}'>{esc(e['nuevo'])}</span></td>"
            f"<td>{esc(e['nivel'])}</td><td class='libre'>{esc(e['detalle'])}</td></tr>"
        )
    cuerpo.append("</tbody></table></div>")
    cuerpo.append("<div class='pie'>coipo_monitoreo &middot; generado sin servidor: "
                  "este archivo se abre con doble clic o se adjunta a un correo.</div>")
    cuerpo.append("</div>")

    return (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Monitoreo CONAF</title><style>" + _CSS + "</style></head><body>"
        + "".join(cuerpo) + "</body></html>"
    )
