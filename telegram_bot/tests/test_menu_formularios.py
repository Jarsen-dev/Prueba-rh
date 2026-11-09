"""Los formularios del bot mandan lo mismo que el panel.

Se leen los nombres de campo de frontend/Ingreso.html y frontend/empleado.html:
si el panel cambia un campo y el bot no, estas pruebas fallan.
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from bot.menu import formularios

RAIZ = Path(__file__).resolve().parents[2]
INGRESO_HTML = (RAIZ / 'frontend' / 'Ingreso.html').read_text(encoding='utf-8')
EMPLEADO_HTML = (RAIZ / 'frontend' / 'empleado.html').read_text(encoding='utf-8')
UTILY = (RAIZ / 'backend' / 'utily.py').read_text(encoding='utf-8')

CATALOGO = {
    'empresas': ['CHEONG WOON', 'KRONOS'],
    'areas': ['EPS', 'CALIDAD'],
    'puestos_por_area': {'EPS': ['MOLDERO', 'OPERARIO'], 'CALIDAD': ['INSPECTOR DE CALIDAD']},
    'turnos': ['DÍA', 'NOCHE'],
    'pagos': ['SEMANAL', 'QUINCENAL'],
    'personal': 'OPERATIVO',
}
HOY = date(2026, 11, 9)
EMPLEADO = {'registro_id': 101, 'nombre_completo': 'Ana Prueba', 'no_empleado': '5001',
            'empresa': 'CHEONG WOON', 'puesto': 'MOLDERO', 'estatus': 'ACTIVO'}


def claves_objeto_js(html, inicio):
    """Claves de un objeto literal JS que empieza en ``inicio``."""
    cuerpo = html[html.index(inicio):]
    cuerpo = cuerpo[cuerpo.index('{') + 1:]
    nivel, fin = 1, 0
    for fin, caracter in enumerate(cuerpo):
        nivel += {'{': 1, '}': -1}.get(caracter, 0)
        if nivel == 0:
            break
    return set(re.findall(r'^\s*([a-z_]+):', cuerpo[:fin], re.M))


def llenar_ingreso():
    a = formularios.asistente_ingreso({'id': 33, 'nombre_completo': 'Beto Ruiz', 'numero_temporal': '77'}, CATALOGO)
    a.responder('KRONOS')
    a.responder('EPS')
    a.responder('MOLDERO')
    a.responder('NOCHE')
    a.responder('  $2,500  semanal ')
    a.responder('SEMANAL')
    return a.construir()


def test_ingreso_manda_los_campos_del_panel():
    pendiente = llenar_ingreso()
    assert set(pendiente.cuerpo) == claves_objeto_js(INGRESO_HTML, 'function construirFilaPayload(tr)')
    assert (pendiente.metodo, pendiente.ruta) == ('POST', '/api/guardar-ingreso')
    assert pendiente.cuerpo == {
        'nombre_candidato': 'Beto Ruiz', 'empresa': 'KRONOS', 'no_empleado': 'TEMP - 77',
        'personal': 'OPERATIVO', 'puesto': 'MOLDERO', 'area': 'EPS', 'turno': 'NOCHE',
        'salario': '$2,500 semanal', 'pago': 'SEMANAL', 'registro_id': '33',
    }
    assert 'Puesto: MOLDERO' in pendiente.resumen()


def test_puestos_dependen_del_area():
    a = formularios.asistente_ingreso({'id': 1, 'nombre_completo': 'X', 'numero_temporal': '1'}, CATALOGO)
    a.responder('KRONOS')
    a.responder('CALIDAD')
    assert a.opciones() == ['INSPECTOR DE CALIDAD']
    with pytest.raises(ValueError):
        a.responder('MOLDERO')


@pytest.mark.parametrize('entrada,esperado', [
    ('77', 'TEMP - 77'), ('TEMP-77', 'TEMP - 77'), ('temp  -  9', 'TEMP - 9'), ('', ''), (None, ''),
])
def test_numero_temporal_como_el_panel(entrada, esperado):
    assert formularios.normalizar_numero_temporal(entrada) == esperado


def test_sin_numero_temporal_no_se_asigna_por_bot():
    with pytest.raises(ValueError):
        formularios.asistente_ingreso({'id': 1, 'nombre_completo': 'X', 'numero_temporal': ''}, CATALOGO)


def test_incidencia_manda_los_campos_del_panel():
    a = formularios.asistente_incidencia(EMPLEADO, hoy=lambda: HOY)
    a.responder('Suspensión')
    a.responder('Faltó tres días sin aviso')
    a.responder('10/11/2026')
    a.responder('12/11/2026')
    a.responder(formularios.validar_archivo('acta.pdf', b'%PDF'))
    p = a.construir()

    campos_panel = set(re.findall(r"datosIncidencia\.set\('([a-z_]+)'", EMPLEADO_HTML))
    assert set(p.formulario) | {'archivo'} == campos_panel
    assert set(json.loads(p.formulario['detalles'])) == claves_objeto_js(EMPLEADO_HTML, 'const detalles = {')
    assert p.ruta == '/api/empleados/101/incidencias'
    # Igual que el panel: a la última fecha de una suspensión se le suma un día.
    assert (p.formulario['fecha_inicio'], p.formulario['fecha_reingreso']) == ('2026-11-10', '2026-11-13')
    assert p.formulario['motivo'] == 'Faltó tres días sin aviso'
    assert p.argumentos_peticion()['files'] == [('archivo', ('acta.pdf', b'%PDF', 'application/pdf'))]


def test_vacaciones_sin_motivo_ni_archivo():
    a = formularios.asistente_incidencia(EMPLEADO, hoy=lambda: HOY)
    a.responder('Vacaciones')
    assert a.paso.campo == 'desde'
    a.responder('hoy')
    a.responder('13/11/2026')
    p = a.construir()
    assert p.formulario['fecha_reingreso'] == '2026-11-13'
    assert p.formulario['motivo'] == 'Vacaciones'
    assert json.loads(p.formulario['detalles'])['dias'] == 5
    assert 'files' not in p.argumentos_peticion()


def test_tipos_de_incidencia_los_acepta_el_backend():
    permitidos = set(re.search(r"if tipo not in (\{[^}]+\})", UTILY).group(1).strip('{}').replace("'", '').replace(' ', '').split(','))
    assert {tipo for tipo, _ in formularios.TIPOS_INCIDENCIA.values()} <= permitidos


def test_fecha_final_antes_de_la_inicial():
    a = formularios.asistente_incidencia(EMPLEADO, hoy=lambda: HOY)
    a.responder('Incapacidad')
    a.responder('12/11/2026')
    with pytest.raises(ValueError):
        a.responder('10/11/2026')


@pytest.mark.parametrize('nombre,contenido', [('virus.exe', b'x'), ('foto.gif', b'x'), ('a.pdf', b'x' * (10 * 1024 * 1024 + 1))])
def test_archivo_invalido(nombre, contenido):
    with pytest.raises(ValueError):
        formularios.validar_archivo(nombre, contenido)


def test_baja_solo_manda_estatus():
    p = formularios.pendiente_estatus(EMPLEADO)
    assert (p.metodo, p.ruta, p.cuerpo) == ('POST', '/api/actualizar-empleado/101', {'estatus': 'INACTIVO'})
    assert formularios.pendiente_estatus({**EMPLEADO, 'estatus': 'INACTIVO'}).cuerpo == {'estatus': 'ACTIVO'}


def test_baja_usa_el_mismo_cuerpo_que_el_listado_del_panel():
    cheong = (RAIZ / 'frontend' / 'Cheong Woon.html').read_text(encoding='utf-8')
    assert re.search(r"actualizar-empleado/\$\{[^}]+\}`, \{\s*estatus: nuevo,\s*\}", cheong)


def test_eliminar_es_delete_con_doble_confirmacion():
    p = formularios.pendiente_eliminar(EMPLEADO)
    assert (p.metodo, p.ruta, p.confirmaciones_faltantes) == ('DELETE', '/api/eliminar-empleado/101', 2)
