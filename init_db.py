import asyncio
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

sql_script = """
CREATE TABLE IF NOT EXISTS confessions (
    id TEXT PRIMARY KEY, 
    user_id TEXT NOT NULL, 
    username TEXT NOT NULL,
    content TEXT NOT NULL, 
    image_url TEXT, 
    public_msg TEXT, 
    admin_msg TEXT,
    timestamp TEXT NOT NULL, 
    status TEXT DEFAULT 'posted',
    confession_number INTEGER, 
    reply_to TEXT,
    is_starred INTEGER DEFAULT 0, 
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS banned_users (
    user_id TEXT PRIMARY KEY, 
    banned_by TEXT, 
    reason TEXT, 
    banned_at TEXT
);

CREATE TABLE IF NOT EXISTS config_store (
    key TEXT PRIMARY KEY, 
    value TEXT
);

CREATE TABLE IF NOT EXISTS blacklist_words (
    word TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS cooldowns (
    user_id TEXT PRIMARY KEY, 
    last_used TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    username TEXT PRIMARY KEY, 
    password_hash TEXT NOT NULL,
    is_main_admin INTEGER DEFAULT 0, 
    role TEXT DEFAULT 'admin',
    is_revoked INTEGER DEFAULT 0,
    last_login TEXT, 
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY, 
    username TEXT NOT NULL,
    action TEXT NOT NULL, 
    details TEXT, 
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_notes (
    id SERIAL PRIMARY KEY, 
    user_id TEXT,
    confession_id TEXT, 
    note TEXT NOT NULL,
    added_by TEXT NOT NULL, 
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitor_logs (
    id SERIAL PRIMARY KEY,
    ip_address TEXT,
    user_agent TEXT,
    referer TEXT,
    timestamp TEXT NOT NULL
);

-- Insert Default Configuration
INSERT INTO config_store (key, value) VALUES 
('enabled','1'), ('cooldown','0'), ('slowdown','0'), ('min_account_age_days','0'),
('bot_name','Confession Bot'), ('bot_pfp_url',''),
('embed_title','Anonymous Confession'), ('embed_footer','Confession | React below!'),
('embed_color','5865F2'),
('msg_success','✅ Your message was sent anonymously into the void.'),
('msg_cooldown','⏳ The void is currently busy. Please wait {wait}s.'),
('msg_shadowban','✅ Your message was sent anonymously into the void.'),
('msg_paused','⏸️ The void is temporarily closed.'),
('msg_tooyoung','❌ Your account is too new to confess.'),
('auto_ban_threshold','3')
ON CONFLICT (key) DO NOTHING;

-- Insert Default Admins (Passwords: [username]123)
-- E.g., username 'byte', password 'byte123'
INSERT INTO admins (username, password_hash, is_main_admin, role, created_by) VALUES 
('byte', '$2b$12$D23/D.3gW7p7h.5G3Wz1qO1z3A/fGf7T2B9R6H8p8u5/6c1qVzY3.', 1, 'god', 'system'),
('tuktuk', '$2b$12$D23/D.3gW7p7h.5G3Wz1qO1z3A/fGf7T2B9R6H8p8u5/6c1qVzY3.', 0, 'admin', 'system')
ON CONFLICT (username) DO NOTHING;
"""

async def run():
    print("Connecting to DB...")
    conn = await asyncpg.connect(DATABASE_URL)
    print("Running SQL script...")
    await conn.execute(sql_script)
    print("Done!")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
