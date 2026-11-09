"""Bitácora mínima de escrituras: quién, por qué canal, qué ruta y resultado.

No guarda cuerpos ni parámetros (pueden traer datos personales).
"""

import logging
import re

from flask import g, request

logger = logging.getLogger(__name__)

METODOS_ESCRITURA = {'POST', 'PUT', 'PATCH', 'DELETE'}
ENDPOINTS_EXCLUIDOS = {'iniciar_sesion_acceso', 'cerrar_sesion_acceso', 'escanear_documento'}
PATRON_ORIGEN = re.compile(r'^(voz|menu|agente|tarea:\d+|trabajo:\d+)$')


def origen_telegram():
    """Cabecera X-Origen-Telegram, solo válida en sesiones de Telegram."""
    usuario = getattr(g, 'usuario', None)
    if not usuario or usuario.get('canal') != 'telegram':
        return None
    valor = (request.headers.get('X-Origen-Telegram') or '').strip()
    return valor if PATRON_ORIGEN.match(valor) else None


def describir_canal(canal, origen=None):
    if canal != 'telegram':
        return canal
    return f'vía Telegram, {origen}' if origen else 'vía Telegram'


def init_bitacora(app, get_db_connection, asegurar_esquema):
    @app.after_request
    def registrar_escritura(respuesta):
        if request.method not in METODOS_ESCRITURA or request.endpoint in ENDPOINTS_EXCLUIDOS:
            return respuesta
        usuario = getattr(g, 'usuario', None)
        # Solo escrituras con sesión que el backend aceptó (las anónimas del
        # formulario público no identifican a nadie).
        if request.endpoint is None or not usuario or respuesta.status_code >= 400:
            return respuesta
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    asegurar_esquema(cur)
                    cur.execute(
                        """INSERT INTO bitacora (usuario_id, usuario, canal, origen, metodo, ruta, estado_http)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            usuario.get('id'),
                            usuario.get('usuario'),
                            usuario.get('canal', 'web'),
                            origen_telegram(),
                            request.method,
                            request.path[:500],
                            respuesta.status_code,
                        ),
                    )
                conn.commit()
        except Exception:
            # La bitácora nunca rompe la respuesta.
            logger.exception('No se pudo registrar en bitácora.')
        return respuesta
