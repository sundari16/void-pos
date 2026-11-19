from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin

# The lifecycle an operator sees on the public board, in order.
REQUEST_STATUSES = ("submitted", "under_review", "planned", "shipped")


class FeatureRequest(Base, TimestampMixin):
    """A feature request on the public board.

    Operators submit, everyone sees the status, the vote count and the dates — the
    visibility incumbents don't give, where a request vanishes with no trace. Votes live
    in their own table (one row per operator or till); the count is always ``COUNT`` of
    those rows, never a stored field, so it can't drift from the ledger.
    """

    __tablename__ = "feature_request"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(120), nullable=False)  # the venue/operator name
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")


class FeatureRequestVote(Base, TimestampMixin):
    """One operator's or till's upvote. Unique per ``(request, voter)`` makes an upvote
    idempotent — pressing it again records nothing new and changes no count. The leading
    column of that unique index also serves lookups by request, so no extra index."""

    __tablename__ = "feature_request_vote"
    __table_args__ = (UniqueConstraint("request_id", "voter", name="uq_feature_request_vote"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    request_id: Mapped[str] = mapped_column(ForeignKey("feature_request.id"), nullable=False)
    voter: Mapped[str] = mapped_column(String(40), nullable=False)  # a till's device id, or "admin"
