"""Cliente de la API del sistema, actuando como la persona vinculada.

El bot no tiene permisos propios. Por cada persona pide al backend una sesión
corta (15 min, canal ``telegram``) y la manda como ``Authorization: Bearer``,
así que cada ruta aplica los mismos permisos que en el panel y la bitácora
registra a esa persona «vía Telegram».
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

CABECERA_SECRETO: Final[str] = 'X-Bot-Secreto'
PREFIJO_INTERNO: Final[str] = '/api/telegram/interno'

#: Margen para no mandar una sesión que vence a media petición.
MARGEN_SESION: Final[timedelta] = timedelta(seconds=60)


class ErrorBackend(Exception):
    """La API contestó con error. ``mensaje`` ya viene en español."""

    def __init__(self, status: int, mensaje: str, codigo: str | None = None) -> None:
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje
        self.codigo = codigo


class NoVinculado(ErrorBackend):
    """Este Telegram no tiene cuenta del panel (o se la quitaron)."""


@dataclass
class Sesion:
    token: str
    expira: datetime
    usuario: dict[str, Any]

    @property
    def permisos(self) -> list[str]:
        return list(self.usuario.get('permisos') or [])


@dataclass
class RespuestaBackend:
    status: int
    tipo: str
    datos: Any = None
    contenido: bytes | None = None
    nombre_archivo: str | None = None

    @property
    def es_archivo(self) -> bool:
        return self.contenido is not None


def mensaje_de_error(status: int, cuerpo: Any, texto: str = '') -> str:
    """El mensaje de error de la API (``{"error": ...}`` o texto plano)."""
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get('error'), str) and cuerpo['error'].strip():
        return cuerpo['error'].strip()
    texto = (texto or '').strip()
    if texto and len(texto) <= 300 and not texto.lstrip().startswith('<'):
        return texto.removeprefix('ERROR:').strip()
    if status == 403:
        return 'Tu cuenta no tiene permiso para esta acción.'
    if status == 404:
        return 'No se encontró.'
    return f'El sistema respondió con error {status}.'


def nombre_de_descarga(cabecera: str | None) -> str | None:
    """Nombre de archivo de un ``Content-Disposition``."""
    if not cabecera:
        return None
    extendido = re.search(r"filename\*=UTF-8''([^;]+)", cabecera, re.IGNORECASE)
    if extendido:
        return unquote(extendido.group(1)).strip()
    simple = re.search(r'filename="?([^";]+)"?', cabecera, re.IGNORECASE)
    return simple.group(1) if simple else None


def _json_o_nada(respuesta: httpx.Response) -> Any:
    try:
        return respuesta.json()
    except ValueError:
        return None


class ClienteBackend:
    def __init__(self, *, base_url: str, secreto: str, http: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip('/')
        self._secreto = secreto
        self._http = http
        self._sesiones: dict[int, Sesion] = {}
        self._guia: dict[str, Any] | None = None

    # --- Endpoints internos ------------------------------------------------

    async def _interno(self, ruta: str, json: dict[str, Any] | None = None, *, metodo: str = 'POST',
                       params: dict[str, Any] | None = None) -> Any:
        respuesta = await self._http.request(
            metodo,
            f'{self._base}{PREFIJO_INTERNO}{ruta}',
            json=json,
            params=params,
            headers={CABECERA_SECRETO: self._secreto},
            timeout=30,
        )
        cuerpo = _json_o_nada(respuesta)
        codigo = cuerpo.get('codigo') if isinstance(cuerpo, dict) else None
        if respuesta.status_code == 404 and ruta == '/sesion':
            raise NoVinculado(404, mensaje_de_error(404, cuerpo), codigo)
        if respuesta.status_code >= 400:
            raise ErrorBackend(
                respuesta.status_code, mensaje_de_error(respuesta.status_code, cuerpo, respuesta.text), codigo
            )
        return cuerpo

    @staticmethod
    def _a_sesion(cuerpo: dict[str, Any]) -> Sesion:
        return Sesion(
            token=cuerpo['token'],
            expira=datetime.fromisoformat(cuerpo['expira_en']),
            usuario=cuerpo['usuario'],
        )

    async def vincular(self, codigo: str, telegram_id: int, telegram_username: str | None) -> Sesion:
        cuerpo = await self._interno(
            '/vincular',
            {'codigo': codigo, 'telegram_id': telegram_id, 'telegram_username': telegram_username},
        )
        sesion = self._a_sesion(cuerpo)
        self._sesiones[telegram_id] = sesion
        return sesion

    async def desvincular(self, telegram_id: int) -> bool:
        self._sesiones.pop(telegram_id, None)
        cuerpo = await self._interno('/desvincular', {'telegram_id': telegram_id})
        return bool(cuerpo and cuerpo.get('desvinculado'))

    async def sesion(self, telegram_id: int, *, renovar: bool = False) -> Sesion:
        vigente = self._sesiones.get(telegram_id)
        if not renovar and vigente is not None and vigente.expira - MARGEN_SESION > datetime.now(UTC):
            return vigente
        try:
            cuerpo = await self._interno('/sesion', {'telegram_id': telegram_id})
        except ErrorBackend:
            self._sesiones.pop(telegram_id, None)
            raise
        sesion = self._a_sesion(cuerpo)
        self._sesiones[telegram_id] = sesion
        return sesion

    def olvidar(self, telegram_id: int) -> None:
        self._sesiones.pop(telegram_id, None)

    # --- Agente (rutas internas, sin sesión de la persona) ------------------

    async def guia(self) -> dict[str, Any]:
        """La guía de la API; se pide una vez por proceso (cambia solo con un deploy)."""
        if self._guia is None:
            self._guia = await self._interno('/guia', metodo='GET')
        return self._guia

    async def registrar_traza(self, telegram_id: int, traza: dict[str, Any]) -> int | None:
        cuerpo = await self._interno('/trazas', {'telegram_id': telegram_id, **traza})
        return cuerpo.get('id') if isinstance(cuerpo, dict) else None

    async def calificar(self, telegram_id: int, traza_id: int, valor: int) -> None:
        await self._interno(f'/trazas/{int(traza_id)}/calificacion', {'telegram_id': telegram_id, 'valor': valor})

    async def presupuesto(self, telegram_id: int) -> tuple[int, int]:
        """(tokens gastados hoy por todos, por esta persona)."""
        cuerpo = await self._interno('/presupuesto', metodo='GET', params={'telegram_id': telegram_id})
        return int(cuerpo['tokens_hoy']), int(cuerpo['tokens_hoy_persona'])

    # --- API como la persona -------------------------------------------------

    async def peticion(
        self,
        telegram_id: int,
        metodo: str,
        ruta: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, str] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 60,
    ) -> RespuestaBackend:
        """Llama a la API con la sesión de la persona.

        Un 401 renueva la sesión una sola vez (la cacheada pudo vencer en el
        camino). Si vuelve a dar 401, desvincularon la cuenta o le cambiaron la
        contraseña, y se dice tal cual.
        """
        for intento in range(2):
            sesion = await self.sesion(telegram_id, renovar=intento == 1)
            respuesta = await self._http.request(
                metodo,
                f'{self._base}{ruta}',
                params={k: v for k, v in (params or {}).items() if v not in (None, '')},
                json=json,
                data=data,
                files=files,
                headers={**(headers or {}), 'Authorization': f'Bearer {sesion.token}'},
                timeout=timeout,
            )
            if respuesta.status_code != 401 or intento == 1:
                break

        tipo = respuesta.headers.get('content-type', '').split(';')[0].strip()
        if respuesta.status_code >= 400:
            if respuesta.status_code == 401:
                self.olvidar(telegram_id)
            cuerpo = _json_o_nada(respuesta)
            raise ErrorBackend(
                respuesta.status_code,
                mensaje_de_error(respuesta.status_code, cuerpo, respuesta.text),
                cuerpo.get('codigo') if isinstance(cuerpo, dict) else None,
            )

        if tipo == 'application/json':
            return RespuestaBackend(respuesta.status_code, tipo, datos=_json_o_nada(respuesta))
        if respuesta.status_code == 204 or not respuesta.content:
            return RespuestaBackend(respuesta.status_code, tipo)
        return RespuestaBackend(
            respuesta.status_code,
            tipo,
            contenido=respuesta.content,
            nombre_archivo=nombre_de_descarga(respuesta.headers.get('content-disposition')),
        )
