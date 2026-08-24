"""La configuracion es un archivo, y el historial tiene que contestar 'desde cuando'."""

from __future__ import annotations

import pytest

from coipo_monitoreo.config import ErrorConfig, cargar


def _escribir(tmp_path, texto):
    ruta = tmp_path / "prueba.yml"
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def test_un_dominio_nuevo_no_requiere_tocar_codigo(entorno):
    """Agregar un dominio es editar el YAML y volver a correr."""
    texto = entorno.ruta.read_text(encoding="utf-8")
    texto += "- nombre: agregado.demo.local\n  notas: agregado a mano\n"
    entorno.ruta.write_text(texto, encoding="utf-8")

    cfg = cargar(entorno.ruta)
    assert "agregado.demo.local" in [d.nombre for d in cfg.efectivos()]


def test_un_resolver_inexistente_se_avisa_antes_de_correr(tmp_path):
    ruta = _escribir(tmp_path, """
resolvers:
  interno: {servidores: [172.16.1.120]}
predeterminados: {ip_proxy: 172.31.2.100}
dominios:
  - nombre: x.conaf.cl
    resolver: nombre_que_no_existe
""")
    with pytest.raises(ErrorConfig) as e:
        cargar(ruta)
    assert "nombre_que_no_existe" in str(e.value)


def test_via_proxy_sin_ip_se_avisa_antes_de_correr(tmp_path):
    ruta = _escribir(tmp_path, """
resolvers:
  interno: {servidores: [172.16.1.120]}
dominios:
  - nombre: x.conaf.cl
""")
    with pytest.raises(ErrorConfig) as e:
        cargar(ruta)
    assert "ip_proxy" in str(e.value)


def test_dominios_repetidos_se_avisan(tmp_path):
    ruta = _escribir(tmp_path, """
resolvers:
  interno: {servidores: [172.16.1.120]}
predeterminados: {ip_proxy: 172.31.2.100}
dominios:
  - nombre: x.conaf.cl
  - nombre: x.conaf.cl
""")
    with pytest.raises(ErrorConfig) as e:
        cargar(ruta)
    assert "repetidos" in str(e.value)


def test_una_clave_mal_escrita_no_pasa_silenciosamente(tmp_path):
    """'minimo_bytes' en vez de 'min_bytes' tiene que fallar, no ignorarse: si no, el
    chequeo corre con el valor por defecto y nadie se entera."""
    ruta = _escribir(tmp_path, """
resolvers:
  interno: {servidores: [172.16.1.120]}
predeterminados: {ip_proxy: 172.31.2.100}
dominios:
  - nombre: x.conaf.cl
    minimo_bytes: 500
""")
    with pytest.raises(ErrorConfig) as e:
        cargar(ruta)
    assert "minimo_bytes" in str(e.value)


def test_las_rutas_relativas_no_dependen_del_directorio_actual(entorno):
    """cron y systemd arrancan desde cualquier lado."""
    assert entorno.cfg.base_datos.startswith(str(entorno.ruta.parent))


def test_el_historial_registra_el_cambio_con_fecha(entorno):
    entorno.ronda()
    entorno.srv.cambiar("sano.demo.local", "corta")
    entorno.ronda()
    entorno.ronda()

    eventos = entorno.alm.eventos(dominio="sano.demo.local", dias=1)
    assert len(eventos) == 1
    e = eventos[0]
    assert e["anterior"] == "OK"
    assert e["nuevo"] == "UPSTREAM_SIN_RESPUESTA"
    assert e["nivel"] == "upstream"
    assert e["ts"]


def test_el_estado_actual_dice_desde_cuando(entorno):
    entorno.ronda()
    fila = entorno.alm.estado("vacio.demo.local")

    assert fila["estado"] == "CONTENIDO_VACIO"
    assert fila["severidad"] == "CAIDO"
    assert fila["desde"]


def test_se_guarda_la_evidencia_cruda_de_cada_corrida(entorno):
    entorno.ronda()
    filas = entorno.alm.cx.execute(
        "SELECT * FROM chequeos WHERE dominio='vacio.demo.local'"
    ).fetchall()

    assert len(filas) == 1
    assert filas[0]["http_estado"] == 200
    assert filas[0]["bytes"] == 0


def test_un_dominio_sacado_de_la_configuracion_se_olvida(entorno):
    entorno.ronda()
    assert entorno.alm.estado("error.demo.local") is not None

    entorno.cfg.dominios = [
        d for d in entorno.cfg.dominios if d.nombre != "error.demo.local"
    ]
    entorno.ronda()

    assert entorno.alm.estado("error.demo.local") is None
