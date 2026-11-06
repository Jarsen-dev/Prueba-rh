import logging
import os
import re
import time
from pathlib import Path
from uuid import uuid4

import psycopg2
import telebot
from dotenv import dotenv_values
from telebot import types


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / '.env'
config = {**dotenv_values(ENV_PATH), **os.environ}

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
    logging.getLogger(__name__).warning(
        'TELEGRAM_ADMIN_IDS no está configurado; los comandos administrativos quedan bloqueados.'
    )

bot = telebot.TeleBot(token)
pending_deletions = {}
CONFIRMATION_TTL_SECONDS = 300


def es_administrador(user_id):
    return user_id is not None and user_id in ADMIN_IDS


def es_chat_privado(message):
    return bool(message.chat and message.chat.type == 'private')


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


def responder_no_autorizado(message):
    bot.reply_to(message, 'No tienes autorización para usar los comandos administrativos.')


@bot.message_handler(commands=['start', 'ayuda'])
def handle_start(message):
    if not es_chat_privado(message):
        bot.reply_to(message, 'Usa el bot en un chat privado para consultar comandos.')
        return
    if not es_administrador(message.from_user.id if message.from_user else None):
        responder_no_autorizado(message)
        return
    bot.reply_to(
        message,
        'Comandos administrativos:\n'
        '/consultar <no_empleado> - consulta puesto y datos básicos\n'
        '/eliminar <no_empleado> - solicita confirmación antes del borrado definitivo\n'
        '/cancelar_eliminar - cancela tus confirmaciones pendientes',
    )


@bot.message_handler(commands=['consultar'])
def handle_consultar(message):
    if not es_chat_privado(message):
        bot.reply_to(message, 'Por seguridad, las consultas solo están disponibles en un chat privado.')
        return
    if not es_administrador(message.from_user.id if message.from_user else None):
        responder_no_autorizado(message)
        return

    partes = (message.text or '').split(maxsplit=1)
    if len(partes) != 2 or not partes[1].strip():
        bot.reply_to(message, 'Uso: /consultar <no_empleado>')
        return

    try:
        empleado, error = buscar_empleado(partes[1].strip())
    except (psycopg2.Error, RuntimeError):
        logging.getLogger(__name__).exception('No se pudo consultar un empleado desde Telegram.')
        bot.reply_to(message, 'No fue posible consultar la base de datos.')
        return

    if error:
        bot.reply_to(message, error)
        return

    bot.reply_to(
        message,
        '\n'.join((
            f'Número: {empleado[2] or "Sin asignar"}',
            f'Nombre: {empleado[1] or "Sin nombre"}',
            f'Puesto: {empleado[3] or "Sin dato"}',
            f'Área: {empleado[4] or "Sin dato"}',
            f'Turno: {empleado[5] or "Sin dato"}',
        )),
    )


@bot.message_handler(commands=['eliminar'])
def handle_eliminar(message):
    if not es_chat_privado(message):
        bot.reply_to(message, 'Por seguridad, las eliminaciones solo están disponibles en un chat privado.')
        return
    if not es_administrador(message.from_user.id if message.from_user else None):
        responder_no_autorizado(message)
        return
    limpiar_confirmaciones_expiradas()
    if str(config.get('DB_WRITE_LOCK') or '0').strip() == '1':
        bot.reply_to(message, 'La base de datos está bloqueada para cambios; no se realizó ninguna acción.')
        return

    partes = (message.text or '').split(maxsplit=1)
    if len(partes) != 2 or not partes[1].strip():
        bot.reply_to(message, 'Uso: /eliminar <no_empleado>')
        return

    no_empleado = partes[1].strip()
    try:
        empleado, error = buscar_empleado(no_empleado)
    except (psycopg2.Error, RuntimeError):
        logging.getLogger(__name__).exception('No se pudo preparar una eliminación desde Telegram.')
        bot.reply_to(message, 'No fue posible consultar la base de datos.')
        return

    if error:
        bot.reply_to(message, error)
        return

    confirmation_id = uuid4().hex
    pending_deletions[confirmation_id] = {
        'admin_id': message.from_user.id,
        'registro_id': empleado[0],
        'no_empleado': no_empleado,
        'expires_at': time.monotonic() + CONFIRMATION_TTL_SECONDS,
    }

    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton('Sí, eliminar definitivamente', callback_data=f'delete:{confirmation_id}'),
        types.InlineKeyboardButton('Cancelar', callback_data=f'cancel:{confirmation_id}'),
    )
    bot.reply_to(
        message,
        (
            f'Confirma el borrado definitivo del registro de {empleado[1] or "este empleado"} '
            f'(número {empleado[2]})?\n'
            'Se eliminarán los datos y documentos relacionados con ese registro. '
            'Esta acción no se puede deshacer.'
        ),
        reply_markup=keyboard,
    )


