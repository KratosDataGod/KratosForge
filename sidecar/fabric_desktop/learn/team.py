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

**Team notes** (the rest of GD-09, team guidance packages): the same policy's ``notes`` parameter adds
the team's own words to a rule's or a decision pattern's guide, by its id:

    notes       {"SQ002": "Our fact tables are partitioned by order_date, so ...", "DP-02": "..."}

The built-in guide stays as it is; a note sits beside it, named with the policy, its owner, version and
SHA-256, so a reader knows who wrote it and which published version it came from. Notes travel with the
policy, in the platform and in the project's policy package, so they are hashed, versioned and owned
like any policy and need no service of their own. Every policy's note for an id is kept (not merged),
in policy name order. A note for an id that has no guide or pattern, or that isn't text, is ignored,
and ``team_notes`` says so. Notes are plain text, never Markdown or HTML, and never change a check.
"""

from __future__ import annotations

from typing import Any

GUIDANCE_RULE = "learn.guidance"
NOTES = "notes"
NOTE_MAX = 2000      # characters in one note
NOTES_MAX = 200      # notes in one policy
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


def team_notes(policies: list[dict[str, Any]], names_rule: Any, known: set[str] | frozenset[str]) -> dict[str, Any]:
    """The team notes from the resolved policies in force: ``{"notes": {id: [note, ...]}, "problems"}``, each
    note ``{"text", "policy", "owner", "version", "sha256"}``. ``known`` is the ids a note may name (the
    guides' and the decision patterns'); ``names_rule(policy)`` says whether a policy names GUIDANCE_RULE."""
    notes: dict[str, list[dict[str, Any]]] = {}
    problems: list[str] = []
    for pol in sorted(policies, key=lambda p: (str(p.get("name") or ""), str(p.get("id") or ""))):
        if not names_rule(pol):
            continue
        raw = (pol.get("params") or {}).get(NOTES)
        if isinstance(raw, dict) and "value" in raw:  # a definition's {"value": ...}; resolved params are plain
            raw = raw["value"]
        if raw in (None, "", {}):
            continue
        name = str(pol.get("name") or pol.get("id") or "")
        if not isinstance(raw, dict):
            problems.append(f"{name} sets notes to something that isn't a set of notes by rule id, so it's ignored")
            continue
        if len(raw) > NOTES_MAX:
            problems.append(f"{name} has {len(raw)} notes; only the first {NOTES_MAX} are used")
        for key, text in list(raw.items())[:NOTES_MAX]:
            rid = str(key).strip().upper()
            if rid not in known:
                problems.append(f"{name} has a note for {str(key)[:40]!r}, which isn't a rule or decision pattern "
                                "with a guide, so it's ignored")
                continue
            if not isinstance(text, str) or not text.strip():
                problems.append(f"{name}'s note for {rid} isn't text, so it's ignored")
                continue
            body = text.strip()
            if len(body) > NOTE_MAX:
                problems.append(f"{name}'s note for {rid} is cut to {NOTE_MAX} characters")
                body = body[:NOTE_MAX].rstrip()
            notes.setdefault(rid, []).append({"text": body, "policy": name, "owner": str(pol.get("owner") or ""),
                                              "version": pol.get("version"), "sha256": str(pol.get("hash") or "")})
    return {"notes": notes, "problems": problems}
