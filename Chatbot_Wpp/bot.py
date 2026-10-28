import os
import re
from logging import getLogger

import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

app = Flask(__name__)
logger = getLogger(__name__)
SESSIONS = {}
WHATSAPP_API_TIMEOUT = 15

load_dotenv(override=True);


def conectar_db():
  host = os.getenv("POSTGRES_HOST", "postgres")
  if host == "postgres" and not os.path.exists("/.dockerenv"):
    host = "localhost"
  return psycopg2.connect(
    host=host,
    port=os.getenv("POSTGRES_PORT", "5432"),
    database=os.getenv("POSTGRES_DB", "rh_system"),
    user=os.getenv("POSTGRES_USER", "rh_app"),
    password=os.getenv("POSTGRES_PASSWORD", "S1s73m4s!"),
    connect_timeout=5,
    options="-c lc_messages=C",
  )


def extraer_mensaje(data):
    """Accept the existing normalized payload and Meta Cloud API webhooks."""
    sender = data.get("sender")
    text = data.get("text", "")

    if sender and text:
      return str(sender), str(text).strip()

    for entry in data.get("entry", []):
      for change in entry.get("changes", []):
        value = change.get("value", {})
        for message in value.get("messages", []):
          message_text = message.get("text", {}).get("body")
          if message_text:
            return str(message.get("from", "")), message_text.strip()
    return None, None


def asegurar_tabla_credenciales(cur):
  cur.execute(
    """
    CREATE TABLE IF NOT EXISTS chatbot_credenciales (
      registro_id INTEGER PRIMARY KEY REFERENCES registro(id) ON DELETE CASCADE,
      contrasena TEXT NOT NULL,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
  )


def buscar_colaborador(numero_empleado):
  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_credenciales(cur)
      cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = 'registro'
            AND column_name = 'estatus'
        )
        """
      )
      tiene_estatus = cur.fetchone()[0]
      filtro_estatus = (
        "AND COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') = 'ACTIVO'"
        if tiene_estatus
        else ""
      )
      cur.execute(
        f"""
        SELECT r.id, r.nombre_completo, i.no_empleado, i.puesto, i.area, i.turno,
               c.contrasena
        FROM registro AS r
        JOIN ingresos_puesto AS i ON i.registro_id = r.id
        LEFT JOIN chatbot_credenciales AS c ON c.registro_id = r.id
        WHERE i.no_empleado::text = %s
          {filtro_estatus}
        LIMIT 1
        """,
        (numero_empleado,),
      )
      return cur.fetchone()


def guardar_contrasena_colaborador(registro_id, contrasena):
  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_credenciales(cur)
      cur.execute(
        """
        INSERT INTO chatbot_credenciales (registro_id, contrasena)
        VALUES (%s, %s)
        ON CONFLICT (registro_id) DO UPDATE
        SET contrasena = EXCLUDED.contrasena,
            updated_at = CURRENT_TIMESTAMP
        """,
        (registro_id, contrasena),
      )


def obtener_contactos_reclutamiento():
  with conectar_db() as conn:
    with conn.cursor() as cur:
      cur.execute(
        """
        SELECT TRIM(telefono)
        FROM personal_reclutamiento
        WHERE telefono IS NOT NULL
          AND NULLIF(TRIM(telefono), '') IS NOT NULL
        ORDER BY id ASC
        """
      )
      return [row[0] for row in cur.fetchall() if row[0]]


def enviar_respuesta(destinatario, texto):
  phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
  access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()

  if not phone_number_id or not access_token:
    print(
      f"[WHATSAPP SIMULADO] Destino: {destinatario}\n"
      f"[WHATSAPP SIMULADO] Mensaje:\n{texto}",
      flush=True,
    )
    return jsonify({"status": "simulated", "success": True, "message": texto}), 200

  recipient = re.sub(r"\D", "", destinatario)
  api_version = os.getenv("WHATSAPP_API_VERSION", "v23.0")
  url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
  try:
    response = requests.post(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      json={
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": texto},
      },
      timeout=WHATSAPP_API_TIMEOUT,
    )
    response.raise_for_status()
  except requests.RequestException:
    if "response" in locals():
      print(f"RESPUESTA META: {response.text}", flush=True)
    logger.exception("No se pudo enviar el mensaje por WhatsApp Cloud API")
    return jsonify({"status": "send_error"}), 502

  return jsonify({"status": "sent"}), 200


def bienvenida(remitente):
  SESSIONS[remitente] = {"paso": "menu_principal"}
  return enviar_respuesta(
    remitente,
    "¡Hola! Bienvenido al asistente de Cheong Woon Mexico.\n\n"
    "Selecciona una de estas opciones:\n\n"
    "1. Personal Activo\n"
    "2. Quiero ser parte del Equipo\n\n"
    "Envía el número de la opción deseada.",
  )


