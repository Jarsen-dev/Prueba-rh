"""Autenticación del lado del servidor.

- El login emite un token firmado (itsdangerous) que viaja en la cookie
  ``rh_sesion`` (para enlaces y descargas) o en ``Authorization: Bearer``
  (para fetch entre puertos y para el bot de Telegram).
- Cada ruta tiene un permiso en ``PERMISOS_POR_ENDPOINT``. Una ruta nueva sin
  clasificar exige ``admin`` (cerrado por omisión) y una prueba lo detecta.
- ``AUTH_ESTRICTO=0`` (por omisión) deja pasar al panel sin token con un
  warning, para migrar sin romper páginas. Con ``1`` responde 401.
  El canal Telegram siempre es estricto.
"""

import hashlib
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone

from flask import g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

COOKIE_SESION = 'rh_sesion'
CANAL_WEB = 'web'
CANAL_TELEGRAM = 'telegram'
MINUTOS_POR_CANAL = {CANAL_WEB: 8 * 60, CANAL_TELEGRAM: 15}

PUBLICO = 'publico'
SESION = 'sesion'  # cualquier usuario con sesión
ADMIN = frozenset({'admin'})
ADMIN_O_RECLUTAMIENTO = frozenset({'admin', 'reclutamiento'})

# endpoint -> permiso, o {metodo: permiso} cuando cambia por método.
PERMISOS_POR_ENDPOINT = {
    # Públicas: formulario de registro de candidatos y salud.
    'static': PUBLICO,
    'health_check': PUBLICO,
    'iniciar_sesion_acceso': PUBLICO,
    'cerrar_sesion_acceso': PUBLICO,
    'guardar_registro': PUBLICO,
    'guardar_encuesta': PUBLICO,
    'escanear_documento': PUBLICO,
    'manejar_reclutadores': {'GET': PUBLICO, 'POST': ADMIN_O_RECLUTAMIENTO},
    'sesion_actual': SESION,

    # Reclutamiento y admin (InicioReclu, listados, papelería del expediente).
    'api_datos_grafica': ADMIN_O_RECLUTAMIENTO,
    'obtener_ingresos_por_team': ADMIN_O_RECLUTAMIENTO,
    'exportar_reclutamiento': ADMIN_O_RECLUTAMIENTO,
    'actualizar_reclutador': ADMIN_O_RECLUTAMIENTO,
    'eliminar_telefono_reclutador': ADMIN_O_RECLUTAMIENTO,
    'obtener_ingresos': ADMIN_O_RECLUTAMIENTO,
    'obtener_empleado': ADMIN_O_RECLUTAMIENTO,
    'obtener_empleado_por_id': ADMIN_O_RECLUTAMIENTO,
    'listar_archivos_empleado': ADMIN_O_RECLUTAMIENTO,
    'subir_archivo_empleado': ADMIN_O_RECLUTAMIENTO,
    'eliminar_archivo_empleado': ADMIN_O_RECLUTAMIENTO,
    'servir_documento_desde_db': ADMIN_O_RECLUTAMIENTO,
    'servir_documento_empleado': ADMIN_O_RECLUTAMIENTO,
    'buscar_empleados': ADMIN_O_RECLUTAMIENTO,
    'catalogo_ingreso': ADMIN,

    # Telegram: el panel genera códigos y ve su vínculo; las internas las
    # protege el secreto del bot (telegram_api.exigir_bot), no la sesión.
    'telegram.crear_codigo': SESION,
    'telegram.ver_vinculo': SESION,
    'telegram.borrar_vinculo_propio': SESION,
    'telegram.listar_vinculos': ADMIN,
    'telegram.revocar_vinculo': ADMIN,
    'telegram.interno_vincular': PUBLICO,
    'telegram.interno_sesion': PUBLICO,
    'telegram.interno_desvincular': PUBLICO,
    'telegram.interno_guia': PUBLICO,
    'telegram.interno_trazas': PUBLICO,
    'telegram.interno_calificar': PUBLICO,
    'telegram.interno_presupuesto': PUBLICO,

    # Solo admin.
    'actualizar_empleado': ADMIN,
    'exportar_empleados': ADMIN,
    'exportar_excel': ADMIN,
    'eliminar_empleado_completo': ADMIN,
    'incidencias_empleado': ADMIN,
    'eliminar_incidencia_empleado': ADMIN,
    'descargar_archivo_incidencia': ADMIN,
    'obtener_pendientes': ADMIN,
    'eliminar_pendiente': ADMIN,
    'guardar_ingreso': ADMIN,
    'guardar_ingresos': ADMIN,
    'obtener_eventos_compartidos': ADMIN,
    'guardar_eventos_compartidos': ADMIN,
    'listar_usuarios_acceso': ADMIN,
    'crear_usuario_acceso': ADMIN,
    'actualizar_usuario_acceso': ADMIN,
    'eliminar_usuario_acceso': ADMIN,
    'admin_normalize_encuestas': ADMIN,
    'admin_preview_encuestas': ADMIN,
}


