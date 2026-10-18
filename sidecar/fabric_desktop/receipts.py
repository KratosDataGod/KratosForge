"""Run receipts: an immutable record of what a run was, that someone else can check and reproduce.

A receipt is a JSON document: the notebook's code revision (each cell by its stable id and the
hash of its source), the parameters, the inputs (Delta table versions, file hashes), the
environment on each side, who ran it, the outputs and how they were verified, and the
measurements with their assumptions. Its id is the SHA-256 of its canonical JSON, so changing
any byte gives a different id: a receipt can be checked by anyone who has it, without trusting
the PC it came from.

The store writes each receipt once. Writing the same receipt again is a no-op; writing different
content under an id that exists is refused. Reading checks the content still matches its id.

Exports (Markdown, HTML) are rendered from a receipt with redaction chosen at export time: the
receipt itself keeps everything, the export leaves out what the person chose to hide.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "fabric-desktop/run-receipt@1"
_ID = re.compile(r"^[0-9a-f]{64}$")


class ReceiptError(Exception):
    """A receipt that is malformed, tampered with, or conflicts with one already stored."""


# ---- identity of content


def canonical(body: dict[str, Any]) -> bytes:
    """The bytes a receipt is hashed over: sorted keys, no whitespace, UTF-8, no NaN or infinity."""
    def check(v: Any) -> None:
        if isinstance(v, float) and not math.isfinite(v):
            raise ReceiptError("A receipt can't hold NaN or infinity; record them as null with a note.")
        if isinstance(v, dict):
            for k, x in v.items():
                if not isinstance(k, str):
                    raise ReceiptError("Receipt keys must be text.")
                check(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                check(x)
    check(body)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def receipt_id(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def code_revision(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """The notebook's code as a receipt names it: each cell's id, kind and source hash, and one
    hash over all of them in order. Two notebooks with the same revision ran the same code."""
    rows = [{"id": c.get("cell_id") or f"#{i + 1}", "type": c.get("type") or "code",
             "sha256": hashlib.sha256((c.get("source") or "").encode("utf-8")).hexdigest()}
            for i, c in enumerate(cells)]
    whole = hashlib.sha256(json.dumps([[r["id"], r["type"], r["sha256"]] for r in rows]).encode()).hexdigest()
    return {"revision": whole, "cells": rows}


def new_receipt(kind: str, **sections: Any) -> dict[str, Any]:
    """A receipt body: ``kind`` ("proof", "run") and its sections. ``created_at`` is added."""
    return {"schema": SCHEMA, "kind": kind,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **sections}


# ---- the store


class ReceiptStore:
    """Receipts as ``<folder>/<id>.json``, written once."""

    def __init__(self, folder: Path):
        self.folder = folder

    def _path(self, rid: str) -> Path:
        if not _ID.match(rid or ""):
            raise ReceiptError(f"{rid!r} isn't a receipt id (64 hex characters).")
        return self.folder / f"{rid}.json"

    def put(self, body: dict[str, Any]) -> str:
        if body.get("schema") != SCHEMA:
            raise ReceiptError(f"Not a run receipt: schema is {body.get('schema')!r}.")
        data = canonical(body)
        rid = hashlib.sha256(data).hexdigest()
        path = self._path(rid)
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as f:  # create only: never replace a receipt
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            if path.read_bytes() != data:
                raise ReceiptError(f"Receipt {rid[:12]} already exists with different content; it is left as it was.") from None
        return rid

    def get(self, rid: str) -> dict[str, Any]:
        path = self._path(rid)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise LookupError(f"No receipt {rid[:12]}") from None
        if hashlib.sha256(data).hexdigest() != rid:
            raise ReceiptError(f"Receipt {rid[:12]} has been changed since it was written; it can't be trusted.")
        return json.loads(data)

    def verify(self, body: dict[str, Any], rid: str) -> bool:
        """Whether ``body`` (from anywhere: an export, an email) is the receipt ``rid``."""
        return receipt_id(body) == rid

    def list(self, notebook: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Summaries, newest first. Receipts that fail their check are listed as such, not hidden."""
        if not self.folder.exists():
            return []
        rows = []
        for path in self.folder.glob("*.json"):
            rid = path.stem
            try:
                r = self.get(rid)
            except (ReceiptError, ValueError) as exc:
                rows.append({"id": rid, "valid": False, "problem": str(exc), "created_at": ""})
                continue
            name = (r.get("notebook") or {}).get("name")
            if notebook and name != notebook:
                continue
            rows.append({"id": rid, "valid": True, "kind": r.get("kind"), "created_at": r.get("created_at", ""),
                         "notebook": name, "verdict": (r.get("verification") or {}).get("verdict")})
        return sorted(rows, key=lambda x: x.get("created_at") or "", reverse=True)[:limit]


