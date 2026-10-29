import os
import re
import json
import base64
import tempfile
import traceback
import mimetypes
from copy import copy
from datetime import datetime
import psycopg2
import openpyxl
from flask import Flask, jsonify, request, send_from_directory, send_file
from io import BytesIO
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)


@app.after_request
def agregar_headers_cors(response):
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
    return response


load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')), override=True)


def is_db_write_locked():
    return str(os.getenv("DB_WRITE_LOCK", "0")).strip() == "1"


@app.before_request
def enforce_db_write_lock():
    if request.method == 'OPTIONS':
        return None

    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and is_db_write_locked():
        return jsonify({
            'error': 'DB_WRITE_LOCK is enabled. Write operations are blocked.',
            'db_write_lock': '1',
        }), 503

    return None


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok'}), 200


def asegurar_tabla_incidencias(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS incidencias_empleado (
            id BIGSERIAL PRIMARY KEY,
            registro_id INTEGER NOT NULL REFERENCES registro(id) ON DELETE CASCADE,
            no_empleado VARCHAR(50) NOT NULL,
            tipo VARCHAR(40) NOT NULL,
            motivo TEXT NOT NULL,
            fecha_inicio DATE,
            fecha_reingreso DATE,
            dias SMALLINT CHECK (dias BETWEEN 1 AND 999),
            opcion VARCHAR(20),
            hora TIME,
            motivos JSONB NOT NULL DEFAULT '[]'::jsonb,
            otro_motivo TEXT,
            archivo_nombre TEXT,
            archivo_mime VARCHAR(100),
            archivo_contenido BYTEA,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incidencias_empleado_registro_created
        ON incidencias_empleado (registro_id, created_at, id)
        """
    )


def serializar_incidencia(row):
    hora = row[7]
    return {
        'id': row[0],
        'type': row[1],
        'motivo': row[2] or '',
        'fecha_inicio': row[3].isoformat() if row[3] else None,
        'fecha_reingreso': row[4].isoformat() if row[4] else None,
        'dias': row[5],
        'opcion': row[6],
        'hora': (
            hora.strftime('%H:%M')
            if hasattr(hora, 'strftime')
            else str(hora)[:5] if hora else None
        ),
        'motivos': row[8] or [],
        'otro_motivo': row[9] or '',
        'archivo_nombre': row[10],
        'createdAt': row[11].isoformat() if row[11] else None,
    }


@app.route('/api/empleados/<int:registro_id>/incidencias', methods=['GET', 'POST'])
def incidencias_empleado(registro_id):
    if request.method == 'GET':
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    asegurar_tabla_incidencias(cur)
                    cur.execute(
                        """
                        SELECT id, tipo, motivo, fecha_inicio, fecha_reingreso,
                               dias, opcion, hora, motivos, otro_motivo, archivo_nombre,
                               created_at
                        FROM incidencias_empleado
                        WHERE registro_id = %s
                        ORDER BY created_at ASC, id ASC
                        """,
                        (registro_id,),
                    )
                    incidencias = [serializar_incidencia(row) for row in cur.fetchall()]
            return jsonify({'incidencias': incidencias}), 200
        except psycopg2.Error as error:
            print(f"Error al consultar incidencias del empleado {registro_id}: {error}")
            return jsonify({'error': 'No se pudieron consultar las incidencias.'}), 500

    datos = request.form.to_dict() if request.form else request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify({'error': 'Se requieren datos de incidencia válidos.'}), 400

    tipo = str(datos.get('tipo') or '').strip()
    if tipo not in {'entrada-salida', 'vacaciones', 'incapacidad', 'suspension', 'carta-suspension', 'indisciplina'}:
        return jsonify({'error': 'El tipo de incidencia no es válido.'}), 400

    try:
        detalles = json.loads(datos.get('detalles') or '{}')
    except (TypeError, ValueError):
        return jsonify({'error': 'Los detalles de la incidencia no tienen un formato válido.'}), 400
    if not isinstance(detalles, dict):
        return jsonify({'error': 'Los detalles de la incidencia deben ser un objeto.'}), 400

    motivo = str(datos.get('motivo') or '').strip()
    if len(motivo) > 1000:
        return jsonify({'error': 'El motivo no puede superar 1000 caracteres.'}), 400
    if tipo in {'entrada-salida', 'suspension', 'carta-suspension'} and not motivo:
        return jsonify({'error': 'El motivo de la incidencia es obligatorio.'}), 400
    if tipo == 'indisciplina':
        categoria = str(detalles.get('categoria') or '').strip()
        otro_motivo = str(detalles.get('otro_motivo') or '').strip()
        if not categoria or (categoria == 'otro' and not otro_motivo):
            return jsonify({'error': 'Selecciona un motivo o especifica otro motivo.'}), 400
        if categoria == 'otro':
            motivo = otro_motivo
        else:
            motivo = categoria
        detalles['otro_motivo'] = otro_motivo

    opcion = None
    hora = None
    motivos = detalles.get('motivos') or []
    if not isinstance(motivos, list) or not all(isinstance(item, str) for item in motivos):
        return jsonify({'error': 'La lista de motivos no es válida.'}), 400
    if tipo == 'entrada-salida':
        opcion = str(detalles.get('opcion') or '').strip()
        hora = str(detalles.get('hora') or '').strip()
        otro_motivo = str(detalles.get('otro_motivo') or '').strip()
        if opcion not in {'entrada', 'salida'}:
            return jsonify({'error': 'Selecciona entrada o salida.'}), 400
        if not motivos and not otro_motivo:
            return jsonify({'error': 'Selecciona un motivo o especifica otro motivo.'}), 400
        try:
            datetime.strptime(hora, '%H:%M')
        except ValueError:
            return jsonify({'error': 'La hora de entrada o salida no es válida.'}), 400
        detalles['otro_motivo'] = otro_motivo

    fecha_inicio = fecha_reingreso = dias = None

    def fecha_payload(valor, nombre):
        if not valor:
            return None
        try:
            return datetime.strptime(str(valor), '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f'La fecha {nombre} no tiene un formato válido.')

    try:
        fecha_inicio = fecha_payload(datos.get('fecha_inicio'), 'inicial')
        fecha_reingreso = fecha_payload(datos.get('fecha_reingreso'), 'final')
        if tipo == 'entrada-salida':
            fecha_inicio = fecha_inicio or datetime.now().date()
            fecha_reingreso = fecha_inicio
        elif tipo in {'suspension', 'carta-suspension'} and (not fecha_inicio or not fecha_reingreso):
            return jsonify({'error': 'Las fechas de suspensión o carta compromiso son obligatorias.'}), 400
        elif tipo in {'vacaciones', 'incapacidad'} and (not fecha_inicio or not fecha_reingreso):
            return jsonify({'error': 'Las fechas inicial y final son obligatorias.'}), 400
        if fecha_inicio and fecha_reingreso and fecha_reingreso < fecha_inicio:
            return jsonify({'error': 'La fecha final no puede ser anterior a la inicial.'}), 400
        if fecha_inicio and fecha_reingreso and tipo in {'suspension', 'carta-suspension', 'vacaciones', 'incapacidad'}:
            dias = (fecha_reingreso - fecha_inicio).days + 1
            if tipo in {'suspension', 'carta-suspension'}:
                dias -= 1
            if not 1 <= dias <= 999:
                return jsonify({'error': 'El periodo debe contener entre 1 y 999 días.'}), 400
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    detalles['dias'] = dias
    archivo = request.files.get('archivo')
    archivo_nombre = archivo_mime = archivo_contenido = None
    if archivo and archivo.filename:
        archivo_nombre = secure_filename(archivo.filename)
        archivo_extension = os.path.splitext(archivo_nombre)[1].lower()
        tipos_archivo = {
            '.pdf': 'application/pdf',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
        }
        if archivo_extension not in tipos_archivo:
            return jsonify({'error': 'El archivo debe ser PDF, PNG o JPG.'}), 400
        archivo_contenido = archivo.read(10 * 1024 * 1024 + 1)
        if len(archivo_contenido) > 10 * 1024 * 1024:
            return jsonify({'error': 'El archivo no puede superar 10 MB.'}), 413
        archivo_mime = tipos_archivo[archivo_extension]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_incidencias(cur)
                cur.execute(
                    """
                    SELECT ip.no_empleado
                    FROM registro AS r
                    LEFT JOIN ingresos_puesto AS ip ON ip.registro_id = r.id
                    WHERE r.id = %s
                    ORDER BY ip.id DESC NULLS LAST
                    LIMIT 1
                    """,
                    (registro_id,),
                )
                empleado = cur.fetchone()
                if not empleado:
                    return jsonify({'error': 'No se encontró al empleado.'}), 404
                no_empleado = empleado[0]
                if not no_empleado:
                    return jsonify({'error': 'El empleado no tiene un número de empleado registrado.'}), 409

                cur.execute(
                    """
                    INSERT INTO incidencias_empleado (
                        registro_id, no_empleado, tipo, motivo, fecha_inicio, fecha_reingreso,
                        dias, opcion, hora, motivos, otro_motivo, archivo_nombre,
                        archivo_mime, archivo_contenido
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    RETURNING id, tipo, motivo, fecha_inicio, fecha_reingreso,
                              dias, opcion, hora, motivos, otro_motivo, archivo_nombre,
                              created_at
                    """,
                    (
                        registro_id,
                        str(no_empleado),
                        tipo,
                        motivo or tipo,
                        fecha_inicio,
                        fecha_reingreso,
                        dias,
                        opcion,
                        hora or None,
                        json.dumps(motivos, ensure_ascii=False),
                        detalles.get('otro_motivo') or None,
                        archivo_nombre,
                        archivo_mime,
                        archivo_contenido,
                    ),
                )
                incidencia = serializar_incidencia(cur.fetchone())
            conn.commit()
        return jsonify({'incidencia': incidencia}), 201
    except psycopg2.Error as error:
        print(f"Error al guardar incidencia del empleado {registro_id}: {error}")
        return jsonify({'error': 'No se pudo guardar la incidencia.'}), 500


@app.route('/api/empleados/<int:registro_id>/incidencias/<int:incidencia_id>', methods=['DELETE'])
def eliminar_incidencia_empleado(registro_id, incidencia_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_incidencias(cur)
                cur.execute(
                    """
                    DELETE FROM incidencias_empleado
                    WHERE id = %s
                      AND registro_id = %s
                    RETURNING id
                    """,
                    (incidencia_id, registro_id),
                )
                eliminada = cur.fetchone()
            conn.commit()
        if not eliminada:
            return jsonify({
                'error': 'La incidencia no existe o no se puede eliminar.'
            }), 409
        return jsonify({'ok': True}), 200
    except psycopg2.Error as error:
        print(f"Error al eliminar incidencia {incidencia_id}: {error}")
        return jsonify({'error': 'No se pudo eliminar la incidencia.'}), 500


@app.route('/api/empleados/<int:registro_id>/incidencias/<int:incidencia_id>/archivo', methods=['GET'])
def descargar_archivo_incidencia(registro_id, incidencia_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT archivo_nombre, archivo_mime, archivo_contenido
                    FROM incidencias_empleado
                    WHERE id = %s AND registro_id = %s
                    """,
                    (incidencia_id, registro_id),
                )
                archivo = cur.fetchone()
        if not archivo or not archivo[2]:
            return jsonify({'error': 'El archivo de la incidencia no existe.'}), 404
        return send_file(
            BytesIO(bytes(archivo[2])),
            as_attachment=True,
            download_name=secure_filename(archivo[0] or 'archivo-incidencia'),
            mimetype=archivo[1] or 'application/octet-stream',
        )
    except psycopg2.Error as error:
        print(f"Error al consultar el archivo de incidencia {incidencia_id}: {error}")
        return jsonify({'error': 'No se pudo consultar el archivo de la incidencia.'}), 500


DEFAULT_DOCS_ROOT = os.getenv(
    "EMPLOYEE_DOCS_ROOT",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "uploads")),
)


