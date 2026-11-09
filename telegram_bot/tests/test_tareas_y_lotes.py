"""Lotes (un Confirmar, varios cambios en orden) y tareas programadas (solo lectura)."""

from types import SimpleNamespace

import httpx

from bot import principal, tareas
from bot.agente import Agente
from bot.backend import ErrorBackend, RespuestaBackend
from bot.borrador import CambioPendiente, Lote
from bot.config import Settings
from bot.estado import Estados
from tests.fakes import Chat, FakeBackend, FakeLLM, contexto, pulsar, texto

OK = RespuestaBackend(200, 'application/json', datos={})


def cambio(n, metodo='POST'):
    return CambioPendiente(metodo, f'/api/actualizar-empleado/{n}', f'Cambio {n}:', cuerpo={'estatus': 'INACTIVO'},
                           detalle=[f'Nombre: Persona {n}'])


async def confirmar_lote(rutas, cambios):
    backend = FakeBackend(rutas=rutas)
    ctx = contexto(backend, Estados(), Settings())
    chat = Chat()
    lote = Lote(cambios)
    ctx.application.bot_data['estados'].de(7).pendiente = lote
    for _ in range(lote.confirmaciones_faltantes):
        await principal.al_boton(pulsar(chat, f'ok:{lote.id}'), ctx)
    return backend, chat, lote


async def test_lote_aplica_en_orden_y_sigue_tras_un_rechazo():
    rutas = {('POST', '/api/actualizar-empleado/1'): OK,
             ('POST', '/api/actualizar-empleado/2'): ErrorBackend(400, 'Dato inválido'),
             ('POST', '/api/actualizar-empleado/3'): OK}
    backend, chat, lote = await confirmar_lote(rutas, [cambio(1), cambio(2), cambio(3)])
    assert [r for _m, r, _k in backend.llamadas] == [f'/api/actualizar-empleado/{n}' for n in (1, 2, 3)]
    assert all(k['headers'] == {'X-Origen-Telegram': f'trabajo:{lote.trabajo}'} for _m, _r, k in backend.llamadas)
    final = chat.ultimo()[0]
    assert '📦 Resultado: 2 de 3 aplicados.' in final
    assert '✅ 1. Cambio 1 · Persona 1' in final and '⚠️ 2. Cambio 2 · Persona 2: ' in final
    assert '✅ 3. Cambio 3 · Persona 3' in final
    assert any(m.startswith('⏳ Aplicando 2 de 3') for m, _ in chat.mensajes)


async def test_lote_se_detiene_si_el_sistema_no_responde():
    rutas = {('POST', '/api/actualizar-empleado/1'): httpx.ReadTimeout('x'), ('POST', '/api/actualizar-empleado/2'): OK}
    backend, chat, _ = await confirmar_lote(rutas, [cambio(1), cambio(2)])
    assert len(backend.llamadas) == 1
    assert '❓ 1. Cambio 1 · Persona 1' in chat.ultimo()[0]
    assert '⏸ 2. Cambio 2 · Persona 2: no se intentó.' in chat.ultimo()[0]


async def test_lote_con_borrado_pide_dos_clics():
    backend, chat, lote = await confirmar_lote({('POST', '/api/actualizar-empleado/1'): OK,
                                                ('DELETE', '/api/actualizar-empleado/2'): OK},
                                               [cambio(1), cambio(2, 'DELETE')])
    assert lote.confirmaciones_faltantes == 0 and len(backend.llamadas) == 2
    assert any('¿Seguro?' in m for m, _ in chat.mensajes)


def test_lote_rechaza_cambios_encadenados():
    import pytest
    con_siguiente = cambio(1)
    con_siguiente.siguiente = cambio(9)
    with pytest.raises(ValueError):
        Lote([con_siguiente, cambio(2)])


# --- Tareas programadas ---------------------------------------------------------------

