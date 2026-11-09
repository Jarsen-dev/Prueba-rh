"""Cambios pendientes de confirmar.

Nada se escribe sin que la persona pulse Confirmar. El resumen que ve lo arma
el código a partir de lo que se va a ejecutar, así que lo que se aprueba es
exactamente lo que se manda. Un DELETE pide dos confirmaciones.
"""

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

METODOS_ESCRITURA = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


def nuevo_id() -> str:
    return secrets.token_hex(4)


@dataclass(frozen=True)
class Archivo:
    """Adjunto de un formulario multipart: (campo, nombre, contenido, mime)."""
    campo: str
    nombre: str
    contenido: bytes
    mime: str


@dataclass
class CambioPendiente:
    metodo: str
    ruta: str
    titulo: str
    detalle: list[str] = field(default_factory=list)
    #: Cuerpo JSON, o None si es un formulario.
    cuerpo: Any = None
    #: Campos de texto de un formulario multipart (excluye ``cuerpo``).
    formulario: dict[str, str] | None = None
    archivo: Archivo | None = None
    origen: str = 'menu'
    id: str = field(default_factory=nuevo_id)
    creado: float = field(default_factory=time.monotonic)
    confirmaciones_faltantes: int = 1

    def __post_init__(self) -> None:
        self.metodo = self.metodo.upper()
        if self.metodo not in METODOS_ESCRITURA:
            raise ValueError(f'{self.metodo} no es un método de escritura.')
        if not self.ruta.startswith('/api/') or '..' in self.ruta or '//' in self.ruta:
            raise ValueError('Ruta inválida.')
        if self.cuerpo is not None and (self.formulario is not None or self.archivo is not None):
            raise ValueError('Un cambio es JSON o formulario, no ambos.')
        if self.metodo == 'DELETE':
            self.confirmaciones_faltantes = 2

    def argumentos_peticion(self) -> dict[str, Any]:
        """Lo que se manda tal cual al confirmar (``ClienteBackend.peticion``)."""
        if self.formulario is not None or self.archivo is not None:
            argumentos: dict[str, Any] = {'data': dict(self.formulario or {})}
            if self.archivo is not None:
                a = self.archivo
                argumentos['files'] = [(a.campo, (a.nombre, a.contenido, a.mime))]
            return argumentos
        return {'json': self.cuerpo}

    def resumen(self) -> str:
        lineas = [self.titulo, *(f'• {linea}' for linea in self.detalle)]
        if self.archivo is not None:
            lineas.append(f'• Archivo adjunto: {self.archivo.nombre}')
        if self.metodo == 'DELETE':
            lineas.append('')
            lineas.append('⚠️ Esta acción es permanente.')
        return '\n'.join(lineas)

    def caducado(self, minutos: int, ahora: float | None = None) -> bool:
        return ((ahora or time.monotonic()) - self.creado) > minutos * 60
