from pathlib import Path
import telebot
from dotenv import dotenv_values

# Carga directa de las variables mediante lectura explícita del archivo
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

config = dotenv_values(ENV_PATH)
token = config.get("TELEGRAM_BOT_TOKEN")

if not token:
    raise RuntimeError(
        f"No se pudo extraer TELEGRAM_BOT_TOKEN del archivo en: {ENV_PATH}"
    )

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