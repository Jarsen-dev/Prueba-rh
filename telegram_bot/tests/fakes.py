"""Dobles de Telegram y del backend para probar handlers sin red."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.constants import ChatType

from bot.backend import ErrorBackend, RespuestaBackend, Sesion
from bot.config import Settings
from bot.estado import Estados


#: La guía real (la genera el backend; test_guia.py comprueba que esté al día).
GUIA_REAL = json.loads((Path(__file__).parent / 'guia.json').read_text(encoding='utf-8'))


class FakeBackend:
    """Responde según (método, ruta) y registra cada llamada."""

    def __init__(self, permisos=('admin',), rutas=None, guia=None, presupuesto=(0, 0)):
        self.permisos = list(permisos)
        self.rutas = rutas or {}
        self.llamadas = []
        self._guia = guia or GUIA_REAL
        self._presupuesto = presupuesto
        self.trazas = []
        self.calificaciones = []

    async def guia(self):
        return self._guia

    async def presupuesto(self, telegram_id):
        return self._presupuesto

    async def registrar_traza(self, telegram_id, traza):
        self.trazas.append(traza)
        return len(self.trazas)

    async def calificar(self, telegram_id, traza_id, valor):
        self.calificaciones.append((telegram_id, traza_id, valor))

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


class FakeLLM:
    """Modelo con guion: cada elemento es texto final o una lista de (herramienta, argumentos)."""

    def __init__(self, guion, modelo='gpt-4o-mini'):
        self.guion = list(guion)
        self.modelo = modelo
        self.recibidos = []
        self.herramientas_recibidas = []

    async def completar(self, mensajes, *, herramientas=None, max_tokens=1200, clave_cache=None):
        from bot.llm import LlamadaHerramienta, RespuestaLLM
        self.recibidos.append([dict(m) for m in mensajes])
        self.herramientas_recibidas.append(herramientas or [])
        paso = self.guion.pop(0)
        if isinstance(paso, Exception):
            raise paso
        if isinstance(paso, str):
            return RespuestaLLM(self.modelo, paso, [], {'role': 'assistant', 'content': paso}, 100, 10)
        llamadas = [LlamadaHerramienta(f'c{i}', n, a) for i, (n, a) in enumerate(paso)]
        mensaje = {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': l.id, 'type': 'function', 'function': {'name': l.nombre, 'arguments': '{}'}} for l in llamadas]}
        return RespuestaLLM(self.modelo, '', llamadas, mensaje, 100, 10)


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
        chat=SimpleNamespace(type=ChatType.PRIVATE), photo=None, document=None, text=None, voice=None, audio=None,
        **extra,
    )


def texto(chat, contenido, telegram_id=7):
    mensaje = _mensaje(chat)
    mensaje.text = contenido
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje, callback_query=None)


def archivo(chat, nombre, file_id='f1', tamano=100, telegram_id=7):
    mensaje = _mensaje(chat)
    mensaje.document = SimpleNamespace(file_name=nombre, file_size=tamano, file_id=file_id)
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje, callback_query=None)


def nota_de_voz(chat, segundos=5, file_id='v1', tamano=1000, telegram_id=7):
    mensaje = _mensaje(chat)
    mensaje.voice = SimpleNamespace(duration=segundos, file_size=tamano, file_id=file_id, mime_type='audio/ogg')
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje,
                           effective_chat=SimpleNamespace(id=telegram_id), callback_query=None)


class FakeVoz:
    """Transcribe siempre lo mismo y anota lo que recibió."""

    def __init__(self, texto, error=None):
        self.texto, self.error, self.recibidos, self.modelo = texto, error, [], 'gpt-4o-mini-transcribe'

    async def transcribir(self, audio, nombre='voz.ogg', tipo='audio/ogg'):
        from bot.voz import Transcripcion
        self.recibidos.append((audio, nombre, tipo))
        if self.error:
            raise self.error
        return Transcripcion(' '.join(self.texto.split()), self.modelo, 120, 15, 0.4)


def pulsar(chat, datos, telegram_id=7):
    async def editar(texto, reply_markup=None):
        chat.mensajes.append((texto, reply_markup))

    mensaje = _mensaje(chat)
    consulta = SimpleNamespace(
        data=datos, from_user=SimpleNamespace(id=telegram_id), message=mensaje,
        answer=AsyncMock(), edit_message_text=editar,
    )
    return SimpleNamespace(effective_user=SimpleNamespace(id=telegram_id), effective_message=mensaje, callback_query=consulta)
