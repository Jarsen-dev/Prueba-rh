"""Flujos completos del menú con Telegram y backend simulados."""

import json
from datetime import date

from bot import principal
from bot.backend import ErrorBackend, RespuestaBackend
from bot.estado import Estados
from bot.menu import pantallas
from tests.fakes import Chat, FakeBackend, archivo, contexto, pulsar, texto

EMPLEADO = {'registro_id': 101, 'nombre_completo': 'Ana Prueba', 'no_empleado': '5001',
            'empresa': 'CHEONG WOON', 'puesto': 'MOLDERO', 'estatus': 'ACTIVO'}
CATALOGO = {'empresas': ['CHEONG WOON', 'KRONOS'], 'areas': ['EPS'], 'puestos_por_area': {'EPS': ['MOLDERO']},
            'turnos': ['DÍA', 'NOCHE'], 'pagos': ['SEMANAL'], 'personal': 'OPERATIVO'}


def preparar(permisos=('admin',), rutas=None, archivos=None):
    backend = FakeBackend(permisos, rutas)
    ctx = contexto(backend, Estados(), archivos=archivos)
    return backend, ctx, Chat()


def escrituras(backend):
    return [(m, r, kw) for m, r, kw in backend.llamadas if m != 'GET']


async def test_sistema_muestra_menu_segun_permisos():
    _b, ctx, chat = preparar(['reclutamiento'])
    await pantallas.cmd_sistema(texto(chat, '/sistema'), ctx)
    assert list(chat.botones().values()) == ['m:empleados', 'm:reclutamiento', 'm:descargas']


async def test_nodo_de_admin_rechazado_para_reclutamiento():
    backend, ctx, chat = preparar(['reclutamiento'])
    await pantallas.al_boton_nodo(pulsar(chat, 'm:ingresos'), ctx)
    assert 'no tiene acceso' in chat.ultimo()[0]
    assert backend.llamadas == []


async def test_nodo_inexistente():
    _b, ctx, chat = preparar()
    await pantallas.al_boton_nodo(pulsar(chat, 'm:nada'), ctx)
    assert chat.ultimo()[0] == pantallas.CADUCADO


async def test_buscar_abrir_ficha_y_dar_de_baja_con_confirmar():
    backend, ctx, chat = preparar(rutas={
        ('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO, {**EMPLEADO, 'registro_id': 102, 'nombre_completo': 'Ana Otra'}]},
        ('POST', '/api/actualizar-empleado/101'): {'ok': True},
    })
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, 'ana'), ctx)
    assert backend.llamadas[-1][2]['params'] == {'q': 'ana'}
    assert set(chat.botones().values()) >= {'e:0', 'e:1'}

    await pantallas.al_boton_empleado(pulsar(chat, 'e:0'), ctx)
    assert '5001' in chat.ultimo()[0]
    await pantallas.al_boton_accion_empleado(pulsar(chat, 'ea:est'), ctx)
    assert escrituras(backend) == []                       # nada sin Confirmar
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    await principal.al_boton(pulsar(chat, ok), ctx)
    assert escrituras(backend) == [('POST', '/api/actualizar-empleado/101',
                                    {'headers': {'X-Origen-Telegram': 'menu'}, 'json': {'estatus': 'INACTIVO'}})]
    assert chat.ultimo()[0].endswith('✅ Hecho.')


