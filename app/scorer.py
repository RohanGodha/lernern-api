"""Deterministic, rule-based matching engine.

Every dimension is a small, pure function of the candidate/job fields and the
configured weights, so a recommendation is always explainable by reading its
breakdown. There is no machine learning anywhere: the engine is a transparent
weighted scorer built directly from the assignment's business rules.

Rules implemented here:
  * Must-have skills are a HARD filter: missing any => job is excluded.
  * Nice-to-have skills only add points (partial credit).
  * Experience below the minimum is PENALIZED, never used to exclude.
  * Location: exact match > remote allowed > mismatch.
  * Salary: overlap/position relative to the range (see _salary).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .models import Candidate, Job
from .skills import normalize_skill
from .weights import DEFAULT_WEIGHTS, Weights

# Location credit for a remote-friendly role when the candidate is elsewhere.
# Exact match gets 100% of the weight; remote fallback gets 65%; mismatch 0%.
# (This mirrors Naukri-Automation's location handling, where an "exact" match
# includes containment, e.g. "Bengaluru" matches "Bengaluru, Karnataka".)
REMOTE_CREDIT_FACTOR = 0.65

# Inside the skills dimension, when a job has both kinds of requirements,
# must-have skills own 60% of the points and nice-to-have own the other 40%.
# If only one kind exists it inherits the full weight (see `_skills`).
MUST_HAVE_SKILLS_SHARE = 0.6

# In the salary dimension, being over the range is not treated as an instant
# zero: it ramps from 1/3 credit at the boundary down to 0 the further above
# the range max the candidate's expectation sits (formula in `_salary`).


def _norm(value: object) -> str:
    """Normalize a location label so matching is case/space-insensitive."""
    return str(value).strip().casefold()


def locations_overlap(a: object, b: object) -> bool:
    """True when two location strings point at the same place.

    Exact normalized equality, OR meaningful containment either way
    ("Bengaluru" vs "Bengaluru, Karnataka"). Containment requires the shorter
    string to be >= 3 chars to avoid absurd matches like "a" in "atlanta".
    """
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return (len(x) >= 3 and x in y) or (len(y) >= 3 and y in x)


def _skill_sets(job: Job) -> tuple[set[str], set[str]]:
    musts = {normalize_skill(r.skill) for r in job.requiredSkills if r.required}
    nices = {normalize_skill(r.skill) for r in job.requiredSkills if not r.required}
    return musts, nices


def candidate_passes_must_have_filter(candidate: Candidate, job: Job) -> bool:
    """True unless the job requires a must-have skill the candidate lacks."""
    musts, _ = _skill_sets(job)
    if not musts:
        return True
    owned = {normalize_skill(s) for s in candidate.skills}
    return musts <= owned


def _skills(candidate: Candidate, job: Job, weight: float):
    """Return (score, detail) for the skills dimension, or None if excluded.

    Score is the weighted sum of:
      - must-have hit ratio  (1.0 by definition, since failure => exclusion)
      - nice-to-have hit ratio
    """
    musts, nices = _skill_sets(job)
    owned = {normalize_skill(s) for s in candidate.skills}

    if musts and not musts <= owned:
        return None

    if not musts and not nices:
        return weight, "no skill requirements - full credit"

    if musts and nices:
        must_w, nice_w = round(weight * MUST_HAVE_SKILLS_SHARE, 2), round(
            weight * (1 - MUST_HAVE_SKILLS_SHARE), 2
        )
    elif musts:
        must_w, nice_w = weight, 0.0
    else:
        must_w, nice_w = 0.0, weight

    must_ratio = 1.0
    nice_ratio = len(owned & nices) / len(nices) if nices else 1.0

    score = round(must_w * must_ratio + nice_w * nice_ratio, 1)

    parts = []
    if musts:
        parts.append(f"must-have {len(musts & owned)}/{len(musts)} matched")
    if nices:
        parts.append(f"nice-to-have {len(nices & owned)}/{len(nices)} matched")
    return score, "; ".join(parts)


def _experience(candidate: Candidate, job: Job, weight: float):
    """PENALIZE (never exclude) candidates below the minimum experience.

    Below the minimum, credit scales linearly with the ratio of the
    candidate's years to the minimum. At/above the minimum => full credit.
    """
    min_yoe = float(job.minYearsExperience)
    if min_yoe <= 0:
        return weight, "no minimum experience required"

    ratio = min(1.0, candidate.yearsOfExperience / min_yoe)
    score = round(weight * ratio, 1)
    return score, f"{candidate.yearsOfExperience} yrs vs required {min_yoe} yrs"


def _location(candidate: Candidate, job: Job, weight: float):
    """Exact/overlapping location scores highest, remote fallback next, mismatch 0."""
    if locations_overlap(candidate.location, job.location):
        return weight, "exact location match"
    if job.remoteAllowed:
        return (
            round(weight * REMOTE_CREDIT_FACTOR, 1),
            "location mismatch but role allows remote work",
        )
    return 0.0, "location mismatch and remote not allowed"


def _salary(candidate: Candidate, job: Job, weight: float):
    """Score based on where the expected salary sits relative to the range.

    Three regimes (weight = w, default 15):
      * expected <  min : full credit w (job comfortably above expectation)
      * expected in range: linear w at the floor down to w/3 at the ceiling
      * expected >  max : near-zero; w/3 at the boundary ramping to 0 as the
                          expectation climbs further above the range max
    """
    exp = float(candidate.expectedSalary)
    lo = float(job.salaryRange.min)
    hi = float(job.salaryRange.max)

    if exp < lo:
        return weight, f"expected {exp:g} below range floor {lo:g}"

    if exp <= hi:
        if hi == lo:
            return weight, f"expected {exp:g} exactly matches band {lo:g}"
        t = (exp - lo) / (hi - lo)
        score = round(weight - (weight * 2 / 3) * t, 1)
        return score, f"expected {exp:g} within [{lo:g}, {hi:g}]"

    overshoot = (exp - hi) / hi if hi > 0 else (exp - hi)
    score = max(0.0, round(weight / 3 - weight * overshoot, 1))
    return score, f"expected {exp:g} above range max {hi:g}"


def _score_parts(
    candidate: Candidate, job: Job, weights: Weights
) -> Optional[tuple[float, dict[str, Any]]]:
    """Return (match_score, breakdown) or None if the pair must be excluded."""
    w = weights.normalized()

    skills = _skills(candidate, job, w.skills)
    if skills is None:
        return None

    experience = _experience(candidate, job, w.experience)
    location = _location(candidate, job, w.location)
    salary = _salary(candidate, job, w.salary)

    match_score = round(skills[0] + experience[0] + location[0] + salary[0], 1)
    breakdown = {
        "skills": {"score": skills[0], "max": w.skills, "detail": skills[1]},
        "experience": {"score": experience[0], "max": w.experience, "detail": experience[1]},
        "location": {"score": location[0], "max": w.location, "detail": location[1]},
        "salary": {"score": salary[0], "max": w.salary, "detail": salary[1]},
    }
    return match_score, breakdown


def evaluate(
    candidate: Candidate, job: Job, weights: Weights = DEFAULT_WEIGHTS
) -> Optional[dict[str, Any]]:
    """Score one (candidate, job) pair.

    Returns None when the job must be excluded (candidate missing a must-have
    skill). Otherwise returns a dict suitable for the API response:
        {"job": Job, "matchScore": float, "breakdown": {...}}
    """
    parts = _score_parts(candidate, job, weights)
    if parts is None:
        return None
    match_score, breakdown = parts
    return {"job": job, "matchScore": match_score, "breakdown": breakdown}


def _sort_key(match_score: float, entity_id: Optional[int]) -> tuple[float, int]:
    """Descending score, then ascending id (stable, deterministic tie-break)."""
    return -match_score, entity_id if entity_id is not None else 0


def recommend_jobs_for_candidate(
    candidate: Candidate,
    jobs: Iterable[Job],
    weights: Weights = DEFAULT_WEIGHTS,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Rank the best-matching jobs for a candidate (top-N via `limit`)."""
    results = []
    for job in jobs:
        rec = evaluate(candidate, job, weights)
        if rec is not None:
            results.append(rec)
    results.sort(key=lambda r: _sort_key(r["matchScore"], r["job"].id))
    return results[:limit] if limit is not None else results


def recommend_candidates_for_job(
    candidates: Iterable[Candidate],
    job: Job,
    weights: Weights = DEFAULT_WEIGHTS,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Reverse view: rank the best-fit candidates for a job (top-N via `limit`)."""
    results = []
    for candidate in candidates:
        parts = _score_parts(candidate, job, weights)
        if parts is None:
            continue
        match_score, breakdown = parts
        results.append(
            {"candidate": candidate, "matchScore": match_score, "breakdown": breakdown}
        )
    results.sort(key=lambda r: _sort_key(r["matchScore"], r["candidate"].id))
    return results[:limit] if limit is not None else results