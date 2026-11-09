"""Herramientas preparar_*: nunca escriben, respetan guardas y arman el payload del panel."""

import json
from datetime import date, datetime

import pytest

from bot import herramientas
from bot.agente import Agente
from bot.borrador import Archivo
from bot.guardas import Vistos
from tests.fakes import GUIA_REAL, FakeBackend, FakeLLM

HOY = date(2026, 11, 9)


GUIA = GUIA_REAL
CATALOGO = {'empresas': ['CHEONG WOON', 'KRONOS'], 'areas': ['EPS', 'CALIDAD'],
            'puestos_por_area': {'EPS': ['MOLDERO', 'AYUDANTE GENERAL'], 'CALIDAD': ['INSPECTOR DE CALIDAD']},
            'turnos': ['DÍA', 'NOCHE'], 'pagos': ['SEMANAL', 'QUINCENAL'], 'personal': 'OPERATIVO'}
BRUNO = {'registro_id': 102, 'nombre_completo': 'Bruno Castillo', 'no_empleado': '5002', 'empresa': 'CHEONG WOON',
         'area': 'EPS', 'puesto': 'AYUDANTE GENERAL', 'turno': 'NOCHE', 'salario': '2300', 'pago': 'SEMANAL',
         'estatus': 'ACTIVO'}
RUTAS = {
    ('GET', '/api/catalogos/ingreso'): CATALOGO,
    ('GET', '/api/obtener-empleado/102'): BRUNO,
    ('GET', '/api/reclutadores-nombres'): [{'id': 1, 'nombre': 'Laura Reclutadora', 'team': 3, 'telefono': '5530000001'}],
}


def ctx(textos, permisos=('admin',), vistos=None, adjunto=None):
    v = vistos or Vistos()
    if vistos is None:
        v.registrar_empleado(BRUNO)
        v.candidatos[301] = {'id': 301, 'nombre_completo': 'Iván Torres', 'numero_temporal': '41'}
        v.incidencias[(102, 9)] = {'id': 9, 'type': 'vacaciones', 'fecha_inicio': '2026-10-01'}
    return herramientas.Contexto(backend=FakeBackend(permisos, RUTAS, guia=GUIA), telegram_id=1,
                                 permisos=list(permisos), hoy=HOY, textos=list(textos), vistos=v, adjunto=adjunto)


async def correr(c, herramienta, /, **argumentos):
    salida = await herramientas.ejecutar(c, herramienta, argumentos)
    assert all(m == 'GET' for m, _r, _k in c.backend.llamadas), 'una herramienta escribió'
    return salida


async def test_baja():
    c = ctx(['da de baja a bruno'])
    salida = await correr(c, 'preparar_estatus', registro_id=102, accion='baja')
    assert 'NO ESTÁ GUARDADO' in salida
    p = c.pendiente
    assert (p.metodo, p.ruta, p.cuerpo, p.origen) == ('POST', '/api/actualizar-empleado/102', {'estatus': 'INACTIVO'}, 'agente')


async def test_baja_de_quien_ya_esta_inactivo():
    c = ctx(['baja'])
    c.vistos.registrar_empleado({**BRUNO, 'estatus': 'INACTIVO'})
    assert 'ya está INACTIVO' in await correr(c, 'preparar_estatus', registro_id=102, accion='baja')
    assert c.pendiente is None


async def test_id_inventado():
    c = ctx(['da de baja al 999'])
    assert 'No preparé' in await correr(c, 'preparar_estatus', registro_id=999, accion='baja')
    assert c.pendiente is None


async def test_un_cambio_por_turno():
    c = ctx(['baja a bruno y bórralo'])
    await correr(c, 'preparar_estatus', registro_id=102, accion='baja')
    assert 'solo va uno por mensaje' in await correr(c, 'preparar_eliminacion', que='empleado', id=102)
    assert c.pendiente.metodo == 'POST'


async def test_sin_permiso_no_prepara():
    c = ctx(['baja a bruno'], permisos=['reclutamiento'])
    assert 'no tiene permiso' in await correr(c, 'preparar_estatus', registro_id=102, accion='baja')
    assert c.pendiente is None


