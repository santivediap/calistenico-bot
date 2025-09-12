import discord
from discord.ext import commands, tasks
import json
import os
import openai
from datetime import datetime, timedelta, timezone, time
import config
from config import DISCORD_BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL, ADMIN_ROLE_ID # <--- CAMBIO
import asyncpg # <--- CAMBIO
from keep_alive import keep_alive

import random
import gspread
from google.oauth2.service_account import Credentials

keep_alive()

def load_data(file_path, default_data={}):
    """
    Carga datos desde un archivo JSON.
    Si el archivo no existe, está vacío o corrupto, devuelve los datos por defecto.
    """
    # Primero, revisa si el archivo existe en la carpeta.
    if os.path.exists(file_path):
        # Si existe, lo abre en modo lectura ('r') con codificación UTF-8 (importante para emojis y acentos).
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                # Intenta leer el contenido y convertirlo de JSON a un diccionario de Python.
                return json.load(f)
            except json.JSONDecodeError:
                # Si el archivo está vacío o mal formateado, no dará error,
                # sino que devolverá los datos por defecto para evitar que el bot se caiga.
                return default_data
    else:
        # Si el archivo no existe, simplemente devuelve los datos por defecto.
        return default_data

def save_data(file_path, data):
    """
    Guarda los datos (un diccionario de Python) en un archivo JSON.
    Sobrescribe el archivo si ya existe.
    """
    # Abre el archivo en modo escritura ('w'), lo que significa que creará el archivo si no existe
    # o borrará su contenido si ya existe para escribir los nuevos datos.
    with open(file_path, 'w', encoding='utf-8') as f:
        # Convierte el diccionario de Python a formato JSON y lo escribe en el archivo.
        # indent=4 es para que el archivo se guarde de forma bonita y ordenada,
        # fácil de leer para un humano.
        json.dump(data, f, indent=4)

# --- CONFIGURACIÓN DEL BOT ---
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

GOOGLE_SHEET_NAME = "Rutinas Academia Bot"  # Asegúrate de que este nombre sea exacto.
ROUTINE_CHANNEL_NAME = "rutina-semanal"
USED_ROUTINES_FILE = "used_routines.json" # ????
# Define la hora de publicación. Ejemplo: 8:00 AM en horario de España (CET/CEST es UTC+2)
TIME_TO_POST = time(hour=8, minute=0, tzinfo=timezone(timedelta(hours=2))) 
# Inicializa el cliente de Google Sheets
gsheet_client = None

bot = commands.Bot(command_prefix="!", intents=INTENTS, help_command=None)

# --- VARIABLES GLOBALES Y CONEXIÓN A DB --- # <--- CAMBIO
db_pool = None # <--- CAMBIO: La piscina de conexiones a la base de datos
openai_client = None

# --- PALETA DE COLORES (sin cambios) ---
COLOR_PALETTE = [
    discord.Color.blue(), discord.Color.green(), discord.Color.orange(), discord.Color.purple(),
    discord.Color.red(), discord.Color.gold(), discord.Color.teal(), discord.Color.magenta(),
    discord.Color.dark_green(), discord.Color.dark_blue(), discord.Color.from_rgb(230, 60, 60),
    discord.Color.from_rgb(60, 180, 230)
]

# --- FUNCIONES AUXILIARES (ahora con funciones de DB) --- # <--- CAMBIO

# Eliminamos load_data y save_data, ya no son necesarios.