@bot.message_handler(commands=['cancelar_eliminar'])
def handle_cancelar_eliminar(message):
    if not es_chat_privado(message):
        bot.reply_to(message, 'Por seguridad, las confirmaciones solo se administran en un chat privado.')
        return
    user_id = message.from_user.id if message.from_user else None
    if not es_administrador(user_id):
        responder_no_autorizado(message)
        return

    limpiar_confirmaciones_expiradas()

    canceladas = [
        confirmation_id
        for confirmation_id, pendiente in pending_deletions.items()
        if pendiente['admin_id'] == user_id
    ]
    for confirmation_id in canceladas:
        pending_deletions.pop(confirmation_id, None)
    bot.reply_to(message, 'Se cancelaron tus confirmaciones pendientes.')


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith(('delete:', 'cancel:'))))
def handle_confirmacion_eliminacion(call):
    accion, _, confirmation_id = call.data.partition(':')
    pendiente = pending_deletions.get(confirmation_id)
    user_id = call.from_user.id if call.from_user else None

    if (
        not call.message
        or call.message.chat.type != 'private'
        or not es_administrador(user_id)
        or not pendiente
        or pendiente['admin_id'] != user_id
    ):
        bot.answer_callback_query(call.id, 'Esta confirmación no está disponible.')
        return
    if pendiente['expires_at'] <= time.monotonic():
        pending_deletions.pop(confirmation_id, None)
        bot.answer_callback_query(call.id, 'La confirmación expiró.')
        bot.send_message(call.message.chat.id, 'La solicitud expiró. Usa /eliminar para iniciar otra.')
        return
    if accion == 'cancel':
        pending_deletions.pop(confirmation_id, None)
        bot.answer_callback_query(call.id, 'Eliminación cancelada.')
        bot.send_message(call.message.chat.id, 'No se eliminó ningún registro.')
        return
    if str(config.get('DB_WRITE_LOCK') or '0').strip() == '1':
        pending_deletions.pop(confirmation_id, None)
        bot.answer_callback_query(call.id, 'La base de datos está bloqueada.')
        bot.send_message(call.message.chat.id, 'La base de datos está bloqueada; no se eliminó ningún registro.')
        return

    try:
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
                    (pendiente['registro_id'], pendiente['no_empleado']),
                )
                eliminado = cur.fetchone()
            conn.commit()
    except (psycopg2.Error, RuntimeError):
        logging.getLogger(__name__).exception('No se pudo completar una eliminación desde Telegram.')
        bot.answer_callback_query(call.id, 'No fue posible completar el borrado.')
        bot.send_message(call.message.chat.id, 'No fue posible completar el borrado; no confirmes de nuevo sin consultar el estado.')
        return

    pending_deletions.pop(confirmation_id, None)
    if eliminado:
        bot.answer_callback_query(call.id, 'Registro eliminado.')
        bot.send_message(
            call.message.chat.id,
            f'Se eliminó el registro con número de empleado {pendiente["no_empleado"]}.',
        )
    else:
        bot.answer_callback_query(call.id, 'El registro ya no coincide.')
        bot.send_message(call.message.chat.id, 'No se eliminó ningún registro; consulta su estado actual.')


@bot.message_handler(content_types=['text'])
def handle_text(message):
    if es_chat_privado(message):
        bot.reply_to(message, 'Usa /ayuda para consultar los comandos disponibles.')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info('Bot de Telegram encendido.')
    bot.infinity_polling()