async def test_incidencia_igual_que_el_panel():
    acta = Archivo('archivo', 'acta.pdf', b'%PDF', 'application/pdf')
    c = ctx(['suspende a bruno del 12 al 13 de noviembre por faltar, con el acta que te mandé'], adjunto=acta)
    await correr(c, 'preparar_incidencia', registro_id=102, tipo='suspension', desde='2026-11-12',
                 hasta='2026-11-13', motivo='Faltó sin aviso', usar_adjunto=True)
    p = c.pendiente
    assert p.ruta == '/api/empleados/102/incidencias'
    assert (p.formulario['fecha_inicio'], p.formulario['fecha_reingreso']) == ('2026-11-12', '2026-11-14')
    assert json.loads(p.formulario['detalles'])['dias'] == 2
    assert p.archivo is acta


async def test_incidencia_con_fecha_supuesta():
    c = ctx(['registra vacaciones a bruno el lunes'])
    salida = await correr(c, 'preparar_incidencia', registro_id=102, tipo='vacaciones', desde='2026-11-16',
                          hasta='2026-11-16')
    assert 'no aparece' in salida and c.pendiente is None


async def test_suspension_sin_motivo_o_sin_archivo():
    c = ctx(['suspende a bruno el 12 de noviembre'])
    assert 'motivo' in (await correr(c, 'preparar_incidencia', registro_id=102, tipo='suspension',
                                     desde='2026-11-12', hasta='2026-11-12')).lower()
    assert 'no ha enviado' in await correr(c, 'preparar_incidencia', registro_id=102, tipo='suspension',
                                           desde='2026-11-12', hasta='2026-11-12', motivo='x', usar_adjunto=True)


async def test_asignacion():
    c = ctx(['asigna a iván en kronos, eps, moldero, noche, 2600 semanal'])
    await correr(c, 'preparar_asignacion', candidato_id=301, empresa='kronos', area='eps', puesto='moldero',
                 turno='noche', salario='2600', pago='semanal')
    assert c.pendiente.cuerpo == {
        'nombre_candidato': 'Iván Torres', 'empresa': 'KRONOS', 'no_empleado': 'TEMP - 41', 'personal': 'OPERATIVO',
        'puesto': 'MOLDERO', 'area': 'EPS', 'turno': 'NOCHE', 'salario': '2600', 'pago': 'SEMANAL', 'registro_id': '301'}


@pytest.mark.parametrize('cambio,error', [
    ({'salario': '2600'}, 'no aparece'),                      # salario no dicho
    ({'puesto': 'INSPECTOR DE CALIDAD'}, 'no es válido'),    # puesto de otra área
    ({'turno': 'mixto'}, 'no es válido'),
])
async def test_asignacion_rechazada(cambio, error):
    c = ctx(['asigna a iván en kronos, eps, moldero, noche, semanal'])
    argumentos = {'candidato_id': 301, 'empresa': 'KRONOS', 'area': 'EPS', 'puesto': 'MOLDERO', 'turno': 'NOCHE',
                  'salario': '2500', 'pago': 'SEMANAL', **cambio}
    assert error in await correr(c, 'preparar_asignacion', **argumentos)
    assert c.pendiente is None


async def test_cambio_de_puesto_solo_manda_lo_que_cambia():
    c = ctx(['cambia a bruno a moldero con salario 2600'])
    await correr(c, 'preparar_cambio_puesto', registro_id=102, puesto='moldero', salario='2600', turno='NOCHE')
    p = c.pendiente
    assert p.cuerpo == {'puesto': 'MOLDERO', 'salario': '2600'}            # turno igual: no va
    assert 'Puesto: AYUDANTE GENERAL → MOLDERO' in p.resumen()


async def test_cambio_de_puesto_rechaza_datos_personales():
    c = ctx(['cambia la curp de bruno'])
    assert 'panel' in await correr(c, 'preparar_cambio_puesto', registro_id=102, curp='X')


