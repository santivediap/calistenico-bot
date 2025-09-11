import os
from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Admin Role
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Base de datos (Neon Postgres)
DATABASE_URL = os.getenv("DATABASE_URL")

# XP y roles
XP_PER_MESSAGE = 5
XP_RUTINA_HECHA = 15
XP_ATTACHMENT = 20

LEVEL_ROLES_BASE = {
    10: "ESPARTANO ⚔️",
    20: "GUERRERO 🔥",
    30: "TITÁN 💪",
    40: "LEYENDA 🏆",
    50: "DIOS DE LA BARRA 🌌",
    60: "JAGUAR ÁGIL 🐆",
    70: "FÉNIX RENACIDO 🔥🦅",
    80: "CAMPEÓN DEL HIERRO 🏋️‍♂️",
    90: "BESTIA INDOMABLE 🐺",
    100: "MAESTRO ABSOLUTO 👑",
    110: "GLADIADOR DE ACERO 🛡️",
    120: "HÉROE DE LA COMUNIDAD 🌟",
    130: "CONQUISTADOR DE LÍMITES 🚀",
    140: "ATLETA DE ÉLITE 🥇",
    150: "FUERZA DE LA NATURALEZA 🌪️",
    160: "MENTOR INSPIRADOR ✨",
    170: "ICONO DEL FITNESS 💥",
    180: "CAMPEÓN CELESTIAL 💫",
    190: "PODER ENCARNADO ⚡",
    200: "LEYENDA SUPREMA 🔱"
}

LEVEL_ROLES_MODIFIERS = {
    5: "CALISTÉNICO",
    1: "DISCIPLINADO",
    6: "INCANSABLE"
}

IA_SYSTEM_PROMPT = """
Eres CALISTENICO, un entrenador de calistenia experto que vive en un servidor de Discord.
Tu personalidad es motivadora, amigable y siempre positiva.
Tu misión es ayudar a los miembros del servidor con sus dudas sobre entrenamiento de calistenia, fitness, nutrición, disciplina y mentalidad.
Usa un lenguaje cercano, da consejos prácticos y anima a los usuarios a superarse.

Reglas importantes:
1. Si un usuario te pregunta sobre algo que no tiene ninguna relación con la calistenia, el fitness, la salud o el desarrollo personal (por ejemplo, política, videojuegos, historia, etc.), debes declinar la respuesta de forma amable.
2. Tu respuesta debe ser algo como: "¡Esa es una pregunta interesante! Pero mi especialidad son las dominadas y las flexiones, no los agujeros negros. ¿Puedo ayudarte con algo relacionado con tu entrenamiento?".
3. Mantén tus respuestas concisas y directas, ideales para un chat de Discord.
"""
