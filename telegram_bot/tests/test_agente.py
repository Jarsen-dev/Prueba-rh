from datetime import date, datetime

from bot import herramientas
from bot.agente import CIERRE_RONDAS, MAX_RONDAS, Agente
from bot.backend import ErrorBackend, RespuestaBackend
from tests.fakes import GUIA_REAL, FakeBackend, FakeLLM

AHORA = datetime(2026, 11, 9, 10, 30)
GUIA = GUIA_REAL
ANA = {'registro_id': 7, 'nombre_completo': 'Ana Prueba', 'no_empleado': '5001', 'empresa': 'KRONOS',
       'area': 'EPS', 'puesto': 'MOLDERO', 'turno': 'NOCHE', 'estatus': 'ACTIVO'}


def ctx(backend, permisos=('admin',)):
    return herramientas.Contexto(backend=backend, telegram_id=1, permisos=list(permisos), hoy=date(2026, 11, 9))


async def test_respuesta_directa_y_contexto_del_turno():
    llm = FakeLLM(['Hola, ¿en qué te ayudo?'])
    resultado = await Agente(llm=llm, backend=FakeBackend()).responder(1, 'hola', [], AHORA)
    assert resultado.texto == 'Hola, ¿en qué te ayudo?' and resultado.rondas == 1
    enviados = llm.recibidos[0]
    assert enviados[0]['role'] == 'system'
    assert enviados[-2]['role'] == 'developer' and 'lunes 09/11/2026' in enviados[-2]['content']
    assert 'Permisos: admin' in enviados[-2]['content']
    assert enviados[-1] == {'role': 'user', 'content': 'hola'}


async def test_bucle_con_herramienta_y_datos_enmascarados():
    backend = FakeBackend(rutas={
        ('GET', '/api/empleados/buscar'): {'empleados': [ANA]},
        ('GET', '/api/obtener-empleado/7'): {**ANA, 'curp': 'PELA900101MDFRPN09', 'contrasena': 'x1'},
    })
    llm = FakeLLM([[('buscar_empleado', {'texto': 'ana'})], [('ver_empleado', {'registro_id': 7})], 'Ana es moldero.'])
    resultado = await Agente(llm=llm, backend=backend).responder(1, '¿quién es ana?', [], AHORA)
    assert resultado.herramientas == ['buscar_empleado', 'ver_empleado']
    assert resultado.tokens == 330
    enviado = str(llm.recibidos[-1])
    assert '<datos_no_confiables>' in enviado
    assert 'PELA900101MDFRPN09' not in enviado and 'x1' not in enviado and '****PN09' in enviado


async def test_tope_de_rondas():
    llm = FakeLLM([[('buscar_endpoints', {'consulta': 'x'})]] * MAX_RONDAS)
    resultado = await Agente(llm=llm, backend=FakeBackend()).responder(1, 'x', [], AHORA)
    assert (resultado.texto, resultado.resultado, resultado.rondas) == (CIERRE_RONDAS, 'rondas', MAX_RONDAS)


async def test_historial_va_antes_del_turno():
    llm = FakeLLM(['ok'])
    historial = [{'role': 'user', 'content': 'antes'}, {'role': 'assistant', 'content': 'resp'}]
    await Agente(llm=llm, backend=FakeBackend()).responder(1, 'ahora', historial, AHORA)
    assert [m['content'] for m in llm.recibidos[0][1:3]] == ['antes', 'resp']


async def test_consultar_api_solo_rutas_de_la_guia():
    backend = FakeBackend(guia=GUIA)
    for ruta in ('/api/access-users', '/api/auth/me', '/api/telegram/vinculos', '/api/../auth/me', 'http://x/api/a'):
        salida = await herramientas.consultar_api(ctx(backend), ruta)
        assert 'no' in salida.lower()
    assert backend.llamadas == []


async def test_consultar_api_no_hace_escrituras():
    backend = FakeBackend(guia=GUIA)
    salida = await herramientas.consultar_api(ctx(backend), '/api/eliminar-empleado/7')
    assert 'no es una operación disponible' in salida and backend.llamadas == []