async def test_cambio_de_area_sin_puesto_valido():
    c = ctx(['pasa a bruno a calidad'])
    assert 'puesto nuevo' in await correr(c, 'preparar_cambio_puesto', registro_id=102, area='CALIDAD')


async def test_editar_reclutador_conserva_el_equipo():
    c = ctx(['cambia el teléfono de laura a 55 4444 3333'], permisos=['reclutamiento'])
    await correr(c, 'preparar_reclutador', accion='editar', nombre='laura', telefono='5544443333')
    p = c.pendiente
    assert (p.metodo, p.ruta, p.cuerpo) == ('PUT', '/api/reclutadores-nombres/1', {'telefono': '5544443333', 'team': 3})
    assert '5530000001' not in p.resumen()


async def test_alta_reclutador_exige_datos_dichos():
    c = ctx(['da de alta a pedro pérez en el equipo 2'])
    assert 'teléfono' in await correr(c, 'preparar_reclutador', accion='alta', nombre='Pedro Pérez', team=2)
    c = ctx(['da de alta a pedro pérez, tel 5511112222, equipo 2'])
    await correr(c, 'preparar_reclutador', accion='alta', nombre='Pedro Pérez', telefono='5511112222', team=2)
    assert c.pendiente.cuerpo == {'nombre': 'Pedro Pérez', 'telefono': '5511112222', 'team': 2}


@pytest.mark.parametrize('argumentos,ruta', [
    ({'que': 'empleado', 'id': 102}, '/api/eliminar-empleado/102'),
    ({'que': 'candidato', 'id': 301}, '/api/eliminar-pendiente/301'),
    ({'que': 'incidencia', 'id': 9, 'registro_id': 102}, '/api/empleados/102/incidencias/9'),
])
async def test_eliminaciones_con_doble_clic(argumentos, ruta):
    c = ctx(['bórralo'])
    await correr(c, 'preparar_eliminacion', **argumentos)
    assert (c.pendiente.metodo, c.pendiente.ruta, c.pendiente.confirmaciones_faltantes) == ('DELETE', ruta, 2)


async def test_incidencia_no_vista():
    c = ctx(['borra la incidencia 10'])
    assert 'ver_incidencias' in await correr(c, 'preparar_eliminacion', que='incidencia', id=10, registro_id=102)


async def test_el_modelo_no_ve_el_resumen():
    c = ctx(['cambia el teléfono de laura a 5544443333'])
    salida = await correr(c, 'preparar_reclutador', accion='editar', nombre='laura', telefono='5544443333')
    assert '5544443333' not in salida and 'Laura' not in salida


async def test_agente_devuelve_el_pendiente_y_solo_usa_textos_de_la_persona():
    backend = FakeBackend(rutas={('GET', '/api/empleados/buscar'): {'empleados': [BRUNO]}}, guia=GUIA)
    llm = FakeLLM([[('buscar_empleado', {'texto': 'bruno'})],
                   [('preparar_incidencia', {'registro_id': 102, 'tipo': 'vacaciones', 'desde': '2026-11-16',
                                             'hasta': '2026-11-20'})],
                   'Listo para confirmar.'])
    historial = [{'role': 'assistant', 'content': 'Del 16 al 20 de noviembre'}]   # lo dijo el asistente, no cuenta
    r = await Agente(llm=llm, backend=backend).responder(1, 'vacaciones a bruno', historial, datetime(2026, 11, 9))
    assert r.pendiente is None and 'No preparé' in str(llm.recibidos[-1])

    llm = FakeLLM([[('buscar_empleado', {'texto': 'bruno'})],
                   [('preparar_incidencia', {'registro_id': 102, 'tipo': 'vacaciones', 'desde': '2026-11-16',
                                             'hasta': '2026-11-20'})],
                   'Listo para confirmar.'])
    vistos = Vistos()
    r = await Agente(llm=llm, backend=backend).responder(
        1, 'vacaciones a bruno del 16 al 20 de noviembre', [], datetime(2026, 11, 9), vistos=vistos)
    assert r.pendiente.formulario['tipo'] == 'vacaciones' and 102 in vistos.empleados
    assert all(m == 'GET' for m, _r, _k in backend.llamadas)
