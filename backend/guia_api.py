"""Guía de la API para el agente de Telegram.

Flask no genera OpenAPI, así que la guía se escribe a mano. ``test_guia.py``
recorre ``app.url_map`` y falla si una ruta no está documentada ni oculta, o si
la guía menciona una ruta que no existe: así no se desfasa del código.

Los permisos no se repiten aquí: salen de ``auth.PERMISOS_POR_ENDPOINT``.

Qué NO entra a la guía (``PREFIJOS_OCULTOS`` / ``RUTAS_OCULTAS``): sesión y
cuentas, vínculos de Telegram, documentos personales y binarios, el formulario
público de registro y utilidades de mantenimiento. Las rutas sin ``/api`` son
duplicados de compatibilidad y tampoco entran.
"""

import re

import auth

PREFIJOS_OCULTOS = (
    '/api/auth',
    '/api/telegram',
    '/api/access-users',
    '/api/documentos',      # también /api/documentos-db
    '/api/health',
    '/admin',
    '/static',
)

#: (método, ruta Flask) que se ocultan aunque su prefijo no lo esté.
RUTAS_OCULTAS = {
    ('POST', '/api/guardar-registro'),        # formulario público de candidatos
    ('POST', '/api/guardar-encuesta'),        # idem
    ('POST', '/api/escanear-documento'),      # manda imágenes a OpenAI
    ('GET', '/api/exportar-excel'),           # plantilla de ejemplo, sin datos
    ('GET', '/api/empleados/<int:registro_id>/incidencias/<int:incidencia_id>/archivo'),  # binario personal
    ('POST', '/api/guardar-ingresos'),        # lote; el bot usa guardar-ingreso
}

EMPRESA = 'CHEONG_WOON o KRONOS'

