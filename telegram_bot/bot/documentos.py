"""Leer documentos (OCR) para actualizar los datos personales de un empleado.

La lectura la hace el backend (``/api/escanear-documento``, el mismo que usa el
registro público) y el resultado lo procesa **solo el código**. El modelo de
chat nunca ve lo extraído: a lo más sabe que se preparó un cambio.

El resumen muestra el valor nuevo completo, para poder comprobar que la lectura
salió bien, y el anterior enmascarado. Al confirmar o cancelar, el mensaje se
reemplaza por una versión enmascarada para que los datos no se queden en el chat.
Después de actualizar los datos se ofrece guardar el archivo en el expediente.
"""

from dataclasses import dataclass
from typing import Any

from bot.borrador import Archivo, CambioPendiente
from bot.guardas import normalizar
from bot.seguridad import enmascarar_valor


@dataclass(frozen=True)
class TipoDocumento:
    clave: str
    nombre: str          # como lo espera /api/escanear-documento
    doc_key: str         # casilla del expediente (/api/employee-files)


TIPOS = {
    t.clave: t for t in (
        TipoDocumento('acta', 'Acta de Nacimiento', 'acta_nacimiento'),
        TipoDocumento('domicilio', 'Comprobante de Domicilio', 'comprobante_domicilio'),
        TipoDocumento('csf', 'Constancia de Situación Fiscal', 'rfc_doc'),
        TipoDocumento('nss', 'Número de Seguro Social', 'nss_doc'),
    )
}

#: Campo del escaneo -> (campo del empleado, etiqueta, caracteres visibles al enmascarar).
CAMPOS = {
    'nombre_completo': ('nombre_completo', 'Nombre', None),
    'fecha_nac': ('fecha_nacimiento', 'Fecha de nacimiento', 0),
    'genero': ('genero', 'Género', None),
    'curp': ('curp', 'CURP', 4),
    'rfc': ('rfc', 'RFC', 3),
    'nss': ('nss', 'NSS', 4),
    'calle': ('calle', 'Calle', 0),
    'num_ext': ('num_ext', 'Núm. exterior', 0),
    'num_int': ('num_int', 'Núm. interior', 0),
    'colonia': ('colonia', 'Colonia', 0),
    'municipio': ('municipio', 'Municipio', 0),
    'estado': ('estado', 'Estado', None),
    'cp': ('codigo_postal', 'C.P.', 0),
}
#: El backend recibe el domicilio partido en dos campos compuestos (como el panel).
NUMERACION = ('num_ext', 'num_int')
UBICACION = ('municipio', 'estado', 'codigo_postal')

VACIOS = {'', 'null', 'none', 'n/a', 'na', 'no aplica', 'no disponible'}
FORMATOS = ('.pdf', '.jpg', '.jpeg', '.png', '.webp')
MAX_BYTES = 9 * 1024 * 1024


def tipo_por_texto(texto: Any) -> TipoDocumento:
    """«csf», «constancia de situación fiscal», «Número de Seguro Social»… -> TipoDocumento."""
    buscado = normalizar(texto)
    for tipo in TIPOS.values():
        if buscado in (tipo.clave, normalizar(tipo.nombre)):
            return tipo
    raise ValueError(f'Tipo de documento «{texto}» no válido. Opciones: '
                     + ', '.join(t.nombre for t in TIPOS.values()) + '.')


def validar(archivo: Archivo) -> None:
    if not archivo.nombre.lower().endswith(FORMATOS):
        raise ValueError('Para leer datos, el documento debe ser foto (JPG, PNG, WEBP) o PDF.')
    if len(archivo.contenido) > MAX_BYTES:
        raise ValueError('Para leer datos, el documento no puede pasar de 9 MB.')


async def escanear(backend, telegram_id: int, tipo: TipoDocumento, archivo: Archivo) -> dict[str, str]:
    """Pide la lectura al backend. Devuelve solo los campos con valor."""
    validar(archivo)
    respuesta = await backend.peticion(
        telegram_id, 'POST', '/api/escanear-documento', timeout=90,
        data={'tipo_documento': tipo.nombre},
        files=[('archivo', (archivo.nombre, archivo.contenido, archivo.mime))],
    )
    datos = respuesta.datos if isinstance(respuesta.datos, dict) else {}
    campos = datos.get('campos', datos)
    # A veces la lectura devuelve el texto «null» en vez de un valor vacío.
    return {k: str(v).strip() for k, v in (campos or {}).items()
            if k in CAMPOS and v is not None and str(v).strip().lower() not in VACIOS}


def _mismo(a: Any, b: Any) -> bool:
    return normalizar(a) == normalizar(b)


def _oculto(valor: Any, visibles: int | None) -> str:
    if valor in (None, ''):
        return '(vacío)'
    return str(valor) if visibles is None else str(enmascarar_valor(valor, visibles))


def pendiente_de_documento(empleado: dict[str, Any], tipo: TipoDocumento, leidos: dict[str, str],
                           archivo: Archivo, origen: str = 'menu') -> CambioPendiente:
    """El cambio de datos (solo lo que difiere) y, encadenado, guardar el archivo en el expediente."""
    registro_id = int(empleado['registro_id'])
    nombre = empleado.get('nombre_completo') or 'Sin nombre'
    guardar = CambioPendiente(
        'POST', '/api/employee-files', f'📎 Guardar el documento en el expediente ({tipo.nombre}):',
        detalle=[f'Empleado: {nombre}'],
        formulario={'registro_id': str(registro_id), 'doc_key': tipo.doc_key},
        archivo=Archivo('file', archivo.nombre, archivo.contenido, archivo.mime), origen=origen,
    )
    if not leidos:
        raise ValueError(f'No pude leer datos del documento. Revisa que sea un(a) {tipo.nombre} legible '
                         '(foto completa, con luz y sin reflejos) y vuelve a intentarlo.')

    nuevos: dict[str, str] = {}
    detalle, final = [f'Empleado: {nombre}'], [f'Empleado: {nombre}']
    for campo_leido, valor in leidos.items():
        campo, etiqueta, visibles = CAMPOS[campo_leido]
        antes = empleado.get(campo)
        if _mismo(antes, valor):
            continue
        nuevos[campo] = valor
        detalle.append(f'{etiqueta}: {_oculto(antes, visibles)} → {valor}')
        final.append(f'{etiqueta}: {_oculto(valor, visibles)}')
    if not nuevos:
        guardar.detalle.append('Los datos del documento ya coinciden con el expediente; no hay nada que cambiar.')
        return guardar

    cuerpo = {k: v for k, v in nuevos.items() if k not in NUMERACION + UBICACION}
    if set(nuevos) & set(NUMERACION):
        # «ext/int»: lo que no trae el documento se conserva.
        ext, interior = (nuevos.get(c) or empleado.get(c) or '' for c in NUMERACION)
        cuerpo['numeracion'] = f'{ext}/{interior}' if interior else ext
    if set(nuevos) & set(UBICACION):
        cuerpo['ubicacion'] = '/'.join(str(nuevos.get(c) or empleado.get(c) or '') for c in UBICACION)

    detalle.append('Revisa que la lectura sea correcta antes de confirmar.')
    return CambioPendiente(
        'POST', f'/api/actualizar-empleado/{registro_id}', f'🪪 Actualizar datos con {tipo.nombre}:',
        detalle=detalle, detalle_final=final, cuerpo=cuerpo, origen=origen, siguiente=guardar,
    )
