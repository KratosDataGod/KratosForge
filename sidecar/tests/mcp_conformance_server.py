"""The real local API for the MCP project's conformance suite (docs/product/ai-integration-client-checklist.md).

    python tests/mcp_conformance_server.py 47677
    npx @modelcontextprotocol/conformance@0.1.16 server --url http://127.0.0.1:47677/mcp

Starts the local API on 127.0.0.1 with a temporary data folder and one connected tool, and adds
that tool's token to requests that have none, since the suite can't send one. Everything else
(Host and Origin checks, permissions, approvals, audit) is the real server. Not a pytest module.
"""

import os
import sys
import tempfile
from pathlib import Path


def main(port: int) -> None:
    data = tempfile.mkdtemp(prefix="fd-conformance-")
    os.environ["LOCALAPPDATA"] = os.environ["ProgramData"] = data
    import uvicorn

    from fabric_desktop.integrations.audit import IntegrationAudit
    from fabric_desktop.integrations.http_server import RateLimiter, build_app
    from fabric_desktop.integrations.permissions import PermissionAuthorizer
    from fabric_desktop.integrations.registry import default_registry
    from fabric_desktop.integrations.store import IntegrationStore
    from fabric_desktop.service import Service

    service = Service(root=Path(data) / "FabricDesktop")
    store = IntegrationStore.at(service.root)
    store.set_setting("enabled", True)
    _, token = store.add_client("Conformance suite", "other-mcp", transport="http", grants=["READ_METADATA"])
    registry = default_registry()
    registry.authorizer = PermissionAuthorizer(store)
    registry.audit = IntegrationAudit(store).record
    # The suite sends requests faster than any tool, so the per-tool rate limit is raised here only.
    app = build_app(registry, store, port, lambda: service, limiter=RateLimiter(rate=1000, burst=1000))
    header = (b"authorization", f"Bearer {token}".encode())

    async def with_token(scope, receive, send):
        if scope["type"] == "http" and not any(k == b"authorization" for k, _ in scope["headers"]):
            scope = dict(scope, headers=[*scope["headers"], header])
        await app(scope, receive, send)

    print(f"Local API for the conformance suite on http://127.0.0.1:{port}/mcp (data in {data})", file=sys.stderr)
    uvicorn.run(with_token, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 47677)