# ---- exports

# What a person can choose to leave out of an export.
REDACTIONS = {
    "identity": "Who ran it (user and tenant)",
    "locations": "Workspace and lakehouse names and ids",
    "values": "Row values shown as examples of differences",
    "machine": "This PC's name and hardware",
}


def redact(receipt: dict[str, Any], hide: Iterable[str]) -> dict[str, Any]:
    """A copy of the receipt with the chosen parts replaced by "hidden". The id is not changed:
    an export says which receipt it came from, and that it was redacted."""
    hide = set(hide)
    unknown = hide - set(REDACTIONS)
    if unknown:
        raise ValueError(f"Can't redact {', '.join(sorted(unknown))}. Choose from {', '.join(REDACTIONS)}.")
    out = json.loads(json.dumps(receipt))

    def walk(v: Any, path: tuple[str, ...]) -> Any:
        if isinstance(v, dict):
            res = {}
            for k, x in v.items():
                if "identity" in hide and k == "identity":
                    res[k] = "hidden"
                elif "locations" in hide and k in ("workspace_id", "workspace_name", "lakehouse_id", "lakehouse_name",
                                                   "workspace", "uri", "url", "item_id"):
                    res[k] = "hidden"
                elif "values" in hide and k in ("examples", "sample", "rows_sample"):
                    res[k] = "hidden"
                elif "machine" in hide and k in ("machine", "host"):
                    res[k] = "hidden"
                else:
                    res[k] = walk(x, path + (k,))
            return res
        if isinstance(v, list):
            return [walk(x, path) for x in v]
        return v
    return walk(out, ())


