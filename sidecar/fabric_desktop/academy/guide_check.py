"""Certification change detection (ACD-020): compare a vendor's published study guide with a pack's registry.

A scheduled workflow (.github/workflows/academy-guide-check.yml) fetches each pack's study guide with
``tools/academy_guide_check.py`` and hands the page to ``check()``. The page is untrusted text: it is
parsed with the standard library's HTML parser, nothing in it is run, and only the skills list is kept.

``extract()`` reads the skills-measured part of a Microsoft Learn study guide: domain headings (h3) with
their weight range, skill areas (h4) and the objectives listed under each (li). ``compare()`` lines that up
with the registry's domains, skill areas and objectives by position and reports what was added, removed or
reworded, with a draft crosswalk (old objective id -> new objective ids) for people to review. A changed
page never changes the registry or anyone's results: the workflow opens a draft PR with the report, and
people update the registry, its snapshot hash and the crosswalk themselves (docs/academy/architecture.md).

Only Learn study guides (``source.url`` on learn.microsoft.com) are read here. A pack whose guide is a PDF
(the Databricks pack) is reported as not checked automatically.
"""

from __future__ import annotations

import difflib
import re
from html.parser import HTMLParser
from typing import Any

from . import registry

LEARN_HOST = "https://learn.microsoft.com/"
MAX_PAGE_BYTES = 4 * 1024 * 1024   # a study guide page is a few hundred KB
REWORDED = 0.6                     # how alike two objectives' words must be to count as one reworded
_WEIGHT = re.compile(r"^(?P<title>.+?)\s*\((?P<min>\d+)\s*[–—-]\s*(?P<max>\d+)\s*%\)\s*$")
_UPDATED = re.compile(r"Last updated on\s+(\d{4}-\d{2}-\d{2})")
_SPACE = re.compile(r"\s+")


