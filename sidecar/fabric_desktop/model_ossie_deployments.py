"""The deployment record of Import from Ossie (OSSIE-015b): one line per step of each confirmed import.

``<data>/model_deployments.jsonl`` on this PC. Before the model is written in Fabric a ``started`` line goes in,
and after it a ``finished`` line with the same ``id`` says ``ok`` or ``failed``, so an import that failed or
crashed still leaves evidence. Lines are only ever appended (``runlog.append``, which keeps the newest
``KEEP_ROWS``); ``deployments`` folds them back into one record per import.

A record holds when, who (the signed-in account, as the audit trail records it), the environment, workspace and
model names, the mode, the file's Ossie version and profile, the change counts, the fidelity report's class counts
and ``needs_dax`` as a count. It never holds a role filter, a member, an expression or the file's text: an error
message has every one of those that the file or the model holds replaced (``model_ossie_redact``), credential-shaped
text removed (``redact.secrets``) and is cut short. The preview, and an import not confirmed, write nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from . import model_ossie as mo
from . import model_ossie_redact, ossie_profiles, runlog
from .redact import secrets

FILE = "model_deployments.jsonl"
TRIM_BYTES = 2 * 1024 * 1024  # a record is well under 2 KB: thousands of imports before the file is trimmed
KEEP_ROWS = 2_000
MAX_MESSAGE = 200
_MEMBER_KEYS = frozenset({"memberName", "memberId"})


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _texts(value: Any, out: set[str], key: str = "") -> set[str]:
    """Every expression (any key ending ``xpression``: a measure's, a filter's, a format string's, an Ossie
    dialect's) and every member name and id in ``value``, a whole line and each of its lines."""
    if isinstance(value, dict):
        for k, v in value.items():
            _texts(v, out, str(k))
    elif isinstance(value, list) and not (key.endswith("xpression") and all(isinstance(v, str) for v in value)):
        for v in value:
            _texts(v, out, key)
    elif key.endswith("xpression") or key in _MEMBER_KEYS:
        text = mo._text(value)
        out |= {text, *text.split("\n")}
    return out


def _needles(*sources: Any) -> list[tuple[str, str]]:
    """What a message about this import must not quote: each source's expressions and members, and the roles
    kept in an Ossie file's Power BI section."""
    found: set[str] = set()
    for source in sources:
        _texts(source, found)
    roles = [r for s in sources for r in model_ossie_redact._stash_roles(s) + model_ossie_redact._model_roles(s)]
    found |= model_ossie_redact._sensitive(roles)
    return model_ossie_redact._needles({s for s in found if len(s.strip()) >= model_ossie_redact.MIN_SCRUBBED})


def message(exc: BaseException, *sources: Any) -> str:
    """``exc``'s message safe for the record: no filter, member or expression of ``sources``, no secret, short."""
    text = model_ossie_redact._scrub(secrets(str(exc)), _needles(*sources))
    return text if len(text) <= MAX_MESSAGE else f"{text[:MAX_MESSAGE]}... ({len(text)} characters)"


def fidelity_summary(report: Any) -> dict[str, Any]:
    """The fidelity report as counts only: its status, each class's count, and how many warnings (security ones
    apart) and flagged roles and calculation groups it has. No object is named."""
    if not isinstance(report, dict):
        return {"status": "none"}
    counts: dict[str, Any] = c if isinstance(c := report.get("counts"), dict) else {}
    warnings = [w for w in report.get("warnings") or [] if isinstance(w, dict)]
    flagged: dict[str, Any] = f if isinstance(f := report.get("flagged"), dict) else {}
    return {"status": str(report.get("status", "")),
            "counts": {c: int(counts.get(c) or 0) for c in mo.CLASSES},
            "warnings": len(warnings), "security_warnings": sum(w.get("severity") == "security" for w in warnings),
            "flagged_roles": len(flagged.get("roles") or []),
            "flagged_calculation_groups": len(flagged.get("calculation_groups") or [])}


def _profile(doc: Any) -> str:
    try:
        return ossie_profiles.detect_profile(doc)
    except ValueError:  # narrow: UnknownProfile; parse already read the file, so this is never expected
        return "unknown"


def started(folder: Path, *, env: str, workspace: str, name: str, mode: str, doc: Any, who: str,
            counts: dict[str, int], fidelity: Any, needs_dax: int) -> str:
    """Append the ``started`` line of an import about to be written, and return its id."""
    record_id = uuid.uuid4().hex
    version = doc.get("version") if isinstance(doc, dict) else None
    runlog.append(Path(folder) / FILE, {
        "id": record_id, "phase": "started", "at": _now(), "who": who, "env": env,
        "workspace": mo._short(workspace), "model": mo._short(name), "mode": mode,
        "ossie_version": mo._short(version) if isinstance(version, str) else "", "profile": _profile(doc),
        "counts": {str(k): int(v) for k, v in counts.items()}, "fidelity": fidelity_summary(fidelity),
        "needs_dax": int(needs_dax), "outcome": "started"}, TRIM_BYTES, KEEP_ROWS)
    return record_id


def finished(folder: Path, record_id: str, outcome: str, error_class: str = "", error: str = "",
             created: bool | None = None) -> None:
    """Append the ``finished`` line of import ``record_id``: ``ok``, or ``failed`` with the error's class and a
    message already made safe (``message``)."""
    row: dict[str, Any] = {"id": record_id, "phase": "finished", "at": _now(), "outcome": outcome}
    if outcome == "failed":
        row["error"] = {"class": error_class, "message": error}
    if created is not None:
        row["created"] = created
    runlog.append(Path(folder) / FILE, row, TRIM_BYTES, KEEP_ROWS)


def deployments(folder: Path, env: str, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The imports recorded on this PC for ``env`` (and model ``name`` when given), newest first. One record per
    import: its ``started`` line with the ``finished`` line's outcome, error and ``finished_at`` laid over, or
    ``outcome`` still "started" when none followed (a crash, or one still running). A line that isn't a JSON
    object, or a ``finished`` line whose ``started`` line was trimmed away, is skipped."""
    try:
        lines = (Path(folder) / FILE).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    records: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):  # narrow: a damaged or half-written line; only it is skipped
            continue
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        if row.get("phase") == "started":
            records[row["id"]] = {k: v for k, v in row.items() if k != "phase"} | {"finished_at": None}
        elif row.get("phase") == "finished" and row["id"] in records:
            records[row["id"]] |= {"outcome": row.get("outcome", ""), "finished_at": row.get("at"),
                                   **{k: row[k] for k in ("error", "created") if k in row}}
    rows = [r for r in records.values() if r.get("env") == env and (name is None or r.get("model") == mo._short(name))]
    return rows[::-1][:max(0, int(limit))]

