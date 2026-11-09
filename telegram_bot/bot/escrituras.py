"""Herramientas de escritura del agente (Fase 3).

**Ninguna escribe.** Cada una arma un ``CambioPendiente``, el mismo que produce el
menú y con exactamente el payload del panel (``menu/formularios.py``), y lo deja
en ``ctx.pendiente``. El bot le muestra a la persona el resumen con el botón
Confirmar, y solo ``principal.al_boton`` manda la petición. Hay un cambio por
mensaje como máximo.

El resumen lo genera el código a partir del payload real; el modelo no lo
redacta. Las guardas (``guardas.py``) revisan ids, fechas, números y catálogos
antes de armar el cambio. Al modelo solo le vuelve una frase neutra, sin los
datos del cambio.
"""

import re
from typing import TYPE_CHECKING, Any

from bot import documentos, tareas
from bot.borrador import CambioPendiente, Lote
from bot.guardas import (
    GuardaFallo,
    de_catalogo,
    fecha_mencionada,
    leer_fecha,
    normalizar,
    numero_mencionado,
    texto_mencionado,
)
from bot.menu import formularios
from bot.seguridad import RutaNoPermitida, buscar_endpoint, puede_usar

if TYPE_CHECKING:
    from bot.herramientas import Contexto

LISTO = ('Preparé el cambio «{titulo}». La persona ya ve el resumen con el botón Confirmar. TODAVÍA NO ESTÁ '
         'GUARDADO: díselo en una línea y pídele que revise el resumen y confirme.')
LISTO_LOTE = ('Agregué «{titulo}» al lote: ya son {total} cambios con un solo botón Confirmar. TODAVÍA NO ESTÁ '
              'GUARDADO. Si faltan más cambios de los que pidió, prepáralos; al terminar díselo en una línea.')
TIPOS = list(formularios.TITULOS_INCIDENCIA)
DIAS_SEMANA = ('lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo')
CAMPOS_PUESTO = ('empresa', 'area', 'puesto', 'turno', 'salario', 'pago')
NOMBRE_CAMPO = {'empresa': 'Empresa', 'area': 'Área', 'puesto': 'Puesto', 'turno': 'Turno', 'salario': 'Salario',
                'pago': 'Pago'}


def _funcion(nombre, descripcion, propiedades, requeridos=None):
    return {'type': 'function', 'function': {
        'name': nombre, 'description': descripcion,
        'parameters': {'type': 'object', 'properties': propiedades, 'required': requeridos or [],
                       'additionalProperties': False},
    }}