async def get_user_data(user_id): # <--- CAMBIO: Nueva función para obtener datos de un usuario
    async with db_pool.acquire() as connection:
        return await connection.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def upsert_user_xp(user_id, xp_gain, is_rutina=False, is_attachment=False):
    today = datetime.now(timezone.utc).date()
    now_ts = datetime.now(timezone.utc)
    
    # Primero, intentamos obtener los datos del usuario.
    user_data = await get_user_data(user_id)
    
    if not user_data:
        # Si no hay datos, es un usuario nuevo. Hacemos un INSERT.
        query_insert = """
            INSERT INTO users (user_id, xp, weekly_xp, last_message_timestamp, last_rutina_date, last_attachment_date, attachments_today)
            VALUES ($1, $2, $2, $3, $4, $5, $6)
            RETURNING xp, level;
        """
        async with db_pool.acquire() as connection:
            return await connection.fetchrow(
                query_insert, user_id, xp_gain, now_ts, 
                today if is_rutina else None, 
                today if is_attachment else None, 
                1 if is_attachment else 0
            )
    else:
        # Si hay datos, es un usuario existente. Hacemos un UPDATE.
        update_clauses = [
            "xp = users.xp + $2",
            "weekly_xp = users.weekly_xp + $2",
        ]
        params = [user_id, xp_gain]
        param_idx = 3

        if is_rutina:
            update_clauses.append(f"last_rutina_date = ${param_idx}")
            params.append(today)
            param_idx += 1
        
        if is_attachment:
            update_clauses.append(f"last_attachment_date = ${param_idx}")
            params.append(today)
            param_idx += 1
            update_clauses.append("attachments_today = users.attachments_today + 1")

        update_clauses.append(f"last_message_timestamp = ${param_idx}")
        params.append(now_ts)

        update_string = ",\n            ".join(update_clauses)
        query_update = f"UPDATE users SET {update_string} WHERE user_id = $1 RETURNING xp, level;"
        
        async with db_pool.acquire() as connection:
            return await connection.fetchrow(query_update, *params)

def get_level(xp):
    return int(xp / 150) + 1

