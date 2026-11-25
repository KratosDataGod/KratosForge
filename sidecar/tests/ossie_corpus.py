"""The Ossie fixture corpus (OSSIE-012a): small synthetic files under ``fixtures/ossie_corpus`` and what each
entry point does with them.

``manifest.json`` lists every file once with its ``kind`` (``ossie``, ``view``, ``tmsl`` or ``tmdl``), the
outcome expected from each entry point that reads that kind (``expect``) and ``notes`` on what the file shows.
``outcomes`` runs a file through every such entry point and sums each up in a word, so a test can compare it
with the manifest and ``tools/ossie_upgrade_diff.py`` can compare two converters. Synthetic data only.

The words: ``ok`` (it ran), ``refused`` (a plain ValueError), ``error:<Type>`` (anything else, never expected);
``validate`` gives the profile check's status; ``round_trip`` ``same``, ``differs`` or ``not run``;
``semantic_view`` ``valid`` or ``invalid`` (the files' own check); ``parity_l1`` the report's summary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fabric_desktop import model_ossie as mo
from fabric_desktop import ossie_validate, semantic_parity, semantic_view, tmdl

CORPUS = Path(__file__).resolve().parent / "fixtures" / "ossie_corpus"
MANIFEST = CORPUS / "manifest.json"
KINDS = ("ossie", "view", "tmsl", "tmdl")
# The entry points each kind goes through, in the order they run.
ENTRY_POINTS = {
    "ossie": ("to_tmsl", "validate", "round_trip", "tmdl", "semantic_view", "parity_l1"),
    "view": ("to_tmsl", "validate", "round_trip", "tmdl", "semantic_view", "parity_l1"),
    "tmsl": ("export", "round_trip", "tmdl", "semantic_view", "parity_l1"),
    "tmdl": ("export", "round_trip", "tmdl", "semantic_view", "parity_l1"),
}
REFUSED = "refused"
OK = "ok"


def manifest() -> list[dict[str, Any]]:
    """The manifest's entries, in file order."""
    return list(json.loads(MANIFEST.read_text(encoding="utf-8"))["files"])


def entry(path: str) -> dict[str, Any]:
    """The manifest entry of one file (its path under the corpus)."""
    return next(e for e in manifest() if e["path"] == path)


def corpus_files() -> list[str]:
    """Every fixture in the corpus as the manifest names it: a file, or a ``.SemanticModel`` folder for TMDL."""
    out = set()
    for p in CORPUS.rglob("*"):
        if not p.is_file() or p == MANIFEST:
            continue
        rel = p.relative_to(CORPUS).as_posix()
        folder = next((part for part in p.relative_to(CORPUS).parts if part.endswith(".SemanticModel")), None)
        out.add(rel[:rel.index(folder) + len(folder)] if folder else rel)
    return sorted(out)


def load(e: dict[str, Any]) -> Any:
    """A file as its entry points take it: text for Ossie and views, the parsed ``model.bim`` for TMSL, and
    path -> text for a TMDL folder."""
    path = CORPUS / e["path"]
    if e["kind"] == "tmdl":
        return {p.relative_to(path).as_posix(): p.read_text(encoding="utf-8") for p in sorted(path.rglob("*.tmdl"))}
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if e["kind"] == "tmsl" else text


def _word(exc: BaseException) -> str:
    return REFUSED if isinstance(exc, ValueError) else f"error:{type(exc).__name__}"


def _bim(e: dict[str, Any], value: Any) -> dict[str, Any]:
    """The model a TMSL or TMDL entry stands for."""
    return value if e["kind"] == "tmsl" else tmdl.to_bim(value, Path(e["path"]).stem.split(".")[0])[0]


def run(e: dict[str, Any], point: str, value: Any) -> tuple[str, dict[str, Any]]:
    """One entry point over one file: its word and what it gave (``message`` for a refusal, ``fidelity`` as
    object -> class for a conversion)."""
    try:
        return _run(e, point, value)
    except Exception as exc:  # noqa: BLE001 - the outcome is the point; any exception is summed up as a word
        return _word(exc), {"message": str(exc), "type": type(exc).__name__}


def _classes(fid: dict[str, Any]) -> dict[str, str]:
    return {o["object"]: o["class"] for o in fid.get("objects") or []}


def _run(e: dict[str, Any], point: str, value: Any) -> tuple[str, dict[str, Any]]:
    ossie = e["kind"] in ("ossie", "view")
    if point == "to_tmsl":
        doc = mo.parse(value)
        bim, report = mo.to_tmsl(doc)
        fid = mo.safe_fidelity(doc, bim, report)
        return OK, {"fidelity": _classes(fid), "status": fid["status"], "report": len(report)}
    if point == "export":
        out = mo.export(_bim(e, value), translate_dax=False, validate=True)
        return OK, {"fidelity": _classes(out["fidelity"]), "status": out["fidelity"]["status"],
                    "report": len(out["report"])}
    if point == "validate":
        result = ossie_validate.validate(value, "fabric-semantic-view" if e["kind"] == "view" else None)
        return result["status"], {"rules": sorted({d["rule"] for d in result["diagnostics"]})}
    if point == "round_trip":
        bim = mo.to_tmsl(mo.parse(value))[0] if ossie else _bim(e, value)
        check = mo.round_trip_check(bim)
        word = ("same" if check["passed"] else "differs") if check["ran"] else "not run"
        return word, {"rows": [r["object"] for r in check["rows"]], "error": check["error"]}
    if point == "tmdl":
        if ossie:
            files, report = mo.ossie_to_tmdl(value)
        elif e["kind"] == "tmsl":
            files, report = tmdl.from_bim(value)
        else:
            bim, report = tmdl.to_bim(value, "corpus")
            files = tmdl.from_bim(bim)[0]
        return OK, {"files": sorted(files), "report": [r["object"] for r in report]}
    if point == "semantic_view":
        source = mo.parse(value) if ossie else value
        out = semantic_view.semantic_view(source, view_name="corpus_view")
        return ("valid" if out["report"]["valid"] else "invalid"), {"files": sorted(out["files"]),
                                                                   "counts": out["report"]["counts"]}
    if point == "parity_l1":
        kind = {"view": "semantic_view", "ossie": "ossie", "tmsl": "tmsl", "tmdl": "tmdl"}[e["kind"]]
        parity = semantic_parity.parity_l1(value, value, left_kind=kind, right_kind=kind)
        return parity["summary"], {"error": parity["error"]}
    raise ValueError(f"no entry point {point!r}")


def outcomes(e: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Every entry point the file's kind goes through: point -> (word, detail)."""
    value = load(e)
    return {point: run(e, point, value) for point in ENTRY_POINTS[e["kind"]]}
