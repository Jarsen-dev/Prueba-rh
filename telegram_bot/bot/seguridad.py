"""Qué puede pedir el agente y qué le llega al modelo.

Todo es puro y está probado. Los permisos los aplica el backend en cada
petición. Estas guardas existen porque el modelo es un tercero: escribe rutas
a partir de texto libre, y lo que responde el sistema viaja a OpenAI.

Hay dos niveles:
- ``CAMPOS_OCULTOS`` se borran siempre: contraseñas, firmas, fotos y binarios.
- Los datos personales (CURP, RFC, NSS, teléfonos, correo, domicilio, contacto
  de emergencia) se enmascaran por campo y, además, por patrón en cualquier
  texto, porque pueden venir escritos en un motivo o una descripción.
"""

import json
import re
from typing import Any, Final

CAMPOS_OCULTOS: Final[frozenset[str]] = frozenset({
    'password', 'password_hash', 'contrasena', 'contrasena_chatbot', 'token',
    'firma', 'firma_archivo', 'fotografia', 'fotografia_base64', 'foto',
    'documentos_papeleria', 'archivo_base64', 'contenido',
})

#: Campo -> cuántos caracteres finales se dejan ver (0 = nada).
CAMPOS_ENMASCARADOS: Final[dict[str, int]] = {
    'curp': 4, 'rfc': 3, 'nss': 4,
    'tel_movil': 4, 'telefono': 4, 'telefono_emergencia': 4, 'telefono_notificacion': 4, 'celular': 4,
    'correo': 0, 'email': 0,
    'calle': 0, 'num_ext': 0, 'num_int': 0, 'colonia': 0, 'codigo_postal': 0, 'domicilio': 0, 'direccion': 0,
    'nombre_emergencia': 0, 'parentesco_emergencia': 0,
}

MAX_CARACTERES_RESULTADO: Final[int] = 12_000
#: Un texto sin espacios más largo que esto es un binario o base64.
MAX_TOKEN_CONTINUO: Final[int] = 200

_PATRONES_PERSONALES: Final[list[tuple[re.Pattern[str], str]]] = [
    (re.compile(r'\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b', re.I), '[CURP oculta]'),
    (re.compile(r'\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b', re.I), '[RFC oculto]'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '[correo oculto]'),
    # Teléfonos (10 dígitos, con o sin +52 y separadores) y NSS (11 dígitos).
    (re.compile(r'(?<![\w])(?:\+?52[\s-]?)?(?:\d[\s-]?){9}\d(?:[\s-]?\d)?(?![\w])'), '[número oculto]'),
]
_DATA_URL = re.compile(r'data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+')
_SIN_ESPACIOS_LARGO = re.compile(r'\S{%d,}' % (MAX_TOKEN_CONTINUO + 1))
_RUTA_VALIDA = re.compile(r'^/api(/[A-Za-z0-9_\-.:]+)+$')
_ETIQUETA_DATOS = re.compile(r'</?\s*datos_no_confiables\s*>', re.I)


class RutaNoPermitida(ValueError):
    """La ruta que pidió el modelo no es una que el agente pueda usar."""


# --- Rutas ---------------------------------------------------------------------

def normalizar_ruta(ruta: str) -> str:
    """Acepta ``empleados/buscar`` o ``/api/empleados/buscar`` y devuelve la segunda.

    Rechaza query incrustada, ``..``, dobles barras o caracteres raros: los
    parámetros van aparte, nunca en la ruta.
    """
    limpia = str(ruta or '').strip()
    if not limpia.startswith('/'):
        limpia = '/' + limpia
    if not limpia.startswith('/api/'):
        limpia = '/api' + limpia
    limpia = limpia.rstrip('/')
    if '..' in limpia or '//' in limpia or not _RUTA_VALIDA.match(limpia):
        raise RutaNoPermitida(f'La ruta «{ruta}» no es válida.')
    return limpia


def _plantilla_a_regex(plantilla: str) -> re.Pattern[str]:
    partes = re.split(r'(\{[^}]+\})', plantilla)
    return re.compile('^' + ''.join(r'[^/]+' if p.startswith('{') else re.escape(p) for p in partes) + '$')


