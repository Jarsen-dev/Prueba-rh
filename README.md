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
| backend   | 8001        | API Flask (gunicorn) |
| chatbot   | 5002        | Webhook de WhatsApp, respuestas automáticas y contactos de Reclutamiento |
| postgres  | (interno)   | sin puerto publicado; solo el backend lo alcanza por la red interna de Docker |

Estos puertos (3001/8001) se eligieron para no chocar con el otro proyecto que ya corre en el mismo servidor (`produccion_frontend` en 3000, `produccion_backend` en 8000, `produccion_pgadmin` en 5050 y su Postgres publicado en el host 5433). Este proyecto tiene su propio contenedor y volumen de Postgres, aislado del de ese otro proyecto, y por eso puede quedar sin puerto publicado en el host — evita justamente chocar con el 5433 que ya usa el otro proyecto.

El esquema de las tablas (`registro`, `encuesta_reclutamiento`, `incidencias_empleado`) se crea automáticamente la primera vez que el volumen de Postgres está vacío, vía `db/init.sql`. Cambios de esquema posteriores requieren un `ALTER TABLE` manual — no hay herramienta de migraciones.

### Chatbot de WhatsApp

El chatbot conserva sus respuestas automáticas para personal activo y su conexión con los teléfonos registrados en Reclutamiento. Configura `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` y `WHATSAPP_VERIFY_TOKEN` con los datos de Meta WhatsApp Cloud API. Los avisos de incidencias por WhatsApp y la notificación diferida a contactos de emergencia ya no forman parte del sistema.

El historial interno del expediente de empleado se guarda en la tabla `incidencias_empleado`, con el tipo, fechas, duración, motivos, hora/opción de entrada o salida y los archivos adjuntos que correspondan. Los avisos de incidencia por WhatsApp no se envían.

## Limitaciones conocidas

- El login de `portalrh.html` es una validación fija en el cliente (`admin` / `123`), sin backend ni sesión real — cualquiera puede saltarlo navegando directo a `iniciorh.html`. No se corrigió en esta reorganización; queda pendiente.
- `main.html` y `cheong-woon.html` llaman a `/api/datos-grafica` y `/api/baja-empleado` respectivamente, pero esos endpoints todavía no están implementados en el backend.
- No hay HTTPS ni dominio configurado — nginx sirve solo HTTP en el puerto 3001. Si el servidor ya tiene un reverse proxy compartido (nginx/Traefik) para el otro proyecto, este stack puede integrarse detrás de él apuntando ese proxy a `localhost:3001`.