class BackendTareas(FakeBackend):
    def __init__(self, turno, presupuesto=(0, 0)):
        super().__init__(presupuesto=presupuesto, rutas={('GET', '/api/empleados/buscar'): {'empleados': []}})
        self.turno = turno

    async def tareas_turno(self):
        return list(self.turno)


def app_con(backend, guion, settings=None):
    enviados = []

    async def send_message(chat_id, text):
        enviados.append((chat_id, text))

    async def send_document(chat_id, document, filename):
        enviados.append((chat_id, f'archivo:{filename}'))

    llm = FakeLLM(guion)
    app = SimpleNamespace(bot_data={'backend': backend, 'settings': settings or Settings(OPENAI_API_KEY='sk'),
                                    'agente': Agente(llm=llm, backend=backend)},
                          bot=SimpleNamespace(send_message=send_message, send_document=send_document))
    return app, llm, enviados


TAREA = {'id': 4, 'telegram_id': 7, 'instruccion': '¿Cuántos activos hay?', 'descripcion': 'todos los días a las 09:00'}


async def test_tarea_corre_en_solo_lectura_y_deja_traza():
    backend = BackendTareas([TAREA])
    app, llm, enviados = app_con(backend, ['Hay 3 activos.'])
    assert await tareas.repartir(app) == 1
    nombres = {d['function']['name'] for d in llm.herramientas_recibidas[0]}
    assert nombres and not any(n.startswith('preparar_') for n in nombres)
    assert 'TAREA PROGRAMADA' in llm.recibidos[0][-2]['content']
    (chat_id, texto_enviado), = enviados
    assert chat_id == 7 and texto_enviado.startswith('🗓 Tarea programada (todos los días a las 09:00)')
    assert 'Hay 3 activos.' in texto_enviado and backend.trazas[0]['resultado'] == 'ok'


async def test_tarea_sin_presupuesto_avisa_sin_gastar():
    backend = BackendTareas([TAREA], presupuesto=(10**9, 0))
    app, llm, enviados = app_con(backend, [])
    await tareas.repartir(app)
    assert llm.recibidos == [] and 'tope diario' in enviados[0][1]


async def test_una_tarea_que_falla_no_detiene_las_demas():
    backend = BackendTareas([{**TAREA, 'telegram_id': 1}, TAREA])
    app, _llm, enviados = app_con(backend, ['Hay 3 activos.'])
    original = app.bot.send_message

    async def falla_para_1(chat_id, text):
        if chat_id == 1:
            raise RuntimeError('bloqueó al bot')
        await original(chat_id, text)

    app.bot.send_message = falla_para_1
    backend.turno = [{**TAREA, 'telegram_id': 1}, TAREA]
    app.bot_data['agente'] = Agente(llm=FakeLLM(['uno', 'dos']), backend=backend)
    assert await tareas.repartir(app) == 1 and enviados[0][0] == 7


async def test_comando_tareas_y_borrar_con_doble_clic():
    lista = {'tareas': [{'id': 11, 'instruccion': 'Resumen', 'descripcion': 'cada lunes a las 09:00'}]}
    backend = FakeBackend(rutas={('GET', '/api/tareas'): lista, ('DELETE', '/api/tareas/11'): OK})
    ctx = contexto(backend)
    chat = Chat()
    await tareas.cmd_tareas(texto(chat, '/tareas'), ctx)
    assert '1. cada lunes a las 09:00: «Resumen»' in chat.ultimo()[0]
    await tareas.al_boton_borrar(pulsar(chat, chat.botones()['🗑 Borrar 1']), ctx)
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    await principal.al_boton(pulsar(chat, ok), ctx)
    await principal.al_boton(pulsar(chat, ok), ctx)
    assert ('DELETE', '/api/tareas/11') in [(m, r) for m, r, _k in backend.llamadas]


async def test_sin_tareas():
    ctx = contexto(FakeBackend(rutas={('GET', '/api/tareas'): {'tareas': []}}))
    chat = Chat()
    await tareas.cmd_tareas(texto(chat, '/tareas'), ctx)
    assert 'No tienes tareas' in chat.ultimo()[0]
