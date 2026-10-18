"""What every usage provider gives the importer, and the list of providers.

A provider knows one kind of exported file: which columns it has (as the source documents them),
which are needed, and how one row becomes a :class:`fabric_desktop.measurement` measurement. The
importer, the local store and reconciliation know nothing about any particular source.

Adding a provider (the Azure Cost Management export is next) means adding a module with a
``Provider`` and listing it in :data:`PROVIDERS`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, ClassVar

from . import reader


@dataclass(frozen=True)
class Layout:
    """One shape of a provider's file, such as one row per operation or one row per item."""

    id: str
    label: str
    # field -> column names the source uses for it (compared with reader.normal)
    columns: dict[str, tuple[str, ...]]
    required: tuple[str, ...]
    # True when each row has its own start and end; False when the rows are totals over a period
    # the file doesn't state, so the person gives it.
    timed: bool
    # Columns that must not be the CU column, with why (shown when they are the only CU column).
    refuse: dict[str, str] = field(default_factory=dict)
    # Fields holding numbers (their decimal mark is worked out together) and times (their date order).
    numbers: tuple[str, ...] = ("cu", "duration")
    times: tuple[str, ...] = ("start", "end")
    # What a row describes: "operation", "item" or "capacity" (a whole capacity, as billed).
    level: str = "operation"
    # What the person enters when the file has no times of its own.
    period_hint: str = ""


@dataclass
class Options:
    """What the person chose for things the file can't settle on its own."""

    tz: str = "UTC"
    decimal: str | None = None  # "." or ","; None works it out from the file
    date_order: str | None = None  # "mdy" or "dmy"; None works it out
    period_start: str | None = None  # the period an untimed file covers (dates or times)
    period_end: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Options:
        d = d or {}
        pick = lambda k, ok: d.get(k) if d.get(k) in ok else None
        return cls(tz=str(d.get("tz") or "UTC"), decimal=pick("decimal", (".", ",")),
                   date_order=pick("date_order", ("mdy", "dmy")),
                   period_start=d.get("period_start") or None, period_end=d.get("period_end") or None)


@dataclass
class Context:
    """What a provider needs to turn rows into measurements."""

    file_name: str
    imported_at: dt.datetime
    options: Options
    tz: dt.timezone
    decimal: str | None
    date_order: str | None
    period: tuple[dt.datetime | None, dt.datetime | None] = (None, None)


class Provider:
    id = ""
    label = ""
    layouts: tuple[Layout, ...] = ()
    # Columns read but never stored, with why (for example, people's names).
    private: ClassVar[dict[str, str]] = {}
    about = ""

    def source(self, ctx: Context) -> str:
        return f"{self.label} ({ctx.file_name})"

    def row(self, cells: dict[str, str], layout: Layout, ctx: Context) -> dict[str, Any] | str:
        """A canonical usage row, or the reason the row is skipped."""
        raise NotImplementedError


def match(provider: Provider, headers: list[str]) -> dict[str, Any]:
    """How well a file's headers fit each of a provider's layouts; the best one first.

    A layout fits when every required field has a column. Columns the provider doesn't use are
    listed, so the person sees what was left out."""
    norm = {reader.normal(h): i for i, h in enumerate(headers) if h}
    best: dict[str, Any] | None = None
    for layout in provider.layouts:
        index: dict[str, int] = {}
        for f, names in layout.columns.items():
            for name in names:
                if reader.normal(name) in norm:
                    index[f] = norm[reader.normal(name)]
                    break
        missing = [f for f in layout.required if f not in index]
        refused = {headers[norm[reader.normal(c)]]: why for c, why in layout.refuse.items() if reader.normal(c) in norm}
        score = (not missing, len(index))
        if best is None or score > best["score"]:
            used = set(index.values())
            best = {"layout": layout, "index": index, "missing": missing, "score": score, "refused": refused,
                    "private": [h for h in headers if reader.normal(h) in {reader.normal(p) for p in provider.private}],
                    "ignored": [h for i, h in enumerate(headers) if h and i not in used]}
    assert best is not None
    return best


def detect(headers: list[str]) -> tuple[Provider | None, dict[str, Any] | None, str]:
    """The provider and layout a file fits, or why none does."""
    from . import PROVIDERS

    fits = [(p, match(p, headers)) for p in PROVIDERS.values()]
    full = [(p, m) for p, m in fits if not m["missing"]]
    if full:
        p, m = max(full, key=lambda pm: pm[1]["score"])
        return p, m, ""
    p, m = max(fits, key=lambda pm: pm[1]["score"])
    if not m["index"]:
        known = "; ".join(f"{p.label}: {p.about}" for p in PROVIDERS.values())
        return None, None, f"The file's columns don't match a supported export. Supported: {known}"
    labels = {f: m["layout"].columns[f][0] for f in m["missing"]}
    why = f"This looks like a {p.label} export, but it has no {', '.join(repr(v) for v in labels.values())} column."
    if m["refused"]:
        why += " " + " ".join(m["refused"].values())
    return None, m, why
