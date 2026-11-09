"""Configuración del bot. Nadie lee os.environ fuera de este módulo."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra='ignore')

    TELEGRAM_BOT_TOKEN: str = ''
    TELEGRAM_BOT_SECRETO_INTERNO: str = ''
    BACKEND_URL: str = 'http://backend:8000'
    #: Minutos que vive un cambio esperando el botón Confirmar.
    BOT_MINUTOS_PENDIENTE: int = 30
    #: Para «hoy» y «mañana» en las fechas de los formularios.
    ZONA_HORARIA: str = 'America/Mexico_City'

    # --- Agente (texto libre). Sin OPENAI_API_KEY solo funcionan el menú y los comandos.
    OPENAI_API_KEY: str = ''
    OPENAI_MODEL: str = 'gpt-4o-mini'
    OPENAI_BASE_URL: str = 'https://api.openai.com/v1'
    #: Tope de tokens (entrada + salida) por día para todo el bot y por persona.
    BOT_TOKENS_DIA: int = 300_000
    BOT_TOKENS_PERSONA_DIA: int = 60_000
    #: CURP, RFC, NSS, teléfonos, correo y domicilio se enmascaran antes de llegar al modelo.
    BOT_ENMASCARAR_DATOS: bool = True
    #: Notas de voz (requieren OPENAI_API_KEY). 0 las apaga.
    BOT_VOZ_MAX_SEGUNDOS: int = 120
    OPENAI_MODELO_VOZ: str = 'gpt-4o-mini-transcribe'

    @property
    def voz_activa(self) -> bool:
        return self.agente_activo and self.BOT_VOZ_MAX_SEGUNDOS > 0

    @property
    def agente_activo(self) -> bool:
        return bool(self.OPENAI_API_KEY.strip())

    def problemas(self) -> list[str]:
        faltan = []
        if not self.TELEGRAM_BOT_TOKEN.strip():
            faltan.append('TELEGRAM_BOT_TOKEN está vacío.')
        if not self.TELEGRAM_BOT_SECRETO_INTERNO.strip():
            faltan.append('TELEGRAM_BOT_SECRETO_INTERNO está vacío (debe ser el mismo que en el backend).')
        if self.agente_activo and (self.BOT_TOKENS_DIA <= 0 or self.BOT_TOKENS_PERSONA_DIA <= 0):
            faltan.append('Con OPENAI_API_KEY, BOT_TOKENS_DIA y BOT_TOKENS_PERSONA_DIA deben ser mayores que 0.')
        if self.BOT_MINUTOS_PENDIENTE < 1:
            faltan.append('BOT_MINUTOS_PENDIENTE debe ser al menos 1.')
        return faltan


@lru_cache
def obtener_settings() -> Settings:
    return Settings()