#: Lo que el agente puede conocer. ``archivo``: la respuesta es un archivo que
#: va directo a la persona, nunca al modelo.
GUIA = [
    # --- Empleados ---------------------------------------------------------
    {'metodo': 'GET', 'ruta': '/api/empleados/buscar',
     'resumen': 'Buscar empleados por número exacto o parte del nombre. Devuelve ficha corta (empresa, puesto, área, turno, estatus).',
     'parametros': {'no_empleado': 'número exacto', 'q': 'parte del nombre (mín. 3 letras)', 'limite': '1-25, 10 por omisión'}},
    {'metodo': 'GET', 'ruta': '/api/obtener-empleado/{id_empleado}',
     'resumen': 'Expediente completo de un empleado por registro_id (datos personales enmascarados, puesto, salario, pago).'},
    {'metodo': 'GET', 'ruta': '/api/obtener-empleados/{empresa}',
     'resumen': f'Todos los empleados de una empresa ({EMPRESA}) con número, área, puesto, turno, estatus y fechas. Puede ser largo: prefiere contar_empleados.'},
    {'metodo': 'GET', 'ruta': '/api/empleados/{registro_id}/incidencias',
     'resumen': 'Historial de incidencias de un empleado (vacaciones, incapacidad, suspensión, entrada-salida, indisciplina) con fechas y motivo.'},
    {'metodo': 'GET', 'ruta': '/api/memoria', 'resumen': 'Notas que la persona pidió al asistente recordar.'},
    {'metodo': 'POST', 'ruta': '/api/memoria', 'resumen': 'Guardar una nota de memoria (texto, hasta 200 caracteres).'},
    {'metodo': 'DELETE', 'ruta': '/api/memoria/{nota_id}', 'resumen': 'Borrar una nota de memoria propia.'},
    {'metodo': 'GET', 'ruta': '/api/tareas',
     'resumen': 'Tareas programadas de la persona (consultas que el asistente corre solo a una hora fija).'},
    {'metodo': 'POST', 'ruta': '/api/tareas',
     'resumen': 'Programar una tarea de consulta (instruccion, frecuencia diaria/semanal/mensual, hora, minuto, '
                'dias_semana 0=lunes, dia_mes 1-28).'},
    {'metodo': 'DELETE', 'ruta': '/api/tareas/{tarea_id}', 'resumen': 'Borrar una tarea programada propia.'},
    {'metodo': 'GET', 'ruta': '/api/avisos',
     'resumen': 'Avisos del día: contratos que vencen pronto (fin = ingreso + 67 días hábiles), quién regresa o '
                'empieza una incidencia hoy o mañana, y candidatos sin asignar desde hace días. Cada sección '
                'depende de los permisos.',
     'parametros': {'dias_contrato': 'ventana de vencimiento en días (por omisión 7)',
                    'dias_candidato': 'días esperando para contar un candidato como estancado (por omisión 7)'}},
    {'metodo': 'GET', 'ruta': '/api/employee-files',
     'resumen': 'Qué documentos tiene cargados un empleado (solo nombres de documento, no su contenido).',
     'parametros': {'registro_id': 'id del empleado'}},
    {'metodo': 'GET', 'ruta': '/api/exportar-empleados/{empresa}', 'archivo': True,
     'resumen': f'Excel con el listado de empleados de una empresa ({EMPRESA}). Se envía a la persona.',
     'parametros': {'todos': '1 para incluir inactivos antiguos'}},
    {'metodo': 'POST', 'ruta': '/api/actualizar-empleado/{registro_id}',
     'resumen': 'Actualizar datos de un empleado; solo cambia los campos enviados no vacíos (p. ej. estatus ACTIVO/INACTIVO).'},
    {'metodo': 'DELETE', 'ruta': '/api/eliminar-empleado/{registro_id}',
     'resumen': 'Eliminar definitivamente un empleado y todos sus datos.'},
    {'metodo': 'POST', 'ruta': '/api/empleados/{registro_id}/incidencias',
     'resumen': 'Registrar una incidencia (multipart: tipo, motivo, fecha_inicio, fecha_reingreso, detalles JSON, archivo opcional).'},
    {'metodo': 'DELETE', 'ruta': '/api/empleados/{registro_id}/incidencias/{incidencia_id}',
     'resumen': 'Eliminar una incidencia de un empleado.'},
    {'metodo': 'POST', 'ruta': '/api/employee-files',
     'resumen': 'Subir un documento al expediente de un empleado (multipart).'},
    {'metodo': 'DELETE', 'ruta': '/api/employee-files',
     'resumen': 'Quitar un documento del expediente de un empleado.'},

    # --- Ingresos ------------------------------------------------------------
    {'metodo': 'GET', 'ruta': '/api/obtener-pendientes',
     'resumen': 'Candidatos registrados que aún no tienen puesto asignado (nombre, número temporal, id).'},
    {'metodo': 'GET', 'ruta': '/api/obtener-ingresos',
     'resumen': f'Igual que obtener-empleados pero con la empresa como parámetro.',
     'parametros': {'empresa': EMPRESA}},
    {'metodo': 'GET', 'ruta': '/api/catalogos/ingreso',
     'resumen': 'Opciones válidas de empresa, área, puesto por área, turno y pago para asignar puesto.'},
    {'metodo': 'POST', 'ruta': '/api/guardar-ingreso',
     'resumen': 'Asignar puesto a un candidato pendiente (empresa, área, puesto, turno, salario, pago).'},
    {'metodo': 'DELETE', 'ruta': '/api/eliminar-pendiente/{id}',
     'resumen': 'Eliminar un candidato pendiente.'},

    # --- Reclutamiento ---------------------------------------------------------
    {'metodo': 'GET', 'ruta': '/api/datos-grafica',
     'resumen': 'Ingresos para las gráficas de reclutamiento. periodo=12meses: 12 totales (ene-dic del año actual); '
                'periodo=mes&mes=N: 6 totales por fuente en orden FACEBOOK, POSTEO, VOLANTE, RECOMENDACION, RECLU CW, RECLU PURO; '
                'periodo=reclutadores&mes=N: [{nombre, total}].',
     'parametros': {'periodo': '12meses | mes | reclutadores', 'mes': '1-12'}},
    {'metodo': 'GET', 'ruta': '/api/ingresos-por-team',
     'resumen': 'Ingresos del mes por equipo de reclutamiento y por reclutador.',
     'parametros': {'mes': '1-12', 'anio': 'AAAA'}},
    {'metodo': 'GET', 'ruta': '/api/exportar-reclutamiento', 'archivo': True,
     'resumen': 'Excel con el reporte de reclutamiento de un mes. Se envía a la persona.',
     'parametros': {'mes': '1-12', 'anio': 'AAAA'}},
    {'metodo': 'GET', 'ruta': '/api/reclutadores-nombres',
     'resumen': 'Reclutadores registrados con su equipo.'},
    {'metodo': 'POST', 'ruta': '/api/reclutadores-nombres',
     'resumen': 'Registrar un reclutador (nombre, telefono, team).'},
    {'metodo': 'PUT', 'ruta': '/api/reclutadores-nombres/{reclutador_id}',
     'resumen': 'Editar un reclutador.'},
    {'metodo': 'POST', 'ruta': '/api/reclutadores-nombres/{reclutador_id}',
     'resumen': 'Editar un reclutador (variante POST).'},
    {'metodo': 'DELETE', 'ruta': '/api/reclutadores-nombres/{reclutador_id}',
     'resumen': 'Dar de baja un reclutador.'},
    {'metodo': 'DELETE', 'ruta': '/api/reclutadores-nombres/{reclutador_id}/telefono',
     'resumen': 'Borrar el teléfono de un reclutador.'},

    # --- Agenda ---------------------------------------------------------------
    {'metodo': 'GET', 'ruta': '/api/shared-events',
     'resumen': 'Eventos del calendario compartido de RH (título, fechas, tipo, asistentes, descripción).'},
    {'metodo': 'POST', 'ruta': '/api/shared-events',
     'resumen': 'Guardar el calendario compartido de RH.'},
]

