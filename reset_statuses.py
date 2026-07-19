"""Reset stuck bump account statuses so the loop re-auths them on next start."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def reset():
    import asyncpg
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("No DATABASE_URL found in .env")
        return
    pool = await asyncpg.create_pool(url)
    async with pool.acquire() as con:
        result = await con.execute(
            "UPDATE bump_accounts SET status='Offline' "
            "WHERE status NOT IN ('Error: Bad Token', 'Error: Command Fetch Failed')"
        )
        print("Reset result:", result)
        rows = await con.fetch(
            "SELECT id, name, status, is_enabled, last_bump_time, bump_count FROM bump_accounts"
        )
        if rows:
            for r in rows:
                print(dict(r))
        else:
            print("No bump accounts found in DB.")
    await pool.close()

asyncio.run(reset())
