"""Unit tests for the scoring engine - the highest-value test surface."""

import pytest

from app.models import Candidate, Job
from app.scorer import (
    candidate_passes_must_have_filter,
    evaluate,
    recommend_jobs_for_candidate,
)
from app.weights import DEFAULT_WEIGHTS, Weights


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
        requiredSkills=[
            {"skill": "python", "required": True},
            {"skill": "sql", "required": True},
            {"skill": "docker", "required": False},
        ],
        minYearsExperience=3,
        location="Bengaluru",
        salaryRange={"min": 1000, "max": 1500},
        remoteAllowed=False,
    )
    base.update(overrides)
    return Job(**base)


# ---------------------------------------------------------------------------
# Must-have hard filter
# ---------------------------------------------------------------------------


def test_missing_must_have_skill_is_excluded():
    candidate = make_candidate(skills=["java", "go"])
    job = make_job()  # requires python + sql as must-have
    assert evaluate(candidate, job) is None
    assert not candidate_passes_must_have_filter(candidate, job)


def test_has_all_must_have_skills_is_not_excluded():
    candidate = make_candidate()
    job = make_job()
    assert candidate_passes_must_have_filter(candidate, job)
    result = evaluate(candidate, job)
    assert result is not None


def test_missing_must_have_excluded_despite_perfect_elsewhere():
    # Missing must-have, but everything else matches perfectly.
    # The job must still never appear in recommendations.
    candidate = make_candidate(
        skills=["java"], yearsOfExperience=10, location="Bengaluru", expectedSalary=1100
    )
    job = make_job(minYearsExperience=1, salaryRange={"min": 800, "max": 2000})
    assert evaluate(candidate, job) is None


def test_skill_match_is_case_and_whitespace_insensitive():
    candidate = make_candidate(skills=["Python", "SQL"])
    job = make_job(requiredSkills=[{"skill": " python ", "required": True}])
    result = evaluate(candidate, job)
    assert result is not None


# ---------------------------------------------------------------------------
# Skills dimension
# ---------------------------------------------------------------------------


def test_nice_to_have_skills_boost_the_score():
    candidate = make_candidate(skills=["python", "sql", "docker"])
    job_with_docker = make_job()
    job_without_docker = make_job(
        requiredSkills=[
            {"skill": "python", "required": True},
            {"skill": "sql", "required": True},
            {"skill": "kubernetes", "required": False},
        ]
    )
    score_with = evaluate(candidate, job_with_docker)["matchScore"]
    score_without = evaluate(candidate, job_without_docker)["matchScore"]
    assert score_with > score_without


def test_job_with_only_nice_to_have_skills_does_not_gate():
    candidate = make_candidate(skills=["excel"])
    job = make_job(
        requiredSkills=[
            {"skill": "powerpoint", "required": False},
            {"skill": "excel", "required": False},
        ]
    )
    result = evaluate(candidate, job)
    assert result is not None
    skills = result["breakdown"]["skills"]
    assert skills["score"] > 0  # 1 of 2 nice-to-haves matched


def test_job_with_no_skill_requirements_gives_full_skill_credit():
    candidate = make_candidate(skills=[])
    job = make_job(requiredSkills=[])
    result = evaluate(candidate, job)
    assert result is not None
    assert result["breakdown"]["skills"]["score"] == DEFAULT_WEIGHTS.skills


# ---------------------------------------------------------------------------
# Experience dimension (penalize, never exclude)
# ---------------------------------------------------------------------------


def test_below_min_experience_is_penalized_not_excluded():
    candidate = make_candidate(yearsOfExperience=1)
    job = make_job(minYearsExperience=5)
    result = evaluate(candidate, job)
    assert result is not None  # not excluded
    exp = result["breakdown"]["experience"]
    assert exp["max"] == DEFAULT_WEIGHTS.experience
    assert exp["score"] < exp["max"]  # penalized
    assert exp["score"] > 0


def test_experience_scales_linearly_with_shortfall():
    min_yoe = 10
    job = make_job(minYearsExperience=min_yoe)
    weight = DEFAULT_WEIGHTS.experience

    half = evaluate(make_candidate(yearsOfExperience=5), job)
    quarter = evaluate(make_candidate(yearsOfExperience=2.5), job)
    zero = evaluate(make_candidate(yearsOfExperience=0), job)

    assert half["breakdown"]["experience"]["score"] == pytest.approx(weight * 0.5)
    assert quarter["breakdown"]["experience"]["score"] == pytest.approx(weight * 0.25)
    assert zero["breakdown"]["experience"]["score"] == pytest.approx(0)


def test_at_or_above_min_experience_is_full_credit():
    job = make_job(minYearsExperience=3)
    for yoe in (3, 3, 9, 25):  # noqa: B007 duplicate 3 is intentional
        result = evaluate(make_candidate(yearsOfExperience=yoe), job)
        assert result["breakdown"]["experience"]["score"] == DEFAULT_WEIGHTS.experience


def test_no_minimum_experience_means_full_credit():
    job = make_job(minYearsExperience=0)
    result = evaluate(make_candidate(yearsOfExperience=0), job)
    assert result["breakdown"]["experience"]["score"] == DEFAULT_WEIGHTS.experience


