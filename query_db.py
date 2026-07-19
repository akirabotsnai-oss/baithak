import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://postgres.uoznrjbkctazkieedpkx:akirabotsnai@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres')
    rows = await conn.fetch('SELECT * FROM bump_accounts')
    for row in rows:
        print(dict(row))
    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
