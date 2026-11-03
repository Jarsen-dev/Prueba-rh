import os
from pathlib import Path
import telebot
from dotenv import load_dotenv

# Ruta absoluta al .env en la raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
  raise RuntimeError("Falta configurar TELEGRAM_BOT_TOKEN en el archivo .env")

bot = telebot.TeleBot(token)


@bot.message_handler(commands=["start"])
def handle_start(message):
  bot.reply_to(
      message,
      "¡Hola! Soy el bot de Telegram. Envíame un mensaje y te lo repetiré.",
  )


@bot.message_handler(content_types=["text"])
def echo_text(message):
  bot.reply_to(message, message.text)


if __name__ == "__main__":
  print("Bot encendido y listo en Telegram...")
  bot.infinity_polling()