# ---------------------------------------------------------------------------
# Location dimension ordering: exact > remote > mismatch
# ---------------------------------------------------------------------------


def test_location_exact_beats_remote_beats_mismatch():
    candidate = make_candidate(location="Bengaluru")

    exact = make_job(location="Bengaluru", remoteAllowed=False)
    remote = make_job(location="Mumbai", remoteAllowed=True)
    mismatch = make_job(location="Mumbai", remoteAllowed=False)

    exact_score = evaluate(candidate, exact)["breakdown"]["location"]["score"]
    remote_score = evaluate(candidate, remote)["breakdown"]["location"]["score"]
    mismatch_score = evaluate(candidate, mismatch)["breakdown"]["location"]["score"]

    assert exact_score == DEFAULT_WEIGHTS.location
    assert remote_score < exact_score
    assert remote_score > mismatch_score
    assert mismatch_score == 0
    assert remote_score > 0  # partial credit for remote-friendly roles


# ---------------------------------------------------------------------------
# Salary dimension
# ---------------------------------------------------------------------------


def test_salary_below_range_floor_is_full_credit():
    # Job comfortably above the candidate's expectation -> highest score.
    candidate = make_candidate(expectedSalary=800)
    job = make_job(salaryRange={"min": 1000, "max": 1500})
    result = evaluate(candidate, job)
    assert result["breakdown"]["salary"]["score"] == DEFAULT_WEIGHTS.salary


def test_salary_inside_range_is_partial_credit():
    salary = DEFAULT_WEIGHTS.salary  # 15
    job = make_job(salaryRange={"min": 1000, "max": 1500})

    at_floor = evaluate(make_candidate(expectedSalary=1000), job)
    at_ceiling = evaluate(make_candidate(expectedSalary=1500), job)
    mid = evaluate(make_candidate(expectedSalary=1250), job)

    assert at_floor["breakdown"]["salary"]["score"] == pytest.approx(salary)
    assert at_ceiling["breakdown"]["salary"]["score"] == pytest.approx(salary / 3)
    assert mid["breakdown"]["salary"]["score"] == pytest.approx(salary * 2 / 3)
    assert at_ceiling["breakdown"]["salary"]["score"] < at_floor["breakdown"]["salary"]["score"]


def test_salary_above_max_almost_zero():
    # max well below expectation -> near-zero on this dimension.
    job = make_job(salaryRange={"min": 1000, "max": 1500})
    result = evaluate(make_candidate(expectedSalary=6000), job)
    score = result["breakdown"]["salary"]["score"]
    assert score >= 0
    assert score < 1  # "near zero"


def test_salary_at_boundary_above_max_is_penalized_but_positive():
    salary = DEFAULT_WEIGHTS.salary
    job = make_job(salaryRange={"min": 1000, "max": 1500})
    result = evaluate(make_candidate(expectedSalary=1600), job)
    score = result["breakdown"]["salary"]["score"]
    assert 0 < score < salary  # penalized but not instantly zero


def test_single_point_salary_band():
    job = make_job(salaryRange={"min": 1200, "max": 1200})
    exact = evaluate(make_candidate(expectedSalary=1200), job)
    above = evaluate(make_candidate(expectedSalary=2000), job)
    assert exact["breakdown"]["salary"]["score"] == DEFAULT_WEIGHTS.salary
    assert above["breakdown"]["salary"]["score"] < exact["breakdown"]["salary"]["score"]


def test_salary_no_overlap_scores_near_zero():
    # Candidate expectation far above the max -> effectively zero overlap.
    job = make_job(salaryRange={"min": 500, "max": 800})
    result = evaluate(make_candidate(expectedSalary=4000), job)
    assert result["breakdown"]["salary"]["score"] == 0


# ---------------------------------------------------------------------------
# Overall score + configurable weights
# ---------------------------------------------------------------------------


def test_total_score_is_in_0_to_100_range():
    candidate = make_candidate(skills=["python", "sql", "docker"])
    job = make_job()
    result = evaluate(candidate, job)
    assert 0 <= result["matchScore"] <= 100


def test_custom_weights_change_dimension_maxima():
    weights = Weights(skills=40, experience=20, location=15, salary=25)
    candidate = make_candidate()
    job = make_job()
    result = evaluate(candidate, job, weights)
    assert result["breakdown"]["skills"]["max"] == 40
    assert result["breakdown"]["salary"]["max"] == 25


def test_weights_normalized_before_scoring():
    # Non-zero custom weights that sum to 200 must still yield a <= 100 score.
    weights = Weights(skills=100, experience=40, location=30, salary=30)
    candidate = make_candidate(skills=["python", "sql", "docker"])
    job = make_job()
    result = evaluate(candidate, job, weights)
    assert result["matchScore"] <= 100
    # Normalization scales each component back: skills max is 50%.
    assert result["breakdown"]["skills"]["max"] == pytest.approx(50)


