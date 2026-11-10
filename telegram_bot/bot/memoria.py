"""/memoria: ver y borrar las notas que el asistente recuerda de la persona.

Las notas se crean hablando con el asistente («recuerda que…», ``preparar_recuerdo``)
y viven en el backend (``/api/memoria``). Borrar pide Confirmar, como cualquier cambio.
"""

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.backend import ErrorBackend
from bot.escrituras import pendiente_olvidar
from bot.interfaz import boton, mostrar, responder, teclado_confirmar, texto_error

VACIA = ('🧠 No tengo notas guardadas de ti.\n\nPídemelo escribiendo, por ejemplo: «recuerda que cuando digo '
         '"la planta" me refiero a Kronos» o «recuerda que mis reportes son de Cheong Woon».')


async def cmd_memoria(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    datos = context.application.bot_data
    estado = datos['estados'].de(update.effective_user.id)
    try:
        respuesta = await datos['backend'].peticion(update.effective_user.id, 'GET', '/api/memoria')
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    notas = list((respuesta.datos or {}).get('notas') or [])
    estado.notas = notas
    if not notas:
        await responder(update, VACIA)
        return
    lineas = [f'{n}. {nota.get("texto")}' for n, nota in enumerate(notas, 1)]
    filas = [[boton(f'🗑 Olvidar {n}', f'mb:{n - 1}') for n in range(i + 1, min(i + 4, len(notas)) + 1)]
             for i in range(0, len(notas), 3)]
    await responder(update, '🧠 Lo que recuerdo de ti:\n\n' + '\n'.join(lineas), InlineKeyboardMarkup(filas))


async def al_boton_olvidar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = context.application.bot_data['estados'].de(consulta.from_user.id)
    try:
        indice = int((consulta.data or '').removeprefix('mb:'))
    except ValueError:
        indice = -1
    if not 0 <= indice < len(estado.notas):
        await mostrar(update, 'Esa lista ya no está vigente; vuelve a abrir /memoria.', editar=True)
        return
    pendiente = pendiente_olvidar(estado.notas[indice])
    async with estado.candado:
        estado.pendiente = pendiente
    await mostrar(update, pendiente.resumen(), teclado_confirmar(pendiente), editar=True)
