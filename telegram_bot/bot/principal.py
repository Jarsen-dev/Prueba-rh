"""Handlers del bot: vinculación, consultas y cambios con botón Confirmar.

Todo actúa como la persona vinculada (``ClienteBackend.peticion``); el bot no
decide permisos. Solo atiende chats privados.
"""

import logging
import sys
import time
from typing import Any

import httpx
from telegram import BotCommand, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.backend import ClienteBackend, ErrorBackend
from bot.config import Settings, obtener_settings
from bot.estado import Estados
from bot.interfaz import (  # noqa: F401 - reexportados para quien importe desde principal
    TEXTO_NO_VINCULADO,
    formatear_empleado,
    parametros_busqueda,
    partir,
    responder,
    teclado_confirmar,
    texto_error,
)
from bot import conversacion
from bot.agente import Agente
from bot.llm import ClienteOpenAI
from bot.voz import ClienteVoz
from bot.menu import formularios, pantallas

logger = logging.getLogger(__name__)

PRIVADO = filters.ChatType.PRIVATE

TEXTO_AYUDA = (
    'Comandos disponibles:\n'
    '/sistema — menú con botones (empleados, ingresos, reclutamiento, descargas)\n'
    '/consultar <número o nombre> — datos básicos de un empleado\n'
    '/eliminar <número> — eliminar un empleado (pide confirmación doble)\n'
    '/cancelar — descarta el cambio o formulario pendiente\n'
    '/yo — tu cuenta y permisos\n'
    '/desvincular — desconecta este Telegram de tu cuenta\n'
    '/nuevo — empieza una conversación nueva con el asistente\n'
    '/ayuda — esta lista\n\n'
    'También puedes escribirme, por ejemplo «¿cuántos activos hay en Kronos?», «registra vacaciones a Ana del '
    '16 al 20 de noviembre» o «da de baja a Bruno Castillo». Los cambios siempre te los muestro antes con un '
    'botón Confirmar. '
    'También entiendo notas de voz: te muestro lo que entendí antes de contestar.\n\n'
    'Lo que puedes ver y hacer depende de los permisos de tu cuenta en el panel. '
    'Nada se guarda sin que pulses Confirmar.'
)

COMANDOS = [
    BotCommand('sistema', 'Menú con botones'),
    BotCommand('consultar', 'Consultar un empleado'),
    BotCommand('eliminar', 'Eliminar un empleado'),
    BotCommand('cancelar', 'Descartar lo pendiente'),
    BotCommand('nuevo', 'Conversación nueva con el asistente'),
    BotCommand('yo', 'Mi cuenta y permisos'),
    BotCommand('desvincular', 'Desconectar este Telegram'),
    BotCommand('ayuda', 'Ayuda'),
]


# --- Ayudantes de contexto ---------------------------------------------------

def _backend(context: ContextTypes.DEFAULT_TYPE) -> ClienteBackend:
    return context.application.bot_data['backend']


def _estados(context: ContextTypes.DEFAULT_TYPE) -> Estados:
    return context.application.bot_data['estados']


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data['settings']


# --- Vinculación ---------------------------------------------------------------

