"""
core/registry.py — App Registry.

Each app registers its blueprint and metadata here.
Adding a new app = add two lines to this file.
"""
from apps.confessions import confessions_bp, APP_META as CONFESSIONS_META
from apps.bump_bot import bump_bp, APP_META as BUMP_META

# All installed apps — order controls sort in home/sidebar
REGISTRY = [
    CONFESSIONS_META,
    BUMP_META,
]

# All blueprints to register on the Quart app
BLUEPRINTS = [
    confessions_bp,
    bump_bp,
]