def obtener_directorio_empresa(empresa):
    configured_root = app.config.get("UPLOAD_ROOT") or DEFAULT_DOCS_ROOT
    texto = (empresa or '').strip().upper()
    if 'KRONOS' in texto:
        override = os.getenv("KRONOS_DOCS_DIR")
        if override:
            return asegurar_directorio(os.path.abspath(override))
        return asegurar_directorio(os.path.abspath(os.path.join(configured_root, "KRONOS")))

    override = os.getenv("CHEONG_WOON_DOCS_DIR")
    if override:
        return asegurar_directorio(os.path.abspath(override))
    return asegurar_directorio(os.path.abspath(os.path.join(configured_root, "CHEONG_WOON")))

DOC_FILENAME_KEYS = {
    'ine': 'INE',
    'comprobante_domicilio': 'COMPROBANTE_DOMICILIO',
    'acta_nacimiento': 'ACTA_NACIMIENTO',
    'curp_doc': 'CURP',
    'rfc_doc': 'RFC',
    'nss_doc': 'NSS',
    'comprobante_estudios': 'COMPROBANTE_ESTUDIOS',
    'infonavit_doc': 'INFONAVIT',
    'carta_autorizacion': 'CARTA_AUTORIZACION',
    'contrato_firmado': 'CONTRATO_FIRMADO',
}

DOC_ALLOWED_MIME_TYPES = {
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/webp',
}

def normalizar_empresa(empresa):
    texto = (empresa or '').strip().upper()
    if 'KRONOS' in texto:
        return 'KRONOS', obtener_directorio_empresa(texto)
    return 'CHEONG WOON', obtener_directorio_empresa(texto)


def limpiar_fragmento(texto):
    valor = (texto or '').strip().upper()
    valor = re.sub(r'[^A-Z0-9]+', '_', valor)
    valor = re.sub(r'_+', '_', valor).strip('_')
    return valor or 'SIN_DATO'


def extension_desde_nombre(nombre_archivo):
    _, extension = os.path.splitext(nombre_archivo or '')
    return extension.lower() if extension else '.dat'


def construir_nombre_documento(curp, empresa, doc_key, original_filename):
    empresa_normalizada, _ = normalizar_empresa(empresa)
    curp_texto = limpiar_fragmento(curp)
    empresa_texto = limpiar_fragmento(empresa_normalizada)
    doc_texto = DOC_FILENAME_KEYS.get(doc_key, limpiar_fragmento(doc_key))
    extension = extension_desde_nombre(original_filename)

    return f"{curp_texto}-{empresa_texto}-{doc_texto}{extension}"


def asegurar_directorio(directorio):
    directorio = os.path.abspath(directorio)
    try:
        os.makedirs(directorio, exist_ok=True)
        app.config["UPLOAD_ROOT"] = os.path.dirname(directorio)
        return directorio
    except OSError:
        fallback_candidates = []
        env_candidates = [os.getenv("EMPLOYEE_DOCS_ROOT"), os.getenv("TMPDIR"), os.getenv("TEMP"), os.getenv("TMP")]
        for value in env_candidates:
            if value:
                fallback_candidates.append(os.path.abspath(value))
        fallback_candidates.append(os.path.abspath(os.path.join(tempfile.gettempdir(), "prueba-rh-uploads")))

        for candidate in fallback_candidates:
            try:
                os.makedirs(candidate, exist_ok=True)
                if directorio != candidate:
                    subdir = os.path.basename(directorio)
                    if subdir and subdir.lower() != "uploads":
                        fallback_dir = os.path.join(candidate, subdir)
                        os.makedirs(fallback_dir, exist_ok=True)
                        app.config["UPLOAD_ROOT"] = candidate
                        return fallback_dir
                app.config["UPLOAD_ROOT"] = candidate
                return candidate
            except OSError:
                continue

        fallback_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "uploads"))
        os.makedirs(fallback_dir, exist_ok=True)
        app.config["UPLOAD_ROOT"] = os.path.dirname(fallback_dir)
        return fallback_dir


def buscar_documentos(curp, empresa):
    curp_texto = limpiar_fragmento(curp)
    empresa_texto = limpiar_fragmento(normalizar_empresa(empresa)[0])
    base_dir = asegurar_directorio(obtener_directorio_empresa(empresa))
    if not os.path.isdir(base_dir):
        return {}

    documentos = {}
    prefijo = f"{curp_texto}-{empresa_texto}-"

    for file_name in os.listdir(base_dir):
        nombre_upper = file_name.upper()
        if not nombre_upper.startswith(prefijo):
            continue

        nombre_base, _ = os.path.splitext(file_name)
        parte_concepto = nombre_base[len(prefijo):]
        doc_key = None
        for key, value in DOC_FILENAME_KEYS.items():
            if value == parte_concepto:
                doc_key = key
                break

        if not doc_key:
            continue

        documentos[doc_key] = {
            'fileName': file_name,
            'previewUrl': f"/api/documentos/{empresa_texto}/{file_name}",
        }

    return documentos


def asegurar_columna_documentos(cur):
    cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS documentos_papeleria TEXT")


