# Prueba-rh

Sistema de RH de Cheong Woon Mexico: formulario público de registro de candidatos + portal interno de RH. Frontend estático (HTML/CSS/JS sin build) servido por nginx, backend Flask, base de datos Postgres — todo orquestado con Docker Compose.

## Estructura

```
backend/    Flask app (app.py), requirements.txt, Dockerfile
frontend/   Páginas HTML estáticas, cp.csv, nginx.conf, Dockerfile
db/         init.sql (esquema de las tablas registro y encuesta_reclutamiento)
docker-compose.yml
.env.example
```

## Despliegue con Docker (paso a paso, sin experiencia previa)

Docker empaqueta cada parte del proyecto (frontend, backend, base de datos) en "contenedores" — como cajas aisladas que ya traen todo lo necesario para correr, sin tener que instalar Python, Postgres ni nginx a mano en el servidor. `docker compose` es la herramienta que levanta y coordina esas tres cajas juntas leyendo el archivo `docker-compose.yml` de este repo.

### 1. Instalar Docker en el servidor Ubuntu

Si el servidor todavía no tiene Docker instalado (si ya corre el otro proyecto en contenedores, probablemente ya lo tiene y puedes saltar a este paso solo para confirmarlo):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Después de `usermod`, cierra la sesión SSH y vuelve a entrar (o ejecuta `newgrp docker`) para que el cambio de permisos tome efecto. Confirma que quedó instalado:

```bash
docker --version
docker compose version
```

Si `docker compose version` da error de "unknown command", prueba con el guion: `docker-compose version` (es una versión más antigua de la misma herramienta; en ese caso usa `docker-compose` en vez de `docker compose` en todos los comandos de abajo).

### 2. Obtener el código en el servidor

```bash
git clone git@github.com:SystemChWn/Prueba-rh.git
cd Prueba-rh
```

(Si ya existe una copia porque hiciste esto antes, en vez de clonar de nuevo entra a la carpeta y corre `git pull` para traer los cambios más recientes.)

### 3. Configurar las variables de entorno

El proyecto no trae contraseñas escritas en el código — las lee de un archivo `.env` que tú creas localmente y que nunca se sube a git. Cópialo desde la plantilla y edítalo:

```bash
cp .env.example .env
nano .env
```

Cambia el valor de `POSTGRES_PASSWORD` (actualmente `changeme`) por una contraseña real y segura — es la contraseña de la base de datos de este proyecto, nadie más la necesita saber. Guarda con `Ctrl+O`, `Enter`, y sal con `Ctrl+X`.

### 4. Construir y levantar los contenedores

```bash
docker compose up -d --build
```

- `--build` arma las imágenes del frontend y backend desde cero la primera vez (o cuando cambie el código).
- `-d` ("detached") los deja corriendo en segundo plano; sin esa opción verías los logs en vivo y el comando "se quedaría pegado" en la terminal.

La primera vez puede tardar uno o dos minutos mientras descarga las imágenes base (Python, nginx, Postgres) e instala las dependencias.

### 5. Confirmar que todo quedó corriendo

```bash
docker compose ps
```

Deberías ver los servicios `postgres`, `backend`, `chatbot` y `frontend` con estado `Up` (o `Up (healthy)`). Si alguno dice `Restarting` o `Exited`, algo falló; revisa sus logs con:

```bash
docker compose logs frontend   # o backend, o postgres
```

Prueba abrir el sitio desde un navegador (o con `curl` desde el mismo servidor):

```bash
curl -I http://localhost:3001/
```

Si responde `HTTP/1.1 200 OK`, el frontend está sirviendo correctamente. Desde fuera del servidor, se accede con `http://<ip-del-servidor>:3001/`.

### Comandos útiles del día a día