class _Outline(HTMLParser):
    """Headings (h2-h4) and list items, in page order, as ``(tag, text)``."""

    _KEEP = ("h2", "h3", "h4", "li")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self._open: list[tuple[str, list[str]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._KEEP:
            self._open.append((tag, []))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._KEEP and self._open and self._open[-1][0] == tag:
            name, parts = self._open.pop()
            text = _SPACE.sub(" ", "".join(parts)).strip()
            if text:
                self.items.append((name, text))

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._open:
            self._open[-1][1].append(data)


def extract(html: str) -> dict[str, Any]:
    """The published skills list of a Learn study guide page: ``{domains: [{title, weight: {min, max},
    skills: [{title, objectives: [text]}]}], updated}``. Raises ValueError when the page has no
    skills-measured section or no weighted domain in it (the page changed shape, or it isn't a guide)."""
    if len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise ValueError(f"The page is over {MAX_PAGE_BYTES // (1024 * 1024)} MB; a study guide is much smaller.")
    parser = _Outline()
    parser.feed(html)
    parser.close()
    domains: list[dict[str, Any]] = []
    inside = False
    domain: dict[str, Any] | None = None
    skill: dict[str, Any] | None = None
    for tag, text in parser.items:
        if tag == "h2":
            if inside:
                break
            inside = text.lower().startswith("skills measured")
            continue
        if not inside:
            continue
        if tag == "h3":
            m = _WEIGHT.match(text)
            domain, skill = None, None
            if m:   # "Audience profile" and "Skills at a glance" have no weight
                domain = {"title": m["title"], "weight": {"min": int(m["min"]), "max": int(m["max"])}, "skills": []}
                domains.append(domain)
        elif tag == "h4" and domain is not None:
            skill = {"title": text, "objectives": []}
            domain["skills"].append(skill)
        elif tag == "li" and skill is not None:
            skill["objectives"].append(text)
    if not domains:
        raise ValueError("The page has no skills-measured section with weighted domains; its layout may have changed.")
    updated = _UPDATED.search("".join(parser.text))
    return {"domains": domains, "updated": updated.group(1) if updated else None}


def published(cert: dict[str, Any]) -> dict[str, Any]:
    """The registry's copy of what the vendor publishes, in ``extract()``'s shape, with ids."""
    out = []
    for d in cert.get("domains") or []:
        w = d.get("weight") or {}
        out.append({"id": d["id"], "title": d["title"], "weight": {"min": w.get("min"), "max": w.get("max")},
                    "skills": [{"id": s.get("id"), "title": s.get("title"),
                                "objectives": [{"id": o["id"], "text": o["text"]} for o in s.get("objectives") or []]}
                               for s in registry._groups(d)]})
    return {"domains": out}


def _alike(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _match(old: list[str], new: list[str]) -> list[tuple[int | None, int | None]]:
    """Pairs of positions (old, new): the same words first, then the most alike left over (at least
    REWORDED), then what's left on each side alone."""
    pairs: list[tuple[int | None, int | None]] = []
    free_old, free_new = list(range(len(old))), list(range(len(new)))
    for i in list(free_old):
        j = next((j for j in free_new if new[j] == old[i]), None)
        if j is not None:
            pairs.append((i, j))
            free_old.remove(i)
            free_new.remove(j)
    scored = sorted(((_alike(old[i], new[j]), i, j) for i in free_old for j in free_new), reverse=True)
    for score, i, j in scored:
        if score >= REWORDED and i in free_old and j in free_new:
            pairs.append((i, j))
            free_old.remove(i)
            free_new.remove(j)
    pairs += [(i, None) for i in free_old] + [(None, j) for j in free_new]
    return pairs


def compare(cert: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """What changed between the registry and the page. ``changed`` is False when every domain title and
    weight, skill area title and objective's words are the same. New objectives get ids in the page's
    order (domain.skill.n), the way the registry numbers them; the crosswalk maps each old objective id
    to the new ids it became ([] when it was removed)."""
    old = published(cert)["domains"]
    new = page["domains"]
    out: dict[str, Any] = {"domains": [], "skills": [], "added": [], "removed": [], "reworded": [], "moved": [],
                           "crosswalk": {}}
    flat = any(s.get("_flat") for d in cert.get("domains") or [] for s in registry._groups(d))
    if flat:
        raise ValueError(f"{cert.get('id')}: its guide lists objectives straight under each section; "
                         "only Learn study guides with skill areas are checked here")
    for oi, ni in (_match([d["title"] for d in old], [d["title"] for d in new])):
        od = old[oi] if oi is not None else None
        nd = new[ni] if ni is not None else None
        d_id = str(ni + 1) if ni is not None else None
        if od and nd and (od["title"] != nd["title"] or od["weight"] != nd["weight"]):
            out["domains"].append({"id": od["id"], "new_id": d_id, "was": f"{od['title']} ({od['weight']['min']}-{od['weight']['max']}%)",
                                   "now": f"{nd['title']} ({nd['weight']['min']}-{nd['weight']['max']}%)"})
        elif od and not nd:
            out["domains"].append({"id": od["id"], "new_id": None, "was": od["title"], "now": None})
        elif nd and not od:
            out["domains"].append({"id": None, "new_id": d_id, "was": None, "now": nd["title"]})
        old_skills = od["skills"] if od else []
        new_skills = nd["skills"] if nd else []
        for si, sj in _match([s["title"] for s in old_skills], [s["title"] for s in new_skills]):
            os_ = old_skills[si] if si is not None else None
            ns = new_skills[sj] if sj is not None else None
            s_id = f"{d_id}.{sj + 1}" if (d_id and sj is not None) else None
            if os_ and ns and os_["title"] != ns["title"]:
                out["skills"].append({"id": os_["id"], "new_id": s_id, "was": os_["title"], "now": ns["title"]})
            elif os_ and not ns:
                out["skills"].append({"id": os_["id"], "new_id": None, "was": os_["title"], "now": None})
            elif ns and not os_:
                out["skills"].append({"id": None, "new_id": s_id, "was": None, "now": ns["title"]})
            old_obj = os_["objectives"] if os_ else []
            new_obj = ns["objectives"] if ns else []
            for oj, nj in _match([o["text"] for o in old_obj], new_obj):
                o = old_obj[oj] if oj is not None else None
                n_id = f"{s_id}.{nj + 1}" if (s_id and nj is not None) else None
                if o and nj is not None:
                    out["crosswalk"][o["id"]] = [n_id]
                    if o["text"] != new_obj[nj]:
                        out["reworded"].append({"id": o["id"], "new_id": n_id, "was": o["text"], "now": new_obj[nj]})
                    elif o["id"] != n_id:
                        out["moved"].append({"id": o["id"], "new_id": n_id, "text": o["text"]})
                elif o:
                    out["crosswalk"][o["id"]] = []
                    out["removed"].append({"id": o["id"], "text": o["text"]})
                elif nj is not None:
                    out["added"].append({"new_id": n_id, "text": new_obj[nj]})
    out["changed"] = any(out[k] for k in ("domains", "skills", "added", "removed", "reworded", "moved"))
    if not out["changed"]:
        out["crosswalk"] = {}
    return out


def _cell(text: Any) -> str:
    """Page text in a Markdown table cell: no pipes, no line breaks, no HTML, nothing that links."""
    s = _SPACE.sub(" ", str(text if text is not None else "")).strip()
    for ch in "|<>[]`":
        s = s.replace(ch, " ")
    s = s.replace("://", ": //").replace("@", "@\u200b")   # no autolinked URL, no @mention that notifies
    s = re.sub(r"(?i)\bwww\.", "www .", s)
    return s[:400] or "-"


def report(cert: dict[str, Any], diff: dict[str, Any], updated: str | None) -> str:
    """The draft PR's body: what changed, the draft crosswalk, and what people do next."""
    src = cert.get("source") or {}
    lines = [f"# {cert.get('id')}: the study guide changed", "",
             (f"Compared {src.get('url')} (page last updated {updated or 'not shown'}) with the registry "
              f"(read {src.get('read')}, page updated {src.get('page_updated')})."), "",
             ("This is a draft for people to review. Nothing in the app changed: the registry, its snapshot hash, "
              "the crosswalk and everyone's results stay as they are until someone updates them by hand."), ""]

    def table(title: str, head: list[str], rows: list[list[Any]]) -> None:
        if not rows:
            return
        lines.extend([f"## {title}", "", "| " + " | ".join(head) + " |", "|" + " --- |" * len(head)])
        lines.extend("| " + " | ".join(_cell(c) for c in r) + " |" for r in rows)
        lines.append("")

    table("Domains", ["Id", "New id", "Was", "Now"], [[d["id"], d["new_id"], d["was"], d["now"]] for d in diff["domains"]])
    table("Skill areas", ["Id", "New id", "Was", "Now"], [[s["id"], s["new_id"], s["was"], s["now"]] for s in diff["skills"]])
    table("Objectives added", ["New id", "Text"], [[a["new_id"], a["text"]] for a in diff["added"]])
    table("Objectives removed", ["Id", "Text"], [[r["id"], r["text"]] for r in diff["removed"]])
    table("Objectives reworded", ["Id", "New id", "Was", "Now"], [[r["id"], r["new_id"], r["was"], r["now"]] for r in diff["reworded"]])
    table("Objectives renumbered", ["Id", "New id", "Text"], [[m["id"], m["new_id"], m["text"]] for m in diff["moved"]])
    table("Draft crosswalk", ["Old id", "New ids"], [[k, ", ".join(v) or "(removed)"] for k, v in sorted(diff["crosswalk"].items())])
    lines += ["## Next", "",
              "1. Check each row against the live page; a reworded objective may really be a new one.",
              ("2. Update certification.json: a new version, the objectives' words, source.read, source.page_updated "
               "and source.snapshot_sha256 (registry.problems() gives the new hash)."),
              "3. Add the reviewed crosswalk under the old version, so earlier evidence still counts.",
              "4. Re-check the exercises, questions and gap records of every changed objective."]
    return "\n".join(lines) + "\n"


def check(cert: dict[str, Any], html: str) -> dict[str, Any]:
    """``{changed, diff, report, updated}`` for one pack's live page."""
    page = extract(html)
    diff = compare(cert, page)
    return {"changed": diff["changed"], "diff": diff, "updated": page["updated"],
            "report": report(cert, diff, page["updated"]) if diff["changed"] else ""}


def checkable(cert: dict[str, Any]) -> bool:
    """Whether this pack's guide is a Learn page this module can read."""
    return str((cert.get("source") or {}).get("url") or "").startswith(LEARN_HOST)