def parsear_documentos_papeleria(raw_value):
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return raw_value

    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def resolver_registro_para_documentos(cur, registro_id=None, curp=None, empresa=None):
    if registro_id:
        try:
            rid = int(str(registro_id).strip())
            cur.execute("SELECT id FROM registro WHERE id = %s LIMIT 1", (rid,))
            row = cur.fetchone()
            return row[0] if row else None
        except (TypeError, ValueError):
            return None

    curp_texto = (curp or '').strip().upper()
    if not curp_texto:
        return None

    empresa_texto = limpiar_fragmento(normalizar_empresa(empresa or '')[0]) if empresa else ''
    if empresa_texto:
        cur.execute(
            """
            SELECT r.id
            FROM registro r
            LEFT JOIN ingresos_puesto ip ON ip.registro_id = r.id
            WHERE UPPER(COALESCE(r.curp, '')) = %s
              AND UPPER(REPLACE(COALESCE(ip.empresa, ''), ' ', '_')) = %s
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (curp_texto, empresa_texto),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    cur.execute(
        """
        SELECT id
        FROM registro
        WHERE UPPER(COALESCE(curp, '')) = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (curp_texto,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def obtener_curp_empresa_para_registro(cur, registro_id):
    cur.execute(
        """
        SELECT r.curp, COALESCE(ip.empresa, '')
        FROM registro r
        LEFT JOIN ingresos_puesto ip ON ip.registro_id = r.id
        WHERE r.id = %s
        ORDER BY ip.id DESC NULLS LAST
        LIMIT 1
        """,
        (registro_id,),
    )
    row = cur.fetchone()
    if not row:
        return '', ''
    return (row[0] or '').strip(), (row[1] or '').strip()


def leer_documentos_papeleria(cur, registro_id):
    cur.execute("SELECT documentos_papeleria FROM registro WHERE id = %s LIMIT 1", (registro_id,))
    row = cur.fetchone()
    return parsear_documentos_papeleria(row[0] if row else None)


def serializar_documentos_para_respuesta(registro_id, documentos_guardados):
    documentos = {}
    for doc_key, doc_data in (documentos_guardados or {}).items():
        if doc_key not in DOC_FILENAME_KEYS or not isinstance(doc_data, dict):
            continue
        if not doc_data.get('base64Content'):
            continue
        documentos[doc_key] = {
            'fileName': doc_data.get('fileName') or '',
            'mimeType': doc_data.get('mimeType') or 'application/octet-stream',
            'previewUrl': f"/api/documentos-db/{registro_id}/{doc_key}",
        }
    return documentos

def obtener_ruta_documento(empresa, file_name):
    _, base_dir = normalizar_empresa(empresa)
    return os.path.join(base_dir, file_name)


def get_db_connection():
    host = os.getenv("POSTGRES_HOST", "postgres")
    if host == "postgres" and not os.path.exists("/.dockerenv"):
        host = "localhost"

    return psycopg2.connect(
        host=host,
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "rh_system"),
        user=os.getenv("POSTGRES_USER", "rh_app"),
        password=os.getenv("POSTGRES_PASSWORD", "S1s73m4s!"),
        options="-c lc_messages=C",
    )


def asegurar_tabla_usuarios_acceso(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios_acceso (
            id SERIAL PRIMARY KEY,
            usuario VARCHAR(100) NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            permisos JSONB NOT NULL DEFAULT '[]'::jsonb,
            nombre_notificacion VARCHAR(150),
            telefono_notificacion VARCHAR(30),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def normalizar_permisos(permisos):
    permitidos = {'admin', 'reclutamiento', 'notificaciones'}
    if not isinstance(permisos, list):
        return []
    permisos_normalizados = ['admin' if permiso == 'recursos_humanos' else permiso for permiso in permisos]
    return list(dict.fromkeys(permiso for permiso in permisos_normalizados if permiso in permitidos))


def usuario_acceso_dict(row):
    permisos = row[3] if isinstance(row[3], list) else json.loads(row[3] or '[]')
    return {
        'id': row[0],
        'usuario': row[1],
        'permisos': normalizar_permisos(permisos),
        'nombre_notificacion': row[4] or '',
        'telefono_notificacion': row[5] or '',
    }


@app.route('/api/access-users', methods=['GET'])
def listar_usuarios_acceso():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_usuarios_acceso(cur)
                cur.execute(
                    """
                    SELECT id, usuario, password_hash, permisos,
                           nombre_notificacion, telefono_notificacion
                    FROM usuarios_acceso
                    ORDER BY usuario
                    """
                )
                usuarios = [usuario_acceso_dict(row) for row in cur.fetchall()]
            conn.commit()
        return jsonify({'usuarios': usuarios})
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/access-users', methods=['POST'])
def crear_usuario_acceso():
    datos = request.get_json(silent=True) or {}
    usuario = str(datos.get('usuario', '')).strip()
    password = str(datos.get('password', '')).strip()
    permisos = normalizar_permisos(datos.get('permisos'))

    if not usuario or not password or not permisos:
        return jsonify({'error': 'Usuario, contraseña y al menos un permiso son obligatorios.'}), 400

    nombre_notificacion = str(datos.get('nombre_notificacion', '')).strip() if 'notificaciones' in permisos else ''
    telefono_notificacion = str(datos.get('telefono_notificacion', '')).strip() if 'notificaciones' in permisos else ''
    if 'notificaciones' in permisos and (not nombre_notificacion or not telefono_notificacion):
        return jsonify({'error': 'Nombre y teléfono son obligatorios para Notificaciones.'}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_usuarios_acceso(cur)
                cur.execute(
                    """
                    INSERT INTO usuarios_acceso (
                        usuario, password_hash, permisos,
                        nombre_notificacion, telefono_notificacion
                    ) VALUES (%s, %s, %s::jsonb, %s, %s)
                    RETURNING id, usuario, password_hash, permisos,
                              nombre_notificacion, telefono_notificacion
                    """,
                    (usuario, generate_password_hash(password), json.dumps(permisos),
                     nombre_notificacion, telefono_notificacion),
                )
                creado = usuario_acceso_dict(cur.fetchone())
            conn.commit()
        return jsonify(creado), 201
    except psycopg2.IntegrityError:
        return jsonify({'error': 'Ese usuario ya está registrado.'}), 409
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/access-users/<int:usuario_id>', methods=['PUT'])
def actualizar_usuario_acceso(usuario_id):
    datos = request.get_json(silent=True) or {}
    usuario = str(datos.get('usuario', '')).strip()
    password = str(datos.get('password', '')).strip()
    permisos = normalizar_permisos(datos.get('permisos'))

    if not usuario or not permisos:
        return jsonify({'error': 'Usuario y al menos un permiso son obligatorios.'}), 400

    nombre_notificacion = str(datos.get('nombre_notificacion', '')).strip() if 'notificaciones' in permisos else ''
    telefono_notificacion = str(datos.get('telefono_notificacion', '')).strip() if 'notificaciones' in permisos else ''
    if 'notificaciones' in permisos and (not nombre_notificacion or not telefono_notificacion):
        return jsonify({'error': 'Nombre y teléfono son obligatorios para Notificaciones.'}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_usuarios_acceso(cur)
                if password:
                    cur.execute(
                        """UPDATE usuarios_acceso
                           SET usuario = %s, password_hash = %s, permisos = %s::jsonb,
                               nombre_notificacion = %s,
                               telefono_notificacion = %s, updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s
                           RETURNING id, usuario, password_hash, permisos,
                                     nombre_notificacion, telefono_notificacion""",
                        (usuario, generate_password_hash(password), json.dumps(permisos),
                         nombre_notificacion, telefono_notificacion, usuario_id),
                    )
                else:
                    cur.execute(
                        """UPDATE usuarios_acceso
                          SET usuario = %s, permisos = %s::jsonb,
                               nombre_notificacion = %s, telefono_notificacion = %s,
                               updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s
                          RETURNING id, usuario, password_hash, permisos,
                                     nombre_notificacion, telefono_notificacion""",
                        (usuario, json.dumps(permisos), nombre_notificacion,
                         telefono_notificacion, usuario_id),
                    )
                actualizado = cur.fetchone()
            conn.commit()
        if not actualizado:
            return jsonify({'error': 'Usuario no encontrado.'}), 404
        return jsonify(usuario_acceso_dict(actualizado))
    except psycopg2.IntegrityError:
        return jsonify({'error': 'Ese usuario ya está registrado.'}), 409
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/access-users/<int:usuario_id>', methods=['DELETE'])
def eliminar_usuario_acceso(usuario_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_usuarios_acceso(cur)
                cur.execute('DELETE FROM usuarios_acceso WHERE id = %s', (usuario_id,))
                eliminado = cur.rowcount
            conn.commit()
        if not eliminado:
            return jsonify({'error': 'Usuario no encontrado.'}), 404
        return jsonify({'ok': True})
    except Exception as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/auth/login', methods=['POST'])
def iniciar_sesion_acceso():
    datos = request.get_json(silent=True) or {}
    usuario = str(datos.get('usuario', '')).strip()
    password = str(datos.get('password', '')).strip()

    if not usuario or not password:
        return jsonify({'error': 'Usuario y contraseña son obligatorios.'}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_usuarios_acceso(cur)
                cur.execute(
                    """SELECT id, usuario, password_hash, permisos,
                              nombre_notificacion, telefono_notificacion
                       FROM usuarios_acceso WHERE usuario = %s LIMIT 1""",
                    (usuario,),
                )
                encontrado = cur.fetchone()
            conn.commit()
        if not encontrado or not check_password_hash(encontrado[2], password):
            return jsonify({'error': 'Usuario o contraseña incorrectos.'}), 401
        return jsonify(usuario_acceso_dict(encontrado))
    except Exception as error:
        return jsonify({'error': str(error)}), 500


def obtener_columnas(cur, tabla):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        """,
        (tabla,),
    )
    return {row[0] for row in cur.fetchall()}


def obtener_empleado_dict(cur, registro_id):
    cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS procedencia VARCHAR(150)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_credenciales (
            registro_id INTEGER PRIMARY KEY REFERENCES registro(id) ON DELETE CASCADE,
            contrasena TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cols_registro = obtener_columnas(cur, 'registro')

    fecha_col = None
    for candidato in ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta'):
        if candidato in cols_registro:
            fecha_col = candidato
            break

    fecha_select = f"r.{fecha_col} AS fecha_ingreso" if fecha_col else "NULL::timestamp AS fecha_ingreso"
    fotografia_select = "r.fotografia_base64 AS fotografia_base64" if 'fotografia_base64' in cols_registro else "NULL::text AS fotografia_base64"
    procedencia_select = "r.procedencia AS procedencia" if 'procedencia' in cols_registro else "NULL::text AS procedencia"

    cur.execute(
        f"""
        SELECT
            r.id,
            r.nombre_completo,
            r.fecha_nacimiento,
            r.edad,
            r.genero,
            r.estado_civil,
            r.nacionalidad,
            {procedencia_select},
            r.curp,
            r.rfc,
            r.nss,
            r.tel_movil,
            r.correo,
            r.codigo_postal,
            r.colonia,
            r.calle,
            r.num_ext,
            r.num_int,
            r.municipio,
            r.estado,
            r.nombre_emergencia,
            r.parentesco_emergencia,
            r.telefono_emergencia,
            r.firma_archivo,
            {fotografia_select},
            {fecha_select},
            ip.empresa,
            ip.no_empleado,
            ip.personal,
            ip.puesto,
            ip.area,
            ip.turno,
            ip.salario,
            ip.pago,
            cc.contrasena
        FROM registro r
        LEFT JOIN ingresos_puesto ip ON ip.registro_id = r.id
        LEFT JOIN chatbot_credenciales cc ON cc.registro_id = r.id
        WHERE r.id = %s
        LIMIT 1
        """,
        (registro_id,),
    )
    row = cur.fetchone()

    if not row:
        return None

    return {
        'registro_id': row[0],
        'nombre_completo': row[1],
        'fecha_nacimiento': row[2].strftime('%Y-%m-%d') if row[2] else None,
        'edad': row[3],
        'genero': row[4],
        'estado_civil': row[5],
        'nacionalidad': row[6],
        'procedencia': row[7],
        'curp': row[8],
        'rfc': row[9],
        'nss': row[10],
        'tel_movil': row[11],
        'correo': row[12],
        'codigo_postal': row[13],
        'colonia': row[14],
        'calle': row[15],
        'num_ext': row[16],
        'num_int': row[17],
        'municipio': row[18],
        'estado': row[19],
        'nombre_emergencia': row[20],
        'parentesco_emergencia': row[21],
        'telefono_emergencia': row[22],
        'firma_archivo': row[23],
        'fotografia_base64': row[24],
        'fecha_ingreso': row[25].strftime('%Y-%m-%d') if row[25] else None,
        'empresa': row[26],
        'no_empleado': row[27],
        'personal': row[28],
        'puesto': row[29],
        'area': row[30],
        'turno': row[31],
        'salario': row[32],
        'pago': row[33],
        'contrasena_chatbot': row[34],
    }

def separar_numeracion(valor):
    texto = (valor or '').strip()
    if not texto:
        return None, None

    if '/' in texto:
        parte_ext, parte_int = texto.split('/', 1)
        return parte_ext.strip() or None, parte_int.strip() or None

    return texto, None


def separar_ubicacion(valor):
    texto = (valor or '').strip()
    if not texto:
        return None, None, None

    partes = [parte.strip() for parte in texto.split('/')]
    while len(partes) < 3:
        partes.append(None)

    return (
        partes[0] or None,
        partes[1] or None,
        partes[2] or None,
    )


@app.route('/guardar-registro', methods=['POST'])
@app.route('/api/guardar-registro', methods=['POST'])
def guardar_registro():
    datos = request.form.to_dict()
    if not datos:
        return ("ERROR: No se recibieron datos", 400)

    try:
        firma_png = datos.get('firma_png') or None
        fotografia_base64 = datos.get('fotografia_base64') or None

        query = """
        INSERT INTO registro (
            nombre_completo, fecha_nacimiento, edad, genero, estado_civil, nacionalidad, procedencia,
            curp, rfc, nss, tel_movil, correo, codigo_postal, colonia, calle,
            num_ext, num_int, municipio, estado, nombre_emergencia,
            parentesco_emergencia, telefono_emergencia, firma_archivo, fotografia_base64
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        values = (
            datos.get('nombre'), datos.get('fecha_nac'), datos.get('edad'), datos.get('genero'),
            datos.get('edo_civil'), datos.get('nacionalidad'), datos.get('procedencia'), datos.get('curp'), datos.get('rfc'),
            datos.get('nss'), datos.get('tel_movil'), datos.get('correo'), datos.get('cp'),
            datos.get('colonia'), datos.get('calle'), datos.get('num_ext'), datos.get('num_int'),
            datos.get('municipio'), datos.get('estado'), datos.get('nom_eme1'), datos.get('par_eme1'),
            datos.get('tel_eme1'), firma_png, fotografia_base64,
        )

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS fotografia_base64 TEXT")
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS procedencia VARCHAR(150)")
                cur.execute(query, values)
            conn.commit()

        return ("OK", 200)
    except Exception as e:
        print(f"Error: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/guardar-encuesta', methods=['POST'])
@app.route('/api/guardar-encuesta', methods=['POST'])
def guardar_encuesta():
    datos = request.form.to_dict()
    fuente = (datos.get('fuente') or '').strip()
    sub_fuente = (datos.get('sub_fuente') or '').strip()

    def normalizar_fuente(fuente_raw, sub_raw):
        f = (fuente_raw or '').strip().upper()
        s = (sub_raw or '').strip().upper()

        if f == 'FACEBOOK':
            return 'FACEBOOK'
        if f == 'POSTEO':
            return 'POSTEO'
        if f == 'VOLANTE':
            return 'VOLANTE'
        if f.startswith('RECOM'):
            return 'RECOMENDACION'
        if f == 'AGENCIA':
            if s == 'CW':
                return 'RECLU CW'
            if s == 'PURO':
                return 'RECLU PURO'
            return s or 'AGENCIA'

        return f or s or 'OTRO'

    fuente_norm = normalizar_fuente(fuente, sub_fuente)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "ALTER TABLE encuesta_reclutamiento "
                    "ADD COLUMN IF NOT EXISTS nombre_reclutador VARCHAR(255)"
                )
                cur.execute(
                    "ALTER TABLE encuesta_reclutamiento "
                    "ADD COLUMN IF NOT EXISTS nombre_empleado VARCHAR(200)"
                )
                cur.execute(
                    "ALTER TABLE encuesta_reclutamiento "
                    "ADD COLUMN IF NOT EXISTS registro_id INTEGER REFERENCES registro(id)"
                )
                cur.execute(
                    "ALTER TABLE encuesta_reclutamiento "
                    "ADD COLUMN IF NOT EXISTS sub_fuente VARCHAR(50)"
                )
                cur.execute(
                    """
                    SELECT id
                    FROM registro
                    WHERE UPPER(TRIM(curp)) = UPPER(TRIM(%s))
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    ((datos.get('curp') or '').strip(),),
                )
                registro = cur.fetchone()
                registro_id = registro[0] if registro else None
                cur.execute(
                    """
                    INSERT INTO encuesta_reclutamiento
                        (fuente, sub_fuente, fecha_registro, nombre_reclutador, nombre_empleado, registro_id)
                    VALUES (%s, %s, now(), %s, %s, %s)
                    """,
                    (
                        fuente_norm,
                        sub_fuente or None,
                        (datos.get('nombre_reclutador') or '').strip(),
                        (datos.get('nombre_empleado') or '').strip(),
                        registro_id,
                    )
                )
            conn.commit()
        return ("OK", 200)
    except Exception as e:
        print(f"ERROR: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/obtener-pendientes', methods=['GET'])
@app.route('/api/obtener-pendientes', methods=['GET'])
def obtener_pendientes():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.nombre_completo, r.id
                    FROM registro r
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ingresos_puesto ip
                        WHERE ip.registro_id = r.id
                    )
                    ORDER BY r.id DESC
                    """
                )
                rows = cur.fetchall()

        data = [
            {
                'nombre_completo': row[0],
                'id': row[1],
            }
            for row in rows
        ]
        return jsonify(data), 200
    except Exception as e:
        print(f"Error: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/eliminar-pendiente/<int:id>', methods=['DELETE'])
@app.route('/api/eliminar-pendiente/<id>', methods=['DELETE', 'OPTIONS'])
def eliminar_pendiente(id):
    if request.method == 'OPTIONS':
        return '', 200

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM registro
                    WHERE id = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM ingresos_puesto ip
                          WHERE ip.registro_id = registro.id
                      )
                    RETURNING id
                    """,
                    (id,),
                )
                eliminado = cur.fetchone()

            conn.commit()

        if not eliminado:
            return ("ERROR: El registro no existe o ya tiene un ingreso asignado", 409)
        return ("OK", 200)
    except Exception as e:
        return str(e), 500


@app.route('/api/eliminar-empleado/<int:registro_id>', methods=['DELETE', 'OPTIONS'])
def eliminar_empleado_completo(registro_id):
    if request.method == 'OPTIONS':
        return '', 200

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.curp,
                           COALESCE((
                               SELECT ip.empresa
                               FROM ingresos_puesto ip
                               WHERE ip.registro_id = r.id
                               ORDER BY ip.id DESC
                               LIMIT 1
                           ), 'CHEONG WOON') AS empresa
                    FROM registro r
                    WHERE r.id = %s
                    FOR UPDATE
                    """,
                    (registro_id,),
                )
                empleado = cur.fetchone()
                if not empleado:
                    return jsonify({'error': 'Empleado no encontrado.'}), 404

                curp, empresa = empleado
                cur.execute("DELETE FROM ingresos_puesto WHERE registro_id = %s", (registro_id,))
                cur.execute("DELETE FROM registro WHERE id = %s RETURNING id", (registro_id,))
                eliminado = cur.fetchone()

            conn.commit()

        archivos_pendientes = []
        if curp:
            empresa_normalizada, directorio = normalizar_empresa(empresa)
            prefijo = f"{limpiar_fragmento(curp)}-{limpiar_fragmento(empresa_normalizada)}-"
            try:
                for nombre_archivo in os.listdir(directorio):
                    ruta_archivo = os.path.join(directorio, nombre_archivo)
                    if nombre_archivo.upper().startswith(prefijo) and os.path.isfile(ruta_archivo):
                        try:
                            os.remove(ruta_archivo)
                        except OSError:
                            archivos_pendientes.append(nombre_archivo)
            except OSError:
                archivos_pendientes.append(directorio)

        if archivos_pendientes:
            return jsonify({
                'error': 'El expediente fue eliminado, pero algunos archivos heredados no pudieron borrarse.',
                'registro_eliminado': bool(eliminado),
                'archivos_pendientes': archivos_pendientes,
            }), 500

        return jsonify({'ok': True, 'registro_id': registro_id}), 200
    except Exception as e:
        print(f"Error al eliminar expediente {registro_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/obtener-empleados/<empresa>', methods=['GET'])
@app.route('/api/obtener-empleados/<empresa>', methods=['GET'])
@app.route('/obtener-ingresos', methods=['GET'])
@app.route('/api/obtener-ingresos', methods=['GET'])
def obtener_ingresos(empresa=None):
    if empresa is None:
        empresa = request.args.get('empresa')
    empresa = (empresa or '').strip().upper()
    if not empresa:
        return ("ERROR: El parámetro empresa es obligatorio", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS estatus VARCHAR(20) DEFAULT 'ACTIVO'")
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'registro'
                    """
                )
                cols_registro = {row[0] for row in cur.fetchall()}

                fecha_col = None
                for candidato in ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta'):
                    if candidato in cols_registro:
                        fecha_col = candidato
                        break

                fecha_select = f"r.{fecha_col}" if fecha_col else "NULL::timestamp"

                cur.execute(
                    f"""
                    SELECT ingresos_puesto.registro_id,
                           ingresos_puesto.nombre_candidato,
                           ingresos_puesto.no_empleado,
                           ingresos_puesto.empresa
                        , {fecha_select} AS fecha_ingreso
                        , r.fecha_nacimiento
                        , COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') AS estatus
                        , ingresos_puesto.area
                        , ingresos_puesto.puesto
                        , ingresos_puesto.turno
                    FROM ingresos_puesto
                    LEFT JOIN registro r ON r.id = ingresos_puesto.registro_id
                    WHERE UPPER(REPLACE(ingresos_puesto.empresa, ' ', '_')) = %s
                    ORDER BY ingresos_puesto.registro_id DESC
                    """,
                    (empresa,),
                )
                rows = cur.fetchall()

        data = [
            {
                'registro_id': row[0],
                'nombre': row[1],
                'no_empleado': row[2],
                'empresa': row[3],
                'fecha_ingreso': row[4].strftime('%Y-%m-%d') if row[4] else None,
                'fecha_nacimiento': row[5].strftime('%Y-%m-%d') if row[5] else None,
                'estatus': row[6] or 'ACTIVO',
                'area': row[7] or '',
                'puesto': row[8] or '',
                'turno': row[9] or '',
            }
            for row in rows
        ]
        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/exportar-empleados/<empresa>', methods=['GET'])
def exportar_empleados(empresa):
    empresa = (empresa or '').strip().upper()
    if not empresa:
        return ("ERROR: El parámetro empresa es obligatorio", 400)

    filtro_estatus = '' if request.args.get('todos') == '1' else """
                      AND (
                          COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') = 'ACTIVO'
                          OR (
                              COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') = 'INACTIVO'
                              AND r.fecha_inactivo >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                          )
                      )
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS estatus VARCHAR(20) DEFAULT 'ACTIVO'")
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS fecha_inactivo TIMESTAMP")
                cur.execute("ALTER TABLE encuesta_reclutamiento ADD COLUMN IF NOT EXISTS nombre_empleado VARCHAR(200)")
                cur.execute(
                    """
                    UPDATE registro
                    SET fecha_inactivo = CURRENT_TIMESTAMP
                    WHERE COALESCE(NULLIF(UPPER(TRIM(estatus)), ''), 'ACTIVO') = 'INACTIVO'
                      AND fecha_inactivo IS NULL
                    """
                )
                cur.execute(
                    f"""
                    SELECT r.fecha_registro,
                           r.nombre_completo,
                           r.fecha_nacimiento,
                           r.edad,
                           r.genero,
                           r.estado_civil,
                           r.nacionalidad,
                           r.curp,
                           r.rfc,
                           r.nss,
                           r.tel_movil,
                           r.correo,
                           r.codigo_postal,
                           r.colonia,
                           r.calle,
                           r.num_ext,
                           r.num_int,
                           r.municipio,
                           r.estado,
                           r.nombre_emergencia,
                           r.parentesco_emergencia,
                           r.telefono_emergencia,
                           ip.no_empleado,
                           ip.puesto,
                           ip.area,
                           ip.turno,
                           ip.salario,
                           reclutamiento.nombre_reclutador,
                           reclutamiento.fuente,
                           r.estatus,
                           r.fecha_inactivo
                    FROM registro AS r
                    JOIN ingresos_puesto AS ip ON ip.registro_id = r.id
                    LEFT JOIN LATERAL (
                        SELECT e.nombre_reclutador, e.fuente
                        FROM encuesta_reclutamiento AS e
                        WHERE LOWER(TRIM(COALESCE(e.nombre_empleado, ''))) =
                              LOWER(TRIM(r.nombre_completo))
                        ORDER BY e.fecha_registro DESC NULLS LAST, e.id DESC
                        LIMIT 1
                    ) AS reclutamiento ON TRUE
                    WHERE UPPER(REPLACE(ip.empresa, ' ', '_')) = %s
                    {filtro_estatus}
                    ORDER BY r.nombre_completo ASC
                    """,
                    (empresa,),
                )
                rows = cur.fetchall()

        fields = (
            'fecha_registro', 'nombre_completo', 'fecha_nacimiento', 'edad',
            'genero', 'estado_civil', 'nacionalidad', 'curp', 'rfc', 'nss',
            'tel_movil', 'correo', 'codigo_postal', 'colonia', 'calle',
            'num_ext', 'num_int', 'municipio', 'estado', 'nombre_emergencia',
            'parentesco_emergencia', 'telefono_emergencia', 'no_empleado',
            'puesto', 'area', 'turno', 'salario', 'nombre_reclutador', 'fuente',
            'estatus', 'fecha_inactivo',
        )
        data = []
        for row in rows:
            empleado = dict(zip(fields, row))
            for field in ('fecha_registro', 'fecha_nacimiento', 'fecha_inactivo'):
                if empleado[field]:
                    empleado[field] = empleado[field].isoformat()
            data.append(empleado)

        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = 'Empleados'
        columnas = (
            ('fecha_registro', 'fecha_registro'),
            ('nombre_completo', 'nombre_completo'),
            ('fecha_nacimiento', 'fecha_nacimiento'),
            ('edad', 'edad'),
            ('genero', 'genero'),
            ('estado_civil', 'estado_civil'),
            ('nacionalidad', 'nacionalidad'),
            ('curp', 'curp'),
            ('rfc', 'rfc'),
            ('nss', 'nss'),
            ('tel_movil', 'tel_movil'),
            ('correo', 'correo'),
            ('codigo_postal', 'codigo_postal'),
            ('colonia', 'colonia'),
            ('calle', 'calle'),
            ('num_ext', 'num_ext'),
            ('num_int', 'num_int'),
            ('municipio', 'municipio'),
            ('estado', 'estado'),
            ('nombre_emergencia', 'nombre_emergencia'),
            ('parentesco_emergencia', 'parentesco_emergencia'),
            ('telefono_emergencia', 'telefono_emergencia'),
            ('no_empleado', 'no_empleado'),
            ('puesto', 'puesto'),
            ('area', 'area'),
            ('turno', 'turno'),
            ('salario', 'salario'),
        )
        worksheet.append([titulo for _, titulo in columnas])

        for empleado in data:
            worksheet.append([empleado.get(campo) for campo, _ in columnas])
            if empleado.get('estatus') == 'INACTIVO':
                for cell in worksheet[worksheet.max_row]:
                    font = copy(cell.font)
                    font.color = 'FFFF0000'
                    font.underline = 'single'
                    cell.font = font

        worksheet.freeze_panes = 'A2'
        worksheet.auto_filter.ref = worksheet.dimensions
        for column_cells in worksheet.columns:
            column_letter = column_cells[0].column_letter
            max_length = max(len(str(cell.value or '')) for cell in column_cells)
            worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 32)

        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        filename = f"empleados_{empresa.lower()}_{datetime.now():%Y%m%d}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    except Exception as e:
        print(f"Error al exportar empleados: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/obtener-empleado/<int:registro_id>', methods=['GET'])
def obtener_empleado(registro_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                data = obtener_empleado_dict(cur, registro_id)

        if not data:
            return ("ERROR: Empleado no encontrado", 404)
        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/actualizar-empleado/<int:registro_id>', methods=['POST'])
@app.route('/api/actualizar-empleado/<int:registro_id>', methods=['POST'])
def actualizar_empleado(registro_id):
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict) or not payload:
        return ("ERROR: No se recibieron datos para actualizar", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS fotografia_base64 TEXT")
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS procedencia VARCHAR(150)")
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS estatus VARCHAR(20) DEFAULT 'ACTIVO'")
                cur.execute("ALTER TABLE registro ADD COLUMN IF NOT EXISTS fecha_inactivo TIMESTAMP")
                cols_registro = obtener_columnas(cur, 'registro')
                cols_ingresos = obtener_columnas(cur, 'ingresos_puesto')

                cur.execute(
                    "SELECT COALESCE(NULLIF(UPPER(TRIM(estatus)), ''), 'ACTIVO') "
                    "FROM registro WHERE id = %s FOR UPDATE",
                    (registro_id,),
                )
                estado_actual = cur.fetchone()
                estado_actual = estado_actual[0] if estado_actual else None

                nombre_ext, nombre_int = separar_numeracion(payload.get('numeracion'))
                municipio, estado, codigo_postal = separar_ubicacion(payload.get('ubicacion'))

                fecha_ingreso_valor = payload.get('fecha_ingreso') or payload.get('fecha_ingreso_raw') or payload.get('fecha_registro')
                fecha_col = None
                for candidato in ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta'):
                    if candidato in cols_registro:
                        fecha_col = candidato
                        break

                registro_map = {
                    'nombre_completo': payload.get('nombre_completo'),
                    'fecha_nacimiento': payload.get('fecha_nacimiento'),
                    'edad': payload.get('edad'),
                    'genero': payload.get('genero'),
                    'estado_civil': payload.get('estado_civil'),
                    'nacionalidad': payload.get('nacionalidad'),
                    'procedencia': payload.get('procedencia'),
                    'curp': payload.get('curp'),
                    'rfc': payload.get('rfc'),
                    'nss': payload.get('nss'),
                    'tel_movil': payload.get('tel_movil'),
                    'correo': payload.get('correo'),
                    'calle': payload.get('calle'),
                    'colonia': payload.get('colonia'),
                    'num_ext': nombre_ext,
                    'num_int': nombre_int,
                    'municipio': municipio,
                    'estado': estado,
                    'codigo_postal': codigo_postal,
                    'nombre_emergencia': payload.get('emergencia_nombre'),
                    'parentesco_emergencia': payload.get('emergencia_parentesco'),
                    'telefono_emergencia': payload.get('emergencia_telefono'),
                    'fotografia_base64': payload.get('fotografia_base64'),
                    'estatus': payload.get('estatus'),
                }

                if fecha_col:
                    registro_map[fecha_col] = fecha_ingreso_valor

                registro_updates = []
                registro_values = []
                for campo, valor in registro_map.items():
                    es_campo_opcional_editable = campo in {'nacionalidad', 'procedencia'} and campo in payload
                    if campo in cols_registro and (valor not in (None, '') or es_campo_opcional_editable):
                        registro_updates.append(f"{campo} = %s")
                        registro_values.append(valor)

                if registro_updates:
                    registro_values.append(registro_id)
                    cur.execute(
                        f"UPDATE registro SET {', '.join(registro_updates)} WHERE id = %s",
                        registro_values,
                    )

                estatus_nuevo = str(payload.get('estatus') or '').strip().upper()
                if estatus_nuevo == 'INACTIVO' and estado_actual != 'INACTIVO':
                    cur.execute(
                        "UPDATE registro SET fecha_inactivo = CURRENT_TIMESTAMP WHERE id = %s",
                        (registro_id,),
                    )
                elif estatus_nuevo == 'ACTIVO' and estado_actual == 'INACTIVO':
                    cur.execute(
                        "UPDATE registro SET fecha_inactivo = NULL WHERE id = %s",
                        (registro_id,),
                    )

                cur.execute(
                    "SELECT 1 FROM ingresos_puesto WHERE registro_id = %s LIMIT 1",
                    (registro_id,),
                )
                existe_ingreso = cur.fetchone() is not None

                ingreso_map = {
                    'empresa': payload.get('empresa'),
                    'no_empleado': payload.get('no_empleado'),
                    'personal': payload.get('personal'),
                    'puesto': payload.get('puesto'),
                    'area': payload.get('area'),
                    'turno': payload.get('turno'),
                    'salario': payload.get('salario'),
                    'pago': payload.get('pago'),
                }

                ingreso_updates = []
                ingreso_values = []
                for campo, valor in ingreso_map.items():
                    if campo in cols_ingresos and valor not in (None, ''):
                        ingreso_updates.append(f"{campo} = %s")
                        ingreso_values.append(valor)

                if existe_ingreso:
                    if ingreso_updates:
                        ingreso_values.append(registro_id)
                        cur.execute(
                            f"UPDATE ingresos_puesto SET {', '.join(ingreso_updates)} WHERE registro_id = %s",
                            ingreso_values,
                        )
                elif ingreso_updates:
                    columnas = []
                    valores = []
                    for campo, valor in ingreso_map.items():
                        if campo in cols_ingresos and valor not in (None, ''):
                            columnas.append(campo)
                            valores.append(valor)

                    if 'registro_id' in cols_ingresos:
                        columnas.append('registro_id')
                        valores.append(registro_id)

                    if columnas:
                        placeholders = ', '.join(['%s'] * len(valores))
                        cur.execute(
                            f"INSERT INTO ingresos_puesto ({', '.join(columnas)}) VALUES ({placeholders})",
                            valores,
                        )

                conn.commit()

                data = obtener_empleado_dict(cur, registro_id)

        if not data:
            return ("ERROR: Empleado no encontrado después de actualizar", 404)

        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado: {e}")
        if hasattr(e, 'pgerror') and e.pgerror:
            print(f"PostgreSQL error: {e.pgerror}")
        if hasattr(e, 'diag') and e.diag:
            if getattr(e.diag, 'message_detail', None):
                print(f"Detalle: {e.diag.message_detail}")
            if getattr(e.diag, 'constraint_name', None):
                print(f"Constraint: {e.diag.constraint_name}")
            if getattr(e.diag, 'column_name', None):
                print(f"Columna: {e.diag.column_name}")
        print(f"Payload recibido en /actualizar-empleado: {payload}")
        return (f"ERROR: {str(e)}", 500)


def asegurar_tabla_eventos_compartidos(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agenda_eventos_compartidos (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            month_day VARCHAR(5) NOT NULL,
            start_time VARCHAR(5),
            end_time VARCHAR(5),
            asistentes TEXT,
            tipo VARCHAR(20) NOT NULL DEFAULT 'evento',
            descripcion TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def normalizar_evento_compartido(payload):
    evento_id = str((payload or {}).get('id') or '').strip()
    title = str((payload or {}).get('title') or '').strip()
    month_day = str((payload or {}).get('monthDay') or (payload or {}).get('month_day') or '').strip()
    start_time = str((payload or {}).get('startTime') or (payload or {}).get('start_time') or '').strip()
    end_time = str((payload or {}).get('endTime') or (payload or {}).get('end_time') or '').strip()
    asistentes = str((payload or {}).get('asistentes') or '').strip()
    tipo = str((payload or {}).get('tipo') or 'evento').strip().lower() or 'evento'
    descripcion = str((payload or {}).get('descripcion') or '').strip()

    if not evento_id:
        raise ValueError('El id del evento es obligatorio.')
    if not title:
        raise ValueError('El titulo del evento es obligatorio.')
    if not re.match(r'^\d{2}-\d{2}$', month_day):
        raise ValueError('monthDay debe tener formato DD-MM.')
    if start_time and not re.match(r'^\d{2}:\d{2}$', start_time):
        raise ValueError('startTime debe tener formato HH:MM.')
    if end_time and not re.match(r'^\d{2}:\d{2}$', end_time):
        raise ValueError('endTime debe tener formato HH:MM.')

    if tipo not in {'cumpleanos', 'evento', 'aviso'}:
        tipo = 'evento'

    return {
        'id': evento_id,
        'title': title,
        'month_day': month_day,
        'start_time': start_time,
        'end_time': end_time,
        'asistentes': asistentes,
        'tipo': tipo,
        'descripcion': descripcion,
    }


def listar_eventos_compartidos(cur):
    asegurar_tabla_eventos_compartidos(cur)
    cur.execute(
        """
        SELECT id, title, month_day, start_time, end_time, asistentes, tipo, descripcion
        FROM agenda_eventos_compartidos
        ORDER BY month_day ASC, COALESCE(start_time, ''), id ASC
        """
    )
    rows = cur.fetchall()
    return [
        {
            'id': row[0],
            'title': row[1],
            'monthDay': row[2],
            'startTime': row[3] or '',
            'endTime': row[4] or '',
            'asistentes': row[5] or '',
            'tipo': row[6] or 'evento',
            'descripcion': row[7] or '',
        }
        for row in rows
    ]


@app.route('/api/shared-events', methods=['GET'])
def obtener_eventos_compartidos():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                eventos = listar_eventos_compartidos(cur)
        return jsonify({'eventos': eventos}), 200
    except Exception as e:
        print(f"Error en /api/shared-events GET: {e}")
        traceback.print_exc()
        return jsonify({'eventos': [], 'error': str(e)}), 500


@app.route('/api/shared-events', methods=['POST'])
def guardar_eventos_compartidos():
    payload = request.get_json(silent=True) or {}
    eventos_input = payload.get('eventos')
    if not isinstance(eventos_input, list):
        return ('ERROR: Debes enviar un arreglo en eventos.', 400)

    try:
        normalizados = [normalizar_evento_compartido(item) for item in eventos_input]
    except ValueError as error_validacion:
        return (f'ERROR: {str(error_validacion)}', 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_tabla_eventos_compartidos(cur)
                cur.execute("DELETE FROM agenda_eventos_compartidos")
                for evt in normalizados:
                    cur.execute(
                        """
                        INSERT INTO agenda_eventos_compartidos
                        (id, title, month_day, start_time, end_time, asistentes, tipo, descripcion, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        """,
                        (
                            evt['id'],
                            evt['title'],
                            evt['month_day'],
                            evt['start_time'],
                            evt['end_time'],
                            evt['asistentes'],
                            evt['tipo'],
                            evt['descripcion'],
                        ),
                    )
                conn.commit()

                eventos = listar_eventos_compartidos(cur)
        return jsonify({'ok': True, 'eventos': eventos}), 200
    except Exception as e:
        print(f"Error en /api/shared-events POST: {e}")
        traceback.print_exc()
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/obtener-empleado/<id_empleado>', methods=['GET'])
@app.route('/obtener-empleado/<id_empleado>', methods=['GET'])
def obtener_empleado_por_id(id_empleado):
    try:
        registro_id = int(str(id_empleado).strip())
    except (TypeError, ValueError):
        return ("ERROR: ID de empleado inválido", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                data = obtener_empleado_dict(cur, registro_id)

        if not data:
            return ("ERROR: Empleado no encontrado", 404)
        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado en /obtener-empleado: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/employee-files', methods=['GET'])
def listar_archivos_empleado():
    curp = request.args.get('curp', '').strip()
    empresa = request.args.get('empresa', '').strip()
    registro_id = request.args.get('registro_id', '').strip()

    if not registro_id and (not curp or not empresa):
        return ("ERROR: registro_id o (curp y empresa) son obligatorios", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_columna_documentos(cur)
                resolved_registro_id = resolver_registro_para_documentos(
                    cur,
                    registro_id=registro_id,
                    curp=curp,
                    empresa=empresa,
                )

                if not resolved_registro_id:
                    conn.commit()
                    return jsonify({'documentos': {}, 'registro_id': None}), 200

                documentos_guardados = leer_documentos_papeleria(cur, resolved_registro_id)
                documentos = serializar_documentos_para_respuesta(resolved_registro_id, documentos_guardados)

                # Compatibilidad con documentos legacy guardados en disco.
                if not documentos and curp and empresa:
                    documentos = buscar_documentos(curp, empresa)

            conn.commit()

        return jsonify({'documentos': documentos, 'registro_id': resolved_registro_id}), 200
    except Exception as e:
        print(f"Error detallado en /api/employee-files GET: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/employee-files', methods=['POST'])
def subir_archivo_empleado():
    curp = (request.form.get('curp') or '').strip()
    empresa = (request.form.get('empresa') or '').strip()
    registro_id = (request.form.get('registro_id') or '').strip()
    doc_key = (request.form.get('doc_key') or '').strip()
    archivo = request.files.get('file')

    if not doc_key or archivo is None:
        return ("ERROR: doc_key y file son obligatorios", 400)

    if not registro_id and (not curp or not empresa):
        return ("ERROR: registro_id o (curp y empresa) son obligatorios", 400)

    if doc_key not in DOC_FILENAME_KEYS:
        return ("ERROR: doc_key inválido", 400)

    mime_type = (archivo.mimetype or '').lower().strip()
    if mime_type not in DOC_ALLOWED_MIME_TYPES:
        return ("ERROR: Formato no permitido", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_columna_documentos(cur)
                resolved_registro_id = resolver_registro_para_documentos(
                    cur,
                    registro_id=registro_id,
                    curp=curp,
                    empresa=empresa,
                )
                if not resolved_registro_id:
                    return ("ERROR: Empleado no encontrado", 404)

                if not curp or not empresa:
                    curp_db, empresa_db = obtener_curp_empresa_para_registro(cur, resolved_registro_id)
                    curp = curp or curp_db
                    empresa = empresa or empresa_db

                empresa_normalizada = normalizar_empresa(empresa or '')[0]
                file_name = construir_nombre_documento(curp, empresa_normalizada, doc_key, archivo.filename)
                contenido = archivo.read() or b''
                contenido_base64 = base64.b64encode(contenido).decode('ascii')

                documentos_guardados = leer_documentos_papeleria(cur, resolved_registro_id)
                documentos_guardados[doc_key] = {
                    'fileName': file_name,
                    'mimeType': mime_type,
                    'base64Content': contenido_base64,
                }

                cur.execute(
                    "UPDATE registro SET documentos_papeleria = %s WHERE id = %s",
                    (json.dumps(documentos_guardados), resolved_registro_id),
                )

            conn.commit()

        return jsonify({
            'ok': True,
            'registro_id': resolved_registro_id,
            'docKey': doc_key,
            'fileName': file_name,
            'previewUrl': f"/api/documentos-db/{resolved_registro_id}/{doc_key}",
        }), 200
    except Exception as e:
        print(f"Error detallado en /api/employee-files POST: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/employee-files', methods=['DELETE'])
def eliminar_archivo_empleado():
    curp = (request.args.get('curp') or '').strip()
    empresa = (request.args.get('empresa') or '').strip()
    registro_id = (request.args.get('registro_id') or '').strip()
    doc_key = (request.args.get('doc_key') or '').strip()

    if not doc_key:
        return ("ERROR: doc_key es obligatorio", 400)

    if not registro_id and (not curp or not empresa):
        return ("ERROR: registro_id o (curp y empresa) son obligatorios", 400)

    if doc_key not in DOC_FILENAME_KEYS:
        return ("ERROR: doc_key inválido", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_columna_documentos(cur)
                resolved_registro_id = resolver_registro_para_documentos(
                    cur,
                    registro_id=registro_id,
                    curp=curp,
                    empresa=empresa,
                )
                if not resolved_registro_id:
                    return ("ERROR: Empleado no encontrado", 404)

                documentos_guardados = leer_documentos_papeleria(cur, resolved_registro_id)
                if doc_key in documentos_guardados:
                    del documentos_guardados[doc_key]
                    cur.execute(
                        "UPDATE registro SET documentos_papeleria = %s WHERE id = %s",
                        (json.dumps(documentos_guardados), resolved_registro_id),
                    )

                # Limpieza legacy en disco si existiera documento previo.
                if curp and empresa:
                    empresa_normalizada, directorio = normalizar_empresa(empresa)
                    directorio = asegurar_directorio(directorio)

                    file_name = construir_nombre_documento(curp, empresa_normalizada, doc_key, 'archivo.dat')
                    ruta = os.path.join(directorio, file_name)

                    if os.path.exists(ruta):
                        os.remove(ruta)
                    else:
                        prefijo = f"{limpiar_fragmento(curp)}-{limpiar_fragmento(empresa_normalizada)}-{DOC_FILENAME_KEYS[doc_key]}"
                        for existing_file in os.listdir(directorio) if os.path.isdir(directorio) else []:
                            if existing_file.upper().startswith(prefijo):
                                os.remove(os.path.join(directorio, existing_file))
                                break

            conn.commit()

        return ("OK", 200)
    except Exception as e:
        print(f"Error detallado en /api/employee-files DELETE: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/documentos-db/<int:registro_id>/<doc_key>', methods=['GET'])
def servir_documento_desde_db(registro_id, doc_key):
    if doc_key not in DOC_FILENAME_KEYS:
        return ("ERROR: doc_key inválido", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                asegurar_columna_documentos(cur)
                documentos_guardados = leer_documentos_papeleria(cur, registro_id)

        doc = documentos_guardados.get(doc_key)
        if not isinstance(doc, dict) or not doc.get('base64Content'):
            return ("ERROR: Archivo no encontrado", 404)

        contenido = base64.b64decode(doc.get('base64Content'))
        mime_type = (doc.get('mimeType') or 'application/octet-stream').strip() or 'application/octet-stream'
        file_name = (doc.get('fileName') or f"{doc_key}.dat").strip() or f"{doc_key}.dat"

        return send_file(
            BytesIO(contenido),
            mimetype=mime_type,
            as_attachment=False,
            download_name=file_name,
        )
    except Exception as e:
        print(f"Error detallado en /api/documentos-db: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/documentos/<empresa>/<path:file_name>', methods=['GET'])
def servir_documento_empleado(empresa, file_name):
    empresa_normalizada, directorio = normalizar_empresa(empresa)
    directorio = asegurar_directorio(directorio)
    if not os.path.isdir(directorio):
        return ("ERROR: Directorio no disponible", 404)

    mimetype, _ = mimetypes.guess_type(file_name)
    return send_from_directory(directorio, file_name, mimetype=mimetype or 'application/octet-stream')


def guardar_o_actualizar_ingreso(cur, payload):
    required_keys = [
        'nombre_candidato', 'empresa', 'no_empleado', 'personal', 'puesto', 'area',
        'turno', 'salario', 'pago', 'registro_id'
    ]

    faltantes = [k for k in required_keys if not str(payload.get(k, '')).strip()]
    if faltantes:
        return (f"ERROR: Fila incompleta ({', '.join(faltantes)})", 400)

    try:
        registro_id = int(str(payload.get('registro_id')).strip())
    except Exception:
        return ("ERROR: registro_id inválido", 400)

    valores = (
        str(payload.get('nombre_candidato')).strip(),
        str(payload.get('empresa')).strip(),
        str(payload.get('no_empleado')).strip(),
        str(payload.get('personal')).strip(),
        str(payload.get('puesto')).strip(),
        str(payload.get('area')).strip(),
        str(payload.get('turno')).strip(),
        str(payload.get('salario')).strip(),
        str(payload.get('pago')).strip(),
        registro_id,
    )

    cur.execute(
        """
        SELECT 1
        FROM ingresos_puesto
        WHERE registro_id = %s
        LIMIT 1
        """,
        (registro_id,),
    )

    existe = cur.fetchone() is not None

    if existe:
        cur.execute(
            """
            UPDATE ingresos_puesto
            SET nombre_candidato = %s,
                empresa = %s,
                no_empleado = %s,
                personal = %s,
                puesto = %s,
                area = %s,
                turno = %s,
                salario = %s,
                pago = %s
            WHERE registro_id = %s
            """,
            valores,
        )
    else:
        cur.execute(
            """
            INSERT INTO ingresos_puesto (
                nombre_candidato, empresa, no_empleado, personal, puesto, area,
                turno, salario, pago, registro_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            valores,
        )

    return ("OK", 200)


@app.route('/guardar-ingreso', methods=['POST'])
@app.route('/api/guardar-ingreso', methods=['POST'])
def guardar_ingreso():
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict) or not payload:
        return ("ERROR: No se recibieron datos para guardar", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                resultado = guardar_o_actualizar_ingreso(cur, payload)
            conn.commit()

        return resultado
    except Exception as e:
        print(f"Error detallado: {e}")
        if hasattr(e, 'pgerror') and e.pgerror:
            print(f"PostgreSQL error: {e.pgerror}")
        if hasattr(e, 'diag') and e.diag:
            if getattr(e.diag, 'message_detail', None):
                print(f"Detalle: {e.diag.message_detail}")
            if getattr(e.diag, 'constraint_name', None):
                print(f"Constraint: {e.diag.constraint_name}")
            if getattr(e.diag, 'column_name', None):
                print(f"Columna: {e.diag.column_name}")
        print(f"Payload recibido en /guardar-ingreso: {payload}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/guardar-ingresos', methods=['POST'])
@app.route('/api/guardar-ingresos', methods=['POST'])
def guardar_ingresos():
    payload = request.get_json(silent=True) or []

    if not isinstance(payload, list) or not payload:
        return ("ERROR: No se recibieron filas para guardar", 400)

    required_keys = [
        'nombre_candidato', 'empresa', 'no_empleado', 'personal', 'puesto',
        'area', 'turno', 'salario', 'pago', 'registro_id'
    ]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for idx, fila in enumerate(payload, start=1):
                    if not isinstance(fila, dict):
                        return (f"ERROR: Fila {idx} inválida", 400)

                    resultado = guardar_o_actualizar_ingreso(cur, fila)
                    if resultado[1] != 200:
                        return (f"ERROR: Fila {idx}: {resultado[0]}", resultado[1])

            conn.commit()

        return ("OK", 200)
    except Exception as e:
        print(f"Error detallado: {e}")
        if hasattr(e, 'pgerror') and e.pgerror:
            print(f"PostgreSQL error: {e.pgerror}")
        if hasattr(e, 'diag') and e.diag:
            if getattr(e.diag, 'message_detail', None):
                print(f"Detalle: {e.diag.message_detail}")
            if getattr(e.diag, 'constraint_name', None):
                print(f"Constraint: {e.diag.constraint_name}")
            if getattr(e.diag, 'column_name', None):
                print(f"Columna: {e.diag.column_name}")
        print(f"Payload recibido en /guardar-ingresos: {payload}")
        return (f"ERROR: {str(e)}", 500)

#-----------------------------------------------------------------------------------------------------------------
@app.route('/admin/normalize-encuestas', methods=['POST'])
def admin_normalize_encuestas():
    """Endpoint temporal: normaliza los valores de `fuente` en encuesta_reclutamiento.
    Devuelve los recuentos antes y después. Usar con precaución (solo local).
    """
    case_sql = '''
UPDATE encuesta_reclutamiento
SET fuente = CASE
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%FACEBOOK%' THEN 'FACEBOOK'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%POSTEO%' THEN 'POSTEO'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%VOLANTE%' THEN 'VOLANTE'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%RECOM%' THEN 'RECOMENDACION'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%AGENCIA%' AND (UPPER(COALESCE(fuente, '')) LIKE '%CW%' OR UPPER(COALESCE(fuente, '')) LIKE '%% C W%%') THEN 'RECLU CW'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%AGENCIA%' AND UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%CW%' THEN 'RECLU CW'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%PURO%' THEN 'RECLU PURO'
  ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
END
WHERE fuente IS NOT NULL;
'''

    summary_sql = "SELECT fuente, COUNT(*) FROM encuesta_reclutamiento GROUP BY fuente ORDER BY COUNT(*) DESC;"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(summary_sql)
                before = cur.fetchall()

                cur.execute(case_sql)
                conn.commit()

                cur.execute(summary_sql)
                after = cur.fetchall()

        return jsonify({'before': before, 'after': after}), 200
    except Exception as e:
        traceback.print_exc()
        return (f"ERROR: {str(e)}", 500)

@app.route('/admin/preview-encuestas', methods=['GET'])
def admin_preview_encuestas():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM encuesta_reclutamiento LIMIT 20")
                filas = cur.fetchall()
                cols = [d[0] for d in cur.description]
        # convert to list of dicts
        data = [dict(zip(cols, f)) for f in filas]
        return jsonify(data), 200
    except Exception as e:
        traceback.print_exc()
        return (f"ERROR: {str(e)}", 500)

@app.route('/api/datos-grafica')
def api_datos_grafica():
    periodo = request.args.get('periodo')
    tz = request.args.get('tz') or 'America/Mexico_City'

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if periodo == 'mes':
                    mes = int(request.args.get('mes', '1'))

                    cur.execute("""
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'encuesta_reclutamiento' AND column_name = 'sub_fuente'
                    """)
                    has_sub = cur.fetchone() is not None

                    if has_sub:
                        query = """
                            SELECT
                              CASE
                                WHEN UPPER(COALESCE(sub_fuente, '')) LIKE '%%CW%%' THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(sub_fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                WHEN UPPER(TRIM(COALESCE(fuente, ''))) IN ('RECLU CW','RECLU PURO') THEN UPPER(TRIM(COALESCE(fuente, '')))
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%FACEBOOK%%' THEN 'FACEBOOK'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%POSTEO%%' THEN 'POSTEO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%VOLANTE%%' THEN 'VOLANTE'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%RECOM%%' THEN 'RECOMENDACION'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND (UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' OR UPPER(COALESCE(fuente, '')) LIKE '%% C W%%') THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
                              END as fuente_norm,
                              COUNT(*)
                            FROM encuesta_reclutamiento
                            WHERE EXTRACT(MONTH FROM timezone(%s, fecha_registro)) = %s
                              AND EXTRACT(YEAR FROM timezone(%s, fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                            GROUP BY fuente_norm
                        """
                    else:
                        query = """
                            SELECT
                              CASE
                                WHEN UPPER(TRIM(COALESCE(fuente, ''))) IN ('RECLU CW','RECLU PURO') THEN UPPER(TRIM(COALESCE(fuente, '')))
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%FACEBOOK%%' THEN 'FACEBOOK'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%POSTEO%%' THEN 'POSTEO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%VOLANTE%%' THEN 'VOLANTE'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%RECOM%%' THEN 'RECOMENDACION'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND (UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' OR UPPER(COALESCE(fuente, '')) LIKE '%% C W%%') THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
                              END as fuente_norm,
                              COUNT(*)
                            FROM encuesta_reclutamiento
                            WHERE EXTRACT(MONTH FROM timezone(%s, fecha_registro)) = %s
                              AND EXTRACT(YEAR FROM timezone(%s, fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                            GROUP BY fuente_norm
                        """
                    cur.execute(query, (tz, mes, tz, tz))
                    filas_raw = cur.fetchall()
                    resultados = {}
                    for row in filas_raw:
                        if isinstance(row, (list, tuple)) and len(row) >= 2:
                            try:
                                resultados[str(row[0])] = int(row[1])
                            except Exception:
                                resultados[str(row[0])] = 0

                    labels_orden = ['FACEBOOK', 'POSTEO', 'VOLANTE', 'RECOMENDACION', 'RECLU CW', 'RECLU PURO']
                    datos_ordenados = [int(resultados.get(label, 0)) for label in labels_orden]

                    return jsonify(datos_ordenados)

                elif periodo == 'reclutadores':
                    mes = int(request.args.get('mes', str(datetime.now().month)))

                    query = """
                        SELECT
                                                        TRIM(e.nombre_reclutador),
                            COUNT(e.id) AS total
                                                FROM encuesta_reclutamiento e
                                                WHERE NULLIF(TRIM(e.nombre_reclutador), '') IS NOT NULL
                                                    AND EXTRACT(MONTH FROM timezone(%s, e.fecha_registro)) = %s
                                                    AND EXTRACT(YEAR FROM timezone(%s, e.fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                                                GROUP BY TRIM(e.nombre_reclutador)
                                                ORDER BY total DESC, TRIM(e.nombre_reclutador) ASC
                    """
                    cur.execute(query, (tz, mes, tz, tz))
                    filas = cur.fetchall()

                    datos = [
                        {
                            'nombre': str(row[0]).strip(),
                            'total': int(row[1] or 0),
                        }
                        for row in filas
                        if str(row[0] or '').strip()
                    ]
                    return jsonify(datos)

                elif periodo == '12meses':
                    query = """
                        SELECT EXTRACT(MONTH FROM timezone(%s, fecha_registro)) as mes, COUNT(*)
                        FROM encuesta_reclutamiento
                        WHERE EXTRACT(YEAR FROM timezone(%s, fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                        GROUP BY mes
                    """
                    cur.execute(query, (tz, tz, tz))
                    filas = cur.fetchall()

                    meses_counts = [0] * 12
                    for mes_val, total in filas:
                        m = int(mes_val)
                        if 1 <= m <= 12:
                            meses_counts[m - 1] = int(total)

                    return jsonify(meses_counts)

    except Exception as e:
        print(f"Error en API gráfica: {e}")
        traceback.print_exc()
        return jsonify([0] * 12), 500


# ===== ENDPOINTS PARA PERSONAL DE RECLUTAMIENTO =====

def _ensure_reclutamiento_schema(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS personal_reclutamiento (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(100) NOT NULL UNIQUE,
            team INT DEFAULT 1,
            fecha_registro DATE DEFAULT CURRENT_DATE,
            telefono VARCHAR(50)
        )
    """)
    for stmt in (
        "ALTER TABLE personal_reclutamiento ADD COLUMN IF NOT EXISTS team INT DEFAULT 1",
        "ALTER TABLE personal_reclutamiento ADD COLUMN IF NOT EXISTS telefono VARCHAR(50)",
        "ALTER TABLE personal_reclutamiento ALTER COLUMN telefono TYPE VARCHAR(50)",
        "ALTER TABLE encuesta_reclutamiento ADD COLUMN IF NOT EXISTS nombre_reclutador VARCHAR(255)",
        "ALTER TABLE encuesta_reclutamiento ADD COLUMN IF NOT EXISTS sub_fuente VARCHAR(50)",
    ):
        try:
            cur.execute(stmt)
        except Exception:
            pass


@app.route('/api/ingresos-por-team', methods=['GET'])
def obtener_ingresos_por_team():
    """Obtiene los reclutamientos por team para el mes solicitado."""
    try:
        mes = int(request.args.get('mes', str(datetime.now().month)))
        anio = int(request.args.get('anio', str(datetime.now().year)))
        if not 1 <= mes <= 12 or not 2000 <= anio <= 2100:
            return jsonify({'error': 'Mes o año inválido'}), 400

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _ensure_reclutamiento_schema(cur)
                cur.execute("""
                    SELECT p.id, p.nombre, COALESCE(p.team, 1), COUNT(e.id)
                    FROM personal_reclutamiento p
                    LEFT JOIN encuesta_reclutamiento e
                      ON LOWER(TRIM(COALESCE(e.nombre_reclutador, ''))) = LOWER(TRIM(p.nombre))
                     AND (
                         UPPER(TRIM(COALESCE(e.fuente, ''))) IN ('FACEBOOK', 'RECLU CW')
                         OR (
                             UPPER(TRIM(COALESCE(e.fuente, ''))) = 'AGENCIA'
                             AND UPPER(TRIM(COALESCE(e.sub_fuente, ''))) = 'CW'
                         )
                     )
                     AND EXTRACT(MONTH FROM e.fecha_registro) = %s
                     AND EXTRACT(YEAR FROM e.fecha_registro) = %s
                    WHERE COALESCE(p.team, 1) BETWEEN 1 AND 4
                    GROUP BY p.id, p.nombre, p.team
                    ORDER BY p.team, p.nombre
                """, (mes, anio))
                rows = cur.fetchall()

        equipos = {team: {'team': team, 'total': 0, 'reclutadores': []} for team in range(1, 5)}
        for reclutador_id, nombre, team, total in rows:
            team = int(team or 1)
            total = int(total or 0)
            equipos[team]['total'] += total
            equipos[team]['reclutadores'].append({
                'id': reclutador_id,
                'nombre': nombre,
                'total': total,
            })

        return jsonify({
            'mes': mes,
            'anio': anio,
            'equipos': list(equipos.values()),
        }), 200
    except Exception as e:
        print(f"Error en /api/ingresos-por-team: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/exportar-reclutamiento', methods=['GET'])
def exportar_reclutamiento():
    try:
        mes = int(request.args.get('mes', str(datetime.now().month)))
        anio = int(request.args.get('anio', str(datetime.now().year)))
        if not 1 <= mes <= 12 or not 2000 <= anio <= 2100:
            return jsonify({'error': 'Mes o año inválido'}), 400

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    ALTER TABLE encuesta_reclutamiento
                    ADD COLUMN IF NOT EXISTS sub_fuente VARCHAR(50)
                """)
                cur.execute("""
                    ALTER TABLE encuesta_reclutamiento
                    ADD COLUMN IF NOT EXISTS registro_id INTEGER REFERENCES registro(id)
                """)
                cur.execute("""
                    SELECT e.fecha_registro,
                           COALESCE(NULLIF(TRIM(e.nombre_reclutador), ''), ''),
                           p.team
                    FROM encuesta_reclutamiento AS e
                    LEFT JOIN personal_reclutamiento AS p
                      ON LOWER(TRIM(p.nombre)) = LOWER(TRIM(e.nombre_reclutador))
                    WHERE EXTRACT(MONTH FROM e.fecha_registro) = %s
                      AND EXTRACT(YEAR FROM e.fecha_registro) = %s
                      AND NULLIF(TRIM(e.nombre_reclutador), '') IS NOT NULL
                      AND (
                          UPPER(TRIM(COALESCE(e.fuente, ''))) IN ('FACEBOOK', 'RECLU CW')
                          OR (
                              UPPER(TRIM(COALESCE(e.fuente, ''))) = 'AGENCIA'
                              AND UPPER(TRIM(COALESCE(e.sub_fuente, ''))) = 'CW'
                          )
                      )
                    ORDER BY e.fecha_registro, e.nombre_empleado
                """, (mes, anio))
                filas = cur.fetchall()

        # Crear el archivo Excel con openpyxl
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = 'Reclutamiento'

        # Cabeceras de la tabla
        encabezados = ['Fecha de registro', 'Mes', 'Nombre del reclutador', 'Team de reclutamiento']
        worksheet.append(encabezados)

        # Agregar filas de datos
        nombres_meses = [
            'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
            'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
        ]
        for fila in filas:
            worksheet.append([
                fila[0],
                nombres_meses[fila[0].month - 1] if fila[0] else '',
                fila[1],
                f'Team {fila[2]}' if fila[2] else '',
            ])
            if fila[0]:
                worksheet.cell(row=worksheet.max_row, column=1).number_format = 'dd/mm/yyyy'

        # Dar formato visual básico (congelar paneles y ajustar columnas)
        worksheet.freeze_panes = 'A2'
        if worksheet.dimensions:
            worksheet.auto_filter.ref = worksheet.dimensions

        for column_cells in worksheet.columns:
            column_letter = column_cells[0].column_letter
            max_length = max(len(str(cell.value or '')) for cell in column_cells)
            worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 32)

        # Guardar en memoria y retornar como archivo descargable
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        filename = f"reporte_reclutamiento_{mes}_{anio}.xlsx"
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    except Exception as e:
        print(f"Error en /api/exportar-reclutamiento: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/reclutadores-nombres', methods=['GET', 'POST', 'OPTIONS'])
def manejar_reclutadores():
    if request.method == 'OPTIONS':
        return '', 200

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                _ensure_reclutamiento_schema(cur)

                if request.method == 'POST':
                    datos = request.get_json(silent=True) or {}
                    nombre = str(datos.get('nombre', '')).strip()
                    telefono = str(datos.get('telefono', '')).strip()
                    try:
                        team = int(datos.get('team', 1))
                    except (TypeError, ValueError):
                        return jsonify({'error': 'El team debe ser un número válido.'}), 400

                    if not nombre:
                        return jsonify({'error': 'El nombre del reclutador es obligatorio.'}), 400
                    if not telefono:
                        return jsonify({'error': 'El teléfono del reclutador es obligatorio.'}), 400

                    cur.execute("""
                        INSERT INTO personal_reclutamiento (nombre, team, telefono)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (nombre) DO UPDATE SET
                            team = EXCLUDED.team,
                            telefono = COALESCE(NULLIF(EXCLUDED.telefono, ''), personal_reclutamiento.telefono)
                        RETURNING id, nombre, team
                    """, (nombre, team, telefono))

                    row = cur.fetchone()
                    if row is None:
                        return jsonify({'error': 'No se pudo guardar el reclutador.'}), 500
                    conn.commit()
                    return jsonify({
                        'id': row[0],
                        'nombre': row[1],
                        'team': row[2],
                        'activo': True,
                    }), 201

                cur.execute("""
                    SELECT id, nombre, COALESCE(team, 1) as team, telefono
                    FROM personal_reclutamiento
                    ORDER BY nombre ASC
                """)
                rows = cur.fetchall()

        reclutadores = [
            {'id': row[0], 'nombre': row[1], 'team': row[2], 'telefono': row[3], 'activo': True}
            for row in rows if row[1]
        ]
        return jsonify(reclutadores), 200

    except Exception as e:
        print(f"Error en /api/reclutadores-nombres: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/reclutadores-nombres/<int:reclutador_id>', methods=['POST', 'PUT', 'DELETE', 'OPTIONS'])
def actualizar_reclutador(reclutador_id):
    if request.method == 'OPTIONS':
        return '', 200

    if request.method == 'DELETE':
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM personal_reclutamiento WHERE id = %s", (reclutador_id,))
                    eliminado = cur.rowcount
                conn.commit()

            if not eliminado:
                return jsonify({'error': 'Reclutador no encontrado.'}), 404
            return jsonify({'ok': True}), 200
        except Exception as e:
            print(f"Error al eliminar reclutador: {e}")
            return jsonify({'error': str(e)}), 500

    datos = request.get_json(silent=True) or {}
    telefono = str(datos.get('telefono', '')).strip()
    try:
        team = int(datos.get('team', 1))
    except (TypeError, ValueError):
        return jsonify({'error': 'El team debe ser un número válido.'}), 400

    if not telefono:
        return jsonify({'error': 'El teléfono del reclutador es obligatorio.'}), 400

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE personal_reclutamiento SET telefono = %s WHERE id = %s",
                    (telefono, reclutador_id),
                )
                actualizado = cur.rowcount
                if actualizado:
                    cur.execute(
                        "UPDATE personal_reclutamiento SET team = %s WHERE id = %s",
                        (team, reclutador_id),
                    )
                    cur.execute(
                        "SELECT id, nombre, team, telefono FROM personal_reclutamiento WHERE id = %s",
                        (reclutador_id,),
                    )
                    row = cur.fetchone()
            conn.commit()

        if not actualizado:
            return jsonify({'error': 'Reclutador no encontrado.'}), 404
        return jsonify({
            'id': row[0],
            'nombre': row[1],
            'team': row[2],
            'telefono': row[3],
            'activo': True,
        }), 200
    except Exception as e:
        print(f"Error al actualizar reclutador: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/reclutadores-nombres/<int:reclutador_id>/telefono', methods=['DELETE', 'OPTIONS'])
def eliminar_telefono_reclutador(reclutador_id):
    if request.method == 'OPTIONS':
        return '', 200

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE personal_reclutamiento SET telefono = NULL WHERE id = %s",
                    (reclutador_id,),
                )
                actualizado = cur.rowcount
            conn.commit()

        if not actualizado:
            return jsonify({'error': 'Reclutador no encontrado.'}), 404
        return jsonify({'ok': True}), 200
    except Exception as e:
        print(f"Error al eliminar teléfono del reclutador: {e}")
        return jsonify({'error': str(e)}), 500

# =======================================================
@app.route('/api/exportar-excel', methods=['GET'])
def exportar_excel():
    try:
        # 1. Crear el libro de Excel con openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Reporte"
        
        # Ejemplo de datos
        ws.append(["ID", "Nombre", "Empresa"])
        ws.append([1, "Ejemplo", "Cheong Woon"])

        # 2. Guardar en memoria
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        # 3. Enviar como archivo descargable
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='reporte_personal.xlsx'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =======================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)