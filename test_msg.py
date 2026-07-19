import asyncio
import httpx
from core.crypto import decrypt_token
import sqlite3
import os

_HEADERS = lambda token: {
    "Authorization": token,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

async def send_discord_channel_message(token: str, channel_id: str, content: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=_HEADERS(token), json={"content": content}, timeout=8
            )
            print(f"Status Code: {r.status_code}")
            print(f"Response: {r.text}")
    except Exception as e:
        print(f"Exception: {e}")

async def test():
    import asyncpg
    conn = await asyncpg.connect(os.environ.get("DATABASE_URL"))
    rows = await conn.fetch("SELECT * FROM bump_accounts WHERE is_enabled=1")
    for acc in rows:
        token = decrypt_token(acc["token"])
        channel_id = acc["channel_id"]
        print(f"Testing for account {acc['name']}...")
        await send_discord_channel_message(token, channel_id, "test message")
    await conn.close()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(test())
