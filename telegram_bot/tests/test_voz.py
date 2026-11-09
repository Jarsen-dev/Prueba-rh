"""Notas de voz: transcribir, mostrar lo entendido y seguir el camino del texto."""

import httpx

from bot import principal
from bot.agente import Agente
from bot.backend import RespuestaBackend
from bot.config import Settings
from bot.estado import Estados
from bot.llm import ErrorLLM
from bot.voz import ClienteVoz, interpretar
from tests.fakes import Chat, FakeBackend, FakeLLM, FakeVoz, contexto, nota_de_voz, pulsar
from tests.test_escrituras import BRUNO


def preparar(transcrito, guion=(), presupuesto=(0, 0), rutas=None, settings=None, error=None):
    backend = FakeBackend(presupuesto=presupuesto, rutas=rutas)
    llm = FakeLLM(list(guion))
    ctx = contexto(backend, Estados(), settings or Settings(OPENAI_API_KEY='sk'), archivos={'v1': b'OggS audio'})
    ctx.application.bot_data['agente'] = Agente(llm=llm, backend=backend)
    voz = FakeVoz(transcrito, error)
    ctx.application.bot_data['voz'] = voz
    ctx.bot.send_chat_action = lambda *a, **k: None
    return backend, llm, voz, ctx, Chat()


def test_interpretar_uso_por_tokens_o_por_duracion():
    t = interpretar({'text': ' hola\n mundo ', 'usage': {'type': 'tokens', 'input_tokens': 50, 'output_tokens': 4}},
                    'gpt-4o-mini-transcribe', 0.3)
    assert (t.texto, t.tokens_entrada, t.tokens_salida) == ('hola mundo', 50, 4)
    assert interpretar({'text': 'x', 'usage': {'type': 'duration', 'seconds': 3}}, 'whisper-1', 0).tokens_entrada == 0


async def test_cliente_manda_el_audio_en_espanol_con_vocabulario():
    vistos = {}

    def manejar(peticion: httpx.Request):
        vistos['url'], vistos['cuerpo'] = str(peticion.url), peticion.content
        vistos['auth'] = peticion.headers['authorization']
        return httpx.Response(200, json={'text': 'da de baja a bruno'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(manejar)) as http:
        t = await ClienteVoz(api_key='sk-x', modelo='gpt-4o-mini-transcribe', base_url='https://api.x/v1/',
                             http=http).transcribir(b'OggS')
    assert t.texto == 'da de baja a bruno'
    assert vistos['url'] == 'https://api.x/v1/audio/transcriptions' and vistos['auth'] == 'Bearer sk-x'
    assert b'name="language"\r\n\r\nes' in vistos['cuerpo'] and b'Cheong Woon' in vistos['cuerpo']


async def test_cliente_convierte_errores():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500, text='x'))) as http:
        try:
            await ClienteVoz(api_key='k', modelo='m', base_url='https://a', http=http).transcribir(b'')
        except ErrorLLM as error:
            assert '500' in str(error)
        else:
            raise AssertionError('debió fallar')


async def test_nota_de_voz_muestra_lo_entendido_y_contesta():
    backend, llm, voz, ctx, chat = preparar('cuántos activos hay', ['Hay 3 activos.'])
    await principal.al_voz(nota_de_voz(chat), ctx)
    textos = [m for m, _ in chat.mensajes]
    assert textos[0] == '🎙 Entendí: «cuántos activos hay»' and textos[1].startswith('Hay 3 activos.')
    assert voz.recibidos == [(b'OggS audio', 'voz.ogg', 'audio/ogg')]
    assert llm.recibidos[0][-1] == {'role': 'user', 'content': 'cuántos activos hay'}
    assert 'nota de voz' in llm.recibidos[0][-2]['content']
    transcripcion, turno = backend.trazas
    assert transcripcion['herramientas'] == ['transcribir_voz'] and transcripcion['tokens_entrada'] == 120
    assert 'activos' not in str(backend.trazas)                    # la traza no lleva el texto
    assert ctx.application.bot_data['estados'].de(7).historial[0]['content'] == 'cuántos activos hay'


async def test_cambio_por_voz_queda_con_origen_voz():
    rutas = {('GET', '/api/empleados/buscar'): {'empleados': [BRUNO]},
             ('POST', '/api/actualizar-empleado/102'): RespuestaBackend(200, 'application/json', datos={})}
    backend, _llm, _voz, ctx, chat = preparar(
        'da de baja a bruno castillo', [[('buscar_empleado', {'texto': 'bruno'})],
                                         [('preparar_estatus', {'registro_id': 102, 'accion': 'baja'})], 'Listo.'],
        rutas=rutas)
    await principal.al_voz(nota_de_voz(chat), ctx)
    assert not [l for l in backend.llamadas if l[0] != 'GET']
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    await principal.al_boton(pulsar(chat, ok), ctx)
    assert [l for l in backend.llamadas if l[0] != 'GET'] == [
        ('POST', '/api/actualizar-empleado/102', {'headers': {'X-Origen-Telegram': 'voz'}, 'json': {'estatus': 'INACTIVO'}})]


async def test_voz_llena_el_paso_de_un_formulario_abierto():
    from bot.menu.asistente import Asistente, Paso

    _b, llm, _voz, ctx, chat = preparar('Faltó sin avisar')
    estado = ctx.application.bot_data['estados'].de(7)
    estado.asistente = Asistente(titulo='Prueba', pasos=[Paso('motivo', 'Motivo:', tipo='texto'),
                                                         Paso('otro', 'Otro:', tipo='texto')],
                                 al_terminar=lambda r: None)
    await principal.al_voz(nota_de_voz(chat), ctx)
    assert estado.asistente.respuestas == {'motivo': 'Faltó sin avisar'} and llm.recibidos == []


async def test_nota_larga_no_se_descarga_ni_se_cobra():
    backend, _llm, voz, ctx, chat = preparar('x', settings=Settings(OPENAI_API_KEY='sk', BOT_VOZ_MAX_SEGUNDOS=60))
    await principal.al_voz(nota_de_voz(chat, segundos=61), ctx)
    assert 'muy larga' in chat.ultimo()[0] and voz.recibidos == [] and backend.trazas == []


async def test_sin_presupuesto_no_transcribe():
    _b, _llm, voz, ctx, chat = preparar('x', presupuesto=(10**9, 0))
    await principal.al_voz(nota_de_voz(chat), ctx)
    assert 'presupuesto' in chat.ultimo()[0] and voz.recibidos == []


async def test_sin_voz_configurada():
    _b, _llm, _voz, ctx, chat = preparar('x')
    del ctx.application.bot_data['voz']
    await principal.al_voz(nota_de_voz(chat), ctx)
    assert 'no están activas' in chat.ultimo()[0]


async def test_falla_o_silencio():
    _b, llm, _voz, ctx, chat = preparar('x', error=ErrorLLM('caído'))
    await principal.al_voz(nota_de_voz(chat), ctx)
    assert 'no está disponible' in chat.ultimo()[0]
    _b, llm, _voz, ctx, chat = preparar('  ')
    await principal.al_voz(nota_de_voz(chat), ctx)
    assert 'No entendí' in chat.ultimo()[0] and llm.recibidos == []
    assert ctx.application.bot_data['estados'].de(7).ocupado is False


def test_voz_activa_depende_de_la_clave_y_del_maximo():
    assert Settings(OPENAI_API_KEY='sk').voz_activa
    assert not Settings(OPENAI_API_KEY='').voz_activa
    assert not Settings(OPENAI_API_KEY='sk', BOT_VOZ_MAX_SEGUNDOS=0).voz_activa
