"""Vinculación de cuentas del panel con Telegram y sesión delegada para el bot.

Panel (con sesión normal):
  POST   /api/telegram/codigo              código de un solo uso + deep link
  GET    /api/telegram/vinculo             estado del vínculo propio
  DELETE /api/telegram/vinculo             desvincularse
  GET    /api/telegram/vinculos            (admin) todos los vínculos
  DELETE /api/telegram/vinculos/<usuario>  (admin) revocar

Bot (solo con X-Bot-Secreto; nginx responde 404 a /api/telegram/interno/):
  POST /api/telegram/interno/vincular      consume el código y vincula
  POST /api/telegram/interno/sesion        token de 15 min de la cuenta vinculada
  POST /api/telegram/interno/desvincular

El bot no tiene credenciales de BD ni permisos propios: con la sesión que
recibe llama a la misma API que el panel y le aplican los mismos permisos.
"""

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import psycopg2
from flask import Blueprint, g, jsonify, request

import auth

CABECERA_SECRETO = 'X-Bot-Secreto'
ALFABETO_CODIGO = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # sin 0/O ni 1/I
LARGO_CODIGO = 8
PATRON_CODIGO = re.compile(rf'^[{ALFABETO_CODIGO}]{{{LARGO_CODIGO}}}$')


def minutos_codigo():
    try:
        return max(1, int(os.getenv('TELEGRAM_MINUTOS_CODIGO', '10')))
    except ValueError:
        return 10


def secreto_bot():
    return (os.getenv('TELEGRAM_BOT_SECRETO_INTERNO') or '').strip()


def generar_codigo():
    return ''.join(secrets.choice(ALFABETO_CODIGO) for _ in range(LARGO_CODIGO))


def normalizar_codigo(texto):
    """Acepta minúsculas, espacios y guiones: 'abcd-efgh' -> 'ABCDEFGH'."""
    limpio = re.sub(r'[\s-]', '', str(texto or '')).upper()
    return limpio if PATRON_CODIGO.match(limpio) else None


def formatear_codigo(codigo):
    return f'{codigo[:4]}-{codigo[4:]}'


def hash_codigo(codigo):
    return hashlib.sha256(codigo.encode()).hexdigest()


def enlace_start(codigo):
    usuario_bot = (os.getenv('TELEGRAM_BOT_USERNAME') or '').strip().lstrip('@')
    return f'https://t.me/{usuario_bot}?start={codigo}' if usuario_bot else None


def exigir_bot(vista):
    """Rutas internas: cerradas (503) sin secreto configurado, 401 con secreto incorrecto."""
    @wraps(vista)
    def envoltura(*args, **kwargs):
        esperado = secreto_bot()
        if not esperado:
            return jsonify({'error': 'El bot de Telegram está desactivado.', 'codigo': 'BOT_APAGADO'}), 503
        recibido = request.headers.get(CABECERA_SECRETO, '')
        if not hmac.compare_digest(recibido.encode(), esperado.encode()):
            return jsonify({'error': 'No autorizado.'}), 401
        return vista(*args, **kwargs)
    return envoltura


def _telegram_id(datos):
    valor = datos.get('telegram_id')
    if isinstance(valor, bool):
        return None
    try:
        telegram_id = int(valor)
    except (TypeError, ValueError):
        return None
    return telegram_id if telegram_id > 0 else None


