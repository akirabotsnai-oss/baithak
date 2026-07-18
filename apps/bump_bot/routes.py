"""
apps/bump_bot/routes.py — All Auto Bumper web routes + background loop.

Supports multiple bump accounts using the bump_accounts table.
"""
import time
import random
import string
import httpx
import asyncio
from datetime import datetime

from quart import render_template, request, jsonify, session

from apps.bump_bot import bump_bp
from core.auth import require_login, require_app_access, base_ctx
from core.db import query, cfg, set_cfg
from core.crypto import encrypt_token, decrypt_token

# ─── Shared in-memory state ───────────────────────────────────────────────────

# In-memory logs per account (or global)
# We'll use a global unified log.
_bump_logs = []

def push_bump_log(account_name: str, msg: str, level: str = "info") -> None:
    ts = datetime.utcnow().strftime("%H:%M:%S")
    prefix = f"[{account_name}] " if account_name else ""
    _bump_logs.append({"t": ts, "m": f"{prefix}{msg}", "l": level})
    if len(_bump_logs) > 100:
        _bump_logs.pop(0)

# ─── Discord helpers ──────────────────────────────────────────────────────────

_HEADERS = lambda token: {
    "Authorization": token,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

async def verify_discord_user_token(token: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://discord.com/api/v10/users/@me", headers=_HEADERS(token), timeout=8)
            if r.status_code == 200:
                d = r.json()
                return f"{d['username']}#{d.get('discriminator', '0')}"
            return None
    except Exception:
        return None

async def get_discord_bump_command(token: str, guild_id: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/application-command-index",
                headers=_HEADERS(token), timeout=10
            )
            if r.status_code == 200:
                for cmd in r.json().get("application_commands", []):
                    if cmd.get("name") == "bump" and cmd.get("application_id") == "302050872383242240":
                        return cmd
            return None
    except Exception:
        return None

async def check_disboard_status() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://disboard.org/", timeout=8)
            return r.status_code < 500
    except Exception:
        return False

async def send_discord_channel_message(token: str, channel_id: str, content: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=_HEADERS(token), json={"content": content}, timeout=8
            )
            return r.status_code in (200, 201)
    except Exception:
        return False

def _generate_nonce() -> str:
    epoch_ms = int(datetime.utcnow().timestamp() * 1000) - 1420070400000
    return str((epoch_ms << 22) + random.randint(0, 4194303))

def _random_session() -> str:
    return ''.join(random.choices(string.hexdigits.lower(), k=32))

async def do_discord_bump(token: str, guild_id: str, channel_id: str, cmd: dict):
    payload = {
        "type": 2, "application_id": "302050872383242240",
        "guild_id": guild_id, "channel_id": channel_id,
        "session_id": _random_session(), "nonce": _generate_nonce(),
        "data": {
            "version": cmd["version"], "id": cmd["id"],
            "name": "bump", "type": 1,
            "options": [], "application_command": cmd, "attachments": []
        }
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://discord.com/api/v10/interactions",
                headers=_HEADERS(token), json=payload, timeout=10
            )
            if r.status_code == 204:   return True, "ok"
            if r.status_code == 429:   return False, "rate_limited"
            if r.status_code == 401:   return False, "bad_token"
            if r.status_code >= 500:   return False, "discord_down"
            return False, f"http_{r.status_code}"
    except Exception as e:
        return False, str(e)


# ─── Background Loop ──────────────────────────────────────────────────────────

_cached_cmds = {}
_notified = set()
_disboard_down_alerted = False

async def bump_bot_loop(bot):
    await bot.wait_until_ready()
    push_bump_log("System", "Multi-Account Auto-Bump Service Initialized", "info")
    COOLDOWN = 7200
    global _disboard_down_alerted

    while True:
        try:
            accounts = await query("SELECT * FROM bump_accounts WHERE is_enabled=1") or []
            if not accounts:
                await asyncio.sleep(10)
                continue

            disboard_up = await check_disboard_status()
            if not disboard_up:
                if not _disboard_down_alerted:
                    push_bump_log("System", "Disboard is down — retrying in 5 mins", "warn")
                    _disboard_down_alerted = True
                await asyncio.sleep(300)
                continue
            else:
                if _disboard_down_alerted:
                    push_bump_log("System", "Disboard recovered!", "info")
                    _disboard_down_alerted = False

            for acc in accounts:
                acc_id = acc["id"]
                name = acc["name"]
                token = decrypt_token(acc["token"])
                if not token:
                    if status != "Error: Bad Token":
                        await query("UPDATE bump_accounts SET status='Error: Bad Token' WHERE id=?", acc_id)
                    continue
                guild_id = acc["guild_id"]
                channel_id = acc["channel_id"]
                last_bump = float(acc["last_bump_time"] or 0)
                status = acc["status"]
                
                remaining = max(0.0, COOLDOWN - (time.time() - last_bump))

                if status in ("Offline", "Starting...", "Connecting...", "Error: Bad Token"):
                    await query("UPDATE bump_accounts SET status='Connecting...' WHERE id=?", acc_id)
                    username = await verify_discord_user_token(token)
                    if not username:
                        await query("UPDATE bump_accounts SET status='Error: Bad Token' WHERE id=?", acc_id)
                        continue
                    
                    if acc_id not in _cached_cmds:
                        cmd = await get_discord_bump_command(token, guild_id)
                        if cmd:
                            _cached_cmds[acc_id] = cmd
                        else:
                            await query("UPDATE bump_accounts SET status='Error: Command Fetch Failed' WHERE id=?", acc_id)
                            continue

                    await query("UPDATE bump_accounts SET status='Online' WHERE id=?", acc_id)
                    continue

                if remaining > 10:
                    if status != "Waiting":
                        await query("UPDATE bump_accounts SET status='Waiting' WHERE id=?", acc_id)
                    continue

                # Ready to bump!
                await query("UPDATE bump_accounts SET status='Bumping...' WHERE id=?", acc_id)
                cmd = _cached_cmds.get(acc_id)
                if not cmd:
                    cmd = await get_discord_bump_command(token, guild_id)
                    _cached_cmds[acc_id] = cmd
                
                if not cmd:
                    await query("UPDATE bump_accounts SET status='Error: Command Fetch Failed' WHERE id=?", acc_id)
                    continue
                
                push_bump_log(name, "Sending /bump to Discord...", "info")
                success, reason = await do_discord_bump(token, guild_id, channel_id, cmd)
                
                if success:
                    now_ts = time.time()
                    new_count = (acc["bump_count"] or 0) + 1
                    await query("UPDATE bump_accounts SET last_bump_time=?, bump_count=?, status='Waiting' WHERE id=?", str(now_ts), new_count, acc_id)
                    push_bump_log(name, f"Bump #{new_count} successful!", "bump")
                elif reason == "rate_limited":
                    push_bump_log(name, "Rate limited — waiting 5 mins", "warn")
                    await query("UPDATE bump_accounts SET status='Rate Limited' WHERE id=?", acc_id)
                elif reason == "bad_token":
                    push_bump_log(name, "Token verification failed!", "error")
                    await query("UPDATE bump_accounts SET status='Error: Bad Token' WHERE id=?", acc_id)
                else:
                    push_bump_log(name, f"Bump failed ({reason})", "warn")
                    await query("UPDATE bump_accounts SET status='Retrying...' WHERE id=?", acc_id)
                    _cached_cmds.pop(acc_id, None)

            # Prevent busy looping when no bumps are due
            await asyncio.sleep(10)
        except Exception as e:
            print("Error in bump loop:", e)
            push_bump_log("System", f"Loop exception: {e}", "error")
            await asyncio.sleep(30)


# ─── Web Routes ───────────────────────────────────────────────────────────────

@bump_bp.route("/")
@require_login
@require_app_access("bump_bot")
async def bump_dashboard():
    ctx = await base_ctx()
    return await render_template("bump.html", **ctx, active="dashboard")

@bump_bp.route("/settings")
@require_login
@require_app_access("bump_bot")
async def bump_settings():
    ctx = await base_ctx()
    return await render_template("bump.html", **ctx, active="settings")


@bump_bp.route("/api/status")
@require_login
@require_app_access("bump_bot")
async def bump_status():
    accounts = await query("SELECT * FROM bump_accounts ORDER BY id ASC") or []
    
    acc_list = []
    now = time.time()
    for acc in accounts:
        last_bump = float(acc["last_bump_time"] or 0)
        remaining = max(0.0, 7200.0 - (now - last_bump)) if last_bump else 7200.0
        acc_list.append({
            "id": acc["id"],
            "name": acc["name"],
            "is_enabled": acc["is_enabled"],
            "status": acc["status"],
            "bump_count": acc["bump_count"] or 0,
            "last_bump": last_bump,
            "remaining": remaining,
            "guild_id": acc["guild_id"],
            "channel_id": acc["channel_id"]
        })
        
    return jsonify({
        "accounts": acc_list,
        "cooldown": 7200,
        "logs": _bump_logs,
    })


@bump_bp.route("/api/accounts/add", methods=["POST"])
@require_login
@require_app_access("bump_bot")
async def add_bump_account():
    data = await request.json or {}
    name = data.get("name", "").strip()
    token = data.get("token", "").strip()
    guild_id = data.get("guild_id", "").strip()
    channel_id = data.get("channel_id", "").strip()
    
    if not name or not token or not guild_id or not channel_id:
        return jsonify({"ok": False, "error": "Missing fields"})
        
    enc_token = encrypt_token(token)
    await query(
        "INSERT INTO bump_accounts (name, token, guild_id, channel_id) VALUES (?,?,?,?)",
        name, enc_token, guild_id, channel_id
    )
    push_bump_log("System", f"Added new account: {name}", "info")
    return jsonify({"ok": True})


@bump_bp.route("/api/accounts/delete", methods=["POST"])
@require_login
@require_app_access("bump_bot")
async def delete_bump_account():
    data = await request.json or {}
    acc_id = data.get("id")
    if not acc_id: return jsonify({"ok": False})
    
    await query("DELETE FROM bump_accounts WHERE id=?", acc_id)
    return jsonify({"ok": True})


@bump_bp.route("/api/accounts/toggle", methods=["POST"])
@require_login
@require_app_access("bump_bot")
async def toggle_bump_account():
    data = await request.json or {}
    acc_id = data.get("id")
    enabled = 1 if data.get("enabled") else 0
    if not acc_id: return jsonify({"ok": False})
    
    await query("UPDATE bump_accounts SET is_enabled=?, status='Offline' WHERE id=?", enabled, acc_id)
    return jsonify({"ok": True})


@bump_bp.route("/api/bump-now", methods=["POST"])
@require_login
@require_app_access("bump_bot")
async def bump_now():
    data = await request.json or {}
    acc_id = data.get("account_id")
    if not acc_id: return jsonify({"ok": False, "error": "Missing account id"})
    
    acc = await query("SELECT * FROM bump_accounts WHERE id=?", acc_id, fetch_one=True)
    if not acc: return jsonify({"ok": False, "error": "Account not found"})
    
    token = decrypt_token(acc["token"])
    if not token: return jsonify({"ok": False, "error": "Decryption failed or missing token"})
    
    guild_id = acc["guild_id"]
    channel_id = acc["channel_id"]
    
    cmd = await get_discord_bump_command(token, guild_id)
    if not cmd:
        return jsonify({"ok": False, "error": "Disboard's /bump command not found for this account"})
        
    success, reason = await do_discord_bump(token, guild_id, channel_id, cmd)
    if success:
        now_ts = time.time()
        new_count = (acc["bump_count"] or 0) + 1
        await query("UPDATE bump_accounts SET last_bump_time=?, bump_count=?, status='Waiting' WHERE id=?", str(now_ts), new_count, acc_id)
        push_bump_log(acc["name"], "Manual bump triggered successfully!", "bump")
        return jsonify({"ok": True})
        
    return jsonify({"ok": False, "error": reason})
