"""Herramientas de lectura del agente (las de escritura están en ``escrituras.py``).

Cada herramienta llama a la API con la sesión de la persona: el backend aplica
sus permisos. Lo que vuelve al modelo pasa por ``seguridad.para_modelo``
(campos ocultos fuera, datos personales enmascarados, envuelto como datos no
confiables). Los archivos van a la persona y el modelo solo sabe que se mandaron.

Las curadas (contar_empleados, resumen_reclutamiento…) hacen las cuentas en
código: un modelo chico cuenta mal una lista de 300 empleados.
"""

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import quote

from bot import borrador
from bot.backend import ClienteBackend, ErrorBackend
from bot.guardas import GuardaFallo, Vistos
from bot.seguridad import (
    RutaNoPermitida,
    buscar_en_guia,
    buscar_endpoint,
    normalizar_ruta,
    para_modelo,
    puede_usar,
)

EMPRESAS = {'CHEONG_WOON': 'CHEONG WOON', 'KRONOS': 'KRONOS'}
FUENTES = ['FACEBOOK', 'POSTEO', 'VOLANTE', 'RECOMENDACION', 'RECLU CW', 'RECLU PURO']
MAX_PARAMETROS = 8
MAX_VALOR_PARAMETRO = 100
MUESTRA = 15


class SinPermiso(Exception):
    """La consulta no está en la guía o la cuenta no tiene permiso; no se manda."""


@dataclass
class Archivo:
    nombre: str
    contenido: bytes


@dataclass
class Contexto:
    """Lo que una herramienta necesita para un turno de una persona."""
    backend: ClienteBackend
    telegram_id: int
    permisos: list[str]
    hoy: date
    enmascarar: bool = True
    #: Archivos que se le mandarán a la persona al terminar el turno.
    archivos: list[Archivo] = field(default_factory=list)
    #: Cada petición a la API que hizo el agente: (método, ruta, params). Para trazas y el banco de evaluación.
    peticiones: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    #: Lo que escribió la persona (este mensaje y los anteriores): las guardas buscan ahí los datos.
    textos: list[str] = field(default_factory=list)
    #: Registros que salieron de la API; los cambios solo pueden apuntar a estos.
    vistos: Vistos = field(default_factory=Vistos)
    #: Archivo que mandó la persona por Telegram (el modelo solo sabe su nombre).
    adjunto: borrador.Archivo | None = None
    #: El cambio que preparó el agente en este turno (uno como máximo).
    pendiente: borrador.CambioPendiente | None = None

    async def get(self, ruta: str, params: dict[str, Any] | None = None):
        # Toda consulta del agente (curada o no) pasa por la guía y los permisos
        # antes de salir: lo que la cuenta no puede ver ni se pide.
        try:
            endpoint = buscar_endpoint(await self.backend.guia(), 'GET', ruta)
        except RutaNoPermitida as error:
            raise SinPermiso(str(error)) from error
        if not puede_usar(endpoint, self.permisos):
            raise SinPermiso('La cuenta de la persona no tiene permiso para esta consulta.')
        self.peticiones.append(('GET', ruta, dict(params or {})))
        respuesta = await self.backend.peticion(self.telegram_id, 'GET', ruta, params=params, timeout=90)
        registrar_vistos(self.vistos, ruta, respuesta.datos)
        return respuesta


#: Solo esto de un empleado se guarda en memoria (nada de datos personales).
CAMPOS_EMPLEADO = ('registro_id', 'nombre_completo', 'no_empleado', 'empresa', 'puesto', 'area', 'turno',
                   'salario', 'pago', 'estatus')


def registrar_vistos(vistos: Vistos, ruta: str, datos: Any) -> None:
    """Anota los registros que devolvió la API, para que un cambio solo apunte a algo real."""
    def empleado(e):
        if isinstance(e, dict):
            vistos.registrar_empleado({k: e.get(k) for k in CAMPOS_EMPLEADO})

    if ruta == '/api/empleados/buscar' and isinstance(datos, dict):
        for e in datos.get('empleados') or []:
            empleado(e)
    elif re.fullmatch(r'/api/obtener-empleado/\d+', ruta):
        empleado(datos)
    elif re.fullmatch(r'/api/obtener-empleados/[A-Z_]+|/api/obtener-ingresos', ruta) and isinstance(datos, list):
        for e in datos:
            empleado(e)
    elif ruta == '/api/obtener-pendientes' and isinstance(datos, list):
        for c in datos:
            if isinstance(c, dict) and isinstance(c.get('id'), int):
                vistos.candidatos[c['id']] = {k: c.get(k) for k in ('id', 'nombre_completo', 'numero_temporal')}
    elif (m := re.fullmatch(r'/api/empleados/(\d+)/incidencias', ruta)) and isinstance(datos, dict):
        for i in datos.get('incidencias') or []:
            if isinstance(i, dict) and isinstance(i.get('id'), int):
                vistos.incidencias[(int(m.group(1)), i['id'])] = {
                    k: i.get(k) for k in ('id', 'type', 'fecha_inicio', 'fecha_reingreso', 'dias')}


