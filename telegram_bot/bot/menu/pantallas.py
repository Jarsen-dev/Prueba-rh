"""Pantallas del menú /sistema.

Prefijos de ``callback_data`` (todos ≤ 64 bytes):
  m:<nodo>      navegar o ejecutar un nodo de ``navegacion.NODOS``
  e:<i>         abrir el empleado i de la última lista
  ea:<acción>   acción sobre el empleado abierto (inc, reg, est, del)
  c:<i>         elegir el candidato i de los ingresos pendientes
  cp:<página>   página de la lista de candidatos
  a:<i|b|s|x>   formulario: opción i, atrás, omitir, cancelar

Los botones llevan índices, nunca ids ni rutas. Ningún camino de aquí escribe:
los formularios terminan en un ``CambioPendiente`` que confirma ``al_boton``.
"""

import inspect
import logging
from datetime import date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.backend import ClienteBackend, ErrorBackend
from bot.borrador import CambioPendiente
from bot.config import Settings
from bot.estado import EstadoUsuario, Estados
from bot.interfaz import (
    boton,
    formatear_empleado,
    mostrar,
    parametros_busqueda,
    responder,
    teclado_confirmar,
    texto_error,
)
from bot import documentos
from bot.menu import formularios
from bot.menu.asistente import Asistente
from bot.menu.navegacion import NODOS, hijos_visibles, padre_de, puede

logger = logging.getLogger(__name__)

POR_PAGINA = 8
#: Orden de las barras que devuelve /api/datos-grafica?periodo=mes (InicioReclu.html).
FUENTES = ['Facebook', 'Posteo', 'Volante', 'Recomendación', 'Reclu CW', 'Reclu puro']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']
CADUCADO = 'Este menú ya caducó. Ábrelo de nuevo con /sistema.'
EXPORTAR_EMPRESA = {'dl_cw': 'CHEONG_WOON', 'dl_kronos': 'KRONOS'}


def _backend(context) -> ClienteBackend:
    return context.application.bot_data['backend']


def _estados(context) -> Estados:
    return context.application.bot_data['estados']


def _settings(context) -> Settings:
    return context.application.bot_data['settings']


def hoy(context) -> date:
    return datetime.now(ZoneInfo(_settings(context).ZONA_HORARIA)).date()


def _menu_inicio() -> list:
    return [boton('🏠 Menú', 'm:raiz')]


async def _permisos(context, telegram_id: int) -> list[str]:
    return (await _backend(context).sesion(telegram_id)).permisos


# --- Navegación ---------------------------------------------------------------------

def teclado_nodo(clave: str, permisos: list[str]) -> InlineKeyboardMarkup:
    filas = [[boton(hijo.titulo, f'm:{hijo.clave}')] for hijo in hijos_visibles(clave, permisos)]
    padre = padre_de(clave)
    if padre:
        filas.append([boton('⬅️ Atrás', f'm:{padre}')])
    return InlineKeyboardMarkup(filas)


async def cmd_sistema(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    estado = _estados(context).de(telegram_id)
    estado.limpiar_menu()
    try:
        permisos = await _permisos(context, telegram_id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    await responder(update, '¿Qué quieres hacer?', teclado_nodo('raiz', permisos))


async def al_boton_nodo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    telegram_id = consulta.from_user.id
    clave = (consulta.data or '').removeprefix('m:')
    nodo = NODOS.get(clave)
    if nodo is None:
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        permisos = await _permisos(context, telegram_id)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)
        return
    if not puede(nodo, permisos):
        await mostrar(update, '🚫 Tu cuenta no tiene acceso a esta opción.', editar=True)
        return
    estado = _estados(context).de(telegram_id)
    estado.limpiar_menu()
    if nodo.hijos:
        titulo = '¿Qué quieres hacer?' if clave == 'raiz' else nodo.titulo
        await mostrar(update, titulo, teclado_nodo(clave, permisos), editar=True)
        return
    try:
        await ACCIONES[nodo.accion](update, context, estado)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), InlineKeyboardMarkup([_menu_inicio()]), editar=True)


# --- Empleados --------------------------------------------------------------------------

async def accion_buscar_empleado(update, context, estado: EstadoUsuario) -> None:
    estado.esperando = 'buscar'
    await mostrar(
        update,
        '🔎 Escribe el número de empleado o al menos 3 letras del nombre.',
        InlineKeyboardMarkup([_menu_inicio()]),
        editar=True,
    )


