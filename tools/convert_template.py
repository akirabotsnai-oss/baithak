import re

with open(r'd:\confession bot discord\index.html', encoding='utf-8') as f:
    content = f.read()

# Route URLs
for old, new in [
    ('/overview', '/confessions/overview'),
    ('/recycle-bin', '/confessions/recycle-bin'),
    ('/quarantine', '/confessions/quarantine'),
    ('/bans', '/confessions/bans'),
    ('/user-info', '/confessions/users'),
    ('/audit', '/confessions/audit'),
    ('/security', '/confessions/security'),
    ('/admin-manager', '/confessions/admin-manager'),
    ('/feed/export', '/confessions/export'),
    ('/feed', '/confessions/feed'),
    ('/settings', '/confessions/settings'),
]:
    content = content.replace("href=\"" + old + "\"", "href=\"" + new + "\"")
    content = content.replace("href=\"" + old + "'", "href=\"" + new + "'")
    content = content.replace("action=\"" + old + "\"", "action=\"" + new + "\"")
    content = content.replace("window.location='" + old.rstrip('/') + "'", "window.location='" + new + "'")
    content = content.replace("window.location='" + old + "'", "window.location='" + new + "'")
    content = content.replace("location.href = '" + old.rstrip('/') + "'", "location.href = '" + new + "'")

# Sidebar links that navigate by JS
content = content.replace("window.location='/confessions/feed'", "window.location='/confessions/feed'")

# API calls in JS strings
for old_api, new_api in [
    ("/api/confession/", "/confessions/api/confession/"),
    ("/api/quarantine/", "/confessions/api/quarantine/"),
    ("/api/user/", "/confessions/api/user/"),
    ("/api/settings/", "/confessions/api/settings/"),
    ("/api/admin/", "/confessions/api/admin/"),
]:
    content = content.replace("'" + old_api, "'" + new_api)
    content = content.replace("`" + old_api, "`" + new_api)
    content = content.replace('"' + old_api, '"' + new_api)

with open(r'd:\confession bot discord\templates\confessions.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done - confessions.html written')
