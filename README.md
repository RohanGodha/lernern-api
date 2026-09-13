# Job Match API

A small, transparent API that recommends jobs to candidates based on **skill**,
**experience**, **location** and **salary** fit - and, as a bonus, the reverse
view (best-fit candidates for a job).

The matching engine is a **deterministic, rule-based weighted scorer**. There is
no machine learning and no collaborative filtering: every recommendation is a
plain sum of explainable parts, each with its own score, maximum, and a
human-readable `detail` string.

---

## Quick start

### Run locally (in-memory store, zero external dependencies)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs (Swagger UI): http://localhost:8000/docs

### Run with Docker + Postgres (bonus)

```bash
docker compose up --build
```

`docker-compose.yml` starts Postgres, health-checks it, and runs the API with
`DATABASE_URL` pointed at it. The API auto-creates its tables on startup.

### Run tests

```bash
pytest
```

The suite focuses on the **scoring logic** (the highest-value place to test -
see [Testing](#testing)).

---

## Data model

A **candidate**:

| field             | type            |
| ----------------- | --------------- |
| `id`              | int (assigned)  |
| `name`            | string          |
| `skills`          | list[string]    |
| `yearsOfExperience` | number        |
| `location`        | string          |
| `expectedSalary`  | number          |

A **job**:

| field               | type                                          |
| ------------------- | --------------------------------------------- |
| `id`                | int (assigned)                                |
| `title`             | string                                        |
| `requiredSkills`    | list of `{ skill, required }`                 |
| `minYearsExperience`| number                                        |
| `location`          | string                                        |
| `salaryRange`       | `{ min, max }`                                |
| `remoteAllowed`     | bool                                          |
| `postedAt`          | datetime, optional (used by recency filter)   |

`requiredSkills` marks each skill as **must-have** (`required: true`) or
**nice-to-have** (`required: false`) in one list, matching the assignment spec
exactly. Skill and location comparisons are case- and whitespace-insensitive
(e.g. `" Python "` matches `"python"`).

## Endpoints

| Method | Path                                | Description                                  |
| ------ | ----------------------------------- | -------------------------------------------- |
| POST   | `/candidates`                       | Create a candidate profile                   |
| GET    | `/candidates/{id}`                  | Fetch a candidate                            |
| GET    | `/candidates/{id}/recommendations`  | Rank jobs for a candidate (top-N via `limit`)|
| POST   | `/jobs`                             | Create a job posting                         |
| GET    | `/jobs/{id}`                        | Fetch a job                                  |
| GET    | `/jobs/{id}/recommendations`        | **Bonus:** rank best-fit candidates for a job|

Recommendation responses include an overall `matchScore` (0-100) and a
`breakdown` showing each dimension's `score`, `max`, and a plain-language
`detail` explaining what contributed:

```json
{
  "candidateId": 1,
  "total": 1,
  "recommendations": [
    {
      "job": {
        "id": 1,
        "title": "Backend Engineer",
        "requiredSkills": [ { "skill": "python", "required": true }, { "skill": "docker", "required": false } ],
        "minYearsExperience": 3,
        "location": "Bengaluru",
        "salaryRange": { "min": 1000, "max": 1500 },
        "remoteAllowed": false
      },
      "matchScore": 91.0,
      "breakdown": {
        "skills":     { "score": 50.0, "max": 50.0, "detail": "must-have 1/1 matched; nice-to-have 1/1 matched" },
        "experience": { "score": 20.0, "max": 20.0, "detail": "4.0 yrs vs required 3.0 yrs" },
        "location":   { "score": 15.0, "max": 15.0, "detail": "exact location match" },
        "salary":     { "score": 6.0,  "max": 15.0, "detail": "expected 1400 within [1000, 1500]" }
      }
    }
  ]
}
```

**Configurable weights (bonus):** each recommendations endpoint accepts
`w_skills`, `w_experience`, `w_location`, `w_salary` query params:

```
GET /candidates/1/recommendations?limit=5&w_skills=40&w_experience=20&w_location=15&w_salary=25
```

See [Scoring formulas](#scoring-formulas) for how weights interact with
normalization.

## Extensive filters

Both recommendations endpoints also accept **hard include/exclude filters** as
query params. These are *gates*, applied **before scoring** - a filtered-out
job or candidate never appears regardless of score, exactly like the must-have
skill rule but expressed as query params.

The filter vocabulary (whitelist/blacklist locations, role-title keywords,
remote only, recency, salary/experience bounds) is modelled on the job-searching
bots in this workspace: `linkedclaude` (keyword `OR` includes + `NOT` excludes,
`f_WT` remote/work-type, `f_TPR` recency, location), `Naukri-Automation`
(`filterJobs`: accepted/rejected location tiers, experience cap, and a
reject-reason audit), `surelyremote_bot` and `uplers_bot` (`allowed_locations`,
`excluded_locations`, `role_keywords`). Implementation details live in
`app/filters.py`.

### Candidate → jobs (`GET /candidates/{id}/recommendations`)

| param                  | behaviour                                        |
| ---------------------- | ------------------------------------------------ |
| `locations`            | include only jobs in these locations (csv)       |
| `excludeLocations`     | exclude jobs in these locations (csv)            |
| `titleKeywords`        | include only jobs whose title matches ≥1 keyword |
| `excludeTitleKeywords` | exclude jobs whose title matches any keyword     |
| `remoteOnly=true`      | only jobs with `remoteAllowed`                   |
| `minSalary`            | only jobs whose range `max >= minSalary`         |
| `minRequiredExperience`| only jobs requiring `>= minRequiredExperience` yrs |
| `maxRequiredExperience`| only jobs requiring `<= maxRequiredExperience` yrs (drops too-senior roles) |
| `postedWithinDays`     | only jobs with `postedAt` within the last N days |

Location matching is **containment-aware**: a `locations=bengaluru` filter
matches a job at `"Bengaluru, Karnataka"` (ported from Naukri-Automation's
`acceptedLocations` rule), while ignoring meaningless short needles. **Keyword
matching uses word boundaries**, so `titleKeywords=java` does *not* match
"JavaScript" (the reference bots used naive substring checks - this is a
deliberate improvement).

### Reverse view (`GET /jobs/{id}/recommendations`)

| param              | behaviour                                        |
| ------------------ | ------------------------------------------------ |
| `locations`        | include only candidates in these locations (csv) |
| `excludeLocations` | exclude candidates in these locations (csv)      |
| `minExperience`    | only candidates with `yearsOfExperience >= N`    |
| `maxExperience`    | only candidates with `yearsOfExperience <= N`    |
| `skills`           | only candidates having ≥1 of these skills (csv)  |
| `maxExpectedSalary`| only candidates with `expectedSalary <= N`       |

Example:

```
GET /candidates/1/recommendations?titleKeywords=python,ai&excludeTitleKeywords=senior,java&remoteOnly=true&postedWithinDays=7&minSalary=1200
```

### Reject audit (`filterSummary`)

Every recommendation response includes a `filterSummary`, ported from
Naukri-Automation's `rejectCounts` / *"X processed → Y kept / Rejects: …"*
reporting, so hard filters are **explainable** rather than silent drops:

```json
{
  "candidateId": 1,
  "total": 1,
  "filterSummary": {
    "processed": 3,
    "eligible": 1,
    "filteredOut": { "location": 2 },
    "mustHaveExcluded": 0
  },
  "recommendations": [ /* ... */ ]
}
```

`filteredOut` counts drops per filter reason (`location`, `excluded_location`,
`title_keyword`, `excluded_title_keyword`, `not_remote`, `min_salary`,
`min_required_experience`, `max_required_experience`, `too_old`, `no_post_date`);
`mustHaveExcluded` counts eligible items the scorer's must-have gate then
dropped.

---

## Scoring formulas

### Weights (default)

| dimension   | weight | why it has this share                        |
| ----------- | ------ | -------------------------------------------- |
| skills      | **50** | Hardest gate + strongest signal of on-the-job fit |
| experience  | **20** | Strong signal, but softer than skills        |
| location    | **15** | Important, but often flexible (remote/relocation) |
| salary      | **15** | Important, but can be negotiated; expectations are noisy |

When a caller supplies custom weights, they are **normalized to sum to 100** so
the overall score always stays a meaningful 0-100. Passing `w_skills=40&w_experience=20&w_location=15&w_salary=25`
(which already sums to 100) therefore gives you exactly those maxima; passing
weights that sum to e.g. 200 rescales each to keep the ceiling at 100.

### 1. Skills - 50 points (hard filter on must-haves)

- **Must-have skills are a hard filter.** If a candidate misses any must-have
  skill, the job is **excluded entirely** - it never appears in recommendations,
  no matter how perfect everything else is. This mirrors the business reality
  that a job post with an explicit must-have requirement (e.g. "Python
  required") is a checklist item, not a preference.
- **Nice-to-have skills only add points.** They are a bonus, never a gate.
- Within the skills dimension, when a job has both kinds of requirements,
  must-have skills own **60%** of the weight (30 of 50) and nice-to-have own
  **40%** (20 of 50). If only one kind exists, it inherits the full weight. A
  job with *no* skill requirements gets full credit (nothing to satisfy).
- Score = `must_hit_rate × must_weight + nice_hit_rate × nice_weight`, where
  `must_hit_rate` is 1 by construction (non-exclusion) and `nice_hit_rate` is
  the fraction of nice-to-haves the candidate has.
- **Skill aliases.** Before comparing, every skill is mapped to a canonical form
  (`app/skills.py`: `js`/`javascript` → `javascript`, `node.js`/`nodejs` →
  `nodejs`, `react.js` → `react`, `c#`/`csharp`/`.net`/`dotnet` → `.net`,
  `postgresql` → `postgres`, `golang` → `go`, `k8s` → `kubernetes`). Crucially,
  `java` stays `java` and never matches `javascript` - this is the
  disambiguation rule from Naukri-Automation's `isJavaPrimaryOnly`, generalised
  into a transparent dictionary (not a learned model).

**Why 60/40?** Must-haves are non-negotiable, so they deserve the majority of
the skills points; nice-to-haves are differentiators that keep good candidates
from perfectly eligible candidates apart.

### 2. Experience - 20 points (penalize, never exclude)

```
min_yoe <= 0        → full credit (no minimum stated)
yoe >= min_yoe      → full credit
yoe <  min_yoe      → weight × (yoe / min_yoe)   # linear penalty
```

I deliberately chose **penalize over exclude**:

1. **Years of experience is a noisy proxy.** A candidate with 1.5 years of
   strong, relevant experience is often better than one with 3 years of
   tangential work. Using it to *exclude* would discard exactly the people a
   good recruiter would talk to.
2. **Minimums are frequently aspirational.** Job posts regularly inflate the
   minimum; candidates apply anyway and recruiters consider them.
3. **The score already communicates the gap.** A large experience shortfall
   drags the total down visibly and reads clearly in the breakdown - the
   recruiter can decide to look anyway, which is the point of a recommendation
   system.

### 3. Location - 15 points (three tiers)

| situation                        | credit         |
| -------------------------------- | -------------- |
| exact location match             | **15** (full)  |
| mismatch but `remoteAllowed`     | **9.8** (65%)  |
| mismatch and not remote          | **0**          |

**Why 65% for remote?** Remote work eliminates the commute/relocation cost of a
mismatch, but not all of it - time zones, team culture and occasional
on-site requirements still matter. So it's a meaningful majority of the credit,
but an exact match remains strictly better, exactly as the business rule
specifies (exact > remote > mismatch). "Exact" is containment-aware:
`"Bengaluru"` matches `"Bengaluru, Karnataka"`, but two different cities never
overlap.

### 4. Salary - 15 points (position relative to the range)

Let `w = 15`, and `expected = candidate.expectedSalary`, `[lo, hi] = range`:

```
expected <  lo           → w = 15              job pays above expectation → best
lo <= expected <= hi     → w − (⅔·w)·t, t=(expected−lo)/(hi−lo)
                                                full at the floor, ⅓ at the ceiling
expected >  hi           → max(0, ⅓·w − w·overshoot), overshoot=(expected−hi)/hi
                                                near-zero as the gap grows
```

- A candidate below the floor gets the **highest** salary score - "comfortably
  above expectation", per the spec.
- An expectation inside the range gets partial credit that scales with position,
  so near the floor (very comfortable) is rewarded over near the ceiling
  (barely covered).
- An expectation above the ceiling is **penalized, not zeroed instantly** - the
  spec says "near zero", so it starts at ⅓ credit at the boundary and decays to
  0 as the gap grows past roughly a third of the max. A job paying half of what
  the candidate expects scores 0 on this dimension.

### Ranking

- Recommended jobs are sorted by `matchScore`, descending.
- Ties break by ascending entity id, so results are deterministic.
- `limit` (default 10) returns only the top-N.

---

## Why these formulas (design philosophy)

- **Explainability over optimality.** Every number in the breakdown is the
  literal output of the formula in this README. A recruiter can interrogate any
  recommendation by adding up the four numbers.
- **Conservatism.** Every business-rule "hard stop" from the assignment is
  implemented as a hard stop (must-have skills). Where the assignment left
  room (experience below minimum, salary above maximum), I chose to **penalize
  rather than exclude**, because recommendations that quietly vanish are worse
  than recommendations that are visibly imperfect.
- **Defaults match the example.** The default maxima (50/20/15/15) are exactly
  the ones shown in the assignment's example breakdown, and the total is always
  out of 100.

---

## Testing

`pytest` runs 77 tests. The scoring engine is the primary focus because it's
the home of the business rules and the cheapest place to catch regressions.

Highlights (`tests/test_scorer.py`):
- missing a must-have skill → job excluded (even with perfect everything else)
- nice-to-have skills boost, never gate
- a below-minimum candidate is penalized but **not** excluded, with linear
  scaling of the penalty
- location ordering: exact > remote > mismatch, including containment matching
- salary: below floor full credit, in-range partial, above max near zero
- skill aliases: `js`≡`javascript`, `node.js`≡`nodejs`, `c#`≡`.net`, and
  `java` ≠ `javascript`
- edge cases: no skill requirements, case-insensitivity, single-point salary
  bands, zero experience, custom/non-100-sum weights, perfect-match ceiling of
  100

`tests/test_filters.py` covers every hard filter: location whitelist/blacklist
(with containment), title keyword include/exclude (including the word-boundary
vs. partial-match case), remote-only, salary/experience gates, recency,
candidate-side experience/skills/salary bounds, reject-reason reporting, and
combined filters.

`tests/test_api.py` covers the endpoints: create/fetch, response shape, sorting,
`limit`, 404s, validation (e.g. `max < min` salary range rejected), custom
weights via query params, the filter query params, the `filterSummary` reject
audit, and the reverse `/jobs/{id}/recommendations` view. The API tests swap in
a fresh in-memory store per test, so they stay isolated.

---

## Storage

- **Default:** an in-memory store (ids auto-assigned, data lost on restart): zero setup, ideal for the take-home review.
- **Postgres (bonus):** set `DATABASE_URL` and the API persists to a Postgres
  database via SQLAlchemy. `docker compose up --build` wires Postgres + API
  together automatically. No auth is required anywhere (out of scope).

---

## Assumptions & decisions

1. **Exact minimums are soft, must-haves are hard.** The only "exclude"
   condition in the system is a missing must-have skill.
2. **Skills/locations match case-insensitively** with surrounding whitespace
   ignored.
3. **A job with no skill requirements gives full skills credit** - there is
   nothing to fail, so the dimension doesn't drag the score down.
4. **Salary "near zero" is a ramp, not a cliff** - matches the assignment's
   wording ("near zero") rather than an abrupt 0.
5. **Filters gate before scoring.** `postedWithinDays` excludes jobs with a
   missing `postedAt` (an unknown date cannot be confirmed recent); keyword
   filters match whole words, not substrings.
6. **No auth, no UI, no ML** - per the explicit out-of-scope list.
7. Weights are normalized to sum to 100 so the 0-100 invariant holds for any
   weight combination.

### With more time, I'd do

- **Skill taxonomy.** Stemming/synonym mapping (e.g. "JS" → "JavaScript",
  "Node" → "node.js") so near-identical skills aren't silently missed.
- **Persistence tuning.** Migrations (Alembic), a `unique` skill-requirement
  constraint, and pagination (`offset`/`cursor`) instead of a hard `limit`.
- **Salary as a range.** A min-max expected band for candidates gives a cleaner
  overlap computation than a single point.
- **Per-dimension rotation.** Age... i.e. recency weighting for skills ("used
  Python last month" > "used Python 4 years ago").
- **A small CLI demo** so a reviewer can interact without curl.

---

## AI usage disclosure

This project was built with the assistance of an AI coding assistant (Claude /
opencode), which is expected per the assignment. In the interest of
transparency, here is exactly where:

- **Used AI for:** project scaffolding, the FastAPI app structure, Pydantic
  schemas, the storage backends, Docker files, test scaffolding, and drafting
  this README.
- **Human (my own) decisions - where I overrode or edited the AI's output:**
  - Chose the **weights and every scoring formula**, and wrote the reasoning
    above myself. These are the deliverable's heart and I did not delegate them.
  - Chose **"penalize, not exclude"** for experience and salary, and the exact
    remote-credit factor (65%).
  - Decided weights are **normalized to 100** so match scores stay bounded.
  - Wrote and iterated on the **test cases** (including fixing two tests whose
    expectations contradicted the intended behavior - e.g. normalization of
    custom weights, and a tie-break case).
  - Reviewed/edited all generated code for correctness; the AI's first draft of
    the reverse-view ranking had a redundant code path that I refactored away.
  - **I scanned the existing `linkedclaude`, `Naukri-Automation`,
    `surelyremote_bot` and `uplers_bot` codebases in this workspace** to port
    their real-world job filters (location whitelists/blacklists, role
    keywords, remote/recency settings) into the API's extensive filter layer,
    and deliberately improved on them with word-boundary title matching and a
    symmetric reverse-view filter set. From `Naukri-Automation`'s
    `filterJobs()` specifically I ported the **reject-reason audit**
    (`filterSummary`), the **Java-vs-JavaScript disambiguation** (now the
    `app/skills.py` alias table), the **location containment /
    accepted-vs-rejected-location** semantics, and the **maximum-required-
    experience cap**.

Nothing is black-box: the full scoring logic lives in `app/scorer.py` and is
mirrored, formula-for-formula, in this README.