def test_best_overall_match_is_100():
    # Perfect candidate/job alignment should hit the ceiling.
    candidate = make_candidate(skills=["python", "sql", "docker"])
    job = make_job(
        requiredSkills=[
            {"skill": "python", "required": True},
            {"skill": "sql", "required": True},
            {"skill": "docker", "required": False},
        ],
        minYearsExperience=3,
        location="Bengaluru",
        salaryRange={"min": 1200, "max": 1500},  # expected 1200 == floor
        remoteAllowed=False,
    )
    result = evaluate(candidate, job)
    assert result["matchScore"] == pytest.approx(100)


# ---------------------------------------------------------------------------
# Ranking / top-N
# ---------------------------------------------------------------------------


def test_recommendations_ranked_descending_and_limited():
    candidate = make_candidate(
        skills=["python", "sql", "docker"],
        yearsOfExperience=6,
        location="Bengaluru",
        expectedSalary=300,  # below most salary bands -> max salary credit
    )
    jobs = [
        make_job(title="best", requiredSkills=[{"skill": "python", "required": True}], salaryRange={"min": 1000, "max": 1500}),
        make_job(title="ok", requiredSkills=[{"skill": "java", "required": True}, {"skill": "sql", "required": True}], minYearsExperience=8, location="Mumbai", salaryRange={"min": 1000, "max": 1500}),
        make_job(title="worst", requiredSkills=[{"skill": "python", "required": True}], minYearsExperience=12, location="Mumbai", salaryRange={"min": 200, "max": 250}, remoteAllowed=False),
    ]
    recs = recommend_jobs_for_candidate(candidate, jobs)
    scores = [r["matchScore"] for r in recs]
    assert scores == sorted(scores, reverse=True)

    limited = recommend_jobs_for_candidate(candidate, jobs, limit=2)
    assert len(limited) == 2
    assert limited[0]["matchScore"] >= limited[1]["matchScore"]


def test_recommendations_exclude_missing_must_have_jobs():
    candidate = make_candidate(skills=["python"])
    ineligible = make_job(title="needs java", requiredSkills=[{"skill": "java", "required": True}])
    eligible = make_job(title="python role", requiredSkills=[{"skill": "python", "required": True}])
    recs = recommend_jobs_for_candidate(candidate, [ineligible, eligible])
    titles = [r["job"].title for r in recs]
    assert "needs java" not in titles
    assert "python role" in titles


def test_recommendation_response_has_explainable_breakdown():
    candidate = make_candidate()
    job = make_job()
    result = evaluate(candidate, job)
    breakdown = result["breakdown"]
    assert set(breakdown) == {"skills", "experience", "location", "salary"}
    assert breakdown["skills"]["detail"]
    assert breakdown["experience"]["detail"]
    assert breakdown["location"]["detail"]
    assert breakdown["salary"]["detail"]
    # Breakdown sums to the overall score.
    total = sum(b["score"] for b in breakdown.values())
    assert result["matchScore"] == pytest.approx(round(total, 1))


# ---------------------------------------------------------------------------
# Skill aliases / taxonomy (ported from Naukri-Automation's isJavaPrimaryOnly)
# ---------------------------------------------------------------------------


def test_skill_alias_js_matches_javascript():
    candidate = make_candidate(skills=["js"])
    job = make_job(requiredSkills=[{"skill": "JavaScript", "required": True}])
    assert evaluate(candidate, job) is not None


def test_skill_alias_node_variants_match():
    candidate = make_candidate(skills=["node.js"])
    job = make_job(requiredSkills=[{"skill": "nodejs", "required": True}])
    assert evaluate(candidate, job) is not None


def test_skill_alias_dotnet_family_match():
    candidate = make_candidate(skills=["c#"])
    job = make_job(requiredSkills=[{"skill": ".NET", "required": True}])
    assert evaluate(candidate, job) is not None


def test_java_does_not_match_javascript():
    # The whole point of the Naukri disambiguation: java != javascript.
    candidate = make_candidate(skills=["javascript"])
    job = make_job(requiredSkills=[{"skill": "java", "required": True}])
    assert evaluate(candidate, job) is None


def test_skill_alias_applies_to_nice_to_have():
    candidate = make_candidate(skills=["python", "sql", "react.js"])
    job = make_job(
        requiredSkills=[
            {"skill": "python", "required": True},
            {"skill": "sql", "required": True},
            {"skill": "react", "required": False},
        ]
    )
    result = evaluate(candidate, job)
    assert "nice-to-have 1/1 matched" in result["breakdown"]["skills"]["detail"]


# ---------------------------------------------------------------------------
# Location containment in scoring (ported from Naukri's acceptedLocations rule)
# ---------------------------------------------------------------------------


def test_location_containment_counts_as_exact():
    candidate = make_candidate(location="Bengaluru")
    job = make_job(location="Bengaluru, Karnataka")
    score = evaluate(candidate, job)["breakdown"]["location"]["score"]
    assert score == DEFAULT_WEIGHTS.location


def test_location_containment_does_not_false_match():
    candidate = make_candidate(location="Bengaluru")
    job = make_job(location="Mumbai, Maharashtra", remoteAllowed=False)
    score = evaluate(candidate, job)["breakdown"]["location"]["score"]
    assert score == 0