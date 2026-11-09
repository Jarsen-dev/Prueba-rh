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
from bot.borrador import Lote
from bot.config import Settings
from bot.estado import Estados, EstadoUsuario
from bot.interfaz import boton, responder, teclado_confirmar, texto_error
from bot.llm import ErrorLLM
from bot.voz import ClienteVoz

logger = logging.getLogger(__name__)

#: Mensajes (persona + asistente) que se recuerdan entre turnos.
MAX_HISTORIAL = 10
SIN_AGENTE = 'Usa /sistema para el menú con botones, o /ayuda para ver los comandos.'
OCUPADO = 'Sigo con tu mensaje anterior; espera a que conteste.'
SIN_PRESUPUESTO = ('El asistente ya usó su presupuesto de hoy. Mientras tanto puedes usar /sistema, '
                   'que no lo consume.')
SIN_PRESUPUESTO_PERSONA = ('Ya usaste tu cuota diaria del asistente. Puedes seguir con /sistema, '
                           'que no la consume.')
#: Telegram no deja que los bots descarguen archivos de más de 20 MB.
MAX_AUDIO = 20 * 1024 * 1024
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


async def atender(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str, origen: str = 'agente') -> None:
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
        await _turno(update, context, settings, agente, estado, telegram_id, texto, origen)
    finally:
        estado.ocupado = False


async def _puede_gastar(update: Update, backend, settings: Settings, telegram_id: int) -> bool:
    """Vínculo vigente y presupuesto del día; si no, avisa y devuelve False."""
    try:
        await backend.sesion(telegram_id)  # sin vínculo no se gasta nada
        total, persona = await backend.presupuesto(telegram_id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return False
    if total >= settings.BOT_TOKENS_DIA:
        await responder(update, SIN_PRESUPUESTO)
        return False
    if persona >= settings.BOT_TOKENS_PERSONA_DIA:
        await responder(update, SIN_PRESUPUESTO_PERSONA)
        return False
    return True


async def _turno(update, context, settings: Settings, agente: Agente, estado: EstadoUsuario,
                 telegram_id: int, texto: str, origen: str = 'agente') -> None:
    backend = context.application.bot_data['backend']
    if not await _puede_gastar(update, backend, settings, telegram_id):
        return

    try:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    except Exception:  # noqa: BLE001 - es cosmético
        pass

    ahora = datetime.now(ZoneInfo(settings.ZONA_HORARIA))
    try:
        resultado = await agente.responder(telegram_id, texto, list(estado.historial), ahora,
                                           vistos=estado.vistos, adjunto=estado.adjunto, origen=origen)
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
    pendiente = resultado.pendiente
    if pendiente is not None:
        # El mismo pendiente y el mismo botón que el menú: solo al_boton escribe.
        async with estado.candado:
            estado.pendiente = pendiente
        usados = [*(pendiente.cambios if isinstance(pendiente, Lote) else [pendiente]), pendiente.siguiente]
        if estado.adjunto is not None and any(
                p is not None and p.archivo is not None and p.archivo.contenido is estado.adjunto.contenido
                for p in usados):
            estado.adjunto = None
        await responder(update, pendiente.resumen(), teclado_confirmar(pendiente))


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
    estados.de(telegram_id).olvidar_conversacion()


async def guardar_adjunto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Un archivo fuera de un formulario: se guarda para que el agente lo adjunte si se lo piden."""
    from bot.menu import formularios

    if context.application.bot_data.get('agente') is None:
        return False
    mensaje = update.effective_message
    if mensaje.photo:
        foto = mensaje.photo[-1]
        nombre, tamano, file_id = 'foto.jpg', foto.file_size or 0, foto.file_id
    else:
        documento = mensaje.document
        nombre, tamano, file_id = documento.file_name or 'documento', documento.file_size or 0, documento.file_id
    try:
        if tamano > formularios.MAX_ARCHIVO:
            raise ValueError('El archivo no puede pasar de 10 MB.')
        formularios.validar_archivo(nombre, b'')
        descarga = await context.bot.get_file(file_id)
        archivo = formularios.validar_archivo(nombre, bytes(await descarga.download_as_bytearray()))
    except ValueError as error:
        await responder(update, f'⚠️ {error}')
        return True
    context.application.bot_data['estados'].de(update.effective_user.id).adjunto = archivo
    await responder(update, f'📎 Recibí «{nombre}». Dime qué hago con él, por ejemplo:\n'
                            '• «es el acta de la suspensión de Bruno del 12 al 13 de noviembre por faltas»\n'
                            '• «actualiza los datos de Bruno con esta constancia fiscal» (leo el documento y te '
                            'muestro el antes y el después)')
    return True


# --- Notas de voz ----------------------------------------------------------------------

SIN_VOZ = 'Las notas de voz no están activas. Escríbeme el mensaje o usa /sistema.'
NO_SE_ENTENDIO = 'No entendí la nota de voz. ¿Me la repites o me lo escribes?'


async def transcribir_nota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Transcribe la nota de voz (con vínculo, presupuesto y traza). None si no hay texto que atender."""
    datos = context.application.bot_data
    settings: Settings = datos['settings']
    voz: ClienteVoz | None = datos.get('voz')
    if voz is None:
        await responder(update, SIN_VOZ)
        return None
    nota = update.effective_message.voice or update.effective_message.audio
    if (nota.duration or 0) > settings.BOT_VOZ_MAX_SEGUNDOS:
        await responder(update, f'La nota de voz es muy larga: el máximo es de {settings.BOT_VOZ_MAX_SEGUNDOS} '
                                'segundos. Mándala en partes o escríbela.')
        return None
    if (nota.file_size or 0) > MAX_AUDIO:
        await responder(update, 'El audio pesa demasiado (máximo 20 MB).')
        return None
    telegram_id = update.effective_user.id
    estado = datos['estados'].de(telegram_id)
    if estado.ocupado:
        await responder(update, OCUPADO)
        return None
    estado.ocupado = True
    try:
        backend = datos['backend']
        if not await _puede_gastar(update, backend, settings, telegram_id):
            return None
        try:
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001 - es cosmético
            pass
        descarga = await context.bot.get_file(nota.file_id)
        audio = bytes(await descarga.download_as_bytearray())
        nombre = getattr(nota, 'file_name', None) or 'voz.ogg'
        tipo = getattr(nota, 'mime_type', None) or 'audio/ogg'
        try:
            transcripcion = await voz.transcribir(audio, nombre, tipo)
        except ErrorLLM as error:
            logger.warning('Falla al transcribir: %s', error)
            await responder(update, FALLA_LLM)
            return None
        try:
            await backend.registrar_traza(telegram_id, {
                'modelo': transcripcion.modelo, 'tokens_entrada': transcripcion.tokens_entrada,
                'tokens_salida': transcripcion.tokens_salida, 'tokens_cache': 0, 'rondas': 0,
                'herramientas': ['transcribir_voz'], 'segundos': round(transcripcion.segundos, 2), 'resultado': 'ok',
            })
        except Exception as error:  # noqa: BLE001 - la traza nunca tumba la respuesta
            logger.warning('No se pudo registrar la traza de voz: %s', error)
    finally:
        estado.ocupado = False
    if not transcripcion.texto:
        await responder(update, NO_SE_ENTENDIO)
        return None
    await responder(update, f'🎙 Entendí: «{transcripcion.texto}»')
    return transcripcion.texto
