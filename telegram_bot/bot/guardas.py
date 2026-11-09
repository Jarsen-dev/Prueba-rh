"""Guardas de escritura: lo que el modelo no puede decidir solo.

Un supuesto en una consulta solo cuesta una respuesta imprecisa. En un cambio
termina guardado con el nombre de quien confirmó. Por eso, antes de armar un
cambio, el código comprueba lo siguiente sin importar lo que diga el modelo:

- **Ids vistos:** todo registro_id o candidato salió de una consulta (``Vistos``),
  nunca de la imaginación del modelo.
- **Lo que la persona dijo:** salario, teléfono y fechas aparecen en lo que
  ella escribió. Las fechas relativas («el lunes», «la próxima semana») no se
  aceptan.
- **Catálogos:** empresa, área, puesto, turno y pago son valores válidos del catálogo.

Todo es puro y está probado.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
}
_MES = '|'.join(MESES)


class GuardaFallo(ValueError):
    """El cambio no se preparó; el mensaje le dice al modelo qué preguntar."""


def normalizar(texto: Any) -> str:
    """Minúsculas, sin acentos, espacios simples y sin separador de miles."""
    texto = unicodedata.normalize('NFD', str(texto or '').lower())
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    texto = re.sub(r'(?<=\d)[,.](?=\d{3}\b)', '', texto)
    return ' '.join(texto.split())


@dataclass
class Vistos:
    """Registros que salieron de la API para esta persona. Viven en su estado de 30 min."""
    empleados: dict[int, dict[str, Any]] = field(default_factory=dict)
    candidatos: dict[int, dict[str, Any]] = field(default_factory=dict)
    #: (registro_id, incidencia_id) -> incidencia
    incidencias: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    #: Tareas programadas de la persona (id -> tarea).
    tareas: dict[int, dict[str, Any]] = field(default_factory=dict)

    def empleado(self, registro_id: Any) -> dict[str, Any]:
        try:
            clave = int(registro_id)
        except (TypeError, ValueError):
            clave = None
        if clave not in self.empleados:
            raise GuardaFallo('Ese registro_id no salió de ninguna consulta. Busca primero al empleado con '
                              'buscar_empleado y usa el registro_id que devuelva.')
        return self.empleados[clave]

    def candidato(self, candidato_id: Any) -> dict[str, Any]:
        try:
            clave = int(candidato_id)
        except (TypeError, ValueError):
            clave = None
        if clave not in self.candidatos:
            raise GuardaFallo('Ese candidato no salió de ninguna consulta. Consulta primero ver_pendientes '
                              'y usa el id que devuelva.')
        return self.candidatos[clave]

    def registrar_empleado(self, empleado: dict[str, Any]) -> None:
        try:
            clave = int(empleado.get('registro_id'))
        except (TypeError, ValueError):
            return
        previo = self.empleados.get(clave, {})
        self.empleados[clave] = {**previo, **{k: v for k, v in empleado.items() if v not in (None, '')},
                                 'registro_id': clave}


def numero_mencionado(valor: Any, textos: list[str]) -> str:
    """El número (salario, teléfono) tiene que estar escrito por la persona."""
    digitos = re.sub(r'\D', '', str(valor or ''))
    if not digitos:
        raise GuardaFallo('Falta el número. Pregúntaselo a la persona.')
    escritos = set()
    for texto in textos:
        limpio = normalizar(texto)
        escritos |= {re.sub(r'\D', '', m) for m in re.findall(r'\d[\d\s,.\-]*\d|\d', limpio)}
        escritos |= set(re.findall(r'\d+', limpio))
    if digitos not in escritos:
        # Si copió mal un número que sí escribió la persona (p. ej. un dígito repetido de más), que lo corrija
        # solo: esos números salen del texto de la persona, que el modelo ya tiene.
        parecidos = sorted(e for e in escritos if abs(len(e) - len(digitos)) <= 2 and len(e) >= 3)
        pista = (f' Los números que sí escribió son: {", ".join(parecidos)}. Si es uno de ellos, vuelve a intentarlo '
                 'copiándolo exacto.') if parecidos else ''
        raise GuardaFallo(f'«{valor}» no aparece en lo que escribió la persona.{pista} Si no, pregúntale el dato '
                          'exacto; no lo supongas.')
    return digitos


def texto_mencionado(valor: Any, textos: list[str], que: str) -> str:
    limpio = normalizar(valor)
    if not limpio or limpio not in normalizar(' '.join(textos)):
        raise GuardaFallo(f'{que} «{valor}» no aparece en lo que escribió la persona. Pregúntaselo.')
    return str(valor).strip()


def fecha_mencionada(fecha: date, textos: list[str], hoy: date) -> date:
    """La fecha sale de lo que escribió la persona: «hoy», «mañana», 16/11, «16 de noviembre», «del 16 al 20 de noviembre»."""
    texto = normalizar(' '.join(textos))
    palabras = set(re.findall(r'[a-z]+', texto))
    if (fecha == hoy and 'hoy' in palabras) or (fecha == hoy + timedelta(days=1) and 'manana' in palabras) \
            or (fecha == hoy - timedelta(days=1) and 'ayer' in palabras):
        return fecha
    if fecha.isoformat() in texto:
        return fecha
    anios = {int(a) for a in re.findall(r'\b(20\d{2})\b', texto)}
    if anios and fecha.year not in anios:
        raise GuardaFallo(f'El año de {fecha:%d/%m/%Y} no coincide con lo que escribió la persona.')
    if not anios and fecha.year not in (hoy.year, hoy.year + 1):
        raise GuardaFallo(f'La persona no dijo el año de {fecha:%d/%m/%Y}; pregúntaselo.')
    for dia, mes, anio in re.findall(r'\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b', texto):
        if (int(dia), int(mes)) == (fecha.day, fecha.month) and (not anio or int(anio) % 100 == fecha.year % 100):
            return fecha
    meses_dichos = {MESES[m] for m in re.findall(rf'\b({_MES})\b', texto)}
    dias_dichos = {int(d) for d in re.findall(r'(?<![\d/\-])(\d{1,2})(?![\d/\-])', texto)}
    mes_valido = fecha.month in meses_dichos or (not meses_dichos and fecha.month == hoy.month)
    if mes_valido and fecha.day in dias_dichos:
        return fecha
    raise GuardaFallo(f'La fecha {fecha:%d/%m/%Y} no aparece en lo que escribió la persona. Pregúntale la fecha '
                      'exacta (día y mes); no calcules fechas relativas como «el lunes».')


def leer_fecha(valor: Any) -> date:
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError as exc:
        raise GuardaFallo(f'«{valor}» no es una fecha AAAA-MM-DD.') from exc


def de_catalogo(valor: Any, opciones: list[str], que: str) -> str:
    """El valor canónico del catálogo (sin importar mayúsculas ni acentos)."""
    buscado = normalizar(valor)
    for opcion in opciones:
        if normalizar(opcion) == buscado:
            return opcion
    raise GuardaFallo(f'{que} «{valor}» no es válido. Opciones: {", ".join(opciones)}.')
