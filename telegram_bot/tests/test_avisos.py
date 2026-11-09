"""Avisos diarios: formato, reparto por persona (con su sesión) y /avisos."""

from types import SimpleNamespace

from bot import avisos
from bot.backend import ErrorBackend
from tests.fakes import Chat, FakeBackend, contexto, pulsar, texto

ADMIN = {
    'fecha': '2026-11-09',
    'contratos': [{'registro_id': 1, 'nombre_completo': 'Ana Martínez', 'no_empleado': '5001', 'empresa': 'KRONOS',
                   'fin_contrato': '2026-11-10'}],
    'contratos_dias': 7,
    'reingresos': [{'nombre_completo': 'Bruno Castillo', 'no_empleado': '5002', 'tipo': 'vacaciones',
                    'fecha': '2026-11-09'}],
    'inicios': [{'nombre_completo': 'Carla Gómez', 'no_empleado': '5003', 'tipo': 'incapacidad',
                 'fecha': '2026-11-12'}],
    'candidatos': {'sin_asignar': 12, 'registrados_ayer': 2, 'dias': 7,
                   'lista': [{'nombre_completo': f'C{n}', 'dias_esperando': 9} for n in range(12)]},
}


def test_formato_completo():
    texto = avisos.formatear(ADMIN)
    assert texto.startswith('🔔 Avisos de hoy (lunes 09/11/2026)')
    assert '• Ana Martínez (5001) · KRONOS · vence mañana' in texto
    assert '• Bruno Castillo (5002) · hoy (de vacaciones)' in texto
    assert '· jueves 12/11' in texto
    assert '12 candidato(s) llevan más de 7 días' in texto and '… y 2 más' in texto
    assert 'Ayer se registraron 2' in texto


def test_sin_nada_no_se_manda():
    assert avisos.formatear({'fecha': '2026-11-09', 'contratos': [], 'candidatos': {'sin_asignar': 0}}) is None


def test_reclutamiento_ve_solo_conteos():
    texto = avisos.formatear({'fecha': '2026-11-09', 'candidatos': {'sin_asignar': 3, 'registrados_ayer': 0, 'dias': 7}})
    assert '3 candidato(s) llevan más de 7 días sin puesto asignado.' in texto and '•' not in texto


class BackendAvisos(FakeBackend):
    def __init__(self, turno, por_persona, preferencias=None):
        super().__init__()
        self.turno, self.por_persona = turno, por_persona
        self.preferencias = preferencias or {'activos': True, 'hora': 8}
        self.cambios = []

    async def avisos_turno(self):
        return list(self.turno)

    async def avisos_preferencias(self, telegram_id, **cambios):
        self.cambios.append(cambios)
        self.preferencias.update(cambios)
        return dict(self.preferencias)

    async def peticion(self, telegram_id, metodo, ruta, **kw):
        from bot.backend import RespuestaBackend
        self.llamadas.append((telegram_id, metodo, ruta))
        datos = self.por_persona[telegram_id]
        if isinstance(datos, Exception):
            raise datos
        return RespuestaBackend(200, 'application/json', datos=datos)


async def test_reparto_con_la_sesion_de_cada_persona():
    vacio = {'fecha': '2026-11-09', 'candidatos': {'sin_asignar': 0}}
    backend = BackendAvisos([1, 2, 3, 4], {1: ADMIN, 2: vacio, 3: ErrorBackend(403, 'sin permiso'), 4: ADMIN})
    mandados = []

    async def send_message(chat_id, text):
        if chat_id == 1:
            raise RuntimeError('la persona bloqueó al bot')
        mandados.append((chat_id, text))

    app = SimpleNamespace(bot_data={'backend': backend}, bot=SimpleNamespace(send_message=send_message))
    assert await avisos.repartir(app) == 1
    assert [c for c, _ in mandados] == [4] and mandados[0][1].endswith(avisos.PIE)
    assert backend.llamadas == [(t, 'GET', '/api/avisos') for t in (1, 2, 3, 4)]


async def test_comando_y_botones():
    backend = BackendAvisos([], {7: {'fecha': '2026-11-09'}})
    ctx = contexto(backend)
    chat = Chat()
    await avisos.cmd_avisos(texto(chat, '/avisos'), ctx)
    pantalla, teclado = chat.ultimo()
    assert 'encendidos, a las 8:00' in pantalla and '✅ 8:00' in chat.botones()
    await avisos.al_boton(pulsar(chat, 'av:h9'), ctx)
    assert backend.cambios[-1] == {'hora': 9} and 'a las 9:00' in chat.ultimo()[0]
    await avisos.al_boton(pulsar(chat, 'av:off'), ctx)
    assert backend.cambios[-1] == {'activos': False} and 'apagados' in chat.ultimo()[0]
    assert '9:00' not in chat.botones()
    await avisos.al_boton(pulsar(chat, 'av:ver'), ctx)
    assert 'Hoy no hay avisos' in chat.ultimo()[0]
    await avisos.al_boton(pulsar(chat, 'av:h3'), ctx)            # hora fuera de la lista: se ignora
    assert backend.cambios[-1] == {'activos': False}


async def test_ciclo_sobrevive_a_un_backend_caido():
    import asyncio

    class Caido(BackendAvisos):
        vueltas = 0

        async def avisos_turno(self):
            Caido.vueltas += 1
            raise ErrorBackend(503, 'caído')

    app = SimpleNamespace(bot_data={'backend': Caido([], {})}, bot=None)
    tarea = asyncio.create_task(avisos.ciclo(app, intervalo=0.01))
    await asyncio.sleep(0.05)
    tarea.cancel()
    assert Caido.vueltas >= 2
