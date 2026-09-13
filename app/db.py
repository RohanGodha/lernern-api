"""SQLAlchemy mappings for the optional Postgres backend."""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class CandidateRow(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    skills = Column(JSON, nullable=False, default=list)
    years_of_experience = Column(Float, nullable=False, default=0)
    location = Column(String, nullable=False, default="")
    expected_salary = Column(Float, nullable=False)


class JobRow(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    required_skills = Column(JSON, nullable=False, default=list)
    min_years_experience = Column(Float, nullable=False, default=0)
    location = Column(String, nullable=False, default="")
    salary_min = Column(Float, nullable=False)
    salary_max = Column(Float, nullable=False)
    remote_allowed = Column(Boolean, nullable=False, default=False)
    posted_at = Column(DateTime(timezone=True), nullable=True)


def build_session_factory(url: str) -> sessionmaker:
    """Create a configured session factory, ensuring tables exist."""
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)