"""OneLake table addresses are built in one place, onelake.lakehouse_root and onelake.table_uri, which
check the ids and names (QA-025). A hand-built address skipped those checks, and one turned a table
name without a schema into Tables//name."""

import ast
from pathlib import Path

import pytest

from fabric_desktop import onelake
from fabric_desktop.onelake import DFS_HOST, TableRef, lakehouse_root, table_uri, workspace_root

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"

# Files that may still spell an address out, and why.
ALLOWED = {
    "onelake.py": "the builder",
    "proof_runtime.py": "runs in Fabric as a self-contained file, so it can't import the builder; its root comes from it",
    "xray_service.py": "matches adopted tables by their address's start, and builds none",
    "notebook_practices.py": "a pattern that recognises addresses in a notebook's code, and builds none",
}


def _hand_built(tree: ast.AST) -> list[int]:
    """f-strings that put a value into an abfss:// address, or straight before /Tables/."""
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = node.values
        text = "".join(v.value if isinstance(v, ast.Constant) else "\0" for v in parts)
        if "abfss://\0" in text or "\0/Tables/" in text:
            lines.append(node.lineno)
    return lines


def test_no_address_is_built_by_hand():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in ALLOWED:
            continue
        found += [f"{rel}:{line}" for line in _hand_built(ast.parse(path.read_text(encoding="utf-8")))]
    assert found == [], ("build OneLake addresses with onelake.lakehouse_root and onelake.table_uri, which check "
                         "names: " + ", ".join(found))


def test_the_guard_sees_both_shapes_and_leaves_lakehouse_paths_alone():
    tree = ast.parse('a = f"abfss://{w}@h/{l}"\nb = f"{root}/Tables/{t}"\nc = f"Tables/{s}/{t}"\n'
                     'd = f"/Tables/{s}"\ne = f"{x}: must be an address (abfss://...)"\n')
    assert _hand_built(tree) == [1, 2]


def test_the_allowed_files_still_exist():
    assert all((PACKAGE / rel).is_file() for rel in ALLOWED)


def test_the_builder_gives_the_address_table_ref_always_gave():
    root = lakehouse_root("ws-1", "lh-1")
    assert root == f"abfss://ws-1@{DFS_HOST}/lh-1"
    assert table_uri(root, "dbo", "orders") == f"{root}/Tables/dbo/orders"
    assert table_uri(root, "ignored", "orders", flat=True) == f"{root}/Tables/orders"
    assert TableRef("ws-1", "lh-1", "sales", "orders").uri == f"{root}/Tables/sales/orders"
    assert TableRef("ws-1", "lh-1", "dbo", "orders", flat=True).uri == f"{root}/Tables/orders"
    assert table_uri("C:/proof/lh", "dbo", "t") == "C:/proof/lh/Tables/dbo/t"  # a local root, as in runs on this PC


@pytest.mark.parametrize("ws, lh", [("", "lh"), ("ws", ""), ("w/s", "lh"), ("ws", "l@h"), ("ws", "lh?x"),
                                    ("ws", "lh#x"), ("w s", "lh"), ("ws", ".."), (".", "lh"),
                                    ("ws", "lh%2F"), ("ws", "c:lh")])
def test_an_id_that_would_change_the_address_is_refused(ws, lh):
    with pytest.raises(ValueError, match="Invalid (workspace|lakehouse) id"):
        lakehouse_root(ws, lh)


@pytest.mark.parametrize("schema, table", [("dbo", "../other"), ("dbo", "a/b"), ("", "t"), ("d#", "t"), ("dbo", " t"),
                                           ("dbo", ".."), ("..", "x"), ("dbo", "."), (".", "x")])
def test_a_name_that_would_step_out_of_its_folder_is_refused(schema, table):
    with pytest.raises(ValueError, match="Invalid (schema|table) name"):
        table_uri("root", schema, table)


def test_side_by_side_puts_a_table_without_a_schema_in_dbo(monkeypatch):
    from fabric_desktop.side_by_side_service import OneLakeTables
    monkeypatch.setattr(onelake.FileStore, "__init__", lambda self, *a, **k: None)
    tables = OneLakeTables("ws", "lh", True, lambda: "tok")
    assert tables._uri("orders") == f"abfss://ws@{DFS_HOST}/lh/Tables/dbo/orders"  # was Tables//orders
    assert tables._uri("sales.orders") == f"abfss://ws@{DFS_HOST}/lh/Tables/sales/orders"
    assert OneLakeTables("ws", "lh", False, lambda: "tok")._uri("dbo.orders") == f"abfss://ws@{DFS_HOST}/lh/Tables/orders"


def test_a_workspace_root_is_the_container_and_checks_its_id():
    assert workspace_root("ws-1") == f"abfss://ws-1@{DFS_HOST}"
    with pytest.raises(ValueError):
        workspace_root("ws/1")
