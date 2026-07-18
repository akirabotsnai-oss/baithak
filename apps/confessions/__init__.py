"""
apps/confessions/__init__.py — Confession Bot App

Registers the blueprint and declares app metadata for the registry.
"""
from quart import Blueprint

APP_META = {
    "id":           "confessions",
    "display_name": "Confession Bot",
    "description":  "Anonymous confessions platform",
    "icon_emoji":   "💬",
    "icon_color":   "#5865f2",
    "route_prefix": "/confessions",
}

confessions_bp = Blueprint(
    "confessions",
    __name__,
    url_prefix="/confessions",
    template_folder="templates",
)

# Import routes so they register on the blueprint
from apps.confessions import routes  # noqa: F401, E402
