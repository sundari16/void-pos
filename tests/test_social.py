from __future__ import annotations

import pytest

from void_pos.errors import InvalidRequest
from void_pos.social import (
    INSTAGRAM_CAPTION_MAX,
    LogPoster,
    SocialPost,
    SocialPoster,
    validate_post,
)


def test_log_poster_reports_posted_without_calling_out() -> None:
    poster: SocialPoster = LogPoster(network="facebook")
    result = poster.post(SocialPost(network="facebook", text="Dosa night, Friday from 5",
                                    link="https://example.com/order/LOC"))
    assert (result.network, result.status, result.external_ref) == ("facebook", "posted", None)


def test_facebook_takes_text_alone_and_instagram_needs_an_image() -> None:
    validate_post(SocialPost(network="facebook", text="Fresh mango lassi is back"))
    validate_post(SocialPost(network="instagram", text="Lassi!", image_url="https://x/l.jpg"))
    with pytest.raises(InvalidRequest, match="needs an image"):
        LogPoster(network="instagram").post(SocialPost(network="instagram", text="Lassi!"))


def test_posts_need_text_and_respect_the_caption_limit() -> None:
    with pytest.raises(InvalidRequest, match="needs some text"):
        validate_post(SocialPost(network="facebook", text="   "))
    long = "x" * (INSTAGRAM_CAPTION_MAX + 1)
    with pytest.raises(InvalidRequest, match="at most 2200"):
        validate_post(SocialPost(network="instagram", text=long, image_url="https://x/a.jpg"))
    validate_post(SocialPost(network="facebook", text=long))  # Facebook allows far longer


def test_invalid_posts_carry_a_stable_code() -> None:
    with pytest.raises(InvalidRequest) as err:
        validate_post(SocialPost(network="facebook", text=""))
    assert err.value.code == "invalid_social_post"
