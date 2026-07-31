import os
import re
import secrets
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands
from core.db import query, cfg

GUILD_ID          = int(os.environ.get("GUILD_ID", "0") or "0")
PUBLIC_CHANNEL_ID = os.environ.get("PUBLIC_CHANNEL_ID", "")
ADMIN_CHANNEL_ID  = os.environ.get("ADMIN_CHANNEL_ID", "")

def generate_id():
    return "C-" + secrets.token_hex(4)

class ConfessionsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="confess", description="Submit an anonymous message into the void")
    async def confess(self, interaction: discord.Interaction, message: str, image: str = None):
        user_id  = str(interaction.user.id)
        username = str(interaction.user)

        # Defer response
        await interaction.response.defer(ephemeral=True)

        # Guild lock: only allow confessions from the configured server
        allowed_guild = await cfg("guild_id", str(GUILD_ID))
        if allowed_guild and allowed_guild != "0":
            if not interaction.guild or str(interaction.guild.id) != allowed_guild:
                return await interaction.followup.send(
                    "❌ This bot is private and only works in its home server.",
                    ephemeral=True
                )

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

        # Self-harm / Danger flag pre-filter
        danger_kws = ["suicide", "suicidal", "kill myself", "end my life", "self harm", "cutting myself", "depressed and want to die", "mar jana chahta", "zehar"]
        pub_ch_id  = await cfg("public_channel_id", PUBLIC_CHANNEL_ID)
        if any(w in message.lower() for w in danger_kws):
            admin_ch_id = await cfg("admin_channel_id", ADMIN_CHANNEL_ID)
            if admin_ch_id:
                try:
                    admin_channel = self.bot.get_channel(int(admin_ch_id))
                    if admin_channel:
                        embed = discord.Embed(
                            title="🚨 CONFESSION DANGER ALERT (Self-Harm Detected)",
                            description=f"An anonymous confession triggered self-harm detection.\n\n**Content:** *\"{message}\"*",
                            color=0xFF0000,
                            timestamp=datetime.utcnow()
                        )
                        await admin_channel.send(content="@everyone Potential danger detected in anonymous confession!", embed=embed)
                except Exception as e:
                    print("Failed to route self-harm confession alert:", e)
            
            conf_id = generate_id()
            max_num = (await query("SELECT MAX(confession_number) FROM confessions", fetch_one=True))[0]
            conf_num = (max_num or 0) + 1
            reply_to = None
            success_msg = (await cfg("msg_success")).replace("{channel}", f"<#{pub_ch_id}>")
            await query(
                "INSERT INTO confessions (id, user_id, username, content, image_url, public_msg, "
                "timestamp, status, confession_number, reply_to) VALUES (?,?,?,?,?,?,?,?,?,?)",
                conf_id, user_id, username, message, image, None,
                datetime.utcnow().isoformat(), 'danger_flagged', conf_num, reply_to
            )
            return await interaction.followup.send(success_msg, ephemeral=True)

        words  = [r[0].lower() for r in await query("SELECT word FROM blacklist_words")]
        status = 'quarantine' if any(w in message.lower() for w in words) else 'posted'

        conf_id  = generate_id()
        max_num  = (await query("SELECT MAX(confession_number) FROM confessions", fetch_one=True))[0]
        conf_num = (max_num or 0) + 1

        reply_match = re.match(r'^reply to #(\d+):?', message, re.IGNORECASE)
        reply_to    = reply_match.group(1) if reply_match else None

        pub_msg_id = None
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
                pub_channel = self.bot.get_channel(int(pub_ch_id))
                if pub_channel:
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