def get_role_name_for_level(level):
    if level < 10: return "Rookie 🐣"
    base_level = (level // 10) * 10
    base_title = config.LEVEL_ROLES_BASE.get(base_level, f"Nivel {base_level}")
    if level % 10 == 0: return base_title
    if level % 5 == 0: return f"{base_title} CALISTÉNICO"
    return f"{base_title} DISCIPLINADO"

# --- FUNCIÓN DE ASIGNAR ROLES (sin cambios lógicos) ---
async def assign_level_role(member, new_level):
    guild = member.guild
    new_role_name = get_role_name_for_level(new_level)
    base_level_tier = (new_level // 10)
    color_index = (base_level_tier - 1) % len(COLOR_PALETTE)
    selected_color = COLOR_PALETTE[color_index]
    roles_de_nivel_base = list(config.LEVEL_ROLES_BASE.values()) + ["Rookie 🐣"]
    roles_to_remove = [role for role in member.roles if any(base in role.name for base in roles_de_nivel_base) and role.name != new_role_name]

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Actualización de rol de nivel.")

    new_role = discord.utils.get(guild.roles, name=new_role_name)
    if not new_role:
        new_role = await guild.create_role(name=new_role_name, color=selected_color, mentionable=False)
    elif new_role.color == discord.Color.default():
        await new_role.edit(color=selected_color)
    
    if new_role not in member.roles:
        await member.add_roles(new_role, reason="Subida de nivel.")

# --- EVENTOS PRINCIPALES ---
@bot.event
async def on_ready():
    global db_pool, openai_client  # <--- CAMBIO
    global gsheet_client
    try: # <--- CAMBIO: Conectamos a la base de datos
        db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)
        print("✅ Conectado a la base de datos PostgreSQL.")
    except Exception as e:
        print(f"❌ Error al conectar a la base de datos: {e}")
        return
    
    try:
        # 1. Construimos el diccionario de credenciales leyendo las variables de entorno.
        gcp_credentials_dict = {
            "type": "service_account",
            "project_id": config.GCP_PROJECT_ID,
            "private_key_id": config.GCP_PRIVATE_KEY_ID,
            # Este .replace() es clave para que los saltos de línea de la private_key funcionen bien.
            "private_key": config.GCP_PRIVATE_KEY.replace('\n', '\n'),
            "client_email": config.GCP_CLIENT_EMAIL,
            "client_id": config.GCP_CLIENT_ID,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": config.GCP_CLIENT_X509_CERT_URL
        }

        # 2. Verificamos que las variables esenciales no estén vacías.
        if not all([config.GCP_PROJECT_ID, config.GCP_PRIVATE_KEY, config.GCP_CLIENT_EMAIL]):
            print("⚠️ AVISO: Faltan variables de entorno de GCP. La función de rutinas diarias no funcionará.")
            gsheet_client = None
        else:
            # 3. Autorizamos usando la información del diccionario, no un archivo.
            scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_info(gcp_credentials_dict, scopes=scopes)
            gsheet_client = gspread.authorize(creds)
            print("✅ Conexión con Google Sheets establecida desde variables de entorno.")

    except Exception as e:
        print(f"❌ ERROR al conectar con Google Sheets desde variables de entorno: {e}")
        gsheet_client = None
    # --- FIN DEL BLOQUE DE CONEXIÓN ---
    
    check_inactivity.start()
    ranking_semanal.start()
    recordatorio_asesorias.start()
    revisar_clases.start()
    post_daily_routine.start() # <-- AÑADE ESTA LÍNEA para iniciar la nueva tarea
    
    print(f"✅ Bot conectado como {bot.user}")
    print(f"   - Servidores: {[guild.name for guild in bot.guilds]}")

# --- PEGA ESTA NUEVA TAREA PROGRAMADA JUNTO A LAS OTRAS TAREAS ---
@tasks.loop(time=TIME_TO_POST)
async def post_daily_routine():
    await bot.wait_until_ready()

    # # Comprobación del día de la semana
    # hoy = datetime.now(timezone.utc)
    # # En Python, Lunes es 0, Martes es 1, ..., Sábado es 5 y Domingo es 6.
    # if hoy.weekday() in [5, 6]: # Si es Sábado o Domingo
    #     print("Hoy es fin de semana, no se publica rutina.")
    #     return # La función se detiene y no hace nada más.

    # Asume que el bot está en un solo servidor
    guild = bot.guilds[0] if bot.guilds else None
    if not guild or not gsheet_client:
        print("Bot no está en un servidor o no hay conexión con Google Sheets. Saltando rutina.")
        return

    routine_channel = discord.utils.get(guild.text_channels, name=ROUTINE_CHANNEL_NAME)
    if not routine_channel:
        print(f"!!! AVISO: No se encontró el canal #{ROUTINE_CHANNEL_NAME}.")
        return

    try:
        spreadsheet = gsheet_client.open(GOOGLE_SHEET_NAME)
        worksheet = spreadsheet.sheet1
        all_routines = worksheet.get_all_records()
    except Exception as e:
        print(f"❌ ERROR al leer el Google Sheet: {e}")
        return

    if not all_routines:
        return

    # Lógica para no repetir rutinas en la misma semana
    used_data = load_data(USED_ROUTINES_FILE, {"last_reset_week": -1, "used_indices": []})
    current_week = datetime.now(timezone.utc).isocalendar()[1]
    if used_data["last_reset_week"] != current_week:
        used_data["last_reset_week"] = current_week
        used_data["used_indices"] = []

    available_routines = [(i, r) for i, r in enumerate(all_routines) if i not in used_data["used_indices"]]
    if not available_routines:
        used_data["used_indices"] = []
        available_routines = list(enumerate(all_routines))
        await routine_channel.send("¡Hemos completado todas las rutinas de la semana! Empezamos de nuevo el ciclo. 🔥")

    chosen_index, chosen_routine = random.choice(available_routines)
    used_data["used_indices"].append(chosen_index)
    save_data(USED_ROUTINES_FILE, used_data)

    # --- Bloque de mejora de la presentación con IA ---
    raw_title = chosen_routine.get('titulo_rutina', 'Rutina del Día')
    raw_description = chosen_routine.get('descripcion_rutina', 'No hay descripción.')
    enhanced_description = raw_description

    if openai_client:
        try:
            enhancer_prompt = f"""
            Toma la siguiente rutina y mejórala para un anuncio de Discord.
            Reglas: NO cambies los ejercicios, series o repeticiones. SÓLO mejora la presentación. Usa emojis (💪,🔥), formato de Discord como **negritas** y añade una intro y cierre motivadores.
            
            Título: {raw_title}
            Descripción: {raw_description}
            """
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Eres un entrenador de fitness que formatea rutinas para anuncios de Discord de forma visual y motivadora."},
                    {"role": "user", "content": enhancer_prompt}
                ],
                max_tokens=1024,
                temperature=0.7
            )
            enhanced_description = response.choices[0].message.content
        except Exception as e:
            print(f"!!! ERROR al mejorar la rutina con IA: {e}. Publicando sin formato.")
    
    # Publicar la rutina mejorada en Discord
    embed = discord.Embed(
        title=f"🗓️ {raw_title}",
        description=enhanced_description,
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="¡A entrenar! No olvides escribir 'RUTINA HECHA' al terminar.")
    await routine_channel.send(f"¡Buenos días, equipo! @everyone aquí tenéis el entrenamiento de hoy:", embed=embed)