DEFINICIONES = [
    _funcion('preparar_estatus',
             'Prepara dar de baja (estatus INACTIVO) o reactivar a un empleado. Requiere el registro_id de buscar_empleado.',
             {'registro_id': {'type': 'integer'}, 'accion': {'type': 'string', 'enum': ['baja', 'reactivar']}},
             ['registro_id', 'accion']),
    _funcion('preparar_incidencia',
             'Prepara registrar una incidencia. Fechas AAAA-MM-DD tal como las dijo la persona: «desde» es el primer '
             'día y «hasta» el último (inclusive). suspension y carta-suspension exigen motivo y aceptan el archivo '
             'que mandó la persona (usar_adjunto).',
             {'registro_id': {'type': 'integer'}, 'tipo': {'type': 'string', 'enum': TIPOS},
              'desde': {'type': 'string'}, 'hasta': {'type': 'string'}, 'motivo': {'type': 'string'},
              'usar_adjunto': {'type': 'boolean'}},
             ['registro_id', 'tipo', 'desde', 'hasta']),
    _funcion('preparar_asignacion',
             'Prepara asignar puesto a un candidato pendiente (id de ver_pendientes). Todos los datos los da la '
             'persona; los valores válidos salen del catálogo.',
             {'candidato_id': {'type': 'integer'}, 'empresa': {'type': 'string'}, 'area': {'type': 'string'},
              'puesto': {'type': 'string'}, 'turno': {'type': 'string'}, 'salario': {'type': 'string'},
              'pago': {'type': 'string'}},
             ['candidato_id', 'empresa', 'area', 'puesto', 'turno', 'salario', 'pago']),
    _funcion('preparar_cambio_puesto',
             'Prepara cambiar empresa, área, puesto, turno, salario o pago de un empleado. Manda SOLO los campos '
             'que la persona pidió cambiar: los demás se quedan como están, así que no los preguntes (si cambia de '
             'puesto no hace falta pedir turno ni salario). Los datos personales (CURP, domicilio…) no van aquí: '
             'se actualizan con un documento (preparar_datos_de_documento) o en el panel.',
             {'registro_id': {'type': 'integer'}, **{c: {'type': 'string'} for c in CAMPOS_PUESTO}},
             ['registro_id']),
    _funcion('preparar_reclutador',
             'Prepara dar de alta, editar (teléfono o equipo) o dar de baja a un reclutador. Los reclutadores NO '
             'son empleados: no los busques con buscar_empleado; esta herramienta los encuentra por nombre.',
             {'accion': {'type': 'string', 'enum': ['alta', 'editar', 'baja']}, 'nombre': {'type': 'string'},
              'telefono': {'type': 'string'}, 'team': {'type': 'integer', 'minimum': 1, 'maximum': 4}},
             ['accion', 'nombre']),
    _funcion('preparar_eliminacion',
             'Prepara eliminar definitivamente un empleado, un candidato pendiente, una incidencia o una tarea '
             'programada (pide doble confirmación). Para incidencia: id de la incidencia (ver_incidencias) y '
             'registro_id del empleado. Para tarea: id de ver_tareas.',
             {'que': {'type': 'string', 'enum': ['empleado', 'candidato', 'incidencia', 'tarea']},
              'id': {'type': 'integer'}, 'registro_id': {'type': 'integer'}},
             ['que', 'id']),
    _funcion('preparar_tarea',
             'Prepara una tarea programada: una CONSULTA que el asistente correrá solo y le mandará a la persona '
             '(nunca hace cambios). instruccion: lo que hay que consultar, en una frase clara y autosuficiente, '
             'p. ej. «Dame el resumen de reclutamiento del mes con las fuentes». La hora (0-23, hora de México) y '
             'los días los dice la persona.',
             {'instruccion': {'type': 'string'},
              'frecuencia': {'type': 'string', 'enum': ['diaria', 'semanal', 'mensual']},
              'hora': {'type': 'integer', 'minimum': 0, 'maximum': 23},
              'minuto': {'type': 'integer', 'minimum': 0, 'maximum': 59},
              'dias_semana': {'type': 'array', 'items': {'type': 'string', 'enum': list(DIAS_SEMANA)}},
              'dia_mes': {'type': 'integer', 'minimum': 1, 'maximum': 28}},
             ['instruccion', 'frecuencia', 'hora']),
    _funcion('preparar_datos_de_documento',
             'Prepara actualizar los datos personales de un empleado leyendo el documento que la persona mandó por '
             'el chat (acta de nacimiento, comprobante de domicilio, constancia de situación fiscal o documento '
             'del NSS). El sistema lee el documento y le muestra a la persona el antes y el después; tú NO ves lo '
             'que se leyó. Úsala solo si la persona pide actualizar datos con ese documento.',
             {'registro_id': {'type': 'integer'},
              'tipo': {'type': 'string', 'enum': [t.nombre for t in documentos.TIPOS.values()]}},
             ['registro_id', 'tipo']),
]


async def _exigir_permiso(ctx: 'Contexto', metodo: str, ruta: str) -> None:
    if ctx.solo_lectura:
        raise GuardaFallo('Una tarea programada solo consulta; no puede preparar cambios.')
    if ctx.pendiente is not None and ctx.pendiente.siguiente is not None:
        raise GuardaFallo('La actualización con documento va sola. Dile a la persona que la confirme y luego pida '
                          'lo demás.')
    try:
        endpoint = buscar_endpoint(await ctx.backend.guia(), metodo, ruta)
    except RutaNoPermitida as error:
        raise GuardaFallo(str(error)) from error
    if not endpoint.get('escritura') or not puede_usar(endpoint, ctx.permisos):
        raise GuardaFallo('La cuenta de la persona no tiene permiso para este cambio.')


