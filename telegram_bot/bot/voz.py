"""Notas de voz: se transcriben con OpenAI y siguen el mismo camino que un texto.

La transcripción se le muestra a la persona («🎙 Entendí: …») antes de contestar,
así ve qué entendió el bot. Para las guardas, lo transcrito cuenta como lo que
ella dijo, y cualquier cambio sigue necesitando el botón Confirmar, con un
resumen que el código arma a partir de los datos reales.
"""

import time
from dataclasses import dataclass

import httpx

from bot.llm import ErrorLLM

#: Pistas de vocabulario para que el modelo escriba bien nombres propios y términos de RH.
VOCABULARIO = ('Recursos Humanos de Cheong Woon y Kronos. Términos: incidencia, vacaciones, incapacidad, '
               'suspensión, carta compromiso, reingreso, baja, reactivar, candidato, reclutador, equipo, EPS, '
               'moldero, ayudante general, turno día, turno noche, salario, pago semanal, quincenal. '
               'Escribe los números y las fechas con dígitos, por ejemplo «del 16 al 20 de noviembre», «2600».')


@dataclass
class Transcripcion:
    texto: str
    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    segundos: float = 0.0


def interpretar(datos: dict, modelo: str, segundos: float) -> Transcripcion:
    uso = datos.get('usage') or {}
    # gpt-4o-*-transcribe cobra por tokens; whisper-1 informa duración (sin tokens).
    entrada = int(uso.get('input_tokens') or 0) if uso.get('type') == 'tokens' else 0
    salida = int(uso.get('output_tokens') or 0) if uso.get('type') == 'tokens' else 0
    return Transcripcion(texto=' '.join(str(datos.get('text') or '').split()), modelo=modelo,
                         tokens_entrada=entrada, tokens_salida=salida, segundos=segundos)


class ClienteVoz:
    def __init__(self, *, api_key: str, modelo: str, base_url: str, http: httpx.AsyncClient,
                 timeout: float = 60) -> None:
        self.modelo = modelo
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._api_key = api_key
        self._http = http
        self._timeout = timeout

    async def transcribir(self, audio: bytes, nombre: str = 'voz.ogg', tipo: str = 'audio/ogg') -> Transcripcion:
        inicio = time.monotonic()
        try:
            respuesta = await self._http.post(
                self._url, headers={'Authorization': f'Bearer {self._api_key}'}, timeout=self._timeout,
                files={'file': (nombre, audio, tipo)},
                # Sin «language»: el modelo detecta el idioma (el vocabulario en español lo inclina al español).
                data={'model': self.modelo, 'response_format': 'json', 'prompt': VOCABULARIO},
            )
        except httpx.TimeoutException as exc:
            raise ErrorLLM('OpenAI no transcribió a tiempo.') from exc
        except httpx.HTTPError as exc:
            raise ErrorLLM(f'No se pudo conectar con OpenAI: {type(exc).__name__}') from exc
        if respuesta.status_code >= 400:
            raise ErrorLLM(f'OpenAI respondió {respuesta.status_code} al transcribir: {respuesta.text[:200]}')
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ErrorLLM('OpenAI devolvió una transcripción inválida.') from exc
        return interpretar(datos, self.modelo, time.monotonic() - inicio)
