"""
apps/confessions/routes.py — All Confession Bot web routes.

All routes are prefixed /confessions via the blueprint url_prefix.
All routes require @require_app_access("confessions").
"""
import asyncio
import urllib.parse
from datetime import datetime, timedelta

import httpx
from quart import render_template, request, session, jsonify, redirect, url_for, Response

from apps.confessions import confessions_bp
from core.auth import require_login, require_app_access, base_ctx, is_god, is_god_or_god2, current_role, ROLE_GOD, ROLE_GOD2
from core.db import query, cfg, set_cfg, log_audit

import os
BOT_TOKEN         = os.environ.get("BOT_TOKEN", "")
PUBLIC_CHANNEL_ID = os.environ.get("PUBLIC_CHANNEL_ID", "")
ADMIN_CHANNEL_ID  = os.environ.get("ADMIN_CHANNEL_ID", "")


def safe_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ─── Redirect root /confessions → /confessions/overview ──────────────────────

@confessions_bp.route("/")
@require_login
@require_app_access("confessions")
async def confessions_index():
    return redirect(url_for("confessions.overview"))


# ─── Overview Dashboard ───────────────────────────────────────────────────────

@confessions_bp.route("/overview")
@require_login
@require_app_access("confessions")
async def overview():
    today = datetime.utcnow().date().isoformat()
    days = [(datetime.utcnow() - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
    start_date = days[0]

    results = await asyncio.gather(
        query("SELECT COUNT(*) FROM confessions", fetch_one=True),
        query("SELECT COUNT(*) FROM confessions WHERE status='quarantine'", fetch_one=True),
        query("SELECT COUNT(*) FROM banned_users", fetch_one=True),
        query("SELECT COUNT(*) FROM confessions WHERE timestamp LIKE ?", today + "%", fetch_one=True),
        query("SELECT id, content, status, timestamp FROM confessions ORDER BY timestamp DESC LIMIT 6"),
        query("SELECT SUBSTR(timestamp, 1, 10) as d, COUNT(*) as c FROM confessions WHERE timestamp >= ? GROUP BY SUBSTR(timestamp, 1, 10)", start_date),
        base_ctx()
    )
    total, qcount, banned, today_c, recent, chart_data, ctx = results

    chart_dict = {row['d']: row['c'] for row in chart_data} if chart_data else {}
    chart_counts = [chart_dict.get(d, 0) for d in days]
    chart_days = [d[5:] for d in days]

    return await render_template(
        "confessions.html", **ctx,
        active="overview",
        total=total[0], qcount=qcount[0], banned=banned[0],
        today_c=today_c[0], chart_days=chart_days,
        chart_counts=chart_counts, recent=recent
    )


# ─── Confession Feed ──────────────────────────────────────────────────────────

@confessions_bp.route("/feed")
@require_login
@require_app_access("confessions")
async def feed():
    page = int(request.args.get("page", 1))
    limit, offset = 50, (page - 1) * 50
    results = await asyncio.gather(
        query("SELECT * FROM confessions WHERE deleted_at IS NULL ORDER BY timestamp DESC LIMIT ? OFFSET ?", limit, offset),
        query("SELECT COUNT(*) FROM confessions WHERE deleted_at IS NULL", fetch_one=True),
        base_ctx()
    )
    rows, total_res, ctx = results
    total = total_res[0]
    return await render_template(
        "confessions.html", **ctx, active="feed",
        confessions=rows, page=page,
        pages=max(1, (total + limit - 1) // limit)
    )


# ─── Recycle Bin ─────────────────────────────────────────────────────────────

@confessions_bp.route("/recycle-bin")
@require_login
@require_app_access("confessions")
async def recycle_bin():
    rows = await query("SELECT * FROM confessions WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
    ctx = await base_ctx()
    return await render_template(
        "confessions.html", **ctx, active="recycle_bin",
        confessions=rows,
        thirty_days_ago=(datetime.utcnow() - timedelta(days=30)).date().isoformat()
    )


# ─── Quarantine ───────────────────────────────────────────────────────────────

@confessions_bp.route("/quarantine")
@require_login
@require_app_access("confessions")
async def quarantine():
    rows = await query("SELECT * FROM confessions WHERE status='quarantine' ORDER BY timestamp DESC")
    ctx = await base_ctx()
    return await render_template("confessions.html", **ctx, active="quarantine", confessions=rows)


# ─── Bans ─────────────────────────────────────────────────────────────────────

@confessions_bp.route("/bans")
@require_login
@require_app_access("confessions")
async def bans():
    rows = await query("SELECT * FROM banned_users ORDER BY banned_at DESC")
    ctx = await base_ctx()
    return await render_template("confessions.html", **ctx, active="bans", bans=rows)


# ─── User Lookup ──────────────────────────────────────────────────────────────

@confessions_bp.route("/users")
@require_login
@require_app_access("confessions")
async def user_info():
    q = request.args.get("q", "").strip()
    profile, history, is_banned = None, [], False
    if q:
        row = await query(
            "SELECT user_id, username, COUNT(*) as total, MIN(timestamp) as first_seen, "
            "MAX(timestamp) as last_seen FROM confessions WHERE user_id=? OR username LIKE ? "
            "GROUP BY user_id, username",
            q, f"%{q}%", fetch_one=True
        )
        if row:
            ban = await query("SELECT * FROM banned_users WHERE user_id=?", row["user_id"], fetch_one=True)
            history = await query("SELECT * FROM confessions WHERE user_id=? ORDER BY timestamp DESC", row["user_id"])
            is_banned = ban is not None
            profile = {
                "user_id": row["user_id"], "username": row["username"],
                "total": row["total"], "first_seen": row["first_seen"],
                "last_seen": row["last_seen"], "ban": dict(ban) if ban else None
            }
        else:
            ban = await query("SELECT * FROM banned_users WHERE user_id=?", q, fetch_one=True)
            is_banned = ban is not None
    ctx = await base_ctx()
    return await render_template(
        "confessions.html", **ctx, active="user_info",
        q=q, profile=profile, history=history,
        is_banned=is_banned, confessions=history
    )


# ─── Settings ─────────────────────────────────────────────────────────────────

@confessions_bp.route("/settings")
@require_login
@require_app_access("confessions")
async def settings():
    words = [r[0] for r in await query("SELECT word FROM blacklist_words")]
    ctx = await base_ctx()
    return await render_template(
        "confessions.html", **ctx, active="settings",
        blacklist=words,
        enabled=await cfg("enabled", "1") == "1",
        cooldown=safe_int(await cfg("cooldown"), 0),
        slowdown=safe_int(await cfg("slowdown"), 0),
        min_age=safe_int(await cfg("min_account_age_days"), 0),
        msg_success=await cfg("msg_success"),
        msg_cooldown=await cfg("msg_cooldown"),
        msg_shadowban=await cfg("msg_shadowban"),
        msg_paused=await cfg("msg_paused"),
        msg_tooyoung=await cfg("msg_tooyoung"),
        guild_id=await cfg("guild_id"),
        public_channel_id=await cfg("public_channel_id", PUBLIC_CHANNEL_ID),
        admin_channel_id=await cfg("admin_channel_id", ADMIN_CHANNEL_ID),
    )


# ─── Audit Logs ───────────────────────────────────────────────────────────────

@confessions_bp.route("/audit")
@require_login
@require_app_access("confessions")
async def audit():
    action_f = request.args.get("action", "")
    admin_f = request.args.get("admin", "")
    q_sql, params = "SELECT * FROM audit_logs WHERE 1=1", []
    if not is_god():
        god_row = await query("SELECT username FROM admins WHERE role='god'", fetch_one=True)
        if god_row:
            q_sql += " AND username != ?"
            params.append(god_row["username"])
    if action_f:
        q_sql += " AND action LIKE ?"
        params.append(f"%{action_f}%")
    if admin_f:
        q_sql += " AND username=?"
        params.append(admin_f)
    q_sql += " ORDER BY timestamp DESC LIMIT 500"
    logs = await query(q_sql, *params)
    admins = [r[0] for r in await query("SELECT username FROM admins")]
    ctx = await base_ctx()
    return await render_template(
        "confessions.html", **ctx, active="audit",
        logs=logs, admins=admins,
        action_filter=action_f, admin_filter=admin_f
    )


# ─── Security Logs ────────────────────────────────────────────────────────────

@confessions_bp.route("/security")
@require_login
@require_app_access("confessions")
async def security():
    if current_role() not in (ROLE_GOD, ROLE_GOD2):
        return redirect(url_for("confessions.overview"))
    logs = await query("SELECT * FROM visitor_logs ORDER BY timestamp DESC LIMIT 500")
    ctx = await base_ctx()
    return await render_template("confessions.html", **ctx, active="security", logs=logs)


# ─── Export ───────────────────────────────────────────────────────────────────

@confessions_bp.route("/export")
@require_login
@require_app_access("confessions")
async def export_feed():
    rows = await query(
        "SELECT confession_number, id, username, user_id, content, status, timestamp "
        "FROM confessions WHERE deleted_at IS NULL ORDER BY confession_number"
    )
    import csv as csv_mod, io
    output = io.StringIO()
    writer = csv_mod.writer(output)
    writer.writerow(["#", "ID", "Username", "User ID", "Content", "Status", "Timestamp"])
    for r in rows:
        content = str(r["content"])
        if content and content[0] in ('=', '+', '-', '@'):
            content = "'" + content
        writer.writerow([r["confession_number"], r["id"], r["username"], r["user_id"], content, r["status"], r["timestamp"]])
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=confessions.csv"}
    )


# ─── API Routes ───────────────────────────────────────────────────────────────

@confessions_bp.route("/api/<path:path>", methods=["POST", "GET"])
@require_login
@require_app_access("confessions")
async def api(path):
    try:
        req = await request.json if request.method == "POST" else {}
        if req is None:
            req = {}
    except Exception:
        req = {}
    u = session.get("user")

    # confession/<id>/action
    if path.startswith("confession/"):
        parts = path.split("/")
        cid = parts[1]
        action = parts[2] if len(parts) > 2 else ""

        if action == "soft-delete":
            await query("UPDATE confessions SET deleted_at=? WHERE id=?", datetime.utcnow().isoformat(), cid)
            await log_audit(u, "soft_delete", f"#{cid}")
            return jsonify({"ok": True})
        if action == "restore":
            await query("UPDATE confessions SET deleted_at=NULL WHERE id=?", cid)
            await log_audit(u, "restore_confession", f"#{cid}")
            return jsonify({"ok": True})
        if action == "permanent-delete":
            await query("DELETE FROM confessions WHERE id=?", cid)
            await log_audit(u, "permanent_delete", f"#{cid}")
            return jsonify({"ok": True})
        if action == "star":
            r = await query("SELECT is_starred FROM confessions WHERE id=?", cid, fetch_one=True)
            if r:
                new_v = 0 if r["is_starred"] else 1
                await query("UPDATE confessions SET is_starred=? WHERE id=?", new_v, cid)
                await log_audit(u, "star_confession" if new_v else "unstar_confession", f"#{cid}")
                return jsonify({"ok": True, "starred": bool(new_v)})
        if action == "note":
            if len(parts) > 4 and parts[4] == "delete":
                await query("DELETE FROM admin_notes WHERE id=?", int(parts[3]))
            else:
                await query(
                    "INSERT INTO admin_notes (confession_id, note, added_by, timestamp) VALUES (?,?,?,?)",
                    cid, req.get("note"), u, datetime.utcnow().isoformat()
                )
            return jsonify({"ok": True})
        if action == "shadowban":
            r = await query("SELECT user_id FROM confessions WHERE id=?", cid, fetch_one=True)
            if r:
                await query(
                    "INSERT INTO banned_users (user_id,banned_by,reason,banned_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    r["user_id"], u, req.get("reason"), datetime.utcnow().isoformat()
                )
                await query("UPDATE confessions SET deleted_at=? WHERE id=?", datetime.utcnow().isoformat(), cid)
                await log_audit(u, "shadowban", f"User: {r['user_id']} via #{cid}")
            return jsonify({"ok": True})
        if not action:
            c = await query("SELECT * FROM confessions WHERE id=?", cid, fetch_one=True)
            n = await query("SELECT * FROM admin_notes WHERE confession_id=? ORDER BY timestamp DESC", cid)
            return jsonify({"ok": bool(c), "confession": dict(c) if c else None, "notes": [dict(x) for x in n] if n else []})

    # user/<id>/action
    if path.startswith("user/"):
        parts = path.split("/")
        uid, action = parts[1], parts[2]
        if action == "ban":
            await query(
                "INSERT INTO banned_users (user_id,banned_by,reason,banned_at) VALUES (?,?,?,?) "
                "ON CONFLICT (user_id) DO UPDATE SET reason=EXCLUDED.reason",
                uid, u, req.get("reason"), datetime.utcnow().isoformat()
            )
            await log_audit(u, "ban_user", uid)
            return jsonify({"ok": True})
        if action == "unban":
            await query("DELETE FROM banned_users WHERE user_id=?", uid)
            await log_audit(u, "unban_user", uid)
            return jsonify({"ok": True})

    # quarantine/<id>/action
    if path.startswith("quarantine/"):
        parts = path.split("/")
        cid, action = parts[1], parts[2]
        if action == "approve":
            await query("UPDATE confessions SET status='posted', deleted_at=NULL WHERE id=?", cid)
            await log_audit(u, "approve_confession", f"#{cid}")
            row = await query("SELECT * FROM confessions WHERE id=?", cid, fetch_one=True)
            pub_ch_id = await cfg("public_channel_id", PUBLIC_CHANNEL_ID)
            if BOT_TOKEN and pub_ch_id:
                conf_num = row["confession_number"] or 1
                embed_colors = ["3498DB", "F1C40F", "5865F2", "9B59B6", "2ECC71", "E67E22", "E74C3C", "1ABC9C", "E91E63"]
                emb_color = int(embed_colors[conf_num % len(embed_colors)], 16)
                embed = {"title": f"Anonymous Confession (#{conf_num})", "description": f'"{row["content"]}"', "color": emb_color}
                if row["image_url"]:
                    embed["image"] = {"url": row["image_url"]}
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"https://discord.com/api/v10/channels/{pub_ch_id}/messages",
                        headers={"Authorization": f"Bot {BOT_TOKEN}"}, json={"embeds": [embed]}
                    )
                    if resp.status_code in (200, 201):
                        msg_data = resp.json()
                        pub_msg_id = msg_data.get("id")
                        if await cfg("reactions_enabled", "0") == "1" and pub_msg_id:
                            emojis = [e.strip() for e in (await cfg("reaction_emojis", "👍,👎,❤️")).split(",") if e.strip()]
                            for e in emojis:
                                try:
                                    await client.put(
                                        f"https://discord.com/api/v10/channels/{pub_ch_id}/messages/{pub_msg_id}/reactions/{urllib.parse.quote(e)}/@me",
                                        headers={"Authorization": f"Bot {BOT_TOKEN}"}
                                    )
                                except Exception:
                                    pass
            return jsonify({"ok": True})
        if action == "reject":
            r = await query("SELECT user_id FROM confessions WHERE id=?", cid, fetch_one=True)
            if r:
                await query(
                    "INSERT INTO banned_users (user_id,banned_by,reason,banned_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    r["user_id"], u, req.get("reason"), datetime.utcnow().isoformat()
                )
                await log_audit(u, "reject_and_ban", f"User: {r['user_id']} via #{cid}")
            await query("UPDATE confessions SET deleted_at=? WHERE id=?", datetime.utcnow().isoformat(), cid)
            await log_audit(u, "reject_confession", f"#{cid}")
            return jsonify({"ok": True})

    # settings/section
    if path.startswith("settings/"):
        s = path.split("/")[1]
        if s in ["system", "embed", "messages", "server", "bot-identity"]:
            for k, v in req.items():
                await set_cfg(k, v)
            return jsonify({"ok": True})
        if s == "blacklist":
            action = path.split("/")[2] if len(path.split("/")) > 2 else ""
            word = (req.get("word") or "").lower()
            if action == "add" and word:
                await query("INSERT INTO blacklist_words (word) VALUES (?) ON CONFLICT DO NOTHING", word)
            if action == "remove" and word:
                await query("DELETE FROM blacklist_words WHERE word=?", word)
            return jsonify({"ok": True})

    # admin/action (confession-app admin management)
    if path.startswith("admin/") and is_god():
        parts = path.split("/")
        if parts[1] == "create":
            import bcrypt
            un, pw, role = req.get("username"), req.get("password"), req.get("role")
            if await query("SELECT 1 FROM admins WHERE username=?", un, fetch_one=True):
                return jsonify({"error": "User exists"})
            h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
            await query("INSERT INTO admins (username,password_hash,role,created_by) VALUES (?,?,?,?)", un, h, role, u)
            await log_audit(u, "admin_create", un)
            return jsonify({"ok": True})

        target_un = parts[1]
        action = parts[2] if len(parts) > 2 else ""
        if action == "role":
            if target_un == 'byte':
                return jsonify({"error": "Cannot edit system admin"})
            await query("UPDATE admins SET role=? WHERE username=?", req.get("role"), target_un)
            return jsonify({"ok": True})
        if action == "reset-password":
            import bcrypt
            h = bcrypt.hashpw(req.get("password").encode(), bcrypt.gensalt()).decode()
            await query("UPDATE admins SET password_hash=? WHERE username=?", h, target_un)
            return jsonify({"ok": True})
        if action == "revoke":
            if target_un == 'byte':
                return jsonify({"error": "Cannot revoke system admin"})
            r = await query("SELECT is_revoked FROM admins WHERE username=?", target_un, fetch_one=True)
            if r:
                nv = 0 if r["is_revoked"] else 1
                await query("UPDATE admins SET is_revoked=? WHERE username=?", nv, target_un)
                return jsonify({"ok": True, "revoked": bool(nv)})

    return jsonify({"ok": False, "error": "Unknown route"})
