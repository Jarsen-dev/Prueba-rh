import unittest
from unittest.mock import Mock, patch

import bot


class ChatbotResponseTests(unittest.TestCase):
  def setUp(self):
    self.client = bot.app.test_client()

  def tearDown(self):
    bot.SESSIONS.clear()

  def test_automatic_replies_keep_the_employee_number_prompt(self):
    sender = "5512345678"
    bot.SESSIONS[sender] = {"paso": "menu_principal"}
    enviar = Mock(return_value=({"status": "sent"}, 200))

    with patch.object(bot, "enviar_respuesta", enviar):
      response = self.client.post(
        "/webhook-whatsapp",
        json={"sender": sender, "text": "1"},
      )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(bot.SESSIONS[sender]["paso"], "esperando_numero_empleado")
    self.assertEqual(enviar.call_args.args[1], "Ingresa tu número de empleado:")

  def test_recruitment_contacts_remain_available_from_the_menu(self):
    sender = "5512345678"
    bot.SESSIONS[sender] = {"paso": "menu_principal"}
    enviar = Mock(return_value=({"status": "sent"}, 200))

    with (
      patch.object(bot, "obtener_contactos_reclutamiento", return_value=["8112345678", "8187654321"]),
      patch.object(bot, "enviar_respuesta", enviar),
    ):
      response = self.client.post(
        "/webhook-whatsapp",
        json={"sender": sender, "text": "2"},
      )

    self.assertEqual(response.status_code, 200)
    self.assertIn("8112345678", enviar.call_args.args[1])
    self.assertIn("8187654321", enviar.call_args.args[1])

  def test_incident_notification_endpoints_are_removed(self):
    self.assertEqual(
      self.client.post("/api/incidencias", json={"tipo": "suspension"}).status_code,
      404,
    )
    self.assertEqual(
      self.client.post("/api/internal/incidencias", json={"tipo": "suspension"}).status_code,
      404,
    )


if __name__ == "__main__":
  unittest.main()