def crear_blueprint(get_db_connection, normalizar_permisos, asegurar_esquema):
    bp = Blueprint('telegram', __name__, url_prefix='/api/telegram')

    def sesion_para(cur, telegram_id):
        """Emite la sesión de la cuenta vinculada a ese Telegram, o None."""
        cur.execute(
            """UPDATE telegram_vinculos SET ultimo_uso_en = now()
               WHERE telegram_id = %s
               RETURNING usuario_id""",
            (telegram_id,),
        )
        fila = cur.fetchone()
        if not fila:
            return None
        cur.execute(
            'SELECT id, usuario, password_hash, permisos FROM usuarios_acceso WHERE id = %s',
            (fila[0],),
        )
        usuario = cur.fetchone()
        if not usuario:
            return None
        minutos = auth.MINUTOS_POR_CANAL[auth.CANAL_TELEGRAM]
        return {
            'token': auth.emitir_token(usuario[0], usuario[2], auth.CANAL_TELEGRAM),
            'expira_en': (datetime.now(timezone.utc) + timedelta(minutes=minutos)).isoformat(),
            'usuario': {
                'id': usuario[0],
                'usuario': usuario[1],
                'permisos': normalizar_permisos(auth._permisos(usuario[3])),
            },
        }

    # --- Panel -------------------------------------------------------------

    @bp.route('/codigo', methods=['POST'])
    def crear_codigo():
        usuario = g.usuario
        if not usuario or usuario['canal'] != auth.CANAL_WEB:
            return jsonify({'error': 'Genera el código desde el panel.'}), 403
        codigo = generar_codigo()
        expira = datetime.now(timezone.utc) + timedelta(minutes=minutos_codigo())
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_esquema(cur)
                cur.execute(
                    'DELETE FROM telegram_codigos WHERE usuario_id = %s AND usado_en IS NULL',
                    (usuario['id'],),
                )
                cur.execute(
                    'INSERT INTO telegram_codigos (codigo_hash, usuario_id, expira_en) VALUES (%s, %s, %s)',
                    (hash_codigo(codigo), usuario['id'], expira),
                )
            conn.commit()
        return jsonify({
            'codigo': formatear_codigo(codigo),
            'expira_en': expira.isoformat(),
            'enlace': enlace_start(codigo),
            'bot_activo': bool(secreto_bot()),
        }), 201

    @bp.route('/vinculo', methods=['GET'])
    def ver_vinculo():
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_esquema(cur)
                cur.execute(
                    """SELECT telegram_username, creado_en, ultimo_uso_en
                       FROM telegram_vinculos WHERE usuario_id = %s""",
                    (g.usuario['id'],),
                )
                fila = cur.fetchone()
            conn.commit()
        bot = (os.getenv('TELEGRAM_BOT_USERNAME') or '').strip().lstrip('@') or None
        if not fila:
            return jsonify({'vinculado': False, 'bot': bot})
        return jsonify({
            'vinculado': True,
            'bot': bot,
            'telegram_username': fila[0],
            'creado_en': fila[1].isoformat() if fila[1] else None,
            'ultimo_uso_en': fila[2].isoformat() if fila[2] else None,
        })

    @bp.route('/vinculo', methods=['DELETE'])
    def borrar_vinculo_propio():
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM telegram_vinculos WHERE usuario_id = %s', (g.usuario['id'],))
                borrados = cur.rowcount
            conn.commit()
        return jsonify({'ok': True, 'desvinculado': bool(borrados)})

    @bp.route('/vinculos', methods=['GET'])
    def listar_vinculos():
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_esquema(cur)
                cur.execute(
                    """SELECT v.usuario_id, u.usuario, v.telegram_username, v.creado_en, v.ultimo_uso_en
                       FROM telegram_vinculos v JOIN usuarios_acceso u ON u.id = v.usuario_id
                       ORDER BY u.usuario"""
                )
                filas = cur.fetchall()
            conn.commit()
        return jsonify({'vinculos': [
            {
                'usuario_id': f[0],
                'usuario': f[1],
                'telegram_username': f[2],
                'creado_en': f[3].isoformat() if f[3] else None,
                'ultimo_uso_en': f[4].isoformat() if f[4] else None,
            }
            for f in filas
        ]})

    @bp.route('/vinculos/<int:usuario_id>', methods=['DELETE'])
    def revocar_vinculo(usuario_id):
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM telegram_vinculos WHERE usuario_id = %s', (usuario_id,))
                borrados = cur.rowcount
            conn.commit()
        if not borrados:
            return jsonify({'error': 'Esa cuenta no tiene Telegram vinculado.'}), 404
        return jsonify({'ok': True})

    # --- Bot ---------------------------------------------------------------

    @bp.route('/interno/vincular', methods=['POST'])
    @exigir_bot
    def interno_vincular():
        datos = request.get_json(silent=True) or {}
        codigo = normalizar_codigo(datos.get('codigo'))
        telegram_id = _telegram_id(datos)
        username = str(datos.get('telegram_username') or '').strip().lstrip('@')[:64] or None
        if not codigo or not telegram_id:
            return jsonify({'error': 'Código inválido.', 'codigo': 'CODIGO_INVALIDO'}), 400
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    asegurar_esquema(cur)
                    # Comprobar y consumir en la misma sentencia: dos canjes
                    # simultáneos del mismo código no pueden ganar ambos.
                    cur.execute(
                        """UPDATE telegram_codigos SET usado_en = now()
                           WHERE codigo_hash = %s AND usado_en IS NULL AND expira_en > now()
                           RETURNING usuario_id""",
                        (hash_codigo(codigo),),
                    )
                    fila = cur.fetchone()
                    if not fila:
                        conn.rollback()
                        return jsonify({
                            'error': 'El código no existe, ya se usó o expiró.',
                            'codigo': 'CODIGO_INVALIDO',
                        }), 400
                    usuario_id = fila[0]
                    cur.execute(
                        'SELECT usuario_id FROM telegram_vinculos WHERE telegram_id = %s',
                        (telegram_id,),
                    )
                    otro = cur.fetchone()
                    if otro and otro[0] != usuario_id:
                        conn.rollback()
                        return jsonify({
                            'error': 'Este Telegram ya está vinculado a otra cuenta. Usa /desvincular primero.',
                            'codigo': 'TELEGRAM_EN_USO',
                        }), 409
                    cur.execute('DELETE FROM telegram_vinculos WHERE usuario_id = %s', (usuario_id,))
                    cur.execute(
                        """INSERT INTO telegram_vinculos (usuario_id, telegram_id, telegram_username)
                           VALUES (%s, %s, %s)""",
                        (usuario_id, telegram_id, username),
                    )
                    sesion = sesion_para(cur, telegram_id)
                conn.commit()
        except psycopg2.IntegrityError:
            return jsonify({'error': 'Este Telegram ya está vinculado a otra cuenta.', 'codigo': 'TELEGRAM_EN_USO'}), 409
        # Para que la bitácora registre el vínculo a nombre de la cuenta.
        g.usuario = {**sesion['usuario'], 'canal': auth.CANAL_TELEGRAM}
        return jsonify(sesion), 201

    @bp.route('/interno/sesion', methods=['POST'])
    @exigir_bot
    def interno_sesion():
        telegram_id = _telegram_id(request.get_json(silent=True) or {})
        if not telegram_id:
            return jsonify({'error': 'telegram_id inválido.'}), 400
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_esquema(cur)
                sesion = sesion_para(cur, telegram_id)
            conn.commit()
        if not sesion:
            return jsonify({'error': 'Este Telegram no está vinculado.', 'codigo': 'NO_VINCULADO'}), 404
        return jsonify(sesion)

    @bp.route('/interno/desvincular', methods=['POST'])
    @exigir_bot
    def interno_desvincular():
        telegram_id = _telegram_id(request.get_json(silent=True) or {})
        if not telegram_id:
            return jsonify({'error': 'telegram_id inválido.'}), 400
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM telegram_vinculos v USING usuarios_acceso u
                       WHERE v.telegram_id = %s AND u.id = v.usuario_id
                       RETURNING v.usuario_id, u.usuario""",
                    (telegram_id,),
                )
                fila = cur.fetchone()
            conn.commit()
        if fila:
            g.usuario = {'id': fila[0], 'usuario': fila[1], 'permisos': [], 'canal': auth.CANAL_TELEGRAM}
        return jsonify({'ok': True, 'desvinculado': bool(fila)})

    return bp