# on_member_join (sin cambios)
@bot.event
async def on_member_join(member):
    welcome_channel = discord.utils.get(member.guild.text_channels, name="bienvenida")
    if welcome_channel:
        await welcome_channel.send(
            f"👋 Bienvenido {member.mention} a la Academia de Calistenia 🏋️ \n"
            "Aquí entrenamos juntos, compartimos progresos y nos respetamos siempre 💪🔥 \n"
            "📌 No olvides leer `#reglas` \n"
            "📸 Comparte tus avances en `#progresos` y motiva a la comunidad \n"
            "¡Prepárate para crecer con nosotros! \n"
        )

@bot.event
async def on_message(message):
    if message.author.bot or not db_pool: return # <--- CAMBIO: Verificamos que haya conexión a la DB

    user_id = message.author.id
    today_str = datetime.now(timezone.utc).date()

    user_data = await get_user_data(user_id) # <--- CAMBIO: Obtenemos datos del usuario desde la DB

    old_level = user_data['level'] if user_data else 1
    gained_xp = config.XP_PER_MESSAGE
    is_rutina, is_attachment = False, False

    if "RUTINA HECHA!" in message.content.upper():
        if not user_data or user_data.get("last_rutina_date") != today_str:
            gained_xp += config.XP_RUTINA_HECHA
            is_rutina = True

    if message.attachments:
        attachments_today = user_data.get("attachments_today", 0) if user_data else 0
        last_attachment_date = user_data.get("last_attachment_date") if user_data else None
        
        # Si la última fecha es diferente a hoy, reseteamos el contador
        if last_attachment_date != today_str:
            attachments_today = 0
            # Necesitamos actualizar esto en la DB incluso si no ganan XP
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET attachments_today = 0, last_attachment_date = $2 WHERE user_id = $1", user_id, today_str)

        if attachments_today < 4:
            gained_xp += config.XP_ATTACHMENT
            is_attachment = True

    # <--- CAMBIO: Actualizamos todo en una sola operación de DB
    updated_data = await upsert_user_xp(user_id, gained_xp, is_rutina, is_attachment)
    
    new_level = get_level(updated_data['xp'])

    if new_level > old_level:
        async with db_pool.acquire() as conn: # <--- CAMBIO: Actualizamos el nivel en la DB
            await conn.execute("UPDATE users SET level = $1 WHERE user_id = $2", new_level, user_id)
        
        await assign_level_role(message.author, new_level)
        
        level_up_channel = discord.utils.get(message.guild.text_channels, name="level-up")
        if level_up_channel:
            try:
                await level_up_channel.send(f"🎉 ¡Enhorabuena {message.author.mention}, has subido a **Nivel {new_level}**! Tu nuevo rol es **{get_role_name_for_level(new_level)}**.")
            except Exception as e:
                print(f"!!! ERROR al anunciar en #level-up: {e}")

    await bot.process_commands(message)

# --- COMANDOS PARA MIEMBROS ---
@bot.command(name="nivel")
async def nivel(ctx):
    user_data = await get_user_data(ctx.author.id) # <--- CAMBIO
    if user_data:
        await ctx.send(f"📊 {ctx.author.mention}, eres **Nivel {user_data['level']}** con **{user_data['xp']}** XP.")
    else:
        await ctx.send("Aún no tienes XP. ¡Empieza a participar!")

# --- PEGA ESTE NUEVO COMANDO DE TEST JUNTO A LOS OTROS COMANDOS DE ADMIN ---
@bot.command(name="test_rutina")
@commands.has_role(ADMIN_ROLE_ID)
async def test_rutina(ctx):
    await ctx.send("⚙️ Forzando la publicación de una rutina de prueba...")
    await post_daily_routine()

