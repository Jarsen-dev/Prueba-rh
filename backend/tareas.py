"""Tareas programadas del asistente de Telegram.

Una tarea es una instrucción de consulta («dame los ingresos de la semana») que
el bot corre a una hora fija **en modo solo lectura** y con la sesión de su
dueño. Las rutas ``/api/tareas`` son de cada persona: solo ve y borra las suyas.
``/api/telegram/interno/tareas/turno`` (secreto del bot) reclama las que ya
toca correr y calcula la siguiente ejecución antes de devolverlas, así un
reinicio del bot no las repite.

La instrucción se guarda tal como la escribió la persona (es lo único que el
bot necesita para correrla); las trazas siguen sin guardar texto.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, request

import telegram_api

ZONA = ZoneInfo('America/Mexico_City')
FRECUENCIAS = ('diaria', 'semanal', 'mensual')
MAX_TAREAS = 10
MAX_INSTRUCCION = 300
DIAS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')

SQL_ESQUEMA = """
CREATE TABLE IF NOT EXISTS bot_tareas (
    id            SERIAL PRIMARY KEY,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios_acceso(id) ON DELETE CASCADE,
    instruccion   VARCHAR(300) NOT NULL,
    frecuencia    VARCHAR(10) NOT NULL CHECK (frecuencia IN ('diaria', 'semanal', 'mensual')),
    dias_semana   VARCHAR(20) NOT NULL DEFAULT '',
    dia_mes       SMALLINT CHECK (dia_mes BETWEEN 1 AND 28),
    hora          SMALLINT NOT NULL CHECK (hora BETWEEN 0 AND 23),
    minuto        SMALLINT NOT NULL DEFAULT 0 CHECK (minuto BETWEEN 0 AND 59),
    proxima       TIMESTAMPTZ NOT NULL,
    ultima        TIMESTAMPTZ,
    creada_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bot_tareas_proxima ON bot_tareas (proxima);
"""


class TareaInvalida(ValueError):
    pass


def validar(datos) -> dict:
    """Normaliza y valida lo que manda el bot (o el panel). Lanza TareaInvalida."""
    if not isinstance(datos, dict):
        raise TareaInvalida('Cuerpo inválido.')
    instruccion = ' '.join(str(datos.get('instruccion') or '').split())
    if not instruccion:
        raise TareaInvalida('Falta la instrucción.')
    if len(instruccion) > MAX_INSTRUCCION:
        raise TareaInvalida(f'La instrucción no puede pasar de {MAX_INSTRUCCION} caracteres.')
    frecuencia = datos.get('frecuencia')
    if frecuencia not in FRECUENCIAS:
        raise TareaInvalida('frecuencia debe ser diaria, semanal o mensual.')

    def entero(campo, minimo, maximo, defecto=None):
        valor = datos.get(campo, defecto)
        if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
            raise TareaInvalida(f'{campo} debe ser un entero de {minimo} a {maximo}.')
        return valor

    tarea = {'instruccion': instruccion, 'frecuencia': frecuencia, 'hora': entero('hora', 0, 23),
             'minuto': entero('minuto', 0, 59, 0), 'dias_semana': [], 'dia_mes': None}
    if frecuencia == 'semanal':
        dias = datos.get('dias_semana')
        if (not isinstance(dias, list) or not dias
                or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in dias)):
            raise TareaInvalida('dias_semana debe ser una lista de 0 (lunes) a 6 (domingo).')
        tarea['dias_semana'] = sorted(set(dias))
    if frecuencia == 'mensual':
        tarea['dia_mes'] = entero('dia_mes', 1, 28)
    return tarea


def toca(tarea: dict, dia) -> bool:
    if tarea['frecuencia'] == 'semanal':
        return dia.weekday() in tarea['dias_semana']
    if tarea['frecuencia'] == 'mensual':
        return dia.day == tarea['dia_mes']
    return True


def proxima(tarea: dict, desde: datetime) -> datetime:
    """La siguiente ejecución estrictamente después de ``desde`` (hora de México)."""
    local = desde.astimezone(ZONA)
    for n in range(0, 63):
        dia = local.date() + timedelta(days=n)
        cuando = datetime(dia.year, dia.month, dia.day, tarea['hora'], tarea['minuto'], tzinfo=ZONA)
        if cuando > local and toca(tarea, dia):
            return cuando
    raise TareaInvalida('No hay una próxima ejecución en los siguientes dos meses.')


def descripcion(tarea: dict) -> str:
    hora = f'{tarea["hora"]:02d}:{tarea["minuto"]:02d}'
    if tarea['frecuencia'] == 'semanal':
        dias = [DIAS[d] for d in tarea['dias_semana']]
        texto = 'cada ' + (', '.join(dias[:-1]) + ' y ' + dias[-1] if len(dias) > 1 else dias[0])
    elif tarea['frecuencia'] == 'mensual':
        texto = f'el día {tarea["dia_mes"]} de cada mes'
    else:
        texto = 'todos los días'
    return f'{texto} a las {hora}'


def _dias(texto) -> list[int]:
    return [int(d) for d in str(texto or '').split(',') if d.strip().isdigit()]


def _fila_a_tarea(fila) -> dict:
    tarea = {'id': fila[0], 'instruccion': fila[1], 'frecuencia': fila[2], 'dias_semana': _dias(fila[3]),
             'dia_mes': fila[4], 'hora': fila[5], 'minuto': fila[6],
             'proxima': fila[7].astimezone(ZONA).isoformat() if fila[7] else None}
    tarea['descripcion'] = descripcion(tarea)
    return tarea


COLUMNAS = 'id, instruccion, frecuencia, dias_semana, dia_mes, hora, minuto, proxima'


def crear_blueprint(get_db_connection, asegurar_esquema):
    bp = Blueprint('tareas', __name__, url_prefix='/api')

    def esquema(cur):
        asegurar_esquema(cur)
        cur.execute(SQL_ESQUEMA)

    def usuario_id():
        usuario = getattr(g, 'usuario', None)
        return usuario['id'] if usuario else None

    @bp.route('/tareas', methods=['GET'])
    def listar_tareas():
        uid = usuario_id()
        if uid is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute(f'SELECT {COLUMNAS} FROM bot_tareas WHERE usuario_id = %s ORDER BY id', (uid,))
                filas = cur.fetchall()
            conn.commit()
        return jsonify({'tareas': [_fila_a_tarea(f) for f in filas], 'maximo': MAX_TAREAS})

    @bp.route('/tareas', methods=['POST'])
    def crear_tarea():
        uid = usuario_id()
        if uid is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        try:
            tarea = validar(request.get_json(silent=True))
            siguiente = proxima(tarea, datetime.now(ZONA))
        except TareaInvalida as error:
            return jsonify({'error': str(error)}), 400
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute('SELECT COUNT(*) FROM bot_tareas WHERE usuario_id = %s', (uid,))
                if cur.fetchone()[0] >= MAX_TAREAS:
                    return jsonify({'error': f'Ya tienes {MAX_TAREAS} tareas; borra alguna primero.'}), 409
                cur.execute(
                    f"""INSERT INTO bot_tareas (usuario_id, instruccion, frecuencia, dias_semana, dia_mes, hora,
                                                minuto, proxima)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {COLUMNAS}""",
                    (uid, tarea['instruccion'], tarea['frecuencia'], ','.join(map(str, tarea['dias_semana'])),
                     tarea['dia_mes'], tarea['hora'], tarea['minuto'], siguiente),
                )
                fila = cur.fetchone()
            conn.commit()
        return jsonify(_fila_a_tarea(fila)), 201

    @bp.route('/tareas/<int:tarea_id>', methods=['DELETE'])
    def borrar_tarea(tarea_id):
        uid = usuario_id()
        if uid is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute('DELETE FROM bot_tareas WHERE id = %s AND usuario_id = %s RETURNING id', (tarea_id, uid))
                borrada = cur.fetchone()
            conn.commit()
        if not borrada:
            return jsonify({'error': 'Tarea no encontrada.'}), 404
        return jsonify({'ok': True})

    @bp.route('/telegram/interno/tareas/turno', methods=['POST'])
    @telegram_api.exigir_bot
    def interno_turno():
        """Las tareas que ya tocan; su próxima ejecución se calcula y guarda antes de devolverlas."""
        ahora = datetime.now(ZONA)
        salida = []
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                esquema(cur)
                cur.execute(
                    f"""SELECT t.id, t.instruccion, t.frecuencia, t.dias_semana, t.dia_mes, t.hora, t.minuto,
                               t.proxima, v.telegram_id
                        FROM bot_tareas t JOIN telegram_vinculos v ON v.usuario_id = t.usuario_id
                        WHERE t.proxima <= now()
                        ORDER BY t.proxima
                        LIMIT 50
                        FOR UPDATE OF t SKIP LOCKED""")
                for fila in cur.fetchall():
                    tarea = _fila_a_tarea(fila[:8])
                    cur.execute('UPDATE bot_tareas SET proxima = %s, ultima = now() WHERE id = %s',
                                (proxima(tarea, ahora), tarea['id']))
                    salida.append({'id': tarea['id'], 'telegram_id': fila[8], 'instruccion': tarea['instruccion'],
                                   'descripcion': tarea['descripcion']})
            conn.commit()
        return jsonify({'tareas': salida})

    return bp
