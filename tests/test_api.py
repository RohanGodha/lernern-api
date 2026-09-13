"""End-to-end tests for the FastAPI endpoints (in-memory store)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app import main
    from app.storage import InMemoryStore

    monkeypatch.setattr(main, "store", InMemoryStore())
    return TestClient(main.app)


def _candidate_payload(**overrides):
    payload = {
        "name": "Priya Sharma",
        "skills": ["python", "sql", "docker"],
        "yearsOfExperience": 4,
        "location": "Bengaluru",
        "expectedSalary": 1200,
    }
    payload.update(overrides)
    return payload


def _job_payload(**overrides):
    payload = {
        "title": "Backend Engineer",
        "requiredSkills": [
            {"skill": "python", "required": True},
            {"skill": "sql", "required": True},
            {"skill": "docker", "required": False},
        ],
        "minYearsExperience": 3,
        "location": "Bengaluru",
        "salaryRange": {"min": 1000, "max": 1500},
        "remoteAllowed": False,
    }
    payload.update(overrides)
    return payload


def _create_candidate(client, **overrides):
    resp = client.post("/candidates", json=_candidate_payload(**overrides))
    assert resp.status_code == 201
    return resp.json()


def _create_job(client, **overrides):
    resp = client.post("/jobs", json=_job_payload(**overrides))
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_and_fetch_candidate(client):
    candidate = _create_candidate(client, name="Ada")
    assert candidate["id"] == 1

    resp = client.get(f"/candidates/{candidate['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Ada"


def test_create_and_fetch_job(client):
    job = _create_job(client)
    assert job["id"] == 1
    assert job["salaryRange"]["min"] == 1000

    resp = client.get(f"/jobs/{job['id']}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Backend Engineer"


def test_fetch_unknown_candidate_returns_404(client):
    assert client.get("/candidates/999").status_code == 404


def test_fetch_unknown_job_returns_404(client):
    assert client.get("/jobs/999").status_code == 404


def test_invalid_salary_range_rejected(client):
    resp = client.post("/jobs", json=_job_payload(salaryRange={"min": 2000, "max": 1000}))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /candidates/{id}/recommendations
# ---------------------------------------------------------------------------


def test_recommendations_response_shape(client):
    _create_candidate(client)
    job = _create_job(client)

    resp = client.get("/candidates/1/recommendations")
    assert resp.status_code == 200

    body = resp.json()
    assert body["candidateId"] == 1
    assert body["total"] == 1

    rec = body["recommendations"][0]
    assert rec["job"]["id"] == job["id"]
    assert 0 <= rec["matchScore"] <= 100
    assert set(rec["breakdown"]) == {"skills", "experience", "location", "salary"}
    assert {"score", "max", "detail"} <= set(rec["breakdown"]["skills"])


def test_recommendations_are_sorted_descending(client):
    _create_candidate(client)  # id 1
    _create_job(client, title="decent")
    _create_job(client, title="worst", location="Mumbai", minYearsExperience=12)
    _create_job(
        client,
        title="best",
        requiredSkills=[{"skill": "python", "required": True}],
        salaryRange={"min": 1200, "max": 1500},  # expected 1200 -> full salary credit
    )

    body = client.get("/candidates/1/recommendations").json()
    scores = [r["matchScore"] for r in body["recommendations"]]
    assert scores == sorted(scores, reverse=True)
    assert body["recommendations"][0]["job"]["title"] == "best"


def test_recommendations_limit_top_n(client):
    _create_candidate(client)
    for _ in range(3):
        _create_job(client)

    body = client.get("/candidates/1/recommendations?limit=2").json()
    assert body["total"] == 2
    assert len(body["recommendations"]) == 2


def test_recommendations_exclude_jobs_with_missing_must_have(client):
    candidate = _create_candidate(client, skills=["python"])
    excluded = _create_job(client, title="needs go", requiredSkills=[{"skill": "go", "required": True}])
    included = _create_job(client, title="python role", requiredSkills=[{"skill": "python", "required": True}])

    body = client.get(f"/candidates/{candidate['id']}/recommendations").json()
    job_ids = [r["job"]["id"] for r in body["recommendations"]]
    assert excluded["id"] not in job_ids
    assert included["id"] in job_ids


def test_recommendations_unknown_candidate_404(client):
    assert client.get("/candidates/404/recommendations").status_code == 404


# ---------------------------------------------------------------------------
# Configurable weights via query params
# ---------------------------------------------------------------------------


def test_recommendations_with_custom_weights(client):
    _create_candidate(client)
    _create_job(client)

    body = client.get(
        "/candidates/1/recommendations?w_skills=40&w_experience=20&w_location=15&w_salary=25"
    ).json()
    breakdown = body["recommendations"][0]["breakdown"]
    assert breakdown["skills"]["max"] == 40
    assert breakdown["experience"]["max"] == 20
    assert breakdown["location"]["max"] == 15
    assert breakdown["salary"]["max"] == 25

    score = body["recommendations"][0]["matchScore"]
    assert 0 <= score <= 100


def test_recommendations_reject_negative_weight(client):
    _create_candidate(client)
    resp = client.get("/candidates/1/recommendations?w_skills=-5")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Reverse view: /jobs/{id}/recommendations
# ---------------------------------------------------------------------------


def test_reverse_recommendations_rank_candidates(client):
    job = _create_job(client)
    best = _create_candidate(client, name="best", skills=["python", "sql", "docker"])
    weak = _create_candidate(client, name="weak", skills=["java"], yearsOfExperience=1, location="Mumbai", expectedSalary=5000)

    body = client.get(f"/jobs/{job['id']}/recommendations").json()
    assert body["jobId"] == job["id"]
    scores = [r["matchScore"] for r in body["recommendations"]]
    assert scores == sorted(scores, reverse=True)

    candidate_ids = [r["candidate"]["id"] for r in body["recommendations"]]
    assert best["id"] in candidate_ids
    # weak misses the python/sql must-haves -> should not appear.
    assert weak["id"] not in candidate_ids


def test_reverse_recommendations_unknown_job_404(client):
    assert client.get("/jobs/404/recommendations").status_code == 404


# ---------------------------------------------------------------------------
# Extensive filters via query params
# ---------------------------------------------------------------------------


def test_filters_locations(client):
    _create_candidate(client)
    in_bengaluru = _create_job(client, title="A")
    in_mumbai = _create_job(client, title="B", location="Mumbai")

    body = client.get("/candidates/1/recommendations?locations=bengaluru").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert titles == ["A"]

    body = client.get("/candidates/1/recommendations?excludeLocations=bengaluru").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert titles == ["B"]


def test_filters_title_keywords_and_exclude(client):
    _create_candidate(client)
    _create_job(client, title="Generative AI Engineer")
    _create_job(client, title="Farmer")

    body = client.get("/candidates/1/recommendations?titleKeywords=ai").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert "Generative AI Engineer" in titles
    assert "Farmer" not in titles

    body = client.get("/candidates/1/recommendations?excludeTitleKeywords=ai").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert "Farmer" in titles
    assert "Generative AI Engineer" not in titles


def test_filters_remote_only(client):
    _create_candidate(client)
    _create_job(client, title="remote ok", remoteAllowed=True)
    _create_job(client, title="onsite", remoteAllowed=False)

    body = client.get("/candidates/1/recommendations?remoteOnly=true").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert titles == ["remote ok"]


def test_filters_min_salary(client):
    _create_candidate(client)
    _create_job(client, title="rich", salaryRange={"min": 2000, "max": 3000})
    _create_job(client, title="cheap", salaryRange={"min": 100, "max": 200})

    body = client.get("/candidates/1/recommendations?minSalary=1500").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert titles == ["rich"]


def test_filters_posted_within_days(client):
    from datetime import datetime, timedelta, timezone

    _create_candidate(client)
    _create_job(client, title="fresh", postedAt=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    _create_job(client, title="ancient", postedAt=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat())
    _create_job(client, title="no date")

    body = client.get("/candidates/1/recommendations?postedWithinDays=7").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert titles == ["fresh"]


def test_reverse_filters_skills_and_experience(client):
    # Job requires python+sql (nice: docker); min 3 yrs.
    job = _create_job(client)
    _create_candidate(client, name="pythonista", skills=["python", "sql", "docker"], yearsOfExperience=5)
    _create_candidate(client, name="qa", skills=["python", "sql", "java"], yearsOfExperience=5)
    _create_candidate(client, name="junior", skills=["python"], yearsOfExperience=0)

    # Only the candidate that ALSO has docker passes the skill filter.
    body = client.get(f"/jobs/{job['id']}/recommendations?skills=docker&minExperience=3").json()
    names = [r["candidate"]["name"] for r in body["recommendations"]]
    assert names == ["pythonista"]

    # Without the skill filter, both experienced python+sql candidates appear;
    # pythonista ranks first because docker is a matched nice-to-have.
    body = client.get(f"/jobs/{job['id']}/recommendations?minExperience=3").json()
    names = [r["candidate"]["name"] for r in body["recommendations"]]
    assert names[:2] == ["pythonista", "qa"]

    # Nobody expects <= 100 here (all expect 1200).
    body = client.get(f"/jobs/{job['id']}/recommendations?maxExpectedSalary=100").json()
    assert body["total"] == 0


def test_filters_max_required_experience(client):
    _create_candidate(client)
    _create_job(client, title="junior role", minYearsExperience=1)
    _create_job(client, title="senior role", minYearsExperience=9)

    body = client.get("/candidates/1/recommendations?maxRequiredExperience=3").json()
    titles = [r["job"]["title"] for r in body["recommendations"]]
    assert titles == ["junior role"]


def test_recommendations_filter_summary_audit(client):
    # Candidate lacks "go", so one job is dropped by the scorer's must-have gate.
    _create_candidate(client, skills=["python"])
    _create_job(client, title="python role", location="Mumbai", requiredSkills=[{"skill": "python", "required": True}])
    _create_job(client, title="go role", requiredSkills=[{"skill": "go", "required": True}])
    _create_job(client, title="far away", location="Chennai", requiredSkills=[{"skill": "python", "required": True}])

    body = client.get("/candidates/1/recommendations?locations=mumbai").json()
    summary = body["filterSummary"]
    assert summary["processed"] == 3
    assert summary["eligible"] == 1  # only the Mumbai python job passes the location filter
    assert summary["filteredOut"] == {"location": 2}
    assert summary["mustHaveExcluded"] == 0
    assert [r["job"]["title"] for r in body["recommendations"]] == ["python role"]


def test_recommendations_filter_summary_counts_must_have_exclusions(client):
    _create_candidate(client, skills=["python"])  # no go, no java
    _create_job(client, requiredSkills=[{"skill": "go", "required": True}])
    _create_job(client, requiredSkills=[{"skill": "java", "required": True}])

    body = client.get("/candidates/1/recommendations").json()
    summary = body["filterSummary"]
    assert summary["processed"] == 2
    assert summary["eligible"] == 2
    assert summary["filteredOut"] == {}
    assert summary["mustHaveExcluded"] == 2
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_root_endpoint(client):
    assert client.get("/").json()["app"] == "Job Match API"