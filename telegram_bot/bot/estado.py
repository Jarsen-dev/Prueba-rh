"""Estado en memoria por persona.

Solo vive aquí lo que es aceptable perder en un reinicio: cambios sin
confirmar, el formulario a medio llenar y la última lista mostrada. Un solo
proceso lee Telegram, así que no hace falta compartirlo.

Los botones del menú mandan índices a ``lista`` y no ids ni rutas: un
``callback_data`` manipulado no puede apuntar a un registro arbitrario.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from bot.borrador import CambioPendiente

#: Minutos sin actividad tras los que se descarta el estado del menú.
CADUCIDAD_MENU = 30


@dataclass
class EstadoUsuario:
    pendiente: CambioPendiente | None = None
    #: Formulario paso a paso en curso (bot.menu.asistente.Asistente).
    asistente: Any = None
    #: Qué texto libre se espera: 'buscar' o None.
    esperando: str | None = None
    #: Última lista mostrada (empleados o candidatos) y su tipo.
    lista: list[dict[str, Any]] = field(default_factory=list)
    tipo_lista: str | None = None
    #: Empleado abierto en la ficha.
    seleccionado: dict[str, Any] | None = None
    ultimo_uso: float = field(default_factory=time.monotonic)
    candado: asyncio.Lock = field(default_factory=asyncio.Lock)

    def pendiente_vigente(self, minutos: int) -> CambioPendiente | None:
        if self.pendiente is not None and self.pendiente.caducado(minutos):
            self.pendiente = None
        return self.pendiente

    def tocar(self, ahora: float | None = None) -> None:
        """Marca actividad; si pasó la caducidad, olvida el menú a medias."""
        ahora = ahora or time.monotonic()
        if ahora - self.ultimo_uso > CADUCIDAD_MENU * 60:
            self.limpiar_menu()
        self.ultimo_uso = ahora

    def limpiar_menu(self) -> None:
        self.asistente = None
        self.esperando = None
        self.lista = []
        self.tipo_lista = None
        self.seleccionado = None


class Estados:
    def __init__(self) -> None:
        self._por_usuario: dict[int, EstadoUsuario] = {}

    def de(self, telegram_id: int) -> EstadoUsuario:
        estado = self._por_usuario.get(telegram_id)
        if estado is None:
            estado = self._por_usuario[telegram_id] = EstadoUsuario()
        estado.tocar()
        return estado

    def olvidar(self, telegram_id: int) -> None:
        estado = self._por_usuario.get(telegram_id)
        if estado is not None:
            estado.pendiente = None
            estado.limpiar_menu()
