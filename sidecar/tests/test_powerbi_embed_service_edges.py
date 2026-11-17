"""Embed Studio's content call: which token and which retry token it hands the Power BI client.
A fake client stands in for Power BI; nothing reaches a tenant."""

from fabric_desktop import powerbi_embed_service as pes
from fabric_desktop.auth import POWERBI_EMBED_SCOPES


class Auth:
    def __init__(self):
        self.asked = []

    def _token(self, scopes, min_seconds=0):
        self.asked.append((scopes, min_seconds))
        return "embed-tok"


class FakeEmbed:
    made = []

    def __init__(self, client):
        FakeEmbed.made.append(client)

    def content(self, workspace_id, item_id, item_type):
        return {"workspace": workspace_id, "item": item_id, "type": item_type}


def test_content_retries_a_refused_token_with_a_new_one_from_the_service(monkeypatch):
    FakeEmbed.made = []
    monkeypatch.setattr(pes, "EmbedClient", FakeEmbed)
    refreshes = []

    class Svc(pes.PowerBiEmbedMixin):
        def _refreshed(self, scopes):
            refreshes.append(scopes)
            return lambda: "fresh-tok"

    svc = Svc()
    svc.auth = Auth()
    out = svc.pbi_embed_content("ws", "rep", "Report")
    assert out == {"workspace": "ws", "item": "rep", "type": "Report"}
    assert refreshes == [POWERBI_EMBED_SCOPES]
    client = FakeEmbed.made[0]
    assert client._refresh() == "fresh-tok"
    assert client._token() == "embed-tok" and svc.auth.asked == [(POWERBI_EMBED_SCOPES, 600)]


def test_without_a_refresher_the_retry_asks_the_same_provider_again(monkeypatch):
    FakeEmbed.made = []
    monkeypatch.setattr(pes, "EmbedClient", FakeEmbed)
    svc = pes.PowerBiEmbedMixin()
    svc.auth = Auth()
    svc.pbi_embed_content("ws", "dash", "Dashboard")
    client = FakeEmbed.made[0]
    assert client._refresh() == "embed-tok"
    assert len(svc.auth.asked) == 1  # only the retry provider was called, once
