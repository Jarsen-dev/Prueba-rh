"""Memoria del asistente por persona: notas que ella pidió recordar.

Preferencias y alias («cuando digo "la planta" me refiero a Kronos», «mis
reportes son de Cheong Woon»). Se guardan con el botón Confirmar, como
cualquier cambio, y el bot se las pasa al agente en cada turno. Cada quien ve y
borra solo las suyas. Son texto escrito por la persona: no van en las trazas.
"""

from flask import Blueprint, g, jsonify, request

MAX_NOTAS = 20
MAX_TEXTO = 200

SQL_ESQUEMA = """
CREATE TABLE IF NOT EXISTS bot_memoria (
    id          SERIAL PRIMARY KEY,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios_acceso(id) ON DELETE CASCADE,
    texto       VARCHAR(200) NOT NULL,
    creada_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bot_memoria_usuario ON bot_memoria (usuario_id);
"""


def crear_blueprint(get_db_connection, asegurar_esquema):
    bp = Blueprint('memoria', __name__, url_prefix='/api')

    def esquema(cur):
        asegurar_esquema(cur)
        cur.execute(SQL_ESQUEMA)

    def usuario_id():
        usuario = getattr(g, 'usuario', None)
        return usuario['id'] if usuario else None

    @bp.route('/memoria', methods=['GET'])
    def listar_memoria():
        uid = usuario_id()
        if uid is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute('SELECT id, texto FROM bot_memoria WHERE usuario_id = %s ORDER BY id', (uid,))
                notas = [{'id': f[0], 'texto': f[1]} for f in cur.fetchall()]
            conn.commit()
        return jsonify({'notas': notas, 'maximo': MAX_NOTAS})

    @bp.route('/memoria', methods=['POST'])
    def guardar_memoria():
        uid = usuario_id()
        if uid is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        datos = request.get_json(silent=True) or {}
        texto = ' '.join(str(datos.get('texto') or '').split()) if isinstance(datos, dict) else ''
        if not texto or len(texto) > MAX_TEXTO:
            return jsonify({'error': f'La nota debe tener de 1 a {MAX_TEXTO} caracteres.'}), 400
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute('SELECT COUNT(*) FROM bot_memoria WHERE usuario_id = %s', (uid,))
                if cur.fetchone()[0] >= MAX_NOTAS:
                    return jsonify({'error': f'Ya tienes {MAX_NOTAS} notas; borra alguna primero.'}), 409
                cur.execute('INSERT INTO bot_memoria (usuario_id, texto) VALUES (%s, %s) RETURNING id', (uid, texto))
                nota_id = cur.fetchone()[0]
            conn.commit()
        return jsonify({'id': nota_id, 'texto': texto}), 201

    @bp.route('/memoria/<int:nota_id>', methods=['DELETE'])
    def borrar_memoria(nota_id):
        uid = usuario_id()
        if uid is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute('DELETE FROM bot_memoria WHERE id = %s AND usuario_id = %s RETURNING id', (nota_id, uid))
                borrada = cur.fetchone()
            conn.commit()
        if not borrada:
            return jsonify({'error': 'Nota no encontrada.'}), 404
        return jsonify({'ok': True})

    return bp
