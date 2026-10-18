"""Measured CU from usage files a person imports (FINOPS-003).

No supported Fabric API gives an ordinary developer the CU their runs used, so measured usage
comes from files that someone with access exports from a Microsoft tool and hands over. This
package reads such a file, checks it, turns each row into a *measured* measurement
(measurement.py), keeps it on this PC, and reconciles it with the runs this app started.

- ``reader``: CSV text, numbers and times, without guessing a locale.
- ``providers``: what every provider gives the importer; :data:`PROVIDERS` lists them.
- ``metrics_app``: the Capacity Metrics app export (the first provider).
- ``store``: imported files on this PC, with duplicate detection.
- ``reconcile``: which imported rows belong to which of this app's runs, and what is covered.

:func:`read` is the whole import check. It writes nothing: the service previews with it, and
saves only what the person confirms.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter
from typing import Any

from .. import measurement as ms
from . import reader
from .cost_management import CostManagement
from .metrics_app import MetricsApp
from .providers import Context, Options, Provider, detect

PROVIDERS: dict[str, Provider] = {p.id: p for p in (MetricsApp(), CostManagement())}

_SAMPLE = 5


def _period(opts: Options, order: str | None, tz: dt.timezone) -> tuple[dt.datetime | None, dt.datetime | None]:
    """The period an untimed file covers. A date alone means the whole day, so an end date counts to its midnight."""
    def one(raw: str | None, end: bool) -> dt.datetime | None:
        if not raw:
            return None
        t = reader.when(raw, order or "mdy", tz)
        if t is None:
            raise reader.FileProblem(f"{raw!r} isn't a date.")
        if end and len(str(raw).strip()) <= 10:
            t += dt.timedelta(days=1)
        return t
    return one(opts.period_start, False), one(opts.period_end, True)


def _overlaps(a: tuple[Any, Any], b: tuple[Any, Any]) -> bool:
    return bool(a[0] and a[1] and b[0] and b[1] and a[0] < b[1] and b[0] < a[1])


def read(text: str, file_name: str, options: dict[str, Any] | None = None, *, known_keys: set[str] | None = None,
         imports: list[dict[str, Any]] | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    """Check a usage file and say what importing it would add.

    ``known_keys`` are rows already imported (skipped as duplicates); ``imports`` are earlier
    imports, to refuse the same file twice and overlapping item totals. The result has
    ``ready`` and, when not ready, ``problem`` (and ``needs`` when the person must choose
    something). ``rows`` are the measurements to save; everything else is the preview."""
    now = now or dt.datetime.now(dt.timezone.utc)
    known_keys = known_keys or set()
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    out: dict[str, Any] = {"file_name": file_name, "sha256": digest, "ready": False, "problem": None, "needs": None,
                           "provider": None, "layout": None, "columns": None, "rows": [], "notes": []}
    same = next((i for i in imports or [] if i.get("sha256") == digest), None)
    if same:
        return out | {"problem": f"This file was already imported on {same.get('imported_at', '')[:10]} "
                                 f"as {same.get('file_name')!r}.", "duplicate_of": same.get("id")}
    try:
        headers, rows = reader.table(text)
    except reader.FileProblem as exc:
        return out | {"problem": str(exc)}
    provider, fit, why = detect(headers)
    if fit:
        out["columns"] = {"used": {f: headers[i] for f, i in fit["index"].items()}, "ignored": fit["ignored"],
                          "not_stored": fit["private"], "missing": fit["missing"]}
    if provider is None or fit is None:
        return out | {"problem": why}
    layout = fit["layout"]
    opts = Options.from_dict(options)
    out |= {"provider": {"id": provider.id, "label": provider.label}, "layout": {"id": layout.id, "label": layout.label,
                                                                               "timed": layout.timed}}
    cells = reader.cells(headers, rows, fit["index"])
    try:
        tz = reader.zone(opts.tz)
        decimal = opts.decimal or reader.decimal_mark([c.get(f, "") for c in cells for f in layout.numbers])
        order = opts.date_order or (reader.date_order([c.get(f, "") for c in cells for f in layout.times])
                                    if layout.timed else None)
        period = _period(opts, opts.date_order, tz) if not layout.timed else (None, None)
    except reader.NeedsChoice as exc:
        return out | {"problem": str(exc), "needs": {"option": exc.option, "choices": exc.choices}}
    except reader.FileProblem as exc:
        return out | {"problem": str(exc)}
    if not layout.timed and not (period[0] and period[1]):
        return out | {"problem": layout.period_hint or "Enter the period this file covers.",
                      "needs": {"option": "period", "choices": {}}}
    if period[0] and period[1] and period[1] <= period[0]:
        return out | {"problem": "The period ends before it starts."}
    ctx = Context(file_name=file_name, imported_at=now, options=opts, tz=tz, decimal=decimal, date_order=order,
                  period=period)
    kept: list[dict[str, Any]] = []
    skipped: dict[str, list[int]] = {}
    seen: set[str] = set()
    in_file = already = 0
    for line, c in enumerate(cells, start=2):
        r = provider.row(c, layout, ctx)
        if isinstance(r, str):
            skipped.setdefault(r, []).append(line)
            continue
        if r["key"] in seen:
            in_file += 1
            continue
        seen.add(r["key"])
        if r["key"] in known_keys:
            already += 1
            continue
        kept.append(r | {"line": line})
    workspaces = sorted({r["workspace"] for r in kept if r.get("workspace")})
    capacities = sorted({r["capacity"] for r in kept if r.get("capacity")})
    if not layout.timed:
        for i in imports or []:
            if i.get("layout") == layout.id and i.get("provider") == provider.id \
                    and set(i.get("workspaces") or []) & set(workspaces) \
                    and _overlaps(period, (ms.parse_time(i["period"]["start"]), ms.parse_time(i["period"]["end"]))):
                return out | {"problem": f"Its period overlaps {i.get('file_name')!r}, imported on "
                                         f"{i.get('imported_at', '')[:10]}, for the same workspaces, so the same use would "
                                         "count twice. Remove that import first, or export a period that doesn't overlap."}
    periods = [measure(r)["period"] for r in kept]
    starts = [ms.parse_time(p["start"]) for p in periods if p["start"]]
    ends = [ms.parse_time(p["end"]) for p in periods if p["end"]]
    cu_rows = [r["cu"] for r in kept if r.get("cu")]
    total = ms.total(cu_rows, "fabric_capacity_cu", ms.CU_HOURS) if cu_rows else None
    billing = Counter({True: "billable", False: "non_billable", None: "not_stated"}[r.get("billable")] for r in kept)
    notes = [f"Times without a time zone are read as {opts.tz or 'UTC'}: the export doesn't say which zone it uses."
             if layout.timed else "Each row is a total for the whole period you entered."]
    if fit["private"]:
        why = {reader.normal(k): v for k, v in provider.private.items()}
        notes += [f"Not stored: the {h} column. {why.get(reader.normal(h), '')}".strip() for h in fit["private"]]
    if decimal:
        notes.append(f"Numbers read with {'a point' if decimal == '.' else 'a comma'} as the decimal mark.")
    if order:
        notes.append(f"Dates read {'month' if order == 'mdy' else 'day'} first.")
    return out | {
        "ready": bool(kept), "problem": None if kept else (
            "Every row in this file that can be imported is already imported." if already else "No row in this file can be imported."),
        "rows": kept, "notes": notes,
        "options": {"tz": opts.tz, "decimal": decimal, "date_order": order,
                    "period_start": reader.iso(period[0]), "period_end": reader.iso(period[1])},
        "rows_read": len(cells), "to_import": len(kept), "duplicates_in_file": in_file, "already_imported": already,
        "skipped": [{"reason": k, "count": len(v), "lines": v[:_SAMPLE]} for k, v in
                    sorted(skipped.items(), key=lambda kv: -len(kv[1]))],
        "period": {"start": reader.iso(min(starts)) if starts else None, "end": reader.iso(max(ends)) if ends else None},
        "workspaces": workspaces, "capacities": capacities,
        "items": len({(r.get("workspace"), r.get("item")) for r in kept if r.get("item")}),
        "billing": dict(billing), "total": total, "cost": cost_totals([r["cost"] for r in kept if r.get("cost")]),
        "sample": [{k: r.get(k) for k in ("workspace", "item", "item_kind", "operation", "capacity", "meter", "start", "end")}
                   | {"cu": (r.get("cu") or {}).get("value"), "cost": (r.get("cost") or {}).get("value")}
                   for r in kept[:_SAMPLE]]}


def measure(row: dict[str, Any]) -> dict[str, Any]:
    """The row's main measurement: its CU when it has one, otherwise its cost."""
    return row.get("cu") or row["cost"]


def cost_totals(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Money totals, one per currency: amounts in different currencies are never added."""
    by: dict[str, list[dict[str, Any]]] = {}
    for p in parts:
        by.setdefault(p["currency"], []).append(p)
    return [ms.total(v, "billed_cost", ms.CURRENCY, currency=c) for c, v in sorted(by.items())]
