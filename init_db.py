import asyncio
import os
import sqlite3
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

-- ─── Workspace Platform Tables ────────────────────────────────────────────────

-- App Registry: every installed app is a row
CREATE TABLE IF NOT EXISTS workspace_apps (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT,
    icon_emoji   TEXT DEFAULT '📦',
    icon_color   TEXT DEFAULT '#5865f2',
    route_prefix TEXT NOT NULL,
    is_active    INTEGER DEFAULT 1,
    sort_order   INTEGER DEFAULT 0
);

-- Layer 1 RBAC: which users can access which apps
CREATE TABLE IF NOT EXISTS workspace_members (
    user_id    TEXT NOT NULL,
    app_id     TEXT NOT NULL,
    granted_by TEXT,
    granted_at TEXT,
    PRIMARY KEY (user_id, app_id)
);

-- Layer 2 RBAC: what role the user has inside an app
CREATE TABLE IF NOT EXISTS app_members (
    user_id  TEXT NOT NULL,
    app_id   TEXT NOT NULL,
    app_role TEXT NOT NULL DEFAULT 'viewer',
    PRIMARY KEY (user_id, app_id)
);

-- Layer 2 RBAC: which roles have which permissions per app
CREATE TABLE IF NOT EXISTS app_permissions (
    app_id     TEXT NOT NULL,
    role       TEXT NOT NULL,
    permission TEXT NOT NULL,
    PRIMARY KEY (app_id, role, permission)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_confessions_status ON confessions(status);
CREATE INDEX IF NOT EXISTS idx_confessions_timestamp ON confessions(timestamp);
CREATE INDEX IF NOT EXISTS idx_confessions_deleted ON confessions(deleted_at);
CREATE INDEX IF NOT EXISTS idx_banned_users_id ON banned_users(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_requests_status ON admin_requests(status);

-- Seed: default apps in the registry
INSERT INTO workspace_apps (id, display_name, description, icon_emoji, icon_color, route_prefix, is_active, sort_order) VALUES
    ('confessions', 'Confession Bot', 'Anonymous confessions platform', '💬', '#5865f2', '/confessions', 1, 1),
    ('bump_bot',    'Auto Bumper',    'Disboard auto-bump service',     '🚀', '#10b981', '/bump',         1, 2)
ON CONFLICT (id) DO NOTHING;

-- Seed: default app permissions for confessions
INSERT INTO app_permissions (app_id, role, permission) VALUES
    ('confessions', 'viewer',    'view_confessions'),
    ('confessions', 'moderator', 'view_confessions'),
    ('confessions', 'moderator', 'approve_confessions'),
    ('confessions', 'moderator', 'delete_confessions'),
    ('confessions', 'moderator', 'reply_confessions'),
    ('confessions', 'admin',     'view_confessions'),
    ('confessions', 'admin',     'approve_confessions'),
    ('confessions', 'admin',     'delete_confessions'),
    ('confessions', 'admin',     'reply_confessions'),
    ('confessions', 'admin',     'manage_members'),
    ('confessions', 'admin',     'app_settings'),
    ('bump_bot',    'viewer',    'view_logs'),
    ('bump_bot',    'operator',  'view_logs'),
    ('bump_bot',    'operator',  'configure_bump'),
    ('bump_bot',    'admin',     'view_logs'),
    ('bump_bot',    'admin',     'configure_bump'),
    ('bump_bot',    'admin',     'app_settings')
ON CONFLICT DO NOTHING;
"""

def is_postgres():
    return DATABASE_URL and (DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"))

async def run():
    if is_postgres():
        print("Connecting to PostgreSQL DB...")
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            print("Running SQL script...")
            await conn.execute(sql_script)
            print("Done!")
            await conn.close()
            return
        except Exception as e:
            print(f"PostgreSQL connection failed: {e}")
            print("Falling back to local SQLite database...")

    print("Connecting to SQLite DB (database.db)...")
    sqlite_script = sql_script.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    print("Running SQL script...")
    cursor.executescript(sqlite_script)
    conn.commit()
    conn.close()
    print("Done! Local SQLite database (database.db) initialized.")

if __name__ == "__main__":
    asyncio.run(run())
