"""Estado en memoria por persona.

Solo vive aquí lo que es aceptable perder en un reinicio: cambios sin
confirmar. Un solo proceso lee Telegram, así que no hace falta compartirlo.
"""

import asyncio
from dataclasses import dataclass, field

from bot.borrador import CambioPendiente


@dataclass
class EstadoUsuario:
    pendiente: CambioPendiente | None = None
    candado: asyncio.Lock = field(default_factory=asyncio.Lock)

    def pendiente_vigente(self, minutos: int) -> CambioPendiente | None:
        if self.pendiente is not None and self.pendiente.caducado(minutos):
            self.pendiente = None
        return self.pendiente


class Estados:
    def __init__(self) -> None:
        self._por_usuario: dict[int, EstadoUsuario] = {}

    def de(self, telegram_id: int) -> EstadoUsuario:
        estado = self._por_usuario.get(telegram_id)
        if estado is None:
            estado = self._por_usuario[telegram_id] = EstadoUsuario()
        return estado

    def olvidar(self, telegram_id: int) -> None:
        estado = self._por_usuario.get(telegram_id)
        if estado is not None:
            estado.pendiente = None
