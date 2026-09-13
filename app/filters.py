"""Extensive hard include/exclude filters, applied BEFORE scoring.

The idea (and several specific rules) come from the job-application bots in
this workspace:

  * D:\\RohanDocs\\linkedclaude          - keyword OR-includes + NOT-excludes, work-type + recency
  * D:\\RohanDocs\\Naukri-Automation     - filterJobs(): reject-reason audit ("X processed -> Y
                                            kept / Rejects: salary, age, exp, location, ..."),
                                            acceptedLocations/rejectedLocations with
                                            keep-if-ANY-acceptable semantics, and
                                            minimumExperience <= target cap
  * D:\\RohanDocs\\surelyremote_bot      - allowed_locations / excluded_locations / role_keywords
  * D:\\RohanDocs\\uplers_bot            - preferred_locations / excluded_locations / role keyword list

Filters here are HARD gates: unlike the scoring dimensions (which are soft
credits), a filtered-out job or candidate is removed before ranking and never
appears, no matter how high its score would have been.

Deliberate improvements over the bots:
  * keyword matching uses word boundaries, so "java" does NOT match "javascript"
    (the bots used naive substring checks);
  * location matching supports containment ("Bengaluru" matches
    "Bengaluru, Karnataka") while ignoring junk substrings (needs >= 3 chars);
  * every rejection records a reason, so callers get an audit summary instead
    of silent drops.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Candidate, Job
from .skills import normalize_skill


def _norm_label(value: object) -> str:
    return str(value).strip().casefold()


def _loc_matches(needle: str, haystack: str) -> bool:
    """Containment-aware location match (mirrors Naukri's accepted/rejected rules).

    Adjacent segments like "Bengaluru, Karnataka" match a "bengaluru" needle,
    but only when the shorter string is meaningful (>= 3 chars) to avoid
    absurd hits ("a" in "atlanta"). Empty strings never match.
    """
    n, h = _norm_label(needle), _norm_label(haystack)
    if not n or not h:
        return False
    if n == h:
        return True
    return (len(n) >= 3 and n in h) or (len(h) >= 3 and h in n)


def _split(value: str | None) -> list[str]:
    """Turns a comma-separated query param into a trimmed list."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _keyword_re(kw: str) -> re.Pattern:
    """Case-insensitive, word-boundary regex, e.g. 'java' -> \\bjava\\b."""
    return re.compile(r"\b" + re.escape(kw.strip()) + r"\b", re.IGNORECASE)


@dataclass(frozen=True)
class JobFilters:
    """Hard filters for the candidate -> jobs direction."""

    locations: list[str] = field(default_factory=list)            # include: job.location overlaps one of these
    exclude_locations: list[str] = field(default_factory=list)    # exclude: job.location overlaps any of these
    title_keywords: list[str] = field(default_factory=list)       # include: job.title matches >=1 keyword
    exclude_title_keywords: list[str] = field(default_factory=list)  # exclude: job.title matches any keyword
    remote_only: bool = False                                     # only jobs that allow remote work
    min_salary: float | None = None                               # job.salaryRange.max >= this
    min_required_experience: float | None = None                  # job.minYearsExperience >= this
    max_required_experience: float | None = None                  # job.minYearsExperience <= this
    posted_within_days: int | None = None                         # job.postedAt within last N days


@dataclass(frozen=True)
class CandidateFilters:
    """Hard filters for the job -> candidates direction (reverse view)."""

    locations: list[str] = field(default_factory=list)            # include: candidate.location overlaps one of these
    exclude_locations: list[str] = field(default_factory=list)    # exclude: candidate.location overlaps any of these
    min_experience: float | None = None                           # candidate.yearsOfExperience >= this
    max_experience: float | None = None                           # candidate.yearsOfExperience <= this
    skills: list[str] = field(default_factory=list)               # include: candidate has >=1 of these skills
    max_expected_salary: float | None = None                      # candidate.expectedSalary <= this


def job_reject_reason(job: Job, f: JobFilters) -> str | None:
    """Return why this job is filtered out, or None if it passes."""
    loc = _norm_label(job.location)
    if f.locations and not any(_loc_matches(x, loc) for x in f.locations):
        return "location"
    if f.exclude_locations and any(_loc_matches(x, loc) for x in f.exclude_locations):
        return "excluded_location"

    title = job.title.casefold()
    if f.exclude_title_keywords and any(_keyword_re(k).search(title) for k in f.exclude_title_keywords):
        return "excluded_title_keyword"
    if f.title_keywords and not any(_keyword_re(k).search(title) for k in f.title_keywords):
        return "title_keyword"

    if f.remote_only and not job.remoteAllowed:
        return "not_remote"
    if f.min_salary is not None and job.salaryRange.max < f.min_salary:
        return "min_salary"
    if f.min_required_experience is not None and job.minYearsExperience < f.min_required_experience:
        return "min_required_experience"
    if f.max_required_experience is not None and job.minYearsExperience > f.max_required_experience:
        return "max_required_experience"
    if f.posted_within_days is not None:
        if job.postedAt is None:
            return "no_post_date"  # unknown date cannot be confirmed recent
        age_days = (datetime.now(timezone.utc) - job.postedAt).total_seconds() / 86400
        if age_days > f.posted_within_days:
            return "too_old"

    return None


def job_passes_filters(job: Job, f: JobFilters) -> bool:
    return job_reject_reason(job, f) is None


def candidate_reject_reason(candidate: Candidate, f: CandidateFilters) -> str | None:
    """Return why this candidate is filtered out, or None if it passes."""
    loc = _norm_label(candidate.location)
    if f.locations and not any(_loc_matches(x, loc) for x in f.locations):
        return "location"
    if f.exclude_locations and any(_loc_matches(x, loc) for x in f.exclude_locations):
        return "excluded_location"

    if f.min_experience is not None and candidate.yearsOfExperience < f.min_experience:
        return "min_experience"
    if f.max_experience is not None and candidate.yearsOfExperience > f.max_experience:
        return "max_experience"

    if f.skills:
        owned = {normalize_skill(s) for s in candidate.skills}
        if not any(normalize_skill(s) in owned for s in f.skills):
            return "skills"

    if f.max_expected_salary is not None and candidate.expectedSalary > f.max_expected_salary:
        return "max_expected_salary"

    return None


def candidate_passes_filters(candidate: Candidate, f: CandidateFilters) -> bool:
    return candidate_reject_reason(candidate, f) is None