# calistenico (sin cambios)
@bot.command(name="calistenico")
async def calistenico(ctx, *, prompt: str):
    if not openai_client: return await ctx.send("Lo siento, la función de IA no está configurada por el administrador.")
    try:
        async with ctx.typing():
            response = await openai_client.chat.completions.create(model="gpt-4o", messages=[{"role": "system", "content": config.IA_SYSTEM_PROMPT}, {"role": "user", "content": prompt}], max_tokens=600, temperature=0.7)
            await ctx.reply(response.choices[0].message.content, mention_author=True)
    except Exception as e:
        print(f"Error con API de OpenAI: {e}")
        await ctx.send("🤯 Uff, mi cerebro tuvo un cortocircuito. Inténtalo de nuevo en un momento.")

@bot.command(name="clases")
async def clases(ctx):
    async with db_pool.acquire() as conn: # <--- CAMBIO
        clases_records = await conn.fetch("SELECT tipo, fecha_hora FROM clases WHERE fecha_hora >= NOW() ORDER BY fecha_hora ASC")
    
    if not clases_records:
        return await ctx.send("📅 No hay clases programadas.")
        
    msg = "📅 **Clases Programadas:**\n"
    clases_gratis = [r for r in clases_records if r['tipo'] == 'gratis']
    clases_premium = [r for r in clases_records if r['tipo'] == 'premium']

    if clases_gratis:
        msg += "\n**Gratuitas:**\n" + "\n".join([f"  - {r['fecha_hora'].strftime('%d/%m/%Y a las %H:%M')} UTC" for r in clases_gratis])
    if clases_premium:
        msg += "\n**Premium:**\n" + "\n".join([f"  - {r['fecha_hora'].strftime('%d/%m/%Y a las %H:%M')} UTC" for r in clases_premium])
    await ctx.send(msg)

# --- COMANDOS DE AYUDA (sin cambios) ---
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(title="🤖 Comandos de la Academia", description="Aquí tienes los comandos que puedes usar:", color=discord.Color.blue())
    embed.add_field(name="`!nivel`", value="Muestra tu nivel y XP actual.", inline=False)
    embed.add_field(name="`!calistenico [pregunta]`", value="Habla con el entrenador IA para resolver tus dudas.", inline=False)
    embed.add_field(name="`!clases`", value="Muestra las próximas clases programadas.", inline=False)
    embed.set_footer(text="Gana XP participando, compartiendo tu progreso y ayudando a otros.")
    await ctx.send(embed=embed)

@bot.command(name="adminhelp")
@commands.has_role(ADMIN_ROLE_ID)
async def adminhelp_command(ctx):
    embed = discord.Embed(title="👑 Comandos de Administración", description="Comandos para gestionar el servidor:", color=discord.Color.gold())
    embed.add_field(name="`!setup`", value="Crea/repara la estructura de canales del servidor.", inline=False)
    embed.add_field(name="`!clase_gratis [AAAA-MM-DD] [HH:MM]`", value="Programa una clase gratuita.", inline=False)
    embed.add_field(name="`!clase_premium [AAAA-MM-DD] [HH:MM]`", value="Programa una clase premium.", inline=False)
    embed.add_field(name="`!test_xp @usuario [cantidad]`", value="Añade XP a un usuario y fuerza un ranking de prueba.", inline=False)
    await ctx.send(embed=embed)

# --- COMANDOS DE ADMINISTRACIÓN ---
# setup (sin cambios)
@bot.command(name="setup")
@commands.has_role(ADMIN_ROLE_ID)
async def setup(ctx):
    await ctx.send("Configurando y verificando canales del servidor...")
    canales = { "📜 INFORMACIÓN": ["bienvenida", "reglas", "anuncios", "level-up", "ranking"], "🏋️ ENTRENAMIENTO": ["rutina-semanal", "videos-explicativos", "progresos"], "💬 COMUNIDAD": ["charla-general", "presentaciones", "💬-banquito"], "💎 PREMIUM": ["clases-grupales", "asesorias-personales", "clases-exclusivas"], "🎤 ZONAS DE VOZ": ["🎤-parque-de-barras"]}
    for cat_name, chan_list in canales.items():
        cat = discord.utils.get(ctx.guild.categories, name=cat_name) or await ctx.guild.create_category(cat_name)
        for chan_name in chan_list:
            if cat_name == "🎤 ZONAS DE VOZ":
                if not discord.utils.get(ctx.guild.voice_channels, name=chan_name): await ctx.guild.create_voice_channel(chan_name, category=cat)
            else:
                if not discord.utils.get(ctx.guild.text_channels, name=chan_name): await ctx.guild.create_text_channel(chan_name, category=cat)
    await ctx.send("✅ ¡Servidor configurado!")