async def test_indice_manipulado_no_abre_nada():
    backend, ctx, chat = preparar(rutas={('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO, EMPLEADO]}})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, 'ana'), ctx)
    await pantallas.al_boton_empleado(pulsar(chat, 'e:99'), ctx)
    assert chat.ultimo()[0] == pantallas.CADUCADO


async def test_lista_de_otra_persona_no_sirve():
    _b, ctx, chat = preparar(rutas={('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO, EMPLEADO]}})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, 'ana'), ctx)
    await pantallas.al_boton_empleado(pulsar(chat, 'e:0', telegram_id=8), ctx)
    assert chat.ultimo()[0] == pantallas.CADUCADO


async def test_eliminar_pide_dos_clics():
    backend, ctx, chat = preparar(rutas={
        ('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO]},
        ('DELETE', '/api/eliminar-empleado/101'): {'ok': True},
    })
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, '5001'), ctx)       # 1 resultado: ficha directa
    await pantallas.al_boton_accion_empleado(pulsar(chat, 'ea:del'), ctx)
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    await principal.al_boton(pulsar(chat, ok), ctx)
    assert escrituras(backend) == []
    await principal.al_boton(pulsar(chat, ok), ctx)
    assert [(m, r) for m, r, _ in escrituras(backend)] == [('DELETE', '/api/eliminar-empleado/101')]


async def test_asignar_puesto_completo():
    backend, ctx, chat = preparar(rutas={
        ('GET', '/api/obtener-pendientes'): [{'id': 33, 'nombre_completo': 'Beto Ruiz', 'numero_temporal': '77'}],
        ('GET', '/api/catalogos/ingreso'): CATALOGO,
        ('POST', '/api/guardar-ingreso'): RespuestaBackend(200, 'text/plain', datos='OK'),
    })
    await pantallas.al_boton_nodo(pulsar(chat, 'm:ingresos'), ctx)
    await pantallas.al_boton_candidato(pulsar(chat, 'c:0'), ctx)
    for datos in ('a:1', 'a:0', 'a:0', 'a:1'):            # KRONOS, EPS, MOLDERO, NOCHE
        await pantallas.al_boton_asistente(pulsar(chat, datos), ctx)
    await principal.al_texto(texto(chat, '2500'), ctx)
    await pantallas.al_boton_asistente(pulsar(chat, 'a:0'), ctx)  # SEMANAL
    assert 'Puesto: MOLDERO' in chat.ultimo()[0]
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    await principal.al_boton(pulsar(chat, ok), ctx)
    (_m, _r, kw), = escrituras(backend)
    assert kw['json']['empresa'] == 'KRONOS' and kw['json']['no_empleado'] == 'TEMP - 77'


async def test_incidencia_con_archivo_va_como_multipart():
    backend, ctx, chat = preparar(
        rutas={('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO]},
               ('POST', '/api/empleados/101/incidencias'): {'ok': True}},
        archivos={'f1': b'%PDF-1.4'},
    )
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, '5001'), ctx)
    await pantallas.al_boton_accion_empleado(pulsar(chat, 'ea:reg'), ctx)
    await pantallas.al_boton_asistente(pulsar(chat, 'a:2'), ctx)       # Suspensión
    await principal.al_texto(texto(chat, 'Faltas'), ctx)
    await principal.al_texto(texto(chat, '10/11/2026'), ctx)
    await principal.al_texto(texto(chat, '11/11/2026'), ctx)
    await principal.al_archivo(archivo(chat, 'virus.exe'), ctx)
    assert 'PDF, PNG o JPG' in chat.ultimo()[0]
    await principal.al_archivo(archivo(chat, 'acta.pdf'), ctx)
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    await principal.al_boton(pulsar(chat, ok), ctx)
    (_m, ruta, kw), = escrituras(backend)
    assert ruta == '/api/empleados/101/incidencias'
    assert kw['data']['fecha_reingreso'] == '2026-11-12'
    assert json.loads(kw['data']['detalles'])['dias'] == 2
    assert kw['files'] == [('archivo', ('acta.pdf', b'%PDF-1.4', 'application/pdf'))]


async def test_fecha_invalida_repite_el_paso():
    _b, ctx, chat = preparar(rutas={('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO]}})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, '5001'), ctx)
    await pantallas.al_boton_accion_empleado(pulsar(chat, 'ea:reg'), ctx)
    await pantallas.al_boton_asistente(pulsar(chat, 'a:0'), ctx)       # Vacaciones
    await principal.al_texto(texto(chat, 'el lunes'), ctx)
    assert 'No entendí la fecha' in chat.ultimo()[0] and 'Primer día' in chat.ultimo()[0]


async def test_cancelar_formulario():
    _b, ctx, chat = preparar(rutas={('GET', '/api/empleados/buscar'): {'empleados': [EMPLEADO]}})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:empleados'), ctx)
    await principal.al_texto(texto(chat, '5001'), ctx)
    await pantallas.al_boton_accion_empleado(pulsar(chat, 'ea:reg'), ctx)
    await pantallas.al_boton_asistente(pulsar(chat, 'a:x'), ctx)
    assert 'cancelado' in chat.ultimo()[0]
    await principal.al_texto(texto(chat, 'hola'), ctx)
    assert '/sistema' in chat.ultimo()[0]


async def test_descarga_va_directo_al_chat():
    backend, ctx, chat = preparar(rutas={('GET', '/api/exportar-empleados/CHEONG_WOON'): RespuestaBackend(
        200, 'application/vnd.ms-excel', contenido=b'XLSX', nombre_archivo='cw.xlsx')})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:dl_cw'), ctx)
    assert chat.documentos == [('cw.xlsx', b'XLSX')]
    assert backend.llamadas[0][2]['params'] == {'todos': '1'}


async def test_indicadores_del_mes(monkeypatch):
    monkeypatch.setattr(pantallas, 'hoy', lambda _c: date(2026, 11, 9))
    _b, ctx, chat = preparar(['reclutamiento'], rutas={('GET', '/api/datos-grafica'): [1, 2, 3, 4, 5, 6]})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:ind_mes'), ctx)
    assert 'noviembre 2026' in chat.ultimo()[0] and '• Reclu puro: 6' in chat.ultimo()[0]


async def test_error_del_backend_se_explica():
    _b, ctx, chat = preparar(rutas={('GET', '/api/obtener-pendientes'): ErrorBackend(403, 'x')})
    await pantallas.al_boton_nodo(pulsar(chat, 'm:ingresos'), ctx)
    assert 'no tiene permiso' in chat.ultimo()[0]