def _funcion(nombre: str, descripcion: str, propiedades: dict[str, Any], requeridos: list[str] | None = None):
    return {'type': 'function', 'function': {
        'name': nombre, 'description': descripcion,
        'parameters': {'type': 'object', 'properties': propiedades, 'required': requeridos or [],
                       'additionalProperties': False},
    }}


DEFINICIONES = [
    _funcion('buscar_empleado',
             'Busca empleados (personas que ya tienen puesto) por número de empleado exacto o por parte del nombre. '
             'Devuelve registro_id, nombre, número, empresa, puesto, área, turno y estatus. Los candidatos que aún '
             'no tienen puesto NO aparecen aquí: búscalos con ver_pendientes.',
             {'texto': {'type': 'string', 'description': 'Número de empleado o al menos 3 letras del nombre'}},
             ['texto']),
    _funcion('ver_empleado',
             'Expediente de un empleado por registro_id (sale de buscar_empleado): datos generales, puesto, '
             'salario y pago. Los datos personales llegan enmascarados.',
             {'registro_id': {'type': 'integer'}}, ['registro_id']),
    _funcion('contar_empleados',
             'Cuenta empleados de una o ambas empresas con filtros opcionales y devuelve totales por estatus, '
             'área, puesto y turno, más una muestra. Úsala para cualquier pregunta de «cuántos».',
             {
                 'empresa': {'type': 'string', 'enum': ['CHEONG_WOON', 'KRONOS', 'TODAS']},
                 'estatus': {'type': 'string', 'enum': ['ACTIVO', 'INACTIVO', 'TODOS'],
                             'description': 'ACTIVO por omisión'},
                 'area': {'type': 'string'}, 'puesto': {'type': 'string'}, 'turno': {'type': 'string'},
             }, ['empresa']),
    _funcion('resumen_reclutamiento',
             'Ingresos de un mes: total, por fuente de reclutamiento, por equipo y por reclutador, y totales '
             'por mes del año en curso.',
             {'mes': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'description': 'Mes actual por omisión'},
              'anio': {'type': 'integer', 'description': 'Año actual por omisión'}}),
    _funcion('ver_pendientes',
             'Candidatos registrados que aún no tienen puesto asignado (id, nombre, número temporal).', {}),
    _funcion('ver_incidencias',
             'Historial de incidencias de un empleado por registro_id, con el id de cada incidencia.',
             {'registro_id': {'type': 'integer'}}, ['registro_id']),
    _funcion('buscar_endpoints',
             'Busca en la guía de la API las consultas disponibles para tu cuenta (ruta, resumen y parámetros). '
             'Úsala antes de consultar_api cuando ninguna otra herramienta sirve.',
             {'consulta': {'type': 'string', 'description': 'Palabras clave, p. ej. «incidencias», «calendario»'}},
             ['consulta']),
    _funcion('consultar_api',
             'Hace un GET a una ruta de la guía, con los {parámetros} de la ruta ya sustituidos. Si la ruta '
             'devuelve un archivo (Excel), se le envía a la persona.',
             {'ruta': {'type': 'string', 'description': 'p. ej. /api/empleados/12/incidencias'},
              'parametros': {'type': 'object', 'description': 'Parámetros de consulta (query string)',
                             'additionalProperties': {'type': ['string', 'integer']}}},
             ['ruta']),
]
NOMBRES = {d['function']['name'] for d in DEFINICIONES}


# --- Implementaciones ---------------------------------------------------------------------

async def buscar_empleado(ctx: Contexto, texto: str = '') -> str:
    texto = ' '.join(str(texto or '').split())
    if texto.isdigit():
        params = {'no_empleado': texto}
    elif len(texto) >= 3:
        params = {'q': texto, 'limite': 15}
    else:
        return 'Escribe un número de empleado o al menos 3 letras del nombre.'
    respuesta = await ctx.get('/api/empleados/buscar', params)
    empleados = (respuesta.datos or {}).get('empleados') or []
    return para_modelo({'total': len(empleados), 'empleados': empleados}, enmascarar=ctx.enmascarar)


