"""Historial en SQLite.

Por que SQLite y no el PostgreSQL compartido de 172.31.2.40: el monitoreo no puede
depender de infraestructura compartida que podria ser justamente lo que esta caido.
Un archivo local no tiene red de por medio, se copia con 'cp' y se consulta con
'sqlite3' sin instalar nada.

Las tres tablas responden tres preguntas distintas:
  chequeos       -> que se midio en cada corrida (evidencia cruda, con retencion)
  estado_actual  -> como esta ahora cada dominio y DESDE CUANDO
  eventos        -> cuando cambio de estado (esto es lo que se le muestra a Informatica)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .modelos import Estado, Resultado, info, sello

ESQUEMA = """
CREATE TABLE IF NOT EXISTS chequeos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    dominio      TEXT NOT NULL,
    estado       TEXT NOT NULL,
    nivel        TEXT NOT NULL,
    severidad    TEXT NOT NULL,
    ip_resuelta  TEXT,
    ip_consultada TEXT,
    http_estado  INTEGER,
    bytes        INTEGER,
    ms_dns       REAL,
    ms_tls       REAL,
    ms_http      REAL,
    cert_dias    INTEGER,
    detalle      TEXT,
    hallazgos    TEXT,
    dns          TEXT
);
CREATE INDEX IF NOT EXISTS ix_chequeos ON chequeos(dominio, ts);

