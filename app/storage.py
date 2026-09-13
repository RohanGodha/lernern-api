"""Storage backends.

The API uses an in-memory store by default so it runs with zero external
dependencies. When the `DATABASE_URL` environment variable is set, it uses a
Postgres-backed store instead (see docker-compose.yaml).
"""
from __future__ import annotations

import os

from .models import Candidate, Job


class InMemoryStore:
    """Thread-local, id-assigning store used when no DB is configured."""

    def __init__(self) -> None:
        self._candidates: dict[int, Candidate] = {}
        self._jobs: dict[int, Job] = {}
        self._next_candidate_id = 1
        self._next_job_id = 1

    def add_candidate(self, candidate: Candidate) -> Candidate:
        saved = candidate.model_copy(update={"id": self._next_candidate_id})
        self._next_candidate_id += 1
        self._candidates[saved.id] = saved
        return saved

    def add_job(self, job: Job) -> Job:
        saved = job.model_copy(update={"id": self._next_job_id})
        self._next_job_id += 1
        self._jobs[saved.id] = saved
        return saved

    def get_candidate(self, candidate_id: int) -> Candidate | None:
        return self._candidates.get(candidate_id)

    def get_job(self, job_id: int) -> Job | None:
        return self._jobs.get(job_id)

    def all_candidates(self) -> list[Candidate]:
        return list(self._candidates.values())

    def all_jobs(self) -> list[Job]:
        return list(self._jobs.values())


class DbStore:
    """Postgres-backed store (SQLAlchemy). Used when DATABASE_URL is set."""

    def __init__(self, url: str) -> None:
        from . import db

        self._session_factory = db.build_session_factory(url)

    def add_candidate(self, candidate: Candidate) -> Candidate:
        from . import db

        with self._session_factory() as session:
            row = db.CandidateRow(
                name=candidate.name,
                skills=candidate.skills,
                years_of_experience=candidate.yearsOfExperience,
                location=candidate.location,
                expected_salary=candidate.expectedSalary,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return Candidate(
                id=row.id,
                name=row.name,
                skills=row.skills,
                yearsOfExperience=row.years_of_experience,
                location=row.location,
                expectedSalary=row.expected_salary,
            )

    def add_job(self, job: Job) -> Job:
        from . import db

        with self._session_factory() as session:
            row = db.JobRow(
                title=job.title,
                required_skills=[r.model_dump() for r in job.requiredSkills],
                min_years_experience=job.minYearsExperience,
                location=job.location,
                salary_min=job.salaryRange.min,
                salary_max=job.salaryRange.max,
                remote_allowed=job.remoteAllowed,
                posted_at=job.postedAt,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._row_to_job(row)

    def get_candidate(self, candidate_id: int) -> Candidate | None:
        from . import db

        with self._session_factory() as session:
            row = session.get(db.CandidateRow, candidate_id)
            if row is None:
                return None
            return Candidate(
                id=row.id,
                name=row.name,
                skills=row.skills,
                yearsOfExperience=row.years_of_experience,
                location=row.location,
                expectedSalary=row.expected_salary,
            )

    def get_job(self, job_id: int) -> Job | None:
        from . import db

        with self._session_factory() as session:
            row = session.get(db.JobRow, job_id)
            if row is None:
                return None
            return self._row_to_job(row)

    def all_candidates(self) -> list[Candidate]:
        from . import db

        with self._session_factory() as session:
            rows = session.query(db.CandidateRow).all()
            return [
                Candidate(
                    id=r.id,
                    name=r.name,
                    skills=r.skills,
                    yearsOfExperience=r.years_of_experience,
                    location=r.location,
                    expectedSalary=r.expected_salary,
                )
                for r in rows
            ]

    def all_jobs(self) -> list[Job]:
        from . import db

        with self._session_factory() as session:
            rows = session.query(db.JobRow).all()
            return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row) -> Job:
        return Job(
            id=row.id,
            title=row.title,
            requiredSkills=row.required_skills or [],
            minYearsExperience=row.min_years_experience,
            location=row.location,
            salaryRange={"min": row.salary_min, "max": row.salary_max},
            remoteAllowed=row.remote_allowed,
            postedAt=row.posted_at,
        )


def get_store():
    """Pick the store based on configuration. In-memory unless a DB is set."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return DbStore(url)
    return InMemoryStore()