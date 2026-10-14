"""Links between a local request and the Seerr requests it fulfils."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.siftarr.models._base import Base, utc_now

if TYPE_CHECKING:
    from app.siftarr.models.request import Request


class RequestOverseerrLink(Base):
    """One Seerr request linked to a local request.

    A show or movie has one local request row. Every Seerr request for the same
    media (for example, separate season requests) links to that row.
    """

    __tablename__ = "request_overseerr_links"
    __table_args__ = (Index("ix_request_overseerr_links_request_id", "request_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    overseerr_request_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    request: Mapped[Request] = relationship("Request", back_populates="overseerr_links")

    def __repr__(self) -> str:
        return (
            f"<RequestOverseerrLink(request_id={self.request_id}, "
            f"overseerr_request_id={self.overseerr_request_id})>"
        )
