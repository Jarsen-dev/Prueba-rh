"""Avisos del día: lo que conviene saber hoy, calculado por reglas (sin IA).

``GET /api/avisos`` devuelve las secciones que la cuenta puede ver:

- admin: contratos que vencen pronto, quién regresa o empieza una incidencia hoy
  o mañana, y candidatos sin asignar desde hace días (con nombre);
- reclutamiento: solo conteos de candidatos (no tiene acceso a la lista de pendientes).

El fin de contrato se calcula igual que ``frontend/documentos/contrato.html``:
fecha de ingreso + 67 días hábiles (lunes a viernes).
"""

from datetime import date, timedelta

from flask import Blueprint, g, jsonify, request

ZONA = 'America/Mexico_City'
DIAS_HABILES_CONTRATO = 67
#: Contratos que pueden vencer en la ventana: 67 hábiles son ~94 naturales.
DIAS_BUSQUEDA_CONTRATO = 120
COLUMNAS_FECHA_INGRESO = ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta')
MAX_LISTA = 30


def sumar_dias_habiles(inicio: date, dias: int) -> date:
    fecha = inicio
    while dias > 0:
        fecha += timedelta(days=1)
        if fecha.weekday() < 5:
            dias -= 1
    return fecha


def fin_de_contrato(ingreso: date) -> date:
    return sumar_dias_habiles(ingreso, DIAS_HABILES_CONTRATO)


def _como_fecha(valor):
    if valor is None:
        return None
    return valor.date() if hasattr(valor, 'date') and callable(valor.date) else valor


def _entero(valor, defecto, minimo, maximo):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(maximo, numero))


def contratos_por_vencer(filas, hoy: date, dias: int) -> list[dict]:
    """filas: (registro_id, nombre, no_empleado, empresa, fecha_ingreso)."""
    limite = hoy + timedelta(days=dias)
    salida = []
    for registro_id, nombre, no_empleado, empresa, ingreso in filas:
        ingreso = _como_fecha(ingreso)
        if ingreso is None:
            continue
        fin = fin_de_contrato(ingreso)
        if hoy <= fin <= limite:
            salida.append({'registro_id': registro_id, 'nombre_completo': nombre, 'no_empleado': no_empleado,
                           'empresa': empresa, 'fecha_ingreso': ingreso.isoformat(), 'fin_contrato': fin.isoformat(),
                           'dias_restantes': (fin - hoy).days})
    salida.sort(key=lambda c: (c['fin_contrato'], c['nombre_completo'] or ''))
    return salida


def _columnas(cur, tabla):
    cur.execute('SELECT column_name FROM information_schema.columns WHERE table_name = %s', (tabla,))
    return {fila[0] for fila in cur.fetchall()}


def _activo(columnas):
    return ("COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') = 'ACTIVO'"
            if 'estatus' in columnas else 'TRUE')


def _contratos(cur, columnas, hoy, dias):
    fecha_col = next((c for c in COLUMNAS_FECHA_INGRESO if c in columnas), None)
    if fecha_col is None:
        return []
    cur.execute(
        f"""SELECT DISTINCT ON (r.id) r.id, r.nombre_completo, i.no_empleado, i.empresa, r.{fecha_col}
            FROM registro r JOIN ingresos_puesto i ON i.registro_id = r.id
            WHERE {_activo(columnas)} AND r.{fecha_col} >= %s
            ORDER BY r.id, i.id DESC""",
        (hoy - timedelta(days=DIAS_BUSQUEDA_CONTRATO),),
    )
    return contratos_por_vencer(cur.fetchall(), hoy, dias)


def _incidencias(cur, columnas, campo, hoy):
    cur.execute(
        f"""SELECT r.id, r.nombre_completo, ie.no_empleado, ie.tipo, ie.{campo}
            FROM incidencias_empleado ie JOIN registro r ON r.id = ie.registro_id
            WHERE {_activo(columnas)} AND ie.{campo} BETWEEN %s AND %s
            ORDER BY ie.{campo}, r.nombre_completo""",
        (hoy, hoy + timedelta(days=1)),
    )
    return [{'registro_id': f[0], 'nombre_completo': f[1], 'no_empleado': f[2], 'tipo': f[3],
             'fecha': _como_fecha(f[4]).isoformat()} for f in cur.fetchall()]


def _candidatos(cur, hoy, dias, con_nombres):
    cur.execute(
        """SELECT r.id, r.nombre_completo, r.numero_temporal, r.fecha_registro
           FROM registro r
           WHERE NOT EXISTS (SELECT 1 FROM ingresos_puesto ip WHERE ip.registro_id = r.id)
             AND r.fecha_registro <= %s
           ORDER BY r.fecha_registro, r.id""",
        (hoy - timedelta(days=dias),),
    )
    filas = cur.fetchall()
    cur.execute('SELECT COUNT(*) FROM registro WHERE fecha_registro = %s', (hoy - timedelta(days=1),))
    datos = {'sin_asignar': len(filas), 'registrados_ayer': int(cur.fetchone()[0]), 'dias': dias}
    if con_nombres:
        datos['lista'] = [{'id': f[0], 'nombre_completo': f[1], 'numero_temporal': f[2],
                           'dias_esperando': (hoy - _como_fecha(f[3])).days} for f in filas[:MAX_LISTA]]
    return datos


def crear_blueprint(get_db_connection):
    bp = Blueprint('avisos', __name__, url_prefix='/api')

    @bp.route('/avisos', methods=['GET'])
    def avisos_del_dia():
        usuario = getattr(g, 'usuario', None)
        if usuario is None:
            return jsonify({'error': 'Inicia sesión para continuar.'}), 401
        permisos = set(usuario.get('permisos') or [])
        dias_contrato = _entero(request.args.get('dias_contrato'), 7, 1, 60)
        dias_candidato = _entero(request.args.get('dias_candidato'), 7, 1, 90)
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT (now() AT TIME ZONE %s)::date', (ZONA,))
                hoy = cur.fetchone()[0]
                columnas = _columnas(cur, 'registro')
                datos = {'fecha': hoy.isoformat()}
                if 'admin' in permisos:
                    datos['contratos'] = _contratos(cur, columnas, hoy, dias_contrato)
                    datos['contratos_dias'] = dias_contrato
                    datos['reingresos'] = _incidencias(cur, columnas, 'fecha_reingreso', hoy)
                    datos['inicios'] = _incidencias(cur, columnas, 'fecha_inicio', hoy)
                if permisos & {'admin', 'reclutamiento'}:
                    datos['candidatos'] = _candidatos(cur, hoy, dias_candidato, con_nombres='admin' in permisos)
        return jsonify(datos)

    return bp