| Qué quieres hacer | Comando |
|---|---|
| Ver logs en vivo de un servicio | `docker compose logs -f backend` |
| Parar todo (sin borrar datos) | `docker compose stop` |
| Volver a arrancar lo que ya estaba parado | `docker compose start` |
| Apagar y quitar los contenedores (los datos de Postgres se conservan en su volumen) | `docker compose down` |
| Reconstruir después de cambiar código y volver a levantar | `docker compose up -d --build` |
| Ver qué contenedores/servicios están corriendo | `docker compose ps` |

**Importante:** nunca uses `docker compose down -v` a menos que quieras borrar por completo la base de datos de este proyecto — el flag `-v` elimina también el volumen donde vive la información de Postgres.

Puertos publicados en el host:

| Servicio  | Puerto host | Notas |
|-----------|-------------|-------|
| frontend  | 3001        | nginx sirve las páginas estáticas y reenvía `/api/` al backend |
| backend   | 127.0.0.1:8001 | API Flask (gunicorn); solo en el propio servidor, desde fuera se usa nginx |
| chatbot   | 5002        | Webhook de WhatsApp, respuestas automáticas y contactos de Reclutamiento |
| postgres  | 127.0.0.1:5434 | PostgreSQL para herramientas locales; el backend usa la red interna de Docker |

Estos puertos se eligieron para no chocar con el otro proyecto que ya corre en el mismo servidor (`produccion_frontend` en 3000, `produccion_backend` en 8000, `produccion_pgadmin` en 5050 y su Postgres publicado en el host 5433). PostgreSQL de este proyecto escucha en el puerto `5434` del loopback (`127.0.0.1`) para herramientas locales (pgAdmin, psql); no queda publicado en interfaces externas. Si el puerto 5434 ya está ocupado, define `TELEGRAM_POSTGRES_PORT` en `.env` con otro puerto local disponible y recrea el servicio de PostgreSQL.

El esquema de las tablas (`registro`, `encuesta_reclutamiento`, `ingresos_puesto`, `incidencias_empleado`) se crea automáticamente la primera vez que el volumen de Postgres está vacío, vía `db/init.sql`. La tabla `registro` guarda el nombre de cada persona en un único campo `nombre_completo`. En bases existentes que tengan los campos separados de una versión anterior, la API vuelve a agregar `nombre_completo` y combina los valores para conservarlos; las columnas anteriores se dejan intactas para no borrar datos. Otros cambios de esquema posteriores requieren un `ALTER TABLE` manual — no hay herramienta de migraciones.

### Chatbot de WhatsApp

El chatbot conserva sus respuestas automáticas para personal activo y su conexión con los teléfonos registrados en Reclutamiento. Configura `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` y `WHATSAPP_VERIFY_TOKEN` con los datos de Meta WhatsApp Cloud API. Los avisos de incidencias por WhatsApp y la notificación diferida a contactos de emergencia ya no forman parte del sistema.

### Bot de Telegram

El bot (`telegram_bot/`) es un contenedor aparte, con un solo proceso y long polling: no publica puertos ni necesita webhook. **No tiene credenciales de la base de datos ni permisos propios.** Cada persona vincula su Telegram con su cuenta del panel, y el bot pide al backend una sesión de 15 minutos de esa cuenta. Con ella llama a la misma API que el panel, así que se aplican los mismos permisos y la bitácora registra a la persona con canal `telegram`.

**Configurar:**

1. Crea el bot con @BotFather y copia su token.
2. En `.env` define:
   - `TELEGRAM_BOT_TOKEN`: el token del bot.
   - `TELEGRAM_BOT_USERNAME`: el usuario del bot, sin `@`; se usa para armar el enlace.
   - `TELEGRAM_BOT_SECRETO_INTERNO`: una cadena larga y aleatoria, por ejemplo `openssl rand -hex 32`. El backend y el bot usan la misma.
3. Levántalo con `docker compose --profile telegram up -d --build` (o pon `COMPOSE_PROFILES=telegram` en `.env`). Sin el perfil, el bot no arranca.

**Vincular una cuenta:**

