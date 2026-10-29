"""Embed Studio's engine calls (Preview, PBE-04; powerbi_embed.py does the work).

``pbi_embed_token`` hands the screen a Power BI token from the existing sign-in for the embed frame. It is
never written anywhere: not to disk, the audit trail (it reads only, so it isn't audited) or a log, and
the external AI tools can't call it (it isn't in integrations/capabilities.py).
"""

from __future__ import annotations

from typing import Any

from .auth import POWERBI_EMBED_SCOPES
from .fabric_api import FabricClient
from .powerbi_embed import EmbedClient, token_reply

METHODS = {"pbi_embed_token", "pbi_embed_content"}


class PowerBiEmbedMixin:
    def _embed_token(self) -> str:
        return self.auth._token(POWERBI_EMBED_SCOPES)  # type: ignore[attr-defined]

    def pbi_embed_token(self) -> dict[str, Any]:
        """A Power BI token for the embed frame, with when it expires. Reads only."""
        return token_reply(self._embed_token())

    def pbi_embed_content(self, workspace_id: str = "", item_id: str = "", item_type: str = "Report") -> dict[str, Any]:
        """What the embed frame needs for a report, paginated report or dashboard: its embed URL, semantic
        model, pages or tiles, and what the SDK supports for it. Reads only."""
        return EmbedClient(FabricClient(self._embed_token)).content(workspace_id, item_id, item_type)
