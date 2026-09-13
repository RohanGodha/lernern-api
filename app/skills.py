"""Skill normalization / small taxonomy.

Ported from the `isJavaPrimaryOnly` + rejected-skills logic in
D:\\RohanDocs\\Naukri-Automation\\utils\\jobUtils.js, which realised that
matching skills verbatim is too naive: "java" and "javascript" are different
things, "node.js" and "nodejs" are the same thing, and ".NET" is written as
"csharp", "c#", or "asp.net".

The rule there was: reject a job ONLY if "java" is primary and there are no
JavaScript/JS-family indicators (node.js, react, angular, vue, typescript...).
We adapt that here as a *normalization* layer: every skill string is mapped to
a canonical form BEFORE any comparison, so:

  * "js", "javascript" -> "javascript"
  * "node.js", "nodejs" -> "nodejs"
  * "react.js", "reactjs" -> "react"
  * "c#", "csharp", ".net", "dotnet", "asp.net" -> ".net"
  * "postgresql" -> "postgres", "golang" -> "go", "k8s" -> "kubernetes"

"java" intentionally maps to *itself* and never to "javascript", preserving the
Java <-> JavaScript distinction the bot fought to keep. This is a deliberate,
transparent dictionary - not a learned model - so it stays fully explainable
(no ML, per the assignment).
"""
from __future__ import annotations

# Canonical-as-value alias table. Keys are already casefolded.
ALIASES: dict[str, str] = {
    # each entry: <raw-ish variants> -> <canonical skill>
    "js": "javascript",
    "javascript": "javascript",
    "java script": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "typescript": "typescript",
    "node": "nodejs",
    "node.js": "nodejs",
    "nodejs": "nodejs",
    "express.js": "express",
    "expressjs": "express",
    "react.js": "react",
    "reactjs": "react",
    "react": "react",
    "vue.js": "vue",
    "vuejs": "vue",
    "angular.js": "angular",
    "angularjs": "angular",
    "c#": ".net",
    "csharp": ".net",
    "c sharp": ".net",
    ".net": ".net",
    "dotnet": ".net",
    "asp.net": ".net",
    "aspnet": ".net",
    "vb.net": ".net",
    "postgresql": "postgres",
    "postgres": "postgres",
    "golang": "go",
    "go": "go",
    "k8s": "kubernetes",
    "kubernetes": "kubernetes",
    "docker": "docker",
    "python": "python",
    "sql": "sql",
}


def normalize_skill(skill: object) -> str:
    """Map a raw skill string to its canonical form (case/whitespace-first).

    Unknown skills fall through unchanged (stripped + casefolded), so this is
    safe to apply to every skill in the system.
    """
    s = str(skill).strip().casefold()
    return ALIASES.get(s, s)