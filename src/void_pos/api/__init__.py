"""HTTP API: till commands, sync, export, reports, menu, venue setup and pairing."""

from __future__ import annotations

from .admin_routes import router as admin_router
from .aggregator_routes import router as aggregator_router
from .board_routes import router as board_router
from .delivery_routes import router as delivery_router
from .device_routes import router as device_router
from .errors import install_error_handlers
from .menu_routes import router as menu_router
from .online_routes import router as online_router
from .routes import router
from .voice_routes import router as voice_router

__all__ = [
    "admin_router",
    "aggregator_router",
    "board_router",
    "delivery_router",
    "device_router",
    "install_error_handlers",
    "menu_router",
    "online_router",
    "router",
    "voice_router",
]
