"""Unit tests for the extensive hard filters (app/filters.py).

These filters are borrowed from the workspace job-application bots
(linkedclaude / Naukri-Automation / surelyremote_bot / uplers_bot) which
whitelist/blacklist listings via location lists and role keywords.
"""

from datetime import datetime, timedelta, timezone

from app.filters import (
    CandidateFilters,
    JobFilters,
    candidate_passes_filters,
    candidate_reject_reason,
    job_passes_filters,
    job_reject_reason,
)
from app.models import Candidate, Job

NOW = datetime.now(timezone.utc)


def make_candidate(**overrides):
    base = dict(
        name="Ada",
        skills=["python", "sql"],
        yearsOfExperience=4,
        location="Bengaluru",
        expectedSalary=1200,
    )
    base.update(overrides)
    return Candidate(**base)


def make_job(**overrides):
    base = dict(
        title="Backend Engineer",
        requiredSkills=[{"skill": "python", "required": True}],
        minYearsExperience=3,
        location="Bengaluru",
        salaryRange={"min": 1000, "max": 1500},
        remoteAllowed=False,
        postedAt=NOW,
    )
    base.update(overrides)
    return Job(**base)


# ---------------------------------------------------------------------------
# Job filters (candidate -> jobs)
# ---------------------------------------------------------------------------


def test_no_filters_passes_everything():
    assert job_passes_filters(make_job(), JobFilters()) is True
    assert candidate_passes_filters(make_candidate(), CandidateFilters()) is True


def test_location_include_and_exclude():
    job = make_job(location="Mumbai")
    assert job_passes_filters(job, JobFilters(locations=["mumbai"])) is True
    assert job_passes_filters(job, JobFilters(locations=["bengaluru"])) is False
    assert job_passes_filters(job, JobFilters(exclude_locations=["Mumbai"])) is False
    assert job_passes_filters(job, JobFilters(exclude_locations=["Pune"])) is True


def test_location_match_is_case_insensitive():
    assert job_passes_filters(make_job(location="Bengaluru"), JobFilters(locations=["bengaluru"])) is True


def test_title_keywords_include_any():
    job = make_job(title="Generative AI Engineer")
    assert job_passes_filters(job, JobFilters(title_keywords=["engineer"])) is True
    assert job_passes_filters(job, JobFilters(title_keywords=["backend"])) is False
    # multiple keywords: at least one must match
    assert job_passes_filters(job, JobFilters(title_keywords=["data", "engineer"])) is True


def test_title_keywords_word_boundary_does_not_match_partial():
    # "java" must NOT match "javascript" (improvement over the bots' substring check)
    job = make_job(title="Senior JavaScript Developer")
    assert job_passes_filters(job, JobFilters(title_keywords=["java"])) is False
    assert job_passes_filters(job, JobFilters(title_keywords=["javascript"])) is True


def test_exclude_title_keywords():
    job = make_job(title="Java Engineer")
    assert job_passes_filters(job, JobFilters(exclude_title_keywords=["java"])) is False
    assert job_passes_filters(job, JobFilters(exclude_title_keywords=["python"])) is True


def test_remote_only():
    assert job_passes_filters(make_job(remoteAllowed=False), JobFilters(remote_only=True)) is False
    assert job_passes_filters(make_job(remoteAllowed=True), JobFilters(remote_only=True)) is True


def test_min_salary_gate():
    job = make_job(salaryRange={"min": 1000, "max": 1500})
    assert job_passes_filters(job, JobFilters(min_salary=1500)) is True
    assert job_passes_filters(job, JobFilters(min_salary=1501)) is False


def test_min_required_experience_gate():
    job = make_job(minYearsExperience=3)
    assert job_passes_filters(job, JobFilters(min_required_experience=3)) is True
    assert job_passes_filters(job, JobFilters(min_required_experience=4)) is False


def test_posted_within_days():
    fresh = make_job(postedAt=NOW - timedelta(days=1))
    stale = make_job(postedAt=NOW - timedelta(days=30))
    unknown = make_job(postedAt=None)

    filters = JobFilters(posted_within_days=7)
    assert job_passes_filters(fresh, filters) is True
    assert job_passes_filters(stale, filters) is False
    assert job_passes_filters(unknown, filters) is False  # unknown date cannot be confirmed recent