CREATE TABLE IF NOT EXISTS estado_actual (
    dominio       TEXT PRIMARY KEY,
    estado        TEXT NOT NULL,
    severidad     TEXT NOT NULL,
    desde         TEXT NOT NULL,
    ultima_revision TEXT NOT NULL,
    detalle       TEXT,
    responsable   TEXT,
    candidato     TEXT,
    candidato_desde TEXT,
    candidato_reps  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eventos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    dominio   TEXT NOT NULL,
    anterior  TEXT,
    nuevo     TEXT NOT NULL,
    nivel     TEXT,
    severidad TEXT,
    responsable TEXT,
    detalle   TEXT,
    notificado INTEGER NOT NULL DEFAULT 0,
    supresion TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos ON eventos(dominio, ts);

-- Se indexa por certificado, no por dominio: el proxy usa un comodin *.conaf.cl para
-- los catorce dominios, y avisar una vez por dominio serian catorce correos identicos.
CREATE TABLE IF NOT EXISTS avisos_cert (
    clave TEXT PRIMARY KEY,
    dias  INTEGER,
    ts    TEXT
);

CREATE TABLE IF NOT EXISTS corridas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    duracion_s REAL,
    total     INTEGER,
    ok        INTEGER,
    avisos    INTEGER,
    caidos    INTEGER
);
"""


class Almacen:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(self.ruta, timeout=30)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.executescript(ESQUEMA)
        self.cx.commit()

    def cerrar(self) -> None:
        self.cx.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()

    # ------------------------------------------------------------------ escritura

    def guardar_chequeo(self, r: Resultado) -> None:
        self.cx.execute(
            "INSERT INTO chequeos (ts,dominio,estado,nivel,severidad,ip_resuelta,"
            "ip_consultada,http_estado,bytes,ms_dns,ms_tls,ms_http,cert_dias,detalle,"
            "hallazgos,dns) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r.ts, r.dominio, r.estado.value, r.nivel.value, r.severidad.value,
                r.ip_resuelta, r.ip_consultada, r.http_estado, r.bytes,
                r.ms_dns, r.ms_tls, r.ms_http, r.cert_dias, r.detalle,
                json.dumps(r.hallazgos, ensure_ascii=False),
                json.dumps(
                    {k: {"ips": v.ips, "error": v.error} for k, v in r.dns.items()},
                    ensure_ascii=False,
                ),
            ),
        )

    def guardar_corrida(self, duracion: float, total: int, ok: int, avisos: int,
                        caidos: int) -> None:
        self.cx.execute(
            "INSERT INTO corridas (ts,duracion_s,total,ok,avisos,caidos) "
            "VALUES (?,?,?,?,?,?)",
            (sello(), duracion, total, ok, avisos, caidos),
        )

    def registrar_evento(self, dominio: str, anterior: str | None, nuevo: Estado,
                         detalle: str, responsable: str, ts: str,
                         notificado: bool, supresion: str | None = None) -> int:
        i = info(nuevo)
        cur = self.cx.execute(
            "INSERT INTO eventos (ts,dominio,anterior,nuevo,nivel,severidad,"
            "responsable,detalle,notificado,supresion) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, dominio, anterior, nuevo.value, i.nivel.value, i.severidad.value,
             responsable or i.responsable, detalle, int(notificado), supresion),
        )
        return int(cur.lastrowid or 0)

    def marcar_notificado(self, id_evento: int) -> None:
        self.cx.execute("UPDATE eventos SET notificado=1 WHERE id=?", (id_evento,))

    # --------------------------------------------------------------------- estado

    def estado(self, dominio: str) -> sqlite3.Row | None:
        return self.cx.execute(
            "SELECT * FROM estado_actual WHERE dominio=?", (dominio,)
        ).fetchone()

    def escribir_estado(self, dominio: str, estado: Estado, desde: str,
                        ultima: str, detalle: str, responsable: str,
                        candidato: str | None, candidato_desde: str | None,
                        reps: int) -> None:
        self.cx.execute(
            "INSERT INTO estado_actual (dominio,estado,severidad,desde,ultima_revision,"
            "detalle,responsable,candidato,candidato_desde,candidato_reps) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(dominio) DO UPDATE SET estado=excluded.estado,"
            "severidad=excluded.severidad,desde=excluded.desde,"
            "ultima_revision=excluded.ultima_revision,detalle=excluded.detalle,"
            "responsable=excluded.responsable,candidato=excluded.candidato,"
            "candidato_desde=excluded.candidato_desde,"
            "candidato_reps=excluded.candidato_reps",
            (dominio, estado.value, info(estado).severidad.value, desde, ultima,
             detalle, responsable, candidato, candidato_desde, reps),
        )

    def olvidar(self, dominios: list[str]) -> None:
        """Saca de estado_actual los dominios que ya no estan en la configuracion."""
        if not dominios:
            return
        marcas = ",".join("?" * len(dominios))
        self.cx.execute(
            f"DELETE FROM estado_actual WHERE dominio NOT IN ({marcas})", dominios
        )

    # -------------------------------------------------------------------- lectura

    def estados(self) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT * FROM estado_actual ORDER BY "
            "CASE severidad WHEN 'CAIDO' THEN 0 WHEN 'AVISO' THEN 1 ELSE 2 END, dominio"
        ).fetchall()

    def eventos(self, dominio: str | None = None, dias: int = 30,
                limite: int = 200) -> list[sqlite3.Row]:
        sql = ("SELECT * FROM eventos WHERE ts >= datetime('now', ?) ")
        args: list = [f"-{int(dias)} days"]
        if dominio:
            sql += "AND dominio=? "
            args.append(dominio)
        sql += "ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limite)
        return self.cx.execute(sql, args).fetchall()

    def ultima_corrida(self) -> sqlite3.Row | None:
        return self.cx.execute(
            "SELECT * FROM corridas ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def resumen_disponibilidad(self, dias: int = 7) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT dominio, COUNT(*) AS total, "
            "SUM(CASE WHEN severidad='OK' THEN 1 ELSE 0 END) AS ok "
            "FROM chequeos WHERE ts >= datetime('now', ?) "
            "GROUP BY dominio ORDER BY dominio",
            (f"-{int(dias)} days",),
        ).fetchall()

    # ---------------------------------------------------------------- certificados

    def aviso_cert_pendiente(self, clave: str, repetir_cada_dias: int) -> bool:
        fila = self.cx.execute(
            "SELECT ts FROM avisos_cert WHERE clave=?", (clave,)
        ).fetchone()
        if fila is None:
            return True
        r = self.cx.execute(
            "SELECT ? < datetime('now', ?) AS vencido",
            (fila["ts"], f"-{int(repetir_cada_dias)} days"),
        ).fetchone()
        return bool(r["vencido"])

    def anotar_aviso_cert(self, clave: str, dias: int) -> None:
        self.cx.execute(
            "INSERT INTO avisos_cert (clave,dias,ts) VALUES (?,?,?) "
            "ON CONFLICT(clave) DO UPDATE SET dias=excluded.dias, ts=excluded.ts",
            (clave, dias, sello()),
        )

    # ------------------------------------------------------------------ retencion

    def purgar(self, dias: int) -> int:
        """Borra chequeos viejos. Los eventos NO se borran nunca: son la memoria."""
        cur = self.cx.execute(
            "DELETE FROM chequeos WHERE ts < datetime('now', ?)", (f"-{int(dias)} days",)
        )
        return cur.rowcount or 0

    def commit(self) -> None:
        self.cx.commit()
