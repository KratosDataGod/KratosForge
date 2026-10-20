"""Comments on notebook cells: threads attached to a cell by its stable ``cell_id``, optionally to a
range of lines in its code, as in Fabric and Databricks notebooks.

They are kept in the notebook file, under ``metadata.fabric_desktop.comments``, so they travel with
the .ipynb and through Git. The notebook screen's Save never sends them: merge_saved keeps
``fabric_desktop`` keys it doesn't own, and every change here re-reads the file and replaces it in
one step under the store's write lock (NotebookStore.update_extra), so a save and a comment can't
undo each other. Publishing to Fabric builds the notebook from its cells alone (fabric_ipynb), so
comments stay on this PC and in Git; Fabric's own comments are a separate service.

A thread on a cell that has since been deleted is kept and shown as "on a deleted cell" until it is
resolved or its comments are deleted. ``@name`` in a comment is only highlighted: nobody is notified.

Each comment keeps who wrote it: ``author`` is the signed-in account's name (its email), which goes
into Git with the notebook, or "" when no one was signed in; ``author_id`` is that account's Entra
object id ("aad:<id>"), or a random id kept on this PC (``comment-author-id``) when signed out. Edit
and delete of your own, and "You" on screen, go by ``author_id``.

Stored shape, a list of threads:
    {"id", "cell_id", "lines": [first, last] | None, "quote": the lines' code when commented,
     "resolved": bool, "resolved_by", "resolved_by_id", "resolved_at",
     "comments": [{"id", "author", "author_id", "at", "text", "edited_at"?}]}
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

METHODS = {"notebook_comments_list", "notebook_comment_add", "notebook_comment_reply", "notebook_comment_edit",
           "notebook_comment_delete", "notebook_comment_resolve"}

KEY = "comments"
MAX_TEXT = 4000        # characters in one comment
MAX_THREADS = 500      # in one notebook
MAX_REPLIES = 200      # in one thread
QUOTE_CHARS = 500      # of the commented lines kept with the thread
PC_ID_FILE = "comment-author-id"  # under the data folder: who "you" are when signed out
_CELL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _text(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Write a comment first.")
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise ValueError(f"A comment can be up to {MAX_TEXT} characters.")
    return text


def _threads(saved: Any) -> list[dict[str, Any]]:
    """The saved threads, leaving out anything malformed (a hand-edited or merged file)."""
    if not isinstance(saved, list):
        return []
    return [t for t in saved if isinstance(t, dict) and isinstance(t.get("id"), str)
            and isinstance(t.get("cell_id"), str) and isinstance(t.get("comments"), list)]


def _find(threads: list[dict[str, Any]], thread_id: str) -> dict[str, Any]:
    t = next((t for t in threads if t["id"] == thread_id), None)
    if t is None:
        raise ValueError("That comment thread is no longer in the notebook. Reopen the notebook to see the latest comments.")
    return t


def _comment(thread: dict[str, Any], comment_id: str) -> dict[str, Any]:
    c = next((c for c in thread["comments"] if isinstance(c, dict) and c.get("id") == comment_id), None)
    if c is None:
        raise ValueError("That comment is no longer in the notebook. Reopen the notebook to see the latest comments.")
    return c


def _cell_source(nb: dict[str, Any], cell_id: str) -> str | None:
    for c in nb.get("cells") or []:
        if isinstance(c, dict) and c.get("id") == cell_id:
            src = c.get("source", "")
            return "".join(src) if isinstance(src, list) else str(src)
    return None


def _lines(lines: Any, source: str) -> tuple[list[int] | None, str]:
    """``lines`` checked against the cell's code, and the code on them."""
    if lines is None:
        return None, ""
    if (not isinstance(lines, list) or len(lines) != 2 or not all(isinstance(n, int) and not isinstance(n, bool) for n in lines)
            or lines[0] < 1 or lines[1] < lines[0]):
        raise ValueError("lines is [first, last], counted from 1.")
    code = source.split("\n")
    if lines[1] > len(code):
        raise ValueError(f"The cell has {len(code)} lines; save the notebook and try again.")
    return [lines[0], lines[1]], "\n".join(code[lines[0] - 1:lines[1]])[:QUOTE_CHARS]


