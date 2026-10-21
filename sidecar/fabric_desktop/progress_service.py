"""Profile (progress.py has the rules): what the app calls, and the one place evidence comes in.

``_progress_observe`` runs when a validation finishes (validate_service). It records a row unless
the signed-in person has turned off Record my accomplishments (on by default) or preview features
are hidden (Profile is one), and it never changes the validation's result. Everything is stored per account on this PC; nothing is sent anywhere.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from . import app_settings, progress
from .redact import describe

_log = logging.getLogger(__name__)

METHODS = {"profile_get", "profile_save", "profile_exclude", "profile_backfill", "profile_report",
           "profile_report_save", "profile_export", "profile_delete"}

_LOCK = threading.Lock()


class ProgressMixin:
    def _profile_user(self) -> str | None:
        try:
            return self.auth.signed_in_user()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - no account means the signed-out profile
            _log.info("No signed-in account for the profile: %s", describe(exc))
            return None

    def _profile_path(self) -> Path:
        return progress.path_for(Path(self.root), self._profile_user())  # type: ignore[attr-defined]

    def _profile_view(self, profile: dict[str, Any]) -> dict[str, Any]:
        p = progress.progression(profile)
        excluded = set(profile["excluded"])
        rows = [{**r, "excluded": r["id"] in excluded, "xp": p["xp_by_row"].get(r["id"], 0)}
                for r in sorted(profile["evidence"], key=lambda r: r.get("at") or "", reverse=True)]
        progression = {k: v for k, v in p.items() if k != "xp_by_row"}
        return {"settings": profile["settings"], "identity": profile["identity"], "notice": profile["notice"], "evidence": rows,
                "progression": progression, "skills": progress.coverage(profile), "report": profile["report"],
                "catalog": [{"id": k, **v} for k, v in progress.ACHIEVEMENTS.items()],
                "signed_in": self._profile_user() is not None}

    # ---- the hook

    def _progress_observe(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Record a finished validation if it is a verified accomplishment. Returns achievements it earned."""
        if not app_settings.value("show_preview"):
            return []  # Profile is a Preview feature: nothing is recorded while it's hidden
        row = progress.from_validation(record)
        if row is None:
            return []
        path = self._profile_path()
        with _LOCK:
            profile = progress.load(path)
            if not profile["settings"]["record"]:
                return []
            before = {a["id"] for a in progress.progression(profile)["achievements"]}
            if not progress.add(profile, row):
                return []  # the same code validated again: nothing new
            progress.save(path, profile)
        p = progress.progression(profile)
        if not profile["settings"]["progression"] or not profile["settings"]["notifications"]:
            return []
        return [{"id": a["id"], "name": a["name"], "xp": a["xp"]} for a in p["achievements"] if a["id"] not in before]

    # ---- what the app calls

    def profile_get(self) -> dict[str, Any]:
        return self._profile_view(progress.load(self._profile_path()))

    def profile_save(self, settings: dict[str, Any] | None = None, identity: dict[str, Any] | None = None,
                     report_summary: str | None = None, dismiss_notice: bool = False) -> dict[str, Any]:
        path = self._profile_path()
        with _LOCK:
            profile = progress.load(path)
            for k, v in (settings or {}).items():
                if k not in progress.DEFAULT_SETTINGS or k == "backfilled":
                    raise ValueError(f"{k} isn't a profile setting")
                profile["settings"][k] = bool(v)
            if dismiss_notice:
                profile["notice"] = ""
            for k, v in (identity or {}).items():
                if k not in progress.IDENTITY_KEYS:
                    raise ValueError(f"{k} isn't part of the profile")
                profile["identity"][k] = str(v or "").strip()[:progress.MAX_TEXT]
            if report_summary is not None:
                profile["report"]["summary"] = str(report_summary).strip()[:progress.MAX_TEXT]
            progress.save(path, profile)
        return self._profile_view(profile)

    def profile_exclude(self, id: str, excluded: bool = True) -> dict[str, Any]:  # noqa: A002 - the app's parameter name
        path = self._profile_path()
        with _LOCK:
            profile = progress.load(path)
            if not any(r["id"] == id for r in profile["evidence"]):
                raise LookupError("That accomplishment isn't in your profile.")
            rest = [i for i in profile["excluded"] if i != id]
            profile["excluded"] = rest + [id] if excluded else rest
            progress.save(path, profile)
        return self._profile_view(profile)

    def profile_backfill(self) -> dict[str, Any]:
        """Once: add notebooks that passed Validate before recording was on, dated when they ran."""
        path = self._profile_path()
        with _LOCK:
            profile = progress.load(path)
            if not profile["settings"]["record"]:
                raise PermissionError("Turn on Record my accomplishments first.")
            added = 0
            if not profile["settings"]["backfilled"]:
                for record in self._validations_saved():  # type: ignore[attr-defined]
                    row = progress.from_validation(record, origin="backfill")
                    added += bool(row and progress.add(profile, row))
                profile["settings"]["backfilled"] = True
                progress.save(path, profile)
        return {"added": added, **self._profile_view(profile)}

    def profile_report(self, days: int = 90, include_achievements: bool = False) -> dict[str, Any]:
        if not 1 <= int(days) <= 3660:
            raise ValueError("days is 1 to 3660")
        return progress.report(progress.load(self._profile_path()), int(days), bool(include_achievements))

    def profile_report_save(self, days: int = 90, include_achievements: bool = False) -> dict[str, Any]:
        out = self.profile_report(days, include_achievements)
        return {"path": str(_save_new("accomplishments", "md", out["markdown"]))}

    def profile_export(self) -> dict[str, Any]:
        profile = progress.load(self._profile_path())
        return {"path": str(_save_new("kratosforge-profile", "json", json.dumps(profile, indent=1)))}

    def profile_delete(self) -> dict[str, Any]:
        """Delete this account's profile folder. Validate history and receipts are left alone."""
        folder = self._profile_path().parent
        with _LOCK:
            existed = folder.exists()
            if existed:
                shutil.rmtree(folder)
        return {"deleted": existed}


def _save_new(stem: str, ext: str, text: str) -> Path:
    """A new file in Downloads; one already there (even one made a moment ago) is never replaced."""
    folder = Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "-", stem)
    path, n = folder / f"{stem}.{ext}", 2
    while n <= 1000:
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(text)
            return path
        except FileExistsError:
            path, n = folder / f"{stem}-{n}.{ext}", n + 1
    raise ValueError("Couldn't find a free file name in Downloads.")