async def buscar_texto(update: Update, context, estado: EstadoUsuario, texto: str) -> None:
    params = parametros_busqueda(texto)
    if params is None:
        await responder(update, 'Escribe un número de empleado o al menos 3 letras del nombre.')
        return
    telegram_id = update.effective_user.id
    respuesta = await _backend(context).peticion(telegram_id, 'GET', '/api/empleados/buscar', params=params)
    empleados = list((respuesta.datos or {}).get('empleados') or [])
    if not empleados:
        await responder(update, 'No encontré empleados con ese dato. Escribe otro número o nombre.',
                        InlineKeyboardMarkup([_menu_inicio()]))
        return
    estado.esperando = None
    estado.lista, estado.tipo_lista = empleados, 'empleados'
    if len(empleados) == 1:
        await mostrar_ficha(update, context, estado, empleados[0])
        return
    filas = [
        [boton(f'{e.get("no_empleado") or "s/n"} · {e.get("nombre_completo") or "Sin nombre"}', f'e:{i}')]
        for i, e in enumerate(empleados)
    ]
    filas.append([boton('🔎 Buscar otro', 'm:empleados'), *_menu_inicio()])
    await responder(update, f'Encontré {len(empleados)} empleados. Elige uno:', InlineKeyboardMarkup(filas))


async def mostrar_ficha(update, context, estado: EstadoUsuario, empleado: dict[str, Any], *, editar=False) -> None:
    estado.seleccionado = empleado
    permisos = await _permisos(context, update.effective_user.id)
    filas = []
    if 'admin' in permisos:
        activo = str(empleado.get('estatus') or 'ACTIVO').upper() == 'ACTIVO'
        filas += [
            [boton('🗂 Historial de incidencias', 'ea:inc'), boton('➕ Registrar incidencia', 'ea:reg')],
            [boton('⏸ Dar de baja' if activo else '▶️ Reactivar', 'ea:est'), boton('🗑 Eliminar', 'ea:del')],
            [boton('📄 Actualizar con documento', 'ea:doc')],
        ]
    filas.append([boton('🔎 Buscar otro', 'm:empleados'), *_menu_inicio()])
    await mostrar(update, formatear_empleado(empleado), InlineKeyboardMarkup(filas), editar=editar)


async def al_boton_empleado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = _estados(context).de(consulta.from_user.id)
    try:
        indice = int((consulta.data or '').removeprefix('e:'))
    except ValueError:
        indice = -1
    if estado.tipo_lista != 'empleados' or not 0 <= indice < len(estado.lista):
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        await mostrar_ficha(update, context, estado, estado.lista[indice], editar=True)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)


def lector(context, telegram_id: int, empleado: dict[str, Any]):
    """La lectura del documento para el formulario: ficha fresca + OCR del backend + diff."""
    async def leer(tipo, archivo) -> CambioPendiente:
        backend = _backend(context)
        registro_id = int(empleado['registro_id'])
        actual = (await backend.peticion(telegram_id, 'GET', f'/api/obtener-empleado/{registro_id}')).datos or {}
        leidos = await documentos.escanear(backend, telegram_id, tipo, archivo)
        return documentos.pendiente_de_documento({**empleado, **actual, 'registro_id': registro_id},
                                                 tipo, leidos, archivo)
    return leer


async def al_boton_accion_empleado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    telegram_id = consulta.from_user.id
    estado = _estados(context).de(telegram_id)
    empleado = estado.seleccionado
    accion = (consulta.data or '').removeprefix('ea:')
    if empleado is None:
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        if accion == 'fic':
            await mostrar_ficha(update, context, estado, empleado, editar=True)
        elif accion == 'inc':
            await mostrar_incidencias(update, context, empleado)
        elif accion == 'reg':
            await iniciar_asistente(update, context, estado, formularios.asistente_incidencia(empleado, lambda: hoy(context)))
        elif accion == 'est':
            await proponer(update, estado, formularios.pendiente_estatus(empleado))
        elif accion == 'del':
            await proponer(update, estado, formularios.pendiente_eliminar(empleado))
        elif accion == 'doc':
            await iniciar_asistente(update, context, estado,
                                    formularios.asistente_documento(empleado, lector(context, telegram_id, empleado)))
        else:
            await mostrar(update, CADUCADO, editar=True)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)