def auth_estricto():
    return str(os.getenv('AUTH_ESTRICTO', '0')).strip() == '1'


def permiso_requerido(endpoint, metodo):
    permiso = PERMISOS_POR_ENDPOINT.get(endpoint, ADMIN)
    if isinstance(permiso, dict):
        return permiso.get(metodo, ADMIN)
    return permiso


# --- Clave de firma -------------------------------------------------------

_clave_cache = None
_clave_lock = threading.Lock()


def _ruta_archivo_clave():
    base = os.getenv('EMPLOYEE_DOCS_ROOT') or os.path.join(os.path.dirname(__file__), 'uploads')
    return os.path.join(base, '.auth_secret')


def clave_firma():
    """AUTH_SECRET_KEY, o una clave persistida en disco compartida por los workers."""
    global _clave_cache
    if _clave_cache:
        return _clave_cache
    with _clave_lock:
        if _clave_cache:
            return _clave_cache
        clave = (os.getenv('AUTH_SECRET_KEY') or '').strip()
        if not clave:
            ruta = _ruta_archivo_clave()
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            try:
                fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as archivo:
                    archivo.write(secrets.token_urlsafe(48))
                logger.warning('AUTH_SECRET_KEY no está configurada; se generó %s.', ruta)
            except FileExistsError:
                pass
            with open(ruta) as archivo:
                clave = archivo.read().strip()
        _clave_cache = clave
        return clave


def _serializador():
    return URLSafeTimedSerializer(clave_firma(), salt='rh-sesion')


def huella_password(password_hash):
    """Cambiar la contraseña invalida las sesiones emitidas antes."""
    return hashlib.sha256((password_hash or '').encode()).hexdigest()[:12]


def emitir_token(usuario_id, password_hash, canal=CANAL_WEB):
    return _serializador().dumps({'u': int(usuario_id), 'c': canal, 'p': huella_password(password_hash)})


def leer_token(token):
    """Devuelve el payload o None si es inválido o expiró según su canal."""
    if not token:
        return None
    try:
        payload, emitido = _serializador().loads(
            token, max_age=max(MINUTOS_POR_CANAL.values()) * 60, return_timestamp=True
        )
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(payload, dict) or 'u' not in payload:
        return None
    minutos = MINUTOS_POR_CANAL.get(payload.get('c', CANAL_WEB), 15)
    if (datetime.now(timezone.utc) - emitido).total_seconds() > minutos * 60:
        return None
    return payload


def viene_por_bearer(token):
    """True si el token llegó por cabecera Authorization."""
    return bool(token) and request.headers.get('Authorization', '').lower().startswith('bearer ')


def token_de_peticion():
    cabecera = request.headers.get('Authorization', '')
    if cabecera.lower().startswith('bearer '):
        return cabecera[7:].strip()
    return request.cookies.get(COOKIE_SESION)


# --- Carga de usuario -----------------------------------------------------

def _permisos(valor):
    if isinstance(valor, list):
        return valor
    try:
        return json.loads(valor or '[]')
    except (TypeError, ValueError):
        return []


def cargar_usuario(cur, payload, normalizar_permisos):
    """Usuario vigente del token, o None. En Telegram exige vínculo vigente."""
    canal = payload.get('c', CANAL_WEB)
    if canal == CANAL_TELEGRAM:
        cur.execute(
            """SELECT u.id, u.usuario, u.password_hash, u.permisos
               FROM usuarios_acceso u
               JOIN telegram_vinculos v ON v.usuario_id = u.id
               WHERE u.id = %s""",
            (payload['u'],),
        )
    else:
        cur.execute(
            'SELECT id, usuario, password_hash, permisos FROM usuarios_acceso WHERE id = %s',
            (payload['u'],),
        )
    fila = cur.fetchone()
    if not fila or huella_password(fila[2]) != payload.get('p'):
        return None
    return {
        'id': fila[0],
        'usuario': fila[1],
        'permisos': normalizar_permisos(_permisos(fila[3])),
        'canal': canal,
    }


def _no_autorizado(mensaje='Inicia sesión para continuar.'):
    return jsonify({'error': mensaje, 'codigo': 'NO_AUTENTICADO'}), 401


