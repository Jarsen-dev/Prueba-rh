# Bot de Telegram del sistema de RH.
#
# Un solo proceso a propósito: Telegram rechaza a dos lectores del mismo bot
# (409 Conflict). No publica puertos: sale hacia Telegram por long polling y
# llega al backend por la red interna de Docker. No tiene credenciales de BD.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10002 botuser
COPY --chown=botuser:botuser bot ./bot
USER botuser

CMD ["python", "-m", "bot"]