async def ver_empleado(ctx: Contexto, registro_id: Any = None) -> str:
    try:
        registro_id = int(registro_id)
    except (TypeError, ValueError):
        return 'registro_id debe ser un número (búscalo con buscar_empleado).'
    respuesta = await ctx.get(f'/api/obtener-empleado/{registro_id}')
    return para_modelo(respuesta.datos, enmascarar=ctx.enmascarar)


def _coincide(valor: Any, buscado: str | None) -> bool:
    return not buscado or str(buscado).strip().upper() in str(valor or '').upper()


async def contar_empleados(ctx: Contexto, empresa: str = 'TODAS', estatus: str = 'ACTIVO',
                           area: str | None = None, puesto: str | None = None, turno: str | None = None) -> str:
    empresa = str(empresa or 'TODAS').upper().replace(' ', '_')
    if empresa != 'TODAS' and empresa not in EMPRESAS:
        return 'empresa debe ser CHEONG_WOON, KRONOS o TODAS.'
    estatus = str(estatus or 'ACTIVO').upper()
    empleados = []
    for clave in (EMPRESAS if empresa == 'TODAS' else [empresa]):
        respuesta = await ctx.get(f'/api/obtener-empleados/{quote(clave)}')
        empleados += respuesta.datos if isinstance(respuesta.datos, list) else []
    filtrados = [
        e for e in empleados
        if (estatus == 'TODOS' or str(e.get('estatus') or 'ACTIVO').upper() == estatus)
        and _coincide(e.get('area'), area) and _coincide(e.get('puesto'), puesto) and _coincide(e.get('turno'), turno)
    ]

    def conteo(campo):
        return dict(Counter(str(e.get(campo) or 'Sin dato') for e in filtrados).most_common())

    return para_modelo({
        'filtros': {'empresa': empresa, 'estatus': estatus, 'area': area, 'puesto': puesto, 'turno': turno},
        'total': len(filtrados),
        'por_empresa': conteo('empresa'), 'por_estatus': conteo('estatus'), 'por_area': conteo('area'),
        'por_turno': conteo('turno'), 'por_puesto': conteo('puesto'),
        'muestra': [
            {k: e.get(k) for k in ('registro_id', 'nombre_completo', 'no_empleado', 'empresa', 'area', 'puesto',
                                   'turno', 'estatus', 'fecha_ingreso')}
            for e in filtrados[:MUESTRA]
        ],
        'nota': f'La muestra trae como máximo {MUESTRA}; los totales son exactos.',
    }, enmascarar=ctx.enmascarar)


async def resumen_reclutamiento(ctx: Contexto, mes: Any = None, anio: Any = None) -> str:
    try:
        mes = int(mes or ctx.hoy.month)
        anio = int(anio or ctx.hoy.year)
    except (TypeError, ValueError):
        return 'mes y anio deben ser números.'
    if not 1 <= mes <= 12:
        return 'mes debe estar entre 1 y 12.'
    resultado: dict[str, Any] = {'mes': mes, 'anio': anio}
    equipos = (await ctx.get('/api/ingresos-por-team', {'mes': mes, 'anio': anio})).datos or {}
    resultado['por_equipo'] = equipos.get('equipos') or []
    resultado['total_por_equipos'] = sum(int(e.get('total') or 0) for e in resultado['por_equipo'])
    if anio == ctx.hoy.year:
        # datos-grafica solo conoce el año en curso.
        por_fuente = (await ctx.get('/api/datos-grafica', {'periodo': 'mes', 'mes': mes})).datos or []
        por_mes = (await ctx.get('/api/datos-grafica', {'periodo': '12meses'})).datos or []
        if isinstance(por_fuente, list):
            resultado['por_fuente'] = dict(zip(FUENTES, por_fuente))
            resultado['total_del_mes'] = sum(int(x or 0) for x in por_fuente)
        if isinstance(por_mes, list) and len(por_mes) == 12:
            resultado['por_mes_del_anio'] = dict(zip(range(1, 13), por_mes))
            resultado['total_del_anio'] = sum(int(x or 0) for x in por_mes)
    else:
        resultado['nota'] = 'El desglose por fuente solo existe para el año en curso.'
    return para_modelo(resultado, enmascarar=ctx.enmascarar)


