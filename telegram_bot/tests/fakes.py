"""Dobles de Telegram y del backend para probar handlers sin red."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.constants import ChatType

from bot.backend import ErrorBackend, RespuestaBackend, Sesion
from bot.config import Settings
from bot.estado import Estados


class FakeBackend:
    """Responde según (método, ruta) y registra cada llamada."""

    def __init__(self, permisos=('admin',), rutas=None):
        self.permisos = list(permisos)
        self.rutas = rutas or {}
        self.llamadas = []

    async def sesion(self, telegram_id, renovar=False):
        return Sesion('tok', datetime.now(UTC) + timedelta(minutes=15), {'usuario': 'ana', 'permisos': self.permisos})

    async def peticion(self, telegram_id, metodo, ruta, **kw):
        self.llamadas.append((metodo, ruta, kw))
        respuesta = self.rutas.get((metodo, ruta))
        if isinstance(respuesta, Exception):
            raise respuesta
        if respuesta is None:
            raise ErrorBackend(404, f'Sin doble para {metodo} {ruta}')
        if isinstance(respuesta, RespuestaBackend):
            return respuesta
        return RespuestaBackend(200, 'application/json', datos=respuesta)


class Chat:
    """Registra lo que el bot «dice»: mensajes nuevos, ediciones y documentos."""

    def __init__(self):
        self.mensajes = []   # (texto, teclado)
        self.documentos = []

    def ultimo(self):
        return self.mensajes[-1]

    def botones(self):
        teclado = self.ultimo()[1]
        if teclado is None:
            return {}
        return {b.text: b.callback_data for fila in teclado.inline_keyboard for b in fila}


def contexto(backend, estados=None, settings=None, archivos=None):
    app = SimpleNamespace(bot_data={
        'backend': backend, 'estados': estados or Estados(), 'settings': settings or Settings(),
    })
    bot = SimpleNamespace(get_file=AsyncMock(side_effect=lambda fid: SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray((archivos or {}).get(fid, b'')))
    )))
    return SimpleNamespace(application=app, args=[], bot=bot)


def _mensaje(chat, **extra):
    async def reply_text(texto, reply_markup=None):
        chat.mensajes.append((texto, reply_markup))

    async def reply_document(document=None, filename=None):
        chat.documentos.append((filename, document))

    return SimpleNamespace(
        reply_text=reply_text, reply_document=reply_document,
        chat=SimpleNamespace(type=ChatType.PRIVATE), photo=None, document=None, text=None, **extra,
    )


def texto(chat, contenido, telegram_id=7):
    mensaje = _mensaje(chat)
    mensaje.text = contenido
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje, callback_query=None)


def archivo(chat, nombre, file_id='f1', tamano=100, telegram_id=7):
    mensaje = _mensaje(chat)
    mensaje.document = SimpleNamespace(file_name=nombre, file_size=tamano, file_id=file_id)
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje, callback_query=None)


def pulsar(chat, datos, telegram_id=7):
    async def editar(texto, reply_markup=None):
        chat.mensajes.append((texto, reply_markup))

    mensaje = _mensaje(chat)
    consulta = SimpleNamespace(
        data=datos, from_user=SimpleNamespace(id=telegram_id), message=mensaje,
        answer=AsyncMock(), edit_message_text=editar,
    )
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje, callback_query=consulta)
