"""
apps/ai_resident/cog.py — Discord Bot Cog for AI Resident.

Handles gateway events (on_message), slash commands (/imagine, /meme, games), 
moderation alerts, memory recall, and accent learning.
"""
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import time
import re
import random
import json
from datetime import datetime
from collections import defaultdict

from core.db import query, cfg
from apps.ai_resident.llm import generate_response, get_embedding, transcribe_audio

# Anti-toxicity & profanity sanitization helpers
BANNED_STYLE_WORDS = [
    "baigan", "behen", "shuttfup", "bih", "bhen", "bhenchod", "bc", "mc", 
    "madarchod", "gand", "gaand", "chutiya", "chutiye", "bhosdike", "harami", "saale", "saala"
]

def sanitize_style_text(text: str) -> str:
    if not text:
        return ""
    for w in BANNED_STYLE_WORDS:
        text = re.sub(r'\b' + re.escape(w) + r'\b', "", text, flags=re.IGNORECASE)
    text = re.sub(r'\s*,\s*,', ',', text)
    text = re.sub(r'^\s*,\s*', '', text)
    text = re.sub(r'\s*,\s*$', '', text)
    return text.strip()

# Post-generation output safety filter
BLOCKED_OUTPUT_PATTERNS = [
    r"\bbehen\b", r"\bbaigan\b", r"shuttfup", r"\bbih\b", r"\bbhen\b",
    r"\bbhenchod\b", r"\bmadarchod\b", r"\bchutiya\b", r"\bchutiye\b",
    r"\bbhosdike\b", r"\bgaand\b", r"\bgand\b", r"\bharami\b", r"\bfuck\b",
    r"\bshit\b", r"\basshole\b", r"\bbastard\b", r"\bdick\b", r"\bpussy\b", r"\bwhore\b", r"\bslut\b"
]

def is_response_safe(text: str) -> bool:
    if not text:
        return True
    lower = text.lower()
    return not any(re.search(p, lower) for p in BLOCKED_OUTPUT_PATTERNS)

def cosine_similarity(v1: list, v2: list) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

# Simple stateful game class
class GameState:
    def __init__(self, game_type: str):
        self.game_type = game_type
        self.active = True
        self.data = {}
        self.created_at = time.time()

class AIResidentCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # channel_id -> list of {"role": "user"/"assistant", "content": "...", "author_name": "..."}
        self.rolling_context = defaultdict(list)
        # channel_id -> last response timestamp
        self.cooldowns = defaultdict(float)
        # user_id -> last response timestamp (per-user rate limit for ambient replies)
        self.user_cooldowns = defaultdict(float)
        # guild_id -> list of message timestamps in last 10 minutes (for Dynamic Mood Engine)
        self.guild_chat_timestamps = defaultdict(list)
        # channel_id -> last message seen timestamp
        self.last_message_time = defaultdict(float)
        # channel_id -> GameState
        self.active_games = {}
        
        # Self-harm/danger keywords
        self.danger_keywords = [
            "suicide", "suicidal", "kill myself", "end my life", "self harm", 
            "cutting myself", "depressed and want to die", "mar jana chahta", "zehar",
            "marna chahta", "jaan de dunga", "marna hai", "die alone", "want to die",
            "self-harm", "hurt myself", "hanging myself", "overdose"
        ]
        # Scam patterns
        self.scam_patterns = [
            r"free.*nitro", r"discord.*gift", r"steam.*gift", r"crypto.*double",
            r"get.*free.*money", r"leak.*onlyfans", r"hack.*robux"
        ]
        self.tasks_started = False
        # Per-guild message counter for rate-limiting LLM style analysis
        self._learn_counter = defaultdict(int)

    def get_guild_mood(self, guild_id: str) -> str:
        """Calculates dynamic guild mood based on chat velocity and time of day."""
        now = time.time()
        recent_count = len([t for t in self.guild_chat_timestamps[guild_id] if now - t < 600])
        utc_hour = datetime.utcnow().hour
        
        if 21 <= utc_hour or utc_hour <= 1:  # Late night India/UTC (2am-6am IST)
            return "Sleepy / Cozy"
        elif recent_count > 15:
            return "Hyped / Chaotic"
        elif recent_count > 5:
            return "Witty / Active"
        else:
            return "Chill / Relaxed"

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.tasks_started:
            self.tasks_started = True
            asyncio.create_task(self.inactivity_nudge_loop())
            asyncio.create_task(self.daily_question_loop())
            print(f"[AI Resident] Background tasks scheduled on_ready for bot {self.bot.user}")

    async def inactivity_nudge_loop(self):
        await self.bot.wait_until_ready()
        print(f"[AI Resident] Inactivity nudge loop started for bot {self.bot.user}")
        while not self.bot.is_closed():
            try:
                # Check every 10 minutes
                await asyncio.sleep(600)
                
                for guild in self.bot.guilds:
                    guild_id = str(guild.id)
                    is_enabled = await self.get_guild_cfg(guild_id, "active", "1") == "1"
                    if not is_enabled:
                        continue
                        
                    interval_str = await self.get_guild_cfg(guild_id, "inactivity_nudge_hours", "12")
                    try:
                        nudge_interval = float(interval_str) * 3600.0
                    except ValueError:
                        nudge_interval = 12.0 * 3600.0
                        
                    active_channels_str = await self.get_guild_cfg(guild_id, "active_channels", "")
                    target_channel_ids = [c.strip() for c in active_channels_str.split(",") if c.strip()]
                    
                    channels = []
                    if target_channel_ids:
                        for cid in target_channel_ids:
                            ch = guild.get_channel(int(cid))
                            if ch:
                                channels.append(ch)
                    else:
                        channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages]
                                
                    now = time.time()
                    for channel in channels:
                        last_active = self.last_message_time.get(channel.id, now)
                        
                        if now - last_active > nudge_interval:
                            self.last_message_time[channel.id] = now
                            
                            preset = await self.get_guild_cfg(guild_id, "personality", "Roast Hyderabadi")
                            custom_prompt = await self.get_guild_cfg(guild_id, "custom_prompt", "")
                            
                            style_row = await query("SELECT slang_words, accent_notes FROM ai_resident_style_notes WHERE guild_id=?", guild_id, fetch_one=True)
                            style_notes = ""
                            if style_row:
                                style_notes = f"Slang words popular here: {style_row['slang_words'] or 'none'}. Accent details: {style_row['accent_notes'] or 'none'}."
                                
                            prompt = (
                                "Generate a short, casual, one-line inactivity nudge asking the server chat where everyone went. "
                                "Make it sound natural, funny, and matching the requested personality. "
                                "Do not mention it is an automated test, just act like a server resident who is bored."
                            )
                            
                            response = await generate_response(
                                messages=[{"role": "user", "content": prompt}],
                                preset_name=preset,
                                custom_system_prompt=custom_prompt,
                                guild_style_notes=style_notes
                            )
                            
                            await channel.send(response)
                            await self.log_stat(guild_id, is_reply=True)
                            
            except Exception as e:
                print(f"[AI Resident] Error in inactivity loop: {e}")

    async def daily_question_loop(self):
        await self.bot.wait_until_ready()
        print(f"[AI Resident] Daily question loop started for bot {self.bot.user}")
        while not self.bot.is_closed():
            try:
                # Run every 24 hours
                await asyncio.sleep(86400)
                
                for guild in self.bot.guilds:
                    guild_id = str(guild.id)
                    is_enabled = await self.get_guild_cfg(guild_id, "active", "1") == "1"
                    if not is_enabled:
                        continue
                        
                    daily_q_enabled = await self.get_guild_cfg(guild_id, "daily_q_enabled", "1") == "1"
                    if not daily_q_enabled:
                        continue
                        
                    active_channels_str = await self.get_guild_cfg(guild_id, "active_channels", "")
                    target_channel_ids = [c.strip() for c in active_channels_str.split(",") if c.strip()]
                    
                    channel = None
                    if target_channel_ids:
                        channel = guild.get_channel(int(target_channel_ids[0]))
                    else:
                        for ch in guild.text_channels:
                            if ch.permissions_for(guild.me).send_messages:
                                channel = ch
                                break
                                
                    if channel:
                        preset = await self.get_guild_cfg(guild_id, "personality", "Roast Hyderabadi")
                        custom_prompt = await self.get_guild_cfg(guild_id, "custom_prompt", "")
                        
                        prompt = (
                            "Generate a unique, funny, and engaging 'Question of the Day' (QOTD) in Hinglish/English. "
                            "It should prompt the server members to reply with their hot takes or opinions (e.g. about games, movies, life, tech, etc.). "
                            "Make it fit your accent/personality preset. Keep it short (1-2 lines)."
                        )
                        
                        response = await generate_response(
                            messages=[{"role": "user", "content": prompt}],
                            preset_name=preset,
                            custom_system_prompt=custom_prompt
                        )
                        
                        await channel.send(f"📢 **Question of the Day:**\n{response}")
                        
            except Exception as e:
                print(f"[AI Resident] Error in daily question loop: {e}")


    async def get_guild_cfg(self, guild_id: str, key: str, default: str = "") -> str:
        row = await query(
            "SELECT value FROM ai_resident_guild_config WHERE guild_id=? AND key=?",
            str(guild_id), key, fetch_one=True
        )
        if row:
            return row["value"]
        return default

    async def set_guild_cfg(self, guild_id: str, key: str, value: str):
        await query(
            "INSERT INTO ai_resident_guild_config (guild_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT (guild_id, key) DO UPDATE SET value=EXCLUDED.value",
            str(guild_id), key, str(value)
        )

    async def log_stat(self, guild_id: str, is_reply: bool = False):
        gid = str(guild_id)
        row = await query("SELECT 1 FROM ai_resident_stats WHERE guild_id=?", gid, fetch_one=True)
        if not row:
            await query("INSERT INTO ai_resident_stats (guild_id, messages_seen, replies_sent) VALUES (?,0,0)", gid)
        
        if is_reply:
            await query("UPDATE ai_resident_stats SET replies_sent = replies_sent + 1 WHERE guild_id=?", gid)
        else:
            await query("UPDATE ai_resident_stats SET messages_seen = messages_seen + 1 WHERE guild_id=?", gid)

    # ─── Event Handlers ───────────────────────────────────────────────────────
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # 0. Game check (trivia / hangman)
        await self.on_message_game_check(message)

        # 1. Moderation & Danger Pre-Filter
        await self.process_moderation_filter(message)

        # 2. Check Guild Active Status
        guild_id = str(message.guild.id) if message.guild else "dm"
        if guild_id == "dm":
            return # Skip DM memories mixing into guild memory

        # Log seen message & track velocity for mood engine
        await self.log_stat(guild_id, is_reply=False)
        self.last_message_time[message.channel.id] = time.time()
        now = time.time()
        self.guild_chat_timestamps[guild_id].append(now)
        self.guild_chat_timestamps[guild_id] = [t for t in self.guild_chat_timestamps[guild_id] if now - t < 600]

        is_enabled = await self.get_guild_cfg(guild_id, "active", "1") == "1"
        if not is_enabled:
            return

        # 3. Learn user style/slang
        await self.learn_accent(message)
        asyncio.create_task(self.extract_and_save_memories(message))

        # 4. Message History Caching & Reply Chain Resolution
        context = await self.resolve_context(message)

        # 5. Check if Bot should reply
        should_reply = False
        bot_user = self.bot.user
        is_mention = False
        if bot_user:
            is_mention = (bot_user in message.mentions) or (f"<@{bot_user.id}>" in message.content) or (f"<@!{bot_user.id}>" in message.content)

        is_direct_reply = (
            message.reference and 
            message.reference.resolved and 
            isinstance(message.reference.resolved, discord.Message) and
            bot_user and message.reference.resolved.author.id == bot_user.id
        )

        if is_mention or is_direct_reply:
            should_reply = True
        else:
            # Random Ambient reply chance
            chance_str = await self.get_guild_cfg(guild_id, "reply_chance", "4")
            try:
                chance = float(chance_str) / 100.0
            except ValueError:
                chance = 0.04
            
            # Smart Interest Filter: prioritize questions, expressive chat, and keyword energy
            content_clean = message.clean_content.strip()
            has_question = "?" in content_clean
            is_lengthy = len(content_clean) >= 12
            has_keywords = any(kw in content_clean.lower() for kw in ["lol", "lmao", "bhai", "bro", "game", "why", "how", "what", "who", "kya", "kaise", "sahi", "op", "hey", "hello", "hi"])
            is_interesting = has_question or is_lengthy or has_keywords

            active_channels_str = await self.get_guild_cfg(guild_id, "active_channels", "")
            active_channels = [c.strip() for c in active_channels_str.split(",") if c.strip()]
            
            if is_interesting and (not active_channels or str(message.channel.id) in active_channels) and random.random() < chance:
                # Apply per-user rate limit (30s) to prevent single user ambient baiting
                if time.time() - self.user_cooldowns[message.author.id] >= 30.0:
                    should_reply = True

        if should_reply:
            # Check Cooldown
            cooldown_str = await self.get_guild_cfg(guild_id, "cooldown", "5")
            try:
                cooldown = float(cooldown_str)
            except ValueError:
                cooldown = 5.0

            last_reply = self.cooldowns[message.channel.id]
            if time.time() - last_reply < cooldown:
                # If direct mention, react with a timer emoji to show cooldown
                if is_mention or is_direct_reply:
                    try:
                        await message.add_reaction("⏳")
                    except Exception:
                        pass
                return

            # Trigger AI Response
            self.cooldowns[message.channel.id] = time.time()
            self.user_cooldowns[message.author.id] = time.time()
            async with message.channel.typing():
                if message.attachments and message.attachments[0].content_type and message.attachments[0].content_type.startswith("image/"):
                    import httpx
                    try:
                        attachment = message.attachments[0]
                        async with httpx.AsyncClient() as client:
                            r = await client.get(attachment.url, timeout=15)
                            if r.status_code == 200:
                                img_bytes = r.content
                                mime_type = attachment.content_type
                                prompt = message.clean_content or "Describe or roast this image/meme."
                                preset = await self.get_guild_cfg(guild_id, "personality", "Roast Hyderabadi")
                                custom_prompt = await self.get_guild_cfg(guild_id, "custom_prompt", "")
                                
                                from apps.ai_resident.llm import generate_vision_response
                                comment = await generate_vision_response(
                                    image_bytes=img_bytes,
                                    mime_type=mime_type,
                                    prompt=prompt,
                                    preset_name=preset,
                                    custom_system_prompt=custom_prompt
                                )
                                await message.reply(comment)
                                await self.log_stat(guild_id, is_reply=True)
                                return
                    except Exception as e:
                        print(f"Failed to comment on image: {e}")

                doc_text = await self.process_attachments(message)
                if doc_text and context:
                    context[-1]["content"] += f"\n\n{doc_text}"

                await self.send_ai_reply(message, context)

    # ─── Core AI Logic ────────────────────────────────────────────────────────
    
    async def send_ai_reply(self, message: discord.Message, context: list):
        guild_id = str(message.guild.id)
        
        # Load Personality Preset (check for channel-specific override first)
        chan_preset = await self.get_guild_cfg(guild_id, f"channel_personality:{message.channel.id}", "")
        preset = chan_preset if chan_preset else await self.get_guild_cfg(guild_id, "personality", "Roast Hyderabadi")
        custom_prompt = await self.get_guild_cfg(guild_id, "custom_prompt", "")
        
        # Inject Dynamic Guild Mood into System Prompt
        guild_mood = self.get_guild_mood(guild_id)
        mood_note = f"CURRENT SERVER MOOD: {guild_mood} (adapt your energy level naturally to match this vibe)."
        system_instructions = f"{custom_prompt}\n\n{mood_note}" if custom_prompt else mood_note

        # Load Memory Toggle
        memory_enabled = await self.get_guild_cfg(guild_id, "memory_enabled", "1") == "1"
        user_memory_context = ""
        
        if memory_enabled:
            user_memory_context = await self.fetch_user_memories(message.guild.id, message.author.id, message.content)

        # Load learned guild style notes
        style_row = await query("SELECT slang_words, accent_notes FROM ai_resident_style_notes WHERE guild_id=?", guild_id, fetch_one=True)
        style_notes = ""
        if style_row and (style_row["slang_words"] or style_row["accent_notes"]):
            slang = sanitize_style_text(style_row["slang_words"] or "")
            notes = sanitize_style_text(style_row["accent_notes"] or "")
            style_notes = (
                f"SERVER VIBE (for energy/tone reference only — do not copy phrases verbatim, just match the general casualness):\n"
                f"{notes}\n"
                f"Some casual words used here (use sparingly, only if they fit naturally): {slang}\n"
                f"Do NOT sound like a generic AI, but your own HARD LIMITS against profanity, slurs, and toxicity always take priority over matching this vibe."
            )

        # Compile System Prompts
        system_instructions = custom_prompt if custom_prompt else None
        
        # Add user memory facts if they exist
        messages_payload = []
        if user_memory_context:
            messages_payload.append({
                "role": "user",
                "content": (
                    f"[SYSTEM NOTIFICATION — PRIVATE CONTEXT ONLY, not something to say out loud:\n"
                    f"Known facts about user {message.author.display_name}:\n{user_memory_context}\n"
                    f"Use this only to personalize tone/references SUBTLY if directly relevant. "
                    f"NEVER read this list back, quote it, summarize it, or say 'I remember you said...' in front of the channel. "
                    f"NEVER bring up a fact unless the user brings up that exact topic first in this message.]"
                )
            })
            
        messages_payload.extend(context)

        # Generate Response
        response = await generate_response(
            messages=messages_payload,
            preset_name=preset,
            custom_system_prompt=system_instructions,
            guild_style_notes=style_notes
        )

        # Discord Presence Sleep Status Integration
        if response.startswith("😴"):
            try:
                await self.bot.change_presence(status=discord.Status.idle, activity=discord.Game(name="Sleeping 😴 (power nap)"))
            except Exception:
                pass
        else:
            try:
                await self.bot.change_presence(status=discord.Status.online, activity=discord.Game(name="Hanging out in chat ✨"))
            except Exception:
                pass

        # Output Safety Filter Check
        if not is_response_safe(response):
            print(f"[Safety Filter] Blocked unsafe response in guild {guild_id}: {response}")
            admin_ch_id = await cfg("admin_channel_id")
            if admin_ch_id:
                try:
                    admin_ch = self.bot.get_channel(int(admin_ch_id))
                    if admin_ch:
                        embed = discord.Embed(
                            title="🛡️ AI OUTPUT SAFETY FILTER TRIGGERED",
                            description=f"Blocked toxic/profane output in {message.channel.mention} for user {message.author.mention}:\n\n*\"{response}\"*",
                            color=0xE74C3C,
                            timestamp=datetime.utcnow()
                        )
                        await admin_ch.send(embed=embed)
                except Exception as e:
                    print(f"Failed to log safety filter alert: {e}")

            response = random.choice([
                "hmm let me think about that differently",
                "nvm skip that one lol",
                "eh not gonna touch that one",
                "kuch naya bolo bhai"
            ])

        # Post reply
        await message.reply(response)
        await self.log_stat(guild_id, is_reply=True)

        # Feed assistant response into rolling context
        self.rolling_context[message.channel.id].append({
            "role": "assistant",
            "content": response,
            "author_name": self.bot.user.name
        })
        if len(self.rolling_context[message.channel.id]) > 20:
            self.rolling_context[message.channel.id].pop(0)

    # ─── Context & Chain Resolution ───────────────────────────────────────────
    
    async def resolve_context(self, message: discord.Message) -> list:
        channel_id = message.channel.id
        
        # Append current user message
        new_msg = {
            "role": "user",
            "content": f"{message.author.display_name}: {message.clean_content}",
            "author_name": message.author.name
        }
        
        # Resolve reference chains
        if message.reference and message.reference.message_id:
            try:
                # Check if already cached, if not fetch from Discord
                ref_id = message.reference.message_id
                cached = next((m for m in self.rolling_context[channel_id] if m.get("msg_id") == ref_id), None)
                if not cached:
                    ref_msg = await message.channel.fetch_message(ref_id)
                    resolved_content = f"[{ref_msg.author.display_name} (replied to)]: {ref_msg.clean_content}"
                    # Insert referenced context just before the current message
                    self.rolling_context[channel_id].append({
                        "role": "user",
                        "content": resolved_content,
                        "author_name": ref_msg.author.name,
                        "msg_id": ref_id
                    })
            except Exception as e:
                print(f"Failed resolving reply chain: {e}")

        self.rolling_context[channel_id].append({
            "role": "user",
            "content": f"{message.author.display_name}: {message.clean_content}",
            "author_name": message.author.name,
            "msg_id": message.id
        })

        if len(self.rolling_context[channel_id]) > 20:
            self.rolling_context[channel_id].pop(0)

        # Return standard OpenAI-like structures
        return [{"role": m["role"], "content": m["content"]} for m in self.rolling_context[channel_id]]

    # ─── Memory & Accent Learning ──────────────────────────────────────────────

    async def learn_accent(self, message: discord.Message):
        """Every 30 messages, uses LLM to analyze server vibe and update clean style notes."""
        guild_id = str(message.guild.id)

        # Collect any words typed into a rolling buffer
        text = message.content.strip()
        if not text or len(text) < 3:
            return

        self._learn_counter[guild_id] += 1

        # Only do a deep LLM analysis every 30 messages to smooth out toxic bursts
        if self._learn_counter[guild_id] % 30 != 0:
            return

        # Grab the last 10 messages from the channel for style analysis
        try:
            msgs = []
            async for m in message.channel.history(limit=15):
                if not m.author.bot and m.content.strip():
                    msgs.append(f"{m.author.display_name}: {m.content}")
            if len(msgs) < 3:
                return
            sample = "\n".join(reversed(msgs[:10]))  # chronological order
        except Exception:
            return

        analysis_prompt = (
            f"Analyze the following Discord server chat messages and extract a concise, CLEAN style profile.\n"
            f"Messages:\n{sample}\n\n"
            f"Reply with ONLY a JSON object with these keys (no markdown, no explanation):\n"
            f'{{"slang": [list of casual/fun slang words used — EXCLUDE any profanity, slurs, insults, or crude terms], '
            f'"tone": "one-line tone description (e.g. playful, energetic, dry)", '
            f'"language": "English/Hinglish/Hindi/mixed", '
            f'"vibe": "one-line vibe like edgy/wholesome/chaotic etc — describe the vibe without profanity", '
            f'"example_style": "one CLEAN example sentence in this server\'s style — must NOT contain profanity, insults, or crude language, even if the original chat did"}}\n\n'
            f"IMPORTANT: Even if the source messages contain rude, crude, or insulting language, do not include any of that in your output. "
            f"Only extract the fun/casual patterns (word choice, energy, sentence rhythm) — never the toxic parts."
        )

        try:
            raw = await generate_response(
                messages=[{"role": "user", "content": analysis_prompt}],
                preset_name="Helpful Professor"
            )
            # Extract JSON even if wrapped in text
            json_start = raw.find("{")
            json_end = raw.rfind("}") + 1
            if json_start == -1 or json_end == 0:
                return
            data = json.loads(raw[json_start:json_end])

            slang_list = data.get("slang", [])
            tone = data.get("tone", "casual")
            language = data.get("language", "English")
            vibe = data.get("vibe", "chill")
            example = data.get("example_style", "")

            slang_str = sanitize_style_text(", ".join(slang_list[:20]) if slang_list else "")
            accent_notes = sanitize_style_text(
                f"Tone: {tone}. Language: {language}. Vibe: {vibe}. "
                f"Example of how people talk here: \"{example}\""
            )

            await query(
                "INSERT INTO ai_resident_style_notes (guild_id, slang_words, accent_notes, last_updated) VALUES (?,?,?,?) "
                "ON CONFLICT (guild_id) DO UPDATE SET slang_words=EXCLUDED.slang_words, accent_notes=EXCLUDED.accent_notes, last_updated=EXCLUDED.last_updated",
                guild_id, slang_str, accent_notes, datetime.utcnow().isoformat()
            )
            print(f"[Style Learner] Updated style for guild {guild_id}: {vibe} / {language}")
        except Exception as e:
            print(f"[Style Learner] Failed: {e}")

    async def fetch_user_memories(self, guild_id, user_id, message_text: str) -> str:
        """Looks up existing memories about the user using high-precision vector RAG search (similarity >= 0.75)."""
        # Check if user opted out
        opt = await query("SELECT 1 FROM ai_resident_opt_out WHERE user_id=?", str(user_id), fetch_one=True)
        if opt:
            return ""

        # Fetch memories stored for this user in this guild
        rows = await query(
            "SELECT fact, vector_embedding FROM ai_resident_memories WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 20",
            str(guild_id), str(user_id)
        ) or []

        if not rows:
            return ""

        # 1. High-Precision Vector RAG Similarity Search
        try:
            msg_embedding = await get_embedding(message_text)
            if msg_embedding:
                matched_facts = []
                for r in rows:
                    fact_str = r["fact"]
                    vec_raw = r.get("vector_embedding")
                    if vec_raw:
                        try:
                            vec_arr = json.loads(vec_raw)
                            sim = cosine_similarity(msg_embedding, vec_arr)
                            # Strict relevance threshold: >= 0.75 cosine similarity
                            if sim >= 0.75:
                                matched_facts.append(fact_str)
                        except Exception:
                            pass
                if matched_facts:
                    return "\n".join([f"- {f}" for f in matched_facts[:3]])
        except Exception as e:
            print(f"[Vector Memory RAG Error]: {e}")

        # 2. Strict Keyword Fallback (ONLY if words match explicitly, no random fallback)
        words = [w.lower() for w in re.findall(r"\b\w{4,12}\b", message_text) if w.lower() not in ("what", "where", "when", "that", "this", "have", "with", "your")]
        if not words:
            return ""

        likes_clauses = " OR ".join(["fact ILIKE ?"] * len(words))
        params = [str(guild_id), str(user_id)] + [f"%{w}%" for w in words]
        
        matched_rows = await query(
            f"SELECT fact FROM ai_resident_memories WHERE guild_id=? AND user_id=? AND ({likes_clauses}) ORDER BY id DESC LIMIT 3",
            *params
        ) or []

        if matched_rows:
            return "\n".join([f"- {r['fact']}" for r in matched_rows])

        return ""  # Zero-Leak Guarantee: Do NOT return random unprompted facts!

    # ─── Moderation Filters ───────────────────────────────────────────────────
    
    async def process_moderation_filter(self, message: discord.Message):
        """Scans for scams and self-harm. Flags to admin_channel."""
        content = message.content.lower()
        guild_id = str(message.guild.id) if message.guild else ""
        if not guild_id:
            return

        # 1. Self-Harm Filter
        if any(kw in content for kw in self.danger_keywords):
            # Route privately to mod-alert
            admin_ch_id = await cfg("admin_channel_id")
            if admin_ch_id:
                try:
                    admin_ch = self.bot.get_channel(int(admin_ch_id))
                    if admin_ch:
                        embed = discord.Embed(
                            title="🚨 SELF-HARM / DANGER ALERT",
                            description=f"User {message.author.mention} (`{message.author}`) triggered danger alert in {message.channel.mention}:\n\n*\"{message.content}\"*",
                            color=0xFF0000,
                            timestamp=datetime.utcnow()
                        )
                        await admin_ch.send(content="@everyone Human intervention required!", embed=embed)
                except Exception as e:
                    print(f"Failed to route self-harm alert: {e}")

        # 2. Scam Link Filter
        for pattern in self.scam_patterns:
            if re.search(pattern, content):
                # Flag to mod logs
                admin_ch_id = await cfg("admin_channel_id")
                if admin_ch_id:
                    try:
                        admin_ch = self.bot.get_channel(int(admin_ch_id))
                        if admin_ch:
                            embed = discord.Embed(
                                title="⚠️ SCAM / SPAM PRE-FILTER FLAG",
                                description=f"Potential scam link matched pattern in {message.channel.mention} by {message.author.mention}:\n\n*\"{message.content}\"*",
                                color=0xF1C40F,
                                timestamp=datetime.utcnow()
                            )
                            await admin_ch.send(embed=embed)
                    except Exception as e:
                        print(f"Failed to route scam alert: {e}")

    # ─── Commands ─────────────────────────────────────────────────────────────

    @app_commands.command(name="help", description="List all available AI Resident commands")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 AI Resident Bot Commands",
            description="I am your friendly neighborhood autonomous AI Server Resident! Here are my slash commands:",
            color=0x8a2be2
        )
        embed.add_field(name="`/imagine <prompt>`", value="Generate an image from a prompt (powered by Stability/Pollinations AI).", inline=False)
        embed.add_field(name="`/meme <caption > [prompt]`", value="Generate a captioned meme template.", inline=False)
        embed.add_field(name="`/trivia`", value="Start a fun trivia game in this channel.", inline=False)
        embed.add_field(name="`/hangman`", value="Start a game of Hangman in this channel.", inline=False)
        embed.add_field(name="`/forgetme`", value="Delete your stored personal facts from my long-term memory.", inline=False)
        embed.add_field(name="💬 Ambient Chatting", value="Mention me (or reply to my messages) in chat to talk to me! I also reply randomly to normal messages depending on the configured chance.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="imagine", description="Generate a high-quality AI image from a prompt")
    async def imagine(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer()
        
        # 1. Primary: Try Google Imagen 3 API (powered by Gemini API Key!)
        from apps.ai_resident.llm import generate_gemini_image
        img_bytes = await generate_gemini_image(prompt)
        
        if img_bytes:
            import io
            file = discord.File(io.BytesIO(img_bytes), filename="imagine.jpg")
            embed = discord.Embed(title=f"🎨 Imagine: {prompt[:100]}", color=0x4285F4)
            embed.set_image(url="attachment://imagine.jpg")
            embed.set_footer(text="Powered by Google Imagen 3")
            return await interaction.followup.send(embed=embed, file=file)

        import urllib.parse
        # Enhance prompt naturally only if the user didn't request a specific style
        enhanced_prompt = prompt.strip()
        lower_prompt = enhanced_prompt.lower()
        has_style = any(s in lower_prompt for s in ["style", "art", "drawing", "illustration", "cartoon", "anime", "painting", "render", "sketch", "logo", "pixel"])
        if len(enhanced_prompt.split()) < 8 and not has_style:
            enhanced_prompt += ", high quality, detailed, realistic lighting"

        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        
        # 2. Check Stability AI
        stability_key = await cfg("STABILITY_API_KEY")
        if stability_key:
            try:
                import httpx
                url = "https://api.stability.ai/v1/generation/stable-diffusion-v1-6/text-to-image"
                headers = {"Authorization": f"Bearer {stability_key}", "Accept": "application/json"}
                body = {"text_prompts": [{"text": prompt}], "cfg_scale": 7, "height": 512, "width": 512, "samples": 1, "steps": 30}
                async with httpx.AsyncClient() as client:
                    r = await client.post(url, headers=headers, json=body, timeout=20)
                    if r.status_code == 200:
                        import base64
                        import io
                        data = r.json()
                        img_b64 = data["artifacts"][0]["base64"]
                        img_bytes = base64.b64decode(img_b64)
                        file = discord.File(io.BytesIO(img_bytes), filename="imagine.png")
                        return await interaction.followup.send(content=f"🎨 **Imagine:** {prompt}", file=file)
            except Exception as e:
                print(f"Stability generation failed: {e}")

        # 3. High-Resolution FLUX.1 Model Fallback
        seed = random.randint(1000, 9999)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width=1024&height=1024&model=flux&enhance=true&nologo=true"
        embed = discord.Embed(title=f"🎨 Imagine: {prompt[:100]}", color=0x8a2be2)
        embed.set_image(url=url)
        embed.set_footer(text="Powered by FLUX.1 AI Model")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="meme", description="Generate a captioned meme")
    async def meme(self, interaction: discord.Interaction, caption: str, prompt: str = "funny template cartoon"):
        await interaction.response.defer()
        import urllib.parse
        encoded = urllib.parse.quote(prompt)
        seed = random.randint(1000, 9999)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={seed}&width=800&height=800&nologo=true"
        embed = discord.Embed(title=caption[:100], color=0xf1c40f)
        embed.set_image(url=url)
        embed.set_footer(text=f"Prompt: {prompt[:80]}")
        await interaction.followup.send(embed=embed)

    # ─── Games Stateful Logic ─────────────────────────────────────────────────
    
    @app_commands.command(name="trivia", description="Start a Hinglish trivia question in this channel")
    async def trivia(self, interaction: discord.Interaction):
        channel_id = interaction.channel.id
        if channel_id in self.active_games and self.active_games[channel_id].active:
            return await interaction.response.send_message("❌ Ek game chalra abhi is channel pe, wait karo!", ephemeral=True)
            
        trivia_questions = [
            {"q": "India ka capital kya hai? (Simple direct name)", "a": "new delhi"},
            {"q": "MS Dhoni ka nickname kya hai?", "a": "mahi"},
            {"q": "Which Bollywood actor is known as King Khan?", "a": "shah rukh khan"},
            {"q": "Biryani ke liye kaunsa city sabse famous hai India me?", "a": "hyderabad"},
            {"q": "Discord launch kab hua tha? (Year batana)", "a": "2015"}
        ]
        q = random.choice(trivia_questions)
        
        game = GameState("trivia")
        game.data = {"question": q["q"], "answer": q["a"]}
        self.active_games[channel_id] = game
        
        await interaction.response.send_message(f"🧠 **Trivia Time!**\n**Question:** {q['q']}\n*(Reply with correct answer to win!)*")

    @app_commands.command(name="hangman", description="Start a game of Hangman in this channel")
    async def hangman(self, interaction: discord.Interaction):
        channel_id = interaction.channel.id
        if channel_id in self.active_games and self.active_games[channel_id].active:
            return await interaction.response.send_message("❌ Game already running here!", ephemeral=True)

        words = ["hyderabad", "biryani", "discord", "confession", "resident", "antigravity"]
        word = random.choice(words)
        
        game = GameState("hangman")
        game.data = {
            "word": word,
            "guessed": [],
            "max_lives": 6,
            "lives": 6
        }
        self.active_games[channel_id] = game
        
        display = " ".join(["_" for _ in word])
        await interaction.response.send_message(f"🎮 **Hangman Started!**\nWord: `{display}`\nLives remaining: `6`\n*(Guess letters by typing them in chat!)*")

    # Listen to answers/inputs for active games
    @commands.Cog.listener()
    async def on_message_game_check(self, message: discord.Message):
        if message.author.bot:
            return
            
        channel_id = message.channel.id
        if channel_id not in self.active_games:
            return
            
        game = self.active_games[channel_id]
        if not game.active:
            return
            
        content = message.content.lower().strip()
        
        if game.game_type == "trivia":
            correct_ans = game.data["answer"].lower()
            if correct_ans in content:
                game.active = False
                await message.reply(f"🎉 **Sahi jawab!** {message.author.mention} ne correct answer diya: **{game.data['answer']}**! Kya baat hai boss!")
                
        elif game.game_type == "hangman":
            word = game.data["word"]
            # Check if letter guess
            if len(content) == 1 and content.isalpha():
                if content in game.data["guessed"]:
                    return await message.reply("Arre bhai, ye letter pehle se guess ho chuka hai!")
                
                game.data["guessed"].append(content)
                if content not in word:
                    game.data["lives"] -= 1
                    if game.data["lives"] <= 0:
                        game.active = False
                        return await message.reply(f"💀 **Game Over!** Koi lives nahi bache. Sahi word tha: **{word}**")
                    else:
                        await message.reply(f"❌ Wrong! Lives remaining: `{game.data['lives']}`")
                
                # Check win
                display = " ".join([char if char in game.data["guessed"] else "_" for char in word])
                if "_" not in display:
                    game.active = False
                    await message.reply(f"🎉 **Win!** {message.author.mention} ne guess kiya! Word tha: **{word}**")
                else:
                    await message.reply(f"Word: `{display}`\nLives: `{game.data['lives']}`")

    # ─── Memory Forget Me Command ─────────────────────────────────────────────
    
    @app_commands.command(name="forgetme", description="Opt-out of AI long-term memories and delete your stored facts")
    async def forgetme(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        await query(
            "INSERT INTO ai_resident_opt_out (user_id) VALUES (?) ON CONFLICT DO NOTHING",
            user_id
        )
        await query(
            "DELETE FROM ai_resident_memories WHERE user_id=?",
            user_id
        )
        await interaction.response.send_message("✅ Sahi hai, aapka sab data delete kar diya. Ab aapko yaad nahi rakhunga!", ephemeral=True)

    async def extract_and_save_memories(self, message: discord.Message):
        """Asynchronously extracts facts from the current message and saves to database."""
        content = message.content.strip()
        if len(content) < 15 or message.author.bot:
            return

        # Personal Fact Indicator Filter to save 95% of LLM extraction API tokens
        PERSONAL_INDICATORS = [
            "i love", "i hate", "i play", "my favorite", "my fav", "i live in", "i am from",
            "my birthday", "i work", "my dog", "my cat", "my pet", "my name is", "mera favorite",
            "meri favorite", "mujhe pasand", "mai rehta", "meri age", "my hobby", "i enjoy",
            "main khelta", "main rehta", "merko pasand", "mera naam"
        ]
        content_lower = content.lower()
        if not any(ind in content_lower for ind in PERSONAL_INDICATORS):
            return  # Skip LLM API extraction call for casual chatter!

        # Check if opted out
        opt = await query("SELECT 1 FROM ai_resident_opt_out WHERE user_id=?", str(message.author.id), fetch_one=True)
        if opt:
            return

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        username = message.author.display_name

        prompt = (
            f"You are a background fact extractor for a Discord bot.\n"
            f"Analyze the following message sent by {username}:\n"
            f"\"{content}\"\n\n"
            f"Task: Check if the message contains a LIGHT, casual personal fact about {username} "
            f"(e.g. favorite food, games they play, hobbies, pets, casual preferences).\n"
            f"DO NOT extract: mental health struggles, relationship/family conflicts, health issues, "
            f"anything sad/sensitive/embarrassing, or anything said in anger or venting.\n"
            f"If and ONLY if a light casual fact is found, reply with a single clear sentence (e.g. \"Enjoys playing Valorant\").\n"
            f"If no such fact is mentioned, or the message is sensitive/emotional/negative, reply with exactly \"NONE\"."
        )

        try:
            response = await generate_response(
                messages=[{"role": "user", "content": prompt}],
                preset_name="Helpful Professor"
            )
            fact = response.strip()
            if fact and fact != "NONE" and len(fact) < 200 and not fact.startswith("❌"):
                fact = fact.replace('"', '').strip()
                # Compute vector embedding for long-term RAG search
                embedding_vec = await get_embedding(fact)
                vec_json = json.dumps(embedding_vec) if embedding_vec else None
                await query(
                    "INSERT INTO ai_resident_memories (guild_id, user_id, fact, vector_embedding, created_at) VALUES (?,?,?,?,?)",
                    guild_id, user_id, fact, vec_json, datetime.utcnow().isoformat()
                )
                print(f"[Memory Extracted & Vectorized] {username}: {fact}")
        except Exception as e:
            print(f"Failed to extract memory: {e}")

    async def process_attachments(self, message: discord.Message) -> str:
        """Downloads and extracts text from PDF, text, and voice note / audio attachments."""
        if not message.attachments:
            return ""

        import httpx
        attachment = message.attachments[0]
        filename = attachment.filename.lower()
        content_type = (attachment.content_type or "").lower()

        # 1. Voice Notes / Audio files
        if content_type.startswith("audio/") or filename.endswith((".ogg", ".mp3", ".wav", ".m4a", ".flac", ".webm")):
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(attachment.url, timeout=25)
                    if r.status_code == 200:
                        audio_bytes = r.content
                        transcript = await transcribe_audio(audio_bytes, attachment.filename, content_type or "audio/ogg")
                        if transcript and not transcript.startswith("❌"):
                            return f"[Uploaded Voice Note / Audio Transcript ({attachment.filename}):]\n\"{transcript}\""
            except Exception as e:
                print(f"Failed transcribing voice note: {e}")

        # 2. Text / PDF files
        if filename.endswith(".pdf") or filename.endswith((".txt", ".py", ".json", ".csv", ".md")):
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(attachment.url, timeout=15)
                    if r.status_code == 200:
                        file_bytes = r.content
                    else:
                        return f"❌ Attachment download failed: HTTP {r.status_code}"

                if filename.endswith(".pdf"):
                    import io
                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                    text_parts = []
                    for i in range(min(5, len(reader.pages))):
                        page_text = reader.pages[i].extract_text()
                        if page_text:
                            text_parts.append(page_text)
                    extracted_text = "\n".join(text_parts)
                    if not extracted_text.strip():
                        return "❌ (PDF does not contain extractable text - scanned/image PDF.)"
                    return f"[Uploaded PDF Content ({filename}):]\n{extracted_text[:3000]}"
                else:
                    decoded_text = file_bytes.decode("utf-8", errors="ignore")
                    return f"[Uploaded Text File Content ({filename}):]\n{decoded_text[:3000]}"

            except Exception as e:
                return f"❌ Attachment extraction error: {e}"

        return ""

    # ─── Admin Commands ────────────────────────────────────────────────────────

    @commands.command(name="sync")
    async def sync_commands(self, ctx):
        """Force re-sync all slash commands to this guild (instant)."""
        try:
            if ctx.guild:
                self.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await self.bot.tree.sync(guild=ctx.guild)
                await self.bot.tree.sync()  # global too
                await ctx.send(f"✅ Synced {len(synced)} slash commands to **{ctx.guild.name}**! Try `/help` now.", delete_after=15)
            else:
                synced = await self.bot.tree.sync()
                await ctx.send(f"✅ Synced {len(synced)} commands globally.", delete_after=15)
        except Exception as e:
            await ctx.send(f"❌ Sync failed: {e}", delete_after=15)
