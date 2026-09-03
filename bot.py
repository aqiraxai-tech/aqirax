import discord
from discord.ext import commands
import aiohttp
import asyncio
import io
import json
import os

# --- CONFIGURACIÓN CON VARIABLES DE ENTORNO ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
IA_KEY_AGNES = os.getenv("IA_KEY_AGNES")
TEXT_API_KEY = os.getenv("TEXT_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

if not DISCORD_TOKEN or not IA_KEY_AGNES or not TEXT_API_KEY:
    print("❌ ERROR: Faltan variables de entorno esenciales.")

# --- ESTADO GLOBAL Y MANTENIMIENTO ---
BOT_ACTIVO = True
MOTIVO_MANTENIMIENTO = "Mantenimiento programado"

# --- ENDPOINTS ---
URL_TEXT = "https://api.b.ai/v1/chat/completions"
URL_IMAGE = "https://apihub.agnes-ai.com/v1/images/generations"
URL_VIDEO = "https://apihub.agnes-ai.com/v1/videos"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents, help_command=None)

# COLA Y ARCHIVO DE MONEDA
video_queue = asyncio.Queue()
XCOINS_FILE = "xcoins.json"

# --- FUNCIONES DE XCOINS ---
def load_xcoins():
    if not os.path.exists(XCOINS_FILE):
        with open(XCOINS_FILE, "w") as f:
            json.dump({}, f)
        return {}
    
    try:
        with open(XCOINS_FILE, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except json.JSONDecodeError:
        with open(XCOINS_FILE, "w") as f:
            json.dump({}, f)
        return {}

def save_xcoins(data):
    with open(XCOINS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_user_xcoins(user_id: str) -> int:
    coins_data = load_xcoins()
    if user_id not in coins_data:
        coins_data[user_id] = 0
        save_xcoins(coins_data)
    return coins_data[user_id]

def update_user_xcoins(user_id: str, amount: int):
    coins_data = load_xcoins()
    actual = coins_data.get(user_id, 0)
    coins_data[user_id] = max(0, actual + amount)
    save_xcoins(coins_data)

async def verificar_estado(ctx):
    """Verifica si el bot está activo o en mantenimiento."""
    if not BOT_ACTIVO and ctx.author.id != OWNER_ID:
        embed = discord.Embed(
            title="🚫 Bot En Mantenimiento",
            description=f"El bot ha sido pausado temporalmente por el Administrador.\n\n**Razón:** `{MOTIVO_MANTENIMIENTO}`",
            color=discord.Color.red()
        )
        embed.set_footer(text="Intenta nuevamente más tarde.")
        await ctx.send(embed=embed)
        return False
    return True

# --- EVENTOS ---
@bot.event
async def on_ready():
    print(f">> Aqirax System en línea como {bot.user}")
    bot.loop.create_task(video_worker())

# --- WORKER DE LA COLA DE VIDEOS ---
async def video_worker():
    while True:
        ctx, prompt, mensaje_espera = await video_queue.get()
        try:
            user_id = str(ctx.author.id)
            coins = get_user_xcoins(user_id)
            
            if coins < 5:
                embed_no_coins = discord.Embed(
                    title="Fondos Insuficientes",
                    description="No tienes suficientes **XCoins** para generar este video.\nCosto: `5 XCoins`",
                    color=discord.Color.orange()
                )
                await mensaje_espera.edit(content=None, embed=embed_no_coins)
                video_queue.task_done()
                continue

            await mensaje_espera.edit(content="⚙️ *Enviando render a los servidores de video (10s)...*")
            
            headers = {
                "Authorization": f"Bearer {IA_KEY_AGNES}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "agnes-video-2.5-flash",
                "prompt": prompt,
                "seconds": "10",  # Actualizado a 10s
                "mode": "text",
                "size": "720P",
                "aspect_ratio": "16:9"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(URL_VIDEO, headers=headers, json=payload, timeout=30) as res:
                    if res.status in [200, 201, 202]:
                        data = await res.json()
                        video_id = data.get("video_id") or data.get("id") or data.get("task_id")
                        if not video_id and "data" in data and isinstance(data["data"], dict):
                            video_id = data["data"].get("id") or data["data"].get("video_id")

                        if not video_id:
                            await mensaje_espera.edit(content="`[Error]` No se generó el ID de tarea para el video.")
                            video_queue.task_done()
                            continue

                        await mensaje_espera.edit(content="🎬 *Procesando fotogramas... Esto puede tomar unos momentos.*")
                        url_poll = f"https://apihub.agnes-ai.com/v1/agnesapi?video_id={video_id}&model_name=agnes-video-2.5-flash"
                        
                        video_exitoso = False
                        for _ in range(40):
                            await asyncio.sleep(5)
                            async with session.get(url_poll, headers=headers, timeout=15) as poll_res:
                                if poll_res.status == 200:
                                    poll_data = await poll_res.json()
                                    status = poll_data.get("status") or poll_data.get("state")
                                    video_url = poll_data.get("video_url") or poll_data.get("url") or (poll_data.get("data", {}).get("url") if isinstance(poll_data.get("data"), dict) else None)

                                    if video_url:
                                        await mensaje_espera.edit(content="📦 *Descargando archivo renderizado...*")
                                        async with session.get(video_url, timeout=60) as file_res:
                                            if file_res.status == 200:
                                                video_bytes = await file_res.read()
                                                archivo_mp4 = discord.File(io.BytesIO(video_bytes), filename="video_10s.mp4")
                                                await ctx.send(content=f"**Solicitado por:** {ctx.author.mention}", file=archivo_mp4)
                                                await mensaje_espera.delete()
                                            else:
                                                await ctx.send(content=f"**Solicitado por:** {ctx.author.mention}\n{video_url}")
                                                await mensaje_espera.delete()
                                        
                                        update_user_xcoins(user_id, -5)
                                        video_exitoso = True
                                        break
                                    elif status in ["failed", "error"]:
                                        await mensaje_espera.edit(content="`[Error]` Falló la generación del video en los servidores.")
                                        break
                        if not video_exitoso:
                            await mensaje_espera.edit(content="`[Timeout]` El video tardó demasiado en procesarse.")
                    else:
                        await mensaje_espera.edit(content=f"`[HTTP {res.status}]` Servidor de video no disponible.")

        except Exception as e:
            await mensaje_espera.edit(content=f"`[Exception]` {str(e)}")
        
        video_queue.task_done()

# --- COMANDOS Mantenimiento / Sistema (OWNER) ---
@bot.command(name="off")
async def apagar_bot(ctx, *, razon: str = "Mantenimiento de rutina"):
    global BOT_ACTIVO, MOTIVO_MANTENIMIENTO
    if ctx.author.id != OWNER_ID:
        return
    
    BOT_ACTIVO = False
    MOTIVO_MANTENIMIENTO = razon
    
    embed = discord.Embed(
        title="🔴 Modo Mantenimiento Activado",
        description=f"El bot ha sido apagado para los usuarios.\n\n**Razón:** `{razon}`",
        color=discord.Color.dark_red()
    )
    embed.set_footer(text=f"Ejecutado por {ctx.author.name}")
    await ctx.send(embed=embed)

@bot.command(name="on")
async def encender_bot(ctx):
    global BOT_ACTIVO
    if ctx.author.id != OWNER_ID:
        return
    
    BOT_ACTIVO = True
    
    embed = discord.Embed(
        title="🟢 Sistema Operativo",
        description="El bot ha sido reactivado. Todos los comandos se encuentran habilitados.",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

# --- COMANDOS PÚBLICOS DE IA ---

# 1. TEXTO (.a)
@bot.command(name="a")
async def responder_pregunta(ctx, *, prompt: str):
    if not await verificar_estado(ctx):
        return

    mensaje_espera = await ctx.send("🧠 *Consultando modelo...*")
    headers = {
        "Authorization": f"Bearer {TEXT_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "Eres Aqirax AI, un modelo avanzado de asistencia. Responde directamente y con claridad."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1200
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(URL_TEXT, headers=headers, json=payload, timeout=60) as res:
                if res.status == 200:
                    data = await res.json()
                    respuesta = data["choices"][0]["message"]["content"]
                    if len(respuesta) > 1900:
                        respuesta = respuesta[:1900] + "..."
                    await mensaje_espera.edit(content=f"**Pregunta:** {prompt}\n\n{respuesta}")
                else:
                    await mensaje_espera.edit(content=f"`[Error {res.status}]` No se pudo obtener respuesta del modelo.")
    except Exception as e:
        await mensaje_espera.edit(content=f"`[Error]` {str(e)}")

# 2. IMAGEN (.i)
@bot.command(name="i")
async def generar_imagen(ctx, *, prompt: str):
    if not await verificar_estado(ctx):
        return

    mensaje_espera = await ctx.send("🎨 *Renderizando imagen 4K...*")
    headers = {
        "Authorization": f"Bearer {IA_KEY_AGNES}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "agnes-image-2.1-flash",
        "prompt": prompt,
        "size": "4K",
        "ratio": "16:9",
        "extra_body": {"response_format": "url"}
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(URL_IMAGE, headers=headers, json=payload, timeout=60) as res:
                if res.status == 200:
                    data = await res.json()
                    image_url = data.get("data", [{}])[0].get("url") or data.get("url")
                    if image_url:
                        embed = discord.Embed(
                            title="Resultado de Imagen",
                            description=f"**Prompt:** {prompt}",
                            color=discord.Color.blue()
                        )
                        embed.set_image(url=image_url)
                        embed.set_footer(text="Aqirax AI • 4K")
                        await mensaje_espera.edit(content=None, embed=embed)
                    else:
                        await mensaje_espera.edit(content="`[Error]` La respuesta no devolvió una URL válida.")
                else:
                    await mensaje_espera.edit(content=f"`[HTTP {res.status}]` Fallo al conectar con el servidor de imagen.")
    except Exception as e:
        await mensaje_espera.edit(content=f"`[Error]` {str(e)}")

# 3. VIDEO (.v) - 10 SEGUNDOS CON COLA
@bot.command(name="v")
async def generar_video(ctx, *, prompt: str):
    if not await verificar_estado(ctx):
        return

    user_id = str(ctx.author.id)
    user_coins = get_user_xcoins(user_id)

    if user_coins < 5:
        embed_no_coins = discord.Embed(
            title="XCoins Insuficientes",
            description=f"Se requieren **5 XCoins** para generar un video de 10s.\nTu saldo actual: `{user_coins} XCoins`",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed_no_coins)

    posicion = video_queue.qsize() + 1
    
    if posicion > 1:
        mensaje_espera = await ctx.send(f"⏳ **Añadido a la cola.** Tu posición actual es **#{posicion}**. Espere su turno...")
    else:
        mensaje_espera = await ctx.send("⏳ **Encolado.** Eres el primero en la fila, procesando video...")

    await video_queue.put((ctx, prompt, mensaje_espera))

# --- COMANDOS DE MONEDA (XCOINS) ---

@bot.command(name="bal")
async def ver_creditos(ctx):
    coins = get_user_xcoins(str(ctx.author.id))
    embed = discord.Embed(
        title="Balance de Usuario",
        description=f"**Usuario:** {ctx.author.mention}\n**Saldo:** `{coins} XCoins`",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

@bot.command(name="vs")
async def ver_creditos_usuario(ctx, member: discord.Member):
    if ctx.author.id != OWNER_ID:
        return
    coins = get_user_xcoins(str(member.id))
    embed = discord.Embed(
        title="Consulta de Saldo",
        description=f"**Usuario:** {member.mention}\n**Saldo:** `{coins} XCoins`",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@bot.command(name="adc")
async def agregar_creditos(ctx, member: discord.Member, cantidad: int):
    if ctx.author.id != OWNER_ID:
        return
    update_user_xcoins(str(member.id), cantidad)
    await ctx.send(f"Añadidos `{cantidad} XCoins` a {member.mention}.")

@bot.command(name="resc")
async def quitar_creditos(ctx, member: discord.Member, cantidad: str):
    if ctx.author.id != OWNER_ID:
        return
    
    user_id = str(member.id)
    current_coins = get_user_xcoins(user_id)

    if cantidad.lower() == "all":
        update_user_xcoins(user_id, -current_coins)
        await ctx.send(f"Se removieron **todos** los XCoins de {member.mention}.")
    else:
        try:
            cant_num = int(cantidad)
            update_user_xcoins(user_id, -cant_num)
            await ctx.send(f"Removidos `{cant_num} XCoins` de {member.mention}.")
        except ValueError:
            await ctx.send("Especifica una cantidad numérica o `all`.")

# --- AYUDA (.help) ---
@bot.command(name="help")
async def ayuda(ctx):
    embed = discord.Embed(
        title="Panel de Comandos - Aqirax AI",
        color=discord.Color.blue()
    )
    embed.add_field(name=".a <prompt>", value="Consulta al modelo Aqirax Flash.", inline=False)
    embed.add_field(name=".i <prompt>", value="Genera imágenes en calidad 4K.", inline=False)
    embed.add_field(name=".v <prompt>", value="Genera videos de 10s (Cuesta 5 XCoins).", inline=False)
    embed.add_field(name=".bal", value="Consulta tu saldo de XCoins.", inline=False)
    
    if ctx.author.id == OWNER_ID:
        embed.add_field(
            name="Comandos de Administrador",
            value=(
                "`.off [razon]` - Pone el bot en mantenimiento.\n"
                "`.on` - Reactiva el bot.\n"
                "`.adc @user <cant>` - Otorga XCoins.\n"
                "`.resc @user <cant/all>` - Quita XCoins.\n"
                "`.vs @user` - Revisa saldo de un usuario."
            ),
            inline=False
        )
    await ctx.send(embed=embed)

# --- INICIALIZACIÓN ---
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
