"""The social-posting seam: a ``SocialPoster`` protocol plus the open-core log poster.

A venue posts a promotion (a new dish, tonight's special) to its own social pages. This
mirrors the marketing ``Sender`` seam: the open core ships a no-op ``LogPoster`` that
records what it would post, so the path is testable with no accounts, and the real Meta
(Facebook / Instagram) adapter implements the same protocol in the commercial layer, where
the venue's page tokens live. ``validate_post`` holds the rules every poster applies, so
the mock and the real adapter refuse the same posts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from ..errors import InvalidRequest

log = logging.getLogger(__name__)

Network = Literal["facebook", "instagram"]

INSTAGRAM_CAPTION_MAX = 2200
FACEBOOK_TEXT_MAX = 63206


@dataclass(frozen=True)
class SocialPost:
    """One post for one network."""

    network: Network
    text: str
    image_url: str | None = None  # Instagram needs one; Facebook can post text alone
    link: str | None = None  # e.g. the venue's storefront, /order/<location id>


@dataclass(frozen=True)
class PostResult:
    network: Network
    status: Literal["posted", "skipped", "failed"]
    detail: str | None = None
    external_ref: str | None = None  # the network's post id, when it gives one


class SocialPoster(Protocol):
    """A social-posting backend. The log poster is open core; the Meta adapter is
    commercial and implements this same protocol."""

    network: Network

    def post(self, post: SocialPost) -> PostResult: ...


def validate_post(post: SocialPost) -> None:
    """The rules every poster applies before anything is sent."""
    if not post.text.strip():
        raise InvalidRequest("a post needs some text", code="invalid_social_post")
    if post.network == "instagram":
        if not post.image_url:
            raise InvalidRequest("an Instagram post needs an image", code="invalid_social_post")
        if len(post.text) > INSTAGRAM_CAPTION_MAX:
            raise InvalidRequest(
                f"an Instagram caption is at most {INSTAGRAM_CAPTION_MAX} characters",
                code="invalid_social_post",
            )
    elif len(post.text) > FACEBOOK_TEXT_MAX:
        raise InvalidRequest(
            f"a Facebook post is at most {FACEBOOK_TEXT_MAX} characters",
            code="invalid_social_post",
        )


@dataclass
class LogPoster:
    """The open-core default: check the post, record it and report it posted, without
    contacting any network. Lets a venue exercise the posting path with no accounts."""

    network: Network = "facebook"

    def post(self, post: SocialPost) -> PostResult:
        validate_post(post)
        log.info(
            "social post (%s)%s%s: %s",
            post.network,
            f" image={post.image_url}" if post.image_url else "",
            f" link={post.link}" if post.link else "",
            post.text,
        )
        return PostResult(network=post.network, status="posted", detail="logged (no-op poster)")