class CommentsMixin:
    def _comment_author(self, create: bool = True) -> dict[str, str]:
        """Who is writing: {"name", "id"}. The signed-in account (only one already signed in; a
        comment never opens a sign-in window), else no name and this PC's own id, made the first
        time it's needed (``create``; a read doesn't make one, and its id is then "")."""
        auth = getattr(self, "_auth", None)
        name = (auth.signed_in_user() if auth is not None else None) or ""
        if name:
            oid = auth.signed_in_object_id() if hasattr(auth, "signed_in_object_id") else None
            return {"name": name, "id": f"aad:{oid}" if oid else f"user:{name.lower()}"}
        path = self.root / PC_ID_FILE  # type: ignore[attr-defined]
        try:
            pc = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pc = ""
        if not pc and create:
            pc = uuid.uuid4().hex
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(pc, encoding="utf-8")
        return {"name": "", "id": f"pc:{pc}" if pc else ""}

    def _comments_out(self, threads: list[dict[str, Any]], me: dict[str, str] | None = None) -> dict[str, Any]:
        return {"threads": threads, "me": me or self._comment_author(create=False)}

    def _comments_change(self, name: str, change: Any) -> dict[str, Any]:
        """Apply ``change(threads, nb)`` to the threads as they are in the file now and save them."""
        def apply(saved: Any, nb: dict[str, Any]) -> Any:
            threads = _threads(saved)
            change(threads, nb)
            return threads or None
        return self._comments_out(_threads(self.notebooks.update_extra(name, KEY, apply)), self._comment_author())  # type: ignore[attr-defined]

    def notebook_comments_list(self, name: str) -> dict[str, Any]:
        """Every comment thread in the notebook, and who "me" is ({"name", "id"}: comments whose
        ``author_id`` is ``me.id`` are yours to edit or delete)."""
        return self._comments_out(_threads(self.notebooks.extra(name, KEY)))  # type: ignore[attr-defined]

    def notebook_comment_add(self, name: str, cell_id: str, text: str, lines: list[int] | None = None) -> dict[str, Any]:
        """Start a thread on a saved cell (save the notebook first), optionally on lines of its code.
        Writes the notebook file."""
        text = _text(text)
        if not isinstance(cell_id, str) or not _CELL_ID.match(cell_id):
            raise ValueError("cell_id isn't a cell id.")
        me = self._comment_author()

        def add(threads: list[dict[str, Any]], nb: dict[str, Any]) -> None:
            source = _cell_source(nb, cell_id)
            if source is None:
                raise ValueError("That cell isn't saved yet. Save the notebook and try again.")
            if len(threads) >= MAX_THREADS:
                raise ValueError(f"A notebook can have up to {MAX_THREADS} comment threads. Delete resolved ones first.")
            span, quote = _lines(lines, source)
            threads.append({"id": _new_id(), "cell_id": cell_id, "lines": span, "quote": quote, "resolved": False,
                            "comments": [{"id": _new_id(), "author": me["name"], "author_id": me["id"], "at": _now(), "text": text}]})
        return self._comments_change(name, add)

    def notebook_comment_reply(self, name: str, thread_id: str, text: str) -> dict[str, Any]:
        """Add a reply to a thread. Writes the notebook file."""
        text = _text(text)
        me = self._comment_author()

        def reply(threads: list[dict[str, Any]], _nb: dict[str, Any]) -> None:
            t = _find(threads, thread_id)
            if len(t["comments"]) >= MAX_REPLIES:
                raise ValueError(f"A thread can have up to {MAX_REPLIES} comments. Start a new thread.")
            t["comments"].append({"id": _new_id(), "author": me["name"], "author_id": me["id"], "at": _now(), "text": text})
        return self._comments_change(name, reply)

    def notebook_comment_edit(self, name: str, thread_id: str, comment_id: str, text: str) -> dict[str, Any]:
        """Change the text of one of your own comments. Writes the notebook file."""
        text = _text(text)
        me = self._comment_author()

        def edit(threads: list[dict[str, Any]], _nb: dict[str, Any]) -> None:
            c = _comment(_find(threads, thread_id), comment_id)
            if not me["id"] or c.get("author_id") != me["id"]:
                raise ValueError("You can only edit your own comments.")
            c.update(text=text, edited_at=_now())
        return self._comments_change(name, edit)

    def notebook_comment_delete(self, name: str, thread_id: str, comment_id: str) -> dict[str, Any]:
        """Delete one of your own comments. Deleting a thread's first comment deletes the thread,
        which needs every reply to be yours too. Writes the notebook file."""
        me = self._comment_author()

        def delete(threads: list[dict[str, Any]], _nb: dict[str, Any]) -> None:
            t = _find(threads, thread_id)
            c = _comment(t, comment_id)
            if not me["id"] or c.get("author_id") != me["id"]:
                raise ValueError("You can only delete your own comments.")
            if c is t["comments"][0]:
                if any(isinstance(r, dict) and r.get("author_id") != me["id"] for r in t["comments"]):
                    raise ValueError("Others have replied to this thread, so it can't be deleted. Resolve it instead.")
                threads.remove(t)
            else:
                t["comments"].remove(c)
        return self._comments_change(name, delete)

    def notebook_comment_resolve(self, name: str, thread_id: str, resolved: bool = True) -> dict[str, Any]:
        """Resolve a thread, or reopen it with ``resolved`` false. Anyone can. Writes the notebook file."""
        me = self._comment_author()

        def resolve(threads: list[dict[str, Any]], _nb: dict[str, Any]) -> None:
            t = _find(threads, thread_id)
            t["resolved"] = bool(resolved)
            if resolved:
                t.update(resolved_by=me["name"], resolved_by_id=me["id"], resolved_at=_now())
            else:
                t.pop("resolved_by", None)
                t.pop("resolved_by_id", None)
                t.pop("resolved_at", None)
        return self._comments_change(name, resolve)
