"""Weight configuration for the scoring engine.

Weights define the maximum number of points each dimension can contribute to
the overall match score (which is bounded between 0 and 100).

The defaults are the same maxima shown in the assignment example breakdown
(skills: 50, experience: 20, location: 15, salary: 15).

Weights are configurable:
  * at the API level via query params (`w_skills`, `w_experience`,
    `w_location`, `w_salary`), or
  * programmatically by constructing a `Weights` instance.

Weights do not have to sum to 100: `normalized()` rescales them so the total
always equals 100, keeping match scores interpretable as percentages.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Weights:
    skills: float = 50.0
    experience: float = 20.0
    location: float = 15.0
    salary: float = 15.0

    def normalized(self) -> "Weights":
        """Return a copy rescaled so the weights sum to exactly 100."""
        total = self.skills + self.experience + self.location + self.salary
        if total <= 0:
            raise ValueError("total weight must be positive")
        k = 100.0 / total
        return Weights(
            skills=round(self.skills * k, 4),
            experience=round(self.experience * k, 4),
            location=round(self.location * k, 4),
            salary=round(self.salary * k, 4),
        )


DEFAULT_WEIGHTS = Weights()