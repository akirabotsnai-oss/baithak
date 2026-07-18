"""
core/workspace_routes.py — Workspace-level routes.

Handles:
  /               — Smart home routing (God → launcher, 1 app → redirect, etc.)
  /workspace/     — God-only workspace management
  /access-denied  — Unauthorized redirect target
  /api/workspace/ — AJAX endpoints for granting/revoking member access
"""
import bcrypt
from datetime import datetime
from quart import Blueprint, render_template, session, redirect, url_for, request, jsonify

from core.db import query, log_audit
from core.auth import (
    require_login, require_god, is_god_or_god2, is_god,
    base_ctx, ROLE_GOD, ROLE_GOD2, ROLE_ADMIN, current_role
)

workspace_bp = Blueprint("workspace", __name__, template_folder="../templates")


# ─── Smart Home Routing ───────────────────────────────────────────────────────

@workspace_bp.route("/")
async def home():
    if "user" not in session:
        return await render_template("landing.html", login_url=url_for("login"))
        
    role = current_role()
    user = session["user"]

    if is_god_or_god2():
        apps = await query(
            "SELECT * FROM workspace_apps WHERE is_active=1 ORDER BY sort_order"
        ) or []
    else:
        apps = await query(
            """SELECT wa.* FROM workspace_apps wa
               JOIN workspace_members wm ON wa.id = wm.app_id
               WHERE wm.user_id = ? AND wa.is_active = 1
               ORDER BY wa.sort_order""",
            user
        ) or []

    # Smart routing for regular members
    if not is_god_or_god2():
        if len(apps) == 0:
            return redirect(url_for("workspace.access_denied"))
        if len(apps) == 1:
            # Auto-redirect into the single assigned app
            return redirect(apps[0]["route_prefix"] + "/")

    ctx = await base_ctx()
    return await render_template(
        "home.html",
        **ctx,
        active="launcher",
        apps=apps
    )


# ─── Access Denied ────────────────────────────────────────────────────────────

@workspace_bp.route("/access-denied")
async def access_denied():
    ctx = {}
    if "user" in session:
        ctx = await base_ctx()
    return await render_template("access_denied.html", **ctx), 403


# ─── Workspace Member Management (God Only) ───────────────────────────────────

@workspace_bp.route("/workspace/members")
@require_login
@require_god
async def workspace_members():
    members = await query(
        "SELECT * FROM admins WHERE username != 'byte' ORDER BY role, username"
    ) or []
    apps = await query(
        "SELECT * FROM workspace_apps WHERE is_active=1 ORDER BY sort_order"
    ) or []

    # Build membership map: {username: {app_id: app_role}}
    raw_access = await query(
        "SELECT wm.user_id, wm.app_id, COALESCE(am.app_role, 'viewer') as app_role "
        "FROM workspace_members wm "
        "LEFT JOIN app_members am ON wm.user_id=am.user_id AND wm.app_id=am.app_id"
    ) or []

    member_access = {}
    for row in raw_access:
        uid = row["user_id"]
        if uid not in member_access:
            member_access[uid] = {}
        member_access[uid][row["app_id"]] = row["app_role"]

    # Pending staff requests
    pending_requests = await query(
        "SELECT * FROM admin_requests WHERE status='pending' ORDER BY requested_at DESC"
    ) or []

    ctx = await base_ctx()
    return await render_template(
        "workspace_members.html",
        **ctx,
        active="workspace_members",
        members=members,
        apps=apps,
        member_access=member_access,
        pending_requests=pending_requests,
    )


# ─── Workspace API — Grant / Revoke / Set Role ────────────────────────────────

@workspace_bp.route("/api/workspace/members/<username>/grant", methods=["POST"])
@require_login
@require_god
async def grant_app_access(username):
    data = await request.json or {}
    app_id = data.get("app_id")
    app_role = data.get("app_role", "viewer")
    if not app_id:
        return jsonify({"ok": False, "error": "Missing app_id"})

    await query(
        "INSERT INTO workspace_members (user_id, app_id, granted_by, granted_at) "
        "VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
        username, app_id, session["user"], datetime.utcnow().isoformat()
    )
    await query(
        "INSERT INTO app_members (user_id, app_id, app_role) VALUES (?,?,?) "
        "ON CONFLICT (user_id, app_id) DO UPDATE SET app_role=EXCLUDED.app_role",
        username, app_id, app_role
    )
    await log_audit(session["user"], "grant_app_access", f"{username} → {app_id} ({app_role})")
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/members/<username>/revoke", methods=["POST"])
@require_login
@require_god
async def revoke_app_access(username):
    data = await request.json or {}
    app_id = data.get("app_id")
    if not app_id:
        return jsonify({"ok": False, "error": "Missing app_id"})

    await query(
        "DELETE FROM workspace_members WHERE user_id=? AND app_id=?",
        username, app_id
    )
    await query(
        "DELETE FROM app_members WHERE user_id=? AND app_id=?",
        username, app_id
    )
    await log_audit(session["user"], "revoke_app_access", f"{username} ← {app_id}")
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/members/<username>/role", methods=["POST"])
@require_login
@require_god
async def set_app_role(username):
    data = await request.json or {}
    app_id = data.get("app_id")
    app_role = data.get("app_role", "viewer")
    await query(
        "INSERT INTO app_members (user_id, app_id, app_role) VALUES (?,?,?) "
        "ON CONFLICT (user_id, app_id) DO UPDATE SET app_role=EXCLUDED.app_role",
        username, app_id, app_role
    )
    await log_audit(session["user"], "set_app_role", f"{username} → {app_id} = {app_role}")
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/members/<username>/suspend", methods=["POST"])
@require_login
@require_god
async def suspend_member(username):
    data = await request.json or {}
    revoke = data.get("revoke", True)
    nv = 1 if revoke else 0
    await query("UPDATE admins SET is_revoked=? WHERE username=?", nv, username)
    action = "suspend" if revoke else "reinstate"
    await log_audit(session["user"], action, username)
    return jsonify({"ok": True, "revoked": bool(nv)})


