# CLAUDE.md

Sistema de RH de Cheong Woon México (empresas: **Cheong Woon** y **Kronos**): registro público de candidatos, portal interno de RH, expediente digital de empleados, indicadores de reclutamiento, generación de documentos/gafetes y bots (WhatsApp y Telegram). Todo el código, UI y mensajes están en español.

## Repositorio

- `origin` = fork propio (`Jarsen-dev/Prueba-rh`), `upstream` = repo del compañero (`SystemChWn/Prueba-rh`). Para sincronizar: `git fetch upstream && git merge --ff-only upstream/main`.
- Rama `docker-branch`: rama vieja (anterior a la reorganización actual); su contenido ya está superado por `main`.

## Stack y estructura

- **Frontend** (`frontend/`): HTML/CSS/JS estático, sin build ni framework. Servido por nginx, que reenvía `/api/` al backend (`frontend/nginx.conf`). Página de inicio: `portalrh.html`.
- **Backend** (`backend/utily.py`): una sola app Flask (~3000 líneas, ~49 rutas) servida con gunicorn (`utily:app`). Usa psycopg2 directo (sin ORM), pandas/openpyxl para exportar Excel y OpenAI para escanear documentos. Los módulos nuevos van aparte y se registran al final de `utily.py`: `auth.py` (sesión y permisos por ruta) y `bitacora.py`.
- **BD**: PostgreSQL 16. Esquema en `db/init.sql`, que **solo se aplica cuando el volumen está vacío**. No hay migraciones: los cambios de esquema van como `ALTER TABLE` manual o con `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` defensivos dentro de `utily.py` (p. ej. `asegurar_tabla_incidencias`).
- **Chatbot WhatsApp** (`Chatbot_Wpp/bot.py`): Flask + Meta Cloud API; corre con la imagen del backend (servicio `chatbot`). `whatsapp-puente.js` es un puente alternativo con wppconnect/Puppeteer.
- **Bot de Telegram** (`telegram/`): independiente, con su propio venv (`.venv-telegram`) y requirements; corre fuera de Docker contra Postgres en `127.0.0.1:5434`.

### Páginas principales del frontend

| Archivo | Función |
|---|---|
| `portalrh.html` + `access-control.js` | Login contra `/api/auth/login` y guardia de página por rol (`admin`, `reclutamiento`, `notificaciones`) |
| `auth-fetch.js` | Se carga primero en toda página protegida: añade el Bearer a los `fetch` al backend y manda al login en 401 |
| `registro.html` | Formulario público de candidatos (CP → colonia vía `cp.csv`, firma, encuesta, escaneo de documentos) |
| `Ingreso.html` | Asignación de empresa/área/puesto/turno/salario a candidatos |
| `InicioReclu.html` | Indicadores de reclutamiento (Chart.js), equipos, enlaces temporales |
| `iniciorh.html` | Inicio del portal de RH |
| `Cheong Woon.html`, `Kronos.html` | Listados de empleados por empresa, filtros, exportación a Excel |
| `empleado.html` | Expediente individual: datos, documentos, incidencias/vacaciones |
| `documentos/*.html`, `formatos-lote.html` | Contratos, cartas IMSS/compromiso, checklist, evaluación, gafetes |
| `data-manager.js`, `control-accesos.js`, `shared.css` | Utilidades compartidas |

Los nombres de archivo tienen mayúsculas y espacios (`Cheong Woon.html`); respetarlos exactamente en enlaces.

### Tablas (`db/init.sql`)

`registro` (candidatos/empleados; nombre en `nombre_completo`), `encuesta_reclutamiento`, `ingresos_puesto`, `incidencias_empleado`, `chatbot_credenciales`, `usuarios_acceso`, `personal_reclutamiento`, `bitacora`, `telegram_codigos`, `telegram_vinculos`.

## Ejecutar

```bash
docker compose up -d --build     # frontend :3001, backend :8001, chatbot :5002, postgres 127.0.0.1:5434
docker compose logs -f backend
```

Nunca usar `docker compose down -v` (borra la BD). El frontend se monta como volumen, así que los cambios de HTML/JS se ven sin reconstruir; el backend **sí** requiere `--build` (el Dockerfile copia solo `utily.py`).

Variables en `.env` (no versionado; plantilla en `.env.example`): `AUTH_SECRET_KEY`, `AUTH_ESTRICTO`, `AUTH_COOKIE_SECURE`, `ADMIN_INICIAL_USUARIO/PASSWORD`, `POSTGRES_DB/USER/PASSWORD/HOST/PORT`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `WHATSAPP_*`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `TELEGRAM_POSTGRES_PORT`, `DB_WRITE_LOCK`, `EMPLOYEE_DOCS_ROOT`.

## Tests

`unittest` con mocks (no requieren BD real):

```bash
cd backend && python -m unittest discover -p 'test_*.py'
python -m unittest Chatbot_Wpp/test_bot.py
.venv-telegram/bin/python -m unittest telegram/test_telegram_bot.py
```

## Notas y deuda conocida

- Muchas rutas existen duplicadas con y sin prefijo `/api` (compatibilidad); mantener ambas al modificarlas.
- **Sesión:** toda ruta nueva debe clasificarse en `auth.PERMISOS_POR_ENDPOINT` (si no, exige `admin` y `test_auth.py` falla). Los tokens de canal `telegram` duran 15 min y exigen un vínculo vigente en `telegram_vinculos` en cada petición.
- `backend/uploads/` contiene archivos de empleados (datos personales); evitar versionar más archivos reales ahí.
- `.env` estuvo versionado en el historial de git (eliminado en el commit `2361fe4`); si contenía credenciales reales, deberían rotarse.
- El README está parcialmente desactualizado (menciona `backend/app.py` y `cheong-woon.html`).
- Plan del agente de Telegram (niveles 0–3): `~/.claude/plans/perfecto-ahora-necesito-que-twinkling-elephant.md`. Se toma como referencia `~/security_evaluations/telegram_bot`.
