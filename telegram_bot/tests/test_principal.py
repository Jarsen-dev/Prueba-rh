from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram.constants import ChatType

from bot import principal
from bot.backend import ErrorBackend, NoVinculado, Sesion
from bot.borrador import CambioPendiente
from bot.config import Settings
from bot.estado import Estados


def test_parametros_busqueda():
    assert principal.parametros_busqueda(' 1234 ') == {'no_empleado': '1234'}
    assert principal.parametros_busqueda('ana  lópez') == {'q': 'ana lópez'}
    assert principal.parametros_busqueda('an') is None
    assert principal.parametros_busqueda('') is None


def test_formatear_empleado_sin_datos():
    texto = principal.formatear_empleado({'nombre_completo': 'Ana', 'no_empleado': None})
    assert 'Ana' in texto and 'Sin asignar' in texto


def test_texto_error():
    assert 'vinculado' in principal.texto_error(NoVinculado(404, 'x'))
    assert 'permiso' in principal.texto_error(ErrorBackend(403, 'x'))
    assert 'Vuelve a vincularlo' in principal.texto_error(ErrorBackend(401, 'x'))


def test_callback_data_cabe_en_64_bytes():
    p = CambioPendiente('DELETE', '/api/eliminar-empleado/999999999', 'x')
    for fila in principal.teclado_confirmar(p).inline_keyboard:
        for boton in fila:
            assert len(boton.callback_data.encode()) <= 64


def test_partir_respeta_el_tope():
    partes = principal.partir('a' * 10000 + '\nfin', tope=4096)
    assert all(len(p) <= 4096 for p in partes)
    assert ''.join(partes).replace('\n', '') == 'a' * 10000 + 'fin'


# --- Flujo del botón -----------------------------------------------------------------

class FakeBackend:
    def __init__(self, error=None):
        self.llamadas = []
        self.error = error

    async def peticion(self, telegram_id, metodo, ruta, **kw):
        self.llamadas.append((telegram_id, metodo, ruta, kw))
        if self.error:
            raise self.error


def contexto(backend, estados):
    app = SimpleNamespace(bot_data={'backend': backend, 'estados': estados, 'settings': Settings()})
    return SimpleNamespace(application=app, args=[])


def pulsar(data, telegram_id=7):
    consulta = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=telegram_id),
        message=SimpleNamespace(chat=SimpleNamespace(type=ChatType.PRIVATE)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=consulta), consulta


async def test_delete_necesita_dos_clics_y_ejecuta_una_vez():
    backend, estados = FakeBackend(), Estados()
    p = CambioPendiente('DELETE', '/api/eliminar-empleado/3', 'Eliminar')
    estados.de(7).pendiente = p
    ctx = contexto(backend, estados)

    update, consulta = pulsar(f'ok:{p.id}')
    await principal.al_boton(update, ctx)
    assert backend.llamadas == []
    assert 'otra vez' in consulta.edit_message_text.call_args[0][0]

    await principal.al_boton(update, ctx)
    assert len(backend.llamadas) == 1
    assert backend.llamadas[0][:3] == (7, 'DELETE', '/api/eliminar-empleado/3')
    assert backend.llamadas[0][3]['headers'] == {'X-Origen-Telegram': 'menu'}

    await principal.al_boton(update, ctx)  # tercer clic: ya no está pendiente
    assert len(backend.llamadas) == 1
    assert 'ya no está pendiente' in consulta.edit_message_text.call_args[0][0]


async def test_boton_de_otra_persona_no_ejecuta_el_pendiente_ajeno():
    backend, estados = FakeBackend(), Estados()
    p = CambioPendiente('POST', '/api/x', 'x')
    estados.de(7).pendiente = p
    update, _ = pulsar(f'ok:{p.id}', telegram_id=8)
    await principal.al_boton(update, contexto(backend, estados))
    assert backend.llamadas == []
    assert estados.de(7).pendiente is p


async def test_boton_viejo_no_ejecuta_el_pendiente_nuevo():
    backend, estados = FakeBackend(), Estados()
    viejo = CambioPendiente('POST', '/api/x', 'x')
    estados.de(7).pendiente = CambioPendiente('POST', '/api/y', 'y')
    update, _ = pulsar(f'ok:{viejo.id}')
    await principal.al_boton(update, contexto(backend, estados))
    assert backend.llamadas == []


async def test_cancelar():
    backend, estados = FakeBackend(), Estados()
    p = CambioPendiente('POST', '/api/x', 'x')
    estados.de(7).pendiente = p
    update, consulta = pulsar(f'no:{p.id}')
    await principal.al_boton(update, contexto(backend, estados))
    assert backend.llamadas == [] and estados.de(7).pendiente is None
    assert 'Cancelado' in consulta.edit_message_text.call_args[0][0]


async def test_sin_respuesta_avisa_que_puede_haberse_aplicado():
    backend, estados = FakeBackend(error=httpx.ReadTimeout('x')), Estados()
    p = CambioPendiente('POST', '/api/x', 'x')
    estados.de(7).pendiente = p
    update, consulta = pulsar(f'ok:{p.id}')
    await principal.al_boton(update, contexto(backend, estados))
    assert 'es posible que el cambio sí se haya aplicado' in consulta.edit_message_text.call_args[0][0]
    assert estados.de(7).pendiente is None  # no se reintenta solo


async def test_eliminar_sin_permiso_admin_no_deja_pendiente():
    class B:
        async def sesion(self, tid):
            return Sesion('t', None, {'usuario': 'rita', 'permisos': ['reclutamiento']})

    estados = Estados()
    mensaje = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_message=mensaje)
    ctx = contexto(B(), estados)
    ctx.args = ['123']
    await principal.cmd_eliminar(update, ctx)
    assert estados.de(7).pendiente is None
    assert 'permiso' in mensaje.reply_text.call_args[0][0]


def test_config_exige_token_y_secreto():
    problemas = Settings(TELEGRAM_BOT_TOKEN='', TELEGRAM_BOT_SECRETO_INTERNO='').problemas()
    assert len(problemas) == 2
    assert Settings(TELEGRAM_BOT_TOKEN='x', TELEGRAM_BOT_SECRETO_INTERNO='y').problemas() == []


def test_handlers_registrados_solo_en_privado():
    app = principal.construir(Settings(TELEGRAM_BOT_TOKEN='123:abc', TELEGRAM_BOT_SECRETO_INTERNO='s'))
    comandos = {c for h in app.handlers[0] if hasattr(h, 'commands') for c in h.commands}
    assert {'start', 'vincular', 'desvincular', 'consultar', 'eliminar', 'cancelar'} <= comandos
