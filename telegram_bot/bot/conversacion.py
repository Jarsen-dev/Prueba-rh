"""Texto libre -> agente. Presupuesto, historial, traza y calificación."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import herramientas
from bot.agente import Agente, Resultado
from bot.backend import ErrorBackend
from bot.config import Settings
from bot.estado import Estados, EstadoUsuario
from bot.interfaz import boton, responder, texto_error
from bot.llm import ErrorLLM

logger = logging.getLogger(__name__)

#: Mensajes (persona + asistente) que se recuerdan entre turnos.
MAX_HISTORIAL = 10
SIN_AGENTE = 'Usa /sistema para el menú con botones, o /ayuda para ver los comandos.'
OCUPADO = 'Sigo con tu mensaje anterior; espera a que conteste.'
SIN_PRESUPUESTO = ('El asistente ya usó su presupuesto de hoy. Mientras tanto puedes usar /sistema, '
                   'que no lo consume.')
SIN_PRESUPUESTO_PERSONA = ('Ya usaste tu cuota diaria del asistente. Puedes seguir con /sistema, '
                           'que no la consume.')
FALLA_LLM = 'El asistente no está disponible en este momento. Puedes usar /sistema mientras tanto.'


def pie(resultado: Resultado) -> str:
    consultas = sum(1 for _ in resultado.peticiones)
    return f'🔹 {resultado.tokens:,} tokens · {consultas} consulta{"s" if consultas != 1 else ""}'.replace(',', ' ')


def teclado_calificar(traza_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[boton('👍', f'cal:{traza_id}:+'), boton('👎', f'cal:{traza_id}:-')]])


def recordar(estado: EstadoUsuario, texto: str, respuesta: str) -> None:
    estado.historial += [{'role': 'user', 'content': texto}, {'role': 'assistant', 'content': respuesta}]
    del estado.historial[:-MAX_HISTORIAL]


def datos_de_traza(resultado: Resultado) -> dict:
    return {
        'modelo': resultado.modelo,
        'tokens_entrada': resultado.tokens_entrada,
        'tokens_salida': resultado.tokens_salida,
        'tokens_cache': resultado.tokens_cache,
        'rondas': resultado.rondas,
        'herramientas': [h if h in herramientas.NOMBRES else 'desconocida' for h in resultado.herramientas][:20],
        'segundos': round(resultado.segundos, 2),
        'resultado': resultado.resultado,
    }


async def atender(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    datos = context.application.bot_data
    settings: Settings = datos['settings']
    agente: Agente | None = datos.get('agente')
    if agente is None:
        await responder(update, SIN_AGENTE)
        return
    telegram_id = update.effective_user.id
    estado = (datos['estados']).de(telegram_id)
    if estado.ocupado:
        await responder(update, OCUPADO)
        return
    estado.ocupado = True
    try:
        await _turno(update, context, settings, agente, estado, telegram_id, texto)
    finally:
        estado.ocupado = False


async def _turno(update, context, settings: Settings, agente: Agente, estado: EstadoUsuario,
                 telegram_id: int, texto: str) -> None:
    backend = context.application.bot_data['backend']
    try:
        await backend.sesion(telegram_id)  # sin vínculo no se gasta nada
        total, persona = await backend.presupuesto(telegram_id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    if total >= settings.BOT_TOKENS_DIA:
        await responder(update, SIN_PRESUPUESTO)
        return
    if persona >= settings.BOT_TOKENS_PERSONA_DIA:
        await responder(update, SIN_PRESUPUESTO_PERSONA)
        return

    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    except Exception:  # noqa: BLE001 - es cosmético
        pass

    ahora = datetime.now(ZoneInfo(settings.ZONA_HORARIA))
    try:
        resultado = await agente.responder(telegram_id, texto, list(estado.historial), ahora)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    except ErrorLLM as error:
        logger.warning('Falla del modelo: %s', error)
        await responder(update, FALLA_LLM)
        return

    for archivo in resultado.archivos:
        await update.effective_message.reply_document(document=archivo.contenido, filename=archivo.nombre)

    traza_id = None
    try:
        traza_id = await backend.registrar_traza(telegram_id, datos_de_traza(resultado))
    except Exception as error:  # noqa: BLE001 - la traza nunca tumba la respuesta
        logger.warning('No se pudo registrar la traza: %s', error)

    recordar(estado, texto, resultado.texto)
    await responder(
        update, f'{resultado.texto}\n\n{pie(resultado)}',
        teclado_calificar(traza_id) if traza_id else None,
    )


async def al_calificar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    _, traza_id, signo = (consulta.data or '').split(':')
    try:
        await context.application.bot_data['backend'].calificar(
            consulta.from_user.id, int(traza_id), 1 if signo == '+' else -1
        )
    except ErrorBackend:
        await consulta.answer('No se pudo guardar la calificación.')
        return
    await consulta.answer('¡Gracias!')
    try:
        await consulta.edit_message_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - el mensaje pudo ser muy viejo
        pass


def olvidar_conversacion(estados: Estados, telegram_id: int) -> None:
    estados.de(telegram_id).historial.clear()
