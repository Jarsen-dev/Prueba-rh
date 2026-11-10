"""Avisos automáticos (sin IA): un resumen al día, a la hora que elige cada persona.

Cada minuto el bot le pregunta al backend a quién ya le toca su resumen
(``/interno/avisos/turno``, que los marca como enviados para que nadie lo reciba
dos veces). Por cada persona pide ``GET /api/avisos`` **con la sesión de esa
persona**, así que solo ve lo que su cuenta puede ver. Si no hay nada que
avisar, no manda nada.

``/avisos`` muestra las preferencias (encendido y hora) y permite ver el
resumen de hoy en ese momento.
"""

import asyncio
import contextlib
import logging
from datetime import date
from typing import Any

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from bot.backend import ErrorBackend
from bot.interfaz import boton, mostrar, partir, responder, texto_error
from bot.menu.formularios import TITULOS_INCIDENCIA

logger = logging.getLogger(__name__)

INTERVALO = 60
HORAS = (6, 7, 8, 9, 10, 12, 14, 16, 18)
MAX_POR_SECCION = 10
DIAS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
PIE = 'Para cambiar la hora o apagar estos avisos: /avisos'


def _fecha(valor: str, hoy: date) -> str:
    try:
        dia = date.fromisoformat(str(valor)[:10])
    except ValueError:
        return '¿?'
    diferencia = (dia - hoy).days
    if diferencia == 0:
        return 'hoy'
    if diferencia == 1:
        return 'mañana'
    return f'{DIAS[dia.weekday()]} {dia:%d/%m}'


def _persona(item: dict[str, Any]) -> str:
    nombre = item.get('nombre_completo') or 'Sin nombre'
    return f'{nombre} ({item["no_empleado"]})' if item.get('no_empleado') else nombre


def _tipo(tipo: Any) -> str:
    return TITULOS_INCIDENCIA.get(str(tipo), str(tipo or 'incidencia')).split(' (')[0]


def _lista(items: list[str]) -> list[str]:
    extra = len(items) - MAX_POR_SECCION
    return items[:MAX_POR_SECCION] + ([f'• … y {extra} más'] if extra > 0 else [])


def formatear(datos: dict[str, Any]) -> str | None:
    """El resumen en texto, o None si no hay nada que avisar."""
    try:
        hoy = date.fromisoformat(str(datos.get('fecha')))
    except ValueError:
        hoy = date.today()
    secciones = []
    contratos = datos.get('contratos') or []
    if contratos:
        secciones.append(f'📄 Contratos que vencen en los próximos {datos.get("contratos_dias", 7)} días:\n' + '\n'.join(
            _lista([f'• {_persona(c)} · {c.get("empresa") or ""} · vence {_fecha(c["fin_contrato"], hoy)}'
                    for c in contratos])))
    reingresos = datos.get('reingresos') or []
    if reingresos:
        secciones.append('↩️ Regresan:\n' + '\n'.join(
            _lista([f'• {_persona(r)} · {_fecha(r["fecha"], hoy)} (de {_tipo(r.get("tipo")).lower()})'
                    for r in reingresos])))
    inicios = datos.get('inicios') or []
    if inicios:
        secciones.append('🗓 Empiezan:\n' + '\n'.join(
            _lista([f'• {_persona(i)} · {_tipo(i.get("tipo"))} · {_fecha(i["fecha"], hoy)}' for i in inicios])))
    candidatos = datos.get('candidatos') or {}
    if candidatos.get('sin_asignar'):
        texto = (f'🧑‍💼 {candidatos["sin_asignar"]} candidato(s) llevan más de {candidatos.get("dias", 7)} días '
                 'sin puesto asignado')
        lista = candidatos.get('lista') or []
        if lista:
            texto += ':\n' + '\n'.join(_lista([
                f'• {c.get("nombre_completo") or "Sin nombre"} · {c.get("dias_esperando")} días' for c in lista]))
        else:
            texto += '.'
        secciones.append(texto)
    if candidatos.get('registrados_ayer'):
        secciones.append(f'📝 Ayer se registraron {candidatos["registrados_ayer"]} candidato(s).')
    if not secciones:
        return None
    return f'🔔 Avisos de hoy ({DIAS[hoy.weekday()]} {hoy:%d/%m/%Y})\n\n' + '\n\n'.join(secciones)


