"""Profile (progress.py has the rules): what the app calls, and the one place evidence comes in.

``_progress_observe`` runs when a validation finishes (validate_service), ``_progress_observe_proof``
when a proof does, and ``_progress_observe_lab`` when a Learn exercise passes. Each records a row unless
the signed-in person has turned off Record my accomplishments (on by default) or preview features
are hidden (Profile is one), and it never changes the validation's result. Everything is stored per account on this PC; nothing is sent anywhere.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import app_settings, jsonfile, pdfreport, progress, ranks, roadmap
from .learn import content as learn_content
from .redact import describe
from . import known_folders

_log = logging.getLogger(__name__)

METHODS = {"profile_get", "profile_save", "profile_exclude", "profile_backfill", "profile_report",
           "profile_report_save", "profile_export", "profile_delete", "profile_avatar", "profile_avatar_set",
           "profile_avatar_remove", "profile_report_pdf", "profile_goal_add", "profile_goal_remove",
           "profile_goal_done"}

# The picture the app made: a 256 px square PNG, re-encoded in the app from what the person chose, so
# photo metadata such as location never reaches the file (PB-125).
AVATAR_SIZE = 256
AVATAR_MAX_BYTES = 512 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"

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
        live = learn_content.modules(include_drafts=False)
        standing = ranks.standing(profile, live)
        progression["title"] = standing["rank"]  # the rank is the title; the level stays a number (PB-125)
        return {"ranks": standing, "avatar": self._avatar_url(), "settings": profile["settings"], "identity": profile["identity"], "notice": profile["notice"], "evidence": rows,
                "progression": progression, "skills": progress.coverage(profile), "report": profile["report"],
                "catalog": [{"id": k, **v} for k, v in progress.ACHIEVEMENTS.items()],
                "impact": progress.impact(profile), "roadmap": roadmap.roadmap(profile, live),
                "signed_in": self._profile_user() is not None}

    # ---- the hook

    def _progress_observe(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Record a finished validation if it is a verified accomplishment. Returns achievements it earned."""
        return self._progress_record(progress.from_validation(record))

    def _progress_observe_proof(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Record a finished proof whose tables all matched. Returns achievements it earned."""
        return self._progress_record(progress.from_proof(record, record.get("receipt_id")))

    def _progress_observe_lab(self, result: dict[str, Any], module: dict[str, Any]) -> list[dict[str, Any]]:
        """Record a Learn exercise whose checks all passed, and the lesson finished when it was its last
        exercise. Returns achievements they earned."""
        return self._progress_record(progress.from_lab(result, module), then=lambda p: progress.module_complete(p, module))

    def _progress_record(self, row: dict[str, Any] | None,
                         then: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None) -> list[dict[str, Any]]:
        """Add ``row``, then the row ``then`` makes from the profile with it (if any), in one write."""
        if row is None or not app_settings.value("show_preview"):
            return []  # Profile is a Preview feature: nothing is recorded while it's hidden
        path = self._profile_path()
        with _LOCK:
            profile = progress.load(path)
            if not profile["settings"]["record"]:
                return []
            before = {a["id"] for a in progress.progression(profile)["achievements"]}
            if not progress.add(profile, row):
                return []  # the same code verified again: nothing new
            follow = then(profile) if then else None
            if follow:
                progress.add(profile, follow)
            progress.save(path, profile)
        p = progress.progression(profile)
        if not profile["settings"]["progression"] or not profile["settings"]["notifications"]:
            return []
        # "first": the profile had no achievement before this one, so the app says recording is on
        return [{"id": a["id"], "name": a["name"], "xp": a["xp"], "first": not before}
                for a in p["achievements"] if a["id"] not in before]

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
        """Once: add notebooks that passed Validate or Prove before recording was on, dated when they ran."""
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
                for record in self._proofs_saved():  # type: ignore[attr-defined]
                    row = progress.from_proof(record, record.get("receipt_id"), origin="backfill")
                    added += bool(row and progress.add(profile, row))
                profile["settings"]["backfilled"] = True
                progress.save(path, profile)
        return {"added": added, **self._profile_view(profile)}

    def profile_report(self, days: int = 90, include_achievements: bool = False) -> dict[str, Any]:
        if not 1 <= int(days) <= 3660:
            raise ValueError("days is 1 to 3660")
        profile = progress.load(self._profile_path())
        plan = roadmap.roadmap(profile, learn_content.modules(include_drafts=False))
        return progress.report(profile, int(days), bool(include_achievements), plan=plan)

    def profile_report_save(self, days: int = 90, include_achievements: bool = False) -> dict[str, Any]:
        out = self.profile_report(days, include_achievements)
        return {"path": str(_save_new("accomplishments", "md", out["markdown"]))}

    def profile_report_pdf(self, days: int = 90, include_achievements: bool = False) -> dict[str, Any]:
        """The same report as a PDF in Downloads (pdfreport.py: text only, standard fonts, no new library)."""
        out = self.profile_report(days, include_achievements)
        return {"path": str(_save_new("accomplishments", "pdf", pdfreport.render(out["markdown"])))}

    # ---- goals (KF-DX-011): a roadmap milestone, met only by verified evidence, or the person's own words

    def _profile_goals(self, change: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
        path = self._profile_path()
        with _LOCK:
            profile = progress.load(path)
            change(profile)
            progress.save(path, profile)
        return self._profile_view(profile)

    def profile_goal_add(self, milestone: str | None = None, text: str | None = None) -> dict[str, Any]:
        if bool(milestone) == bool(text and str(text).strip()):
            raise ValueError("Give a roadmap milestone or a goal in your own words, not both.")
        return self._profile_goals(lambda p: roadmap.add_goal(p, milestone=milestone, text=text))

    def profile_goal_remove(self, id: str) -> dict[str, Any]:  # noqa: A002 - the app's parameter name
        return self._profile_goals(lambda p: roadmap.remove_goal(p, id))

    def profile_goal_done(self, id: str, done: bool = True) -> dict[str, Any]:  # noqa: A002 - the app's parameter name
        return self._profile_goals(lambda p: roadmap.mark_goal(p, id, bool(done)))

    def profile_export(self) -> dict[str, Any]:
        profile = progress.load(self._profile_path())
        return {"path": str(_save_new("kratosforge-profile", "json", json.dumps(profile, indent=1)))}

    # ---- the profile picture (PB-125): stays in this account's profile folder on this PC

    def _avatar_path(self) -> Path:
        return self._profile_path().parent / "avatar.png"

    def _avatar_url(self) -> str | None:
        try:
            data = self._avatar_path().read_bytes()
        except FileNotFoundError:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii") if _avatar_ok(data) else None

    def profile_avatar(self) -> dict[str, Any]:
        return {"avatar": self._avatar_url()}

    def profile_avatar_set(self, png_base64: str) -> dict[str, Any]:
        """Keep the picture the app cropped and re-encoded: a 256 px square PNG, at most 512 KB."""
        try:
            data = base64.b64decode(str(png_base64), validate=True)
        except ValueError:
            raise ValueError("That picture couldn't be read.") from None
        if not _avatar_ok(data):
            raise ValueError(f"The picture must be a {AVATAR_SIZE} px square PNG of at most {AVATAR_MAX_BYTES // 1024} KB.")
        with _LOCK:
            jsonfile.write_bytes(self._avatar_path(), data)
        return self.profile_get()

    def profile_avatar_remove(self) -> dict[str, Any]:
        with _LOCK:
            self._avatar_path().unlink(missing_ok=True)
        return self.profile_get()

    def profile_delete(self) -> dict[str, Any]:
        """Delete this account's profile folder. Validate history and receipts are left alone."""
        folder = self._profile_path().parent
        with _LOCK:
            existed = folder.exists()
            if existed:
                shutil.rmtree(folder)
        return {"deleted": existed}


def _avatar_ok(data: bytes) -> bool:
    """A PNG whose header says AVATAR_SIZE square, within the size limit. The webview decodes it as an
    image only (img src), so nothing in it runs."""
    if len(data) > AVATAR_MAX_BYTES or len(data) < 33 or not data.startswith(_PNG) or data[12:16] != b"IHDR":
        return False
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return width == height == AVATAR_SIZE


def _save_new(stem: str, ext: str, text: str | bytes) -> Path:
    """A new file in Downloads; one already there (even one made a moment ago) is never replaced."""
    folder = known_folders.downloads()
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "-", stem)
    path, n = folder / f"{stem}.{ext}", 2
    while n <= 1000:
        try:
            if isinstance(text, bytes):
                with path.open("xb") as fb:
                    fb.write(text)
            else:
                with path.open("x", encoding="utf-8") as f:
                    f.write(text)
            return path
        except FileExistsError:
            path, n = folder / f"{stem}-{n}.{ext}", n + 1
    raise ValueError("Couldn't find a free file name in Downloads.")