def _dejar(ctx: 'Contexto', pendiente: CambioPendiente) -> str:
    """El primer cambio queda solo; los siguientes del mismo mensaje forman un lote con un solo Confirmar."""
    actual = ctx.pendiente
    if actual is None:
        ctx.pendiente = pendiente
        return LISTO.format(titulo=pendiente.titulo.rstrip(':'))
    previos = actual.cambios if isinstance(actual, Lote) else [actual]
    clave = (pendiente.metodo, pendiente.ruta, repr(pendiente.cuerpo), repr(pendiente.formulario))
    if any((c.metodo, c.ruta, repr(c.cuerpo), repr(c.formulario)) == clave for c in previos):
        raise GuardaFallo('Ese cambio ya está preparado en este mensaje.')
    try:
        if isinstance(actual, Lote):
            actual.agregar(pendiente)
        else:
            ctx.pendiente = Lote([actual, pendiente])
    except ValueError as error:
        raise GuardaFallo(f'{error} Prepara los demás después de que la persona confirme.') from error
    return LISTO_LOTE.format(titulo=pendiente.titulo.rstrip(':'), total=len(ctx.pendiente.cambios))


def _entero(valor: Any, que: str) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError) as exc:
        raise GuardaFallo(f'{que} debe ser un número.') from exc


async def _estatus_actual(ctx: 'Contexto', empleado: dict[str, Any]) -> str:
    if not empleado.get('estatus') and empleado.get('no_empleado'):
        await ctx.get('/api/empleados/buscar', {'no_empleado': empleado['no_empleado']})
        empleado = ctx.vistos.empleado(empleado['registro_id'])
    return str(empleado.get('estatus') or 'ACTIVO').upper()


# --- Implementaciones ---------------------------------------------------------------------

async def preparar_estatus(ctx: 'Contexto', registro_id: Any = None, accion: str = '') -> str:
    empleado = ctx.vistos.empleado(registro_id)
    ruta = f'/api/actualizar-empleado/{int(registro_id)}'
    await _exigir_permiso(ctx, 'POST', ruta)
    if accion not in ('baja', 'reactivar'):
        raise GuardaFallo('accion debe ser «baja» o «reactivar».')
    actual = await _estatus_actual(ctx, empleado)
    if (accion == 'baja') == (actual == 'INACTIVO'):
        raise GuardaFallo(f'El empleado ya está {actual}; no hay nada que cambiar.')
    return _dejar(ctx, formularios.pendiente_estatus({**ctx.vistos.empleado(registro_id), 'estatus': actual}, ctx.origen))


async def preparar_incidencia(ctx: 'Contexto', registro_id: Any = None, tipo: str = '', desde: Any = None,
                              hasta: Any = None, motivo: str | None = None, usar_adjunto: bool = False) -> str:
    empleado = ctx.vistos.empleado(registro_id)
    await _exigir_permiso(ctx, 'POST', f'/api/empleados/{int(registro_id)}/incidencias')
    if tipo not in TIPOS:
        raise GuardaFallo(f'tipo debe ser uno de: {", ".join(TIPOS)}. Otros tipos se registran en el panel.')
    inicio = fecha_mencionada(leer_fecha(desde), ctx.textos, ctx.hoy)
    fin = fecha_mencionada(leer_fecha(hasta), ctx.textos, ctx.hoy)
    archivo = None
    if usar_adjunto:
        if tipo not in formularios.CON_MOTIVO:
            raise GuardaFallo('Solo las suspensiones y cartas compromiso llevan archivo.')
        if ctx.adjunto is None:
            raise GuardaFallo('La persona no ha enviado ningún archivo. Pídele que mande la foto o el PDF.')
        archivo = ctx.adjunto
    try:
        pendiente = formularios.pendiente_incidencia(empleado, tipo, inicio, fin, motivo, archivo, ctx.origen)
    except ValueError as error:
        raise GuardaFallo(str(error)) from error
    return _dejar(ctx, pendiente)


async def _catalogo(ctx: 'Contexto') -> dict[str, Any]:
    return (await ctx.get('/api/catalogos/ingreso')).datos or {}


