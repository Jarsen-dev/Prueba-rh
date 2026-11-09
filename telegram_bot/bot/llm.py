"""Cliente de OpenAI (Responses API) con httpx directo.

Se usa httpx y no el SDK: aquí solo hace falta una llamada y el SDK cambia de
versión mayor seguido. El agente guarda su historial en formato de Chat
Completions; ``a_entrada_responses`` / ``interpretar_responses`` traducen de
ida y vuelta (funciones puras, probadas).

Ningún mensaje que sale de aquí lleva imágenes ni archivos.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


class ErrorLLM(Exception):
    """El modelo no contestó, contestó con error o contestó algo inservible."""


@dataclass(frozen=True)
class LlamadaHerramienta:
    id: str
    nombre: str
    argumentos: dict[str, Any]


@dataclass
class RespuestaLLM:
    modelo: str
    contenido: str
    llamadas: list[LlamadaHerramienta]
    #: El mensaje tal cual, para reenviarlo con los resultados de las herramientas.
    mensaje: dict[str, Any]
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cache: int = 0
    segundos: float = 0.0


def interpretar_argumentos(crudo: Any) -> dict[str, Any]:
    if isinstance(crudo, dict):
        return crudo
    if crudo in (None, ''):
        return {}
    try:
        valor = json.loads(crudo)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ErrorLLM('El modelo mandó argumentos que no son JSON.') from exc
    if not isinstance(valor, dict):
        raise ErrorLLM('El modelo mandó argumentos que no son un objeto.')
    return valor


def a_entrada_responses(
    mensajes: list[dict[str, Any]], herramientas: list[dict[str, Any]] | None
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Mensajes y herramientas de Chat Completions -> (instructions, input, tools)."""
    instrucciones: list[str] = []
    entrada: list[dict[str, Any]] = []
    for mensaje in mensajes:
        rol = mensaje.get('role')
        if rol == 'system':
            instrucciones.append(str(mensaje.get('content') or ''))
        elif rol == 'tool':
            entrada.append({
                'type': 'function_call_output',
                'call_id': mensaje.get('tool_call_id'),
                'output': str(mensaje.get('content') or ''),
            })
        elif rol == 'assistant':
            if mensaje.get('content'):
                entrada.append({'role': 'assistant', 'content': mensaje['content']})
            for llamada in mensaje.get('tool_calls') or []:
                funcion = llamada.get('function') or {}
                argumentos = funcion.get('arguments')
                entrada.append({
                    'type': 'function_call',
                    'call_id': llamada.get('id'),
                    'name': funcion.get('name'),
                    'arguments': argumentos if isinstance(argumentos, str) else json.dumps(argumentos or {}),
                })
        else:
            entrada.append({'role': rol or 'user', 'content': mensaje.get('content') or ''})
    tools = [
        {
            'type': 'function',
            'name': d['function']['name'],
            'description': d['function'].get('description', ''),
            'parameters': d['function'].get('parameters', {}),
            'strict': False,
        }
        for d in herramientas or []
    ]
    return '\n\n'.join(instrucciones), entrada, tools


def interpretar_responses(cuerpo: dict[str, Any], modelo: str) -> RespuestaLLM:
    salida = cuerpo.get('output')
    if not isinstance(salida, list):
        raise ErrorLLM('La respuesta del modelo no trae ninguna salida.')
    textos: list[str] = []
    crudas: list[dict[str, Any]] = []
    for item in salida:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'message':
            for parte in item.get('content') or []:
                if isinstance(parte, dict) and parte.get('type') == 'output_text':
                    textos.append(str(parte.get('text') or ''))
        elif item.get('type') == 'function_call':
            crudas.append({
                'id': str(item.get('call_id') or item.get('id') or f'llamada_{len(crudas)}'),
                'type': 'function',
                'function': {'name': str(item.get('name', '')), 'arguments': item.get('arguments') or '{}'},
            })
    contenido = '\n'.join(t for t in textos if t).strip()
    uso = cuerpo.get('usage') or {}
    return RespuestaLLM(
        modelo=modelo,
        contenido=contenido,
        llamadas=[
            LlamadaHerramienta(c['id'], c['function']['name'], interpretar_argumentos(c['function']['arguments']))
            for c in crudas
        ],
        mensaje={'role': 'assistant', 'content': contenido, **({'tool_calls': crudas} if crudas else {})},
        tokens_entrada=int(uso.get('input_tokens') or 0),
        tokens_salida=int(uso.get('output_tokens') or 0),
        tokens_cache=int((uso.get('input_tokens_details') or {}).get('cached_tokens') or 0),
    )


def clave_de_cache(telegram_id: int) -> str:
    """``prompt_cache_key`` estable por persona, sin su id en claro."""
    return 'rh-' + hashlib.sha256(f'prueba-rh:{telegram_id}'.encode()).hexdigest()[:16]


def admite_temperatura(modelo: str) -> bool:
    """Los modelos de razonamiento (o1/o3/o4, gpt-5…) rechazan ``temperature``."""
    nombre = modelo.lower()
    return nombre.startswith(('gpt-4', 'gpt-3.5'))


class ClienteOpenAI:
    def __init__(self, *, api_key: str, modelo: str, base_url: str, http: httpx.AsyncClient,
                 timeout: float = 60) -> None:
        self.modelo = modelo
        self._url = f"{base_url.rstrip('/')}/responses"
        self._api_key = api_key
        self._http = http
        self._timeout = timeout

    async def completar(self, mensajes: list[dict[str, Any]], *, herramientas: list[dict[str, Any]] | None = None,
                        max_tokens: int = 1200, clave_cache: str | None = None) -> RespuestaLLM:
        instrucciones, entrada, tools = a_entrada_responses(mensajes, herramientas)
        cuerpo: dict[str, Any] = {
            'model': self.modelo,
            'instructions': instrucciones,
            'input': entrada,
            'max_output_tokens': max_tokens,
            # La conversación vive en el bot; OpenAI no tiene por qué guardarla.
            'store': False,
        }
        if tools:
            cuerpo['tools'] = tools
            cuerpo['tool_choice'] = 'auto'
        if clave_cache:
            cuerpo['prompt_cache_key'] = clave_cache
        if admite_temperatura(self.modelo):
            cuerpo['temperature'] = 0.2
        inicio = time.monotonic()
        try:
            respuesta = await self._http.post(
                self._url, json=cuerpo, headers={'Authorization': f'Bearer {self._api_key}'}, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise ErrorLLM('OpenAI no respondió a tiempo.') from exc
        except httpx.HTTPError as exc:
            raise ErrorLLM(f'No se pudo conectar con OpenAI: {type(exc).__name__}') from exc
        if respuesta.status_code >= 400:
            # No se registra el cuerpo completo: puede traer el prompt de vuelta.
            raise ErrorLLM(f'OpenAI respondió {respuesta.status_code}: {respuesta.text[:200]}')
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ErrorLLM('OpenAI no devolvió JSON.') from exc
        resultado = interpretar_responses(datos, self.modelo)
        resultado.segundos = time.monotonic() - inicio
        return resultado
