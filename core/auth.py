"""
core/auth.py — Authentication, session helpers, and RBAC middleware.

Two-layer permission model:
  Layer 1: require_app_access(app_id)   — can user ACCESS this app?
  Layer 2: require_app_permission(...)  — can user DO this action inside an app?

God/God2 bypass both layers automatically.
"""
from functools import wraps
from quart import session, redirect, url_for
from core.db import query, cfg

ROLE_GOD   = 'god'
ROLE_GOD2  = 'god2'
ROLE_ADMIN = 'admin'


def current_role() -> str:
    if session.get('is_main') or session.get('user') == 'byte':
        return ROLE_GOD
    return session.get('role') or ROLE_ADMIN


def is_god() -> bool:
    return current_role() == ROLE_GOD


def is_god_or_god2() -> bool:
    return current_role() in (ROLE_GOD, ROLE_GOD2)


# ─── Layer 0: Must be logged in ───────────────────────────────────────────────

def require_login(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/")
        return await f(*args, **kwargs)
    return decorated


def require_god(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if not is_god():
            return redirect(url_for("workspace.home"))
        return await f(*args, **kwargs)
    return decorated


# ─── Layer 1: App Access (Workspace Membership) ───────────────────────────────

def require_app_access(app_id: str):
    """
    Decorator factory. Checks that the logged-in user has been granted
    access to `app_id` in workspace_members. God/God2 bypass this check.
    """
    def decorator(f):
        @wraps(f)
        async def decorated(*args, **kwargs):
            if "user" not in session:
                return redirect(url_for("login"))
            if is_god_or_god2():
                return await f(*args, **kwargs)
            user = session["user"]
            row = await query(
                "SELECT 1 FROM workspace_members WHERE user_id=? AND app_id=?",
                user, app_id, fetch_one=True
            )
            if not row:
                return redirect(url_for("workspace.access_denied"))
            return await f(*args, **kwargs)
        return decorated
    return decorator


# ─── Layer 2: App Permission (Role inside app) ────────────────────────────────

def require_app_permission(app_id: str, permission: str):
    """
    Decorator factory. Checks that the user's app_role has `permission`
    in app_permissions. God/God2 bypass this check.
    """
    def decorator(f):
        @wraps(f)
        async def decorated(*args, **kwargs):
            if "user" not in session:
                return redirect(url_for("login"))
            if is_god_or_god2():
                return await f(*args, **kwargs)
            user = session["user"]
            role_row = await query(
                "SELECT app_role FROM app_members WHERE user_id=? AND app_id=?",
                user, app_id, fetch_one=True
            )
            if not role_row:
                return redirect(url_for("workspace.access_denied"))
            perm_row = await query(
                "SELECT 1 FROM app_permissions WHERE app_id=? AND role=? AND permission=?",
                app_id, role_row["app_role"], permission, fetch_one=True
            )
            if not perm_row:
                return redirect(url_for("workspace.access_denied"))
            return await f(*args, **kwargs)
        return decorated
    return decorator


# ─── Shared Template Context ──────────────────────────────────────────────────

async def base_ctx() -> dict:
    """
    Build the context dict passed to every template render.
    Includes: user info, role flags, badge counts, accessible apps for sidebar.
    """
    role = current_role()
    user = session.get("user", "")

    # DB counts
    qcount    = await query("SELECT COUNT(*) FROM confessions WHERE status='quarantine' AND deleted_at IS NULL", fetch_one=True)
    bcount    = await query("SELECT COUNT(*) FROM confessions WHERE deleted_at IS NOT NULL", fetch_one=True)
    req_count = await query("SELECT COUNT(*) FROM admin_requests WHERE status='pending'", fetch_one=True)

    # Apps available in the sidebar — God sees all, members see granted apps
    if is_god_or_god2():
        accessible_apps = await query(
            "SELECT * FROM workspace_apps WHERE is_active=1 ORDER BY sort_order"
        ) or []
    else:
        accessible_apps = await query(
            """SELECT wa.* FROM workspace_apps wa
               JOIN workspace_members wm ON wa.id = wm.app_id
               WHERE wm.user_id = ? AND wa.is_active = 1
               ORDER BY wa.sort_order""",
            user
        ) or []

    all_cfgs = await query("SELECT key, value FROM config_store") or []
    config_dict = {r["key"]: r["value"] for r in all_cfgs}

    import os
    return dict(
        user=user,
        is_main=session.get("is_main", False),
        role=role,
        secret_path=await cfg("secret_path", "cmd-9x4k2"),
        accessible_apps=accessible_apps,
        can_export=role in (ROLE_GOD, ROLE_GOD2),
        can_perm_delete=role in (ROLE_GOD, ROLE_GOD2),
        can_recycle_bin=role in (ROLE_GOD, ROLE_GOD2),
        can_settings=role in (ROLE_GOD, ROLE_GOD2),
        can_branding=role == ROLE_GOD,
        can_manage_admins=role == ROLE_GOD,
        bot_pfp=await cfg("bot_pfp_url"),
        bot_name=await cfg("bot_name", "Confession Bot"),
        embed_color=await cfg("embed_color", "5865F2"),
        q_count=qcount[0] if qcount else 0,
        bin_count=bcount[0] if bcount else 0,
        req_count=req_count[0] if req_count else 0,
        embed_title=await cfg("embed_title", "Anonymous Confession"),
        embed_footer=await cfg("embed_footer", "Confession | React below!"),
        cfg=lambda k, d="": config_dict.get(k, d) if config_dict.get(k) is not None else d,
    )
