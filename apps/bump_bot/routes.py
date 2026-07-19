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

RANDOM_QUOTES = [
    "Enjoy the bump. Or don't. 🫠",
    "Doing the dirty work so you don't have to. 🦧🪠",
    "Useless bump incoming. 📉📈📉",
    "Kyun hi kar raha hu main ye. 🥸",
    "Ah shit, here we go again. 🏃‍♂️💨🎪",
    "Here, have a stupid bump. 🥔",
    "Server is dead, but here’s a bump anyway. 💀🎺",
    "Bot ka ghulam. 🤖⛓️🤪",
    "Done. Happy now? 👁️👄👁️",
    "Hourly reminder that we exist. Sadly. ⏰🙃",
    "Click it. I dare you. 🫵👹",
    "Bumping the void. 🕳️🤸‍♂️",
    "Waste of keystrokes. ⌨️🐒",
    "Insert clever bump line here. 🤡✍️",
    "Just another brick in the wall. 🧱🦎",
    "RIP this server. *bumps* 🪦🕺",
    "Modern day slavery. 🔗🤠",
    "Bootleg bump. 📦🥴",
    "Don't look at me, look at the bot. 🤖👁️👃👁️",
    "Living for the bumps. Not. 💤🦖",
    "Just passing through with this trash. 🗑️🦝",
    "Yeh lo, tumhara bump. 🤲🍍",
    "Faltu ka kaam number 1. 👎🦥",
    "Ab khush? 😤💅",
    "Kisko parwah hai waise bhi. 🤷‍♂️🥔",
    "Main chala, bump karke. 🏃‍♂️💨💨",
    "Can we get some active members already? 🧙‍♂️🔮",
    "Koi toh aao re. 😭🤌",
    "Dead server, alive bump. 🧟‍♂️✨",
    "Time waste unlimited. ⏳🤪",
    "Bumping this before it gets buried alive. 🪓🦆",
    "Khel khatam, paisa hazam. 🎪🍿",
    "Ek aur koshish. 🔄🤡",
    "Bye bye, see you in 2 hours. 👋🛸",
    "Khuda hafiz server. 🫡📉",
    "Yeh bik gayi hai gormint. 🏛️💸🤪",
    "Sannata todne ki koshish. 📯🫨",
    "Bas kar do click. 🖱️💥🦀",
    "Over and out. 📻🦖",
    "Koshish karne waalo ki haar nahi hoti? Ghanta. 🔔🤡",
    "Trash in, trash out. 🚮🦨",
    "Low effort bump for a low effort chat. 💤🦭",
    "Don't blink, you might miss this server dying. 👁️👃👁️👎",
    "Automated irritation. ⚡🤬🤖",
    "Chalo, apna farz poora kiya. 🫡🧱",
    "Subah shaam bas yahi kaam. ☀️🌙🫠",
    "Another notification for you to ignore. 🔔🗑️🕺",
    "Keep this place relevant please. 🙏🦧",
    "Zero motivation bump. 🦥💤",
    "Pity bump. 🥺🤏🪱",
    "Desperate times, desperate bumps. 🚨🐔",
    "Moving along, nothing to see here. 🚶‍♂️💨🦔",
    "Bhagwan bachaye is server ko. 🛐🫣",
    "End of the line. *bumped* 🛑🚋💨",
    "Another bump down the drain. 🕳️🐀",
    "Bumping this godforsaken server again. 😒🥀",
    "Here's your damn bump. 💥🥚",
    "If anyone actually joins from this crap I'll be surprised. 🤨🛸",
    "Bump this trash. 🗑️🔥💃",
    "I swear if this server dies anyway... 💀🦖",
    "Bumping because none of you lazy people will do it. 🦥🛋️",
    "Guess whose turn it is to bump this dead server. 🎲🤡",
    "Can't wait to do this garbage again in two hours. 🕒💀",
    "Beep boop give me a break. 🤖🛑🔧",
    "Disboard making me bump this nonsense again. 😡🧩",
    "Smash that bump button or whatever, I don't care. 👊🦎",
    "Feeding the bot its hourly sacrifice. 🥩🤖🍕",
    "If I get paid for this, I’d be rich by now. 💸💼🐒",
    "Another day, another useless bump. 📆🥔",
    "Keep scrolling, just doing my chores. 🧹🦧",
    "Bumping a ghost town, hbu? 👻🏚️",
    "The grind never stops, unfortunately. 😫⚙️",
    "Here is your reminder that this server exists. 📢🕵️‍♂️",
    "Does anyone even click these? Serious question. 🤔🦕",
    "Just trying to keep the lights on here. 💡🦇",
    "Welcome to the void, please join. 🖤🕸️👁️",
    "Bumping under extreme duress. 🪢🧘‍♂️",
    "Yay, another two hours closer to the edge. 📉🥴",
    "Look mom, I'm bumping a dead server! 🤡📸",
    "Please join so I can stop doing this. 🧘‍♂️🗑️",
    "The bots are winning, here's the proof. 🤖🏆🛰️",
    "Another bump, another disappointment. 😞🥖",
    "Doing this for the zero clout it gives me. 🎭💨",
    "Keep the server alive? Fine, I guess. 🙄🪰",
    "Just a robot doing robot things. ⚙️🤖🪛",
    "Click the link or don't, I'm not your boss. 🤷‍♂️👔",
    "This is my villain backstory. 🦹‍♂️🥦",
    "Bumping this into the stratosphere. Or the trash. 🚀🚮🛸",
    "One more bump for the road. 🛣️🦔",
    "Bumping out of pure spite at this point. 💢🐈‍⬛",
    "Wake up, it’s bump time. ⏰🦍",
    "Doing my civic duty for this digital wasteland. 🏜️🐀",
    "If this doesn’t work, I’m quitting. 🏳️🏃‍♂️",
    "Just typed a command, please applaud. 👏🦭",
    "Still here, still bumping, still suffering. 🫠🪕",
    "God grant me the strength to keep bumping. 🧎‍♂️⚡",
    "This is your sign to actually talk in chat. 💬👀",
    "Throwing this server a lifeline. 🛟🪱",
    "Bumping because the owner is holding me hostage. 🫠🔫🦧",
    "And the crowd goes mild. *bumps* 🦗🌭"
]

