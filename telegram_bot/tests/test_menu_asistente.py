from datetime import date

import pytest

from bot.borrador import CambioPendiente
from bot.menu.asistente import Asistente, Paso, interpretar_fecha

HOY = date(2026, 11, 9)


def armar():
    return Asistente(
        titulo='Prueba',
        pasos=[
            Paso('area', 'Área', opciones=lambda r: ['A', 'B']),
            Paso('puesto', 'Puesto', opciones=lambda r: {'A': ['a1', 'a2'], 'B': ['b1']}[r['area']]),
            Paso('nota', 'Nota', tipo='texto', aplica=lambda r: r.get('area') == 'B'),
            Paso('dia', 'Día', tipo='fecha'),
            Paso('extra', 'Extra', tipo='archivo', omitible=True),
        ],
        al_terminar=lambda r: CambioPendiente('POST', '/api/x', 'x', cuerpo=dict(r)),
        hoy=lambda: HOY,
    )


def test_flujo_completo_y_paso_condicional():
    a = armar()
    a.elegir(0)              # A
    assert a.opciones() == ['a1', 'a2']
    a.elegir(1)
    assert a.paso.campo == 'dia'          # 'nota' no aplica para A
    assert a.numero_de_paso() == (3, 4)
    a.responder('mañana')
    a.omitir()
    assert a.terminado
    assert a.construir().cuerpo == {'area': 'A', 'puesto': 'a2', 'dia': date(2026, 11, 10), 'extra': None}


def test_atras_borra_lo_que_dependia():
    a = armar()
    a.elegir(0)
    a.elegir(0)
    a.atras()
    assert a.paso.campo == 'puesto' and 'puesto' not in a.respuestas
    a.atras()
    assert a.paso.campo == 'area' and a.respuestas == {}
    a.elegir(1)
    a.elegir(0)
    assert a.paso.campo == 'nota'


def test_opcion_fuera_de_rango_o_texto_en_opciones():
    a = armar()
    with pytest.raises(ValueError):
        a.elegir(5)
    with pytest.raises(ValueError):
        a.responder('C')
    assert a.paso.campo == 'area'


def test_obligatorio_no_se_omite():
    a = armar()
    with pytest.raises(ValueError):
        a.omitir()


def test_no_construye_incompleto():
    with pytest.raises(ValueError):
        armar().construir()


@pytest.mark.parametrize('texto,esperada', [
    ('hoy', HOY), ('Mañana', date(2026, 11, 10)), ('manana', date(2026, 11, 10)),
    ('15/11/2026', date(2026, 11, 15)), ('1/2/2027', date(2027, 2, 1)),
    ('2026-12-31', date(2026, 12, 31)), ('31.12.2026', date(2026, 12, 31)),
])
def test_fechas_validas(texto, esperada):
    assert interpretar_fecha(texto, HOY) == esperada


@pytest.mark.parametrize('texto', ['ayer', 'el lunes', '31/02/2026', '15/11', '2026/11/15', '', None])
def test_fechas_que_no_se_adivinan(texto):
    with pytest.raises(ValueError):
        interpretar_fecha(texto, HOY)