async def ver_pendientes(ctx: Contexto) -> str:
    respuesta = await ctx.get('/api/obtener-pendientes')
    candidatos = respuesta.datos if isinstance(respuesta.datos, list) else []
    return para_modelo({'total': len(candidatos), 'candidatos': candidatos}, enmascarar=ctx.enmascarar)


async def ver_incidencias(ctx: Contexto, registro_id: Any = None) -> str:
    try:
        registro_id = int(registro_id)
    except (TypeError, ValueError):
        return 'registro_id debe ser un número (búscalo con buscar_empleado).'
    respuesta = await ctx.get(f'/api/empleados/{registro_id}/incidencias')
    return para_modelo(respuesta.datos, enmascarar=ctx.enmascarar)


async def buscar_endpoints(ctx: Contexto, consulta: str = '') -> str:
    guia = await ctx.backend.guia()
    encontrados = buscar_en_guia(guia, consulta, ctx.permisos, solo_lectura=True)
    if not encontrados:
        return 'No hay consultas que coincidan para tu cuenta.'
    return para_modelo([
        {'ruta': e['ruta'], 'resumen': e['resumen'], 'parametros': e.get('parametros') or {},
         **({'archivo': True} if e.get('archivo') else {})}
        for e in encontrados
    ], enmascarar=False)


async def consultar_api(ctx: Contexto, ruta: str = '', parametros: dict[str, Any] | None = None) -> str:
    try:
        ruta = normalizar_ruta(ruta)
        endpoint = buscar_endpoint(await ctx.backend.guia(), 'GET', ruta)
    except RutaNoPermitida as error:
        return str(error)
    if not puede_usar(endpoint, ctx.permisos):
        return 'Tu cuenta no tiene permiso para esa consulta.'
    parametros = parametros if isinstance(parametros, dict) else {}
    if len(parametros) > MAX_PARAMETROS or any(
        not isinstance(k, str) or len(str(v)) > MAX_VALOR_PARAMETRO for k, v in parametros.items()
    ):
        return f'Parámetros inválidos (máximo {MAX_PARAMETROS}, valores cortos).'
    respuesta = await ctx.get(ruta, {k: str(v) for k, v in parametros.items()})
    if respuesta.es_archivo:
        if not endpoint.get('archivo'):
            return 'Esa ruta devolvió un archivo que no se puede mostrar.'
        nombre = respuesta.nombre_archivo or 'archivo.xlsx'
        ctx.archivos.append(Archivo(nombre, respuesta.contenido))
        return f'Se le envió a la persona el archivo «{nombre}». Tú no ves su contenido.'
    return para_modelo(respuesta.datos, enmascarar=ctx.enmascarar)


IMPLEMENTACIONES: dict[str, Callable[..., Awaitable[str]]] = {
    'ver_pendientes': ver_pendientes,
    'ver_incidencias': ver_incidencias,
    'buscar_empleado': buscar_empleado,
    'ver_empleado': ver_empleado,
    'contar_empleados': contar_empleados,
    'resumen_reclutamiento': resumen_reclutamiento,
    'buscar_endpoints': buscar_endpoints,
    'consultar_api': consultar_api,
}
LECTURA = frozenset(IMPLEMENTACIONES)
assert LECTURA == NOMBRES

# Las de escritura se agregan aquí para que el agente tenga una sola lista.
from bot import escrituras  # noqa: E402

DEFINICIONES = DEFINICIONES + escrituras.DEFINICIONES
IMPLEMENTACIONES.update(escrituras.IMPLEMENTACIONES)
NOMBRES = frozenset(IMPLEMENTACIONES)


async def ejecutar(ctx: Contexto, nombre: str, argumentos: dict[str, Any]) -> str:
    """Corre una herramienta; los errores vuelven al modelo como texto, nunca como excepción."""
    funcion = IMPLEMENTACIONES.get(nombre)
    if funcion is None:
        return f'La herramienta «{nombre}» no existe.'
    try:
        return await funcion(ctx, **{k: v for k, v in argumentos.items() if isinstance(k, str)})
    except TypeError:
        return f'Argumentos inválidos para {nombre}.'
    except GuardaFallo as error:
        return f'No preparé el cambio: {error}'
    except SinPermiso as error:
        return str(error)
    except ErrorBackend as error:
        if error.status == 403:
            return 'El sistema respondió 403: tu cuenta no tiene permiso para esto.'
        if error.status == 401:
            raise
        return f'El sistema respondió {error.status}: {error.mensaje[:300]}'
