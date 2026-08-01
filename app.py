"""
app.py — Workspace Platform Entry Point

This file is intentionally thin. All business logic lives in:
  core/       — DB helpers, auth, workspace routes
  apps/       — Each app's routes and logic

To add a new app:
  1. Create apps/new_app/ folder
  2. Add blueprint to core/registry.py
  3. Run init_db.py to seed the apps table
"""
import os
import asyncio
import secrets
import re
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
import httpx
from quart import Quart, request, session, redirect, url_for, render_template, jsonify
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from dotenv import load_dotenv

load_dotenv()

# ─── App Setup ────────────────────────────────────────────────────────────────
app = Quart(__name__, template_folder='templates')
app.secret_key = os.environ.get("FLASK_SECRET") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
)

DATABASE_URL      = os.environ.get("DATABASE_URL")
BOT_TOKEN         = os.environ.get("BOT_TOKEN", "")
PUBLIC_CHANNEL_ID = os.environ.get("PUBLIC_CHANNEL_ID", "")
ADMIN_CHANNEL_ID  = os.environ.get("ADMIN_CHANNEL_ID", "")
GUILD_ID          = int(os.environ.get("GUILD_ID", "0") or "0")

# ─── Discord Bot ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─── Startup ──────────────────────────────────────────────────────────────────
@app.before_serving
async def startup():
    from core.db import init_db_pool, query
    pool = await init_db_pool()

    if pool:
        # Start bump bot loop
        from apps.bump_bot import bump_bot_loop
        app.bump_task = asyncio.create_task(bump_bot_loop())
        
        # Ensure workspace tables exist (idempotent)
        await query("""
            CREATE TABLE IF NOT EXISTS workspace_apps (
                id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                description TEXT, icon_emoji TEXT DEFAULT '📦',
                icon_color TEXT DEFAULT '#5865f2', route_prefix TEXT NOT NULL,
                is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0
            )
        """)
        await query("""
            CREATE TABLE IF NOT EXISTS workspace_members (
                user_id TEXT NOT NULL, app_id TEXT NOT NULL,
                granted_by TEXT, granted_at TEXT,
                PRIMARY KEY (user_id, app_id)
            )
        """)
        await query("""
            CREATE TABLE IF NOT EXISTS app_members (
                user_id TEXT NOT NULL, app_id TEXT NOT NULL,
                app_role TEXT NOT NULL DEFAULT 'viewer',
                PRIMARY KEY (user_id, app_id)
            )
        """)
        await query("""
            CREATE TABLE IF NOT EXISTS app_permissions (
                app_id TEXT NOT NULL, role TEXT NOT NULL, permission TEXT NOT NULL,
                PRIMARY KEY (app_id, role, permission)
            )
        """)
        await query("""
            CREATE TABLE IF NOT EXISTS admin_requests (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE,
                password_hash TEXT, requested_at TEXT, status TEXT
            )
        """)
        await query("""
            CREATE TABLE IF NOT EXISTS bump_accounts (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                token TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                is_enabled INTEGER DEFAULT 1,
                last_bump_time TEXT,
                bump_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'Offline'
            )
        """)
        # Seed apps if not already present
        await query("""
            INSERT INTO workspace_apps (id, display_name, description, icon_emoji, icon_color, route_prefix, is_active, sort_order)
            VALUES ('confessions','Confession Bot','Anonymous confessions platform','💬','#5865f2','/confessions',1,1),
                   ('bump_bot','Auto Bumper','Disboard auto-bump service','🚀','#10b981','/bump',1,2),
                   ('ai_resident','AI Resident','Autonomous AI Server Resident','🤖','#8a2be2','/ai_resident',1,3)
            ON CONFLICT DO NOTHING
        """)

    # Start Discord bots (Main + Optional AI Resident Bot)
    from core.db import cfg
    effective_bot_token = (await cfg("BOT_TOKEN")) or BOT_TOKEN
    effective_ai_token = await cfg("AI_BOT_TOKEN")

    # Load Confessions cog
    from apps.confessions.cog import ConfessionsCog
    await bot.add_cog(ConfessionsCog(bot))
    print("[Confessions] Registered Confessions Cog on the main bot.")

    from apps.ai_resident.cog import AIResidentCog
    
    # Check if a separate AI Bot token is configured for Dual Bot mode
    if effective_ai_token and effective_ai_token != effective_bot_token:
        ai_intents = discord.Intents.default()
        ai_intents.message_content = True
        ai_intents.members = True
        
        ai_bot = commands.Bot(command_prefix="?", intents=ai_intents)
        
        @ai_bot.event
        async def on_ready():
            from core.db import cfg
            gid = await cfg("guild_id", str(GUILD_ID))
            synced_guilds = []
            if gid and gid != "0":
                guild_obj = discord.Object(id=int(gid))
                ai_bot.tree.copy_global_to(guild=guild_obj)
                await ai_bot.tree.sync(guild=guild_obj)
                synced_guilds.append(gid)
            else:
                # Auto-detect: sync to every guild the bot is in for instant results
                for g in ai_bot.guilds:
                    ai_bot.tree.copy_global_to(guild=g)
                    await ai_bot.tree.sync(guild=g)
                    synced_guilds.append(str(g.id))
            await ai_bot.tree.sync()  # global fallback
            print(f"[AI Resident] {ai_bot.user} — synced to guilds: {synced_guilds}")

        bot.ai_bot = ai_bot
        await ai_bot.add_cog(AIResidentCog(ai_bot))
        asyncio.create_task(ai_bot.start(effective_ai_token))
        print("[AI Resident] Launched secondary bot client in parallel.")
    else:
        # Fall back to registering the Cog on the main bot
        bot.ai_bot = None
        await bot.add_cog(AIResidentCog(bot))
        print("[AI Resident] Registered AI Cog on the main bot.")
    @bot.event
    async def on_ready():
        print(f"[Main Bot] Logged in as {bot.user} (ID: {bot.user.id})")
        synced_guilds = []
        for g in bot.guilds:
            try:
                bot.tree.copy_global_to(guild=g)
                await bot.tree.sync(guild=g)
                synced_guilds.append(g.name)
            except Exception as e:
                print(f"[Main Bot Tree Sync Error] {g.name}: {e}")
        try:
            await bot.tree.sync()
        except Exception as e:
            print(f"[Main Bot Global Tree Sync Error]: {e}")
        print(f"[Main Bot] Connected & synced slash commands to guilds: {synced_guilds}")

    if effective_bot_token and len(effective_bot_token) > 20 and effective_bot_token != "YOUR_BOT_TOKEN_HERE":
        async def run_main_bot():
            try:
                print(f"[Main Bot] Connecting to Discord Gateway...")
                await bot.start(effective_bot_token)
            except Exception as e:
                print(f"[Main Bot Connection Error]: {e}")
        asyncio.create_task(run_main_bot())

    # Self-ping to keep Render free tier awake 24/7
    async def keep_alive():
        # Render automatically sets RENDER_EXTERNAL_URL for every service
        base_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        if not base_url:
            return  # Not on Render, skip (local dev)
        await asyncio.sleep(30)  # Wait for server to fully start first
        while True:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.get(f"{base_url}/ping")
                    print(f"[keep-alive] pinged {base_url}/ping")
            except Exception as e:
                print(f"[keep-alive] ping failed: {e}")
            await asyncio.sleep(540)  # Ping every 9 minutes

    asyncio.create_task(keep_alive())




