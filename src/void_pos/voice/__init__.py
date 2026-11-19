"""Voice AI order intake.

The POS side of voice ordering: accept a confirmed phone order from the external voice
stack (``livekit-impl`` / ``cold-caller``), validate it against the live menu, and place it
on the ``voice`` channel in the one order pipeline. The telephony and LLM live in that
separate stack; :mod:`void_pos.voice.agent` is the seam between them and this code.
"""

from __future__ import annotations

from .agent import (
    ConfirmedItem,
    ConfirmedModifier,
    DraftItem,
    DraftModifier,
    InboundCall,
    ScriptedVoiceAgent,
    VoiceAgent,
    VoiceOrderConfirmation,
    VoiceOrderDraft,
)
from .device import (
    VOICE_ORDER_NUMBER_END,
    VOICE_ORDER_NUMBER_START,
    next_voice_number,
    voice_device,
)
from .resolve import ResolvedOrder, resolve_draft
from .service import (
    VoiceCallResult,
    VoicePlacement,
    confirm_voice_order,
    place_voice_order,
    run_voice_call,
)

__all__ = [
    "VOICE_ORDER_NUMBER_END",
    "VOICE_ORDER_NUMBER_START",
    "ConfirmedItem",
    "ConfirmedModifier",
    "DraftItem",
    "DraftModifier",
    "InboundCall",
    "ResolvedOrder",
    "ScriptedVoiceAgent",
    "VoiceAgent",
    "VoiceCallResult",
    "VoiceOrderConfirmation",
    "VoiceOrderDraft",
    "VoicePlacement",
    "confirm_voice_order",
    "next_voice_number",
    "place_voice_order",
    "resolve_draft",
    "run_voice_call",
    "voice_device",
]