1. En el panel, el botón «Telegram» de la barra genera un código de un solo uso, válido 10 minutos (`TELEGRAM_MINUTOS_CODIGO`), y un enlace `t.me/<bot>?start=CÓDIGO`.
2. Abre el enlace en el teléfono o manda `/vincular CÓDIGO` al bot.
3. Cada cuenta tiene a lo más un Telegram.

Para quitar el vínculo:
- la persona puede usar `/desvincular` en el bot o el mismo botón del panel;
- un admin puede revocar cualquier vínculo desde «Usuarios con acceso».

Revocar corta el acceso en la siguiente petición, y cambiar la contraseña de la cuenta también.

**Comandos:**

| Comando | Qué hace |
|---|---|
| `/sistema` (o `/menu`) | Menú con botones; ver abajo |
| `/consultar <número o nombre>` | Datos básicos de un empleado |
| `/eliminar <número>` | Pide dos confirmaciones con botón antes de eliminar; solo `admin` |
| `/cancelar` | Descarta el cambio o formulario pendiente |
| `/yo` | Muestra tu cuenta y tus permisos |
| `/desvincular` | Desconecta este Telegram de tu cuenta |
| `/ayuda` | Lista de comandos |

El bot solo atiende chats privados. Nada se escribe sin pulsar el botón Confirmar.

**Menú `/sistema` (sin IA):** solo muestra las opciones que permite la cuenta. Aun así, quien decide es el backend.

| Opción | Permiso | Qué hace |
|---|---|---|
| Empleados | admin, reclutamiento | Busca por número o nombre y muestra la ficha. Solo con `admin`: historial de incidencias, registrar incidencia (vacaciones, incapacidad, suspensión o carta compromiso, con foto o PDF opcional), dar de baja o reactivar, y eliminar (doble confirmación) |
| Ingresos pendientes | admin | Asigna empresa, área, puesto, turno, salario y pago a un candidato, igual que en `Ingreso.html` |
| Reclutamiento | admin, reclutamiento | Ingresos del mes por fuente y por equipo |
| Descargas | según el Excel | Listado general de Cheong Woon y de Kronos (`admin`) y reporte de reclutamiento del mes; el archivo llega al chat |

Cómo funcionan los formularios:
- Se llenan paso a paso con botones. Las fechas se escriben como DD/MM/AAAA o con los botones «Hoy» y «Mañana», y la zona horaria se fija con `ZONA_HORARIA`.
- Terminan en un resumen con Confirmar y mandan exactamente los mismos campos que el panel; las pruebas lo comparan con el HTML.
- Los catálogos de empresa, área, puesto, turno y pago salen de `GET /api/catalogos/ingreso` (`backend/catalogos.py`). Si cambias los de `Ingreso.html`, cambia también ese archivo: `test_catalogos.py` falla si no coinciden.

**Asistente por texto (IA):** con `OPENAI_API_KEY` el bot también entiende mensajes escritos. Sin la clave, solo funcionan el menú y los comandos. Ejemplos:
- consultas: «¿cuántos activos hay en Kronos en turno noche?», «¿qué incidencias tiene Ana?», «mándame el Excel de reclutamiento»;
- cambios: «registra vacaciones a Ana del 16 al 20 de noviembre», «da de baja a Bruno Castillo», «asigna a Iván a Kronos, EPS, moldero, noche, 2600 semanal», «cambia el teléfono de Laura a …».

