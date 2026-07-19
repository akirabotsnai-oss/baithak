"""
worker.py — Background Task Worker

This script runs the bump bot loop entirely decoupled from the web application.
Run this script separately (e.g. `python worker.py`).
"""
import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from core.db import init_db_pool
from apps.bump_bot.routes import bump_bot_loop

load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="worker!", intents=intents)

async def main():
    print("Connecting to database...")
    await init_db_pool()
    
    print("Starting background bump loop...")
    asyncio.create_task(bump_bot_loop())
    
    if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        print("Logging in to Discord...")
        await bot.start(BOT_TOKEN)
    else:
        print("No BOT_TOKEN provided, keeping loop alive manually.")
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Worker stopped.")
