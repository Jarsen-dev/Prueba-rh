"""Formularios paso a paso.

Cada paso pide un campo: con botones (opciones), texto libre, una fecha o un
archivo. Al terminar, ``al_terminar`` arma el ``CambioPendiente`` que verá la
persona con el botón Confirmar. Este módulo no habla con Telegram ni con la API.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from bot.borrador import CambioPendiente

Respuestas = dict[str, Any]


@dataclass
class Paso:
    campo: str
    pregunta: str
    #: 'opciones' | 'texto' | 'fecha' | 'archivo'
    tipo: str = 'opciones'
    opciones: Callable[[Respuestas], list[str]] | None = None
    #: Devuelve el valor normalizado o lanza ValueError con el mensaje para la persona.
    validar: Callable[[Any, Respuestas], Any] | None = None
    omitible: bool = False
    aplica: Callable[[Respuestas], bool] = lambda _r: True


@dataclass
class Asistente:
    titulo: str
    pasos: list[Paso]
    al_terminar: Callable[[Respuestas], CambioPendiente]
    hoy: Callable[[], date] = date.today
    respuestas: Respuestas = field(default_factory=dict)
    #: Índices de los pasos ya contestados, para «Atrás».
    historial: list[int] = field(default_factory=list)
    indice: int = 0

    def __post_init__(self) -> None:
        self._saltar_no_aplicables()

    def _saltar_no_aplicables(self) -> None:
        while self.indice < len(self.pasos) and not self.pasos[self.indice].aplica(self.respuestas):
            self.indice += 1

    @property
    def terminado(self) -> bool:
        return self.indice >= len(self.pasos)

    @property
    def paso(self) -> Paso | None:
        return None if self.terminado else self.pasos[self.indice]

    def numero_de_paso(self) -> tuple[int, int]:
        aplicables = [i for i, p in enumerate(self.pasos) if p.aplica(self.respuestas)]
        actual = aplicables.index(self.indice) + 1 if self.indice in aplicables else len(aplicables)
        return actual, len(aplicables)

    def opciones(self) -> list[str]:
        paso = self.paso
        if paso is None:
            return []
        if paso.tipo == 'fecha':
            return ['Hoy', 'Mañana']
        return list(paso.opciones(self.respuestas)) if paso.opciones else []

    def elegir(self, indice: int) -> None:
        """Respuesta con botón: el índice dentro de ``opciones()``."""
        opciones = self.opciones()
        if not 0 <= indice < len(opciones):
            raise ValueError('Esa opción ya no está disponible.')
        self.responder(opciones[indice])

    def responder(self, valor: Any) -> None:
        paso = self.paso
        if paso is None:
            raise ValueError('El formulario ya terminó.')
        if paso.tipo == 'opciones' and valor not in self.opciones():
            raise ValueError('Elige una de las opciones con los botones.')
        if paso.tipo == 'fecha':
            valor = interpretar_fecha(valor, self.hoy())
        if paso.validar is not None:
            valor = paso.validar(valor, self.respuestas)
        self.respuestas[paso.campo] = valor
        self._avanzar()

    def omitir(self) -> None:
        paso = self.paso
        if paso is None or not paso.omitible:
            raise ValueError('Este dato es obligatorio.')
        self.respuestas[paso.campo] = None
        self._avanzar()

    def atras(self) -> None:
        if not self.historial:
            return
        self.indice = self.historial.pop()
        # Lo que se contestó después ya no vale (p. ej. el puesto depende del área).
        for paso in self.pasos[self.indice:]:
            self.respuestas.pop(paso.campo, None)

    def _avanzar(self) -> None:
        self.historial.append(self.indice)
        self.indice += 1
        self._saltar_no_aplicables()

    def construir(self) -> CambioPendiente:
        if not self.terminado:
            raise ValueError('Faltan datos.')
        return self.al_terminar(self.respuestas)


_FORMATOS_FECHA = (
    (re.compile(r'^(\d{4})-(\d{1,2})-(\d{1,2})$'), ('a', 'm', 'd')),
    (re.compile(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$'), ('d', 'm', 'a')),
)


def interpretar_fecha(texto: Any, hoy: date) -> date:
    """«hoy», «mañana», AAAA-MM-DD o DD/MM/AAAA. Nunca adivina fechas relativas."""
    if isinstance(texto, date):
        return texto
    limpio = str(texto or '').strip().lower()
    if limpio == 'hoy':
        return hoy
    if limpio in {'mañana', 'manana'}:
        return hoy + timedelta(days=1)
    for patron, orden in _FORMATOS_FECHA:
        coincidencia = patron.match(limpio)
        if coincidencia:
            partes = dict(zip(orden, (int(g) for g in coincidencia.groups())))
            try:
                return date(partes['a'], partes['m'], partes['d'])
            except ValueError:
                break
    raise ValueError('No entendí la fecha. Escríbela como DD/MM/AAAA (por ejemplo 15/11/2026) o usa los botones.')


def formatear_fecha(valor: date) -> str:
    return valor.strftime('%d/%m/%Y')
