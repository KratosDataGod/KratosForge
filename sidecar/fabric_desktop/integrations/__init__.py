"""External AI tools and other programs using KratosForge through standard interfaces.

There is no AI in here. This package lets tools people already use (any MCP client, any HTTP
client) reach the engine's existing services with the same checks for every route in. The
architecture is in docs/architecture/ai-integration.md.

    core       errors, argument checks, timeouts and cancellation shared by every transport
    services   adapters over the engine's Service: choose fields, page, never return secrets
    registry   every capability with its schemas and permission; invoke() is the one way in
    capabilities  the core capabilities, each a registry entry over a services function
"""
