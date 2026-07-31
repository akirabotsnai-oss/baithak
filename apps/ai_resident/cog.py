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
from apps.ai_resident.llm import generate_response, get_embedding

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
        # channel_id -> last message seen timestamp
        self.last_message_time = defaultdict(float)
        # channel_id -> GameState
        self.active_games = {}
        
        # Self-harm/danger keywords
        self.danger_keywords = [
            "suicide", "suicidal", "kill myself", "end my life", "self harm", 
            "cutting myself", "depressed and want to die", "mar jana chahta", "zehar"
        ]
        # Scam patterns
        self.scam_patterns = [
            r"free.*nitro", r"discord.*gift", r"steam.*gift", r"crypto.*double",
            r"get.*free.*money", r"leak.*onlyfans", r"hack.*robux"
        ]
        self.tasks_started = False

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

        # 1. Moderation & Danger Pre-Filter
        await self.process_moderation_filter(message)

        # 2. Check Guild Active Status
        guild_id = str(message.guild.id) if message.guild else "dm"
        if guild_id == "dm":
            return # Skip DM memories mixing into guild memory

        # Log seen message
        await self.log_stat(guild_id, is_reply=False)
        self.last_message_time[message.channel.id] = time.time()

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
        is_mention = self.bot.user in message.mentions
        is_direct_reply = (
            message.reference and 
            message.reference.resolved and 
            message.reference.resolved.author == self.bot.user
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
            
            # Allow random reply if channel is active and configured
            active_channels = (await self.get_guild_cfg(guild_id, "active_channels", "")).split(",")
            active_channels = [c.strip() for c in active_channels if c.strip()]
            
            if (not active_channels or str(message.channel.id) in active_channels) and random.random() < chance:
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
        
        # Load Personality Preset
        preset = await self.get_guild_cfg(guild_id, "personality", "Roast Hyderabadi")
        custom_prompt = await self.get_guild_cfg(guild_id, "custom_prompt", "")
        
        # Load Memory Toggle
        memory_enabled = await self.get_guild_cfg(guild_id, "memory_enabled", "1") == "1"
        user_memory_context = ""
        
        if memory_enabled:
            user_memory_context = await self.fetch_user_memories(message.guild.id, message.author.id, message.content)

        # Load learned guild style notes
        style_row = await query("SELECT slang_words, accent_notes FROM ai_resident_style_notes WHERE guild_id=?", guild_id, fetch_one=True)
        style_notes = ""
        if style_row:
            style_notes = f"Slang words popular here: {style_row['slang_words'] or 'none'}. Accent details: {style_row['accent_notes'] or 'none'}."

        # Compile System Prompts
        system_instructions = custom_prompt if custom_prompt else None
        
        # Add user memory facts if they exist
        messages_payload = []
        if user_memory_context:
            messages_payload.append({
                "role": "user",
                "content": f"[SYSTEM NOTIFICATION: Known facts about user {message.author.display_name}:\n{user_memory_context}]"
            })
            
        messages_payload.extend(context)

        # Generate Response
        response = await generate_response(
            messages=messages_payload,
            preset_name=preset,
            custom_system_prompt=system_instructions,
            guild_style_notes=style_notes
        )

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
        """Analyzes messages and updates style slang words dynamically."""
        # Simple extraction of interesting words (Hinglish/slang words that look casual)
        text = message.content.lower()
        words = re.findall(r"\b[a-z]{3,15}\b", text)
        casual_slang_candidates = ["bhai", "yaar", "arre", " scene", "baigan", "nakko", "hallu", "lite", "potti", "hau", "kya re", "chal", "mast", "ekdum"]
        
        detected = [w for w in words if w in casual_slang_candidates]
        if detected:
            guild_id = str(message.guild.id)
            row = await query("SELECT slang_words FROM ai_resident_style_notes WHERE guild_id=?", guild_id, fetch_one=True)
            
            existing = set()
            if row and row["slang_words"]:
                existing = set(w.strip() for w in row["slang_words"].split(",") if w.strip())
            
            existing.update(detected)
            slang_str = ",".join(existing)
            
            # Simple accent description based on common words used
            accent = "Hyderabadi / Hinglish blend using casual words like: " + ", ".join(list(existing)[:8])
            
            await query(
                "INSERT INTO ai_resident_style_notes (guild_id, slang_words, accent_notes, last_updated) VALUES (?,?,?,?) "
                "ON CONFLICT (guild_id) DO UPDATE SET slang_words=EXCLUDED.slang_words, accent_notes=EXCLUDED.accent_notes, last_updated=EXCLUDED.last_updated",
                guild_id, slang_str, accent, datetime.utcnow().isoformat()
            )

    async def fetch_user_memories(self, guild_id, user_id, message_text: str) -> str:
        """Looks up existing memories about the user."""
        # Check if user opted out
        opt = await query("SELECT 1 FROM ai_resident_opt_out WHERE user_id=?", str(user_id), fetch_one=True)
        if opt:
            return ""

        # Perform simple keyword similarity matching
        words = [w.lower() for w in re.findall(r"\b\w{3,12}\b", message_text)]
        if not words:
            # Fall back to returning last 3 memories
            rows = await query(
                "SELECT fact FROM ai_resident_memories WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 3",
                str(guild_id), str(user_id)
            ) or []
            return "\n".join([f"- {r['fact']}" for r in rows])

        # Find memories that contain matching keywords
        likes_clauses = " OR ".join(["fact ILIKE ?"] * len(words))
        params = [str(guild_id), str(user_id)] + [f"%{w}%" for w in words]
        
        rows = await query(
            f"SELECT fact FROM ai_resident_memories WHERE guild_id=? AND user_id=? AND ({likes_clauses}) ORDER BY id DESC LIMIT 4",
            *params
        ) or []

        if not rows:
            # Fallback to general list if no keyword match
            rows = await query(
                "SELECT fact FROM ai_resident_memories WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 3",
                str(guild_id), str(user_id)
            ) or []

        return "\n".join([f"- {r['fact']}" for r in rows])

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

    @app_commands.command(name="imagine", description="Generate an image from a prompt")
    async def imagine(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer()
        
        # Check pluggable API key
        replicate_key = await cfg("REPLICATE_API_TOKEN")
        stability_key = await cfg("STABILITY_API_KEY")
        
        if not replicate_key and not stability_key:
            # Fallback to Pollinations AI (free, no keys required!)
            url = f"https://image.pollinations.ai/prompt/{random.randint(1000,9999)}_{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true"
            embed = discord.Embed(title=f"🎨 Imagine: {prompt}", color=0x8a2be2)
            embed.set_image(url=url)
            return await interaction.followup.send(embed=embed)
        
        # If Stability AI is configured
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

        # Fallback to Pollinations AI if call fails
        url = f"https://image.pollinations.ai/prompt/{random.randint(1000,9999)}_{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true"
        embed = discord.Embed(title=f"🎨 Imagine: {prompt}", color=0x8a2be2)
        embed.set_image(url=url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="meme", description="Generate a captioned meme")
    async def meme(self, interaction: discord.Interaction, caption: str, prompt: str = "funny template cartoon"):
        await interaction.response.defer()
        
        # We can fetch image from Pollinations and overlay text, or use Memegen API
        # A fun approach: generate clean image using Pollinations, then use Memegen/apitemplate or display it beautifully
        # Let's display it via Pollinations and include captioned text inside an embed
        url = f"https://image.pollinations.ai/prompt/{random.randint(1000,9999)}_{prompt.replace(' ', '%20')}?width=800&height=800&nologo=true"
        embed = discord.Embed(title=caption, color=0xf1c40f)
        embed.set_image(url=url)
        embed.set_footer(text=f"Template prompt: {prompt}")
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

        words = ["hyderabad", "biryani", "discord", "confession", "resident", "antigravity", "baigan"]
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

    # Hook the game listener into general on_message via setup
    # Note: discord.py events run in parallel. We can call game check manually in general on_message or register another listener.
    @commands.Cog.listener()
    async def on_message_games(self, message: discord.Message):
        await self.on_message_game_check(message)

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
            f"Task: Check if the message contains any permanent personal fact about {username} "
            f"(e.g. their birthday, favorite food, games they play, dislikes, location, name, hobbies, etc.).\n"
            f"If and ONLY if a fact is found, reply with a single clear sentence summarizing the fact (e.g. \"Enjoys playing Valorant\").\n"
            f"If no personal fact is mentioned, reply with exactly \"NONE\"."
        )

        try:
            response = await generate_response(
                messages=[{"role": "user", "content": prompt}],
                preset_name="Helpful Professor"
            )
            fact = response.strip()
            if fact and fact != "NONE" and len(fact) < 200 and not fact.startswith("❌"):
                fact = fact.replace('"', '').strip()
                await query(
                    "INSERT INTO ai_resident_memories (guild_id, user_id, fact, created_at) VALUES (?,?,?,?)",
                    guild_id, user_id, fact, datetime.utcnow().isoformat()
                )
                print(f"[Memory Extracted] {username}: {fact}")
        except Exception as e:
            print(f"Failed to extract memory: {e}")

    async def process_attachments(self, message: discord.Message) -> str:
        """Downloads and extracts text from PDF and text attachments."""
        if not message.attachments:
            return ""

        import httpx
        attachment = message.attachments[0]
        filename = attachment.filename.lower()

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