async def resumen_de(backend, telegram_id: int) -> str | None:
    respuesta = await backend.peticion(telegram_id, 'GET', '/api/avisos')
    return formatear(respuesta.datos if isinstance(respuesta.datos, dict) else {})


async def repartir(app: Application) -> int:
    """Una vuelta: manda el resumen a quien ya le toca. Devuelve cuántos se mandaron."""
    backend = app.bot_data['backend']
    enviados = 0
    for telegram_id in await backend.avisos_turno():
        try:
            texto = await resumen_de(backend, telegram_id)
            if texto is None:
                continue
            for parte in partir(f'{texto}\n\n{PIE}'):
                await app.bot.send_message(chat_id=telegram_id, text=parte)
            enviados += 1
        except ErrorBackend as error:
            # Sin vínculo o sin permiso: simplemente no hay avisos para esa persona.
            logger.info('Sin avisos para una persona (%s).', error.status)
        except Exception:  # noqa: BLE001 - una persona (p. ej. bloqueó al bot) no detiene a las demás
            logger.warning('No se pudo mandar el resumen de avisos a una persona.', exc_info=True)
    return enviados


async def ciclo(app: Application, intervalo: float = INTERVALO) -> None:
    while True:
        try:
            enviados = await repartir(app)
            if enviados:
                logger.info('Avisos enviados: %s', enviados)
        except Exception:  # noqa: BLE001 - el backend caído no debe matar el ciclo
            logger.warning('No se pudieron repartir los avisos.', exc_info=True)
        await asyncio.sleep(intervalo)


def iniciar(app: Application) -> None:
    app.bot_data['tarea_avisos'] = asyncio.get_running_loop().create_task(ciclo(app))


async def detener(app: Application) -> None:
    tarea = app.bot_data.pop('tarea_avisos', None)
    if tarea is not None:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea


# --- /avisos -----------------------------------------------------------------------

def _pantalla(preferencias: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    activos, hora = preferencias.get('activos'), int(preferencias.get('hora') or 8)
    texto = ('🔔 Avisos diarios: ' + (f'encendidos, a las {hora}:00 (hora de México).' if activos else 'apagados.')
             + '\n\nTe aviso de contratos por vencer, quién regresa o empieza una incidencia y candidatos sin '
               'asignar, según los permisos de tu cuenta. Si un día no hay nada, no te escribo.')
    filas = [[boton('🔕 Apagar' if activos else '🔔 Encender', 'av:off' if activos else 'av:on'),
              boton('👀 Ver los de hoy', 'av:ver')]]
    if activos:
        horas = [boton(('✅ ' if h == hora else '') + f'{h}:00', f'av:h{h}') for h in HORAS]
        filas += [horas[i:i + 3] for i in range(0, len(horas), 3)]
    return texto, InlineKeyboardMarkup(filas)


async def cmd_avisos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    backend = context.application.bot_data['backend']
    try:
        texto, teclado = _pantalla(await backend.avisos_preferencias(update.effective_user.id))
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    await responder(update, texto, teclado)


async def al_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    backend = context.application.bot_data['backend']
    telegram_id = consulta.from_user.id
    accion = (consulta.data or '').removeprefix('av:')
    try:
        if accion == 'ver':
            texto = await resumen_de(backend, telegram_id)
            await responder(update, texto or '🔔 Hoy no hay avisos para tu cuenta.')
            return
        if accion in ('on', 'off'):
            preferencias = await backend.avisos_preferencias(telegram_id, activos=accion == 'on')
        elif accion.startswith('h') and accion[1:].isdigit() and int(accion[1:]) in HORAS:
            preferencias = await backend.avisos_preferencias(telegram_id, hora=int(accion[1:]))
        else:
            return
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)
        return
    texto, teclado = _pantalla(preferencias)
    await mostrar(update, texto, teclado, editar=True)
