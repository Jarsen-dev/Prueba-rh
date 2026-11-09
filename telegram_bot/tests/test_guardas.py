from datetime import date

import pytest

from bot.guardas import GuardaFallo, Vistos, de_catalogo, fecha_mencionada, numero_mencionado, texto_mencionado

HOY = date(2026, 11, 9)


@pytest.mark.parametrize('texto,fecha', [
    ('vacaciones del 16 al 20 de noviembre', date(2026, 11, 16)),
    ('vacaciones del 16 al 20 de noviembre', date(2026, 11, 20)),
    ('del 16/11 al 20/11', date(2026, 11, 20)),
    ('del 16/11/2026', date(2026, 11, 16)),
    ('desde 2026-12-01', date(2026, 12, 1)),
    ('a partir de hoy', HOY),
    ('desde mañana', date(2026, 11, 10)),
    ('el 3 y 4 de diciembre', date(2026, 12, 4)),
    ('del 12 al 13', date(2026, 11, 13)),          # sin mes: el mes en curso
    ('el 5 de enero de 2027', date(2027, 1, 5)),
])
def test_fechas_dichas(texto, fecha):
    assert fecha_mencionada(fecha, [texto], HOY) == fecha


@pytest.mark.parametrize('texto,fecha', [
    ('vacaciones el lunes', date(2026, 11, 16)),
    ('la próxima semana', date(2026, 11, 16)),
    ('del 16 al 20 de noviembre', date(2026, 12, 16)),   # mes distinto
    ('del 16 al 20 de noviembre', date(2026, 11, 17)),   # día no dicho
    ('del 16 al 20', date(2026, 12, 16)),                # sin mes: no puede ser otro mes
    ('el 5 de enero de 2027', date(2026, 1, 5)),         # año distinto
    ('vacaciones a Ana', date(2026, 11, 9)),
])
def test_fechas_no_dichas(texto, fecha):
    with pytest.raises(GuardaFallo):
        fecha_mencionada(fecha, [texto], HOY)


def test_numero_dicho():
    assert numero_mencionado('2600', ['salario de 2,600 semanal']) == '2600'
    assert numero_mencionado('$2,600', ['gana 2600']) == '2600'
    assert numero_mencionado('5544443333', ['su cel es 55 4444 3333']) == '5544443333'
    assert numero_mencionado('2600', ['el 5002 2600'])
    for texto in ('salario semanal', 'gana 2500', 'gana 26000'):
        with pytest.raises(GuardaFallo):
            numero_mencionado('2600', [texto])


def test_texto_dicho():
    assert texto_mencionado('Pedro Pérez', ['da de alta a pedro perez'], 'El nombre')
    with pytest.raises(GuardaFallo):
        texto_mencionado('Pedro Pérez', ['da de alta a un reclutador'], 'El nombre')


def test_catalogo():
    assert de_catalogo('noche', ['DÍA', 'NOCHE'], 'Turno') == 'NOCHE'
    assert de_catalogo('dia', ['DÍA', 'NOCHE'], 'Turno') == 'DÍA'
    with pytest.raises(GuardaFallo, match='Opciones'):
        de_catalogo('mixto', ['DÍA', 'NOCHE'], 'Turno')


def test_vistos():
    v = Vistos()
    v.registrar_empleado({'registro_id': 7, 'nombre_completo': 'Ana', 'estatus': None})
    v.registrar_empleado({'registro_id': '7', 'estatus': 'ACTIVO', 'nombre_completo': ''})
    assert v.empleado(7) == {'registro_id': 7, 'nombre_completo': 'Ana', 'estatus': 'ACTIVO'}
    for malo in (8, None, 'x'):
        with pytest.raises(GuardaFallo):
            v.empleado(malo)
    with pytest.raises(GuardaFallo):
        v.candidato(1)


def test_numero_mal_copiado_da_pista_con_los_numeros_de_la_persona():
    import pytest
    from bot.guardas import GuardaFallo, numero_mencionado
    with pytest.raises(GuardaFallo, match='5544443333'):
        numero_mencionado('55444433333', ['cambia el teléfono de laura a 5544443333'])


def test_salario_se_normaliza_como_en_el_panel():
    from bot.escrituras import _salario
    from types import SimpleNamespace
    ctx = SimpleNamespace(textos=['salario de $2,600 pesos'])
    assert _salario('$2,600', ctx) == '2600'
    assert _salario('2600 pesos', ctx) == '2600'
