"""
apps/ai_resident/routes.py — Web routes and API endpoints for the AI Resident bot.

Handles Discord OAuth2 authentication, settings config, live statistics,
and the global dashboard Environment Variable manager.
"""
import os
import urllib.parse
import httpx
import asyncio
import json
import discord
from datetime import datetime
from quart import render_template, request, session, jsonify, redirect, url_for, Response

from apps.ai_resident import ai_resident_bp
from core.auth import require_login, require_app_access, base_ctx, is_god_or_god2
from core.db import query, cfg, set_cfg, log_audit

# ─── Helper: Get Guild Configurations ─────────────────────────────────────────

async def get_guild_cfgs(guild_id: str) -> dict:
    rows = await query("SELECT key, value FROM ai_resident_guild_config WHERE guild_id=?", str(guild_id)) or []
    return {r["key"]: r["value"] for r in rows}

async def save_guild_cfg(guild_id: str, key: str, value: str):
    await query(
        "INSERT INTO ai_resident_guild_config (guild_id, key, value) VALUES (?,?,?) "
        "ON CONFLICT (guild_id, key) DO UPDATE SET value=EXCLUDED.value",
        str(guild_id), key, str(value)
    )

async def get_bot_guild_meta(gid: str) -> dict:
    """Helper to fetch server name and icon from active bot instance or Discord HTTP API."""
    from app import bot, BOT_TOKEN
    guild_obj = bot.get_guild(int(gid)) if (hasattr(bot, "get_guild") and gid.isdigit()) else None
    if not guild_obj and hasattr(bot, "ai_bot") and bot.ai_bot and gid.isdigit():
        guild_obj = bot.ai_bot.get_guild(int(gid))
    if guild_obj:
        icon_key = guild_obj.icon.key if (guild_obj.icon and hasattr(guild_obj.icon, "key")) else None
        return {"id": str(guild_obj.id), "name": guild_obj.name, "icon": icon_key}
    
    # Fallback: Query Discord HTTP API using Bot Token
    effective_token = (await cfg("BOT_TOKEN")) or BOT_TOKEN
    if effective_token and gid.isdigit():
        try:
            headers = {"Authorization": f"Bot {effective_token}"}
            async with httpx.AsyncClient() as client:
                r = await client.get(f"https://discord.com/api/v10/guilds/{gid}", headers=headers, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    return {"id": str(data["id"]), "name": data.get("name", f"Server ({gid})"), "icon": data.get("icon")}
        except Exception as e:
            print(f"[Guild Meta Fetch Error] {gid}: {e}")
            
    return {"id": str(gid), "name": f"Server ({gid})", "icon": None}

# ─── Smart Routing: Main Dashboard Index ──────────────────────────────────────

@ai_resident_bp.route("/")
@require_login
@require_app_access("ai_resident")
async def index():
    # If client credentials aren't configured yet, redirect to instructions
    client_id = await cfg("DISCORD_CLIENT_ID")
    client_secret = await cfg("DISCORD_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        ctx = await base_ctx()
        return await render_template("ai_resident_index.html", **ctx, active="ai_resident", setup_mode=True)
        
    # God Mode Admin bypass: directly show all bot active guilds without requiring Discord OAuth login!
    if is_god_or_god2():
        from app import bot
        all_bot_guilds = list(bot.guilds) if hasattr(bot, "guilds") else []
        if hasattr(bot, "ai_bot") and bot.ai_bot and hasattr(bot.ai_bot, "guilds"):
            all_bot_guilds.extend(list(bot.ai_bot.guilds))
        
        active_guilds = []
        seen = set()
        for g in all_bot_guilds:
            gid = str(g.id)
            if gid not in seen:
                seen.add(gid)
                icon_key = g.icon.key if (g.icon and hasattr(g.icon, "key")) else None
                active_guilds.append({
                    "id": gid,
                    "name": g.name,
                    "icon": icon_key,
                    "is_bot_present": True
                })

        # Also check DB configured guilds & stats so God Mode never sees an empty list
        db_guilds = await query("SELECT DISTINCT guild_id FROM ai_resident_guild_config") or []
        db_stats = await query("SELECT DISTINCT guild_id FROM ai_resident_stats") or []
        all_db_gids = set([str(x["guild_id"]) for x in (db_guilds + db_stats)])
        for gid in all_db_gids:
            if gid not in seen and gid != "dm":
                seen.add(gid)
                meta = await get_bot_guild_meta(gid)
                meta["is_bot_present"] = True
                active_guilds.append(meta)

        ctx = await base_ctx()
        return await render_template(
            "ai_resident_index.html", 
            **ctx, 
            active="ai_resident", 
            guilds=active_guilds
        )

    # Check if we already have discord token in session
    discord_token = session.get("discord_oauth_token")
    if not discord_token:
        # Regular members redirect to OAuth2 authorize
        redirect_uri = await cfg("DISCORD_REDIRECT_URI", "https://baithak-1.onrender.com/ai_resident/oauth/callback")
        encoded_uri = urllib.parse.quote(redirect_uri)
        auth_url = f"https://discord.com/api/oauth2/authorize?client_id={client_id}&redirect_uri={encoded_uri}&response_type=code&scope=identify%20guilds"
        return redirect(auth_url)
        
    # User is authenticated. Fetch user guilds from Discord.
    try:
        headers = {"Authorization": f"Bearer {discord_token}"}
        async with httpx.AsyncClient() as client:
            r = await client.get("https://discord.com/api/users/@me/guilds", headers=headers, timeout=10)
            if r.status_code == 401:
                # Token expired, clear and restart OAuth
                session.pop("discord_oauth_token", None)
                return redirect(url_for("ai_resident.index"))
            elif r.status_code != 200:
                return f"❌ Failed to fetch guilds from Discord API: HTTP {r.status_code}", 500
            
            user_guilds = r.json()
    except Exception as e:
        return f"❌ Network error while talking to Discord: {e}", 500

    # Filter guilds where the user has Manage Server (MANAGE_GUILD = 0x00000020)
    manage_guilds = []
    for g in user_guilds:
        perms = int(g.get("permissions", 0))
        is_owner = g.get("owner", False)
        if is_owner or (perms & 0x00000020) == 0x00000020:
            manage_guilds.append(g)

    # Cross-reference with the active guilds the Bot is currently in
    # (Importing bot from main app module)
    from app import bot
    bot_guild_ids = [str(guild.id) for guild in bot.guilds]
    
    # Check if second bot instance is active and merge guilds
    if hasattr(bot, "ai_bot") and bot.ai_bot:
        bot_guild_ids.extend([str(guild.id) for guild in bot.ai_bot.guilds])
    
    bot_guild_ids = list(set(bot_guild_ids))

    active_guilds = []
    for g in manage_guilds:
        g_id = str(g["id"])
        g["is_bot_present"] = g_id in bot_guild_ids
        active_guilds.append(g)

    ctx = await base_ctx()
    return await render_template(
        "ai_resident_index.html", 
        **ctx, 
        active="ai_resident", 
        guilds=active_guilds
    )

# ─── Discord OAuth2 Callback ──────────────────────────────────────────────────

@ai_resident_bp.route("/oauth/callback")
@require_login
async def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "❌ Missing authorization code from Discord.", 400

    client_id = await cfg("DISCORD_CLIENT_ID")
    client_secret = await cfg("DISCORD_CLIENT_SECRET")
    redirect_uri = await cfg("DISCORD_REDIRECT_URI", "https://baithak-1.onrender.com/ai_resident/oauth/callback")

    try:
        async with httpx.AsyncClient() as client:
            # Exchange code for access token
            data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            r = await client.post("https://discord.com/api/oauth2/token", data=data, headers=headers, timeout=10)
            if r.status_code != 200:
                return f"❌ Token exchange failed: {r.text}", 400
                
            res = r.json()
            session["discord_oauth_token"] = res["access_token"]
            return redirect(url_for("ai_resident.index"))
    except Exception as e:
        return f"❌ OAuth2 exchange error: {e}", 500

# ─── Guild Settings Screen ────────────────────────────────────────────────────

@ai_resident_bp.route("/guild/<guild_id>")
@require_login
@require_app_access("ai_resident")
async def guild_settings(guild_id):
    is_god = is_god_or_god2()
    discord_token = session.get("discord_oauth_token")

    guild_meta = None
    if discord_token:
        try:
            headers = {"Authorization": f"Bearer {discord_token}"}
            async with httpx.AsyncClient() as client:
                r = await client.get("https://discord.com/api/users/@me/guilds", headers=headers, timeout=10)
                if r.status_code == 200:
                    for g in r.json():
                        if str(g["id"]) == str(guild_id):
                            perms = int(g.get("permissions", 0))
                            is_owner = g.get("owner", False)
                            if is_owner or (perms & 0x00000020) == 0x00000020:
                                guild_meta = g
                                break
        except Exception:
            pass

    if not guild_meta:
        if is_god_or_god2():
            guild_meta = await get_bot_guild_meta(guild_id)
        else:
            return redirect(url_for("workspace.access_denied"))

    # Fetch configuration and active stats
    guild_config = await get_guild_cfgs(guild_id)
    
    # Get active channels in this guild to display in the checklist
    from app import bot
    channels_list = []
    
    guild_obj = bot.get_guild(int(guild_id))
    if not guild_obj and hasattr(bot, "ai_bot") and bot.ai_bot:
        guild_obj = bot.ai_bot.get_guild(int(guild_id))
        
    if guild_obj:
        for channel in guild_obj.text_channels:
            channels_list.append({
                "id": str(channel.id),
                "name": channel.name
            })
            
    stats = await query("SELECT * FROM ai_resident_stats WHERE guild_id=?", str(guild_id), fetch_one=True) or {
        "messages_seen": 0,
        "replies_sent": 0
    }

    # Fetch learned style slang
    style_row = await query("SELECT slang_words, accent_notes FROM ai_resident_style_notes WHERE guild_id=?", str(guild_id), fetch_one=True)

    ctx = await base_ctx()
    return await render_template(
        "ai_resident_guild.html",
        **ctx,
        active="ai_resident",
        guild_id=guild_id,
        guild_name=guild_meta["name"],
        guild_icon=guild_meta.get("icon"),
        guild_cfg=lambda k, d="": guild_config.get(k, d) if guild_config.get(k) is not None else d,
        channels=channels_list,
        stats=stats,
        style_row=style_row
    )

# ─── Guild Settings Save API ──────────────────────────────────────────────────

@ai_resident_bp.route("/api/guild/<guild_id>/save", methods=["POST"])
@require_login
@require_app_access("ai_resident")
async def save_guild_settings(guild_id):
    # Verify manage guild rights
    discord_token = session.get("discord_oauth_token")
    if not discord_token:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
        
    req_data = await request.json or {}
    
    # Save settings per key
    for k, v in req_data.items():
        await save_guild_cfg(guild_id, k, v)
        
    await log_audit(session["user"], "ai_resident_settings_update", f"Updated settings for guild {guild_id}")
    return jsonify({"ok": True})

# ─── God Mode: Environment Variable Panel ─────────────────────────────────────

@ai_resident_bp.route("/admin/env", methods=["GET", "POST"])
@require_login
@require_app_access("ai_resident")
async def global_env():
    # Only allow God / God2
    if not is_god_or_god2():
        return redirect(url_for("workspace.access_denied"))
        
    success_msg = None
    if request.method == "POST":
        form = await request.form
        
        # Save all config keys to config_store
        keys_to_save = [
            "BOT_TOKEN", "AI_BOT_TOKEN", "GROQ_API_KEY", "GEMINI_API_KEY",
            "OPENAI_API_KEY", "STABILITY_API_KEY", "REPLICATE_API_TOKEN",
            "DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DISCORD_REDIRECT_URI",
            "admin_channel_id", "guild_id"
        ]
        
        for k in keys_to_save:
            # Retrieve all values associated with this input name
            vals = form.getlist(k)
            vals = [v.strip() for v in vals if v.strip()]
            
            if len(vals) == 0:
                val = ""
            elif len(vals) == 1:
                val = vals[0]
            else:
                # Store list of multiple keys as JSON list
                val = json.dumps(vals)
                
            old_val = await cfg(k)
            if old_val != val:
                await set_cfg(k, val)

        # Dynamic AI Bot launcher if AI_BOT_TOKEN was updated
        effective_ai_token = await cfg("AI_BOT_TOKEN")
        if effective_ai_token and len(effective_ai_token) > 20:
            from app import bot
            if not getattr(bot, "ai_bot", None) or getattr(bot.ai_bot, "is_closed", lambda: True)():
                try:
                    import discord
                    from discord.ext import commands
                    from apps.ai_resident.cog import AIResidentCog
                    
                    ai_intents = discord.Intents.default()
                    ai_intents.message_content = True
                    ai_intents.members = True
                    
                    ai_bot = commands.Bot(command_prefix="?", intents=ai_intents)
                    bot.ai_bot = ai_bot
                    await ai_bot.add_cog(AIResidentCog(ai_bot))
                    asyncio.create_task(ai_bot.start(effective_ai_token))
                    print(f"[AI Resident Dynamic Launch] Successfully launched bablu bot!")
                except Exception as e:
                    print(f"[AI Resident Dynamic Launch Error]: {e}")
                
        await log_audit(session["user"], "ai_resident_env_update", "Updated system environment keys")
        success_msg = "✅ Environment configuration updated successfully!"

    # Render settings editor
    ctx = await base_ctx()
    return await render_template(
        "ai_resident_env.html",
        **ctx,
        active="ai_resident",
        success_msg=success_msg
    )

# ─── God Mode: Bot Username and Avatar Profile Changer API ───────────────────

@ai_resident_bp.route("/api/bot/profile", methods=["POST"])
@require_login
@require_app_access("ai_resident")
async def bot_profile_update():
    if not is_god_or_god2():
        return jsonify({"ok": False, "error": "Only God/Byte administrators can edit profiles"}), 403

    form = await request.form
    files = await request.files

    bot_type = form.get("bot_type", "ai")  # 'main' or 'ai'
    if bot_type != "ai":
        return jsonify({"ok": False, "error": "Only the AI Resident bot profile can be modified here."}), 400

    username = form.get("username", "").strip()

    from app import bot
    target_bot = bot.ai_bot if bot_type == "ai" and bot.ai_bot else bot

    if not target_bot:
        return jsonify({"ok": False, "error": f"Discord client for '{bot_type}' bot is offline or not configured in dual mode."}), 400

    avatar_file = files.get("avatar")
    avatar_bytes = None
    if avatar_file and avatar_file.filename:
        avatar_bytes = avatar_file.read()

    try:
        # Edit username if requested
        if username and username != target_bot.user.name:
            await target_bot.user.edit(username=username)
        # Edit avatar if requested
        if avatar_bytes:
            await target_bot.user.edit(avatar=avatar_bytes)

        await log_audit(session["user"], "bot_profile_update", f"Updated {bot_type} Discord bot profile (Name: {username or 'unchanged'})")
        return jsonify({"ok": True})
        
    except discord.HTTPException as e:
        if e.status == 429:
            return jsonify({"ok": False, "error": "Rate limit: Discord allows changing bot name/avatar max 2 times per hour. Please wait!"}), 429
        return jsonify({"ok": False, "error": f"Discord API Error: {e.text}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─── God Mode: Live Bot Status Check API ─────────────────────────────────────

@ai_resident_bp.route("/api/bot/status", methods=["GET"])
@require_login
async def bot_status():
    from app import bot
    main_status = {
        "online": bot.is_ready(),
        "user": str(bot.user) if bot.user else None,
        "guilds": len(bot.guilds) if bot.is_ready() else 0,
        "latency_ms": round(bot.latency * 1000, 1) if bot.is_ready() else None,
    }
    ai_bot = getattr(bot, "ai_bot", None)
    ai_status = None
    if ai_bot:
        ai_status = {
            "online": ai_bot.is_ready(),
            "user": str(ai_bot.user) if ai_bot.user else None,
            "guilds": len(ai_bot.guilds) if ai_bot.is_ready() else 0,
            "latency_ms": round(ai_bot.latency * 1000, 1) if ai_bot.is_ready() else None,
            "closed": ai_bot.is_closed(),
        }

    ai_token_set = bool(await cfg("AI_BOT_TOKEN"))
    
    return jsonify({
        "main_bot": main_status,
        "ai_bot": ai_status,
        "dual_bot_mode": ai_bot is not None,
        "ai_token_configured": ai_token_set,
    })