async def _vincular(update: Update, context: ContextTypes.DEFAULT_TYPE, codigo: str) -> None:
    usuario = update.effective_user
    try:
        sesion = await _backend(context).vincular(codigo, usuario.id, usuario.username)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    nombre = sesion.usuario.get('usuario', '')
    permisos = ', '.join(sesion.permisos) or 'ninguno'
    await responder(update, f'✅ Listo, este Telegram quedó vinculado a la cuenta «{nombre}» (permisos: {permisos}).\n\n{TEXTO_AYUDA}')


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        await _vincular(update, context, context.args[0])
        return
    try:
        sesion = await _backend(context).sesion(update.effective_user.id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    await responder(update, f'Hola, {sesion.usuario.get("usuario", "")}.\n\n{TEXTO_AYUDA}')


async def cmd_vincular(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await responder(update, 'Uso: /vincular CÓDIGO (lo generas en el panel, en «Telegram»).')
        return
    await _vincular(update, context, ''.join(context.args))


async def cmd_desvincular(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    _estados(context).olvidar(telegram_id)
    try:
        desvinculado = await _backend(context).desvincular(telegram_id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    await responder(update, '🔌 Este Telegram quedó desvinculado.' if desvinculado else 'Este Telegram no estaba vinculado.')


async def cmd_yo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        sesion = await _backend(context).sesion(update.effective_user.id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    await responder(update, f'Cuenta: {sesion.usuario.get("usuario")}\nPermisos: {", ".join(sesion.permisos) or "ninguno"}')


async def cmd_ayuda(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await responder(update, TEXTO_AYUDA)


# --- Consultas ---------------------------------------------------------------------

async def _buscar(context, telegram_id: int, params: dict[str, str]) -> list[dict[str, Any]]:
    respuesta = await _backend(context).peticion(telegram_id, 'GET', '/api/empleados/buscar', params=params)
    datos = respuesta.datos if isinstance(respuesta.datos, dict) else {}
    return list(datos.get('empleados') or [])


async def cmd_consultar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    params = parametros_busqueda(' '.join(context.args or []))
    if params is None:
        await responder(update, 'Uso: /consultar <número de empleado> o /consultar <al menos 3 letras del nombre>')
        return
    try:
        empleados = await _buscar(context, update.effective_user.id, params)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    if not empleados:
        await responder(update, 'No encontré empleados con ese dato.')
        return
    encabezado = f'Encontré {len(empleados)} resultado(s):\n\n' if len(empleados) > 1 else ''
    await responder(update, encabezado + '\n\n'.join(formatear_empleado(e) for e in empleados))


# --- Cambios con confirmación ------------------------------------------------------

async def cmd_eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    numero = ' '.join(context.args or []).strip()
    if not numero.isdigit():
        await responder(update, 'Uso: /eliminar <número de empleado>')
        return
    try:
        sesion = await _backend(context).sesion(telegram_id)
        if 'admin' not in sesion.permisos:
            # Solo para avisar pronto; quien decide es el backend al confirmar.
            await responder(update, '🚫 Tu cuenta no tiene permiso para eliminar empleados.')
            return
        empleados = await _buscar(context, telegram_id, {'no_empleado': numero})
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    if not empleados:
        await responder(update, 'No encontré ese número de empleado.')
        return
    if len(empleados) > 1:
        await responder(update, 'Ese número coincide con más de un registro; no hice nada. Revísalo en el panel.')
        return

    pendiente = formularios.pendiente_eliminar(empleados[0])
    estado = _estados(context).de(telegram_id)
    async with estado.candado:
        estado.pendiente = pendiente
    await responder(update, pendiente.resumen(), reply_markup=teclado_confirmar(pendiente))


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = _estados(context).de(update.effective_user.id)
    async with estado.candado:
        habia = estado.pendiente is not None or estado.asistente is not None
        estado.pendiente = None
    estado.limpiar_menu()
    await responder(update, 'Descartado.' if habia else 'No tenías nada pendiente.')


async def cmd_nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conversacion.olvidar_conversacion(_estados(context), update.effective_user.id)
    await responder(update, 'Listo, empezamos de cero.')


async def al_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    if consulta.message is None or consulta.message.chat.type != ChatType.PRIVATE:
        return
    accion, _, pendiente_id = (consulta.data or '').partition(':')
    telegram_id = consulta.from_user.id  # quien PULSÓ, no quien pidió
    estado = _estados(context).de(telegram_id)

    async with estado.candado:
        pendiente = estado.pendiente_vigente(_settings(context).BOT_MINUTOS_PENDIENTE)
        if pendiente is None or pendiente.id != pendiente_id:
            await consulta.edit_message_text('Este cambio ya no está pendiente.')
            return
        if accion == 'no':
            estado.pendiente = None
            await consulta.edit_message_text(f'{pendiente.resumen(final=True)}\n\n✖️ Cancelado.')
            return

        pendiente.confirmaciones_faltantes -= 1
        if pendiente.confirmaciones_faltantes > 0:
            await consulta.edit_message_text(
                f'{pendiente.resumen()}\n\n¿Seguro? Pulsa otra vez para confirmar.',
                reply_markup=teclado_confirmar(pendiente),
            )
            return
        # Se quita antes de ejecutar: un doble clic no repite el cambio.
        estado.pendiente = None

    try:
        await _backend(context).peticion(
            telegram_id,
            pendiente.metodo,
            pendiente.ruta,
            headers={'X-Origen-Telegram': pendiente.origen},
            **pendiente.argumentos_peticion(),
        )
    except ErrorBackend as error:
        await consulta.edit_message_text(f'{pendiente.resumen(final=True)}\n\n{texto_error(error)}')
        return
    except httpx.TransportError:
        # Sin respuesta: quizá sí se aplicó. Nunca se reintenta solo.
        logger.warning('Sin respuesta al aplicar %s %s', pendiente.metodo, pendiente.ruta)
        await consulta.edit_message_text(
            f'{pendiente.resumen(final=True)}\n\n❓ El sistema no respondió; es posible que el cambio sí se haya '
            'aplicado. Revísalo en el panel antes de intentarlo de nuevo.'
        )
        return
    await consulta.edit_message_text(f'{pendiente.resumen(final=True)}\n\n✅ Hecho.')
    siguiente = pendiente.siguiente
    if siguiente is not None:
        siguiente.creado = time.monotonic()   # su propio plazo para confirmar
        async with estado.candado:
            estado.pendiente = siguiente
        await consulta.message.reply_text(siguiente.resumen(), reply_markup=teclado_confirmar(siguiente))


async def al_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = update.effective_message.text or ''
    if await pantallas.atender_texto(update, context, texto):
        return
    await conversacion.atender(update, context, texto)


async def al_archivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await pantallas.atender_archivo(update, context):
        return
    if await conversacion.guardar_adjunto(update, context):
        return
    await responder(update, 'No esperaba un archivo. Para adjuntar un documento, ábrelo desde el formulario en /sistema.')


async def al_voz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Una nota de voz se transcribe y sigue el mismo camino que un texto (formulario abierto o agente)."""
    texto = await conversacion.transcribir_nota(update, context)
    if texto is None:
        return
    if await pantallas.atender_texto(update, context, texto):
        return
    await conversacion.atender(update, context, texto, origen='voz')


async def al_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error('Error atendiendo una actualización', exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(texto_error(context.error))
        except Exception:  # noqa: BLE001 - el aviso de error nunca debe tumbar el bot
            pass


# --- Arranque ------------------------------------------------------------------------

async def al_iniciar(app: Application) -> None:
    settings: Settings = app.bot_data['settings']
    http = httpx.AsyncClient()
    app.bot_data['http'] = http
    app.bot_data['backend'] = ClienteBackend(
        base_url=settings.BACKEND_URL, secreto=settings.TELEGRAM_BOT_SECRETO_INTERNO, http=http
    )
    app.bot_data['estados'] = Estados()
    if settings.agente_activo:
        app.bot_data['agente'] = Agente(
            llm=ClienteOpenAI(api_key=settings.OPENAI_API_KEY, modelo=settings.OPENAI_MODEL,
                              base_url=settings.OPENAI_BASE_URL, http=http),
            backend=app.bot_data['backend'],
            enmascarar=settings.BOT_ENMASCARAR_DATOS,
        )
        logger.info('Agente activo con %s.', settings.OPENAI_MODEL)
        if settings.voz_activa:
            app.bot_data['voz'] = ClienteVoz(api_key=settings.OPENAI_API_KEY, modelo=settings.OPENAI_MODELO_VOZ,
                                             base_url=settings.OPENAI_BASE_URL, http=http)
    else:
        logger.info('Sin OPENAI_API_KEY: solo menú y comandos.')
    await app.bot.set_my_commands(COMANDOS)


async def al_terminar(app: Application) -> None:
    http = app.bot_data.get('http')
    if http is not None:
        await http.aclose()


def construir(settings: Settings) -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_init(al_iniciar)
        .post_shutdown(al_terminar)
        .build()
    )
    app.bot_data['settings'] = settings
    comandos = {
        'start': cmd_start,
        'vincular': cmd_vincular,
        'desvincular': cmd_desvincular,
        'yo': cmd_yo,
        'ayuda': cmd_ayuda,
        'help': cmd_ayuda,
        'consultar': cmd_consultar,
        'eliminar': cmd_eliminar,
        'cancelar': cmd_cancelar,
        'nuevo': cmd_nuevo,
        'sistema': pantallas.cmd_sistema,
        'menu': pantallas.cmd_sistema,
    }
    for nombre, handler in comandos.items():
        app.add_handler(CommandHandler(nombre, handler, filters=PRIVADO))
    app.add_handler(CallbackQueryHandler(al_boton, pattern=r'^(ok|no):[0-9a-f]{8}$'))
    app.add_handler(CallbackQueryHandler(pantallas.al_boton_nodo, pattern=r'^m:[a-z_]{1,30}$'))
    app.add_handler(CallbackQueryHandler(pantallas.al_boton_empleado, pattern=r'^e:\d{1,3}$'))
    app.add_handler(CallbackQueryHandler(pantallas.al_boton_accion_empleado, pattern=r'^ea:[a-z]{2,5}$'))
    app.add_handler(CallbackQueryHandler(pantallas.al_boton_candidato, pattern=r'^cp?:\d{1,4}$'))
    app.add_handler(CallbackQueryHandler(pantallas.al_boton_asistente, pattern=r'^a:(\d{1,3}|[bsx])$'))
    app.add_handler(CallbackQueryHandler(conversacion.al_calificar, pattern=r'^cal:\d{1,12}:[+-]$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & PRIVADO, al_texto))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & PRIVADO, al_archivo))
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO) & PRIVADO, al_voz))
    app.add_error_handler(al_error)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    # httpx registra a INFO cada URL, y la del bot lleva el token de Telegram.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    settings = obtener_settings()
    problemas = settings.problemas()
    if problemas:
        for problema in problemas:
            logger.error(problema)
        sys.exit(1)
    construir(settings).run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
