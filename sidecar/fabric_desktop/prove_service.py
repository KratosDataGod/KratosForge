"""Prove this notebook: what the app calls. Stage 1 has the dependency check and run receipts;
proof runs build on them (see docs/prove/plan.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import envprofile, receipts


class ProveMixin:
    root: Path

    @property
    def receipts(self) -> receipts.ReceiptStore:
        return receipts.ReceiptStore(self.root / "receipts")

    @property
    def _observed_file(self) -> Path:
        return self.root / "fabric_environment.json"

    def _fabric_observed(self) -> dict[str, dict[str, Any]]:
        """Packages earlier runs in Fabric reported: {module: {"version", "seen_at", "python"}}."""
        try:
            return json.loads(self._observed_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def notebook_environment(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                             compute: str | None = None, workspace: dict[str, str] | None = None) -> dict[str, Any]:
        """Which modules each cell imports and where each is available: this PC, Fabric. Takes
        what the notebook screen sends (notebookPayload)."""
        return envprofile.check(cells, self._fabric_observed())

    def receipts_list(self, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.receipts.list(name, limit)

    def receipt_get(self, id: str) -> dict[str, Any]:  # noqa: A002 - the app's parameter name
        return {"id": id, "receipt": self.receipts.get(id)}

    def receipt_export(self, id: str, format: str = "html", hide: list[str] | None = None) -> dict[str, Any]:  # noqa: A002
        """A readable report of a receipt, with the parts in ``hide`` left out (receipts.REDACTIONS)."""
        body = self.receipts.get(id)
        if format == "markdown":
            return {"format": "markdown", "text": receipts.to_markdown(body, id, hide or [])}
        if format == "html":
            return {"format": "html", "text": receipts.to_html(body, id, hide or [])}
        if format == "json":
            return {"format": "json", "text": receipts.canonical(body).decode("utf-8")}
        raise ValueError("format is markdown, html or json")

    def receipt_redactions(self) -> dict[str, str]:
        return dict(receipts.REDACTIONS)
