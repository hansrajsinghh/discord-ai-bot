import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
from dotenv import load_dotenv
import os
import openai

# ---------- LOAD ENV ----------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

if not TOKEN or not OPENAI_API_KEY:
    raise ValueError("Please set DISCORD_TOKEN and OPENAI_API_KEY in .env or Railway secrets")

# ---------- BOT SETUP ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------- DATABASE ----------
conn = sqlite3.connect("memory.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS memory (
    server_id TEXT,
    user_id TEXT,
    key TEXT,
    value TEXT,
    PRIMARY KEY (server_id, user_id, key)
)
""")
conn.commit()

# ---------- MEMORY FUNCTIONS ----------
MAX_MEMORY_LENGTH = 1000
TRIM_LENGTH = 500

def remember(server_id, user_id, key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO memory (server_id, user_id, key, value) VALUES (?, ?, ?, ?)",
        (server_id, user_id, key, value)
    )
    conn.commit()

def recall(server_id, user_id, key):
    cursor.execute(
        "SELECT value FROM memory WHERE server_id=? AND user_id=? AND key=?",
        (server_id, user_id, key)
    )
    row = cursor.fetchone()
    return row[0] if row else None

# ---------- CONVERSATION FUNCTIONS ----------
def update_conversation(server_id, user_id, message):
    convo = recall(server_id, user_id, "conversation") or ""
    convo += f"\nUser: {message}"
    if len(convo) > MAX_MEMORY_LENGTH:
        try:
            prompt = f"Summarize concisely, keeping important info:\n{convo}"
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"user","content":prompt}],
                max_tokens=200
            )
            convo = response.choices[0].message.content.strip()
        except Exception as e:
            print("Error summarizing:", e)
    remember(server_id, user_id, "conversation", convo)

def prune_memory():
    cursor.execute("SELECT server_id, user_id, value FROM memory WHERE key='conversation'")
    rows = cursor.fetchall()
    for server_id, user_id, convo in rows:
        if len(convo) > TRIM_LENGTH:
            trimmed = convo[-TRIM_LENGTH:]
            remember(server_id, user_id, "conversation", trimmed)
    print("Memory pruning completed.")

@tasks.loop(hours=1)
async def auto_prune_task():
    prune_memory()

def gpt_reply(server_id, user_id, message):
    conversation = recall(server_id, user_id, "conversation") or ""
    name = recall(server_id, user_id, "name")
    prompt = f"Conversation with {name if name else 'user'}:\n{conversation}\nUser: {message}\nAI:"
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":prompt}],
            max_tokens=250
        )
        reply = response.choices[0].message.content.strip()
        update_conversation(server_id, user_id, f"AI: {reply}")
        return reply
    except Exception as e:
        return f"Error generating response: {e}"

# ---------- BOT EVENTS ----------
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    try:
        synced = await tree.sync()  # Global slash commands for all servers
        print(f"Synced {len(synced)} global slash commands")
    except Exception as e:
        print("Error syncing commands:", e)
    auto_prune_task.start()

# ---------- SLASH COMMANDS ----------
@tree.command(name="setname", description="Tell the bot your name")
@app_commands.describe(name="Your name")
async def setname(interaction: discord.Interaction, name: str):
    remember(str(interaction.guild.id), str(interaction.user.id), "name", name)
    await interaction.response.send_message(f"Got it! I will remember your name as {name}.")

@tree.command(name="getname", description="Ask the bot your name")
async def getname(interaction: discord.Interaction):
    name = recall(str(interaction.guild.id), str(interaction.user.id), "name")
    if name:
        await interaction.response.send_message(f"Your name is {name}.")
    else:
        await interaction.response.send_message("I don't know your name yet. Use /setname!")

@tree.command(name="ask", description="Ask the AI bot anything")
@app_commands.describe(question="Your question for the AI")
async def ask(interaction: discord.Interaction, question: str):
    server_id = str(interaction.guild.id)
    user_id = str(interaction.user.id)
    update_conversation(server_id, user_id, question)
    reply = gpt_reply(server_id, user_id, question)
    await interaction.response.send_message(reply)

@tree.command(name="forget", description="Forget all your conversation history")
async def forget(interaction: discord.Interaction):
    remember(str(interaction.guild.id), str(interaction.user.id), "conversation", "")
    await interaction.response.send_message("I have cleared all your conversation memory!")

# ---------- RUN BOT ----------
bot.run(TOKEN)
