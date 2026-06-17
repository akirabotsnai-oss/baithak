import os
from dotenv import load_dotenv

load_dotenv()
import asyncio
import base64
import secrets
from datetime import datetime, timedelta
from functools import wraps
import json
import re

import bcrypt
import httpx
from quart import Quart, request, session, redirect, url_for, render_template, jsonify, flash, Response
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg

# ─── Configuration & Setup ───────────────────────────────────────────────────
app = Quart(__name__, template_folder='.')
app.secret_key = os.environ.get("FLASK_SECRET") or secrets.token_hex(32)

DATABASE_URL = os.environ.get("DATABASE_URL")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
SECRET_PATH = os.environ.get("SECRET_PATH", "cmd-9x4k2")
PUBLIC_CHANNEL_ID = os.environ.get("PUBLIC_CHANNEL_ID", "")
ADMIN_CHANNEL_ID = os.environ.get("ADMIN_CHANNEL_ID", "")
GUILD_ID = int(os.environ.get("GUILD_ID", 0)) if os.environ.get("GUILD_ID") else 0

db_pool = None

# Discord Bot Setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@app.before_serving
async def startup():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
    asyncio.create_task(bot.start(BOT_TOKEN)) if BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" else None

# ─── Database Helpers (SQLite -> Postgres Adapter) ───────────────────────────
async def query(sql, *args, fetch_one=False):
    if not db_pool: return None
    # Convert ? to $1, $2, etc for postgres
    parts = sql.split('?')
    new_sql = parts[0]
    for i in range(1, len(parts)):
        new_sql += f"${i}" + parts[i]
    
    async with db_pool.acquire() as con:
        if sql.strip().upper().startswith(("SELECT", "RETURNING")):
            if fetch_one:
                return await con.fetchrow(new_sql, *args)
            return await con.fetch(new_sql, *args)
        else:
            return await con.execute(new_sql, *args)

_config_cache = {}
_config_cache_time = None

async def cfg(key, default=""):
    global _config_cache_time
    # Invalidate cache every 5 minutes automatically
    if not _config_cache_time or (datetime.utcnow() - _config_cache_time).total_seconds() > 300:
        _config_cache.clear()
        _config_cache_time = datetime.utcnow()
        rows = await query("SELECT key, value FROM config_store")
        if rows:
            for r in rows: _config_cache[r["key"]] = r["value"]
    
    return _config_cache.get(key, default)

async def set_cfg(key, value):
    await query("INSERT INTO config_store (key,value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", key, str(value))
    _config_cache[key] = str(value)

async def log_audit(username, action, details=""):
    await query("INSERT INTO audit_logs (username,action,details,timestamp) VALUES (?,?,?,?)", username, action, details, datetime.utcnow().isoformat())

# ─── Auth Helpers ────────────────────────────────────────────────────────────
_login_attempts = {}

ROLE_GOD = 'god'
ROLE_GOD2 = 'god2'
ROLE_ADMIN = 'admin'

def current_role():
    if session.get('is_main') or session.get('user') == 'byte':
        return ROLE_GOD
    return session.get('role') or ROLE_ADMIN

def is_god(): return current_role() == ROLE_GOD
def is_god_or_god2(): return current_role() in (ROLE_GOD, ROLE_GOD2)

