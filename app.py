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
    SESSION_COOKIE_SECURE=True,
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
                   ('bump_bot','Auto Bumper','Disboard auto-bump service','🚀','#10b981','/bump',1,2)
            ON CONFLICT DO NOTHING
        """)

    # Start Discord bot
    if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE":
        asyncio.create_task(bot.start(BOT_TOKEN))




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
_login_attempts = {}

@app.route("/<path:secret>", methods=["GET", "POST"])
async def dynamic_login(secret):
    from core.db import query, cfg, log_audit
    from quart import abort
    real_secret = await cfg("secret_path", "cmd-9x4k2")
    if secret != real_secret:
        abort(404)
        
    if "user" in session:
        return redirect(url_for("workspace.home"))
    error = None
    bot_pfp  = await cfg("bot_pfp_url")
    bot_name = await cfg("bot_name", "Confession Bot")
    if request.method == "POST":
        form     = await request.form
        username = form.get("username", "").strip()
        password = form.get("password", "").strip()
        ip       = request.headers.get("X-Forwarded-For", request.remote_addr or "Unknown").split(',')[0].strip()
        rec = _login_attempts.get(ip, {"count": 0, "locked_until": None})
        if rec["locked_until"] and datetime.utcnow() < rec["locked_until"]:
            error = "Too many failed attempts. Locked out for 10 minutes."
        else:
            if rec.get("locked_until") and datetime.utcnow() >= rec["locked_until"]:
                _login_attempts[ip] = {"count": 0, "locked_until": None}
            row = await query(
                "SELECT password_hash, is_main_admin, is_revoked, role FROM admins WHERE username=?",
                username, fetch_one=True
            )
            if row and not row["is_revoked"] and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
                session.update({
                    "user":    username,
                    "is_main": bool(row["is_main_admin"]),
                    "role":    row["role"] or "admin"
                })
                _login_attempts.pop(ip, None)
                await query("UPDATE admins SET last_login=? WHERE username=?", datetime.utcnow().isoformat(), username)
                await log_audit(username, "login", f"Login from {ip}")
                return redirect(url_for("workspace.home"))
            else:
                count = rec["count"] + 1
                locked = datetime.utcnow() + timedelta(minutes=10) if count >= 5 else None
                _login_attempts[ip] = {"count": count, "locked_until": locked}
                error = "Invalid credentials"
    return await render_template("login.html", error=error, bot_pfp=bot_pfp, bot_name=bot_name)


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


@app.route("/register", methods=["GET", "POST"])
async def register():
    from core.db import query, cfg
    if "user" in session:
        return redirect(url_for("workspace.home"))
    error = None
    bot_pfp  = await cfg("bot_pfp_url")
    bot_name = await cfg("bot_name", "Confession Bot")
    if request.method == "POST":
        form = await request.form
        un   = form.get("username", "").strip()
        pw   = form.get("password", "").strip()
        if not un or not pw:
            error = "Username and password required."
        elif len(pw) < 8:
            error = "Password must be at least 8 characters long."
        elif not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
            error = "Password must contain both letters and numbers."
        else:
            exists_admin = await query("SELECT 1 FROM admins WHERE username=?", un, fetch_one=True)
            exists_req   = await query("SELECT 1 FROM admin_requests WHERE username=? AND status='pending'", un, fetch_one=True)
            if exists_admin or exists_req:
                error = "Username already exists or request is pending."
            else:
                h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
                await query(
                    "INSERT INTO admin_requests (username, password_hash, requested_at, status) VALUES (?,?,?,?)",
                    un, h, datetime.utcnow().isoformat(), "pending"
                )
                return redirect(url_for("register_success"))
    return await render_template("login.html", active="register", error=error, bot_pfp=bot_pfp, bot_name=bot_name)


@app.route("/register-success")
async def register_success():
    from core.db import cfg
    if "user" in session:
        return redirect(url_for("workspace.home"))
    bot_pfp  = await cfg("bot_pfp_url")
    bot_name = await cfg("bot_name", "Confession Bot")
    return await render_template("login.html", active="register_success", bot_pfp=bot_pfp, bot_name=bot_name)


# ─── Discord Bot Commands ─────────────────────────────────────────────────────
import string as _string, random as _random

def generate_id():
    return "C-" + secrets.token_hex(4)


@bot.tree.command(name="confess", description="Submit an anonymous message into the void")
async def confess(interaction: discord.Interaction, message: str, image: str = None):
    from core.db import query, cfg, set_cfg
    await interaction.response.defer(ephemeral=True)
    user_id  = str(interaction.user.id)
    username = str(interaction.user)

    if await cfg("enabled", "1") == "0":
        return await interaction.followup.send(await cfg("msg_paused"), ephemeral=True)
    min_days = int(await cfg("min_account_age_days", "0"))
    if min_days > 0 and (datetime.utcnow() - interaction.user.created_at.replace(tzinfo=None)).days < min_days:
        return await interaction.followup.send(await cfg("msg_tooyoung"), ephemeral=True)
    if await query("SELECT 1 FROM banned_users WHERE user_id=?", user_id, fetch_one=True):
        return await interaction.followup.send(await cfg("msg_shadowban"), ephemeral=True)

    eff_cd = max(int(await cfg("cooldown", "0")), int(await cfg("slowdown", "0")))
    last_cd = await query("SELECT last_used FROM cooldowns WHERE user_id=?", user_id, fetch_one=True)
    if last_cd:
        elapsed = (datetime.utcnow() - datetime.fromisoformat(last_cd[0])).total_seconds()
        if elapsed < eff_cd:
            return await interaction.followup.send(
                (await cfg("msg_cooldown")).replace("{wait}", str(int(eff_cd - elapsed))), ephemeral=True
            )
    await query(
        "INSERT INTO cooldowns (user_id, last_used) VALUES (?,?) "
        "ON CONFLICT (user_id) DO UPDATE SET last_used=EXCLUDED.last_used",
        user_id, datetime.utcnow().isoformat()
    )

    words  = [r[0].lower() for r in await query("SELECT word FROM blacklist_words")]
    status = 'quarantine' if any(w in message.lower() for w in words) else 'posted'

    conf_id  = generate_id()
    max_num  = (await query("SELECT MAX(confession_number) FROM confessions", fetch_one=True))[0]
    conf_num = (max_num or 0) + 1

    reply_match = re.match(r'^reply to #(\d+):?', message, re.IGNORECASE)
    reply_to    = reply_match.group(1) if reply_match else None

    pub_msg_id = None
    pub_ch_id  = await cfg("public_channel_id", PUBLIC_CHANNEL_ID)
    if status == 'posted' and pub_ch_id:
        embed_colors = ["3498DB", "F1C40F", "5865F2", "9B59B6", "2ECC71", "E67E22", "E74C3C", "1ABC9C", "E91E63"]
        emb_color = int(embed_colors[conf_num % len(embed_colors)], 16)
        embed = discord.Embed(
            title=f"Anonymous Confession (#{conf_num})",
            description=f'"{message}"', color=emb_color
        )
        if image:
            embed.set_image(url=image)
        try:
            pub_channel = bot.get_channel(int(pub_ch_id))
            pub_msg     = await pub_channel.send(embed=embed)
            pub_msg_id  = str(pub_msg.id)
            if await cfg("reactions_enabled", "0") == "1":
                emojis = [e.strip() for e in (await cfg("reaction_emojis", "👍,👎,❤️")).split(",") if e.strip()]
                for e in emojis:
                    try:
                        await pub_msg.add_reaction(e)
                    except Exception:
                        pass
        except Exception as e:
            print("Failed to post confession:", e)

    success_msg = (await cfg("msg_success")).replace("{channel}", f"<#{pub_ch_id}>")
    await query(
        "INSERT INTO confessions (id, user_id, username, content, image_url, public_msg, "
        "timestamp, status, confession_number, reply_to) VALUES (?,?,?,?,?,?,?,?,?,?)",
        conf_id, user_id, username, message, image, pub_msg_id,
        datetime.utcnow().isoformat(), status, conf_num, reply_to
    )
    await interaction.followup.send(success_msg, ephemeral=True)


@bot.event
async def on_ready():
    from core.db import cfg
    gid = await cfg("guild_id", str(GUILD_ID))
    gid_int = int(gid) if gid and gid != "0" else None
    await bot.tree.sync(guild=discord.Object(id=gid_int) if gid_int else None)
    print(f"Bot logged in as {bot.user}")


# ─── Register Blueprints ──────────────────────────────────────────────────────
from core.workspace_routes import workspace_bp
from apps.confessions import confessions_bp
from apps.bump_bot import bump_bp

app.register_blueprint(workspace_bp)
app.register_blueprint(confessions_bp)
app.register_blueprint(bump_bp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
