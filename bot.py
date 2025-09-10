import discord
from discord.ext import commands, tasks
import json
import os
import openai
from datetime import datetime, timedelta, timezone
import config
from config import DISCORD_BOT_TOKEN
from config import OPENAI_API_KEY
from keep_alive import keep_alive

keep_alive()

# --- CONFIGURACIÓN DEL BOT ---
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
bot = commands.Bot(command_prefix="!", intents=INTENTS, help_command=None)

# --- VARIABLES GLOBALES Y ARCHIVOS DE DATOS ---
XP_FILE = "xp_data.json"
CLASES_FILE = "clases.json"
xp_data = {}
clases_data = {"gratis": [], "premium": []}
openai_client = None

# --- NUEVA PALETA DE COLORES PARA ROLES ---
COLOR_PALETTE = [
    discord.Color.blue(),
    discord.Color.green(),
    discord.Color.orange(),
    discord.Color.purple(),
    discord.Color.red(),
    discord.Color.gold(),
    discord.Color.teal(),
    discord.Color.magenta(),
    discord.Color.dark_green(),
    discord.Color.dark_blue(),
    discord.Color.from_rgb(230, 60, 60), # Rojo Intenso
    discord.Color.from_rgb(60, 180, 230) # Azul Cielo
]

# --- FUNCIONES AUXILIARES ---
def load_data(file, default_data):
    if os.path.exists(file):
        with open(file, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except json.JSONDecodeError: return default_data
    return default_data

def save_data(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def get_level(xp):
    return int(xp / 150) + 1

def get_role_name_for_level(level):
    if level < 10: return "Rookie 🐣"
    base_level = (level // 10) * 10
    base_title = config.LEVEL_ROLES_BASE.get(base_level, f"Nivel {base_level}")
    if level % 10 == 0: return base_title
    if level % 5 == 0: return f"{base_title} CALISTÉNICO"
    return f"{base_title} DISCIPLINADO"

# --- FUNCIÓN DE ASIGNAR ROLES ACTUALIZADA CON COLORES ---
async def assign_level_role(member, new_level):
    guild = member.guild
    new_role_name = get_role_name_for_level(new_level)
    
    # Seleccionar un color de la paleta basado en el rango de nivel
    base_level_tier = (new_level // 10)
    color_index = (base_level_tier - 1) % len(COLOR_PALETTE)
    selected_color = COLOR_PALETTE[color_index]
    
    roles_de_nivel_base = list(config.LEVEL_ROLES_BASE.values()) + ["Rookie 🐣"]
    roles_to_remove = []
    for role in member.roles:
        if any(base_title in role.name for base_title in roles_de_nivel_base) and role.name != new_role_name:
            roles_to_remove.append(role)

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Actualización de rol de nivel.")

    new_role = discord.utils.get(guild.roles, name=new_role_name)
    if not new_role:
        new_role = await guild.create_role(name=new_role_name, color=selected_color, mentionable=False)
    # Si el rol ya existía pero estaba en gris, le ponemos color
    elif new_role.color == discord.Color.default():
        await new_role.edit(color=selected_color)
    
    if new_role not in member.roles:
        await member.add_roles(new_role, reason="Subida de nivel.")


# --- EVENTOS PRINCIPALES ---
@bot.event
async def on_ready():
    global xp_data, clases_data
    xp_data = load_data(XP_FILE, {})
    clases_data = load_data(CLASES_FILE, {"gratis": [], "premium": []})
    
    save_data_loop.start()
    check_inactivity.start()
    ranking_semanal.start()
    recordatorio_asesorias.start()
    revisar_clases.start()
    
    print(f"✅ Bot conectado como {bot.user}")
    print(f"   - Servidores: {[guild.name for guild in bot.guilds]}")

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
    if message.author.bot: return

    user_id = str(message.author.id)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if user_id not in xp_data:
        xp_data[user_id] = {"xp": 0, "level": 1, "weekly_xp": 0, "last_message_timestamp": 0, "last_rutina_date": "", "last_attachment_date": "", "attachments_today": 0}
    
    xp_data[user_id]["last_message_timestamp"] = datetime.now(timezone.utc).timestamp()
    gained_xp = config.XP_PER_MESSAGE

    if "RUTINA HECHA!" in message.content.upper():
        if xp_data[user_id].get("last_rutina_date") != today_str:
            gained_xp += config.XP_RUTINA_HECHA
            xp_data[user_id]["last_rutina_date"] = today_str

    if message.attachments:
        if xp_data[user_id].get("last_attachment_date") != today_str:
            xp_data[user_id]["attachments_today"] = 0
        if xp_data[user_id]["attachments_today"] < 4:
            gained_xp += config.XP_ATTACHMENT
            xp_data[user_id]["attachments_today"] += 1
            xp_data[user_id]["last_attachment_date"] = today_str

    xp_data[user_id]["xp"] += gained_xp
    xp_data[user_id]["weekly_xp"] = xp_data[user_id].get("weekly_xp", 0) + gained_xp
    
    old_level = xp_data[user_id]["level"]
    new_level = get_level(xp_data[user_id]["xp"])

    if new_level > old_level:
        xp_data[user_id]["level"] = new_level
        await assign_level_role(message.author, new_level)
        
        level_up_channel = discord.utils.get(message.guild.text_channels, name="level-up")
        if level_up_channel:
            try:
                await level_up_channel.send(f"🎉 ¡Enhorabuena {message.author.mention}, has subido a **Nivel {new_level}**! Tu nuevo rol es **{get_role_name_for_level(new_level)}**.")
            except discord.errors.Forbidden:
                print(f"!!! ERROR DE PERMISOS: No puedo enviar mensajes en el canal #level-up. Revisa los permisos del rol del bot.")
            except Exception as e:
                print(f"!!! ERROR INESPERADO al anunciar en #level-up: {e}")
        else:
            print(f"!!! AVISO: No se encontró el canal #level-up en el servidor '{message.guild.name}'. El anuncio de subida de nivel no se mostrará.")

    await bot.process_commands(message)

# --- COMANDOS PARA MIEMBROS ---
@bot.command(name="nivel")
async def nivel(ctx):
    user_id = str(ctx.author.id)
    if user_id in xp_data: await ctx.send(f"📊 {ctx.author.mention}, eres **Nivel {xp_data[user_id]['level']}** con **{xp_data[user_id]['xp']}** XP.")
    else: await ctx.send("Aún no tienes XP. ¡Empieza a participar!")

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
    if not clases_data["gratis"] and not clases_data["premium"]: return await ctx.send("📅 No hay clases programadas.")
    msg = "📅 **Clases Programadas:**\n"
    if clases_data["gratis"]: msg += "\n**Gratuitas:**\n" + "\n".join([f"  - {datetime.fromisoformat(c).strftime('%d/%m/%Y a las %H:%M')}" for c in sorted(clases_data["gratis"])])
    if clases_data["premium"]: msg += "\n**Premium:**\n" + "\n".join([f"  - {datetime.fromisoformat(c).strftime('%d/%m/%Y a las %H:%M')}" for c in sorted(clases_data["premium"])])
    await ctx.send(msg)

# --- COMANDOS DE AYUDA ---
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(title="🤖 Comandos de la Academia", description="Aquí tienes los comandos que puedes usar:", color=discord.Color.blue())
    embed.add_field(name="`!nivel`", value="Muestra tu nivel y XP actual.", inline=False)
    embed.add_field(name="`!calistenico [pregunta]`", value="Habla con el entrenador IA para resolver tus dudas.", inline=False)
    embed.add_field(name="`!clases`", value="Muestra las próximas clases programadas.", inline=False)
    embed.set_footer(text="Gana XP participando, compartiendo tu progreso y ayudando a otros.")
    await ctx.send(embed=embed)

@bot.command(name="adminhelp")
@commands.has_role(1414358400707592382)
async def adminhelp_command(ctx):
    embed = discord.Embed(title="👑 Comandos de Administración", description="Comandos para gestionar el servidor:", color=discord.Color.gold())
    embed.add_field(name="`!setup`", value="Crea/repara la estructura de canales del servidor.", inline=False)
    embed.add_field(name="`!clase_gratis [AAAA-MM-DD] [HH:MM]`", value="Programa una clase gratuita.", inline=False)
    embed.add_field(name="`!clase_premium [AAAA-MM-DD] [HH:MM]`", value="Programa una clase premium.", inline=False)
    embed.add_field(name="`!test_xp @usuario [cantidad]`", value="Añade XP a un usuario y fuerza un ranking de prueba.", inline=False)
    await ctx.send(embed=embed)

# --- COMANDOS DE ADMINISTRACIÓN ---
@bot.command(name="setup")
@commands.has_permissions(administrator=True)
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
@commands.has_permissions(administrator=True)
async def clase_gratis(ctx, fecha: str, hora: str):
    try: dt = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    except ValueError: return await ctx.send("❌ Formato inválido. Usa: `AAAA-MM-DD HH:MM`")
    clases_data["gratis"].append(dt.isoformat())
    await ctx.send(f"✅ Clase gratuita programada para el **{dt.strftime('%d/%m/%Y a las %H:%M')}**.")

@bot.command(name="clase_premium")
@commands.has_permissions(administrator=True)
async def clase_premium(ctx, fecha: str, hora: str):
    try: dt = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    except ValueError: return await ctx.send("❌ Formato inválido. Usa: `AAAA-MM-DD HH:MM`")
    clases_data["premium"].append(dt.isoformat())
    await ctx.send(f"✅ Clase premium programada para el **{dt.strftime('%d/%m/%Y a las %H:%M')}**.")

@bot.command(name="test_xp")
@commands.has_permissions(administrator=True)
async def test_xp(ctx, member: discord.Member, cantidad: int):
    user_id = str(member.id)
    if user_id not in xp_data: xp_data[user_id] = {"xp": 0, "level": 1, "weekly_xp": 0}
    xp_data[user_id]["xp"] += cantidad
    xp_data[user_id]["weekly_xp"] = xp_data[user_id].get("weekly_xp", 0) + cantidad
    await ctx.send(f"✅ Añadidos `{cantidad}` XP a {member.mention}. XP total: `{xp_data[user_id]['xp']}`.")
    old_level = xp_data[user_id].get("level", 1); new_level = get_level(xp_data[user_id]['xp'])
    if new_level > old_level:
        xp_data[user_id]["level"] = new_level
        await assign_level_role(member, new_level)
        await ctx.send(f"¡{member.mention} ha subido al **Nivel {new_level}**!")

# --- TAREAS AUTOMÁTICAS (LOOPS) ---
@tasks.loop(seconds=60)
async def save_data_loop():
    save_data(XP_FILE, xp_data)
    save_data(CLASES_FILE, clases_data)

@tasks.loop(hours=24)
async def check_inactivity():
    await bot.wait_until_ready()
    seven_days_ago_ts = (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    for user_id, data in list(xp_data.items()):
        if data.get("last_message_timestamp", 0) < seven_days_ago_ts:
            user = bot.get_user(int(user_id))
            if user:
                try:
                    await user.send("💪 ¡Hey! Notamos que llevas unos días sin pasar por la Academia de Calistenia 🏋️‍♂️.\n¡Vuelve a entrenar con nosotros y comparte tu progreso!")
                    xp_data[user_id]["last_message_timestamp"] = datetime.now(timezone.utc).timestamp()
                except discord.Forbidden: print(f"No se pudo enviar DM al usuario inactivo {user.name}")

@tasks.loop(hours=1)
async def revisar_clases():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    for tipo, lista_iso in clases_data.items():
        # Asumiendo que el bot está en un solo servidor
        guild = bot.guilds[0] if bot.guilds else None
        if not guild: continue
        
        canal_nombre = "clases-grupales" if tipo == "gratis" else "clases-exclusivas"
        canal = discord.utils.get(guild.text_channels, name=canal_nombre)
        if not canal: continue

        for c_iso in list(lista_iso):
            clase_dt = datetime.fromisoformat(c_iso).replace(tzinfo=timezone.utc)
            if clase_dt < now: 
                clases_data[tipo].remove(c_iso)
                continue
            
            time_until = clase_dt - now
            if timedelta(hours=47) < time_until <= timedelta(hours=48) or timedelta(hours=23) < time_until <= timedelta(hours=24):
                 days_left = 2 if time_until > timedelta(hours=24) else 1
                 day_str = "2 días" if days_left == 2 else "MAÑANA"
                 await canal.send(f"@everyone 🚨 ¡Recordatorio! La clase de **{tipo}** es en {day_str} ({clase_dt.strftime('%d/%m a las %H:%M')})")

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
    
    sorted_users = sorted([item for item in xp_data.items() if item[1].get("weekly_xp", 0) > 0], key=lambda item: item[1]["weekly_xp"], reverse=True)
    if not sorted_users: return
    
    embed = discord.Embed(title="🏆 Ranking Semanal de la Academia 🏆", description="¡Estos son los miembros más activos de la semana!\n\n", color=discord.Color.gold())
    for i, (user_id, data) in enumerate(sorted_users[:10]):
        user = guild.get_member(int(user_id))
        embed.description += f"**{i+1}.** {user.mention if user else f'Usuario Desconocido'} - `{data['weekly_xp']}` XP\n"
    
    await ranking_channel.send(embed=embed)

    campeon_id = int(sorted_users[0][0])
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
    
    for user_id in xp_data: xp_data[user_id]["weekly_xp"] = 0

# --- EJECUCIÓN DEL BOT ---
if __name__ == "__main__":
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    if OPENAI_API_KEY:
        openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    else:
        print("⚠️ AVISO: No se proporcionó clave API de OpenAI. El comando !calistenico no funcionará.")

    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("❌ ERROR: El token de Discord es inválido.")
    except Exception as e:
        print(f"❌ ERROR INESPERADO: {e}")