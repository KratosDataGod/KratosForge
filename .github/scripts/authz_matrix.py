"""Write docs/audits/AUTHORIZATION_MATRIX.md: every state-changing engine method and local API
capability, what it changes and which check guards it, with file and line.

Run from sidecar/:  python ../.github/scripts/authz_matrix.py > ../docs/audits/AUTHORIZATION_MATRIX.md

The tables are found by reading the code, not by running it, so they are a map for review: a row
says where to look, and a reviewer reads the method before relying on it.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from fabric_desktop import audit, server  # noqa: E402
from fabric_desktop.central import core  # noqa: E402
from fabric_desktop.integrations import capabilities, permissions, registry  # noqa: E402
from fabric_desktop.service import Service  # noqa: E402

# Platform operations that change something with no role check, on purpose (also in test_authz_matrix.py).
OPEN_BY_DESIGN = {
    "call_once": "runs another change once; that change makes its own check",
    "claim_exception_warning": "records that a notice went, on the exception's own row",
    "ensure_policy_schema": "adds missing tables and columns; changes no row",
    "ensure_schema": "adds missing tables and columns; changes no row",
    "ensure_template_schema": "adds missing tables and columns; changes no row",
    "follow_product": "the caller's own follow",
    "unfollow_product": "the caller's own follow",
    "log_workload_action": "appends to the caller's own action log",
    "note_seen": "the caller's own last-seen date",
    "request": "anyone may ask; deciding is checked (decide_request)",
}
GUARD_WORDS = ("require", "is_admin", "_product_owners", "_shortcut_changer", "_can_", "_may_", "_require_", "owner",
               "author")

_mods: dict[str, tuple[str, dict[str, ast.FunctionDef]]] = {}


def _module(path: str) -> tuple[str, dict[str, ast.FunctionDef]]:
    if path not in _mods:
        src = Path(path).read_text(encoding="utf-8")
        _mods[path] = (src, {n.name: n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)})
    return _mods[path]


def _calls(fn: ast.AST) -> set[str]:
    out = set()
    for c in ast.walk(fn):
        if isinstance(c, ast.Call):
            out.add(c.func.id if isinstance(c.func, ast.Name) else c.func.attr if isinstance(c.func, ast.Attribute) else "")
    return out


def _guards(path: str, name: str, depth: int = 0, seen: set | None = None) -> set[str]:
    seen = set() if seen is None else seen
    src, fns = _module(path)
    if (path, name) in seen or name not in fns or depth > 3:
        return set()
    seen.add((path, name))
    out = set()
    for c in _calls(fns[name]):
        if any(w in c for w in GUARD_WORDS) and c not in ("_canon",):
            out.add(c)
        if c in fns:
            out |= _guards(path, c, depth + 1, seen)
    return out


def _rel(path: str) -> str:
    return "sidecar/fabric_desktop/" + path.split("fabric_desktop/", 1)[1]


def platform_ops() -> list[tuple[str, str, str]]:
    rows = []
    for op, fn in sorted(core.OPERATIONS.items()):
        if op in core.READ_OPERATIONS:
            continue
        fn = inspect.unwrap(fn)
        path = inspect.getsourcefile(fn) or ""
        line = _module(path)[1][fn.__name__].lineno
        g = sorted(_guards(path, fn.__name__))
        rows.append((op, f"{_rel(path)}:{line}", ", ".join(g) if g else f"none: {OPEN_BY_DESIGN.get(op, 'NOT LISTED')}"))
    return rows


_OPS = re.compile(r'(?:\.call|call_or_repair\([^,]+,|_zone_call|_sc_risk_call)\(?\s*"([a-z_]+)"')
_MARKS = {
    "platform admin (in app)": r"_require_platform_admin|whoami\"\)\.get\(\"admin",
    "confirm or approve argument": r"\bconfirm\b|\bapprove\b|confirm_name|\btyped\b",
    "organization policy": r"_platform_source\(\) == \"policy\"|app_settings\.value\(\"allow_|policy_locked|source\"\] == \"policy\"",
    "risk check": r"risk\.BLOCKED|_risk_ack_write|risk_id",
    "promote gates": r"_duties_gate|governance_promote_gate|promote_checks",
    "PermissionError": r"raise PermissionError",
}
_OTHER = r"databricks|snowflake|\.push\(|bundles\.deploy|remote_url"
_RUNS = r"Engine\(|_run_here|run_pipeline|self\.kernel|kernel\.|executor\.|subprocess|Popen"
_FABRIC = r"self\.fabric\b|_scoped\(|FabricClient\(|fabric_jobs|FileStore\(|_fabric_admin|OneLakeStore|storage_token|self\._store\("


def _source(name: str) -> tuple[str | None, str]:
    attr = inspect.getattr_static(Service, name, None)
    if attr is None:
        return None, ""
    fn = attr.fget if isinstance(attr, property) else getattr(attr, "__func__", attr)
    try:
        fn = inspect.unwrap(fn)
        return textwrap.dedent(inspect.getsource(fn)), f"{_rel(inspect.getsourcefile(fn) or '')}:{inspect.getsourcelines(fn)[1]}"
    except (TypeError, OSError):
        return None, ""


def _scan(name: str, depth: int = 0, seen: set | None = None) -> tuple[set[str], set[str], set[str]]:
    seen = set() if seen is None else seen
    if name in seen or depth > 2:
        return set(), set(), set()
    seen.add(name)
    src, _ = _source(name)
    if src is None:
        return set(), set(), set()
    ops, marks = set(_OPS.findall(src)), {k for k, p in _MARKS.items() if re.search(p, src)}
    reach = {k for k, p in (("fabric", _FABRIC), ("other", _OTHER), ("runs", _RUNS)) if re.search(p, src)}
    for callee in set(re.findall(r"self\.(_?[a-z][a-z0-9_]*)\(", src)):
        o, m, r = _scan(callee, depth + 1, seen)
        ops, marks, reach = ops | o, marks | m, reach | r
    return ops, marks, reach


def rpc_rows() -> list[tuple[str, str, str, str]]:
    writes = sorted(m for m in server.METHODS if m not in audit.READ_ONLY)
    reads = set(core.READ_OPERATIONS)
    rows = []
    for m in writes:
        _, where = _source(m)
        ops, marks, reach = _scan(m)
        changes = [o for o in sorted(ops) if o in core.OPERATIONS and o not in reads]
        what = []
        if changes:
            what.append("platform: " + ", ".join(changes))
        if "fabric" in reach:
            what.append("Fabric or OneLake")
        if "other" in reach:
            what.append("Databricks, Snowflake or a Git remote")
        if "runs" in reach:
            what.append("runs code or a pipeline on this PC")
        what = what or ["this PC"]
        guard = []
        if changes:
            guard.append("platform's check per operation")
        if "fabric" in reach:
            guard.append("Fabric's own rights")
        if "other" in reach:
            guard.append("that service's own rights")
        if "runs" in reach and not ("fabric" in reach or "other" in reach):
            guard.append("what the code reaches is checked where it lands")
        guard += sorted(marks)
        if not guard:
            guard.append("the signed-in Windows user's own files")
        rows.append((m, where, "; ".join(what), "; ".join(guard)))
    return rows


def api_rows() -> list[tuple[str, ...]]:
    reg = registry.Registry()
    capabilities.register_core(reg)
    rows = []
    for c in sorted(reg.all(), key=lambda c: c.id):
        if c.read_only:
            continue
        try:
            where = f"{_rel(inspect.getsourcefile(c.handler) or '')}:{inspect.getsourcelines(c.handler)[1]}"
        except (TypeError, OSError):
            where = ""
        offered = "yes" if c.permission in permissions.OFFERED else "no (refused)"
        rows.append((c.id, where, c.permission, offered, c.approval, "yes" if c.destructive else ""))
    return rows


def table(head: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(f"`{v}`" if i in (0, 1) and v else v for i, v in enumerate(r)) + " |" for r in rows]
    return "\n".join(out)


def main() -> None:
    ops, rpc, api = platform_ops(), rpc_rows(), api_rows()
    print(Path(__file__).with_name("authz_matrix_intro.md").read_text(encoding="utf-8").rstrip() + "\n")
    print(f"## 1. Platform operations that change something ({len(ops)})\n")
    print("Checked inside the platform (central/core.py, central/policy.py), for every caller: this app, "
          "another PC, or a call straight to UDF_Platform. The check column names the functions the operation "
          "calls to decide who may make the change.\n")
    print(table(("Operation", "Where", "Check"), ops) + "\n")
    print(f"## 2. Local API and MCP capabilities that change something ({len(api)})\n")
    print("Every call passes PermissionAuthorizer.check (integrations/permissions.py) before its handler: "
          "external tools on, a connected tool, the permission offered and granted, the tool's workspaces, "
          "then the person's approval when the capability asks for it.\n")
    print(table(("Capability", "Handler", "Permission", "Offered", "Approval", "Destructive"), api) + "\n")
    print(f"## 3. Engine methods that change something ({len(rpc)})\n")
    print("Reached only by the app window, over the engine's own pipe. \"What it changes\" and \"Check\" are found "
          "by reading the method and the methods it calls; \"platform's check per operation\" points at section 1.\n")
    print(table(("Method", "Where", "What it changes", "Check"), rpc))


if __name__ == "__main__":
    main()