# ─── Security Headers ─────────────────────────────────────────────────────────
@app.after_request
async def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response


# ─── Intrusion Tracking ───────────────────────────────────────────────────────
_last_leak_alert = None

@app.before_request
async def track_visitor():
    from core.db import query, cfg
    secret_path = await cfg("secret_path", "cmd-9x4k2")
    if request.path in (f"/{secret_path}", f"/{secret_path}/"):
        if "user" not in session:
            ip  = request.headers.get("X-Forwarded-For", request.remote_addr or "Unknown").split(',')[0].strip()
            ua  = request.headers.get("User-Agent", "")
            ref = request.headers.get("Referer", "")
            await query(
                "INSERT INTO visitor_logs (ip_address, user_agent, referer, timestamp) VALUES (?,?,?,?)",
                ip, ua, ref, datetime.utcnow().isoformat()
            )
            five_mins_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
            unique_ips = await query(
                "SELECT COUNT(DISTINCT ip_address) FROM visitor_logs WHERE timestamp > ?",
                five_mins_ago, fetch_one=True
            )
            global _last_leak_alert
            if unique_ips and unique_ips[0] >= 3:
                if not _last_leak_alert or (datetime.utcnow() - _last_leak_alert).total_seconds() > 300:
                    _last_leak_alert = datetime.utcnow()
                    admin_ch_id = await cfg("admin_channel_id", ADMIN_CHANNEL_ID)
                    if BOT_TOKEN and admin_ch_id:
                        embed = {
                            "title": "🚨 SECURITY ALERT: Possible Link Leak",
                            "description": f"Dashboard hit by **{unique_ips[0]} unique IPs** in 5 minutes.\nLatest Referer: `{ref or 'Direct Link'}`",
                            "color": 16711680,
                            "timestamp": datetime.utcnow().isoformat() + "Z"
                        }
                        async def send_alert():
                            try:
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"https://discord.com/api/v10/channels/{admin_ch_id}/messages",
                                        headers={"Authorization": f"Bot {BOT_TOKEN}"},
                                        json={"content": "@everyone Possible Dashboard Leak!", "embeds": [embed]}
                                    )
                            except Exception as e:
                                print(f"Leak alert failed: {e}")
                        asyncio.create_task(send_alert())


