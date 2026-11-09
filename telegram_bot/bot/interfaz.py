"""Piezas de interfaz compartidas por los comandos y el menú."""

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from bot.backend import ErrorBackend, NoVinculado
from bot.borrador import CambioPendiente

MAX_MENSAJE = 4096
MAX_ETIQUETA = 60

TEXTO_NO_VINCULADO = (
    'Este Telegram no está vinculado a una cuenta del sistema de RH.\n\n'
    'En el panel abre «Telegram», genera un código y envíalo aquí con:\n'
    '/vincular CÓDIGO\n\n'
    'O abre directamente el enlace que te muestra el panel.'
)


def formatear_empleado(empleado: dict[str, Any]) -> str:
    def dato(clave: str, defecto: str = 'Sin dato') -> str:
        valor = empleado.get(clave)
        return str(valor).strip() if valor not in (None, '') else defecto

    return '\n'.join((
        f'👤 {dato("nombre_completo", "Sin nombre")}',
        f'Número: {dato("no_empleado", "Sin asignar")}',
        f'Empresa: {dato("empresa")}',
        f'Puesto: {dato("puesto")}',
        f'Área: {dato("area")}',
        f'Turno: {dato("turno")}',
        f'Estatus: {dato("estatus")}',
    ))


def parametros_busqueda(texto: str) -> dict[str, str] | None:
    """Número de empleado exacto si son solo dígitos; si no, parte del nombre."""
    texto = ' '.join((texto or '').split())
    if not texto:
        return None
    if texto.isdigit():
        return {'no_empleado': texto}
    if len(texto) < 3:
        return None
    return {'q': texto}


def texto_error(error: Exception) -> str:
    if isinstance(error, NoVinculado):
        return TEXTO_NO_VINCULADO
    if isinstance(error, ErrorBackend):
        if error.status == 401:
            return ('Tu sesión ya no es válida: desvincularon este Telegram o cambió la '
                    'contraseña de tu cuenta. Vuelve a vincularlo desde el panel.')
        if error.status == 403:
            return '🚫 Tu cuenta no tiene permiso para esta acción.'
        if error.status == 503 and error.codigo == 'BOT_APAGADO':
            return 'El bot está desactivado en el sistema.'
        return f'⚠️ {error.mensaje}'
    return '⚠️ Ocurrió un error inesperado. Intenta de nuevo.'


def etiqueta(texto: str) -> str:
    texto = ' '.join(str(texto).split())
    return texto if len(texto) <= MAX_ETIQUETA else texto[:MAX_ETIQUETA - 1] + '…'


def boton(texto: str, datos: str) -> InlineKeyboardButton:
    if len(datos.encode()) > 64:
        raise ValueError(f'callback_data demasiado largo: {datos!r}')
    return InlineKeyboardButton(etiqueta(texto), callback_data=datos)


def teclado_confirmar(pendiente: CambioPendiente) -> InlineKeyboardMarkup:
    peligro = pendiente.metodo == 'DELETE'
    return InlineKeyboardMarkup([[
        boton('🗑 Sí, eliminar' if peligro else '✅ Confirmar', f'ok:{pendiente.id}'),
        boton('✖️ Cancelar', f'no:{pendiente.id}'),
    ]])


def partir(texto: str, tope: int = MAX_MENSAJE) -> list[str]:
    partes, actual = [], ''
    for linea in texto.split('\n'):
        while len(linea) > tope:
            if actual:
                partes.append(actual)
                actual = ''
            partes.append(linea[:tope])
            linea = linea[tope:]
        candidato = f'{actual}\n{linea}' if actual else linea
        if len(candidato) > tope:
            partes.append(actual)
            actual = linea
        else:
            actual = candidato
    if actual:
        partes.append(actual)
    return partes or ['']


async def responder(update: Update, texto: str, reply_markup=None) -> None:
    mensaje = update.effective_message
    if mensaje is None:
        return
    partes = partir(texto)
    for i, parte in enumerate(partes):
        await mensaje.reply_text(parte, reply_markup=reply_markup if i == len(partes) - 1 else None)


async def mostrar(update: Update, texto: str, reply_markup=None, *, editar: bool = False) -> None:
    """Edita el mensaje del botón pulsado (menú más limpio) o responde uno nuevo."""
    consulta = update.callback_query
    if editar and consulta is not None and len(texto) <= MAX_MENSAJE:
        await consulta.edit_message_text(texto, reply_markup=reply_markup)
        return
    await responder(update, texto, reply_markup)
