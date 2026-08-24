"""Se alerta cuando algo CAMBIA. Un monitoreo que escribe cada 15 minutos se ignora."""

from __future__ import annotations

from coipo_monitoreo.modelos import Estado


def _asuntos(buzon):
    return [n.asunto for n in buzon.vaciar()]


def test_la_primera_corrida_avisa_lo_que_ya_estaba_roto(entorno):
    _, transiciones = entorno.ronda()
    caidos = [t for t in transiciones if not t.recuperacion]

    assert len(caidos) == 6              # los 6 escenarios de falla del demo
    assert all(t.anterior is None for t in caidos)


def test_la_segunda_corrida_no_avisa_nada(entorno):
    entorno.ronda()
    entorno.buzon.vaciar()

    _, transiciones = entorno.ronda()

    assert transiciones == []
    assert _asuntos(entorno.buzon) == []


def test_un_solo_dato_no_confirma_una_caida(entorno):
    """Amortiguacion de rebote: un timeout aislado no puede despertar a nadie."""
    entorno.ronda()
    entorno.buzon.vaciar()

    entorno.srv.cambiar("sano.demo.local", "corta")
    _, transiciones = entorno.ronda()

    assert transiciones == []                 # todavia sin confirmar
    assert _asuntos(entorno.buzon) == []

    _, transiciones = entorno.ronda()         # segunda observacion consecutiva
    assert len(transiciones) == 1
    assert transiciones[0].dominio == "sano.demo.local"
    assert transiciones[0].nuevo is Estado.UPSTREAM_SIN_RESPUESTA
    assert len(_asuntos(entorno.buzon)) == 1


def test_la_fecha_es_la_primera_observacion_no_la_confirmacion(entorno):
    """'Desde cuando' es lo que se le muestra a Informatica: tiene que ser la hora en
    que efectivamente dejo de andar, no la hora en que el monitoreo se convencio."""
    entorno.ronda()
    entorno.srv.cambiar("sano.demo.local", "corta")

    res, _ = entorno.ronda()
    primera_observacion = res["sano.demo.local"].ts

    _, transiciones = entorno.ronda()

    assert transiciones[0].desde == primera_observacion


def test_la_recuperacion_tambien_se_avisa(entorno):
    entorno.ronda()
    entorno.srv.cambiar("sano.demo.local", "corta")
    entorno.ronda()
    entorno.ronda()
    entorno.buzon.vaciar()

    entorno.srv.cambiar("sano.demo.local", "ok")
    entorno.ronda()
    _, transiciones = entorno.ronda()

    assert len(transiciones) == 1
    assert transiciones[0].recuperacion
    assert _asuntos(entorno.buzon) == ["[OK] sano.demo.local se recupero"]


def test_una_falla_masiva_manda_una_sola_alerta(entorno):
    """Si todo falla en el mismo nivel a la vez, eso no son N incidentes: es la red."""
    entorno.ronda()
    entorno.buzon.vaciar()

    entorno.cfg.resolvers["demo"].estatico = {}   # se cae el DNS entero
    entorno.ronda()
    _, transiciones = entorno.ronda()

    assert len(transiciones) == 7                 # 7 dominios cambiaron de estado
    asuntos = _asuntos(entorno.buzon)
    assert len(asuntos) == 1                      # pero se manda una sola alerta
    assert "falla masiva" in asuntos[0]
    assert all(t.supresion == "tormenta" for t in transiciones)


def test_el_aviso_de_certificado_se_agrupa_por_certificado(entorno):
    """Un comodin *.conaf.cl sirve los 14 dominios: avisar por dominio serian 14
    correos identicos."""
    entorno.ronda()

    avisos = [a for a in entorno.buzon.recibidas if "certificado" in a.asunto]
    assert len(avisos) == 1
    assert "dominio(s)" in avisos[0].asunto


def test_la_alerta_dice_quien_lo_arregla_y_desde_cuando(entorno):
    entorno.ronda()
    cuerpo = next(
        n.cuerpo for n in entorno.buzon.recibidas if "corta.demo.local" in n.asunto
    )

    assert "Responsable:" in cuerpo
    assert "Desde:" in cuerpo
    assert "Que hay que pedir:" in cuerpo
    assert "upstream" in cuerpo