@bot.command(name="clase_gratis")
@commands.has_role(ADMIN_ROLE_ID)
async def clase_gratis(ctx, fecha: str, hora: str):
    try:
        dt = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return await ctx.send("❌ Formato inválido. Usa: `AAAA-MM-DD HH:MM` (en UTC)")
    
    async with db_pool.acquire() as conn: # <--- CAMBIO
        await conn.execute("INSERT INTO clases (tipo, fecha_hora) VALUES ('gratis', $1)", dt)
        
    await ctx.send(f"✅ Clase gratuita programada para el **{dt.strftime('%d/%m/%Y a las %H:%M')} UTC**.")

@bot.command(name="clase_premium")
@commands.has_role(ADMIN_ROLE_ID)
async def clase_premium(ctx, fecha: str, hora: str):
    try:
        dt = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return await ctx.send("❌ Formato inválido. Usa: `AAAA-MM-DD HH:MM` (en UTC)")
        
    async with db_pool.acquire() as conn: # <--- CAMBIO
        await conn.execute("INSERT INTO clases (tipo, fecha_hora) VALUES ('premium', $1)", dt)
        
    await ctx.send(f"✅ Clase premium programada para el **{dt.strftime('%d/%m/%Y a las %H:%M')} UTC**.")

@bot.command(name="test_xp")
@commands.has_role(ADMIN_ROLE_ID)
async def test_xp(ctx, member: discord.Member, cantidad: int):
    user_data = await get_user_data(member.id) # <--- CAMBIO
    old_level = user_data['level'] if user_data else 1
    
    updated_data = await upsert_user_xp(member.id, cantidad, cantidad) # <--- CAMBIO
    
    await ctx.send(f"✅ Añadidos `{cantidad}` XP a {member.mention}. XP total: `{updated_data['xp']}`.")
    
    new_level = get_level(updated_data['xp'])
    if new_level > old_level:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET level = $1 WHERE user_id = $2", new_level, member.id)
        await assign_level_role(member, new_level)
        await ctx.send(f"¡{member.mention} ha subido al **Nivel {new_level}**!")

# --- TAREAS AUTOMÁTICAS (LOOPS) ---

# @tasks.loop(seconds=60) # <--- CAMBIO: Eliminamos este loop por completo
# async def save_data_loop():
#     pass

@tasks.loop(hours=24)
async def check_inactivity():
    await bot.wait_until_ready()
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    
    async with db_pool.acquire() as conn: # <--- CAMBIO
        inactive_users = await conn.fetch("SELECT user_id FROM users WHERE last_message_timestamp < $1", seven_days_ago)
        
        for record in inactive_users:
            user = bot.get_user(record['user_id'])
            if user:
                try:
                    await user.send("💪 ¡Hey! Notamos que llevas unos días sin pasar por la Academia de Calistenia 🏋️‍♂️.\n¡Vuelve a entrenar con nosotros y comparte tu progreso!")
                    # Actualizamos su timestamp para no volver a molestarle pronto
                    await conn.execute("UPDATE users SET last_message_timestamp = $1 WHERE user_id = $2", datetime.now(timezone.utc), user.id)
                except discord.Forbidden:
                    print(f"No se pudo enviar DM al usuario inactivo {user.name}")

