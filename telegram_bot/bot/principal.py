"""Handlers del bot: vinculación, consultas y cambios con botón Confirmar.

Todo actúa como la persona vinculada (``ClienteBackend.peticion``); el bot no
decide permisos. Solo atiende chats privados.
"""

import logging
import sys
from typing import Any

import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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

from bot.backend import ClienteBackend, ErrorBackend, NoVinculado
from bot.borrador import CambioPendiente
from bot.config import Settings, obtener_settings
from bot.estado import Estados

logger = logging.getLogger(__name__)

MAX_MENSAJE = 4096
PRIVADO = filters.ChatType.PRIVATE

TEXTO_NO_VINCULADO = (
    'Este Telegram no está vinculado a una cuenta del sistema de RH.\n\n'
    'En el panel abre «Telegram», genera un código y envíalo aquí con:\n'
    '/vincular CÓDIGO\n\n'
    'O abre directamente el enlace que te muestra el panel.'
)

TEXTO_AYUDA = (
    'Comandos disponibles:\n'
    '/consultar <número o nombre> — datos básicos de un empleado\n'
    '/eliminar <número> — eliminar un empleado (pide confirmación doble)\n'
    '/cancelar — descarta el cambio pendiente\n'
    '/yo — tu cuenta y permisos\n'
    '/desvincular — desconecta este Telegram de tu cuenta\n'
    '/ayuda — esta lista\n\n'
    'Lo que puedes ver y hacer depende de los permisos de tu cuenta en el panel.'
)

COMANDOS = [
    BotCommand('consultar', 'Consultar un empleado'),
    BotCommand('eliminar', 'Eliminar un empleado'),
    BotCommand('cancelar', 'Descartar el cambio pendiente'),
    BotCommand('yo', 'Mi cuenta y permisos'),
    BotCommand('desvincular', 'Desconectar este Telegram'),
    BotCommand('ayuda', 'Ayuda'),
]


# --- Funciones puras ---------------------------------------------------------

def formatear_empleado(empleado: dict[str, Any]) -> str:
    def dato(clave: str, defecto: str = 'Sin dato') -> str:
        valor = empleado.get(clave)
        return str(valor).strip() if valor not in (None, '') else defecto

    return '\n'.join((
        f'👤 {dato("nombre_completo", "Sin nombre")}',
        f'Número: {dato("no_empleado", "Sin asignar")}',
        f'Empresa: {dato("empresa")}',
        f'Puesto: {dato("puesto")}',
        f'Área: {dato("area")}',
        f'Turno: {dato("turno")}',
        f'Estatus: {dato("estatus")}',
    ))


def parametros_busqueda(texto: str) -> dict[str, str] | None:
    """Número de empleado exacto si son solo dígitos; si no, parte del nombre."""
    texto = ' '.join(texto.split())
    if not texto:
        return None
    if texto.isdigit():
        return {'no_empleado': texto}
    if len(texto) < 3:
        return None
    return {'q': texto}


def texto_error(error: Exception) -> str:
    if isinstance(error, NoVinculado):
        return TEXTO_NO_VINCULADO
    if isinstance(error, ErrorBackend):
        if error.status == 401:
            return ('Tu sesión ya no es válida: desvincularon este Telegram o cambió la '
                    'contraseña de tu cuenta. Vuelve a vincularlo desde el panel.')
        if error.status == 403:
            return '🚫 Tu cuenta no tiene permiso para esta acción.'
        if error.status == 503 and error.codigo == 'BOT_APAGADO':
            return 'El bot está desactivado en el sistema.'
        return f'⚠️ {error.mensaje}'
    return '⚠️ Ocurrió un error inesperado. Intenta de nuevo.'


def teclado_confirmar(pendiente: CambioPendiente) -> InlineKeyboardMarkup:
    peligro = pendiente.metodo == 'DELETE'
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('🗑 Sí, eliminar' if peligro else '✅ Confirmar', callback_data=f'ok:{pendiente.id}'),
        InlineKeyboardButton('✖️ Cancelar', callback_data=f'no:{pendiente.id}'),
    ]])


def partir(texto: str, tope: int = MAX_MENSAJE) -> list[str]:
    partes, actual = [], ''
    for linea in texto.split('\n'):
        while len(linea) > tope:
            if actual:
                partes.append(actual)
                actual = ''
            partes.append(linea[:tope])
            linea = linea[tope:]
        candidato = f'{actual}\n{linea}' if actual else linea
        if len(candidato) > tope:
            partes.append(actual)
            actual = linea
        else:
            actual = candidato
    if actual:
        partes.append(actual)
    return partes or ['']


# --- Ayudantes de contexto ---------------------------------------------------

def _backend(context: ContextTypes.DEFAULT_TYPE) -> ClienteBackend:
    return context.application.bot_data['backend']


def _estados(context: ContextTypes.DEFAULT_TYPE) -> Estados:
    return context.application.bot_data['estados']


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data['settings']


async def responder(update: Update, texto: str, reply_markup=None) -> None:
    mensaje = update.effective_message
    if mensaje is None:
        return
    partes = partir(texto)
    for i, parte in enumerate(partes):
        await mensaje.reply_text(parte, reply_markup=reply_markup if i == len(partes) - 1 else None)


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

    empleado = empleados[0]
    pendiente = CambioPendiente(
        metodo='DELETE',
        ruta=f'/api/eliminar-empleado/{int(empleado["registro_id"])}',
        titulo='🗑 Eliminar definitivamente este empleado y todos sus datos:',
        detalle=[
            f'Nombre: {empleado.get("nombre_completo") or "Sin nombre"}',
            f'Número: {empleado.get("no_empleado")}',
            f'Empresa: {empleado.get("empresa") or "Sin dato"}',
            f'Puesto: {empleado.get("puesto") or "Sin dato"}',
        ],
        origen='menu',
    )
    estado = _estados(context).de(telegram_id)
    async with estado.candado:
        estado.pendiente = pendiente
    await responder(update, pendiente.resumen(), reply_markup=teclado_confirmar(pendiente))


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    estado = _estados(context).de(update.effective_user.id)
    async with estado.candado:
        habia = estado.pendiente is not None
        estado.pendiente = None
    await responder(update, 'Cambio pendiente descartado.' if habia else 'No tenías ningún cambio pendiente.')


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
            await consulta.edit_message_text(f'{pendiente.resumen()}\n\n✖️ Cancelado.')
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
            json=pendiente.cuerpo,
            headers={'X-Origen-Telegram': pendiente.origen},
        )
    except ErrorBackend as error:
        await consulta.edit_message_text(f'{pendiente.resumen()}\n\n{texto_error(error)}')
        return
    except httpx.TransportError:
        # Sin respuesta: quizá sí se aplicó. Nunca se reintenta solo.
        logger.warning('Sin respuesta al aplicar %s %s', pendiente.metodo, pendiente.ruta)
        await consulta.edit_message_text(
            f'{pendiente.resumen()}\n\n❓ El sistema no respondió; es posible que el cambio sí se haya '
            'aplicado. Revísalo en el panel antes de intentarlo de nuevo.'
        )
        return
    await consulta.edit_message_text(f'{pendiente.resumen()}\n\n✅ Hecho.')


async def al_texto(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    await responder(update, 'Por ahora atiendo comandos. Escribe /ayuda para ver la lista.')


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
    }
    for nombre, handler in comandos.items():
        app.add_handler(CommandHandler(nombre, handler, filters=PRIVADO))
    app.add_handler(CallbackQueryHandler(al_boton, pattern=r'^(ok|no):[0-9a-f]{8}$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & PRIVADO, al_texto))
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