def _salario(valor: Any, ctx: 'Contexto') -> str:
    numero_mencionado(valor, ctx.textos)
    # Como el panel: solo el número («$2,600 pesos» -> «2600»).
    texto = re.sub(r'(?<=\d),(?=\d{3}\b)', '', str(valor))
    texto = re.sub(r'(?i)[$\s]|pesos|mxn', '', texto)
    if not re.fullmatch(r'\d+(\.\d{1,2})?', texto):
        raise GuardaFallo(f'«{valor}» no es un salario válido; pide solo la cantidad.')
    if len(texto) > formularios.MAX_SALARIO:
        raise GuardaFallo('El salario es demasiado largo.')
    return texto


async def preparar_asignacion(ctx: 'Contexto', candidato_id: Any = None, empresa: str = '', area: str = '',
                              puesto: str = '', turno: str = '', salario: str = '', pago: str = '') -> str:
    candidato = ctx.vistos.candidato(candidato_id)
    await _exigir_permiso(ctx, 'POST', '/api/guardar-ingreso')
    catalogo = await _catalogo(ctx)
    area = de_catalogo(area, catalogo.get('areas') or [], 'El área')
    respuestas = {
        'empresa': de_catalogo(empresa, catalogo.get('empresas') or [], 'La empresa'),
        'area': area,
        'puesto': de_catalogo(puesto, (catalogo.get('puestos_por_area') or {}).get(area, []), f'El puesto para {area}'),
        'turno': de_catalogo(turno, catalogo.get('turnos') or [], 'El turno'),
        'salario': _salario(salario, ctx),
        'pago': de_catalogo(pago, catalogo.get('pagos') or [], 'El pago'),
    }
    try:
        pendiente = formularios.pendiente_ingreso(candidato, catalogo, respuestas, ctx.origen)
    except ValueError as error:
        raise GuardaFallo(str(error)) from error
    return _dejar(ctx, pendiente)


async def preparar_cambio_puesto(ctx: 'Contexto', registro_id: Any = None, **campos: Any) -> str:
    ctx.vistos.empleado(registro_id)
    registro_id = int(registro_id)
    ruta = f'/api/actualizar-empleado/{registro_id}'
    await _exigir_permiso(ctx, 'POST', ruta)
    desconocidos = set(campos) - set(CAMPOS_PUESTO)
    if desconocidos:
        raise GuardaFallo(f'Por aquí solo se cambian {", ".join(CAMPOS_PUESTO)}; lo demás ({", ".join(desconocidos)}) en el panel.')
    pedidos = {c: v for c, v in campos.items() if v not in (None, '')}
    if not pedidos:
        raise GuardaFallo('No indicaste qué cambiar.')
    # Datos frescos: el resumen muestra antes -> después con lo que hay hoy en la BD.
    actual = (await ctx.get(f'/api/obtener-empleado/{registro_id}')).datos or {}
    catalogo = await _catalogo(ctx)
    nuevos: dict[str, str] = {}
    if 'empresa' in pedidos:
        nuevos['empresa'] = de_catalogo(pedidos['empresa'], catalogo.get('empresas') or [], 'La empresa')
    if 'area' in pedidos:
        nuevos['area'] = de_catalogo(pedidos['area'], catalogo.get('areas') or [], 'El área')
    area_final = nuevos.get('area') or actual.get('area') or ''
    puestos = (catalogo.get('puestos_por_area') or {}).get(area_final, [])
    if 'puesto' in pedidos:
        nuevos['puesto'] = de_catalogo(pedidos['puesto'], puestos, f'El puesto para {area_final}')
    elif 'area' in nuevos and normalizar(actual.get('puesto')) not in {normalizar(p) for p in puestos}:
        raise GuardaFallo(f'El puesto actual no existe en {area_final}; pregunta también el puesto nuevo.')
    if 'turno' in pedidos:
        nuevos['turno'] = de_catalogo(pedidos['turno'], catalogo.get('turnos') or [], 'El turno')
    if 'pago' in pedidos:
        nuevos['pago'] = de_catalogo(pedidos['pago'], catalogo.get('pagos') or [], 'El pago')
    if 'salario' in pedidos:
        nuevos['salario'] = _salario(pedidos['salario'], ctx)
    cambios = {c: v for c, v in nuevos.items() if normalizar(v) != normalizar(actual.get(c))}
    if not cambios:
        raise GuardaFallo('El empleado ya tiene esos valores; no hay nada que cambiar.')
    nombre = actual.get('nombre_completo') or 'Sin nombre'
    detalle = [f'Empleado: {nombre} ({actual.get("no_empleado") or "sin número"})']
    detalle += [f'{NOMBRE_CAMPO[c]}: {actual.get(c) or "Sin dato"} → {v}' for c, v in cambios.items()]
    # actualizar-empleado solo toca los campos que llegan con valor: mandar solo
    # lo que cambia no borra nada.
    return _dejar(ctx, CambioPendiente('POST', ruta, '✏️ Cambiar datos de puesto:', detalle=detalle,
                                       cuerpo=cambios, origen=ctx.origen))