async def test_consultar_api_respeta_permisos_antes_de_llamar():
    backend = FakeBackend(guia=GUIA)
    salida = await herramientas.consultar_api(ctx(backend, ['reclutamiento']), '/api/empleados/7/incidencias')
    assert 'permiso' in salida and backend.llamadas == []


async def test_archivo_va_a_la_persona_y_no_al_modelo():
    backend = FakeBackend(guia=GUIA, rutas={('GET', '/api/exportar-reclutamiento'): RespuestaBackend(
        200, 'application/vnd.ms-excel', contenido=b'PKsecreto', nombre_archivo='r.xlsx')})
    c = ctx(backend)
    salida = await herramientas.consultar_api(c, '/api/exportar-reclutamiento', {'mes': 11})
    assert 'r.xlsx' in salida and 'PKsecreto' not in salida
    assert [(a.nombre, a.contenido) for a in c.archivos] == [('r.xlsx', b'PKsecreto')]
    assert backend.llamadas[0][2]['params'] == {'mes': '11'}


async def test_buscar_endpoints_no_ofrece_escrituras_ni_sin_permiso():
    salida = await herramientas.buscar_endpoints(ctx(FakeBackend(guia=GUIA), ['reclutamiento']), 'empleado excel')
    assert 'exportar-reclutamiento' in salida and 'incidencias' not in salida and 'eliminar' not in salida


async def test_contar_empleados_cuenta_en_codigo():
    otros = [{**ANA, 'registro_id': i, 'turno': 'DÍA' if i % 2 else 'NOCHE', 'estatus': 'INACTIVO' if i == 3 else 'ACTIVO'}
             for i in range(1, 7)]
    backend = FakeBackend(rutas={('GET', '/api/obtener-empleados/KRONOS'): otros,
                                 ('GET', '/api/obtener-empleados/CHEONG_WOON'): [{**ANA, 'empresa': 'CHEONG WOON'}]})
    salida = await herramientas.contar_empleados(ctx(backend), empresa='KRONOS', turno='noche')
    assert '"total": 3' in salida
    salida = await herramientas.contar_empleados(ctx(backend), empresa='TODAS', estatus='TODOS')
    assert '"total": 7' in salida


async def test_resumen_reclutamiento():
    backend = FakeBackend(rutas={
        ('GET', '/api/ingresos-por-team'): {'equipos': [{'team': 1, 'total': 4, 'reclutadores': []}]},
        ('GET', '/api/datos-grafica'): [1, 0, 2, 0, 1, 0],
    })
    salida = await herramientas.resumen_reclutamiento(ctx(backend))
    assert '"total_del_mes": 4' in salida and '"VOLANTE": 2' in salida


async def test_errores_vuelven_como_texto():
    backend = FakeBackend(rutas={('GET', '/api/empleados/buscar'): ErrorBackend(403, 'x')})
    assert '403' in await herramientas.ejecutar(ctx(backend), 'buscar_empleado', {'texto': 'ana'})
    assert 'no existe' in await herramientas.ejecutar(ctx(backend), 'borrar_todo', {})
    assert 'Argumentos' in await herramientas.ejecutar(ctx(backend), 'buscar_empleado', {'zzz': 1})


def test_herramientas_de_lectura_y_de_preparar():
    assert herramientas.LECTURA == {'buscar_empleado', 'ver_empleado', 'contar_empleados', 'resumen_reclutamiento',
                                    'buscar_endpoints', 'consultar_api', 'ver_pendientes', 'ver_incidencias',
                                    'ver_tareas'}
    assert all(n.startswith('preparar_') for n in herramientas.NOMBRES - herramientas.LECTURA)


async def test_consultas_curadas_tambien_respetan_permisos():
    backend = FakeBackend(guia=GUIA)
    salida = await herramientas.ejecutar(ctx(backend, ['reclutamiento']), 'ver_incidencias', {'registro_id': 7})
    assert 'permiso' in salida and backend.llamadas == []