def formatear_incidencia(incidencia: dict[str, Any]) -> str:
    def fecha(valor):
        try:
            return date.fromisoformat(str(valor)[:10]).strftime('%d/%m/%Y')
        except ValueError:
            return '¿?'

    partes = [str(incidencia.get('type') or 'incidencia').replace('-', ' ').capitalize()]
    if incidencia.get('fecha_inicio'):
        rango = fecha(incidencia['fecha_inicio'])
        if incidencia.get('fecha_reingreso') and incidencia['fecha_reingreso'] != incidencia['fecha_inicio']:
            rango += f' → {fecha(incidencia["fecha_reingreso"])}'
        partes.append(rango)
    if incidencia.get('dias'):
        partes.append(f'{incidencia["dias"]} día(s)')
    if incidencia.get('hora'):
        partes.append(f'{incidencia.get("opcion") or ""} {incidencia["hora"]}'.strip())
    motivo = str(incidencia.get('motivo') or '').strip()
    if motivo:
        partes.append(motivo if len(motivo) <= 80 else motivo[:79] + '…')
    texto = '• ' + ' · '.join(partes)
    return texto + (' 📎' if incidencia.get('archivo_nombre') else '')


async def mostrar_incidencias(update, context, empleado: dict[str, Any]) -> None:
    respuesta = await _backend(context).peticion(
        update.effective_user.id, 'GET', f'/api/empleados/{int(empleado["registro_id"])}/incidencias'
    )
    incidencias = list((respuesta.datos or {}).get('incidencias') or [])
    nombre = empleado.get('nombre_completo') or 'Sin nombre'
    if not incidencias:
        texto = f'🗂 {nombre}\n\nNo tiene incidencias registradas.'
    else:
        recientes = incidencias[-10:][::-1]
        texto = f'🗂 {nombre} — últimas {len(recientes)} de {len(incidencias)}:\n\n' + '\n'.join(
            formatear_incidencia(i) for i in recientes
        )
    filas = [[boton('➕ Registrar incidencia', 'ea:reg')], [boton('⬅️ Ficha', 'ea:fic'), *_menu_inicio()]]
    await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)


# --- Ingresos pendientes ---------------------------------------------------------------------

async def accion_pendientes(update, context, estado: EstadoUsuario) -> None:
    respuesta = await _backend(context).peticion(update.effective_user.id, 'GET', '/api/obtener-pendientes')
    candidatos = respuesta.datos if isinstance(respuesta.datos, list) else []
    estado.lista, estado.tipo_lista = candidatos, 'candidatos'
    await mostrar_pagina_candidatos(update, estado, 0)