def _encontrar_reclutador(lista: list[dict[str, Any]], nombre: str) -> dict[str, Any] | None:
    buscado = normalizar(nombre)
    exactos = [r for r in lista if normalizar(r.get('nombre')) == buscado]
    if exactos:
        return exactos[0]
    parciales = [r for r in lista if buscado and buscado in normalizar(r.get('nombre'))]
    if len(parciales) > 1:
        raise GuardaFallo(f'«{nombre}» coincide con varios reclutadores; pide el nombre completo.')
    return parciales[0] if parciales else None


def _ocultar_telefono(valor: Any) -> str:
    digitos = ''.join(c for c in str(valor or '') if c.isdigit())
    return f'****{digitos[-4:]}' if len(digitos) > 4 else 'Sin teléfono'


async def preparar_reclutador(ctx: 'Contexto', accion: str = '', nombre: str = '', telefono: str | None = None,
                              team: Any = None) -> str:
    if accion not in ('alta', 'editar', 'baja'):
        raise GuardaFallo('accion debe ser alta, editar o baja.')
    lista = (await ctx.get('/api/reclutadores-nombres')).datos or []
    existente = _encontrar_reclutador(lista if isinstance(lista, list) else [], nombre)
    if team not in (None, ''):
        team = _entero(team, 'El equipo')
        numero_mencionado(team, ctx.textos)
        if not 1 <= team <= 4:
            raise GuardaFallo('El equipo va del 1 al 4.')
    if telefono not in (None, ''):
        telefono = numero_mencionado(telefono, ctx.textos)

    if accion == 'alta':
        await _exigir_permiso(ctx, 'POST', '/api/reclutadores-nombres')
        if existente:
            raise GuardaFallo(f'Ya existe un reclutador llamado {existente.get("nombre")}.')
        nombre = texto_mencionado(nombre, ctx.textos, 'El nombre')
        if not telefono or team in (None, ''):
            raise GuardaFallo('Para el alta hacen falta el teléfono y el equipo (1 a 4); pregúntalos.')
        return _dejar(ctx, CambioPendiente(
            'POST', '/api/reclutadores-nombres', '👤 Dar de alta reclutador:',
            detalle=[f'Nombre: {nombre}', f'Teléfono: {telefono}', f'Equipo: {team}'],
            cuerpo={'nombre': nombre, 'telefono': telefono, 'team': team}, origen=ctx.origen))

    if existente is None:
        raise GuardaFallo(f'No encontré a ningún reclutador llamado «{nombre}».')
    ruta = f'/api/reclutadores-nombres/{int(existente["id"])}'
    if accion == 'baja':
        await _exigir_permiso(ctx, 'DELETE', ruta)
        return _dejar(ctx, CambioPendiente(
            'DELETE', ruta, '🗑 Dar de baja reclutador:',
            detalle=[f'Nombre: {existente.get("nombre")}', f'Equipo: {existente.get("team")}'], origen=ctx.origen))

    await _exigir_permiso(ctx, 'PUT', ruta)
    if not telefono and team in (None, ''):
        raise GuardaFallo('Indica el teléfono o el equipo nuevos.')
    # El PUT reescribe teléfono y equipo (sin «team» lo regresa al 1): se mandan
    # los dos, completando con lo que ya tiene.
    cuerpo = {
        'telefono': telefono or str(existente.get('telefono') or ''),
        'team': team if team not in (None, '') else int(existente.get('team') or 1),
    }
    if not cuerpo['telefono']:
        raise GuardaFallo('El reclutador no tiene teléfono registrado; pide uno, el sistema lo exige.')
    detalle = [f'Nombre: {existente.get("nombre")}']
    if telefono:
        detalle.append(f'Teléfono: {_ocultar_telefono(existente.get("telefono"))} → {telefono}')
    if team not in (None, ''):
        detalle.append(f'Equipo: {existente.get("team")} → {team}')
    return _dejar(ctx, CambioPendiente('PUT', ruta, '✏️ Editar reclutador:', detalle=detalle, cuerpo=cuerpo,
                                       origen=ctx.origen))