def _fmt(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:,.3f}".rstrip("0").rstrip(".") if abs(v) < 1e15 else f"{v:.3e}"
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def to_markdown(receipt: dict[str, Any], rid: str, hide: Iterable[str] = ()) -> str:
    """A readable report of a receipt, for someone who wasn't there."""
    hide = sorted(set(hide))
    r = redact(receipt, hide)
    nb = r.get("notebook") or {}
    v = r.get("verification") or {}
    lines = [f"# {nb.get('name') or 'Notebook'}: {r.get('kind', 'run')} receipt", "",
             f"Receipt `{rid}`, created {r.get('created_at', '')}.",
             f"Check it: the SHA-256 of the receipt's canonical JSON is its id ({SCHEMA}).", ""]
    if hide:
        lines += [f"Redacted in this export: {', '.join(REDACTIONS[h].lower() for h in hide)}.", ""]
    if v:
        lines += ["## Result", "", f"**{v.get('text') or v.get('verdict', '')}**", ""]
        if v.get("level"):
            lines += [f"Verification: {v['level']}.", ""]
    tables = r.get("outputs") or []
    if tables:
        lines += ["## Outputs", "", "| Table | Result | Level | Rows (PC) | Rows (Fabric) | Note |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for t in tables:
            lines.append(f"| {t.get('table', '')} | {t.get('result', '')} | {t.get('level', '')} | "
                         f"{_fmt((t.get('pc') or {}).get('rows'))} | {_fmt((t.get('fabric') or {}).get('rows'))} | "
                         f"{(t.get('note') or '').replace('|', '/')} |")
        lines.append("")
    inputs = r.get("inputs") or {}
    if inputs.get("tables") or inputs.get("files"):
        lines += ["## Inputs", ""]
        for t in inputs.get("tables", []):
            lines.append(f"- Table `{t.get('name')}` at Delta version {t.get('version')}")
        for f in inputs.get("files", []):
            lines.append(f"- File `{f.get('path')}` SHA-256 `{(f.get('sha256') or '')[:16]}…`")
        lines.append("")
    runs = r.get("runs") or {}
    if runs:
        lines += ["## Measurements", "", "| | Duration | Detail |", "| --- | --- | --- |"]
        for side, label in (("pc", "This PC"), ("fabric", "Fabric")):
            s = runs.get(side)
            if s:
                detail = "; ".join(f"{k.replace('_', ' ')} {_fmt(x)}" for k, x in s.items()
                                   if k not in ("status", "seconds") and not isinstance(x, (dict, list)))
                lines.append(f"| {label} | {_fmt(s.get('seconds'))} s ({s.get('status', '')}) | {detail} |")
        lines.append("")
    if r.get("cost"):
        c = r["cost"]
        lines += ["## Cost", "", f"{c.get('text', '')}", ""]
        for side, label in (("pc", "This PC"), ("fabric", "Fabric")):
            m = (c.get(side) or {}).get("measurement") if isinstance(c.get(side), dict) else None
            if m:
                value = "not known" if m.get("value") is None else f"{_fmt(m['value'])} CU-seconds"
                lines.append(f"- {label}: Fabric job CU {value} (**{m.get('label') or m.get('classification')}**"
                             + (f"; {m['reason']}" if m.get("reason") else "") + ")")
        for a in c.get("assumptions", []):
            lines.append(f"- {a}")
        lines.append("")
    env = r.get("environment") or {}
    if env:
        lines += ["## Environment", ""]
        for side, e in env.items():
            if isinstance(e, dict):
                pk = e.get("packages") or {}
                shown = ", ".join(f"{k} {pk[k]}" for k in sorted(pk)[:12])
                lines.append(f"- {side}: Python {e.get('python', '?')}{'; ' + shown if shown else ''}")
        lines.append("")
    code = nb.get("code") or {}
    if code:
        lines += ["## Code", "", f"Revision `{code.get('revision', '')[:16]}…`, {len(code.get('cells', []))} cells.", ""]
    if nb.get("parameters"):
        lines += ["Parameters:", "", "```json", json.dumps(nb["parameters"], indent=2, sort_keys=True), "```", ""]
    limits = r.get("limitations") or []
    if limits:
        lines += ["## Limitations", ""] + [f"- {x}" for x in limits] + [""]
    return "\n".join(lines)


def to_html(receipt: dict[str, Any], rid: str, hide: Iterable[str] = ()) -> str:
    """The Markdown report as a self-contained HTML page (no scripts, no external files)."""
    md = to_markdown(receipt, rid, hide)
    body, in_table, in_code = [], False, False
    for line in md.splitlines():
        if line.startswith("```"):
            body.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(line))
            continue
        if line.startswith("| ") and set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        if line.startswith("| "):
            cells = [html.escape(c.strip()) for c in line.strip("|").split("|")]
            tag = "th" if not in_table else "td"
            if not in_table:
                body.append("<table>")
                in_table = True
            body.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("# "):
            body.append(f"<h1>{_inline(html.escape(line[2:]))}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{_inline(html.escape(line[3:]))}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{_inline(html.escape(line[2:]))}</li>")
        elif line.strip():
            body.append(f"<p>{_inline(html.escape(line))}</p>")
    if in_table:
        body.append("</table>")
    title = html.escape(((receipt.get("notebook") or {}).get("name") or "Notebook") + " receipt")
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>" + title + "</title><style>"
            "body{font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 16px;color:#1b1b1b}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
            "code,pre{font-family:Consolas,monospace;background:#f4f4f4}h1{color:#1b3664}"
            "</style></head><body>" + "\n".join(body) + "</body></html>")


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", text)
