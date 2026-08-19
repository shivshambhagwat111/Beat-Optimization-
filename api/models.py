from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from api.db import Base


class Distributor(Base):
    __tablename__ = "distributors"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)

    outlets = relationship("Outlet", back_populates="distributor")
    beats = relationship("Beat", back_populates="distributor")


class Outlet(Base):
    __tablename__ = "outlets"

    id = Column(Integer, primary_key=True, index=True)
    distributor_code = Column(String, ForeignKey("distributors.code"), nullable=False, index=True)
    retailer_code = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    original_beat_name = Column(String, nullable=True)

    distributor = relationship("Distributor", back_populates="outlets")
    route_sequences = relationship("RouteSequence", back_populates="outlet")


class Beat(Base):
    __tablename__ = "beats"

    id = Column(Integer, primary_key=True, index=True)
    beat_code = Column(String, unique=True, nullable=False, index=True)
    # Exactly one of (distributor_code, combined_key) is set: single-distributor
    # beats set distributor_code and leave combined_key null; jointly-optimized
    # beats (spanning 2+ distributors) set combined_key and leave distributor_code
    # null, since no single distributor owns them.
    distributor_code = Column(String, ForeignKey("distributors.code"), nullable=True, index=True)
    combined_key = Column(String, nullable=True, index=True)
    total_outlets = Column(Integer, nullable=False, default=0)

    distributor = relationship("Distributor", back_populates="beats")
    route_sequences = relationship(
        "RouteSequence", back_populates="beat", order_by="RouteSequence.visit_order"
    )


class RouteSequence(Base):
    __tablename__ = "route_sequences"
    __table_args__ = (UniqueConstraint("beat_id", "visit_order", name="uq_beat_visit_order"),)

    id = Column(Integer, primary_key=True, index=True)
    beat_id = Column(Integer, ForeignKey("beats.id"), nullable=False, index=True)
    outlet_id = Column(Integer, ForeignKey("outlets.id"), nullable=False, index=True)
    visit_order = Column(Integer, nullable=False)

    beat = relationship("Beat", back_populates="route_sequences")
    outlet = relationship("Outlet", back_populates="route_sequences")
