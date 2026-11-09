"""Formularios del menú. Cada uno termina en un ``CambioPendiente``.

Los nombres de campo son los que manda el panel; ``tests/test_menu_formularios``
los compara con ``frontend/Ingreso.html`` y ``frontend/empleado.html``.
"""

import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from bot.borrador import Archivo, CambioPendiente
from bot.menu.asistente import Asistente, Paso, Respuestas, formatear_fecha

MAX_SALARIO = 50
MAX_MOTIVO = 1000
MAX_ARCHIVO = 10 * 1024 * 1024
TIPOS_ARCHIVO = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}


def _dato(valor: Any, defecto: str = 'Sin dato') -> str:
    return str(valor).strip() if valor not in (None, '') else defecto


# --- Asignar puesto (Ingreso.html) ---------------------------------------------

def normalizar_numero_temporal(valor: Any) -> str:
    """Igual que ``normalizarNumeroTemporal`` de Ingreso.html."""
    texto = str(valor or '').strip()
    if not texto:
        return ''
    if re.match(r'^TEMP\s*-', texto, re.IGNORECASE):
        return re.sub(r'^TEMP\s*-\s*', 'TEMP - ', texto, flags=re.IGNORECASE)
    return f'TEMP - {texto}'


def _validar_salario(valor: Any, _r: Respuestas) -> str:
    texto = ' '.join(str(valor or '').split())
    if not texto:
        raise ValueError('Escribe el salario.')
    if len(texto) > MAX_SALARIO:
        raise ValueError(f'El salario no puede pasar de {MAX_SALARIO} caracteres.')
    return texto


def pendiente_ingreso(candidato: dict[str, Any], catalogo: dict[str, Any], r: Respuestas,
                      origen: str = 'menu') -> CambioPendiente:
    """Igual que ``construirFilaPayload`` de Ingreso.html."""
    nombre = _dato(candidato.get('nombre_completo'), '')
    no_empleado = normalizar_numero_temporal(candidato.get('numero_temporal'))
    if not nombre or not no_empleado:
        raise ValueError('A este candidato le falta el nombre o el número temporal; asígnalo desde el panel.')
    cuerpo = {
        'nombre_candidato': nombre,
        'empresa': r['empresa'],
        'no_empleado': no_empleado,
        'personal': catalogo.get('personal') or 'OPERATIVO',
        'puesto': r['puesto'],
        'area': r['area'],
        'turno': r['turno'],
        'salario': r['salario'],
        'pago': r['pago'],
        'registro_id': str(int(candidato['id'])),
    }
    return CambioPendiente(
        'POST', '/api/guardar-ingreso',
        f'📝 Asignar puesto a {nombre}:',
        detalle=[
            f'Número: {no_empleado}',
            f'Empresa: {cuerpo["empresa"]}',
            f'Área: {cuerpo["area"]}',
            f'Puesto: {cuerpo["puesto"]}',
            f'Turno: {cuerpo["turno"]}',
            f'Salario: {cuerpo["salario"]}',
            f'Pago: {cuerpo["pago"]}',
        ],
        cuerpo=cuerpo,
        origen=origen,
    )


def asistente_ingreso(candidato: dict[str, Any], catalogo: dict[str, Any]) -> Asistente:
    nombre = _dato(candidato.get('nombre_completo'), '')
    if not nombre or not normalizar_numero_temporal(candidato.get('numero_temporal')):
        raise ValueError('A este candidato le falta el nombre o el número temporal; asígnalo desde el panel.')
    puestos = catalogo.get('puestos_por_area') or {}

    def terminar(r: Respuestas) -> CambioPendiente:
        return pendiente_ingreso(candidato, catalogo, r)

    return Asistente(
        titulo=f'Asignar puesto — {nombre}',
        pasos=[
            Paso('empresa', 'Empresa:', opciones=lambda _r: catalogo.get('empresas') or []),
            Paso('area', 'Área:', opciones=lambda _r: catalogo.get('areas') or []),
            Paso('puesto', 'Puesto:', opciones=lambda r: puestos.get(r.get('area'), [])),
            Paso('turno', 'Turno:', opciones=lambda _r: catalogo.get('turnos') or []),
            Paso('salario', 'Escribe el salario (tal como en el panel):', tipo='texto', validar=_validar_salario),
            Paso('pago', 'Esquema de pago:', opciones=lambda _r: catalogo.get('pagos') or []),
        ],
        al_terminar=terminar,
    )


