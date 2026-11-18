"""Venue setup and till pairing: locations, devices, pairing codes and device tokens."""

from __future__ import annotations

from .service import (
    PairingCode,
    authenticate_device,
    create_location,
    get_device,
    get_location,
    hash_secret,
    issue_pairing_code,
    list_devices,
    list_locations,
    normalize_pairing_code,
    pair_device,
    register_device,
    revoke_device,
)

__all__ = [
    "PairingCode",
    "authenticate_device",
    "create_location",
    "get_device",
    "get_location",
    "hash_secret",
    "issue_pairing_code",
    "list_devices",
    "list_locations",
    "normalize_pairing_code",
    "pair_device",
    "register_device",
    "revoke_device",
]