async def preparar_eliminacion(ctx: 'Contexto', que: str = '', id: Any = None, registro_id: Any = None) -> str:  # noqa: A002
    if que == 'empleado':
        empleado = ctx.vistos.empleado(id)
        pendiente = formularios.pendiente_eliminar(empleado, ctx.origen)
    elif que == 'candidato':
        candidato = ctx.vistos.candidato(id)
        pendiente = CambioPendiente(
            'DELETE', f'/api/eliminar-pendiente/{int(id)}', '🗑 Eliminar candidato pendiente:',
            detalle=[f'Nombre: {candidato.get("nombre_completo") or "Sin nombre"}',
                     f'Número temporal: {formularios.normalizar_numero_temporal(candidato.get("numero_temporal")) or "Sin número"}'],
            origen=ctx.origen)
    elif que == 'incidencia':
        clave = (_entero(registro_id, 'registro_id'), _entero(id, 'id'))
        incidencia = ctx.vistos.incidencias.get(clave)
        if incidencia is None:
            raise GuardaFallo('Esa incidencia no salió de ninguna consulta; consulta primero ver_incidencias.')
        empleado = ctx.vistos.empleados.get(clave[0], {})
        pendiente = CambioPendiente(
            'DELETE', f'/api/empleados/{clave[0]}/incidencias/{clave[1]}', '🗑 Eliminar incidencia:',
            detalle=[f'Empleado: {empleado.get("nombre_completo") or clave[0]}',
                     f'Tipo: {incidencia.get("type")}',
                     f'Fechas: {incidencia.get("fecha_inicio") or "?"} → {incidencia.get("fecha_reingreso") or "?"}'],
            origen=ctx.origen)
    elif que == 'tarea':
        tarea = ctx.vistos.tareas.get(_entero(id, 'id'))
        if tarea is None:
            raise GuardaFallo('Esa tarea no salió de ninguna consulta; consulta primero ver_tareas.')
        pendiente = tareas.pendiente_borrar(tarea, ctx.origen)
    else:
        raise GuardaFallo('que debe ser empleado, candidato, incidencia o tarea.')
    await _exigir_permiso(ctx, 'DELETE', pendiente.ruta)
    return _dejar(ctx, pendiente)


def _hora_mencionada(hora: int, textos: list[str]) -> None:
    """«a las 9», «a las 18:00» o «6 de la tarde» (18)."""
    for candidata in [hora] + ([hora - 12] if hora > 12 else []):
        try:
            numero_mencionado(candidata, textos)
            return
        except GuardaFallo:
            continue
    raise GuardaFallo(f'La hora {hora}:00 no aparece en lo que escribió la persona; pregúntale a qué hora.')