def require_login(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if "user" not in session: return redirect(url_for("login"))
        return await f(*args, **kwargs)
    return decorated

def require_god(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if not is_god(): return redirect(url_for("overview"))
        return await f(*args, **kwargs)
    return decorated

async def base_ctx():
    role = current_role()
    qcount = await query("SELECT COUNT(*) FROM confessions WHERE status='quarantine' AND deleted_at IS NULL", fetch_one=True)
    bcount = await query("SELECT COUNT(*) FROM confessions WHERE deleted_at IS NOT NULL", fetch_one=True)
    return dict(
        user=session.get("user", ""),
        is_main=session.get("is_main", False),
        role=role,
        can_export=role in (ROLE_GOD, ROLE_GOD2),
        can_perm_delete=role in (ROLE_GOD, ROLE_GOD2),
        can_recycle_bin=role in (ROLE_GOD, ROLE_GOD2),
        can_settings=role in (ROLE_GOD, ROLE_GOD2),
        can_branding=role == ROLE_GOD,
        can_manage_admins=role == ROLE_GOD,
        bot_pfp=await cfg("bot_pfp_url"),
        bot_name=await cfg("bot_name", "Confession Bot"),
        embed_color=await cfg("embed_color", "5865F2"),
        q_count=qcount[0] if qcount else 0,
        bin_count=bcount[0] if bcount else 0,
        embed_title=await cfg("embed_title", "Anonymous Confession"),
        embed_footer=await cfg("embed_footer", "Confession | React below!"),
        cfg=lambda k, d="": _config_cache.get(k, d) if _config_cache.get(k) is not None else d
    )

# ─── Intrusion Tracking ───────────────────────────────────────────────────────
last_leak_alert = None

@app.before_request
async def track_visitor():
    if request.path == f"/{SECRET_PATH}" or request.path == f"/{SECRET_PATH}/":
        if "user" not in session:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "Unknown").split(',')[0].strip()
            ua = request.headers.get("User-Agent", "")
            ref = request.headers.get("Referer", "")
            
            await query("INSERT INTO visitor_logs (ip_address, user_agent, referer, timestamp) VALUES (?,?,?,?)", ip, ua, ref, datetime.utcnow().isoformat())
            
            # Leak Detection (Spike of 3 unique IPs in 5 mins)
            five_mins_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
            unique_ips = await query("SELECT COUNT(DISTINCT ip_address) FROM visitor_logs WHERE timestamp > ?", five_mins_ago, fetch_one=True)
            
            if unique_ips and unique_ips[0] >= 3:
                global last_leak_alert
                if not last_leak_alert or (datetime.utcnow() - last_leak_alert).total_seconds() > 300:
                    last_leak_alert = datetime.utcnow()
                    admin_ch_id = await cfg("admin_channel_id", ADMIN_CHANNEL_ID)
                    if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" and admin_ch_id:
                        embed = {
                            "title": "🚨 SECURITY ALERT: Possible Link Leak", 
                            "description": f"The dashboard login page has been hit by **{unique_ips[0]} unique IPs** in the last 5 minutes.\n\nLatest Referer: `{ref or 'Direct Link'}`\n\nCheck the **Security Logs** tab in the Command Center immediately.", 
                            "color": 16711680, 
                            "timestamp": datetime.utcnow().isoformat() + "Z"
                        }
                        async with httpx.AsyncClient() as client:
                            await client.post(f"https://discord.com/api/v10/channels/{admin_ch_id}/messages", headers={"Authorization": f"Bot {BOT_TOKEN}"}, json={"content": "@everyone Possible Dashboard Leak Detected!", "embeds": [embed]})

@app.after_request
async def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# ─── Web Routes ──────────────────────────────────────────────────────────────
@app.route(f"/{SECRET_PATH}", methods=["GET", "POST"])
@app.route(f"/{SECRET_PATH}/", methods=["GET", "POST"])
async def login():
    if "user" in session: return redirect(url_for("overview"))
    error = None
    if request.method == "POST":
        form = await request.form
        username, password = form.get("username", "").strip(), form.get("password", "").strip()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "Unknown").split(',')[0].strip()
        rec = _login_attempts.get(ip, {"count": 0, "locked_until": None})
        if rec["locked_until"] and datetime.utcnow() < rec["locked_until"]:
            error = "Locked out."
        else:
            if rec.get("locked_until") and datetime.utcnow() >= rec["locked_until"]:
                _login_attempts[ip] = {"count": 0, "locked_until": None}
            row = await query("SELECT password_hash, is_main_admin, is_revoked, role FROM admins WHERE username=?", username, fetch_one=True)
            if row and not row["is_revoked"] and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
                session.update({"user": username, "is_main": bool(row["is_main_admin"]), "role": row["role"] or ROLE_ADMIN})
                _login_attempts.pop(ip, None)
                await query("UPDATE admins SET last_login=? WHERE username=?", datetime.utcnow().isoformat(), username)
                await log_audit(username, "login", f"Login from {ip}")
                return redirect(url_for("overview"))
            else:
                count = rec["count"] + 1
                _login_attempts[ip] = {"count": count, "locked_until": datetime.utcnow() + timedelta(minutes=10) if count >= 5 else None}
                error = "Invalid credentials"
    return await render_template("index.html", active="login", error=error, bot_pfp=await cfg("bot_pfp_url"), bot_name=await cfg("bot_name", "Confession Bot"))

