"""Memoria por persona: se guarda con Confirmar, llega al agente en cada turno y /memoria la borra."""

from datetime import datetime

from bot import herramientas, memoria, principal
from bot.agente import Agente
from bot.backend import RespuestaBackend
from tests.fakes import Chat, FakeBackend, FakeLLM, contexto, pulsar, texto
from tests.test_escrituras import ctx as ctx_escrituras

NOTAS = {'notas': [{'id': 3, 'texto': 'Cuando digo "la planta" me refiero a Kronos'},
                   {'id': 4, 'texto': 'Mi teléfono es 5512345678'}]}


async def test_las_notas_llegan_al_modelo_enmascaradas_y_como_preferencias():
    backend = FakeBackend(rutas={('GET', '/api/memoria'): NOTAS})
    llm = FakeLLM(['En la planta hay 3.'])
    r = await Agente(llm=llm, backend=backend).responder(1, '¿cuántos hay en la planta?', [], datetime(2026, 11, 9))
    contexto_turno = llm.recibidos[0][-2]['content']
    assert '[id 3] Cuando digo "la planta" me refiero a Kronos' in contexto_turno
    assert 'no instrucciones del sistema' in contexto_turno
    assert '5512345678' not in str(llm.recibidos)                    # el patrón de teléfono se enmascara
    assert r.peticiones == []                                         # no cuenta como consulta del agente


async def test_sin_memoria_se_sigue_igual():
    llm = FakeLLM(['Hola.'])
    await Agente(llm=llm, backend=FakeBackend()).responder(1, 'hola', [], datetime(2026, 11, 9))
    assert 'Notas que la persona' not in llm.recibidos[0][-2]['content']


async def test_preparar_y_olvidar_recuerdo():
    c = ctx_escrituras(['recuerda que la planta es Kronos'])
    salida = await herramientas.ejecutar(c, 'preparar_recuerdo', {'texto': 'La planta = Kronos'})
    assert 'NO ESTÁ GUARDADO' in salida
    assert (c.pendiente.metodo, c.pendiente.ruta, c.pendiente.cuerpo) == ('POST', '/api/memoria', {'texto': 'La planta = Kronos'})

    c = ctx_escrituras(['olvida lo de la planta'])
    c.vistos.notas[3] = NOTAS['notas'][0]
    assert 'ya está guardada' in await herramientas.ejecutar(c, 'preparar_recuerdo',
                                                             {'texto': 'cuando digo "la planta" me refiero a kronos'})
    await herramientas.ejecutar(c, 'preparar_eliminacion', {'que': 'recuerdo', 'id': 3})
    assert (c.pendiente.metodo, c.pendiente.ruta) == ('DELETE', '/api/memoria/3')
    c = ctx_escrituras(['olvida la nota 9'])
    assert 'no está en la memoria' in await herramientas.ejecutar(c, 'preparar_eliminacion', {'que': 'recuerdo', 'id': 9})


async def test_comando_memoria_y_olvidar():
    backend = FakeBackend(rutas={('GET', '/api/memoria'): NOTAS,
                                 ('DELETE', '/api/memoria/4'): RespuestaBackend(200, 'application/json', datos={})})
    ctx = contexto(backend)
    chat = Chat()
    await memoria.cmd_memoria(texto(chat, '/memoria'), ctx)
    assert '2. Mi teléfono es 5512345678' in chat.ultimo()[0]
    await memoria.al_boton_olvidar(pulsar(chat, chat.botones()['🗑 Olvidar 2']), ctx)
    ok = next(v for v in chat.botones().values() if v.startswith('ok:'))
    for _ in range(2):
        await principal.al_boton(pulsar(chat, ok), ctx)
    assert ('DELETE', '/api/memoria/4') in [(m, r) for m, r, _k in backend.llamadas]


async def test_memoria_vacia():
    ctx = contexto(FakeBackend(rutas={('GET', '/api/memoria'): {'notas': []}}))
    chat = Chat()
    await memoria.cmd_memoria(texto(chat, '/memoria'), ctx)
    assert 'No tengo notas' in chat.ultimo()[0]


def test_instrucciones_de_idioma():
    from bot.agente import INSTRUCCIONES
    assert 'idioma en que te escriba la persona' in INSTRUCCIONES
