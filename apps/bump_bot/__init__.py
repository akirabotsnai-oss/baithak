"""
apps/bump_bot/__init__.py — Auto Bumper App

Registers the blueprint and declares app metadata for the registry.
"""
from quart import Blueprint

APP_META = {
    "id":           "bump_bot",
    "display_name": "Auto Bumper",
    "description":  "Disboard auto-bump service",
    "icon_emoji":   "🚀",
    "icon_color":   "#10b981",
    "route_prefix": "/bump",
}

bump_bp = Blueprint(
    "bump_bot",
    __name__,
    url_prefix="/bump",
    template_folder="templates",
)

# Import routes so they register on the blueprint
from apps.bump_bot import routes  # noqa: F401, E402