def buscar_endpoint(guia: dict[str, Any], metodo: str, ruta: str) -> dict[str, Any]:
    """El endpoint de la guía que corresponde a la petición; las rutas fijas ganan a las paramétricas."""
    metodo = metodo.upper()
    candidatos = sorted(
        (e for e in guia.get('endpoints', []) if e.get('metodo') == metodo),
        key=lambda e: e['ruta'].count('{'),
    )
    for endpoint in candidatos:
        if _plantilla_a_regex(endpoint['ruta']).match(ruta):
            return endpoint
    raise RutaNoPermitida(
        f'{metodo} {ruta} no es una operación disponible. Usa buscar_endpoints para ver las rutas que existen.'
    )


def puede_usar(endpoint: dict[str, Any], permisos: list[str] | set[str]) -> bool:
    return bool(set(endpoint.get('permisos') or []) & set(permisos))


def buscar_en_guia(guia: dict[str, Any], texto: str, permisos: list[str] | set[str], *,
                   solo_lectura: bool = True) -> list[dict[str, Any]]:
    """Endpoints que la persona puede usar cuya ruta o resumen contienen las palabras buscadas."""
    palabras = [p for p in re.split(r'\W+', (texto or '').lower()) if len(p) > 2]
    resultados = []
    for endpoint in guia.get('endpoints', []):
        if not puede_usar(endpoint, permisos) or (solo_lectura and endpoint.get('escritura')):
            continue
        pajar = f"{endpoint['ruta']} {endpoint.get('resumen', '')}".lower()
        puntos = sum(1 for palabra in palabras if palabra in pajar)
        if puntos or not palabras:
            resultados.append((puntos, endpoint))
    resultados.sort(key=lambda par: -par[0])
    return [e for _, e in resultados[:15]]


# --- Datos hacia el modelo -----------------------------------------------------------

def enmascarar_valor(valor: Any, visibles: int) -> Any:
    if valor in (None, ''):
        return valor
    texto = str(valor).strip()
    if visibles and len(texto) > visibles + 2:
        return f'****{texto[-visibles:]}'
    return '[oculto]'


def enmascarar_texto(texto: str) -> str:
    texto = _DATA_URL.sub('[binario omitido]', texto)
    texto = _SIN_ESPACIOS_LARGO.sub('[binario omitido]', texto)
    for patron, reemplazo in _PATRONES_PERSONALES:
        texto = patron.sub(reemplazo, texto)
    return texto


def limpiar_para_modelo(valor: Any, *, enmascarar: bool = True) -> Any:
    """Copia sin campos ocultos y, si ``enmascarar``, con los datos personales tapados."""
    if isinstance(valor, dict):
        limpio = {}
        for clave, contenido in valor.items():
            nombre = str(clave).lower()
            if nombre in CAMPOS_OCULTOS:
                continue
            if enmascarar and nombre in CAMPOS_ENMASCARADOS:
                limpio[clave] = enmascarar_valor(contenido, CAMPOS_ENMASCARADOS[nombre])
            else:
                limpio[clave] = limpiar_para_modelo(contenido, enmascarar=enmascarar)
        return limpio
    if isinstance(valor, list):
        return [limpiar_para_modelo(v, enmascarar=enmascarar) for v in valor]
    if isinstance(valor, str):
        if not enmascarar:
            return _SIN_ESPACIOS_LARGO.sub('[binario omitido]', _DATA_URL.sub('[binario omitido]', valor))
        return enmascarar_texto(valor)
    return valor


def recortar(texto: str, maximo: int = MAX_CARACTERES_RESULTADO) -> str:
    """Corta lo que no cabe y lo dice, para que el modelo no invente el resto."""
    if len(texto) <= maximo:
        return texto
    return texto[:maximo] + (
        f'\n…[recortado: faltan {len(texto) - maximo} caracteres. Pide menos datos con filtros.]'
    )


def para_modelo(valor: Any, *, enmascarar: bool = True) -> str:
    """Resultado de una herramienta listo para el modelo: limpio, recortado y marcado como datos."""
    texto = valor if isinstance(valor, str) else json.dumps(
        limpiar_para_modelo(valor, enmascarar=enmascarar), ensure_ascii=False, default=str
    )
    if isinstance(valor, str):
        texto = enmascarar_texto(texto) if enmascarar else texto
    texto = _ETIQUETA_DATOS.sub('', recortar(texto))
    return f'<datos_no_confiables>\n{texto}\n</datos_no_confiables>'
