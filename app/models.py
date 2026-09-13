"""Pydantic schemas for the Job Match API.

Field names intentionally mirror the assignment's data model (camelCase),
so requests and responses are self-explanatory.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class SkillRequirement(BaseModel):
    """A single skill required (or preferred) by a job."""

    skill: str
    required: bool = True


class SalaryRange(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def _max_not_below_min(self) -> "SalaryRange":
        if self.max < self.min:
            raise ValueError("salaryRange.max must be >= salaryRange.min")
        return self


class Candidate(BaseModel):
    id: Optional[int] = None
    name: str
    skills: list[str] = Field(default_factory=list)
    yearsOfExperience: float = Field(default=0.0, ge=0)
    location: str = Field(default="")
    expectedSalary: float = Field(ge=0)


class Job(BaseModel):
    id: Optional[int] = None
    title: str
    requiredSkills: list[SkillRequirement] = Field(default_factory=list)
    minYearsExperience: float = Field(default=0.0, ge=0)
    location: str = Field(default="")
    salaryRange: SalaryRange
    remoteAllowed: bool = False
    postedAt: Optional[datetime] = None


# --- API response schemas -------------------------------------------------


class DimensionBreakdown(BaseModel):
    score: float
    max: float
    detail: str


class Breakdown(BaseModel):
    skills: DimensionBreakdown
    experience: DimensionBreakdown
    location: DimensionBreakdown
    salary: DimensionBreakdown


class FilterSummary(BaseModel):
    """Reject-reason audit for hard filters (port of Naukri-Automation's
    "X processed -> Y kept / Rejects: reason: count" reporting)."""

    processed: int
    eligible: int
    filteredOut: dict[str, int] = Field(
        default_factory=dict, description="count of jobs/candidates dropped per filter reason"
    )
    mustHaveExcluded: int = Field(
        default=0, description="eligible items the scorer's must-have gate dropped"
    )


class JobRecommendation(BaseModel):
    job: Job
    matchScore: float
    breakdown: Breakdown


class JobRecommendationList(BaseModel):
    candidateId: int
    total: int
    recommendations: list[JobRecommendation]
    filterSummary: Optional[FilterSummary] = None


class CandidateRecommendation(BaseModel):
    candidate: Candidate
    matchScore: float
    breakdown: Breakdown


class CandidateRecommendationList(BaseModel):
    jobId: int
    total: int
    recommendations: list[CandidateRecommendation]
    filterSummary: Optional[FilterSummary] = None