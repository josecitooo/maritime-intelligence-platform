"""The monitored-region catalog.

Every row is one aisstream bounding box with a name. `enabled` is what the
worker reads: only enabled regions are subscribed, and the UI changes the
selection through `PUT /regions`. The boxes themselves are fixed by the seed
migration — V1 deliberately has no way to invent a region from the browser,
only to turn catalogued regions on and off.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrackedRegion(Base):
    __tablename__ = "tracked_regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)