@app.route("/logout")
async def logout():
    if "user" in session: await log_audit(session["user"], "logout", "Admin logged out")
    session.clear()
    return redirect(url_for("login"))

@app.route("/overview")
@require_login
async def overview():
    today = datetime.utcnow().date().isoformat()
    days = [(datetime.utcnow() - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
    start_date = days[0]
    
    results = await asyncio.gather(
        query("SELECT COUNT(*) FROM confessions", fetch_one=True),
        query("SELECT COUNT(*) FROM confessions WHERE status='quarantine'", fetch_one=True),
        query("SELECT COUNT(*) FROM banned_users", fetch_one=True),
        query("SELECT COUNT(*) FROM confessions WHERE timestamp LIKE ?", today+"%", fetch_one=True),
        query("SELECT id, content, status, timestamp FROM confessions ORDER BY timestamp DESC LIMIT 6"),
        query("SELECT SUBSTR(timestamp, 1, 10) as d, COUNT(*) as c FROM confessions WHERE timestamp >= ? GROUP BY SUBSTR(timestamp, 1, 10)", start_date),
        base_ctx()
    )
    total, qcount, banned, today_c, recent, chart_data, ctx = results
    
    chart_dict = {row['d']: row['c'] for row in chart_data} if chart_data else {}
    chart_counts = [chart_dict.get(d, 0) for d in days]
    chart_days = [d[5:] for d in days]
    
    return await render_template("index.html", **ctx, active="overview", total=total[0], qcount=qcount[0], banned=banned[0], today_c=today_c[0], chart_days=chart_days, chart_counts=chart_counts, recent=recent)

@app.route("/feed")
@require_login
async def feed():
    page = int(request.args.get("page", 1))
    limit, offset = 50, (page - 1) * 50
    results = await asyncio.gather(
        query("SELECT * FROM confessions WHERE deleted_at IS NULL ORDER BY timestamp DESC LIMIT ? OFFSET ?", limit, offset),
        query("SELECT COUNT(*) FROM confessions WHERE deleted_at IS NULL", fetch_one=True),
        base_ctx()
    )
    rows, total_res, ctx = results
    total = total_res[0]
    return await render_template("index.html", **ctx, active="feed", confessions=rows, page=page, pages=max(1, (total + limit - 1) // limit))

@app.route("/recycle-bin")
@require_login
async def recycle_bin():
    rows = await query("SELECT * FROM confessions WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="recycle_bin", confessions=rows, thirty_days_ago=(datetime.utcnow() - timedelta(days=30)).date().isoformat())

@app.route("/quarantine")
@require_login
async def quarantine():
    rows = await query("SELECT * FROM confessions WHERE status='quarantine' ORDER BY timestamp DESC")
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="quarantine", confessions=rows)

@app.route("/bans")
@require_login
async def bans():
    rows = await query("SELECT * FROM banned_users ORDER BY banned_at DESC")
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="bans", bans=rows)

@app.route("/user-info")
@require_login
async def user_info():
    q = request.args.get("q", "").strip()
    profile, history, is_banned = None, [], False
    if q:
        row = await query("SELECT user_id, username, COUNT(*) as total, MIN(timestamp) as first_seen, MAX(timestamp) as last_seen FROM confessions WHERE user_id=? OR username LIKE ? GROUP BY user_id, username", q, f"%{q}%", fetch_one=True)
        if row:
            ban = await query("SELECT * FROM banned_users WHERE user_id=?", row["user_id"], fetch_one=True)
            history = await query("SELECT * FROM confessions WHERE user_id=? ORDER BY timestamp DESC", row["user_id"])
            is_banned = ban is not None
            profile = {"user_id": row["user_id"], "username": row["username"], "total": row["total"], "first_seen": row["first_seen"], "last_seen": row["last_seen"], "ban": dict(ban) if ban else None}
        else:
            # Check if banned even if no confessions
            ban = await query("SELECT * FROM banned_users WHERE user_id=?", q, fetch_one=True)
            is_banned = ban is not None
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="user_info", q=q, profile=profile, history=history, is_banned=is_banned, confessions=history)

def safe_int(val, default=0):
    try: return int(val)
    except (ValueError, TypeError): return default

@app.route("/settings")
@require_login
async def settings():
    words = [r[0] for r in await query("SELECT word FROM blacklist_words")]
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="settings", blacklist=words, enabled=await cfg("enabled","1")=="1", cooldown=safe_int(await cfg("cooldown"), 0), slowdown=safe_int(await cfg("slowdown"), 0), min_age=safe_int(await cfg("min_account_age_days"), 0), msg_success=await cfg("msg_success"), msg_cooldown=await cfg("msg_cooldown"), msg_shadowban=await cfg("msg_shadowban"), msg_paused=await cfg("msg_paused"), msg_tooyoung=await cfg("msg_tooyoung"), guild_id=await cfg("guild_id", str(GUILD_ID)), public_channel_id=await cfg("public_channel_id", PUBLIC_CHANNEL_ID), admin_channel_id=await cfg("admin_channel_id", ADMIN_CHANNEL_ID))

@app.route("/audit")
@require_login
async def audit():
    action_f, admin_f = request.args.get("action", ""), request.args.get("admin", "")
    q_sql, params = "SELECT * FROM audit_logs WHERE 1=1", []
    if not is_god():
        god_row = await query("SELECT username FROM admins WHERE role='god'", fetch_one=True)
        if god_row:
            q_sql += " AND username != ?"; params.append(god_row["username"])
    if action_f: q_sql += " AND action LIKE ?"; params.append(f"%{action_f}%")
    if admin_f: q_sql += " AND username=?"; params.append(admin_f)
    q_sql += " ORDER BY timestamp DESC LIMIT 500"
    logs = await query(q_sql, *params)
    admins = [r[0] for r in await query("SELECT username FROM admins")]
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="audit", logs=logs, admins=admins, action_filter=action_f, admin_filter=admin_f)

