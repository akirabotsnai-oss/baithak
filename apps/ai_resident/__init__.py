"""
apps/ai_resident/__init__.py — AI Resident App

Registers the blueprint and declares app metadata for the registry.
"""
from quart import Blueprint

APP_META = {
    "id":           "ai_resident",
    "display_name": "AI Resident",
    "description":  "Autonomous AI Server Resident",
    "icon_emoji":   "🤖",
    "icon_color":   "#8a2be2",
    "route_prefix": "/ai_resident",
}

ai_resident_bp = Blueprint(
    "ai_resident",
    __name__,
    url_prefix="/ai_resident",
    template_folder="templates",
)

# Import routes so they register on the blueprint
from apps.ai_resident import routes  # noqa: F401, E402