# ─── Auth Routes ──────────────────────────────────────────────────────────────
@app.route("/<path:secret>", methods=["GET"])
async def dynamic_login(secret):
    from core.db import cfg, log_audit
    from quart import abort
    real_secret = await cfg("secret_path", "cmd-9x4k2")
    if secret != real_secret:
        abort(404)
        
    session.update({
        "user":    "admin",
        "is_main": True,
        "role":    "god"
    })
    await log_audit("admin", "login", "Passwordless login via secret URL")
    return redirect(url_for("workspace.home"))


@app.route("/logout")
async def logout():
    from core.db import log_audit
    if "user" in session:
        await log_audit(session["user"], "logout", "Admin logged out")
    session.clear()
    return redirect("/")


@app.route("/ping")
async def ping():
    return jsonify({"status": "alive", "timestamp": datetime.utcnow().isoformat()})


# ─── Discord Bot Commands ─────────────────────────────────────────────────────
import string as _string, random as _random


@bot.event
async def on_ready():
    from core.db import cfg
    gid = await cfg("guild_id", str(GUILD_ID))
    synced_guilds = []
    if gid and gid != "0":
        guild_obj = discord.Object(id=int(gid))
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
        synced_guilds.append(gid)
    else:
        # Auto-detect: sync to every guild the bot is in
        for g in bot.guilds:
            bot.tree.copy_global_to(guild=g)
            await bot.tree.sync(guild=g)
            synced_guilds.append(str(g.id))
    await bot.tree.sync()  # global fallback
    print(f"[Main Bot] {bot.user} — synced to guilds: {synced_guilds}")


@bot.command(name="sync")
async def manual_sync(ctx):
    """Admin-only: force re-sync slash commands to this guild."""
    from core.db import cfg
    gid = str(ctx.guild.id) if ctx.guild else ""
    if gid:
        guild_obj = discord.Object(id=int(gid))
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
    await bot.tree.sync()
    await ctx.send("✅ Slash commands force-synced! They should appear within seconds.", delete_after=10)


# ─── Register Blueprints ──────────────────────────────────────────────────────
from core.workspace_routes import workspace_bp
from apps.confessions import confessions_bp
from apps.bump_bot import bump_bp
from apps.ai_resident import ai_resident_bp

app.register_blueprint(workspace_bp)
app.register_blueprint(confessions_bp)
app.register_blueprint(bump_bp)
app.register_blueprint(ai_resident_bp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
