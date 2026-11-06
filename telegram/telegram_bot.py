import asyncio
import logging
import os
import re
import time
from pathlib import Path
from uuid import uuid4

import psycopg2
from dotenv import dotenv_values
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / '.env'
config = {**dotenv_values(ENV_PATH), **os.environ}
logger = logging.getLogger(__name__)

token = config.get('TELEGRAM_BOT_TOKEN')
if not token:
    raise RuntimeError(f'No se pudo extraer TELEGRAM_BOT_TOKEN del archivo en: {ENV_PATH}')


def parse_admin_ids(value):
    admin_ids = set()
    for item in (value or '').split(','):
        item = item.strip()
        if not item:
            continue
        if not re.fullmatch(r'\d+', item):
            raise RuntimeError('TELEGRAM_ADMIN_IDS debe contener IDs numéricos separados por comas.')
        admin_ids.add(int(item))
    return admin_ids


ADMIN_IDS = parse_admin_ids(config.get('TELEGRAM_ADMIN_IDS'))
if not ADMIN_IDS:
    logger.warning(
        'TELEGRAM_ADMIN_IDS no está configurado; los comandos administrativos quedan bloqueados.'
    )

pending_deletions = {}
CONFIRMATION_TTL_SECONDS = 300


def es_administrador(user_id):
    return user_id is not None and user_id in ADMIN_IDS


def limpiar_confirmaciones_expiradas():
    ahora = time.monotonic()
    expiradas = [
        confirmation_id
        for confirmation_id, pendiente in pending_deletions.items()
        if pendiente['expires_at'] <= ahora
    ]
    for confirmation_id in expiradas:
        pending_deletions.pop(confirmation_id, None)


def get_db_connection():
    host = config.get('POSTGRES_HOST') or 'localhost'
    port = config.get('POSTGRES_PORT') or '5432'
    if host == 'postgres' and not Path('/.dockerenv').exists():
        host = '127.0.0.1'
        port = config.get('TELEGRAM_POSTGRES_PORT') or '5434'

    password = config.get('POSTGRES_PASSWORD')
    if not password:
        raise RuntimeError('Falta configurar POSTGRES_PASSWORD para el bot de Telegram.')

    return psycopg2.connect(
        host=host,
        port=port,
        database=config.get('POSTGRES_DB') or 'rh_system',
        user=config.get('POSTGRES_USER') or 'rh_app',
        password=password,
        options='-c lc_messages=C',
    )


def buscar_empleado(no_empleado):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.nombre_completo, ip.no_empleado,
                       ip.puesto, ip.area, ip.turno
                FROM registro AS r
                JOIN ingresos_puesto AS ip ON ip.registro_id = r.id
                WHERE BTRIM(ip.no_empleado::text) = %s
                ORDER BY ip.id DESC
                """,
                (no_empleado,),
            )
            filas = cur.fetchall()

    empleados = {fila[0]: fila for fila in filas}
    if len(empleados) > 1:
        return None, 'El número de empleado coincide con más de un registro; no se realizó ninguna acción.'
    if not empleados:
        return None, 'No se encontró ese número de empleado.'
    return next(iter(empleados.values())), None


def eliminar_registro(registro_id, no_empleado):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM registro AS r
                WHERE r.id = %s
                  AND EXISTS (
                      SELECT 1
                      FROM ingresos_puesto AS ip
                      WHERE ip.registro_id = r.id
                        AND BTRIM(ip.no_empleado::text) = %s
                  )
                RETURNING r.id
                """,
                (registro_id, no_empleado),
            )
            eliminado = cur.fetchone()
        conn.commit()
    return bool(eliminado)


async def responder(update, texto, reply_markup=None):
    if update.effective_message:
        await update.effective_message.reply_text(texto, reply_markup=reply_markup)


async def autorizar_chat_privado(update):
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        await responder(update, 'Por seguridad, usa estos comandos en un chat privado con el bot.')
        return False
    if not es_administrador(update.effective_user.id if update.effective_user else None):
        await responder(update, 'No tienes autorización para usar los comandos administrativos.')
        return False
    return True


async def handle_start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not await autorizar_chat_privado(update):
        return
    await responder(
        update,
        'Comandos administrativos:\n'
        '/consultar <no_empleado> - consulta puesto y datos básicos\n'
        '/eliminar <no_empleado> - solicita confirmación antes del borrado definitivo\n'
        '/cancelar_eliminar - cancela tus confirmaciones pendientes',
    )


async def handle_consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await autorizar_chat_privado(update):
        return
    if len(context.args) != 1 or not context.args[0].strip():
        await responder(update, 'Uso: /consultar <no_empleado>')
        return

    try:
        empleado, error = await asyncio.to_thread(buscar_empleado, context.args[0].strip())
    except (psycopg2.Error, RuntimeError):
        logger.exception('No se pudo consultar un empleado desde Telegram.')
        await responder(update, 'No fue posible consultar la base de datos.')
        return

    if error:
        await responder(update, error)
        return

    await responder(
        update,
        '\n'.join((
            f'Número: {empleado[2] or "Sin asignar"}',
            f'Nombre: {empleado[1] or "Sin nombre"}',
            f'Puesto: {empleado[3] or "Sin dato"}',
            f'Área: {empleado[4] or "Sin dato"}',
            f'Turno: {empleado[5] or "Sin dato"}',
        )),
    )