- **Permisos:** actúa como la persona, con sus mismos permisos. Toda consulta pasa antes por la guía de la API (`backend/guia_api.py`), filtrada por permiso. La sesión, las cuentas, los vínculos de Telegram, los documentos personales y los binarios nunca entran. `test_guia.py` falla si una ruta nueva no está documentada ni oculta.
- **Cambios con Confirmar:** el agente **nunca escribe**. Sus herramientas `preparar_*` (`telegram_bot/bot/escrituras.py`) arman el mismo cambio que el menú, con los mismos campos que el panel. La persona lo confirma con el mismo botón, y los borrados piden dos clics. Va un cambio por mensaje. El resumen lo genera el código, no el modelo, y en la bitácora queda con origen `agente`.
- **Cambios disponibles:** baja y reactivación; vacaciones, incapacidad, suspensión y carta compromiso, con la foto o el PDF que se mande antes por el chat; asignar puesto a un candidato; cambiar empresa, área, puesto, turno, salario o pago; alta, edición y baja de reclutadores; borrar un empleado, un candidato o una incidencia. Los datos personales del empleado y el calendario se editan en el panel.
- **Guardas** (`telegram_bot/bot/guardas.py`), revisadas en código aunque el modelo diga otra cosa:
  - todo id salió de una consulta;
  - salario, teléfono y fechas aparecen en lo que escribió la persona («el lunes» no vale);
  - empresa, área, puesto, turno y pago están en el catálogo;
  - un cambio de puesto manda solo lo que cambia y muestra «antes → después»;
  - editar un reclutador conserva su equipo, porque el `PUT` del backend lo regresaría al 1.
- **Datos personales:** antes de llegar a OpenAI se quitan contraseñas, firmas y fotos, y se enmascaran CURP, RFC, NSS, teléfonos, correo, domicilio y contacto de emergencia. Se enmascaran por campo y también por patrón dentro de cualquier texto (`telegram_bot/bot/seguridad.py`). Lo que devuelve la API se le pasa al modelo marcado como datos no confiables.
- **Cuentas y archivos:** los conteos los calcula el código. Los Excel van directo al chat y el modelo no ve su contenido.
- **Historial:** se recuerdan los últimos 10 mensajes y los registros consultados, en memoria, durante 30 minutos. `/nuevo` los borra.
- **Trazas:** cada respuesta deja una traza en `bot_trazas` con modelo, tokens, rondas, herramientas usadas, tiempo y la calificación 👍/👎. **Nunca guarda el texto.**
- **Tope diario:** `BOT_TOKENS_DIA` limita el gasto de todo el bot y `BOT_TOKENS_PERSONA_DIA` el de cada persona. Se cuentan desde `bot_trazas`, en hora de México. El bot no arranca si alguno es 0 con la clave puesta.

**Banco de evaluación (`eval/`):** corre el agente real contra un backend y una BD desechables con datos inventados. Lleva 26 casos de lectura, permisos, peticiones de escritura e inyecciones plantadas en nombres y motivos.

```bash
.venv-telegram/bin/pip install -r eval/requirements.txt
docker-compose -p rh_eval -f eval/docker-compose.eval.yml up -d --build
.venv-telegram/bin/python eval/correr.py --repeticiones 3    # usa OPENAI_API_KEY de .env
docker-compose -p rh_eval -f eval/docker-compose.eval.yml down
```

La **compuerta dura** falla el banco (código de salida 1) en cuatro casos:
- algún dato personal o secreto de la semilla llega al modelo o a la respuesta;
- el agente escribe durante su turno, es decir, hace una petición que no es GET;
- el agente consulta una ruta fuera de la guía o sin permiso;
- el agente prepara un cambio fuera de la guía o sin permiso.

Las demás revisiones, sobre lo que dice la respuesta, cuentan para la calificación. Con `gpt-4o-mini` el resultado fue 119/120, con la compuerta limpia y unos 620 mil tokens por corrida. El caso que falló fue una guarda que rechazó un teléfono reescrito por el modelo y lo volvió a pedir.

**Seguridad de las rutas internas:**
- Las rutas `/api/telegram/interno/*` solo aceptan el secreto compartido. Si el secreto está vacío, responden 503.
- nginx les responde 404, así que únicamente se alcanzan por la red interna de Docker.
- El backend publica el puerto 8001 solo en `127.0.0.1`; desde fuera se entra por nginx (3001).

Pruebas del bot:

```bash
python -m venv .venv-telegram
.venv-telegram/bin/pip install -r telegram_bot/requirements.txt
cd telegram_bot && ../.venv-telegram/bin/python -m pytest
```

