import os

file_path = r'd:\confession bot discord\templates\confessions.html'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = [
    '{% extends "shell.html" %}\n',
    '{% block title %}Command Center{% endblock %}\n',
    '{% block sidebar_items %}\n',
    '<a href="/confessions/overview" class="{% if active == \'overview\' %}active{% endif %}">OVERVIEW</a>\n',
    '<a href="/confessions/feed" class="{% if active == \'feed\' %}active{% endif %}">CONFESSIONS</a>\n',
    '<a href="/confessions/quarantine" class="{% if active == \'quarantine\' %}active{% endif %}">QUARANTINE {% if q_count > 0 %}({{ q_count }}){% endif %}</a>\n',
    '<a href="/confessions/bans" class="{% if active == \'bans\' %}active{% endif %}">BANS</a>\n',
    '{% if can_recycle_bin %}\n',
    '<a href="/confessions/recycle-bin" class="{% if active == \'recycle_bin\' %}active{% endif %}">RECYCLE BIN</a>\n',
    '{% endif %}\n',
    '<a href="/confessions/users" class="{% if active == \'user_info\' %}active{% endif %}">USERS</a>\n',
    '<a href="/confessions/audit" class="{% if active == \'audit\' %}active{% endif %}">AUDIT LOGS</a>\n',
    '{% if can_settings %}\n',
    '<a href="/confessions/settings" class="{% if active == \'settings\' %}active{% endif %}">SETTINGS</a>\n',
    '{% endif %}\n',
    '{% if role in [\'god\', \'god2\'] %}\n',
    '<a href="/confessions/security" class="{% if active == \'security\' %}active{% endif %}">SECURITY LOGS</a>\n',
    '{% endif %}\n',
    '{% endblock %}\n\n',
    '{% block content %}\n',
    '{% if active == \'overview\' %}\n'
]

new_lines += lines[983:2570]
new_lines += ['{% endif %}\n', '{% endblock %}\n']

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("confessions.html successfully updated.")