@app.route("/admin-manager")
@require_login
@require_god
async def admin_manager():
    admins = await query("SELECT * FROM admins ORDER BY role, username")
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="admin_manager", admins=admins)

@app.route("/security")
@require_login
async def security():
    if current_role() not in (ROLE_GOD, ROLE_GOD2):
        return redirect(url_for("overview"))
    logs = await query("SELECT * FROM visitor_logs ORDER BY timestamp DESC LIMIT 500")
    ctx = await base_ctx()
    return await render_template("index.html", **ctx, active="security", logs=logs)

# ─── API Routes ──────────────────────────────────────────────────────────────
@app.route("/api/<path:path>", methods=["POST", "GET"])
@require_login
async def api_catchall(path):
    # Condense all simple API routes to save lines.
    try:
        req = await request.json if request.method == "POST" else {}
        if req is None: req = {}
    except Exception:
        req = {}
    u = session.get("user")
    
    if path.startswith("confession/"):
        cid = path.split("/")[1]
        action = path.split("/")[2] if len(path.split("/")) > 2 else ""
        if action == "soft-delete":
            await query("UPDATE confessions SET deleted_at=? WHERE id=?", datetime.utcnow().isoformat(), cid)
            await log_audit(u, "soft_delete", f"#{cid}")
            return jsonify({"ok": True})
        if action == "restore":
            await query("UPDATE confessions SET deleted_at=NULL WHERE id=?", cid)
            return jsonify({"ok": True})
        if action == "permanent-delete":
            await query("DELETE FROM confessions WHERE id=?", cid)
            return jsonify({"ok": True})
        if action == "star":
            r = await query("SELECT is_starred FROM confessions WHERE id=?", cid, fetch_one=True)
            if r:
                new_v = 0 if r["is_starred"] else 1
                await query("UPDATE confessions SET is_starred=? WHERE id=?", new_v, cid)
                return jsonify({"ok": True, "starred": bool(new_v)})
        if action == "note":
            parts = path.split("/")
            if len(parts) > 4 and parts[4] == "delete":
                await query("DELETE FROM admin_notes WHERE id=?", int(parts[3]))
            else:
                await query("INSERT INTO admin_notes (confession_id, note, added_by, timestamp) VALUES (?,?,?,?)", cid, req.get("note"), u, datetime.utcnow().isoformat())
            return jsonify({"ok": True})
        if action == "shadowban":
            r = await query("SELECT user_id FROM confessions WHERE id=?", cid, fetch_one=True)
            if r:
                await query("INSERT INTO banned_users (user_id,banned_by,reason,banned_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", r["user_id"], u, req.get("reason"), datetime.utcnow().isoformat())
                await query("UPDATE confessions SET deleted_at=? WHERE id=?", datetime.utcnow().isoformat(), cid)
            return jsonify({"ok": True})
        if not action:
            c = await query("SELECT * FROM confessions WHERE id=?", cid, fetch_one=True)
            n = await query("SELECT * FROM admin_notes WHERE confession_id=? ORDER BY timestamp DESC", cid)
            return jsonify({"ok": bool(c), "confession": dict(c) if c else None, "notes": [dict(x) for x in n] if n else []})
            
    if path.startswith("user/"):
        uid, action = path.split("/")[1], path.split("/")[2]
        if action == "ban":
            await query("INSERT INTO banned_users (user_id,banned_by,reason,banned_at) VALUES (?,?,?,?) ON CONFLICT (user_id) DO UPDATE SET reason=EXCLUDED.reason", uid, u, req.get("reason"), datetime.utcnow().isoformat())
            return jsonify({"ok": True})
        if action == "unban":
            await query("DELETE FROM banned_users WHERE user_id=?", uid)
            return jsonify({"ok": True})
            
    if path.startswith("quarantine/"):
        cid, action = path.split("/")[1], path.split("/")[2]
        if action == "approve":
            await query("UPDATE confessions SET status='posted', deleted_at=NULL WHERE id=?", cid)
            row = await query("SELECT * FROM confessions WHERE id=?", cid, fetch_one=True)
            pub_ch_id = await cfg("public_channel_id", PUBLIC_CHANNEL_ID)
            if BOT_TOKEN and pub_ch_id:
                emb_color = int((await cfg("embed_color", "5865F2")).replace("#", ""), 16)
                embed = {"title": await cfg("embed_title"), "description": row["content"], "color": emb_color, "timestamp": datetime.utcnow().isoformat() + "Z", "footer": {"text": f"#{row['confession_number']}"}}
                if row["image_url"]: embed["image"] = {"url": row["image_url"]}
                async with httpx.AsyncClient() as client:
                    await client.post(f"https://discord.com/api/v10/channels/{pub_ch_id}/messages", headers={"Authorization": f"Bot {BOT_TOKEN}"}, json={"embeds": [embed]})
            return jsonify({"ok": True})
        if action == "reject":
            r = await query("SELECT user_id FROM confessions WHERE id=?", cid, fetch_one=True)
            if r: await query("INSERT INTO banned_users (user_id,banned_by,reason,banned_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", r["user_id"], u, req.get("reason"), datetime.utcnow().isoformat())
            await query("UPDATE confessions SET deleted_at=? WHERE id=?", datetime.utcnow().isoformat(), cid)
            return jsonify({"ok": True})
            
    if path.startswith("settings/"):
        s = path.split("/")[1]
        if s in ["system", "embed", "messages", "server"]:
            for k, v in req.items(): await set_cfg(k, v)
            return jsonify({"ok": True})
        if s == "bot-identity":
            for k in ["bot_name", "bot_pfp_url"]:
                if k in req: await set_cfg(k, req[k])
            return jsonify({"ok": True})
        if s == "blacklist":
            action = path.split("/")[2]
            if action == "add": await query("INSERT INTO blacklist_words (word) VALUES (?) ON CONFLICT DO NOTHING", req.get("word").lower())
            if action == "remove": await query("DELETE FROM blacklist_words WHERE word=?", req.get("word").lower())
            return jsonify({"ok": True})

    if path.startswith("admin/"):
        if not is_god(): return jsonify({"error": "Unauthorized"})
        parts = path.split("/")
        if parts[1] == "create":
            un, pw, role = req.get("username"), req.get("password"), req.get("role")
            if await query("SELECT 1 FROM admins WHERE username=?", un, fetch_one=True):
                return jsonify({"error": "User exists"})
            h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
            await query("INSERT INTO admins (username,password_hash,role,created_by) VALUES (?,?,?,?)", un, h, role, u)
            await log_audit(u, "admin_create", un)
            return jsonify({"ok": True})
            
        target_un = parts[1]
        action = parts[2] if len(parts) > 2 else ""
        
        if action == "role":
            if target_un == 'byte': return jsonify({"error": "Cannot edit system admin"})
            await query("UPDATE admins SET role=? WHERE username=?", req.get("role"), target_un)
            return jsonify({"ok": True})
        if action == "reset-password":
            h = bcrypt.hashpw(req.get("password").encode(), bcrypt.gensalt()).decode()
            await query("UPDATE admins SET password_hash=? WHERE username=?", h, target_un)
            return jsonify({"ok": True})
        if action == "revoke":
            if target_un == 'byte': return jsonify({"error": "Cannot revoke system admin"})
            r = await query("SELECT is_revoked FROM admins WHERE username=?", target_un, fetch_one=True)
            if r:
                nv = 0 if r["is_revoked"] else 1
                await query("UPDATE admins SET is_revoked=? WHERE username=?", nv, target_un)
                return jsonify({"ok": True, "revoked": bool(nv)})

    return jsonify({"ok": False})