@workspace_bp.route("/api/workspace/members/create", methods=["POST"])
@require_login
@require_god
async def create_member():
    data = await request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", ROLE_ADMIN)
    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password required"})
    if await query("SELECT 1 FROM admins WHERE username=?", username, fetch_one=True):
        return jsonify({"ok": False, "error": "User already exists"})
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await query(
        "INSERT INTO admins (username, password_hash, role, created_by) VALUES (?,?,?,?)",
        username, h, role, session["user"]
    )
    await log_audit(session["user"], "admin_create", username)
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/requests/<int:rid>/approve", methods=["POST"])
@require_login
@require_god
async def approve_request(rid):
    data = await request.json or {}
    role = data.get("role", ROLE_ADMIN)
    r = await query(
        "SELECT username, password_hash FROM admin_requests WHERE id=?", rid, fetch_one=True
    )
    if not r:
        return jsonify({"ok": False, "error": "Request not found"})
    await query(
        "INSERT INTO admins (username, password_hash, role, created_by) VALUES (?,?,?,?) "
        "ON CONFLICT DO NOTHING",
        r["username"], r["password_hash"], role, session["user"]
    )
    await query("UPDATE admin_requests SET status='approved' WHERE id=?", rid)
    await log_audit(session["user"], "admin_approve", r["username"])
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/requests/<int:rid>/reject", methods=["POST"])
@require_login
@require_god
async def reject_request(rid):
    r = await query(
        "SELECT username FROM admin_requests WHERE id=?", rid, fetch_one=True
    )
    if not r:
        return jsonify({"ok": False, "error": "Request not found"})
    await query("UPDATE admin_requests SET status='rejected' WHERE id=?", rid)
    await log_audit(session["user"], "admin_reject", r["username"])
    return jsonify({"ok": True})

@workspace_bp.route("/api/workspace/members/<username>/delete", methods=["POST"])
@require_login
@require_god
async def delete_member(username):
    if username == 'byte':
        return jsonify({"ok": False, "error": "Cannot delete root admin"})
    await query("DELETE FROM admins WHERE username=?", username)
    await query("DELETE FROM workspace_members WHERE user_id=?", username)
    await query("DELETE FROM app_members WHERE user_id=?", username)
    await log_audit(session["user"], "admin_delete", username)
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/members/<username>/global-role", methods=["POST"])
@require_login
@require_god
async def set_global_role(username):
    if username == 'byte':
        return jsonify({"ok": False, "error": "Cannot modify root admin role"})
    data = await request.json or {}
    role = data.get("role")
    if not role:
        return jsonify({"ok": False, "error": "Role required"})
    await query("UPDATE admins SET role=? WHERE username=?", role, username)
    await log_audit(session["user"], "set_global_role", f"{username} -> {role}")
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/members/<username>/reset-password", methods=["POST"])
@require_login
@require_god
async def reset_password(username):
    data = await request.json or {}
    password = data.get("password", "").strip()
    if not password:
        return jsonify({"ok": False, "error": "Password required"})
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await query("UPDATE admins SET password_hash=? WHERE username=?", h, username)
    await log_audit(session["user"], "reset_password", username)
    return jsonify({"ok": True})


@workspace_bp.route("/api/workspace/system/secret", methods=["POST"])
@require_login
@require_god
async def set_secret_url():
    import os
    data = await request.json or {}
    new_path = data.get("new_path", "").strip()
    if not new_path or not new_path.isalnum() and "-" not in new_path:
        return jsonify({"ok": False, "error": "Invalid path format (alphanumeric and dashes only)"})
    
    # Read .env and replace SECRET_PATH
    env_file = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            lines = f.readlines()
        with open(env_file, "w") as f:
            for line in lines:
                if line.startswith("SECRET_PATH="):
                    f.write(f"SECRET_PATH={new_path}\n")
                else:
                    f.write(line)
                    
    os.environ["SECRET_PATH"] = new_path
    await log_audit(session["user"], "system_config", f"Changed secret path to {new_path}")
    return jsonify({"ok": True})
