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

    def problemas(self) -> list[str]:
        faltan = []
        if not self.TELEGRAM_BOT_TOKEN.strip():
            faltan.append('TELEGRAM_BOT_TOKEN está vacío.')
        if not self.TELEGRAM_BOT_SECRETO_INTERNO.strip():
            faltan.append('TELEGRAM_BOT_SECRETO_INTERNO está vacío (debe ser el mismo que en el backend).')
        if self.BOT_MINUTOS_PENDIENTE < 1:
            faltan.append('BOT_MINUTOS_PENDIENTE debe ser al menos 1.')
        return faltan


@lru_cache
def obtener_settings() -> Settings:
    return Settings()