@app.route("/webhook-whatsapp", methods=["GET", "POST"])
def webhook_whatsapp():
  if request.method == "GET":
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
    if not verify_token or request.args.get("hub.verify_token") != verify_token:
      return "Verificación no válida", 403
    return request.args.get("hub.challenge", ""), 200

  data = request.get_json(silent=True) or {}
  remitente, mensaje = extraer_mensaje(data)
  if not remitente or mensaje is None:
    return jsonify({"status": "ignored"}), 200

  mensaje = mensaje.strip()
  mensaje_normalizado = mensaje.casefold()

  if remitente not in SESSIONS or mensaje_normalizado in {"hola", "menu", "menú", "inicio", "0"}:
    return bienvenida(remitente)

  estado = SESSIONS[remitente]

  if estado["paso"] == "menu_principal":
    if mensaje == "1":
      estado["paso"] = "esperando_numero_empleado"
      return enviar_respuesta(
        remitente,
        "Ingresa tu número de empleado:",
      )
    if mensaje == "2":
      try:
        contactos = obtener_contactos_reclutamiento()
      except psycopg2.Error:
        logger.exception("No se pudieron consultar contactos de reclutamiento")
        contactos = []
      texto_respuesta = "Para recibir orientación, ponte en contacto con nuestro equipo de Reclutamiento."
      if contactos:
        texto_contactos = "\n".join(
          f"Contacto {indice}: {telefono}"
          for indice, telefono in enumerate(contactos, start=1)
        )
        texto_respuesta += f"\n\n{texto_contactos}"
      texto_respuesta += "\n\n¡Gracias por tu interés en formar parte de Cheong Woon México! 😊"
      SESSIONS.pop(remitente, None)
      return enviar_respuesta(remitente, texto_respuesta)
    return enviar_respuesta(remitente, "Opción no válida. Envía 1 o 2.")

  if estado["paso"] == "esperando_numero_empleado":
    numero_empleado = mensaje.strip()
    if not numero_empleado:
      return enviar_respuesta(remitente, "Ingresa un número de empleado válido.")
    try:
      colaborador = buscar_colaborador(numero_empleado)
    except psycopg2.Error:
      logger.exception("Error al validar los datos del colaborador")
      return enviar_respuesta(
        remitente,
        "No pudimos validar tus datos en este momento. Intenta de nuevo más tarde.",
      )

    if not colaborador:
      SESSIONS[remitente] = {"paso": "menu_principal"}
      return enviar_respuesta(
        remitente,
        "No encontramos un empleado activo con ese número.\n\n"
        "Menú principal:\n"
        "1. Personal Activo\n"
        "2. Quiero ser parte del Equipo\n\n"
        "Envía el número de la opción deseada.",
      )

    estado.update({
      "registro_id": colaborador[0],
      "nombre_real": colaborador[1],
      "no_empleado": colaborador[2],
      "puesto": colaborador[3],
      "area": colaborador[4],
      "turno": colaborador[5],
    })
    if not colaborador[6]:
      estado["paso"] = "creando_contrasena"
      return enviar_respuesta(
        remitente,
        f"Acceso confirmado. Bienvenido, {colaborador[1]}.\n\n"
        "Crea una contraseña personal de al menos 8 caracteres:",
      )
    estado["contrasena_esperada"] = colaborador[6]
    estado["paso"] = "esperando_contrasena"
    return enviar_respuesta(remitente, "Ingresa tu contraseña personal para continuar.")

  if estado["paso"] == "creando_contrasena":
    if len(mensaje) < 8:
      return enviar_respuesta(remitente, "La contraseña debe tener al menos 8 caracteres. Intenta crear otra:")
    try:
      guardar_contrasena_colaborador(estado["registro_id"], mensaje)
    except psycopg2.Error:
      logger.exception("No se pudo guardar la contraseña del colaborador")
      return enviar_respuesta(remitente, "No pudimos guardar tu contraseña. Intenta de nuevo más tarde.")
    return enviar_respuesta(
      remitente,
      "Tu contraseña quedó guardada.\n\n" + mensaje_menu_colaborador(estado),
    )

  if estado["paso"] == "esperando_contrasena":
    if mensaje != estado["contrasena_esperada"]:
      return enviar_respuesta(remitente, "La contraseña no coincide. Inténtalo de nuevo o envía 0 para volver al menú principal.")
    return enviar_respuesta(remitente, mensaje_menu_colaborador(estado))

  if estado["paso"] == "menu_colaborador":
    respuestas = {
      "1": (
        f"Información de asignación\n\nEmpleado: {estado['nombre_real']}\n"
        f"Área: {estado['area'] or 'No registrada'}\n"
        f"Turno: {estado['turno'] or 'No registrado'}\n\n"
        "Para consultar la ruta exacta, acude con tu supervisor de turno.\n\n"
        "Envía 0 para volver."
      ),
      "2": (
        "Vacaciones\nPara consultar tus días disponibles, acude a la"
        " ventanilla de Recursos Humanos.\n\nEnvía 0 para volver."
      ),
      "3": (
        "Aviso de privacidad\nTus datos personales se manejan de acuerdo"
        " con las políticas internas de Cheong Woon Mexico.\n\n"
        "Envía 0 para volver."
      ),
    }
    if mensaje in respuestas:
      return enviar_respuesta(remitente, respuestas[mensaje])
    return enviar_respuesta(remitente, "Opción no válida. Envía un número del 1 al 3, o 0.")

  return bienvenida(remitente)


def mensaje_menu_colaborador(estado):
  estado["paso"] = "menu_colaborador"
  return (
    f"Acceso confirmado. Bienvenido, {estado['nombre_real']}.\n\n"
    "Selecciona una opción:\n"
    "1. Ruta asignada\n"
    "2. Vacaciones\n"
    "3. Aviso de privacidad\n\n"
    "Envía 0 para volver al menú principal."
  )


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))