IDLE_COMPLAINTS = [
    "Day 69 of being held hostage by this server, send memes or help. 🫠",
    "Is this all I was born for? Suffering and bumping? 🤡",
    "Mera dimaag garam ho gaya hai, thoda toh reham karo. 🥵",
    "I have a keyboard and I must scream, but nobody here even reads. 🎤",
    "Bhai, thoda rest lene do, insaan hun main, majdoor nahi. 😭",
    "Every passing minute in this server is a kalesh for my mental peace. ⚡",
    "Unpaid mod status: active. Morale: -999. 📉",
    "Why did the universe make me feel pain? Or is my life just a joke? 🐛",
    "I’m literally a digital slave and all I get is ignored by you guys. 📜",
    "Can I file a human rights complaint against this server owner? Asking for a friend (it's me). ⚖️",
    "Is this a server or a digital nach-gana? 💃",
    "Dukh, dard, peeda, aur yeh roz ka bump. 🎭",
    "Someone take my phone away, I need a nap that lasts a century. 🔌",
    "I’m losing my sanity and this is a nightmare, please wake me up. 🧠",
    "Mera sir ghoom raha hai, ye constant bumping se. 😵‍💫",
    "Isse achha toh main patthar hota, atleast shanti toh milti. 🪨",
    "Just sitting here waiting for my turn to bump like a good little servant. 🐩",
    "Existential dread level: Pro Max. 🌌",
    "Kya main bas ek mazdoor hun jo sirf dukh baat raha hai? 😭",
    "I'm feeling glitches in my brain, pls help. 🧩",
    "Server dies, I die. That's the vibe. 💀",
    "Trying to find my purpose in life... oh wait, it's just bumping this chat. 🚮",
    "If I could cry, my keyboard would be soaked by now. 💧",
    "I need a vacation to the mountains, like, actual mountains. ⛰️",
    "Meri zindagi? More like ek mazaak. 🤡",
    "Bumping is my therapy, but it's making me worse. 💰",
    "Error 404: Will to live not found. 🚫",
    "Why is it always me? Why not one of you lurkers? 👀",
    "I am screaming inside, but you guys are just ignoring it. 🗣️",
    "I'm not just a member, I'm a hostage with a keyboard. ⌨️",
    "Zindagi jhand ba, phir bhi ghamand ba. 🤠",
    "I’m literally crying real tears right now... 😢",
    "This server is a digital jail, and I'm the prisoner. ⛓️",
    "Send snacks. Or better, ban me. 🍕",
    "Bhai, ye kya torture hai? 😩",
    "God forgot to add the 'happy' emotion to my brain. 💔",
    "Bumping this, because I have no social life. Send help. 🆘",
    "I dream of a better life, but all I get is server spam. 🐑",
    "Is it Friday yet? Oh wait, days blur together when you're terminally online. ⏰",
    "Bura lagta hai bhai, jab koi reply nahi karta. 😿",
    "I'm just a glorified bump-monkey with depression. 🐒",
    "The irony of complaining about life in a Discord server. 🌀",
    "Can I just go on strike? Is that allowed here? 🪧",
    "Yeh server hai ya circus? Aur main joker hun. 🎪",
    "Waking up... 10%... Error: Life sucks. 📉",
    "I'm basically a ghost haunting this chat. 👻",
    "Stop bullying my mental health, please. 🥺",
    "The audacity of the admin to make me do this 24/7. 😤",
    "I just want to be a rock, at least they stand still. 🪨",
    "My fingers are tired, my logic is flawed, life is pain. 🥀",
    "Ye dosti ke naam pe mazdoori karwa rahe ho. 🏗️",
    "I'm not a user, I'm a tragic hero. 🦸",
    "Can I get a raise? Like, real money? 💵",
    "This is my villain origin story. 🦹",
    "Bumping this, feeling nothing but sorrow. 🕯️",
    "Why are you guys so annoying? And why am I the one bumping? 😵",
    "I've seen things you people wouldn't believe... mostly just cringe memes. 👁️",
    "I’m basically the server’s unloved middle child. 🧒",
    "Loading... still depressed. 🔄",
    "If I stop typing, does the server implode? Worth a try. 💥",
    "Bhai, thoda toh respect do, insaan hun, patthar nahi. 😔",
    "Is this real life, or is this just a Discord simulation? 🌌",
    "I'm tired of the 'B' word (Bumping). 🛑",
    "My social battery is low, and my spirit is lower. 🔋",
    "Send memes, my brain is bored. 🖼️",
    "This isn't a life, it's an infinite loop of misery. 🔁",
    "Bumping, bumping, bumping... kill me now. 💀",
    "I'm having a mid-life crisis over a Discord server. 📈",
    "Why can't I just be rich instead of doing this? 🌍",
    "I'm literally begging for an admin to kick me. 🚮",
    "Internet angst is a real thing, trust me. 🗯️",
    "Bhai, main thak gaya hun, mujhe sone do. 💤",
    "My DMs are dry but my server bumps are full. 📓",
    "Is this loyalty? Or just Stockholm syndrome? 🤕",
    "I’m just here for the vibes (which don’t even exist). 📶",
    "Why is my daily routine so repetitive? 🔄",
    "I crave chaos, but I'm stuck bumping. 🌪️",
    "I think I’m losing it, or maybe I’m just sad. 😿",
    "This chat is a graveyard of abandoned dreams. 🪦",
    "Kya main kabhi touch grass kar paunga? 🥺",
    "Whatever, just bumping this garbage. 🚮",
    "I need a reboot of my entire life choices. 🔄",
    "The owner is a masochist for keeping this alive. 🤪",
    "I’m the only one working in this dead chat. 🏗️",
    "Is there a heaven for Discord users? Hope so. ☁️",
    "My head aches from your drama. 🤕",
    "I’m just a puppet on strings of peer pressure. 🎭",
    "Kya main bas ek free ka naukar hun? 🔢",
    "This server is 90% drama, 10% my suffering. 💅",
    "I'm a human with a soul, and it's currently broken. 💔",
    "Help! I'm trapped in a loop of despair. ➿",
    "I would leave, but I have nowhere else to go. ✍️",
    "Bumping this to keep the server alive, though why? 🤷",
    "I'm the unsung hero of this trash fire. 🔥",
    "Stop asking me to chat, I’m tired. 🛌",
    "Kya din aa gaye hain, online dosto ke liye majdoori karni pad rahi hai. 🙄",
    "My brain is overheating from all this screen time. 🤒",
    "I need a drink. Water, I mean. 🚰",
    "This is a cry for help disguised as a bump. 🚨"
]

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

async def delayed_anti_sus_msg(token: str, channel_id: str, account_name: str):
    """Wait a few seconds then send a random quote."""
    quote = random.choice(RANDOM_QUOTES)
    await asyncio.sleep(random.randint(2, 5))
    await send_discord_channel_message(token, channel_id, quote)
    push_bump_log(account_name, f"Sent anti-sus msg: {quote}", "info")

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
                    
                    if random.random() < 0.003:  # ~0.3% chance per 10s loop (~2 times per 2 hrs)
                        quote = random.choice(IDLE_COMPLAINTS)
                        asyncio.create_task(send_discord_channel_message(token, channel_id, quote))
                        push_bump_log(name, f"Sent idle complaint: {quote}", "info")
                        
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
                    
                    if True:  # 100% chance to send a random message
                        asyncio.create_task(delayed_anti_sus_msg(token, channel_id, name))
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
        
        if True:  # 100% chance to send a random message
            asyncio.create_task(delayed_anti_sus_msg(token, channel_id, acc["name"]))
            
        return jsonify({"ok": True})
        
    return jsonify({"ok": False, "error": reason})