# --- Incidencias (empleado.html) ---------------------------------------------

#: Etiqueta del botón -> (tipo del backend, título del panel)
TIPOS_INCIDENCIA = {
    'Vacaciones': ('vacaciones', 'Vacaciones'),
    'Incapacidad': ('incapacidad', 'Incapacidad'),
    'Suspensión': ('suspension', 'Suspensión'),
    'Carta compromiso': ('carta-suspension', 'Carta Compromiso'),
}
CON_MOTIVO = {'suspension', 'carta-suspension'}


def _tipo(r: Respuestas) -> str:
    return TIPOS_INCIDENCIA[r['tipo']][0]


def _validar_motivo(valor: Any, _r: Respuestas) -> str:
    texto = str(valor or '').strip()
    if not texto:
        raise ValueError('Escribe el motivo.')
    if len(texto) > MAX_MOTIVO:
        raise ValueError(f'El motivo no puede pasar de {MAX_MOTIVO} caracteres.')
    return texto


def _validar_hasta(valor: date, r: Respuestas) -> date:
    desde = r['desde']
    if valor < desde:
        raise ValueError('La fecha final no puede ser anterior a la inicial.')
    if (valor - desde).days + 1 > 999:
        raise ValueError('El periodo no puede pasar de 999 días.')
    return valor


def validar_archivo(nombre: str, contenido: bytes) -> Archivo:
    extension = ('.' + nombre.rsplit('.', 1)[-1].lower()) if '.' in nombre else ''
    if extension not in TIPOS_ARCHIVO:
        raise ValueError('El archivo debe ser PDF, PNG o JPG.')
    if len(contenido) > MAX_ARCHIVO:
        raise ValueError('El archivo no puede pasar de 10 MB.')
    return Archivo('archivo', nombre, contenido, TIPOS_ARCHIVO[extension])


#: Tipo del backend -> título del panel.
TITULOS_INCIDENCIA = {tipo: titulo for tipo, titulo in TIPOS_INCIDENCIA.values()}


def pendiente_incidencia(empleado: dict[str, Any], tipo: str, desde: date, hasta: date, motivo: str | None = None,
                         archivo: Archivo | None = None, origen: str = 'menu') -> CambioPendiente:
    """Igual que el formulario de incidencias de empleado.html (de fecha / a fecha)."""
    if tipo not in TITULOS_INCIDENCIA:
        raise ValueError(f'Tipo de incidencia no válido: {tipo}.')
    titulo = TITULOS_INCIDENCIA[tipo]
    hasta = _validar_hasta(hasta, {'desde': desde})
    if tipo in CON_MOTIVO:
        motivo = _validar_motivo(motivo, {})
    registro_id = int(empleado['registro_id'])
    nombre = _dato(empleado.get('nombre_completo'), 'Sin nombre')
    # El panel manda el día de reingreso (el siguiente al último) en suspensiones.
    reingreso = hasta + timedelta(days=1) if tipo in CON_MOTIVO else hasta
    dias = (hasta - desde).days + 1
    motivo = (motivo or '').strip() or titulo
    detalles = {'dias': dias, 'opcion': '', 'hora': '', 'motivos': [], 'otro_motivo': '', 'categoria': ''}
    formulario = {
        'tipo': tipo,
        'motivo': motivo,
        'fecha_inicio': desde.isoformat(),
        'fecha_reingreso': reingreso.isoformat(),
        'detalles': json.dumps(detalles, ensure_ascii=False),
    }
    detalle = [
        f'Empleado: {nombre} ({_dato(empleado.get("no_empleado"), "sin número")})',
        f'Tipo: {titulo}',
        f'Del {formatear_fecha(desde)} al {formatear_fecha(hasta)} ({dias} día{"s" if dias != 1 else ""})',
    ]
    if tipo in CON_MOTIVO:
        detalle.append(f'Reingresa: {formatear_fecha(reingreso)}')
        detalle.append(f'Motivo: {motivo}')
    return CambioPendiente(
        'POST', f'/api/empleados/{registro_id}/incidencias',
        '🗓 Registrar incidencia:',
        detalle=detalle,
        formulario=formulario,
        archivo=archivo,
        origen=origen,
    )