async def mostrar_pagina_candidatos(update, estado: EstadoUsuario, pagina: int) -> None:
    candidatos = estado.lista
    if not candidatos:
        await mostrar(update, '📝 No hay ingresos pendientes.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    paginas = (len(candidatos) + POR_PAGINA - 1) // POR_PAGINA
    pagina = max(0, min(pagina, paginas - 1))
    inicio = pagina * POR_PAGINA
    filas = [
        [boton(f'{c.get("nombre_completo") or "Sin nombre"} ({formularios.normalizar_numero_temporal(c.get("numero_temporal")) or "sin número"})', f'c:{i}')]
        for i, c in enumerate(candidatos[inicio:inicio + POR_PAGINA], start=inicio)
    ]
    navegacion = []
    if pagina > 0:
        navegacion.append(boton('◀️', f'cp:{pagina - 1}'))
    if pagina < paginas - 1:
        navegacion.append(boton('▶️', f'cp:{pagina + 1}'))
    if navegacion:
        filas.append(navegacion)
    filas.append(_menu_inicio())
    texto = f'📝 Ingresos pendientes: {len(candidatos)}' + (f' (página {pagina + 1} de {paginas})' if paginas > 1 else '')
    await mostrar(update, texto + '\nElige a quién asignar puesto:', InlineKeyboardMarkup(filas), editar=True)


async def al_boton_candidato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = _estados(context).de(consulta.from_user.id)
    datos = consulta.data or ''
    if estado.tipo_lista != 'candidatos':
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        if datos.startswith('cp:'):
            await mostrar_pagina_candidatos(update, estado, int(datos[3:]))
            return
        indice = int(datos.removeprefix('c:'))
    except ValueError:
        await mostrar(update, CADUCADO, editar=True)
        return
    if not 0 <= indice < len(estado.lista):
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        catalogo = (await _backend(context).peticion(consulta.from_user.id, 'GET', '/api/catalogos/ingreso')).datos
        asistente = formularios.asistente_ingreso(estado.lista[indice], catalogo or {})
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)
        return
    except ValueError as error:
        await mostrar(update, f'⚠️ {error}', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    await iniciar_asistente(update, context, estado, asistente)


# --- Reclutamiento y descargas ----------------------------------------------------------------

async def accion_ind_mes(update, context, estado) -> None:
    dia = hoy(context)
    backend, telegram_id = _backend(context), update.effective_user.id
    por_fuente = (await backend.peticion(telegram_id, 'GET', '/api/datos-grafica',
                                         params={'periodo': 'mes', 'mes': dia.month})).datos or []
    por_mes = (await backend.peticion(telegram_id, 'GET', '/api/datos-grafica',
                                      params={'periodo': '12meses'})).datos or []
    lineas = [f'📊 Ingresos de {MESES[dia.month - 1]} {dia.year}']
    if isinstance(por_mes, list) and len(por_mes) == 12:
        lineas.append(f'Total del mes: {por_mes[dia.month - 1]} · En el año: {sum(por_mes)}')
    lineas.append('')
    for fuente, total in zip(FUENTES, por_fuente if isinstance(por_fuente, list) else []):
        lineas.append(f'• {fuente}: {total}')
    await mostrar(update, '\n'.join(lineas),
                  InlineKeyboardMarkup([[boton('⬅️ Atrás', 'm:reclutamiento'), *_menu_inicio()]]), editar=True)


async def accion_ind_team(update, context, estado) -> None:
    dia = hoy(context)
    datos = (await _backend(context).peticion(
        update.effective_user.id, 'GET', '/api/ingresos-por-team', params={'mes': dia.month, 'anio': dia.year}
    )).datos or {}
    lineas = [f'👥 Ingresos por equipo — {MESES[dia.month - 1]} {dia.year}', '']
    for equipo in datos.get('equipos') or []:
        lineas.append(f'Team {equipo.get("team")}: {equipo.get("total", 0)}')
        for reclutador in equipo.get('reclutadores') or []:
            lineas.append(f'   · {reclutador.get("nombre")}: {reclutador.get("total", 0)}')
    await mostrar(update, '\n'.join(lineas),
                  InlineKeyboardMarkup([[boton('⬅️ Atrás', 'm:reclutamiento'), *_menu_inicio()]]), editar=True)


async def _descargar(update, context, ruta: str, params: dict | None, nombre_defecto: str) -> None:
    await mostrar(update, '⏳ Generando el archivo…', editar=True)
    respuesta = await _backend(context).peticion(update.effective_user.id, 'GET', ruta, params=params, timeout=120)
    if not respuesta.es_archivo:
        await mostrar(update, '⚠️ El sistema no devolvió un archivo.', editar=True)
        return
    # El archivo va directo a la persona; nunca pasa por un modelo.
    await update.effective_message.reply_document(
        document=respuesta.contenido, filename=respuesta.nombre_archivo or nombre_defecto
    )
    await mostrar(update, '📥 Listo.', InlineKeyboardMarkup([[boton('⬅️ Descargas', 'm:descargas'), *_menu_inicio()]]),
                  editar=True)


async def accion_dl_empresa(update, context, estado) -> None:
    clave = (update.callback_query.data or '').removeprefix('m:')
    empresa = EXPORTAR_EMPRESA[clave]
    await _descargar(update, context, f'/api/exportar-empleados/{quote(empresa)}', {'todos': '1'},
                     f'listado_{empresa.lower()}.xlsx')


async def accion_dl_reclu(update, context, estado) -> None:
    dia = hoy(context)
    await _descargar(update, context, '/api/exportar-reclutamiento', {'mes': dia.month, 'anio': dia.year},
                     f'reclutamiento_{dia.year}_{dia.month:02d}.xlsx')


ACCIONES = {
    'buscar_empleado': accion_buscar_empleado,
    'pendientes': accion_pendientes,
    'ind_mes': accion_ind_mes,
    'ind_team': accion_ind_team,
    'dl_cw': accion_dl_empresa,
    'dl_kronos': accion_dl_empresa,
    'dl_reclu': accion_dl_reclu,
}


# --- Formularios ----------------------------------------------------------------------------

def teclado_asistente(asistente: Asistente) -> InlineKeyboardMarkup:
    opciones = asistente.opciones()
    filas, fila = [], []
    for i, opcion in enumerate(opciones):
        fila.append(boton(opcion, f'a:{i}'))
        if len(fila) == 2:
            filas.append(fila)
            fila = []
    if fila:
        filas.append(fila)
    paso = asistente.paso
    if paso is not None and paso.omitible:
        filas.append([boton('Omitir', 'a:s')])
    control = [boton('✖️ Cancelar', 'a:x')]
    if asistente.historial:
        control.insert(0, boton('⬅️ Atrás', 'a:b'))
    filas.append(control)
    return InlineKeyboardMarkup(filas)


def texto_paso(asistente: Asistente, aviso: str | None = None) -> str:
    actual, total = asistente.numero_de_paso()
    texto = f'{asistente.titulo}\nPaso {actual} de {total}\n\n{asistente.paso.pregunta}'
    return f'⚠️ {aviso}\n\n{texto}' if aviso else texto


async def iniciar_asistente(update, context, estado: EstadoUsuario, asistente: Asistente) -> None:
    estado.asistente = asistente
    estado.esperando = None
    await mostrar(update, texto_paso(asistente), teclado_asistente(asistente), editar=True)


async def proponer(update, estado: EstadoUsuario, pendiente: CambioPendiente, *, editar=True) -> None:
    """Deja el cambio esperando el botón Confirmar (uno por persona)."""
    async with estado.candado:
        estado.pendiente = pendiente
    await mostrar(update, pendiente.resumen(), teclado_confirmar(pendiente), editar=editar)


async def avanzar_asistente(update, estado: EstadoUsuario, *, editar: bool, aviso: str | None = None) -> None:
    asistente = estado.asistente
    if asistente.terminado:
        estado.asistente = None
        try:
            pendiente = asistente.construir()
            if inspect.isawaitable(pendiente):
                await mostrar(update, '🔍 Leyendo el documento…', editar=editar)
                editar = False
                pendiente = await pendiente
        except ValueError as error:
            await mostrar(update, f'⚠️ {error}', InlineKeyboardMarkup([_menu_inicio()]), editar=editar)
            return
        except ErrorBackend as error:
            await mostrar(update, texto_error(error), InlineKeyboardMarkup([_menu_inicio()]), editar=editar)
            return
        await proponer(update, estado, pendiente, editar=editar)
        return
    await mostrar(update, texto_paso(asistente, aviso), teclado_asistente(asistente), editar=editar)


async def al_boton_asistente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = _estados(context).de(consulta.from_user.id)
    asistente = estado.asistente
    if asistente is None:
        await mostrar(update, CADUCADO, editar=True)
        return
    valor = (consulta.data or '').removeprefix('a:')
    aviso = None
    try:
        if valor == 'x':
            estado.asistente = None
            await mostrar(update, '✖️ Formulario cancelado.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
            return
        if valor == 'b':
            asistente.atras()
        elif valor == 's':
            asistente.omitir()
        else:
            asistente.elegir(int(valor))
    except ValueError as error:
        aviso = str(error)
    await avanzar_asistente(update, estado, editar=True, aviso=aviso)


async def texto_asistente(update: Update, estado: EstadoUsuario, texto: str) -> None:
    paso = estado.asistente.paso
    if paso.tipo == 'opciones':
        await responder(update, 'Elige una opción con los botones del mensaje anterior.')
        return
    if paso.tipo == 'archivo':
        await responder(update, 'Envía el documento como foto o PDF, o pulsa «Omitir».')
        return
    aviso = None
    try:
        estado.asistente.responder(texto)
    except ValueError as error:
        aviso = str(error)
    await avanzar_asistente(update, estado, editar=False, aviso=aviso)


async def archivo_asistente(update: Update, context, estado: EstadoUsuario) -> None:
    mensaje = update.effective_message
    if mensaje.photo:
        foto = mensaje.photo[-1]
        nombre, tamano, file_id = 'foto.jpg', foto.file_size or 0, foto.file_id
    else:
        documento = mensaje.document
        nombre, tamano, file_id = documento.file_name or 'documento', documento.file_size or 0, documento.file_id
    aviso = None
    try:
        if tamano > formularios.MAX_ARCHIVO:
            raise ValueError('El archivo no puede pasar de 10 MB.')
        formularios.validar_archivo(nombre, b'')  # tipo antes de descargar
        archivo = await context.bot.get_file(file_id)
        contenido = bytes(await archivo.download_as_bytearray())
        estado.asistente.responder(formularios.validar_archivo(nombre, contenido))
    except ValueError as error:
        aviso = str(error)
    await avanzar_asistente(update, estado, editar=False, aviso=aviso)


# --- Entrada libre (la enruta principal.al_texto / al_archivo) -----------------------------

async def atender_texto(update: Update, context, texto: str) -> bool:
    """True si el menú consumió el texto."""
    estado = _estados(context).de(update.effective_user.id)
    try:
        if estado.asistente is not None:
            await texto_asistente(update, estado, texto)
            return True
        if estado.esperando == 'buscar':
            await buscar_texto(update, context, estado, texto)
            return True
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return True
    return False


async def atender_archivo(update: Update, context) -> bool:
    estado = _estados(context).de(update.effective_user.id)
    if estado.asistente is not None and estado.asistente.paso is not None and estado.asistente.paso.tipo == 'archivo':
        await archivo_asistente(update, context, estado)
        return True
    return False
