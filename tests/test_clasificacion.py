"""Cada falla tiene que caer en su nivel, porque cada nivel lo resuelve otro equipo."""

from __future__ import annotations

from coipo_monitoreo.modelos import Estado, Nivel, Severidad, responsable_de


def test_cada_escenario_cae_en_su_estado(entorno):
    res, _ = entorno.ronda()

    assert res["sano.demo.local"].estado is Estado.OK
    assert res["nodns.demo.local"].estado is Estado.DNS_NXDOMAIN
    assert res["caido.demo.local"].estado is Estado.PROXY_RECHAZADO
    assert res["corta.demo.local"].estado is Estado.UPSTREAM_SIN_RESPUESTA
    assert res["vacio.demo.local"].estado is Estado.CONTENIDO_VACIO
    assert res["sintexto.demo.local"].estado is Estado.CONTENIDO_SIN_TEXTO
    assert res["error.demo.local"].estado is Estado.HTTP_5XX


def test_los_niveles_separan_a_quien_le_toca(entorno):
    res, _ = entorno.ronda()

    assert res["nodns.demo.local"].nivel is Nivel.DNS
    assert res["caido.demo.local"].nivel is Nivel.TRANSPORTE
    assert res["corta.demo.local"].nivel is Nivel.UPSTREAM
    assert res["vacio.demo.local"].nivel is Nivel.CONTENIDO


def test_doscientos_con_cero_bytes_es_una_caida(entorno):
    """El caso que motiva todo esto: un catch-all devuelve HTTP 200 con 0 bytes.

    Un chequeo que solo mire el codigo HTTP lo da por bueno. interno-sidco.conaf.cl
    estuvo asi sin que nadie lo supiera.
    """
    res, _ = entorno.ronda()
    r = res["vacio.demo.local"]

    assert r.http_estado == 200          # el codigo dice que todo bien
    assert r.bytes == 0                  # pero no hay pagina
    assert r.severidad is Severidad.CAIDO
    assert not r.ok


def test_una_redireccion_aceptada_no_es_contenido_vacio(entorno):
    """Un 303 hacia el login trae el cuerpo vacio y eso es correcto.

    Sin esta excepcion, academia.conaf.cl se reportaria caido todos los dias.
    """
    res, _ = entorno.ronda()
    r = res["login.demo.local"]

    assert r.http_estado == 303
    assert r.bytes == 0
    assert r.estado is Estado.OK
    assert "iam.demo.local/login" in r.detalle


def test_se_reportan_los_problemas_de_las_otras_capas(entorno):
    """El patron de archivo.conaf.cl: no resuelve Y ademas no tiene vhost.

    Si solo se reportara la primera falla, arreglar el DNS dejaria el sitio en blanco
    y nadie sabria por que.
    """
    res, _ = entorno.ronda()
    r = res["nodns.demo.local"]

    assert r.estado is Estado.DNS_NXDOMAIN            # lo primero que hay que arreglar
    assert any("contenido" in h for h in r.hallazgos)  # pero no es lo unico


def test_el_responsable_lo_define_el_nivel_no_el_dominio():
    """Si un dominio de la aplicacion X deja de resolver, el arreglo es de Informatica."""
    equipo = "Entrega de Plantas"

    dns = responsable_de(Estado.DNS_NXDOMAIN, equipo)
    assert "Informatica" in dns
    assert equipo not in dns

    contenido = responsable_de(Estado.CONTENIDO_SIN_TEXTO, equipo)
    assert equipo in contenido


def test_el_certificado_no_marca_el_dominio_como_caido(entorno):
    """El certificado del demo vence en 9 dias y aun asi el dominio sano esta OK."""
    res, _ = entorno.ronda()
    r = res["sano.demo.local"]

    assert r.cert_dias is not None and r.cert_dias < 30
    assert r.estado is Estado.OK