async def preparar_tarea(ctx: 'Contexto', instruccion: str = '', frecuencia: str = '', hora: Any = None,
                         minuto: Any = 0, dias_semana: list | None = None, dia_mes: Any = None) -> str:
    await _exigir_permiso(ctx, 'POST', '/api/tareas')
    instruccion = ' '.join(str(instruccion or '').split())
    if not instruccion or len(instruccion) > 300:
        raise GuardaFallo('La instrucción debe ser una frase de hasta 300 caracteres.')
    if frecuencia not in ('diaria', 'semanal', 'mensual'):
        raise GuardaFallo('frecuencia debe ser diaria, semanal o mensual.')
    hora, minuto = _entero(hora, 'La hora'), _entero(minuto or 0, 'El minuto')
    if not 0 <= hora <= 23 or not 0 <= minuto <= 59:
        raise GuardaFallo('La hora va de 0 a 23 y el minuto de 0 a 59.')
    _hora_mencionada(hora, ctx.textos)
    if minuto:
        numero_mencionado(minuto, ctx.textos)
    cuerpo: dict[str, Any] = {'instruccion': instruccion, 'frecuencia': frecuencia, 'hora': hora, 'minuto': minuto}
    if frecuencia == 'semanal':
        dichos = normalizar(' '.join(ctx.textos))
        dias = []
        for dia in dias_semana or []:
            nombre = normalizar(dia)
            if nombre not in DIAS_SEMANA:
                raise GuardaFallo(f'«{dia}» no es un día de la semana.')
            if nombre not in dichos:
                raise GuardaFallo(f'La persona no mencionó el {dia}; pregúntale qué días.')
            dias.append(DIAS_SEMANA.index(nombre))
        if not dias:
            raise GuardaFallo('Falta qué días de la semana; pregúntaselo.')
        cuerpo['dias_semana'] = sorted(set(dias))
    if frecuencia == 'mensual':
        dia_mes = _entero(dia_mes, 'El día del mes')
        if not 1 <= dia_mes <= 28:
            raise GuardaFallo('El día del mes va del 1 al 28.')
        numero_mencionado(dia_mes, ctx.textos)
        cuerpo['dia_mes'] = dia_mes
    cuando = tareas.descripcion(frecuencia, hora, minuto, cuerpo.get('dias_semana'), cuerpo.get('dia_mes'))
    return _dejar(ctx, CambioPendiente(
        'POST', '/api/tareas', '🗓 Programar tarea:',
        detalle=[f'Cuándo: {cuando} (hora de México)', f'Qué: «{instruccion}»',
                 'Solo consulta: nunca hace cambios. Puedes verlas o borrarlas con /tareas.'],
        cuerpo=cuerpo, origen=ctx.origen))


async def preparar_datos_de_documento(ctx: 'Contexto', registro_id: Any = None, tipo: str = '') -> str:
    empleado = ctx.vistos.empleado(registro_id)
    registro_id = int(registro_id)
    if ctx.pendiente is not None:
        raise GuardaFallo('La actualización con documento va sola; pídela en otro mensaje después de confirmar.')
    await _exigir_permiso(ctx, 'POST', f'/api/actualizar-empleado/{registro_id}')
    await _exigir_permiso(ctx, 'POST', '/api/employee-files')
    if ctx.adjunto is None:
        raise GuardaFallo('La persona no ha enviado ningún documento. Pídele que mande la foto o el PDF y luego te '
                          'diga de quién es.')
    try:
        tipo_documento = documentos.tipo_por_texto(tipo)
        actual = (await ctx.get(f'/api/obtener-empleado/{registro_id}')).datos or {}
        # La lectura la usa solo el código; nada de lo leído vuelve al modelo.
        ctx.peticiones.append(('POST', '/api/escanear-documento', {}))
        leidos = await documentos.escanear(ctx.backend, ctx.telegram_id, tipo_documento, ctx.adjunto)
        pendiente = documentos.pendiente_de_documento({**empleado, **actual, 'registro_id': registro_id},
                                                      tipo_documento, leidos, ctx.adjunto, ctx.origen)
    except ValueError as error:
        raise GuardaFallo(str(error)) from error
    return _dejar(ctx, pendiente)


IMPLEMENTACIONES = {
    'preparar_estatus': preparar_estatus,
    'preparar_incidencia': preparar_incidencia,
    'preparar_asignacion': preparar_asignacion,
    'preparar_cambio_puesto': preparar_cambio_puesto,
    'preparar_reclutador': preparar_reclutador,
    'preparar_eliminacion': preparar_eliminacion,
    'preparar_datos_de_documento': preparar_datos_de_documento,
    'preparar_tarea': preparar_tarea,
}
assert set(IMPLEMENTACIONES) == {d['function']['name'] for d in DEFINICIONES}
