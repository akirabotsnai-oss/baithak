"""
core/db.py — Shared database helpers for the Workspace Platform.
All apps import from here. Zero app-specific logic lives here.
"""
import os
from datetime import datetime
from dotenv import load_dotenv
import asyncpg

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

db_pool = None


async def init_db_pool():
    """Called once at startup from app.py before_serving."""
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    return db_pool


async def query(sql: str, *args, fetch_one: bool = False):
    """Execute a SQL query. Returns rows for SELECT, None for mutations."""
    if not db_pool:
        return None
    # Safely convert SQLite ? placeholders to PostgreSQL $1, $2, ... ignoring those inside strings
    new_sql = ""
    in_str = False
    param_idx = 1
    for char in sql:
        if char == "'":
            in_str = not in_str
        if char == '?' and not in_str:
            new_sql += f"${param_idx}"
            param_idx += 1
        else:
            new_sql += char
    async with db_pool.acquire() as con:
        upper = sql.strip().upper()
        if upper.startswith(("SELECT", "RETURNING", "WITH")):
            if fetch_one:
                return await con.fetchrow(new_sql, *args)
            return await con.fetch(new_sql, *args)
        else:
            return await con.execute(new_sql, *args)


async def cfg(key: str, default: str = "") -> str:
    """Get a value from config_store."""
    row = await query("SELECT value FROM config_store WHERE key=?", key, fetch_one=True)
    if row:
        return str(row["value"])
    return default


async def set_cfg(key: str, value) -> None:
    """Upsert a value in config_store."""
    await query(
        "INSERT INTO config_store (key,value) VALUES (?,?) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        key, str(value)
    )


async def log_audit(username: str, action: str, details: str = "") -> None:
    """Insert an audit log entry."""
    await query(
        "INSERT INTO audit_logs (username,action,details,timestamp) VALUES (?,?,?,?)",
        username, action, details, datetime.utcnow().isoformat()
    )