@tasks.loop(hours=1)
async def revisar_clases():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    
    async with db_pool.acquire() as conn:
        # Primero, borramos las clases que ya pasaron
        await conn.execute("DELETE FROM clases WHERE fecha_hora < $1", now)
        
        # Luego, buscamos las clases que necesiten recordatorio
        clases_a_recordar = await conn.fetch("SELECT id, tipo, fecha_hora FROM clases")
        
    guild = bot.guilds[0] if bot.guilds else None
    if not guild: return
    
    for clase in clases_a_recordar:
        canal_nombre = "clases-grupales" if clase['tipo'] == "gratis" else "clases-exclusivas"
        canal = discord.utils.get(guild.text_channels, name=canal_nombre)
        if not canal: continue
        
        time_until = clase['fecha_hora'] - now
        # Lógica de recordatorio (sin cambios)
        if timedelta(hours=47) < time_until <= timedelta(hours=48) or timedelta(hours=23) < time_until <= timedelta(hours=24):
            day_str = "2 días" if time_until > timedelta(hours=24) else "MAÑANA"
            await canal.send(f"@everyone 🚨 ¡Recordatorio! La clase de **{clase['tipo']}** es en {day_str} ({clase['fecha_hora'].strftime('%d/%m a las %H:%M')} UTC)")

# recordatorio_asesorias (sin cambios)
@tasks.loop(hours=48)
async def recordatorio_asesorias():
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild: return
    asesorias_channel = discord.utils.get(guild.text_channels, name="asesorias-personales")
    if asesorias_channel: await asesorias_channel.send("@everyone 📢 ¿Ya reservaste tu asesoría 1 a 1 Premium? ¡No te pierdas la oportunidad de progresar con guía personalizada! 💪")

@tasks.loop(hours=24)
async def ranking_semanal():
    await bot.wait_until_ready()
    if datetime.now(timezone.utc).weekday() != 6 or datetime.now(timezone.utc).hour != 20: return
    
    guild = bot.guilds[0] if bot.guilds else None
    if not guild: return
    ranking_channel = discord.utils.get(guild.text_channels, name="ranking")
    if not ranking_channel: return
    
    async with db_pool.acquire() as conn: # <--- CAMBIO
        sorted_users = await conn.fetch("SELECT user_id, weekly_xp FROM users WHERE weekly_xp > 0 ORDER BY weekly_xp DESC LIMIT 10")
    
    if not sorted_users: return
    
    embed = discord.Embed(title="🏆 Ranking Semanal de la Academia 🏆", description="¡Estos son los miembros más activos de la semana!\n\n", color=discord.Color.gold())
    for i, user_data in enumerate(sorted_users):
        user = guild.get_member(user_data['user_id'])
        embed.description += f"**{i+1}.** {user.mention if user else 'Usuario Desconocido'} - `{user_data['weekly_xp']}` XP\n"
    
    await ranking_channel.send(embed=embed)
    
    campeon_id = sorted_users[0]['user_id']
    campeon_member = guild.get_member(campeon_id)
    if campeon_member:
        max_n = sum(1 for role in guild.roles if role.name.startswith("🏆 Campeón de la Semana #"))
        role_name = f"🏆 Campeón de la Semana #{max_n + 1}"
        campeon_role = await guild.create_role(name=role_name, color=discord.Color.gold(), mentionable=True)
        await campeon_member.add_roles(campeon_role)
        await ranking_channel.send(f"¡Felicidades {campeon_member.mention}, eres el **{role_name}**!")
    
    if guild.member_count > 15:
        for user_id, data in sorted_users[:10]:
            member = guild.get_member(int(user_id))
            if member:
                try: await member.send(f"🚀 ¡Felicidades! Estás en el TOP 10 de la Academia con `{data['weekly_xp']}` XP esta semana.")
                except discord.Forbidden: pass

    async with db_pool.acquire() as conn: # <--- CAMBIO: Reseteamos el XP semanal de todos con un solo comando, ¡mucho más eficiente!
        await conn.execute("UPDATE users SET weekly_xp = 0")
    print("XP semanal reseteado para todos los usuarios.")

# --- EJECUCIÓN DEL BOT ---
if __name__ == "__main__":
    if OPENAI_API_KEY:
        openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    else:
        print("⚠️ AVISO: No se proporcionó clave API de OpenAI. El comando !calistenico no funcionará.")
    
    if not DISCORD_BOT_TOKEN or not DATABASE_URL: # <--- CAMBIO
        print("❌ ERROR: Falta el token de Discord o la URI de la base de datos.")
    else:
        try:
            bot.run(DISCORD_BOT_TOKEN)
        except discord.errors.LoginFailure:
            print("❌ ERROR: El token de Discord es inválido.")
        except Exception as e:
            print(f"❌ ERROR INESPERADO: {e}")