@app.route("/feed/export")
@require_login
async def export_feed():
    rows = await query("SELECT confession_number, id, username, user_id, content, status, timestamp FROM confessions WHERE deleted_at IS NULL ORDER BY confession_number")
    import csv as csv_mod, io
    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow(["#", "ID", "Username", "User ID", "Content", "Status", "Timestamp"])
    for r in rows:
        content = str(r["content"])
        if content and content[0] in ('=', '+', '-', '@'): content = "'" + content
        writer.writerow([r["confession_number"], r["id"], r["username"], r["user_id"], content, r["status"], r["timestamp"]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=confessions.csv"})

# ─── Discord Bot ─────────────────────────────────────────────────────────────
import string, random
def generate_id(): return "C-" + secrets.token_hex(4)

@bot.tree.command(name="confess", description="Submit an anonymous message into the void")
async def confess(interaction: discord.Interaction, message: str, image: str = None):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
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
            return await interaction.followup.send((await cfg("msg_cooldown")).replace("{wait}", str(int(eff_cd - elapsed))), ephemeral=True)

    await query("INSERT INTO cooldowns (user_id, last_used) VALUES (?,?) ON CONFLICT (user_id) DO UPDATE SET last_used=EXCLUDED.last_used", user_id, datetime.utcnow().isoformat())

    words = [r[0].lower() for r in await query("SELECT word FROM blacklist_words")]
    status = 'quarantine' if any(w in message.lower() for w in words) else 'posted'

    conf_id = generate_id()
    max_num = (await query("SELECT MAX(confession_number) FROM confessions", fetch_one=True))[0]
    conf_num = (max_num or 0) + 1

    reply_match = re.match(r'^reply to #(\d+):?', message, re.IGNORECASE)
    reply_to = reply_match.group(1) if reply_match else None

    pub_msg_id = None
    pub_ch_id = await cfg("public_channel_id", PUBLIC_CHANNEL_ID)
    if status == 'posted' and pub_ch_id:
        emb_color = int(await cfg("embed_color", "5865F2"), 16)
        embed = discord.Embed(title=await cfg("embed_title"), description=message, color=emb_color, timestamp=datetime.utcnow())
        if image: embed.set_image(url=image)
        embed.set_footer(text=f"#{conf_num} • {await cfg('embed_footer')}")
        try:
            pub_channel = bot.get_channel(int(pub_ch_id))
            pub_msg = await pub_channel.send(embed=embed)
            for e in ["👍", "👎", "❤️"]: await pub_msg.add_reaction(e)
            pub_msg_id = str(pub_msg.id)
        except Exception as e: print("Failed to post:", e)

    await query("INSERT INTO confessions (id, user_id, username, content, image_url, public_msg, timestamp, status, confession_number, reply_to) VALUES (?,?,?,?,?,?,?,?,?,?)", conf_id, user_id, username, message, image, pub_msg_id, datetime.utcnow().isoformat(), status, conf_num, reply_to)
    await interaction.followup.send(await cfg("msg_success"), ephemeral=True)

@bot.event
async def on_ready():
    gid = await cfg("guild_id", str(GUILD_ID))
    gid_int = int(gid) if gid and gid != "0" else None
    await bot.tree.sync(guild=discord.Object(id=gid_int) if gid_int else None)
    print(f"Bot logged in as {bot.user}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
