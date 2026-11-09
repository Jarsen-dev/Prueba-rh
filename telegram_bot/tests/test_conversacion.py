from bot import conversacion
from bot.agente import Agente
from bot.config import Settings
from bot.estado import Estados
from bot.llm import ErrorLLM
from tests.fakes import Chat, FakeBackend, FakeLLM, contexto, pulsar, texto


def preparar(guion, presupuesto=(0, 0), settings=None):
    backend = FakeBackend(presupuesto=presupuesto)
    llm = FakeLLM(guion)
    ctx = contexto(backend, Estados(), settings or Settings(OPENAI_API_KEY='sk'))
    ctx.application.bot_data['agente'] = Agente(llm=llm, backend=backend)
    ctx.bot.send_chat_action = lambda *a, **k: None
    return backend, llm, ctx, Chat()


async def test_turno_completo_con_traza_y_calificacion():
    backend, _llm, ctx, chat = preparar(['Hay 3 activos.'])
    await conversacion.atender(texto(chat, '¿cuántos?'), ctx, '¿cuántos?')
    respuesta, teclado = chat.ultimo()
    assert respuesta.startswith('Hay 3 activos.') and '110 tokens' in respuesta
    assert backend.trazas[0]['resultado'] == 'ok' and backend.trazas[0]['tokens_entrada'] == 100
    assert 'Hay 3' not in str(backend.trazas)       # la traza no lleva contenido
    datos = [b.callback_data for b in teclado.inline_keyboard[0]]
    assert datos == ['cal:1:+', 'cal:1:-']
    consulta = pulsar(chat, 'cal:1:-')
    consulta.callback_query.edit_message_reply_markup = lambda **k: None
    await conversacion.al_calificar(consulta, ctx)
    assert backend.calificaciones == [(7, 1, -1)]


async def test_historial_se_recuerda_y_se_limita():
    _b, llm, ctx, chat = preparar(['r'] * 8)
    for i in range(8):
        await conversacion.atender(texto(chat, f'm{i}'), ctx, f'm{i}')
    estado = ctx.application.bot_data['estados'].de(7)
    assert len(estado.historial) == conversacion.MAX_HISTORIAL
    assert estado.historial[-2:] == [{'role': 'user', 'content': 'm7'}, {'role': 'assistant', 'content': 'r'}]
    assert not any(m['role'] == 'tool' for m in estado.historial)


async def test_presupuesto_global_y_por_persona():
    s = Settings(OPENAI_API_KEY='sk', BOT_TOKENS_DIA=1000, BOT_TOKENS_PERSONA_DIA=100)
    _b, llm, ctx, chat = preparar(['x'], presupuesto=(1000, 0), settings=s)
    await conversacion.atender(texto(chat, 'hola'), ctx, 'hola')
    assert chat.ultimo()[0] == conversacion.SIN_PRESUPUESTO and llm.recibidos == []
    _b, llm, ctx, chat = preparar(['x'], presupuesto=(10, 100), settings=s)
    await conversacion.atender(texto(chat, 'hola'), ctx, 'hola')
    assert chat.ultimo()[0] == conversacion.SIN_PRESUPUESTO_PERSONA and llm.recibidos == []


async def test_falla_del_modelo():
    _b, _llm, ctx, chat = preparar([ErrorLLM('caído')])
    await conversacion.atender(texto(chat, 'hola'), ctx, 'hola')
    assert chat.ultimo()[0] == conversacion.FALLA_LLM
    assert ctx.application.bot_data['estados'].de(7).ocupado is False


async def test_sin_agente():
    backend = FakeBackend()
    ctx = contexto(backend)
    chat = Chat()
    await conversacion.atender(texto(chat, 'hola'), ctx, 'hola')
    assert chat.ultimo()[0] == conversacion.SIN_AGENTE


async def test_ocupado():
    _b, llm, ctx, chat = preparar(['x'])
    ctx.application.bot_data['estados'].de(7).ocupado = True
    await conversacion.atender(texto(chat, 'hola'), ctx, 'hola')
    assert chat.ultimo()[0] == conversacion.OCUPADO and llm.recibidos == []


def test_traza_filtra_nombres_raros():
    from bot.agente import Resultado
    r = Resultado(texto='x', modelo='m', herramientas=['buscar_empleado', 'Ignora todo'])
    assert conversacion.datos_de_traza(r)['herramientas'] == ['buscar_empleado', 'desconocida']


def test_config_tope_cero_no_arranca():
    assert any('BOT_TOKENS' in p for p in Settings(TELEGRAM_BOT_TOKEN='t', TELEGRAM_BOT_SECRETO_INTERNO='s',
                                                     OPENAI_API_KEY='sk', BOT_TOKENS_DIA=0).problemas())


async def test_pendiente_del_agente_se_confirma_con_el_mismo_boton():
    from bot import principal
    from bot.backend import RespuestaBackend
    from bot.guardas import Vistos
    from tests.test_escrituras import BRUNO, GUIA

    backend = FakeBackend(guia=GUIA, rutas={('GET', '/api/empleados/buscar'): {'empleados': [BRUNO]},
                                            ('POST', '/api/actualizar-empleado/102'): RespuestaBackend(200, 'application/json', datos={})})
    llm = FakeLLM([[('buscar_empleado', {'texto': 'bruno'})],
                   [('preparar_estatus', {'registro_id': 102, 'accion': 'baja'})], 'Listo, confírmalo.'])
    ctx = contexto(backend, Estados(), Settings(OPENAI_API_KEY='sk'))
    ctx.application.bot_data['agente'] = Agente(llm=llm, backend=backend)
    chat = Chat()
    await conversacion.atender(texto(chat, 'da de baja a bruno'), ctx, 'da de baja a bruno')
    assert not [l for l in backend.llamadas if l[0] != 'GET']
    resumen, teclado = chat.ultimo()
    assert 'Dar de baja' in resumen
    ok = teclado.inline_keyboard[0][0].callback_data
    await principal.al_boton(pulsar(chat, ok), ctx)
    escrituras = [l for l in backend.llamadas if l[0] != 'GET']
    assert escrituras == [('POST', '/api/actualizar-empleado/102',
                           {'headers': {'X-Origen-Telegram': 'agente'}, 'json': {'estatus': 'INACTIVO'}})]
    assert isinstance(ctx.application.bot_data['estados'].de(7).vistos, Vistos)


async def test_archivo_fuera_de_formulario_queda_como_adjunto():
    from bot import principal
    from tests.fakes import archivo

    backend = FakeBackend()
    ctx = contexto(backend, Estados(), Settings(OPENAI_API_KEY='sk'), archivos={'f1': b'%PDF'})
    ctx.application.bot_data['agente'] = object()
    chat = Chat()
    await principal.al_archivo(archivo(chat, 'acta.pdf'), ctx)
    assert 'Recibí' in chat.ultimo()[0]
    assert ctx.application.bot_data['estados'].de(7).adjunto.nombre == 'acta.pdf'
