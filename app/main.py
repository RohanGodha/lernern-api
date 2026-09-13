"""Job Match API - FastAPI application.

Endpoints:
  POST /candidates                       create a candidate profile
  GET  /candidates/{id}                  fetch a candidate
  GET  /candidates/{id}/recommendations  rank jobs for a candidate
  POST /jobs                             create a job posting
  GET  /jobs/{id}                        fetch a job
  GET  /jobs/{id}/recommendations        reverse view: rank candidates for a job
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from .filters import (
    CandidateFilters,
    JobFilters,
    _split,
    candidate_reject_reason,
    job_reject_reason,
)
from .models import (
    Candidate,
    CandidateRecommendationList,
    FilterSummary,
    Job,
    JobRecommendationList,
)
from .scorer import recommend_candidates_for_job, recommend_jobs_for_candidate
from .storage import get_store
from .weights import DEFAULT_WEIGHTS, Weights

app = FastAPI(
    title="Job Match API",
    version="1.0.0",
    description=(
        "Recommends jobs to candidates (and candidates to jobs) using a "
        "transparent, rule-based, weighted scorer."
    ),
)

store = get_store()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"app": "Job Match API", "docs": "/docs", "health": "/health"}


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Candidates ---------------------------------------------------------


@app.post("/candidates", response_model=Candidate, status_code=201, tags=["candidates"])
def create_candidate(candidate: Candidate) -> Candidate:
    """Create a candidate profile."""
    return store.add_candidate(candidate)


@app.get("/candidates/{candidate_id}", response_model=Candidate, tags=["candidates"])
def get_candidate(candidate_id: int) -> Candidate:
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@app.get(
    "/candidates/{candidate_id}/recommendations",
    response_model=JobRecommendationList,
    tags=["candidates"],
)
def job_recommendations(
    candidate_id: int,
    limit: int = Query(default=10, ge=1, le=200, description="Top-N results"),
    *,
    w_skills: Optional[float] = Query(None, ge=0, le=1000),
    w_experience: Optional[float] = Query(None, ge=0, le=1000),
    w_location: Optional[float] = Query(None, ge=0, le=1000),
    w_salary: Optional[float] = Query(None, ge=0, le=1000),
    locations: Optional[str] = Query(None, description="Include jobs in these locations (csv)"),
    excludeLocations: Optional[str] = Query(None, description="Exclude jobs in these locations (csv)"),
    titleKeywords: Optional[str] = Query(None, description="Include jobs whose title matches >=1 keyword (csv)"),
    excludeTitleKeywords: Optional[str] = Query(None, description="Exclude jobs whose title matches any keyword (csv)"),
    remoteOnly: Optional[bool] = Query(None, description="Only jobs that allow remote work"),
    minSalary: Optional[float] = Query(None, ge=0, description="Only jobs paying at least this (max >= value)"),
    minRequiredExperience: Optional[float] = Query(None, ge=0, description="Only jobs requiring at least this many years"),
    maxRequiredExperience: Optional[float] = Query(None, ge=0, description="Only jobs requiring at most this many years"),
    postedWithinDays: Optional[int] = Query(None, ge=1, description="Only jobs posted within the last N days"),
) -> JobRecommendationList:
    """Rank the best-matching jobs for a candidate (top-N via `limit`)."""
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    weights = _resolve_weights(w_skills, w_experience, w_location, w_salary)
    job_filters = JobFilters(
        locations=_split(locations),
        exclude_locations=_split(excludeLocations),
        title_keywords=_split(titleKeywords),
        exclude_title_keywords=_split(excludeTitleKeywords),
        remote_only=bool(remoteOnly),
        min_salary=minSalary,
        min_required_experience=minRequiredExperience,
        max_required_experience=maxRequiredExperience,
        posted_within_days=postedWithinDays,
    )

    all_jobs = store.all_jobs()
    filtered_out: dict[str, int] = {}
    eligible_jobs = []
    for job in all_jobs:
        reason = job_reject_reason(job, job_filters)
        if reason:
            filtered_out[reason] = filtered_out.get(reason, 0) + 1
        else:
            eligible_jobs.append(job)

    # Score once without a limit so we can report scorer-level exclusions,
    # then slice the top-N.
    scored = recommend_jobs_for_candidate(candidate, eligible_jobs, weights)
    recommendations = scored[:limit]
    return JobRecommendationList(
        candidateId=candidate_id,
        total=len(recommendations),
        recommendations=recommendations,
        filterSummary=FilterSummary(
            processed=len(all_jobs),
            eligible=len(eligible_jobs),
            filteredOut=filtered_out,
            mustHaveExcluded=len(eligible_jobs) - len(scored),
        ),
    )


# --- Jobs -----------------------------------------------------------------


@app.post("/jobs", response_model=Job, status_code=201, tags=["jobs"])
def create_job(job: Job) -> Job:
    """Create a job posting."""
    return store.add_job(job)


@app.get("/jobs/{job_id}", response_model=Job, tags=["jobs"])
def get_job(job_id: int) -> Job:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get(
    "/jobs/{job_id}/recommendations",
    response_model=CandidateRecommendationList,
    tags=["jobs"],
)
def candidate_recommendations(
    job_id: int,
    limit: int = Query(default=10, ge=1, le=200, description="Top-N results"),
    *,
    w_skills: Optional[float] = Query(None, ge=0, le=1000),
    w_experience: Optional[float] = Query(None, ge=0, le=1000),
    w_location: Optional[float] = Query(None, ge=0, le=1000),
    w_salary: Optional[float] = Query(None, ge=0, le=1000),
    locations: Optional[str] = Query(None, description="Include candidates in these locations (csv)"),
    excludeLocations: Optional[str] = Query(None, description="Exclude candidates in these locations (csv)"),
    minExperience: Optional[float] = Query(None, ge=0, description="Only candidates with at least this many years"),
    maxExperience: Optional[float] = Query(None, ge=0, description="Only candidates with at most this many years"),
    skills: Optional[str] = Query(None, description="Only candidates having at least one of these skills (csv)"),
    maxExpectedSalary: Optional[float] = Query(None, ge=0, description="Only candidates whose expected salary is at most this"),
) -> CandidateRecommendationList:
    """Reverse view: rank the best-fit candidates for a job (top-N via `limit`)."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    weights = _resolve_weights(w_skills, w_experience, w_location, w_salary)
    candidate_filters = CandidateFilters(
        locations=_split(locations),
        exclude_locations=_split(excludeLocations),
        min_experience=minExperience,
        max_experience=maxExperience,
        skills=_split(skills),
        max_expected_salary=maxExpectedSalary,
    )
    eligible_candidates = []
    filtered_out: dict[str, int] = {}
    for candidate in store.all_candidates():
        reason = candidate_reject_reason(candidate, candidate_filters)
        if reason:
            filtered_out[reason] = filtered_out.get(reason, 0) + 1
        else:
            eligible_candidates.append(candidate)

    scored = recommend_candidates_for_job(eligible_candidates, job, weights)
    recommendations = scored[:limit]
    return CandidateRecommendationList(
        jobId=job_id,
        total=len(recommendations),
        recommendations=recommendations,
        filterSummary=FilterSummary(
            processed=len(eligible_candidates) + sum(filtered_out.values()),
            eligible=len(eligible_candidates),
            filteredOut=filtered_out,
            mustHaveExcluded=len(eligible_candidates) - len(scored),
        ),
    )


def _resolve_weights(
    skills: Optional[float],
    experience: Optional[float],
    location: Optional[float],
    salary: Optional[float],
) -> Weights:
    """Build a Weights config. If no params are given, use the defaults."""
    if skills is None and experience is None and location is None and salary is None:
        return DEFAULT_WEIGHTS
    return Weights(
        skills=DEFAULT_WEIGHTS.skills if skills is None else skills,
        experience=DEFAULT_WEIGHTS.experience if experience is None else experience,
        location=DEFAULT_WEIGHTS.location if location is None else location,
        salary=DEFAULT_WEIGHTS.salary if salary is None else salary,
    )