def test_combined_filters_all_must_pass():
    job = make_job(title="Backend Engineer", location="Bengaluru", remoteAllowed=True, salaryRange={"min": 1000, "max": 1800})
    filters = JobFilters(
        locations=["bengaluru"],
        title_keywords=["backend"],
        remote_only=True,
        min_salary=1500,
        min_required_experience=2,
        posted_within_days=14,
    )
    assert job_passes_filters(job, filters) is True
    assert job_passes_filters(job, JobFilters(**{**filters.__dict__, "remote_only": False}), ) is True  # remote unchecked
    assert job_passes_filters(job, JobFilters(**{**filters.__dict__, "min_salary": 2000}), ) is False


# ---------------------------------------------------------------------------
# Candidate filters (reverse view: job -> candidates)
# ---------------------------------------------------------------------------


def test_candidate_location_filters():
    candidate = make_candidate(location="Delhi")
    assert candidate_passes_filters(candidate, CandidateFilters(locations=["delhi"])) is True
    assert candidate_passes_filters(candidate, CandidateFilters(locations=["mumbai"])) is False
    assert candidate_passes_filters(candidate, CandidateFilters(exclude_locations=["delhi"])) is False
    assert candidate_passes_filters(candidate, CandidateFilters(exclude_locations=["mumbai"])) is True


def test_candidate_experience_bounds():
    candidate = make_candidate(yearsOfExperience=4)
    assert candidate_passes_filters(candidate, CandidateFilters(min_experience=3)) is True
    assert candidate_passes_filters(candidate, CandidateFilters(min_experience=5)) is False
    assert candidate_passes_filters(candidate, CandidateFilters(max_experience=4)) is True
    assert candidate_passes_filters(candidate, CandidateFilters(max_experience=3)) is False


def test_candidate_skills_include_any():
    candidate = make_candidate(skills=["python", "docker"])
    assert candidate_passes_filters(candidate, CandidateFilters(skills=["kubernetes"])) is False
    assert candidate_passes_filters(candidate, CandidateFilters(skills=["docker", "rust"])) is True
    assert candidate_passes_filters(candidate, CandidateFilters(skills=["Python"])) is True  # case-insensitive


def test_candidate_max_expected_salary():
    candidate = make_candidate(expectedSalary=1200)
    assert candidate_passes_filters(candidate, CandidateFilters(max_expected_salary=1200)) is True
    assert candidate_passes_filters(candidate, CandidateFilters(max_expected_salary=1199)) is False


# ---------------------------------------------------------------------------
# Ported from Naukri-Automation's filterJobs()
# ---------------------------------------------------------------------------


def test_location_containment_matches_segment():
    # "Bengaluru" filter should accept "Bengaluru, Karnataka" (Naukri rule).
    job = make_job(location="Bengaluru, Karnataka, India")
    assert job_passes_filters(job, JobFilters(locations=["bengaluru"])) is True
    assert job_passes_filters(job, JobFilters(locations=["mumbai"])) is False


def test_location_containment_ignores_junk_short_needles():
    job = make_job(location="Atlanta")
    # A one-letter needle must not match by containment.
    assert job_passes_filters(job, JobFilters(locations=["a"])) is False


def test_max_required_experience_caps_too_senior_roles():
    # Naukri rejected postings whose minimumExperience > the candidate's target.
    job = make_job(minYearsExperience=8)
    assert job_passes_filters(job, JobFilters(max_required_experience=3)) is False
    assert job_passes_filters(make_job(minYearsExperience=2), JobFilters(max_required_experience=3)) is True


def test_reject_reason_reports_why():
    assert job_reject_reason(make_job(location="Mumbai"), JobFilters(locations=["bengaluru"])) == "location"
    assert job_reject_reason(make_job(minYearsExperience=8), JobFilters(max_required_experience=3)) == "max_required_experience"
    assert job_reject_reason(make_job(remoteAllowed=False), JobFilters(remote_only=True)) == "not_remote"
    assert job_reject_reason(make_job(postedAt=None), JobFilters(posted_within_days=7)) == "no_post_date"
    assert job_reject_reason(make_job(), JobFilters()) is None

    assert candidate_reject_reason(make_candidate(skills=["rust"]), CandidateFilters(skills=["python"])) == "skills"
    assert candidate_reject_reason(make_candidate(), CandidateFilters()) is None


def test_candidate_skill_filter_uses_aliases():
    candidate = make_candidate(skills=["javascript", "node.js"])
    assert candidate_passes_filters(candidate, CandidateFilters(skills=["js"])) is True
    assert candidate_passes_filters(candidate, CandidateFilters(skills=["nodejs"])) is True
    assert candidate_passes_filters(candidate, CandidateFilters(skills=["java"])) is False