def _prohibido():
    return jsonify({'error': 'No tienes permiso para esta acción.', 'codigo': 'SIN_PERMISO'}), 403


def init_auth(app, get_db_connection, normalizar_permisos, asegurar_esquema):
    @app.before_request
    def verificar_sesion():
        g.usuario = None
        if request.method == 'OPTIONS' or request.endpoint is None:
            return None

        permiso = permiso_requerido(request.endpoint, request.method)
        token = token_de_peticion()
        payload = leer_token(token) if token else None

        if payload:
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        asegurar_esquema(cur)
                        g.usuario = cargar_usuario(cur, payload, normalizar_permisos)
                    conn.commit()
            except Exception:
                logger.exception('No se pudo validar la sesión.')
                return jsonify({'error': 'No se pudo validar la sesión.'}), 503

        if permiso == PUBLICO:
            return None

        if g.usuario is None:
            # Un Bearer inválido (bot o fetch del panel) siempre es 401; también
            # sin sesión en modo estricto. En modo gradual el panel pasa con warning.
            if viene_por_bearer(token) or auth_estricto():
                return _no_autorizado('La sesión expiró o fue revocada.' if token else 'Inicia sesión para continuar.')
            logger.warning('Petición sin sesión válida a %s %s (modo gradual).', request.method, request.path)
            return None

        if permiso == SESION:
            return None
        if not set(g.usuario['permisos']) & set(permiso):
            return _prohibido()
        return None


def poner_cookie_sesion(respuesta, token):
    respuesta.set_cookie(
        COOKIE_SESION,
        token,
        max_age=MINUTOS_POR_CANAL[CANAL_WEB] * 60,
        httponly=True,
        samesite='Strict',
        secure=str(os.getenv('AUTH_COOKIE_SECURE', '0')).strip() == '1',
        path='/',
    )
    return respuesta


def borrar_cookie_sesion(respuesta):
    respuesta.delete_cookie(COOKIE_SESION, path='/')
    return respuesta


# --- Esquema --------------------------------------------------------------

SQL_ESQUEMA_AUTH = """
CREATE TABLE IF NOT EXISTS telegram_codigos (
    codigo_hash   CHAR(64) PRIMARY KEY,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios_acceso(id) ON DELETE CASCADE,
    expira_en     TIMESTAMPTZ NOT NULL,
    usado_en      TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS telegram_vinculos (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL UNIQUE REFERENCES usuarios_acceso(id) ON DELETE CASCADE,
    telegram_id        BIGINT NOT NULL UNIQUE,
    telegram_username  VARCHAR(64),
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultimo_uso_en      TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS bitacora (
    id          BIGSERIAL PRIMARY KEY,
    fecha       TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id  INTEGER REFERENCES usuarios_acceso(id) ON DELETE SET NULL,
    usuario     VARCHAR(100),
    canal       VARCHAR(20) NOT NULL DEFAULT 'web',
    origen      VARCHAR(40),
    metodo      VARCHAR(10) NOT NULL,
    ruta        TEXT NOT NULL,
    estado_http INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bitacora_fecha ON bitacora (fecha DESC);
CREATE TABLE IF NOT EXISTS bot_trazas (
    id              BIGSERIAL PRIMARY KEY,
    fecha           TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id      INTEGER REFERENCES usuarios_acceso(id) ON DELETE SET NULL,
    modelo          VARCHAR(60) NOT NULL,
    tokens_entrada  INTEGER NOT NULL DEFAULT 0,
    tokens_salida   INTEGER NOT NULL DEFAULT 0,
    tokens_cache    INTEGER NOT NULL DEFAULT 0,
    rondas          SMALLINT NOT NULL DEFAULT 0,
    herramientas    VARCHAR(400) NOT NULL DEFAULT '',
    segundos        REAL NOT NULL DEFAULT 0,
    resultado       VARCHAR(20) NOT NULL,
    calificacion    SMALLINT
);
CREATE INDEX IF NOT EXISTS idx_bot_trazas_fecha ON bot_trazas (fecha DESC);
"""

_esquema_listo = False
_esquema_lock = threading.Lock()


def asegurar_esquema_auth(cur, asegurar_tabla_usuarios_acceso):
    """Crea las tablas de sesión una vez por proceso (no hay migraciones)."""
    global _esquema_listo
    if _esquema_listo:
        return
    with _esquema_lock:
        if _esquema_listo:
            return
        asegurar_tabla_usuarios_acceso(cur)
        cur.execute(SQL_ESQUEMA_AUTH)
        _esquema_listo = True