_PARAMETRO_FLASK = re.compile(r'<(?:[a-z]+:)?([a-z_]+)>')


def plantilla_de_regla(regla: str) -> str:
    """'/api/x/<int:registro_id>' -> '/api/x/{registro_id}'."""
    return _PARAMETRO_FLASK.sub(r'{\1}', regla)


def oculta(metodo: str, regla: str) -> bool:
    if not regla.startswith('/api/'):
        return True
    if (metodo, regla) in RUTAS_OCULTAS:
        return True
    return any(regla == p or regla.startswith(p + '/') or regla.startswith(p + '-') for p in PREFIJOS_OCULTOS)


def permisos_de(endpoint: str, metodo: str) -> list[str]:
    nivel = auth.permiso_requerido(endpoint, metodo)
    return {
        auth.ADMIN: ['admin'],
        auth.ADMIN_O_RECLUTAMIENTO: ['admin', 'reclutamiento'],
        auth.SESION: ['admin', 'reclutamiento', 'notificaciones'],
        auth.PUBLICO: ['admin', 'reclutamiento', 'notificaciones'],
    }[nivel]


def reglas(app):
    """(método, regla Flask, endpoint) de toda la app, sin HEAD/OPTIONS."""
    for regla in app.url_map.iter_rules():
        for metodo in sorted(regla.methods - {'HEAD', 'OPTIONS'}):
            yield metodo, regla.rule, regla.endpoint


def construir_guia(app) -> dict:
    """La guía con los permisos de cada ruta; falla si una entrada no existe."""
    por_plantilla = {(m, plantilla_de_regla(r)): (r, e) for m, r, e in reglas(app)}
    endpoints = []
    for entrada in GUIA:
        clave = (entrada['metodo'], entrada['ruta'])
        if clave not in por_plantilla:
            raise LookupError(f'La guía menciona {clave}, que no existe.')
        regla, endpoint = por_plantilla[clave]
        if oculta(entrada['metodo'], regla):
            raise LookupError(f'La guía menciona {clave}, que está oculta.')
        endpoints.append({
            'metodo': entrada['metodo'],
            'ruta': entrada['ruta'],
            'resumen': entrada['resumen'],
            'parametros': entrada.get('parametros', {}),
            'archivo': bool(entrada.get('archivo')),
            'escritura': entrada['metodo'] != 'GET',
            'permisos': permisos_de(endpoint, entrada['metodo']),
        })
    return {'version': 1, 'endpoints': endpoints}
