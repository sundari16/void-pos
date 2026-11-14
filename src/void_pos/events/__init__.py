"""Event-apply / projection layer: fold the event log onto the aggregates."""

from __future__ import annotations

from .apply import ApplyResult, apply_events, ingest_event
from .types import EventType, can_transition

__all__ = ["ApplyResult", "EventType", "apply_events", "can_transition", "ingest_event"]
