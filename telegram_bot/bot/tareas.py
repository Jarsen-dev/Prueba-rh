"""Tareas programadas: consultas que el asistente corre solo, a una hora fija.

- Se crean con el agente (``preparar_tarea``) y el botón Confirmar, como cualquier
  cambio; viven en el backend (``/api/tareas``, cada quien las suyas).
- Cada minuto el bot pide ``/interno/tareas/turno``. El backend ya dejó
  calculada la próxima ejecución, así que un reinicio no las repite.
- Cada tarea corre el agente **en solo lectura** (no puede preparar cambios),
  con la sesión, los permisos y el tope diario de tokens de su dueño, y deja
  su traza como cualquier respuesta.
- ``/tareas`` las lista y permite borrarlas (con doble clic).
"""

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from bot.backend import ErrorBackend
from bot.borrador import CambioPendiente
from bot.interfaz import boton, mostrar, partir, responder, teclado_confirmar, texto_error

logger = logging.getLogger(__name__)

INTERVALO = 60
DIAS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')


def descripcion(frecuencia: str, hora: int, minuto: int = 0, dias_semana: list[int] | None = None,
                dia_mes: int | None = None) -> str:
    """Igual que ``backend/tareas.descripcion``."""
    cuando = f'{hora:02d}:{minuto:02d}'
    if frecuencia == 'semanal':
        dias = [DIAS[d] for d in sorted(set(dias_semana or []))]
        texto = 'cada ' + (', '.join(dias[:-1]) + ' y ' + dias[-1] if len(dias) > 1 else dias[0])
    elif frecuencia == 'mensual':
        texto = f'el día {dia_mes} de cada mes'
    else:
        texto = 'todos los días'
    return f'{texto} a las {cuando}'


def pendiente_borrar(tarea: dict[str, Any], origen: str = 'menu') -> CambioPendiente:
    return CambioPendiente(
        'DELETE', f'/api/tareas/{int(tarea["id"])}', '🗑 Borrar tarea programada:',
        detalle=[f'Cuándo: {tarea.get("descripcion") or "?"}', f'Qué: «{tarea.get("instruccion") or ""}»'],
        origen=origen)


# --- Ejecución ---------------------------------------------------------------------

async def correr(app: Application, tarea: dict[str, Any]) -> None:
    """Corre una tarea con la sesión de su dueño y le manda el resultado."""
    from bot import conversacion  # import tardío: conversacion importa al agente

    datos = app.bot_data
    telegram_id, settings, backend = int(tarea['telegram_id']), datos['settings'], datos['backend']
    agente = datos.get('agente')
    encabezado = f'🗓 Tarea programada ({tarea.get("descripcion")}): «{tarea.get("instruccion")}»'
    if agente is None:
        await app.bot.send_message(chat_id=telegram_id, text=f'{encabezado}\n\nEl asistente no está activo; no '
                                                             'pude correrla.')
        return
    total, persona = await backend.presupuesto(telegram_id)
    if total >= settings.BOT_TOKENS_DIA or persona >= settings.BOT_TOKENS_PERSONA_DIA:
        await app.bot.send_message(chat_id=telegram_id, text=f'{encabezado}\n\nNo la corrí: ya se usó el tope '
                                                             'diario del asistente.')
        return
    ahora = datetime.now(ZoneInfo(settings.ZONA_HORARIA))
    resultado = await agente.responder(telegram_id, tarea['instruccion'], [], ahora, solo_lectura=True)
    try:
        await backend.registrar_traza(telegram_id, conversacion.datos_de_traza(resultado))
    except Exception as error:  # noqa: BLE001 - la traza nunca tumba la tarea
        logger.warning('No se pudo registrar la traza de una tarea: %s', error)
    for parte in partir(f'{encabezado}\n\n{resultado.texto}\n\n{conversacion.pie(resultado)}\n'
                        'Para ver o borrar tus tareas: /tareas'):
        await app.bot.send_message(chat_id=telegram_id, text=parte)
    for archivo in resultado.archivos:
        await app.bot.send_document(chat_id=telegram_id, document=archivo.contenido, filename=archivo.nombre)


async def repartir(app: Application) -> int:
    corridas = 0
    for tarea in await app.bot_data['backend'].tareas_turno():
        try:
            await correr(app, tarea)
            corridas += 1
        except Exception:  # noqa: BLE001 - una tarea que falla no detiene a las demás
            logger.warning('Falló la tarea programada %s.', tarea.get('id'), exc_info=True)
    return corridas


async def ciclo(app: Application, intervalo: float = INTERVALO) -> None:
    while True:
        try:
            corridas = await repartir(app)
            if corridas:
                logger.info('Tareas programadas corridas: %s', corridas)
        except Exception:  # noqa: BLE001 - el backend caído no debe matar el ciclo
            logger.warning('No se pudieron revisar las tareas programadas.', exc_info=True)
        await asyncio.sleep(intervalo)


def iniciar(app: Application) -> None:
    app.bot_data['tarea_tareas'] = asyncio.get_running_loop().create_task(ciclo(app))


async def detener(app: Application) -> None:
    tarea = app.bot_data.pop('tarea_tareas', None)
    if tarea is not None:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea


# --- /tareas -----------------------------------------------------------------------

async def cmd_tareas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    datos = context.application.bot_data
    estado = datos['estados'].de(update.effective_user.id)
    try:
        respuesta = await datos['backend'].peticion(update.effective_user.id, 'GET', '/api/tareas')
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    lista = list((respuesta.datos or {}).get('tareas') or [])
    estado.tareas = lista
    if not lista:
        await responder(update, '🗓 No tienes tareas programadas.\n\nPídemelas escribiendo, por ejemplo: «cada lunes '
                                'a las 9 mándame los ingresos de la semana pasada».')
        return
    lineas = [f'{n}. {t.get("descripcion")}: «{t.get("instruccion")}»' for n, t in enumerate(lista, 1)]
    filas = [[boton(f'🗑 Borrar {n}', f'tb:{n - 1}') for n in range(i + 1, min(i + 4, len(lista)) + 1)]
             for i in range(0, len(lista), 3)]
    await responder(update, '🗓 Tus tareas programadas (hora de México):\n\n' + '\n'.join(lineas),
                    InlineKeyboardMarkup(filas))


async def al_boton_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = context.application.bot_data['estados'].de(consulta.from_user.id)
    try:
        indice = int((consulta.data or '').removeprefix('tb:'))
    except ValueError:
        indice = -1
    lista = getattr(estado, 'tareas', None) or []
    if not 0 <= indice < len(lista):
        await mostrar(update, 'Esa lista ya no está vigente; vuelve a abrir /tareas.', editar=True)
        return
    pendiente = pendiente_borrar(lista[indice])
    async with estado.candado:
        estado.pendiente = pendiente
    await mostrar(update, pendiente.resumen(), teclado_confirmar(pendiente), editar=True)
