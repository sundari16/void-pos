"""Social posting: the ``SocialPoster`` seam plus the open-core ``LogPoster``.

The real Meta (Facebook / Instagram) adapter is commercial and implements the same
protocol; see ``poster``.
"""

from __future__ import annotations

from .poster import (
    INSTAGRAM_CAPTION_MAX,
    LogPoster,
    Network,
    PostResult,
    SocialPost,
    SocialPoster,
    validate_post,
)

__all__ = [
    "INSTAGRAM_CAPTION_MAX",
    "LogPoster",
    "Network",
    "PostResult",
    "SocialPost",
    "SocialPoster",
    "validate_post",
]