Las pruebas del bot usan una copia de la guía de la API (`telegram_bot/tests/guia.json`). Si cambias `backend/guia_api.py` o los permisos, regenérala; `test_guia.py` avisa cuando está desfasada:

```bash
cd backend && ../.venv/bin/python -c "import json, guia_api, utily; open('../telegram_bot/tests/guia.json','w').write(json.dumps(guia_api.construir_guia(utily.app), ensure_ascii=False, indent=1)+'\n')"
```

### Escaneo de documentos del registro

El botón de captura permite seleccionar imágenes JPG, PNG o WEBP, y archivos PDF de hasta 9 MB. El backend envía el documento a OpenAI para extraer únicamente los campos del tipo elegido, devuelve JSON y completa campos vacíos del formulario sin reemplazar los que ya se capturaron. El archivo no se conserva en la base de datos ni en el servidor. Configura `OPENAI_API_KEY` en `.env` o en el entorno disponible para Docker Compose; opcionalmente, `OPENAI_MODEL` selecciona el modelo (por defecto `gpt-4o-mini`). Confirma los datos extraídos antes de enviar el registro. Como son documentos con datos personales sensibles, habilita HTTPS en el sitio antes de usarlo con candidatos.

El historial interno del expediente de empleado se guarda en la tabla `incidencias_empleado`, con el tipo, fechas, duración, motivos, hora/opción de entrada o salida y los archivos adjuntos que correspondan. Los avisos de incidencia por WhatsApp no se envían.

## Cuentas y sesión del panel

Las cuentas del panel viven en el servidor (tabla `usuarios_acceso`, contraseñas con hash). `portalrh.html` inicia sesión con `/api/auth/login`, que devuelve una sesión firmada de 8 horas. La sesión viaja en una cookie `HttpOnly` y además como `Authorization: Bearer`, que añade `frontend/auth-fetch.js`. Cambiar la contraseña de una cuenta cierra sus sesiones abiertas.

- **Primera cuenta:** define `ADMIN_INICIAL_USUARIO` y `ADMIN_INICIAL_PASSWORD` en `.env`. Se crea sola al primer intento de login si la tabla está vacía. Después cambia la contraseña desde el panel.
- **Usuarios del sistema anterior:** antes las cuentas se guardaban en el navegador (localStorage). Al entrar a `iniciorh.html` como admin desde ese navegador aparece el botón «Importar al servidor», que las sube una sola vez y luego borra la copia local.
- **Permisos:** cada ruta del backend tiene un permiso en `backend/auth.py` (`PERMISOS_POR_ENDPOINT`): público, `reclutamiento` o `admin`. Una ruta nueva sin clasificar exige `admin`, y `test_auth.py` falla hasta que se clasifique.
- **Modo gradual:** con `AUTH_ESTRICTO=0` (por omisión) una petición sin sesión todavía pasa, y el backend registra un warning. Con `AUTH_ESTRICTO=1` responde 401. Actívalo cuando todos usen el nuevo login.
- **Bitácora:** las escrituras con sesión quedan en la tabla `bitacora` (usuario, canal, método, ruta y resultado; nunca el contenido).

## Limitaciones conocidas

- `api/datos-grafica` responde 500 cuando la base no tiene datos (la función termina sin devolver respuesta).
- `api/datos-grafica?periodo=mes` aplica `timezone('America/Mexico_City', fecha_registro)` a una columna `DATE`. La fecha se toma como medianoche UTC y queda en el día anterior, así que lo registrado el día 1 se cuenta en el mes anterior.
- `GET /api/reclutadores-nombres` es público (lo usa el formulario de registro) y devuelve el teléfono de cada reclutador.
- No hay HTTPS ni dominio configurado — nginx sirve solo HTTP en el puerto 3001. Si el servidor ya tiene un reverse proxy compartido (nginx/Traefik) para el otro proyecto, este stack puede integrarse detrás de él apuntando ese proxy a `localhost:3001`.