def asistente_incidencia(empleado: dict[str, Any], hoy: Callable[[], date] = date.today) -> Asistente:
    nombre = _dato(empleado.get('nombre_completo'), 'Sin nombre')

    def terminar(r: Respuestas) -> CambioPendiente:
        return pendiente_incidencia(empleado, TIPOS_INCIDENCIA[r['tipo']][0], r['desde'], r['hasta'],
                                    r.get('motivo'), r.get('archivo'))

    return Asistente(
        titulo=f'Incidencia — {nombre}',
        pasos=[
            Paso('tipo', 'Tipo de incidencia:', opciones=lambda _r: list(TIPOS_INCIDENCIA)),
            Paso('motivo', 'Escribe el motivo:', tipo='texto', validar=_validar_motivo,
                 aplica=lambda r: 'tipo' in r and _tipo(r) in CON_MOTIVO),
            Paso('desde', 'Primer día (DD/MM/AAAA):', tipo='fecha'),
            Paso('hasta', 'Último día (DD/MM/AAAA):', tipo='fecha', validar=_validar_hasta),
            Paso('archivo', 'Si tienes el documento firmado, envíalo como foto o PDF; si no, pulsa Omitir.',
                 tipo='archivo', omitible=True,
                 aplica=lambda r: 'tipo' in r and _tipo(r) in CON_MOTIVO),
        ],
        al_terminar=terminar,
        hoy=hoy,
    )


def asistente_documento(empleado: dict[str, Any], leer: Callable) -> Asistente:
    """Tipo de documento y archivo; ``leer(tipo, archivo)`` (async) arma el cambio con lo leído."""
    from bot import documentos

    nombre = _dato(empleado.get('nombre_completo'), 'Sin nombre')
    nombres = {t.nombre: t for t in documentos.TIPOS.values()}

    def validar(archivo: Archivo, _r: Respuestas) -> Archivo:
        documentos.validar(archivo)
        return archivo

    return Asistente(
        titulo=f'Actualizar con documento — {nombre}',
        pasos=[
            Paso('tipo', '¿Qué documento es?', opciones=lambda _r: list(nombres)),
            Paso('archivo', 'Envía el documento como foto o PDF (completo, con buena luz).', tipo='archivo',
                 validar=validar),
        ],
        # Devuelve una corrutina: la lectura es asíncrona (pantallas la espera).
        al_terminar=lambda r: leer(nombres[r['tipo']], r['archivo']),
    )


# --- Baja / reactivación y eliminación -----------------------------------------------

def pendiente_estatus(empleado: dict[str, Any], origen: str = 'menu') -> CambioPendiente:
    """Igual que el botón de estatus de los listados: solo manda ``estatus``."""
    activo = str(empleado.get('estatus') or 'ACTIVO').upper() == 'ACTIVO'
    nuevo = 'INACTIVO' if activo else 'ACTIVO'
    return CambioPendiente(
        'POST', f'/api/actualizar-empleado/{int(empleado["registro_id"])}',
        '⏸ Dar de baja (estatus INACTIVO):' if activo else '▶️ Reactivar (estatus ACTIVO):',
        detalle=[
            f'Nombre: {_dato(empleado.get("nombre_completo"), "Sin nombre")}',
            f'Número: {_dato(empleado.get("no_empleado"), "Sin asignar")}',
            f'Empresa: {_dato(empleado.get("empresa"))}',
        ],
        cuerpo={'estatus': nuevo},
        origen=origen,
    )


def pendiente_eliminar(empleado: dict[str, Any], origen: str = 'menu') -> CambioPendiente:
    return CambioPendiente(
        'DELETE', f'/api/eliminar-empleado/{int(empleado["registro_id"])}',
        '🗑 Eliminar definitivamente este empleado y todos sus datos:',
        detalle=[
            f'Nombre: {_dato(empleado.get("nombre_completo"), "Sin nombre")}',
            f'Número: {_dato(empleado.get("no_empleado"), "Sin asignar")}',
            f'Empresa: {_dato(empleado.get("empresa"))}',
            f'Puesto: {_dato(empleado.get("puesto"))}',
        ],
        origen=origen,
    )
