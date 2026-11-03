"""Embed Studio's engine calls (Preview, PBE-04; powerbi_embed.py does the work).

``pbi_embed_token`` hands the screen a Power BI token from the existing sign-in for the embed frame. The
calls only read (so they aren't audited), but the token is the person's Power BI access token: it carries
every delegated Power BI permission this client is granted, write permissions included, so it is the first
write-capable token the UI holds. It is never written anywhere (not to disk, the audit trail or a log), the
screen keeps it in memory and hands it only to app.powerbi.com, it expires in about an hour, and the external
AI tools can't call this (it isn't in integrations/capabilities.py).
"""

from __future__ import annotations

from typing import Any

from .auth import POWERBI_EMBED_SCOPES
from .fabric_api import FabricClient
from .powerbi_embed import EmbedClient, token_reply

EMBED_TOKEN_MIN_SECONDS = 600

METHODS = {"pbi_embed_token", "pbi_embed_content"}


class PowerBiEmbedMixin:
    def _embed_token(self) -> str:
        # At least 10 minutes left, so the frame doesn't get a token MSAL is about to replace.
        return self.auth._token(POWERBI_EMBED_SCOPES, min_seconds=EMBED_TOKEN_MIN_SECONDS)  # type: ignore[attr-defined]

    def pbi_embed_token(self) -> dict[str, Any]:
        """A Power BI token for the embed frame, with when it expires. The call only reads; the token can write."""
        return token_reply(self._embed_token())

    def pbi_embed_content(self, workspace_id: str = "", item_id: str = "", item_type: str = "Report") -> dict[str, Any]:
        """What the embed frame needs for a report, paginated report or dashboard: its embed URL, semantic
        model, pages or tiles, and what the SDK supports for it. Reads only."""
        return EmbedClient(FabricClient(self._embed_token)).content(workspace_id, item_id, item_type)
