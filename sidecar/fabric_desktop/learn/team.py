"""Team guidance (GD-09): a governance policy sets a team's default learning guidance level.

A policy whose **Checked by** field names ``learn.guidance`` carries the level in its parameters:

    level       the level for everything: "expert", "professional" or "guided"
    sql         the level for SQL findings only (optional)
    notebooks   the level for notebook findings only (optional)

Assigned at the enterprise, a domain or a project like any policy, it reaches every person on that
project, and it travels in the project's policy package (central/policy_package.py), so it applies
offline too. It's a default, never a lock: a level someone picked themselves wins, and only the IT
policy file (app_settings) can fix a level for a PC. A level changes how much guidance shows, never
which rules are checked or required.

Two policies that set a level for one subject: the one with more guidance wins (Guided over
Professional over Expert), as one policy turning a rule on beats another turning it off. A value that
isn't a level is ignored, and ``team_level`` says so.
"""

from __future__ import annotations

from typing import Any

GUIDANCE_RULE = "learn.guidance"
LEVELS = ("expert", "professional", "guided")   # least to most guidance
SUBJECTS = {"sql": "sql", "notebook": "notebooks"}  # a practice kind -> its parameter and settings subject


def _value(params: dict[str, Any], name: str) -> Any:
    v = params.get(name)
    return v.get("value") if isinstance(v, dict) else v


def team_level(policies: list[dict[str, Any]], kind: str, names_rule: Any) -> dict[str, Any]:
    """The team level for one practice kind ("sql" or "notebook") from the resolved policies in force:
    ``{"level", "policy", "problems"}``, with level None when no policy sets one. ``names_rule(policy)``
    says whether a policy's Checked by field names GUIDANCE_RULE."""
    subject = SUBJECTS[kind]
    best: tuple[int, str] | None = None
    problems: list[str] = []
    for pol in policies:
        if not names_rule(pol):
            continue
        params = pol.get("params") or {}
        name = str(pol.get("name") or pol.get("id") or "")
        for key in (subject, "level"):
            raw = _value(params, key)
            if raw in (None, ""):
                continue
            level = str(raw).strip().lower()
            if level not in LEVELS:
                problems.append(f"{name} sets {key} to {raw!r}, which isn't a level (expert, professional or guided), "
                                "so it's ignored")
                continue
            rank = LEVELS.index(level)
            if best is None or rank > best[0]:
                best = (rank, name)
            break  # the subject's own level, when there is one, is this policy's answer
    return {"level": LEVELS[best[0]] if best else None, "policy": best[1] if best else "", "problems": problems}