async def handle_eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await autorizar_chat_privado(update):
        return
    limpiar_confirmaciones_expiradas()
    if str(config.get('DB_WRITE_LOCK') or '0').strip() == '1':
        await responder(update, 'La base de datos está bloqueada para cambios; no se realizó ninguna acción.')
        return
    if len(context.args) != 1 or not context.args[0].strip():
        await responder(update, 'Uso: /eliminar <no_empleado>')
        return

    no_empleado = context.args[0].strip()
    try:
        empleado, error = await asyncio.to_thread(buscar_empleado, no_empleado)
    except (psycopg2.Error, RuntimeError):
        logger.exception('No se pudo preparar una eliminación desde Telegram.')
        await responder(update, 'No fue posible consultar la base de datos.')
        return

    if error:
        await responder(update, error)
        return

    confirmation_id = uuid4().hex
    pending_deletions[confirmation_id] = {
        'admin_id': update.effective_user.id,
        'registro_id': empleado[0],
        'no_empleado': no_empleado,
        'expires_at': time.monotonic() + CONFIRMATION_TTL_SECONDS,
    }

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            'Sí, eliminar definitivamente',
            callback_data=f'delete:{confirmation_id}',
        ),
        InlineKeyboardButton('Cancelar', callback_data=f'cancel:{confirmation_id}'),
    ]])
    await responder(
        update,
        (
            f'Confirma el borrado definitivo del registro de {empleado[1] or "este empleado"} '
            f'(número {empleado[2]})?\n'
            'Se eliminarán los datos y documentos relacionados con ese registro. '
            'Esta acción no se puede deshacer.'
        ),
        reply_markup=keyboard,
    )


async def handle_cancelar_eliminar(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not await autorizar_chat_privado(update):
        return
    limpiar_confirmaciones_expiradas()
    user_id = update.effective_user.id
    canceladas = [
        confirmation_id
        for confirmation_id, pendiente in pending_deletions.items()
        if pendiente['admin_id'] == user_id
    ]
    for confirmation_id in canceladas:
        pending_deletions.pop(confirmation_id, None)
    await responder(update, 'Se cancelaron tus confirmaciones pendientes.')


async def handle_confirmacion_eliminacion(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if (
        not update.effective_chat
        or update.effective_chat.type != ChatType.PRIVATE
        or not es_administrador(update.effective_user.id if update.effective_user else None)
    ):
        await query.edit_message_text('Esta confirmación no está disponible.')
        return

    accion, _, confirmation_id = (query.data or '').partition(':')
    pendiente = pending_deletions.get(confirmation_id)
    if not pendiente or pendiente['admin_id'] != update.effective_user.id:
        await query.edit_message_text('Esta confirmación no está disponible.')
        return
    if pendiente['expires_at'] <= time.monotonic():
        pending_deletions.pop(confirmation_id, None)
        await query.edit_message_text('La solicitud expiró. Usa /eliminar para iniciar otra.')
        return
    if accion == 'cancel':
        pending_deletions.pop(confirmation_id, None)
        await query.edit_message_text('No se eliminó ningún registro.')
        return
    if accion != 'delete':
        await query.edit_message_text('Esta confirmación no está disponible.')
        return
    if str(config.get('DB_WRITE_LOCK') or '0').strip() == '1':
        pending_deletions.pop(confirmation_id, None)
        await query.edit_message_text(
            'La base de datos está bloqueada; no se eliminó ningún registro.'
        )
        return

    try:
        eliminado = await asyncio.to_thread(
            eliminar_registro,
            pendiente['registro_id'],
            pendiente['no_empleado'],
        )
    except (psycopg2.Error, RuntimeError):
        logger.exception('No se pudo completar una eliminación desde Telegram.')
        await query.edit_message_text(
            'No fue posible completar el borrado. Consulta el estado actual antes de reintentar.'
        )
        return

    pending_deletions.pop(confirmation_id, None)
    if eliminado:
        await query.edit_message_text(
            f'Se eliminó el registro con número de empleado {pendiente["no_empleado"]}.'
        )
    else:
        await query.edit_message_text(
            'No se eliminó ningún registro; consulta su estado actual.'
        )


async def handle_text(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await responder(update, 'Usa /ayuda para consultar los comandos disponibles.')


def crear_aplicacion():
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler(['start', 'ayuda'], handle_start))
    application.add_handler(CommandHandler('consultar', handle_consultar))
    application.add_handler(CommandHandler('eliminar', handle_eliminar))
    application.add_handler(CommandHandler('cancelar_eliminar', handle_cancelar_eliminar))
    application.add_handler(
        CallbackQueryHandler(
            handle_confirmacion_eliminacion,
            pattern=r'^(delete|cancel):[a-f0-9]{32}$',
        )
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    return application


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info('Bot de Telegram encendido.')
    crear_aplicacion().run_polling()
